"""项目自举接口测试：MASTER_KEY 校验、创建、列表"""
from tests.conftest import MASTER_HEADERS


def test_create_project_ok(api, project):
    proj = project(name="p1", callback_url="https://example.com/hook")
    assert proj["name"] == "p1"
    assert proj["callback_url"] == "https://example.com/hook"
    # 两把密钥只在创建时返回
    assert len(proj["admin_key"]) == 64
    assert len(proj["resolve_token"]) == 64
    assert proj["admin_key"] != proj["resolve_token"]


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


def test_list_projects_hides_secrets(api, project):
    project(name="listed")
    r = api.get("/api/projects", headers=MASTER_HEADERS)
    assert r.status_code == 200
    items = r.json()["projects"]
    assert any(p["name"] == "listed" for p in items)
    for p in items:
        assert "admin_key" not in p
        assert "resolve_token" not in p


def test_list_projects_requires_master_key(api):
    assert api.get("/api/projects").status_code == 403
