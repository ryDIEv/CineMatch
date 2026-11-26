"""Запросы `user_choices` — data/120 §5, data/130 §4.7, §4.9, §4.15, §4.16."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from cinematch.data.db import utc_now
from cinematch.data.models import UserChoice, UserRating


def count_choices(session: Session, user_id: str) -> int:
    """Сколько выборов у пользователя — для `deleted_counts` (data/146 §8)."""
    return int(
        session.scalar(
            select(func.count()).select_from(UserChoice).where(UserChoice.user_id == user_id)
        )
        or 0
    )


def dismiss_pending(session: Session, user_id: str) -> None:
    """Прежний `pending` → `dismissed` перед новым выбором (data/130 §4.7)."""
    session.execute(
        update(UserChoice)
        .where(UserChoice.user_id == user_id, UserChoice.status == "pending")
        .values(status="dismissed", closed_at=utc_now())
    )


def insert_pending(
    session: Session, *, user_id: str, movie_id: int, mood: str | None
) -> int:
    """Новый `pending` выбор; `mood` — настроение на момент выбора (ADR-050)."""
    choice = UserChoice(
        user_id=user_id,
        movie_id=movie_id,
        status="pending",
        mood=mood,
        chosen_at=utc_now(),
        closed_at=None,
    )
    session.add(choice)
    session.flush()
    return choice.id


def close_choice(session: Session, choice_id: int) -> bool:
    """`pending` → `dismissed`; `False` — строки нет или она не `pending`."""
    choice = session.get(UserChoice, choice_id)
    if choice is None or choice.status != "pending":
        return False
    choice.status = "dismissed"
    choice.closed_at = utc_now()
    return True


def pending_choice(session: Session, user_id: str) -> UserChoice | None:
    """Ожидающий выбор пользователя — источник истины (data/130 §4.15)."""
    return session.scalar(
        select(UserChoice).where(
            UserChoice.user_id == user_id, UserChoice.status == "pending"
        )
    )


def history_rows(session: Session, user_id: str, limit: int) -> list[tuple[str, object]]:
    """Оценки и выборы вместе, по убыванию времени (data/130 §4.16).

    Возврат — пары `(kind, строка)`, где строка — `UserRating` или `UserChoice`;
    сортировка выполняется в памяти по `rated_at` / `chosen_at`.
    """
    ratings = list(
        session.scalars(select(UserRating).where(UserRating.user_id == user_id))
    )
    choices = list(
        session.scalars(select(UserChoice).where(UserChoice.user_id == user_id))
    )
    merged: list[tuple[str, object]] = [("rating", row) for row in ratings]
    merged += [("choice", row) for row in choices]
    merged.sort(key=_history_key, reverse=True)
    return merged[:limit]


def _history_key(item: tuple[str, object]) -> str:
    """Время элемента истории: `rated_at` или `chosen_at`, затем стабильный ключ."""
    kind, row = item
    stamp = getattr(row, "rated_at", None) or getattr(row, "chosen_at", "")
    return f"{stamp}:{kind}:{getattr(row, 'movie_id', 0)}"
