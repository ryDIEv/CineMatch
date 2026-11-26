"""Запросы таблицы `users` — data/120 §5, data/130 §4.10–4.13.

`save_consent` не перезаписывает первое согласие: `consent_at` и `created_at`
остаются от первой записи (data/146 §6).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from cinematch.data.db import utc_now
from cinematch.data.models import User


def get_user(session: Session, user_id: str) -> User | None:
    """Строка `users` или `None` — data/130 §4.10."""
    return session.get(User, user_id)


def save_consent(session: Session, user_id: str, source: str) -> None:
    """Создать пользователя с согласием; повтор не перезаписывает время.

    Семантика `INSERT … ON CONFLICT(user_id) DO UPDATE`: `source` обновляется,
    `consent_at = COALESCE(consent_at, now)`, `created_at` не меняется
    (data/146 §6).
    """
    now = utc_now()
    user = session.get(User, user_id)
    if user is None:
        session.add(
            User(
                user_id=user_id,
                source=source,
                consent_at=now,
                city=None,
                latitude=None,
                longitude=None,
                created_at=now,
            )
        )
        return
    user.source = source
    if user.consent_at is None:
        user.consent_at = now


def save_settings(
    session: Session, user_id: str, city: str, latitude: float, longitude: float
) -> bool:
    """Обновить город и координаты; `False` — пользователя нет (data/130 §4.13)."""
    user = session.get(User, user_id)
    if user is None:
        return False
    user.city = city
    user.latitude = latitude
    user.longitude = longitude
    return True


def delete_user(session: Session, user_id: str) -> None:
    """Удалить строку `users`; каскад уносит оценки, выборы и сессию (ADR-016)."""
    user = session.get(User, user_id)
    if user is not None:
        session.delete(user)
