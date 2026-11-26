"""Окружение Alembic: путь к базе из конфигурации, метаданные моделей.

Схема создаётся первой ревизией `0001_initial.py` (data/120 §2, §9); этот файл
только связывает Alembic с моделями и файлом базы `CINEMATCH_DB_PATH`
(config.md §1). Путь можно переопределить атрибутом `sqlalchemy.url` конфигурации —
так поступает `cinematch/data/db.py::init_database()` для временной базы.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from cinematch.data import db
from cinematch.data.models import Base

config = context.config

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", db.sqlite_url(db.resolve_db_path()))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Сгенерировать SQL без подключения к базе."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Применить миграции к файлу базы (WAL и внешние ключи — в db.py)."""
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
