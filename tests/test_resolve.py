"""resolve 查询面测试：有效 / suspended / revoked / 已过期 / 不存在 / 鉴权"""
from tests.conftest import admin_headers, cards_url, days_from_now, resolve_headers, set_expires_at


def _resolve(api, proj, card_key):
    return api.post("/api/resolve", json={"card_key": card_key}, headers=resolve_headers(proj))


def test_resolve_valid(api, project_with_card):
    proj, card = project_with_card
    r = _resolve(api, proj, card["card_key"])
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["reason"] is None
    assert body["status"] == "active"
    assert body["expires_at"] == card["expires_at"]


def test_resolve_suspended(api, project_with_card):
    proj, card = project_with_card
    api.patch(cards_url(proj, f"/api/cards/{card['card_key']}"), json={"action": "suspend"}, headers=admin_headers(proj))
    body = _resolve(api, proj, card["card_key"]).json()
    assert body["valid"] is False
    assert body["reason"] == "suspended"
    assert body["status"] == "suspended"


def test_resolve_revoked(api, project_with_card):
    proj, card = project_with_card
    api.patch(cards_url(proj, f"/api/cards/{card['card_key']}"), json={"action": "revoke"}, headers=admin_headers(proj))
    body = _resolve(api, proj, card["card_key"]).json()
    assert body["valid"] is False
    assert body["reason"] == "revoked"


def test_resolve_expired(api, project_with_card):
    proj, card = project_with_card
    set_expires_at(card["card_key"], days_from_now(-1))
    body = _resolve(api, proj, card["card_key"]).json()
    assert body["valid"] is False
    assert body["reason"] == "expired"
    # 过期不是状态：status 仍是 active
    assert body["status"] == "active"


def test_resolve_not_found(api, project):
    proj = project()
    body = _resolve(api, proj, "NOPE-0000-0000-0000").json()
    assert body == {"valid": False, "reason": "not_found"}


def test_resolve_cross_project_invisible(api, project):
    """别项目的账号对本项目 resolve 而言视同不存在"""
    p1, p2 = project(name="r1"), project(name="r2")
    card = api.post(
        cards_url(p1, "/api/cards"), json={"card_key": "66601234", "days": 30}, headers=admin_headers(p1)
    ).json()["card"]
    body = _resolve(api, p2, card["card_key"]).json()
    assert body == {"valid": False, "reason": "not_found"}


def test_resolve_requires_resolve_token(api, project_with_card):
    proj, card = project_with_card
    # 缺 header / 错误 token 都 403
    assert api.post("/api/resolve", json={"card_key": card["card_key"]}).status_code == 403
    assert api.post(
        "/api/resolve", json={"card_key": card["card_key"]},
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 403
