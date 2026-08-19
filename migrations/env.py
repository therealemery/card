"""
Alembic 环境：连接串从 config 的 SQLite 路径拼成 sqlalchemy URL，不写进 alembic.ini。
"""
from logging.config import fileConfig

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from config import sqlite_path  # noqa: E402  （config 缺失变量会直接拒启，符合预期）

# Alembic 不经过 database.init_db，这里自己保证目录存在
_db_file = sqlite_path()
os.makedirs(os.path.dirname(os.path.abspath(_db_file)), exist_ok=True)

config = context.config
config.set_main_option("sqlalchemy.url", f"sqlite:///{_db_file}")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 没用 ORM，迁移脚本里直接 op.execute，无 autogenerate 元数据
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
