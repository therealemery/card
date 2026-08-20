"""cards 表删除 plan_code 列（套餐概念废弃，授权只认账号 + 期限）

老版本 SQLite（< 3.35）不支持 ALTER TABLE ... DROP COLUMN，
与 0003 一样按官方姿势重建表以兼容旧版（如 Alinux 自带 SQLite）。

Revision ID: 0004_drop_plan_code
Revises: 0003_drop_admin_key
Create Date: 2026-08-19
"""
from alembic import op

revision = "0004_drop_plan_code"
down_revision = "0003_drop_admin_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("PRAGMA foreign_keys=OFF")
    op.execute("""
        CREATE TABLE cards_new (
            card_key            TEXT PRIMARY KEY,
            project_id          INTEGER NOT NULL REFERENCES projects(id),
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
    op.execute("""
        INSERT INTO cards_new (card_key, project_id, expires_at, status,
                               remark, renewed_from, reminded_at,
                               expired_notified_at, created_at)
        SELECT card_key, project_id, expires_at, status,
               remark, renewed_from, reminded_at,
               expired_notified_at, created_at FROM cards
    """)
    op.execute("DROP TABLE cards")
    op.execute("ALTER TABLE cards_new RENAME TO cards")
    op.execute("CREATE INDEX idx_cards_project ON cards(project_id)")
    op.execute("CREATE INDEX idx_cards_expires ON cards(expires_at)")
    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    # ADD COLUMN 老版本 SQLite 也支持；回填的值无法恢复，置空字符串占位
    op.execute("ALTER TABLE cards ADD COLUMN plan_code TEXT NOT NULL DEFAULT ''")
