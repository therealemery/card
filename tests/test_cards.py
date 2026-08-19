"""授权管理面测试：创建、列表筛选、详情、续费 GREATEST、状态机（含 reset）"""
import re
from datetime import datetime, timedelta, timezone

from tests.conftest import admin_headers, days_from_now, set_expires_at

ACCOUNT_RE = re.compile(r"^\d{4,32}$")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _create(api, proj, account, days=30, **kw):
    body = {"card_key": account, "days": days, **kw}
    return api.post("/api/cards", json=body, headers=admin_headers(proj))


# ---------- 创建授权 ----------

def test_create_ok(api, project):
    proj = project()
    r = _create(api, proj, "88801234", days=10, plan_code="pro", remark="客户A")
    assert r.status_code == 200, r.text
    c = r.json()["card"]
    assert c["card_key"] == "88801234"
    assert c["status"] == "active"
    assert c["plan_code"] == "pro"
    assert c["remark"] == "客户A"
    # days 语义：到期时间 ≈ now + 10d（容忍 1 分钟误差）
    delta = _parse(c["expires_at"]) - datetime.now(timezone.utc)
    assert timedelta(days=10) - timedelta(minutes=1) < delta < timedelta(days=10, minutes=1)


def test_create_duplicate_409(api, project):
    proj = project()
    assert _create(api, proj, "88801234", days=30).status_code == 200
    r = _create(api, proj, "88801234", days=30)
    assert r.status_code == 409
    # card_key 是全局主键：在别的项目下创建同账号同样 409
    proj2 = project(name="other")
    assert _create(api, proj2, "88801234", days=30).status_code == 409


def test_create_account_validation(api, project):
    proj = project()
    # 非数字
    assert _create(api, proj, "ABC12345", days=30).status_code == 422
    assert _create(api, proj, "8880-1234", days=30).status_code == 422
    # 长度边界：3 位太短、33 位太长
    assert _create(api, proj, "123", days=30).status_code == 422
    assert _create(api, proj, "1" * 33, days=30).status_code == 422
    # 4 位与 32 位合法
    assert _create(api, proj, "1234", days=30).status_code == 200
    assert _create(api, proj, "1" * 32, days=30).status_code == 200
    # 缺 card_key / days 非法
    assert api.post("/api/cards", json={"days": 30}, headers=admin_headers(proj)).status_code == 422
    assert _create(api, proj, "88801234", days=0).status_code == 422


def test_create_requires_admin_key(api, project):
    assert api.post("/api/cards", json={"card_key": "88801234", "days": 1}).status_code == 403
    assert api.post(
        "/api/cards", json={"card_key": "88801234", "days": 1},
        headers={"X-Admin-Key": "wrong"},
    ).status_code == 403


# ---------- 列表筛选 ----------

def test_list_filter_status(api, project):
    proj = project()
    h = admin_headers(proj)
    for acc in ("10000001", "10000002", "10000003"):
        assert _create(api, proj, acc, days=5).status_code == 200

    api.patch("/api/cards/10000001", json={"action": "suspend"}, headers=h)
    api.patch("/api/cards/10000002", json={"action": "revoke"}, headers=h)

    assert api.get("/api/cards", headers=h).json()["count"] == 3
    assert api.get("/api/cards?status=active", headers=h).json()["count"] == 1
    assert api.get("/api/cards?status=suspended", headers=h).json()["count"] == 1
    assert api.get("/api/cards?status=revoked", headers=h).json()["count"] == 1
    assert api.get("/api/cards?status=bogus", headers=h).status_code == 422


def test_list_filter_expiring_in(api, project):
    proj = project()
    h = admin_headers(proj)
    for acc in ("20000001", "20000002", "20000003"):
        assert _create(api, proj, acc, days=30).status_code == 200

    # 一个 3 天后到期（临期），一个已过期，一个保持 30 天
    set_expires_at("20000001", days_from_now(3))
    set_expires_at("20000002", days_from_now(-1))

    r = api.get("/api/cards?expiring_in=7d", headers=h)
    assert r.status_code == 200
    keys = [c["card_key"] for c in r.json()["cards"]]
    # 只含临期未过期的；已过期的不算 expiring
    assert keys == ["20000001"]

    assert api.get("/api/cards?expiring_in=abc", headers=h).status_code == 400


def test_list_isolated_between_projects(api, project):
    p1, p2 = project(name="iso1"), project(name="iso2")
    _create(api, p1, "30000001", days=1)
    _create(api, p1, "30000002", days=1)
    _create(api, p2, "30000003", days=1)
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
    card = _create(api, p1, "40000001", days=1).json()["card"]
    # 不存在
    assert api.get("/api/cards/49999999", headers=admin_headers(p1)).status_code == 404
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
    r = api.post("/api/cards/49999999/renew", json={"days": 1}, headers=admin_headers(proj))
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
    _create(api, proj, "50000001", days=1)
    api.patch("/api/cards/50000001", json={"action": "suspend"}, headers=h)
    r = api.patch("/api/cards/50000001", json={"action": "revoke"}, headers=h)
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


# ---------- 只改备注（action 可选） ----------

def test_patch_remark_only(api, project_with_card):
    proj, card = project_with_card
    h = admin_headers(proj)
    r = api.patch(f"/api/cards/{card['card_key']}", json={"remark": "仅改备注"}, headers=h)
    assert r.status_code == 200
    assert r.json()["card"]["remark"] == "仅改备注"
    assert r.json()["card"]["status"] == "active"  # 状态不变
    # action 与 remark 都不给 → 422
    assert api.patch(f"/api/cards/{card['card_key']}", json={}, headers=h).status_code == 422


# ---------- reset：任意状态 → active，不动 expires_at ----------

def test_reset_from_revoked(api, project_with_card):
    proj, card = project_with_card
    h = admin_headers(proj)
    key = card["card_key"]
    api.patch(f"/api/cards/{key}", json={"action": "revoke"}, headers=h)

    r = api.patch(f"/api/cards/{key}", json={"action": "reset"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["card"]["status"] == "active"
    # expires_at 不动
    assert r.json()["card"]["expires_at"] == card["expires_at"]


def test_reset_from_suspended(api, project_with_card):
    proj, card = project_with_card
    h = admin_headers(proj)
    key = card["card_key"]
    api.patch(f"/api/cards/{key}", json={"action": "suspend"}, headers=h)

    r = api.patch(f"/api/cards/{key}", json={"action": "reset"}, headers=h)
    assert r.status_code == 200
    assert r.json()["card"]["status"] == "active"
    assert r.json()["card"]["expires_at"] == card["expires_at"]


def test_reset_restores_resolve(api, project_with_card):
    """revoke → reset 后 resolve 恢复 valid（未过期前提下）"""
    from tests.conftest import resolve_headers

    proj, card = project_with_card
    h = admin_headers(proj)
    key = card["card_key"]

    api.patch(f"/api/cards/{key}", json={"action": "revoke"}, headers=h)
    body = api.post("/api/resolve", json={"card_key": key}, headers=resolve_headers(proj)).json()
    assert body["valid"] is False and body["reason"] == "revoked"

    api.patch(f"/api/cards/{key}", json={"action": "reset"}, headers=h)
    body = api.post("/api/resolve", json={"card_key": key}, headers=resolve_headers(proj)).json()
    assert body["valid"] is True and body["reason"] is None
