"""项目自举测试：MASTER_KEY 校验、创建、列表（含 resolve_token）、删除级联"""
from tests.conftest import MASTER_HEADERS, admin_headers, cards_url


def test_create_project_ok(api, project):
    proj = project(name="p1", callback_url="https://example.com/hook")
    assert proj["name"] == "p1"
    assert proj["callback_url"] == "https://example.com/hook"
    # 只剩 resolve_token，不再有 admin_key
    assert len(proj["resolve_token"]) == 64
    assert "admin_key" not in proj


def test_create_project_requires_master_key(api):
    # 缺 header
    assert api.post("/api/projects", json={"name": "x"}).status_code == 403
    # 错误 key
    r = api.post("/api/projects", json={"name": "x"}, headers={"X-Master-Key": "wrong"})
    assert r.status_code == 403


def test_create_project_duplicate_name(api, project):
    project(name="dup")
    r = api.post("/api/projects", json={"name": "dup"}, headers=MASTER_HEADERS)
    assert r.status_code == 409


def test_list_projects_includes_resolve_token(api, project):
    created = project(name="listed")
    r = api.get("/api/projects", headers=MASTER_HEADERS)
    assert r.status_code == 200
    items = r.json()["projects"]
    match = [p for p in items if p["name"] == "listed"]
    assert match
    # 列表含完整 resolve_token（面板可反复查看复制），不含 admin_key
    assert match[0]["resolve_token"] == created["resolve_token"]
    assert "admin_key" not in match[0]


def test_list_projects_requires_master_key(api):
    assert api.get("/api/projects").status_code == 403


# ---------- 删除项目（级联删授权） ----------

def test_delete_project_cascades_cards(api, project):
    proj = project(name="to-delete")
    h = admin_headers()
    # 建一张授权
    r = api.post(cards_url(proj, "/api/cards"), json={"card_key": "77700001", "days": 30}, headers=h)
    assert r.status_code == 200

    r = api.delete(f"/api/projects/{proj['id']}", headers=h)
    assert r.status_code == 200, r.text

    # 项目列表里没了
    names = [p["name"] for p in api.get("/api/projects", headers=MASTER_HEADERS).json()["projects"]]
    assert "to-delete" not in names
    # 授权也跟着没了：按 project_id 查 → 项目不存在 404；resolve → not_found
    assert api.get(cards_url(proj, "/api/cards"), headers=admin_headers()).status_code == 404
    body = api.post(
        "/api/resolve", json={"card_key": "77700001"},
        headers={"Authorization": f"Bearer {proj['resolve_token']}"},
    )
    # 项目没了，resolve_token 也失效
    assert body.status_code == 403


def test_delete_project_not_found(api):
    h = admin_headers()
    assert api.delete("/api/projects/99999", headers=h).status_code == 404


def test_delete_project_requires_auth(api, project):
    proj = project(name="no-auth")
    assert api.delete(f"/api/projects/{proj['id']}").status_code == 403


def test_delete_project_with_master_key(api, project):
    proj = project(name="mk-delete")
    r = api.delete(f"/api/projects/{proj['id']}", headers=MASTER_HEADERS)
    assert r.status_code == 200
