"""初始建表：projects + cards

Revision ID: 0001_init
Revises:
Create Date: 2026-08-12
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
            id             SERIAL PRIMARY KEY,
            name           TEXT NOT NULL UNIQUE,
            admin_key      TEXT NOT NULL UNIQUE,
            resolve_token  TEXT NOT NULL UNIQUE,
            callback_url   TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # 卡号：过期不是状态，由 expires_at 表达；reminded_at / expired_notified_at 用于 webhook 去重
    op.execute("""
        CREATE TABLE cards (
            card_key            TEXT PRIMARY KEY,
            project_id          INTEGER NOT NULL REFERENCES projects(id),
            plan_code           TEXT NOT NULL DEFAULT '',
            expires_at          TIMESTAMPTZ NOT NULL,
            status              TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'suspended', 'revoked')),
            remark              TEXT NOT NULL DEFAULT '',
            renewed_from        TEXT,
            reminded_at         TIMESTAMPTZ,
            expired_notified_at TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_cards_project ON cards(project_id)")
    op.execute("CREATE INDEX idx_cards_expires ON cards(expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cards")
    op.execute("DROP TABLE IF EXISTS projects")
