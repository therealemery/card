"""面板管理员认证测试：login/logout/session 校验、双通道（session vs API 密钥）回归"""
from datetime import timedelta

import database
from tests.conftest import admin_headers, cards_url, resolve_headers, utcnow

USERNAME = "admin"            # conftest 顶部注入的测试值
PASSWORD = "test-admin-password"


def _login(api, username=USERNAME, password=PASSWORD):
    return api.post("/api/auth/login", json={"username": username, "password": password})


def _session_headers(api):
    r = _login(api)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------- login ----------

def test_login_ok(api):
    r = _login(api)
    assert r.status_code == 200
    body = r.json()
    assert len(body["token"]) == 64
    assert "expires_at" in body


def test_login_wrong_password(api):
    r = _login(api, password="wrong")
    assert r.status_code == 401
    assert r.json()["detail"] == "用户名或密码错误"


def test_login_wrong_username_same_message(api):
    """不区分用户名错还是密码错"""
    r1 = _login(api, username="nobody")
    r2 = _login(api, password="wrong")
    assert r1.status_code == 401
    assert r1.json()["detail"] == r2.json()["detail"]


def test_login_cleans_expired_sessions(api):
    """login 时顺手删除过期 session"""
    with database.get_db() as conn:
        conn.execute(
            "INSERT INTO admin_sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
            ("expired-token", utcnow().isoformat(), (utcnow() - timedelta(days=1)).isoformat()),
        )
    _login(api)
    with database.get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM admin_sessions WHERE token = 'expired-token'"
        ).fetchone()
    assert row is None


# ---------- session 访问 projects ----------

def test_session_can_list_and_create_projects(api):
    h = _session_headers(api)
    r = api.post("/api/projects", json={"name": "sess-proj"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["resolve_token"]
    assert "admin_key" not in r.json()
    r = api.get("/api/projects", headers=h)
    assert r.status_code == 200
    assert any(p["name"] == "sess-proj" for p in r.json()["projects"])


def test_session_invalid_token_403(api):
    r = api.get("/api/projects", headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 403


def test_session_expired_403(api):
    r = _login(api)
    token = r.json()["token"]
    # 手动把 session 改成已过期
    with database.get_db() as conn:
        conn.execute(
            "UPDATE admin_sessions SET expires_at = ? WHERE token = ?",
            ((utcnow() - timedelta(seconds=1)).isoformat(), token),
        )
    r = api.get("/api/projects", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


# ---------- session 访问 cards（project_id 必传） ----------

def test_session_cards_require_project_id(api, project):
    proj = project(name="sess-cards")
    h = _session_headers(api)
    # 缺 project_id → 400
    r = api.get("/api/cards", headers=h)
    assert r.status_code == 400
    # 不存在的 project_id → 404
    r = api.get("/api/cards?project_id=99999", headers=h)
    assert r.status_code == 404


def test_session_cards_full_flow(api, project):
    proj = project(name="sess-flow")
    h = _session_headers(api)
    pid = proj["id"]

    r = api.post(f"/api/cards?project_id={pid}",
                 json={"card_key": "88889999", "days": 30}, headers=h)
    assert r.status_code == 200, r.text

    assert api.get(f"/api/cards?project_id={pid}", headers=h).json()["count"] == 1
    assert api.get(f"/api/cards/88889999?project_id={pid}", headers=h).status_code == 200
    assert api.post(f"/api/cards/88889999/renew?project_id={pid}",
                    json={"days": 10}, headers=h).status_code == 200
    assert api.patch(f"/api/cards/88889999?project_id={pid}",
                     json={"action": "suspend"}, headers=h).status_code == 200

    # session 只能操作 project_id 指定的项目：跨项目看不到
    proj2 = project(name="sess-flow2")
    r = api.get(f"/api/cards/88889999?project_id={proj2['id']}", headers=h)
    assert r.status_code == 404


# ---------- logout ----------

def test_logout_invalidates_token(api):
    h = _session_headers(api)
    assert api.get("/api/projects", headers=h).status_code == 200
    r = api.post("/api/auth/logout", headers=h)
    assert r.status_code == 200
    assert api.get("/api/projects", headers=h).status_code == 403


# ---------- 回归：API 密钥体系照旧 ----------

def test_master_key_still_works(api, project):
    from tests.conftest import MASTER_HEADERS
    assert api.get("/api/projects", headers=MASTER_HEADERS).status_code == 200
    r = api.post("/api/projects", json={"name": "mk-proj"}, headers=MASTER_HEADERS)
    assert r.status_code == 200


def test_admin_key_path_removed(api, project):
    """X-Admin-Key 鉴权已废弃：管理接口只认 admin session"""
    proj = project(name="ak-proj")
    r = api.post(
        cards_url(proj, "/api/cards"), json={"card_key": "88881111", "days": 30},
        headers={"X-Admin-Key": "whatever"},
    )
    assert r.status_code == 403
    assert api.get(cards_url(proj, "/api/cards"), headers={"X-Admin-Key": "whatever"}).status_code == 403


def test_resolve_unchanged(api, project_with_card):
    proj, card = project_with_card
    body = api.post(
        "/api/resolve", json={"card_key": card["card_key"]}, headers=resolve_headers(proj)
    ).json()
    assert body["valid"] is True
