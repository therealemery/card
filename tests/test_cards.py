"""卡号管理面测试：生成、列表筛选、详情、续费 GREATEST、状态机、换卡"""
import re
from datetime import datetime, timedelta, timezone

from tests.conftest import admin_headers, days_from_now, set_expires_at

CARD_KEY_RE = re.compile(r"^[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


# ---------- 生成 ----------

def test_generate_count_format_days(api, project):
    proj = project()
    r = api.post(
        "/api/cards",
        json={"count": 5, "days": 10, "plan_code": "pro", "remark": "批次A"},
        headers=admin_headers(proj),
    )
    assert r.status_code == 200, r.text
    cards = r.json()["cards"]
    assert len(cards) == 5
    assert len({c["card_key"] for c in cards}) == 5  # 无重复
    for c in cards:
        assert CARD_KEY_RE.match(c["card_key"]), c["card_key"]
        assert c["status"] == "active"
        assert c["plan_code"] == "pro"
        assert c["remark"] == "批次A"
        # days 语义：到期时间 ≈ now + 10d（容忍 1 分钟误差）
        delta = _parse(c["expires_at"]) - datetime.now(timezone.utc)
        assert timedelta(days=10) - timedelta(minutes=1) < delta < timedelta(days=10, minutes=1)


def test_generate_default_days(api, project):
    proj = project()
    r = api.post("/api/cards", json={}, headers=admin_headers(proj))
    assert r.status_code == 200
    c = r.json()["cards"][0]
    delta = _parse(c["expires_at"]) - datetime.now(timezone.utc)
    assert delta > timedelta(days=29)


def test_generate_validation(api, project):
    proj = project()
    assert api.post("/api/cards", json={"count": 0}, headers=admin_headers(proj)).status_code == 422
    assert api.post("/api/cards", json={"count": 101}, headers=admin_headers(proj)).status_code == 422
    assert api.post("/api/cards", json={"days": 0}, headers=admin_headers(proj)).status_code == 422


def test_generate_requires_admin_key(api, project):
    assert api.post("/api/cards", json={}).status_code == 403
    assert api.post("/api/cards", json={}, headers={"X-Admin-Key": "wrong"}).status_code == 403


# ---------- 列表筛选 ----------

def test_list_filter_status(api, project):
    proj = project()
    h = admin_headers(proj)
    cards = api.post("/api/cards", json={"count": 3, "days": 5}, headers=h).json()["cards"]

    api.patch(f"/api/cards/{cards[0]['card_key']}", json={"action": "suspend"}, headers=h)
    api.patch(f"/api/cards/{cards[1]['card_key']}", json={"action": "revoke"}, headers=h)

    assert api.get("/api/cards", headers=h).json()["count"] == 3
    assert api.get("/api/cards?status=active", headers=h).json()["count"] == 1
    assert api.get("/api/cards?status=suspended", headers=h).json()["count"] == 1
    assert api.get("/api/cards?status=revoked", headers=h).json()["count"] == 1
    assert api.get("/api/cards?status=bogus", headers=h).status_code == 422


def test_list_filter_expiring_in(api, project):
    proj = project()
    h = admin_headers(proj)
    cards = api.post("/api/cards", json={"count": 3, "days": 30}, headers=h).json()["cards"]

    # 一张 3 天后到期（临期），一张已过期，一张保持 30 天
    set_expires_at(cards[0]["card_key"], days_from_now(3))
    set_expires_at(cards[1]["card_key"], days_from_now(-1))

    r = api.get("/api/cards?expiring_in=7d", headers=h)
    assert r.status_code == 200
    keys = [c["card_key"] for c in r.json()["cards"]]
    # 只含临期未过期的卡；已过期的不算 expiring
    assert keys == [cards[0]["card_key"]]

    assert api.get("/api/cards?expiring_in=abc", headers=h).status_code == 400


def test_list_isolated_between_projects(api, project):
    p1, p2 = project(name="iso1"), project(name="iso2")
    api.post("/api/cards", json={"count": 2}, headers=admin_headers(p1))
    api.post("/api/cards", json={"count": 1}, headers=admin_headers(p2))
    assert api.get("/api/cards", headers=admin_headers(p1)).json()["count"] == 2
    assert api.get("/api/cards", headers=admin_headers(p2)).json()["count"] == 1


# ---------- 详情 ----------

def test_detail(api, project_with_card):
    proj, card = project_with_card
    r = api.get(f"/api/cards/{card['card_key']}", headers=admin_headers(proj))
    assert r.status_code == 200
    assert r.json()["card"]["card_key"] == card["card_key"]


def test_detail_not_found_and_cross_project(api, project):
    p1, p2 = project(name="d1"), project(name="d2")
    card = api.post("/api/cards", json={}, headers=admin_headers(p1)).json()["cards"][0]
    # 不存在
    assert api.get("/api/cards/NOPE-0000-0000-0000", headers=admin_headers(p1)).status_code == 404
    # 跨项目不可见
    assert api.get(f"/api/cards/{card['card_key']}", headers=admin_headers(p2)).status_code == 404


# ---------- 续费（GREATEST 语义） ----------

def test_renew_active_extends_from_expiry(api, project_with_card):
    """未过期：从原到期时间顺延"""
    proj, card = project_with_card
    old_expires = _parse(card["expires_at"])

    r = api.post(f"/api/cards/{card['card_key']}/renew", json={"days": 10}, headers=admin_headers(proj))
    assert r.status_code == 200, r.text
    new_expires = _parse(r.json()["expires_at"])
    # 新到期 ≈ 原到期 + 10 天（容忍 1 秒）
    assert abs((new_expires - old_expires) - timedelta(days=10)) < timedelta(seconds=1)


def test_renew_expired_extends_from_now(api, project_with_card):
    """已过期：从现在起算，而不是从已过的到期日起算"""
    proj, card = project_with_card
    set_expires_at(card["card_key"], days_from_now(-5))

    r = api.post(f"/api/cards/{card['card_key']}/renew", json={"days": 10}, headers=admin_headers(proj))
    assert r.status_code == 200
    new_expires = _parse(r.json()["expires_at"])
    delta = new_expires - datetime.now(timezone.utc)
    # 应接近 now+10d，而不是 -5d+10d=5d
    assert timedelta(days=9, hours=23) < delta < timedelta(days=10, minutes=1)


def test_renew_not_found(api, project):
    proj = project()
    r = api.post("/api/cards/NOPE-0000-0000-0000/renew", json={"days": 1}, headers=admin_headers(proj))
    assert r.status_code == 404


def test_renew_validation(api, project_with_card):
    proj, card = project_with_card
    assert api.post(
        f"/api/cards/{card['card_key']}/renew", json={"days": 0}, headers=admin_headers(proj)
    ).status_code == 422


# ---------- 状态机 ----------

def test_status_transitions_legal(api, project_with_card):
    proj, card = project_with_card
    h = admin_headers(proj)
    key = card["card_key"]

    # active → suspended
    r = api.patch(f"/api/cards/{key}", json={"action": "suspend"}, headers=h)
    assert r.status_code == 200 and r.json()["card"]["status"] == "suspended"
    # suspended → active
    r = api.patch(f"/api/cards/{key}", json={"action": "resume"}, headers=h)
    assert r.status_code == 200 and r.json()["card"]["status"] == "active"
    # active → revoked
    r = api.patch(f"/api/cards/{key}", json={"action": "revoke"}, headers=h)
    assert r.status_code == 200 and r.json()["card"]["status"] == "revoked"
    # suspended → revoked 也合法
    api.patch(f"/api/cards/{key}", json={"action": "suspend"}, headers=h)  # 失败也无妨
    card2 = api.post("/api/cards", json={}, headers=h).json()["cards"][0]
    api.patch(f"/api/cards/{card2['card_key']}", json={"action": "suspend"}, headers=h)
    r = api.patch(f"/api/cards/{card2['card_key']}", json={"action": "revoke"}, headers=h)
    assert r.status_code == 200 and r.json()["card"]["status"] == "revoked"


def test_status_transitions_illegal(api, project_with_card):
    proj, card = project_with_card
    h = admin_headers(proj)
    key = card["card_key"]

    # active 不能 resume
    r = api.patch(f"/api/cards/{key}", json={"action": "resume"}, headers=h)
    assert r.status_code == 409
    # suspended 不能再次 suspend
    api.patch(f"/api/cards/{key}", json={"action": "suspend"}, headers=h)
    r = api.patch(f"/api/cards/{key}", json={"action": "suspend"}, headers=h)
    assert r.status_code == 409
    # revoked 是终态
    api.patch(f"/api/cards/{key}", json={"action": "revoke"}, headers=h)
    assert api.patch(f"/api/cards/{key}", json={"action": "resume"}, headers=h).status_code == 409
    assert api.patch(f"/api/cards/{key}", json={"action": "suspend"}, headers=h).status_code == 409
    # 未知 action
    assert api.patch(f"/api/cards/{key}", json={"action": "explode"}, headers=h).status_code == 422


def test_patch_updates_remark(api, project_with_card):
    proj, card = project_with_card
    r = api.patch(
        f"/api/cards/{card['card_key']}",
        json={"action": "suspend", "remark": "欠费暂停"},
        headers=admin_headers(proj),
    )
    assert r.status_code == 200
    assert r.json()["card"]["remark"] == "欠费暂停"


# ---------- 换卡 ----------

def test_replace_inherits_remaining(api, project_with_card):
    proj, card = project_with_card
    h = admin_headers(proj)

    r = api.post(f"/api/cards/{card['card_key']}/replace", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    new_card = body["card"]

    assert body["old_card_key"] == card["card_key"]
    assert new_card["card_key"] != card["card_key"]
    assert CARD_KEY_RE.match(new_card["card_key"])
    # 继承：同一到期时间（剩余有效期一致）、同 plan/remark、溯源 renewed_from
    assert new_card["expires_at"] == card["expires_at"]
    assert new_card["plan_code"] == card["plan_code"]
    assert new_card["remark"] == card["remark"]
    assert new_card["renewed_from"] == card["card_key"]
    assert new_card["status"] == "active"

    # 老卡已 revoked
    old = api.get(f"/api/cards/{card['card_key']}", headers=h).json()["card"]
    assert old["status"] == "revoked"


def test_replace_revoked_card_rejected(api, project_with_card):
    proj, card = project_with_card
    h = admin_headers(proj)
    api.patch(f"/api/cards/{card['card_key']}", json={"action": "revoke"}, headers=h)
    assert api.post(f"/api/cards/{card['card_key']}/replace", headers=h).status_code == 409


def test_replace_not_found(api, project):
    proj = project()
    assert api.post("/api/cards/NOPE-0000-0000-0000/replace", headers=admin_headers(proj)).status_code == 404
