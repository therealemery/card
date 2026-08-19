"""
pytest 共享 fixtures。

测试数据库：本机 PostgreSQL（默认 postgresql://postgres:postgres@127.0.0.1:15432/cardlink_test，
可用环境变量 DATABASE_URL 覆盖）。每个测试会话会 DROP SCHEMA public 重建，切勿指向有数据的库。

环境变量必须在导入 app 之前设置（config.py 缺失必填项会拒启）。
"""
import os

# 必须在导入 app 之前设置
os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:15432/cardlink_test"
)
os.environ["MASTER_KEY"] = os.environ.get("MASTER_KEY", "test-master-key")
os.environ.setdefault("SCAN_INTERVAL_MINUTES", "60")
os.environ.setdefault("EXPIRING_DAYS", "7")

from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import psycopg2  # noqa: E402
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
    """每个测试会话：清空 schema → Alembic 建表 → 建连接池"""
    wipe = psycopg2.connect(TEST_DATABASE_URL)
    try:
        wipe.autocommit = True
        with wipe.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")
    finally:
        wipe.close()

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")

    init_db()
    yield
    database.close_db()


@pytest.fixture(autouse=True)
def clean_db():
    """每个测试函数前清空两张表"""
    with database.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM cards")
            cur.execute("DELETE FROM projects")


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
        "/api/cards",
        json={"card_key": "88801234", "days": 30, "plan_code": "pro", "remark": "测试客户"},
        headers={"X-Admin-Key": proj["admin_key"]},
    )
    assert r.status_code == 200, r.text
    return proj, r.json()["card"]


def admin_headers(project):
    return {"X-Admin-Key": project["admin_key"]}


def resolve_headers(project):
    return {"Authorization": f"Bearer {project['resolve_token']}"}


def set_expires_at(card_key, dt: datetime):
    """直接把卡的 expires_at 改到指定时间（构造临期/已过期场景）"""
    with database.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE cards SET expires_at = %s WHERE card_key = %s",
                (dt, card_key),
            )


def utcnow():
    return datetime.now(timezone.utc)


def days_from_now(days: float) -> datetime:
    return utcnow() + timedelta(days=days)
