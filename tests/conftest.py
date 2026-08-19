"""
pytest 共享 fixtures。

测试数据库：每个测试会话用一个临时 SQLite 文件（tmp 目录），跑完即弃，
不依赖任何外部数据库服务。

环境变量必须在导入 app 之前设置（config.py 缺失必填项会拒启）。
"""
import os
import tempfile

# 必须在导入 app 之前设置
_TEST_DIR = tempfile.mkdtemp(prefix="cardlink-test-")
os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL", os.path.join(_TEST_DIR, "test.db")
)
os.environ["MASTER_KEY"] = os.environ.get("MASTER_KEY", "test-master-key")
os.environ["ADMIN_USERNAME"] = os.environ.get("ADMIN_USERNAME", "admin")
os.environ["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("ADMIN_SESSION_DAYS", "7")
os.environ.setdefault("SCAN_INTERVAL_MINUTES", "60")
os.environ.setdefault("EXPIRING_DAYS", "7")

from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
from database import init_db  # noqa: E402
from main import app  # noqa: E402

TEST_DATABASE_URL = os.environ["DATABASE_URL"]
MASTER_HEADERS = {"X-Master-Key": os.environ["MASTER_KEY"]}


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """每个测试会话：Alembic 在临时 SQLite 文件上建表 → 初始化连接"""
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{TEST_DATABASE_URL}")
    command.upgrade(cfg, "head")

    init_db()
    yield
    database.close_db()


@pytest.fixture(autouse=True)
def clean_db():
    """每个测试函数前清空所有表"""
    with database.get_db() as conn:
        conn.execute("DELETE FROM cards")
        conn.execute("DELETE FROM admin_sessions")
        conn.execute("DELETE FROM projects")


@pytest.fixture
def api():
    # TestClient 不进 lifespan（不启动后台 scheduler），扫描用 run_expiry_scan 直接触发
    return TestClient(app)


@pytest.fixture
def project(api):
    """工厂 fixture：经 MASTER_KEY 创建项目，返回完整项目信息（含两把密钥）"""
    def _create(name="demo", callback_url=None):
        payload = {"name": name}
        if callback_url:
            payload["callback_url"] = callback_url
        r = api.post("/api/projects", json=payload, headers=MASTER_HEADERS)
        assert r.status_code == 200, r.text
        return r.json()
    return _create


@pytest.fixture
def project_with_card(project, api):
    """常用组合：项目 + 一个 30 天授权的账号，返回 (project, card)"""
    proj = project()
    r = api.post(
        cards_url(proj, "/api/cards"),
        json={"card_key": "88801234", "days": 30, "remark": "测试客户"},
        headers=admin_headers(proj),
    )
    assert r.status_code == 200, r.text
    return proj, r.json()["card"]


def admin_headers(project=None):
    """面板管理员 session 头（登录一次拿 token；管理接口需配合 cards_url 带 project_id）"""
    client = TestClient(app)
    r = client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def cards_url(project, path):
    """给管理面 URL 追加 ?project_id=（admin session 必传）"""
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}project_id={project['id']}"


def resolve_headers(project):
    return {"Authorization": f"Bearer {project['resolve_token']}"}


def set_expires_at(card_key, dt: datetime):
    """直接把账号的 expires_at 改到指定时间（构造临期/已过期场景）"""
    with database.get_db() as conn:
        conn.execute(
            "UPDATE cards SET expires_at = ? WHERE card_key = ?",
            (dt.isoformat(), card_key),
        )


def utcnow():
    return datetime.now(timezone.utc)


def days_from_now(days: float) -> datetime:
    return utcnow() + timedelta(days=days)
