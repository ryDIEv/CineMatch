"""Запросы `user_ratings` — data/120 §5, data/130 §4.8, data/146 §7."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cinematch.data.db import utc_now
from cinematch.data.models import UserChoice, UserRating


def count_ratings(session: Session, user_id: str) -> int:
    """Сколько оценок у пользователя — для `deleted_counts` (data/146 §8)."""
    return int(
        session.scalar(
            select(func.count()).select_from(UserRating).where(UserRating.user_id == user_id)
        )
        or 0
    )


def upsert_rating(
    session: Session,
    *,
    user_id: str,
    movie_id: int,
    rating: int,
    source: str,
    mood_match: str | None,
) -> None:
    """Одна оценка на пару: повтор перезаписывает поля (data/120 §5).

    Семантика `INSERT … ON CONFLICT(user_id, movie_id) DO UPDATE` (data/130 §4.8).
    """
    now = utc_now()
    row = session.get(UserRating, (user_id, movie_id))
    if row is None:
        session.add(
            UserRating(
                user_id=user_id,
                movie_id=movie_id,
                rating=rating,
                mood_match=mood_match,
                source=source,
                rated_at=now,
            )
        )
        return
    row.rating = rating
    row.mood_match = mood_match
    row.source = source
    row.rated_at = now


def close_pending_choice_for_movie(session: Session, user_id: str, movie_id: int) -> int | None:
    """Перевести `pending` по этому фильму в `rated` — data/146 §7."""
    choice = session.scalar(
        select(UserChoice).where(
            UserChoice.user_id == user_id,
            UserChoice.movie_id == movie_id,
            UserChoice.status == "pending",
        )
    )
    if choice is None:
        return None
    choice.status = "rated"
    choice.closed_at = utc_now()
    return choice.id
