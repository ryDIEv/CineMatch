"""Подключение к SQLite и инициализация базы — data/120 §1, build.md §3.

Параметры соединения (`foreign_keys = ON`, `journal_mode = WAL`, `busy_timeout`,
`synchronous`, `temp_store`) задаются один раз в обработчике события `connect`
и в запросах не повторяются (data/120 §1, ADR-011).

`init_database()` — шаги 3–5 порядка старта backend (build.md §3): создать папку
файла базы, применить миграции `alembic upgrade head`, удалить просроченные
сессии. Вызывает API-слой при старте; это стык модуля данных и интерфейса,
оговорённый в задаче.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from cinematch.overall.config import DEFAULT_DB_PATH, ENV_FILE, _parse_env_file
from cinematch.overall.errors import ErrorCode, ServiceError

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
MIGRATIONS_DIR = ROOT / "migrations"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_configured_path: Path | None = None


def utc_now() -> str:
    """Текущий момент в ISO 8601 UTC: `2026-11-08T18:15:00Z` (data/120 §1)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_db_path() -> Path:
    """Путь к файлу базы по `CINEMATCH_DB_PATH` (config.md §1).

    Окружение процесса важнее `.env`; пустое значение — значение по умолчанию
    `storage/cinematch.db` (config.md §1, п. 2 и 6).
    """
    raw = os.environ.get("CINEMATCH_DB_PATH", "").strip()
    if not raw:
        raw = _parse_env_file(ENV_FILE).get("CINEMATCH_DB_PATH", "").strip()
    return Path(raw or DEFAULT_DB_PATH)


def sqlite_url(path: Path) -> str:
    """URL SQLAlchemy для файла: `sqlite+pysqlite:///…` (ADR-029)."""
    return f"sqlite+pysqlite:///{path.resolve().as_posix()}"


def _prepare_engine(path: Path) -> Engine:
    """Создать движок и включить параметры соединения из data/120 §1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        sqlite_url(path),
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()

    return engine


def configure(db_path: str | Path | None = None) -> Engine:
    """Пересоздать движок: тесты передают временный путь, backend берёт из env."""
    global _engine, _session_factory, _configured_path
    if _engine is not None:
        _engine.dispose()
    _configured_path = Path(db_path) if db_path is not None else resolve_db_path()
    _engine = _prepare_engine(_configured_path)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    """Движок базы; создаётся при первом обращении."""
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def get_db_path() -> Path:
    """Фактический путь файла базы текущего движка."""
    if _configured_path is None:
        return resolve_db_path()
    return _configured_path


def get_session_factory() -> sessionmaker[Session]:
    """Фабрика сессий; создаётся вместе с движком."""
    if _session_factory is None:
        configure()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Одна транзакция на вызов: коммит при успехе, откат при ошибке."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def translate_db_errors() -> Iterator[None]:
    """Недоступность базы для пользователя — `ServiceError(DATA_UNAVAILABLE)`.

    Переводится только `OperationalError` — это `database is locked` после
    `busy_timeout`, ошибка диска или отсутствующий файл (data/130 §6). Нарушение
    `CHECK`/`FOREIGN KEY` — программная ошибка, её перехват скрыл бы дефект кода.
    """
    try:
        yield
    except OperationalError as error:
        raise ServiceError(ErrorCode.DATA_UNAVAILABLE) from error


def init_database(db_path: str | Path | None = None) -> None:
    """Создать папку базы, применить `alembic upgrade head`, очистить сессии.

    Порядок старта backend — build.md §3, шаги 3–5; data/120 §9.
    """
    path = Path(db_path) if db_path is not None else resolve_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    configure(path)

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", sqlite_url(path))
    command.upgrade(config, "head")

    # Локальный импорт: api.py использует движок из этого модуля.
    from cinematch.data.api import purge_expired

    purge_expired()
