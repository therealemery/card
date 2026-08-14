"""
Alembic 环境：离线/在线均走 config.DATABASE_URL，不在 alembic.ini 里落连接串。
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config import DATABASE_URL  # noqa: E402  （config 缺失变量会直接拒启，符合预期）

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 没用 ORM，迁移脚本里直接 op.execute / op.create_table，无 autogenerate 元数据
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
