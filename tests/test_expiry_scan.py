"""到期扫描测试：card.expiring / card.expired 事件、reminded_at / expired_notified_at 去重、payload 与 header"""
from datetime import timedelta

import database
from scheduler import run_expiry_scan
from tests.conftest import admin_headers, cards_url, days_from_now, set_expires_at, utcnow


class MockResponse:
    status_code = 200


def _mock_httpx(monkeypatch, record: list):
    """mock scheduler 里的 httpx.post，记录 (url, json, headers)"""
    def fake_post(url, json=None, headers=None, timeout=None):
        record.append({"url": url, "json": json, "headers": headers})
        return MockResponse()
    monkeypatch.setattr("scheduler.httpx.post", fake_post)


def _make_project_with_card(api, project, name, callback_url, account="88880001"):
    proj = project(name=name, callback_url=callback_url)
    card = api.post(
        cards_url(proj, "/api/cards"), json={"card_key": account, "days": 30},
        headers=admin_headers(proj),
    ).json()["card"]
    return proj, card


def test_scan_sends_expiring_event(api, project, monkeypatch):
    calls = []
    _mock_httpx(monkeypatch, calls)
    proj, card = _make_project_with_card(api, project, "exp1", "https://example.com/hook")
    set_expires_at(card["card_key"], days_from_now(3))

    sent = run_expiry_scan()
    assert sent == {"card.expiring": 1, "card.expired": 0}
    assert len(calls) == 1

    call = calls[0]
    assert call["url"] == "https://example.com/hook"
    assert call["json"]["event"] == "card.expiring"
    assert call["json"]["card_key"] == card["card_key"]
    assert call["json"]["project_id"] == proj["id"]
    assert "expires_at" in call["json"]
    # header 带项目 resolve_token 供校验
    assert call["headers"]["Authorization"] == f"Bearer {proj['resolve_token']}"


def test_scan_sends_expired_event(api, project, monkeypatch):
    calls = []
    _mock_httpx(monkeypatch, calls)
    proj, card = _make_project_with_card(api, project, "exp2", "https://example.com/hook")
    set_expires_at(card["card_key"], days_from_now(-1))

    sent = run_expiry_scan()
    assert sent == {"card.expiring": 0, "card.expired": 1}
    assert calls[0]["json"]["event"] == "card.expired"


def test_scan_dedup_no_repeat(api, project, monkeypatch):
    """reminded_at / expired_notified_at 落库后，重复扫描不再推送"""
    calls = []
    _mock_httpx(monkeypatch, calls)
    proj, card = _make_project_with_card(api, project, "exp3", "https://example.com/hook")

    set_expires_at(card["card_key"], days_from_now(3))
    run_expiry_scan()
    run_expiry_scan()
    run_expiry_scan()
    assert len(calls) == 1  # 只推一次

    # 过期后再扫：expired 事件也只推一次
    set_expires_at(card["card_key"], days_from_now(-1))
    run_expiry_scan()
    run_expiry_scan()
    events = [c["json"]["event"] for c in calls]
    assert events == ["card.expiring", "card.expired"]

    # 两列都已标记
    with database.get_db() as conn:
        row = conn.execute(
            "SELECT reminded_at, expired_notified_at FROM cards WHERE card_key = ?",
            (card["card_key"],),
        ).fetchone()
    assert row["reminded_at"] is not None
    assert row["expired_notified_at"] is not None


def test_scan_marks_only_after_success(api, project, monkeypatch):
    """推送失败不标记，下轮还会再试"""
    def failing_post(url, json=None, headers=None, timeout=None):
        raise RuntimeError("network down")
    monkeypatch.setattr("scheduler.httpx.post", failing_post)

    proj, card = _make_project_with_card(api, project, "exp4", "https://example.com/hook")
    set_expires_at(card["card_key"], days_from_now(3))

    sent = run_expiry_scan()
    assert sent == {"card.expiring": 0, "card.expired": 0}

    with database.get_db() as conn:
        row = conn.execute(
            "SELECT reminded_at FROM cards WHERE card_key = ?", (card["card_key"],)
        ).fetchone()
        assert row["reminded_at"] is None

    # 网络恢复后重扫，能推出去
    calls = []
    _mock_httpx(monkeypatch, calls)
    sent = run_expiry_scan()
    assert sent["card.expiring"] == 1


def test_scan_skips_when_not_due(api, project, monkeypatch):
    """到期日还远、无 callback_url、已 revoked 的卡都不推"""
    calls = []
    _mock_httpx(monkeypatch, calls)

    # 30 天后到期：不临期
    _make_project_with_card(api, project, "skip1", "https://example.com/hook", account="88880011")
    # 无 callback_url
    proj2, card2 = _make_project_with_card(api, project, "skip2", None, account="88880012")
    set_expires_at(card2["card_key"], days_from_now(1))
    # 已 revoked 的过期卡：不打扰
    proj3, card3 = _make_project_with_card(api, project, "skip3", "https://example.com/hook", account="88880013")
    api.patch(cards_url(proj3, f"/api/cards/{card3['card_key']}"), json={"action": "revoke"}, headers=admin_headers(proj3))
    set_expires_at(card3["card_key"], days_from_now(-1))

    sent = run_expiry_scan()
    assert sent == {"card.expiring": 0, "card.expired": 0}
    assert calls == []


def test_scan_boundary_string_compare(api, project, monkeypatch):
    """ISO 字符串比较的边界：恰好 now+7d 算临期、恰好 now 算已过期、超出 7d 不算"""
    calls = []
    _mock_httpx(monkeypatch, calls)
    proj = project(name="boundary", callback_url="https://example.com/hook")

    now = utcnow()
    cases = {
        "77000001": now + timedelta(days=7),            # 恰好临期阈值 → expiring
        "77000002": now,                                 # 恰好现在（扫描时已过去几 ms）→ expired
        "77000003": now + timedelta(days=7, seconds=5),  # 超出阈值 → 不推
        "77000004": now + timedelta(days=6, hours=23),   # 阈值内 → expiring
    }
    for acc, exp in cases.items():
        r = api.post(
            cards_url(proj, "/api/cards"), json={"card_key": acc, "days": 30}, headers=admin_headers(proj)
        )
        assert r.status_code == 200, r.text
        set_expires_at(acc, exp)

    sent = run_expiry_scan()
    assert sent == {"card.expiring": 2, "card.expired": 1}

    by_key = {c["json"]["card_key"]: c["json"]["event"] for c in calls}
    assert by_key == {
        "77000001": "card.expiring",
        "77000002": "card.expired",
        "77000004": "card.expiring",
    }
