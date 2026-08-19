"""内置管理后台页面测试"""


def test_admin_page_served(api):
    r = api.get("/admin")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # 页面标记：标题与三个视图容器
    assert "CardLink 管理后台" in r.text
    assert 'id="view-login"' in r.text
    assert 'id="view-admin"' in r.text
    assert 'id="view-master"' in r.text


def test_root_redirects_to_admin(api):
    r = api.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/admin"
