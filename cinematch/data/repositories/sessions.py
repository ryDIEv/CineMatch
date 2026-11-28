"""Запросы `sessions` — data/120 §6, data/130 §4.14, §4.18."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from cinematch.data.db import utc_now
from cinematch.data.models import SessionRow

SESSION_TTL = timedelta(hours=24)


def session_stamp() -> tuple[str, str]:
    """`(updated_at, expires_at)`: момент записи и `now + 24 ч` (data/120 §6)."""
    now = datetime.now(UTC)
    return (
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        (now + SESSION_TTL).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def get_session(session: Session, user_id: str) -> SessionRow | None:
    """Строка сессии без проверки срока."""
    return session.get(SessionRow, user_id)


def save_session(session: Session, user_id: str, state_json: str) -> None:
    """Upsert состояния; каждое сохранение продлевает сессию (data/120 §6)."""
    updated_at, expires_at = session_stamp()
    row = session.get(SessionRow, user_id)
    if row is None:
        session.add(
            SessionRow(
                user_id=user_id,
                state_json=state_json,
                expires_at=expires_at,
                updated_at=updated_at,
            )
        )
    else:
        row.state_json = state_json
        row.expires_at = expires_at
        row.updated_at = updated_at


def delete_session(session: Session, user_id: str) -> None:
    """Удалить строку сессии; отсутствие — не ошибка (data/130 §4.14)."""
    row = session.get(SessionRow, user_id)
    if row is not None:
        session.delete(row)


def purge_expired(session: Session) -> int:
    """Удалить сессии с `expires_at <= now` — data/130 §4.18."""
    result = session.execute(
        delete(SessionRow).where(SessionRow.expires_at <= utc_now())
    )
    return int(result.rowcount or 0)


def count_sessions(session: Session, user_id: str) -> int:
    """Сколько сессий у пользователя — для `deleted_counts` (data/146 §8)."""
    return int(
        session.scalar(
            select(func.count()).select_from(SessionRow).where(SessionRow.user_id == user_id)
        )
        or 0
    )
