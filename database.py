"""
数据库访问层：psycopg2 连接池 + 事务上下文管理器。

用法：
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(...)
    # 正常退出自动 commit，异常自动 rollback
"""
import logging
from contextlib import contextmanager

from psycopg2 import pool as pg_pool

from config import DATABASE_URL

logger = logging.getLogger(__name__)

db_pool: pg_pool.SimpleConnectionPool | None = None


def init_db():
    """建连接池（建表归 Alembic 管，这里不碰 schema）"""
    global db_pool
    if db_pool is None:
        db_pool = pg_pool.SimpleConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)


def close_db():
    global db_pool
    if db_pool is not None:
        db_pool.closeall()
        db_pool = None


@contextmanager
def get_db():
    """取一个连接，退出时提交/回滚并归还连接池"""
    if db_pool is None:
        init_db()
    conn = db_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)
