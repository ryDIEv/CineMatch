"""Схема и миграция: таблицы, ограничения, индексы — data/120 §2, §9 (TS-DB-10).

Сеть не используется. Проверяются: `alembic upgrade head`, откат `-1`, параметры
соединения из data/120 §1, `CHECK`-ограничения и индексы из §2.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cinematch.data import db
from cinematch.data.db import init_database
from cinematch.data.models import Genre, Movie, User, UserChoice, normalize_text
from tests.data.conftest import TEST_USER, load_seed

ROOT = Path(__file__).resolve().parents[2]

TABLES = {
    "genres",
    "movies",
    "movie_genres",
    "people",
    "movie_cast",
    "users",
    "user_ratings",
    "user_choices",
    "sessions",
    "catalog_loads",
    "alembic_version",
}

INDEXES = {
    "ix_movies_rating",
    "ix_movies_year",
    "ix_movies_title_norm",
    "ix_movie_genres_genre",
    "ix_movie_cast_movie",
    "ix_movie_cast_person",
    "ix_user_ratings_date",
    "ix_user_choices_user",
    "ux_user_choices_pending",
    "ix_sessions_expires",
    "ix_catalog_loads_state",
}


def _alembic_config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_ts_db_10_upgrade_and_downgrade_pass(tmp_path: Path) -> None:
    """`alembic upgrade head` создаёт схему, `downgrade -1` убирает её (§9)."""
    path = tmp_path / "schema.db"
    config = _alembic_config(path)

    command.upgrade(config, "head")
    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
    assert tables >= TABLES
    conn.close()

    command.downgrade(config, "-1")
    conn = sqlite3.connect(path)
    left = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
    assert "movies" not in left
    assert "alembic_version" in left
    conn.close()


def test_ts_db_10_connection_pragmas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Параметры соединения: WAL, внешние ключи, busy_timeout — data/120 §1."""
    path = tmp_path / "pragma.db"
    monkeypatch.setenv("CINEMATCH_DB_PATH", str(path))
    init_database(path)
    engine = db.get_engine()
    try:
        with engine.connect() as connection:
            values = {
                name: connection.exec_driver_sql(f"PRAGMA {name}").scalar()
                for name in ("foreign_keys", "journal_mode", "busy_timeout")
            }
        assert values["foreign_keys"] == 1
        assert str(values["journal_mode"]).lower() == "wal"
        assert values["busy_timeout"] == 5000
    finally:
        db._engine = None
        db._session_factory = None
        db._configured_path = None
        engine.dispose()


def test_ts_db_10_foreign_keys_cascade(data_db, session: Session) -> None:
    """`ON DELETE CASCADE` от `users` работает при включённых внешних ключах."""
    load_seed(session)
    user = session.get(User, TEST_USER)
    assert user is not None

    session.delete(user)
    session.commit()

    assert session.query(UserChoice).count() == 0


def test_ts_db_10_check_constraints(data_db, session: Session) -> None:
    """`CHECK` схемы: рейтинг, длительность, возраст, перечни (data/120 §2)."""
    load_seed(session)

    session.add(
        Movie(
            tmdb_id=900001,
            title="Плохой рейтинг",
            title_norm="плохой рейтинг",
            year=2000,
            runtime=100,
            rating=11,
            vote_count=10,
            updated_at="2026-11-26T00:00:00Z",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.add(
        UserChoice(
            user_id=TEST_USER,
            movie_id=1,
            status="pending",
            mood="happy",
            chosen_at="2026-11-26T00:00:00Z",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_ts_db_10_indexes_exist(data_db, session: Session) -> None:
    """Индексы из data/120 §2 созданы миграцией."""
    rows = session.execute(text("SELECT name FROM sqlite_master WHERE type='index'")).all()
    names = {row[0] for row in rows}
    assert names >= INDEXES


def test_ts_db_10_normalize_text_rules() -> None:
    """Нормализация: `lower`, `ё → е`, сжатые пробелы (data/120 §4)."""
    assert normalize_text("  Ёлка   и   Дом ") == "елка и дом"
    assert normalize_text("Комедия") == "комедия"
    assert normalize_text("Тихий  ДОМ") == "тихий дом"


def test_ts_db_10_seed_genres_are_normalized(data_db, session: Session) -> None:
    """Фикстура создаёт нормализованные названия жанров: «Ужасы» → «ужасы»."""
    load_seed(session)
    genres = {genre.name_norm for genre in session.query(Genre).all()}
    assert "ужасы" in genres and "драма" in genres
