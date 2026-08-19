"""
数据库访问层：标准库 sqlite3，按线程管理连接（threading.local），
不跨线程共享连接。WAL 模式 + 外键约束在每个连接上开启。

时间约定：所有时间字段存 UTC ISO8601 字符串（datetime.now(timezone.utc).isoformat()），
同格式同偏移下字符串字典序即时间序，比较直接用 SQL 比较运算符。

用法：
    with get_db() as conn:
        cur = conn.execute("SELECT ... WHERE card_key = ?", (key,))
        row = cur.fetchone()   # sqlite3.Row，可 dict(row)
    # 正常退出自动 commit，异常自动 rollback
"""
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from config import sqlite_path

logger = logging.getLogger(__name__)

_local = threading.local()
_db_path: str | None = None


def utcnow_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串（全库统一的时间格式）"""
    return datetime.now(timezone.utc).isoformat()


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """确定数据库文件路径（自动创建所在目录）；建表归 Alembic 管"""
    global _db_path
    path = sqlite_path()
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    _db_path = path
    # 先建一个连接确认文件可写、WAL 生效
    conn = _connect(path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    logger.info("SQLite 就绪: %s (journal_mode=%s)", path, mode)


def get_conn() -> sqlite3.Connection:
    """当前线程的连接，没有则新建"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        if _db_path is None:
            init_db()
        conn = _connect(_db_path)
        _local.conn = conn
    return conn


def close_db():
    """关闭当前线程的连接（其他线程的连接随线程退出回收）"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def get_db():
    """取当前线程连接，退出时提交/回滚"""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
