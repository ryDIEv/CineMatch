"""ORM-модели SQLite: таблицы, ограничения, индексы, нормализация строк.

Единственный источник схемы — documents/data/120.data-model.md §2–§7.
Имена таблиц, столбцов, ограничений и индексов повторяют DDL документа;
ревизия `0001_initial.py` создаёт эту же схему (data/120 §9).
"""

from __future__ import annotations

from sqlalchemy import (
    REAL,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def normalize_text(value: str) -> str:
    """Нормализация строк для `title_norm` и `name_norm` — data/120 §4.

    `lower()`, `ё → е`, сжатие повторных пробелов и обрезка. `LIKE` и `lower()`
    в SQLite не понимают кириллицу, поэтому поиск идёт по этому значению.
    """
    return " ".join(value.lower().replace("ё", "е").split())


class Base(DeclarativeBase):
    """Базовый класс моделей модуля данных."""


class Genre(Base):
    """Справочник жанров TMDB — data/120 §2, §4."""

    __tablename__ = "genres"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name_norm: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class Movie(Base):
    """Каталог фильмов — data/120 §2, §3."""

    __tablename__ = "movies"
    __table_args__ = (
        CheckConstraint("runtime IS NULL OR runtime > 0"),
        CheckConstraint("rating IS NULL OR rating BETWEEN 0 AND 10"),
        CheckConstraint("vote_count >= 0"),
        CheckConstraint("age_rating IS NULL OR age_rating IN ('0+','6+','12+','16+','18+')"),
        CheckConstraint("adult IN (0, 1)"),
        Index(
            "ix_movies_rating",
            text("rating DESC"),
            text("vote_count DESC"),
            text("id"),
            sqlite_where=text("rating IS NOT NULL"),
        ),
        Index("ix_movies_year", "year"),
        Index("ix_movies_title_norm", "title_norm"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    title_norm: Mapped[str] = mapped_column(Text, nullable=False)
    original_title: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    runtime: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[float | None] = mapped_column(REAL)
    vote_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    popularity: Mapped[float | None] = mapped_column(REAL)
    age_rating: Mapped[str | None] = mapped_column(Text)
    overview: Mapped[str | None] = mapped_column(Text)
    poster_path: Mapped[str | None] = mapped_column(Text)
    adult: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class MovieGenre(Base):
    """Связка «фильм — жанр» — data/120 §2, §4."""

    __tablename__ = "movie_genres"
    __table_args__ = (
        PrimaryKeyConstraint("movie_id", "genre_id"),
        Index("ix_movie_genres_genre", "genre_id", "movie_id"),
    )

    movie_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False
    )
    genre_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("genres.id", ondelete="RESTRICT"), nullable=False
    )


class Person(Base):
    """Актёр или член съёмочной группы — data/120 §2, §4."""

    __tablename__ = "people"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class MovieCast(Base):
    """Первые 10 актёров фильма — data/120 §2, §4."""

    __tablename__ = "movie_cast"
    __table_args__ = (
        PrimaryKeyConstraint("movie_id", "person_id"),
        CheckConstraint("billing_order BETWEEN 0 AND 9"),
        Index("ix_movie_cast_movie", "movie_id", "billing_order"),
        Index("ix_movie_cast_person", "person_id", "movie_id"),
    )

    movie_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    billing_order: Mapped[int] = mapped_column(Integer, nullable=False)
    character: Mapped[str | None] = mapped_column(Text)


class User(Base):
    """Пользователь — data/120 §2, §5."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("source IN ('web')"),
        CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90"),
        CheckConstraint("longitude IS NULL OR longitude BETWEEN -180 AND 180"),
    )

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    consent_at: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(REAL)
    longitude: Mapped[float | None] = mapped_column(REAL)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class UserRating(Base):
    """Оценка пользователя — data/120 §2, §5."""

    __tablename__ = "user_ratings"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "movie_id"),
        CheckConstraint("rating BETWEEN 1 AND 10"),
        CheckConstraint("mood_match IS NULL OR mood_match IN ('yes','partly','no')"),
        CheckConstraint("source IN ('after_choice','manual')"),
        Index("ix_user_ratings_date", "user_id", text("rated_at DESC")),
    )

    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    movie_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    mood_match: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    rated_at: Mapped[str] = mapped_column(Text, nullable=False)


class UserChoice(Base):
    """Выбор фильма — data/120 §2, §5, ADR-050 (поле `mood`)."""

    __tablename__ = "user_choices"
    __table_args__ = (
        CheckConstraint("status IN ('pending','rated','dismissed')"),
        CheckConstraint(
            "mood IS NULL OR mood IN ('calm','cheerful','sad','tired','tense','other')"
        ),
        Index("ix_user_choices_user", "user_id", text("chosen_at DESC")),
        Index(
            "ux_user_choices_pending",
            "user_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    movie_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    mood: Mapped[str | None] = mapped_column(Text)
    chosen_at: Mapped[str] = mapped_column(Text, nullable=False)
    closed_at: Mapped[str | None] = mapped_column(Text)


class SessionRow(Base):
    """Состояние диалога — data/120 §6; `state_json` не интерпретируется."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_expires", "expires_at"),)

    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class CatalogLoad(Base):
    """Журнал запусков загрузки каталога — data/120 §2, §7."""

    __tablename__ = "catalog_loads"
    __table_args__ = (
        CheckConstraint("status IN ('running','completed','partial','failed')"),
        CheckConstraint("last_page >= 0"),
        Index("ix_catalog_loads_state", "status", text("started_at DESC")),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    movies_upserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    movies_skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_page: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
