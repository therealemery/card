"""初始建表：projects + cards（SQLite 方言）

时间字段一律 TEXT，存 UTC ISO8601 字符串（见 database.py 头部约定）。

Revision ID: 0001_init
Revises:
Create Date: 2026-08-19
"""
from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 接入方（项目）：admin_key 管管理面，resolve_token 管查询面，callback_url 收 webhook
    op.execute("""
        CREATE TABLE projects (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL UNIQUE,
            admin_key      TEXT NOT NULL UNIQUE,
            resolve_token  TEXT NOT NULL UNIQUE,
            callback_url   TEXT,
            created_at     TEXT NOT NULL
        )
    """)

    # 账号授权：card_key = 交易账号（全局唯一）；过期不是状态，由 expires_at 表达；
    # reminded_at / expired_notified_at 用于 webhook 去重
    op.execute("""
        CREATE TABLE cards (
            card_key            TEXT PRIMARY KEY,
            project_id          INTEGER NOT NULL REFERENCES projects(id),
            plan_code           TEXT NOT NULL DEFAULT '',
            expires_at          TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'suspended', 'revoked')),
            remark              TEXT NOT NULL DEFAULT '',
            renewed_from        TEXT,
            reminded_at         TEXT,
            expired_notified_at TEXT,
            created_at          TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_cards_project ON cards(project_id)")
    op.execute("CREATE INDEX idx_cards_expires ON cards(expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cards")
    op.execute("DROP TABLE IF EXISTS projects")
