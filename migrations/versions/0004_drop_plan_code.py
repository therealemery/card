"""cards 表删除 plan_code 列（套餐概念废弃，授权只认账号 + 期限）

plan_code 无索引无约束，SQLite 3.35+ 直接 DROP COLUMN 即可。

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
    op.execute("ALTER TABLE cards DROP COLUMN plan_code")


def downgrade() -> None:
    op.execute("ALTER TABLE cards ADD COLUMN plan_code TEXT NOT NULL DEFAULT ''")
