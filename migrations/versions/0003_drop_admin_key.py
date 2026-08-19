"""projects 表删除 admin_key 列（管理面改走面板 admin session，项目级管理密钥废弃）

SQLite 的 ALTER TABLE DROP COLUMN 不支持带 UNIQUE 约束的列，按官方姿势重建表。
cards 的外键指向 projects(id)，重建期间临时关外键检查。

Revision ID: 0003_drop_admin_key
Revises: 0002_admin_sessions
Create Date: 2026-08-19
"""
from alembic import op

revision = "0003_drop_admin_key"
down_revision = "0002_admin_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("PRAGMA foreign_keys=OFF")
    op.execute("""
        CREATE TABLE projects_new (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL UNIQUE,
            resolve_token  TEXT NOT NULL UNIQUE,
            callback_url   TEXT,
            created_at     TEXT NOT NULL
        )
    """)
    op.execute("""
        INSERT INTO projects_new (id, name, resolve_token, callback_url, created_at)
        SELECT id, name, resolve_token, callback_url, created_at FROM projects
    """)
    op.execute("DROP TABLE projects")
    op.execute("ALTER TABLE projects_new RENAME TO projects")
    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    # 回填的 admin_key 无法恢复，置空字符串占位（该列本就要废弃）
    op.execute("PRAGMA foreign_keys=OFF")
    op.execute("""
        CREATE TABLE projects_old (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL UNIQUE,
            admin_key      TEXT NOT NULL UNIQUE,
            resolve_token  TEXT NOT NULL UNIQUE,
            callback_url   TEXT,
            created_at     TEXT NOT NULL
        )
    """)
    op.execute("""
        INSERT INTO projects_old (id, name, admin_key, resolve_token, callback_url, created_at)
        SELECT id, name, 'deleted-' || id, resolve_token, callback_url, created_at FROM projects
    """)
    op.execute("DROP TABLE projects")
    op.execute("ALTER TABLE projects_old RENAME TO projects")
    op.execute("PRAGMA foreign_keys=ON")
