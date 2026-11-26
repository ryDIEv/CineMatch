"""TS-DB-08: удаление данных пользователя — data/120 §10, data/130 §4.17, ADR-016.

Каскад работает только при `PRAGMA foreign_keys = ON` на соединении (data/120 §1,
п. 2); тест проверяет и `deleted_counts`, и отсутствие пользовательских строк,
и целостность каталога.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cinematch.data.api import (
    delete_user_data,
    get_session_state,
    get_user,
    purge_expired,
    save_choice,
    save_consent,
    save_rating,
    save_session_state,
)
from cinematch.data.models import Movie, MovieGenre, SessionRow, UserChoice, UserRating
from cinematch.overall.contracts import SessionState
from tests.data.conftest import TEST_USER, load_seed


def _count(session: Session, model: object) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_ts_db_08_deletes_user_rows_and_keeps_catalog(data_db, session: Session) -> None:
    """`delete_user_data`: счётчики, каскад, каталог цел (data/120 §10)."""
    load_seed(session)
    movies_before = _count(session, Movie)
    genres_before = _count(session, MovieGenre)

    save_rating(user_id=TEST_USER, movie_id=1, rating=8, source="manual")
    save_rating(user_id=TEST_USER, movie_id=2, rating=7, source="manual")
    save_choice(TEST_USER, 3, mood="calm")
    save_session_state(TEST_USER, SessionState(stage="dialog"))

    result = delete_user_data(TEST_USER)
    session.expire_all()

    assert result == {
        "deleted_counts": {
            "user_ratings": 2,
            "user_choices": 1,
            "sessions": 1,
            "users": 1,
        }
    }
    assert list(result["deleted_counts"]) == ["user_ratings", "user_choices", "sessions", "users"]

    # Пользовательских строк не осталось: каскад сработал (ADR-016).
    assert get_user(TEST_USER) is None
    assert _count(session, UserRating) == 0
    assert _count(session, UserChoice) == 0
    assert _count(session, SessionRow) == 0
    assert get_session_state(TEST_USER) is None

    # Каталог, жанры и оценки не тронуты.
    assert _count(session, Movie) == movies_before == 23
    assert _count(session, MovieGenre) == genres_before == len(_genre_rows(session))


def _genre_rows(session: Session) -> list[object]:
    return list(session.scalars(select(MovieGenre)))


def test_ts_db_08_deletion_is_idempotent(data_db, session: Session) -> None:
    """Отсутствующий пользователь — нули во всех ключах, без ошибки (§4.17)."""
    load_seed(session)
    delete_user_data(TEST_USER)

    again = delete_user_data(TEST_USER)

    assert again == {
        "deleted_counts": {
            "user_ratings": 0,
            "user_choices": 0,
            "sessions": 0,
            "users": 0,
        }
    }
    assert get_user(TEST_USER) is None


def test_ts_db_08_deletion_does_not_touch_other_users(data_db, session: Session) -> None:
    """Данные другого пользователя остаются: функция работает только с `user_id`."""
    load_seed(session)
    other = "web:other"
    save_consent(other, "web")
    save_rating(user_id=other, movie_id=1, rating=9, source="manual")
    save_rating(user_id=TEST_USER, movie_id=2, rating=7, source="manual")

    delete_user_data(TEST_USER)
    session.expire_all()

    assert get_user(other) is not None
    assert _count(session, UserRating) == 1


def test_ts_db_08_purge_expired_removes_only_expired(data_db, session: Session) -> None:
    """`purge_expired` удаляет сессии с `expires_at <= now` (data/130 §4.18)."""
    load_seed(session)
    save_session_state(TEST_USER, SessionState(stage="dialog"))
    # Просроченная строка: записываем явно в прошлом.
    row = session.get(SessionRow, TEST_USER)
    assert row is not None
    row.expires_at = "2000-01-01T00:00:00Z"
    session.commit()

    removed = purge_expired()

    assert removed == 1
    assert _count(session, SessionRow) == 0


def test_ts_db_08_expired_session_is_dropped_on_read(data_db, session: Session) -> None:
    """Просроченная сессия удаляется при чтении и возвращает `None` (data/120 §6)."""
    load_seed(session)
    save_session_state(TEST_USER, SessionState(stage="dialog"))
    row = session.get(SessionRow, TEST_USER)
    assert row is not None
    row.expires_at = "2000-01-01T00:00:00Z"
    session.commit()

    assert get_session_state(TEST_USER) is None
    session.expire_all()
    assert _count(session, SessionRow) == 0


def test_ts_db_08_corrupted_session_is_dropped(data_db, session: Session) -> None:
    """Повреждённый JSON удаляется, возвращается `None` (data/120 §6, п. 3)."""
    load_seed(session)
    save_session_state(TEST_USER, SessionState(stage="dialog"))
    row = session.get(SessionRow, TEST_USER)
    assert row is not None
    row.state_json = "{не json"
    session.commit()

    assert get_session_state(TEST_USER) is None
    session.expire_all()
    assert _count(session, SessionRow) == 0


def test_ts_db_08_session_state_round_trip(data_db, session: Session) -> None:
    """`save_session_state` сериализует `SessionState` целиком (§4.14)."""
    load_seed(session)
    state = SessionState(
        stage="dialog",
        last_shown_ids=[1, 2, 3],
        shown_ids=[1, 2, 3],
        clarifications_asked=1,
        context={"mood": "tired", "available_minutes": 90},
    )

    save_session_state(TEST_USER, state)
    restored = get_session_state(TEST_USER)

    assert restored is not None
    assert restored.stage == "dialog"
    assert restored.last_shown_ids == [1, 2, 3]
    assert restored.context is not None
    assert restored.context.mood == "tired"
    raw = session.get(SessionRow, TEST_USER)
    assert raw is not None
    assert json.loads(raw.state_json)["stage"] == "dialog"


def test_ts_db_08_rating_requires_consent_user(data_db, session: Session) -> None:
    """Без строки `users` оценку записать нельзя: внешний ключ (ADR-016).

    Ошибка базы — не `ServiceError(DATA_UNAVAILABLE)`: отсутствие пользователя
    при оценке означает нарушенный порядок вызовов, а не недоступность базы.
    """
    load_seed(session)

    with pytest.raises(IntegrityError):
        save_rating(user_id="web:ghost", movie_id=1, rating=5, source="manual")

    session.rollback()
    assert _count(session, UserRating) == 0
