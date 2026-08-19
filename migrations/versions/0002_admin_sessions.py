"""admin_sessions：面板管理员会话表（账号密码登录颁发的 Bearer token）

Revision ID: 0002_admin_sessions
Revises: 0001_init
Create Date: 2026-08-19
"""
from alembic import op

revision = "0002_admin_sessions"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE admin_sessions (
            token       TEXT PRIMARY KEY,
            created_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS admin_sessions")
