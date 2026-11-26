"""Первая ревизия: схема data/120 §2.

Ревизия `0001_initial` создаёт все таблицы, ограничения и индексы документа
дословно; откат удаляет их в обратном порядке (data/120 §9).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "genres",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_norm", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tmdb_id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("name_norm"),
        sqlite_autoincrement=True,
    )

    op.create_table(
        "movies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_norm", sa.Text(), nullable=False),
        sa.Column("original_title", sa.Text(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("runtime", sa.Integer(), nullable=True),
        sa.Column("rating", sa.REAL(), nullable=True),
        sa.Column("vote_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("popularity", sa.REAL(), nullable=True),
        sa.Column("age_rating", sa.Text(), nullable=True),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column("poster_path", sa.Text(), nullable=True),
        sa.Column("adult", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("runtime IS NULL OR runtime > 0"),
        sa.CheckConstraint("rating IS NULL OR rating BETWEEN 0 AND 10"),
        sa.CheckConstraint("vote_count >= 0"),
        sa.CheckConstraint(
            "age_rating IS NULL OR age_rating IN ('0+','6+','12+','16+','18+')"
        ),
        sa.CheckConstraint("adult IN (0, 1)"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tmdb_id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_movies_year", "movies", ["year"])
    op.create_index("ix_movies_title_norm", "movies", ["title_norm"])
    # Частичный индекс: rating IS NOT NULL (data/120 §2).
    op.create_index(
        "ix_movies_rating",
        "movies",
        [sa.text("rating DESC"), sa.text("vote_count DESC"), sa.text("id")],
        sqlite_where=sa.text("rating IS NOT NULL"),
    )

    op.create_table(
        "movie_genres",
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("genre_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["genre_id"], ["genres.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("movie_id", "genre_id"),
    )
    op.create_index("ix_movie_genres_genre", "movie_genres", ["genre_id", "movie_id"])

    op.create_table(
        "people",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tmdb_id"),
        sqlite_autoincrement=True,
    )

    op.create_table(
        "movie_cast",
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("billing_order", sa.Integer(), nullable=False),
        sa.Column("character", sa.Text(), nullable=True),
        sa.CheckConstraint("billing_order BETWEEN 0 AND 9"),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("movie_id", "person_id"),
    )
    op.create_index("ix_movie_cast_movie", "movie_cast", ["movie_id", "billing_order"])
    op.create_index("ix_movie_cast_person", "movie_cast", ["person_id", "movie_id"])

    op.create_table(
        "users",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("consent_at", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("latitude", sa.REAL(), nullable=True),
        sa.Column("longitude", sa.REAL(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("source IN ('web')"),
        sa.CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90"),
        sa.CheckConstraint("longitude IS NULL OR longitude BETWEEN -180 AND 180"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "user_ratings",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("mood_match", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("rated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("rating BETWEEN 1 AND 10"),
        sa.CheckConstraint("mood_match IS NULL OR mood_match IN ('yes','partly','no')"),
        sa.CheckConstraint("source IN ('after_choice','manual')"),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "movie_id"),
    )
    op.create_index(
        "ix_user_ratings_date",
        "user_ratings",
        ["user_id", sa.text("rated_at DESC")],
    )

    op.create_table(
        "user_choices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("mood", sa.Text(), nullable=True),
        sa.Column("chosen_at", sa.Text(), nullable=False),
        sa.Column("closed_at", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('pending','rated','dismissed')"),
        sa.CheckConstraint(
            "mood IS NULL OR mood IN ('calm','cheerful','sad','tired','tense','other')"
        ),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_user_choices_user", "user_choices", ["user_id", sa.text("chosen_at DESC")])
    # Не более одного pending на пользователя (data/120 §2).
    op.create_index(
        "ux_user_choices_pending",
        "user_choices",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "sessions",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_sessions_expires", "sessions", ["expires_at"])

    op.create_table(
        "catalog_loads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("movies_upserted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("movies_skipped", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_page", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("status IN ('running','completed','partial','failed')"),
        sa.CheckConstraint("last_page >= 0"),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_catalog_loads_state",
        "catalog_loads",
        ["status", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("catalog_loads")
    op.drop_table("sessions")
    op.drop_table("user_choices")
    op.drop_table("user_ratings")
    op.drop_table("users")
    op.drop_table("movie_cast")
    op.drop_table("people")
    op.drop_table("movie_genres")
    op.drop_table("movies")
    op.drop_table("genres")
