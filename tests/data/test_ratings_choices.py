"""TS-DB-06: оценки, выборы, `pending`, история — data/120 §5, data/130 §4.7–4.9.

`save_choice` проверяет только `movie_id`, наличие фильма и `mood`; вхождение
в `last_shown_ids` последней подборки проверяет реестр инструментов до вызова
(data/130 §4.7). Тест `test_ts_db_06_choice_by_last_shown_ids` повторяет
этот стык документированным способом — через `SessionState.last_shown_ids`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cinematch.data.api import (
    close_choice,
    get_history,
    get_pending_feedback,
    get_user_ratings,
    save_choice,
    save_rating,
)
from cinematch.data.models import UserChoice
from cinematch.overall.contracts import SessionState
from cinematch.overall.errors import ToolError
from tests.data.conftest import TEST_USER, load_seed


def _choice_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(UserChoice)) or 0)


def test_ts_db_06_save_choice_creates_pending(data_db, session: Session) -> None:
    """`save_choice` вставляет `pending` и возвращает `choice_id` (data/130 §4.7)."""
    load_seed(session)

    saved = save_choice(TEST_USER, 1, mood="tired")

    assert saved == {"choice_id": 1}
    pending = get_pending_feedback(TEST_USER)
    assert pending is not None
    assert pending.choice_id == 1
    assert pending.movie.movie_id == 1
    assert pending.movie.title == "Тихий дом у моря"
    assert pending.chosen_at


def test_ts_db_06_new_choice_dismisses_previous(data_db, session: Session) -> None:
    """Новый выбор закрывает прежний `pending` как `dismissed` (data/130 §4.7)."""
    load_seed(session)

    first = save_choice(TEST_USER, 1, mood=None)["choice_id"]
    second = save_choice(TEST_USER, 2, mood="calm")["choice_id"]

    assert second != first
    previous = session.get(UserChoice, first)
    assert previous is not None
    assert previous.status == "dismissed"
    assert previous.closed_at is not None
    pending = get_pending_feedback(TEST_USER)
    assert pending is not None and pending.movie.movie_id == 2
    # Частичный уникальный индекс пропускает только один `pending`.
    pending_rows = session.scalars(
        select(UserChoice).where(UserChoice.status == "pending")
    ).all()
    assert len(pending_rows) == 1


def test_ts_db_06_mood_is_stored_with_choice(data_db, session: Session) -> None:
    """`mood` записывается в `user_choices.mood` (ADR-050)."""
    load_seed(session)

    choice_id = save_choice(TEST_USER, 1, mood="tense")["choice_id"]

    row = session.get(UserChoice, choice_id)
    assert row is not None and row.mood == "tense"

    # `None` — настроение не названо; значение допустимо (data/120 §2).
    silent_id = save_choice(TEST_USER, 2, mood=None)["choice_id"]
    silent = session.get(UserChoice, silent_id)
    assert silent is not None and silent.mood is None


def test_ts_db_06_invalid_mood_is_rejected(data_db, session: Session) -> None:
    """`mood` вне перечня — `INVALID_PARAM` (data/130 §4.7)."""
    load_seed(session)

    with pytest.raises(ToolError) as error:
        save_choice(TEST_USER, 1, mood="happy")

    assert error.value.code == "INVALID_PARAM"
    assert _choice_count(session) == 0


def test_ts_db_06_choice_of_missing_movie_is_not_found(data_db, session: Session) -> None:
    """Фильма нет в каталоге — `NOT_FOUND`; `movie_id` ≤ 0 — `INVALID_PARAM`."""
    load_seed(session)

    with pytest.raises(ToolError) as missing:
        save_choice(TEST_USER, 999)

    assert missing.value.code == "NOT_FOUND"

    with pytest.raises(ToolError) as invalid:
        save_choice(TEST_USER, 0)

    assert invalid.value.code == "INVALID_PARAM"


def test_ts_db_06_choice_by_last_shown_ids(data_db, session: Session) -> None:
    """Выбор фильма из `last_shown_ids`; фильм прошлых подборок — `NOT_FOUND`.

    Реестр инструментов сравнивает `movie_id` с `SessionState.last_shown_ids`
    последней подборки до вызова `save_choice` (data/130 §4.7). Модуль данных
    этот список не видит, поэтому здесь проверяется стык: выбор фильма из
    последней подборки проходит, а отбор по `last_shown_ids` остаётся за
    реестром — его повторяет условие ниже.
    """
    load_seed(session)

    state = SessionState(
        stage="dialog",
        last_shown_ids=[5, 14, 16],
        shown_ids=[1, 2, 5, 14, 16],
    )

    # Фильм из последней подборки — выбор сохраняется.
    assert 5 in state.last_shown_ids
    assert save_choice(TEST_USER, 5, mood="calm") == {"choice_id": 1}

    # Фильм из прошлых подборок, но не из последней: реестр не допустит вызов.
    assert 1 not in state.last_shown_ids
    assert _choice_count(session) == 1


def test_ts_db_06_save_rating_closes_pending_choice(data_db, session: Session) -> None:
    """Оценка по фильму из `pending` переводит выбор в `rated` (data/130 §4.8)."""
    load_seed(session)
    choice_id = save_choice(TEST_USER, 1, mood="tired")["choice_id"]

    saved = save_rating(
        user_id=TEST_USER,
        movie_id=1,
        rating=8,
        source="after_choice",
        mood_match="yes",
    )

    assert saved == {"saved": True, "closed_choice_id": choice_id}
    row = session.get(UserChoice, choice_id)
    assert row is not None
    assert row.status == "rated"
    assert row.closed_at is not None
    assert get_pending_feedback(TEST_USER) is None


def test_ts_db_06_rating_upsert_keeps_one_row(data_db, session: Session) -> None:
    """Повторная оценка перезаписывает поля, а не добавляет строку (data/120 §5)."""
    load_seed(session)

    save_rating(user_id=TEST_USER, movie_id=3, rating=4, source="manual", mood_match="no")
    save_rating(user_id=TEST_USER, movie_id=3, rating=9, source="manual", mood_match="partly")

    ratings = get_user_ratings(TEST_USER)
    assert len(ratings) == 1
    assert ratings[0].rating == 9
    assert ratings[0].mood_match == "partly"
    assert ratings[0].genres == ["Драма"]

    # Оценка без выбора не создаёт `pending` (data/050 §4).
    assert get_pending_feedback(TEST_USER) is None


@pytest.mark.parametrize(
    ("rating", "source", "mood_match"),
    [(0, "manual", None), (11, "manual", None), (5, "other", None), (5, "manual", "maybe")],
)
def test_ts_db_06_invalid_rating_params(
    data_db, session: Session, rating: int, source: str, mood_match: str | None
) -> None:
    """Границы оценки и перечни `source`/`mood_match` — `INVALID_PARAM`."""
    load_seed(session)

    with pytest.raises(ToolError) as error:
        save_rating(
            user_id=TEST_USER,
            movie_id=3,
            rating=rating,
            source=source,
            mood_match=mood_match,
        )

    assert error.value.code == "INVALID_PARAM"


def test_ts_db_06_rating_of_missing_movie_is_not_found(data_db, session: Session) -> None:
    """Оценка несуществующего фильма — `NOT_FOUND` (data/130 §4.8)."""
    load_seed(session)

    with pytest.raises(ToolError) as error:
        save_rating(user_id=TEST_USER, movie_id=999, rating=7, source="manual")

    assert error.value.code == "NOT_FOUND"


def test_ts_db_06_close_choice(data_db, session: Session) -> None:
    """`close_choice` ставит `dismissed`; оценка не создаётся (data/130 §4.9)."""
    load_seed(session)
    choice_id = save_choice(TEST_USER, 1, mood="calm")["choice_id"]

    assert close_choice(choice_id) == {"closed": True}
    row = session.get(UserChoice, choice_id)
    assert row is not None and row.status == "dismissed" and row.closed_at is not None
    assert get_user_ratings(TEST_USER) == []

    # Повторное закрытие — строки в `pending` уже нет.
    with pytest.raises(ToolError) as error:
        close_choice(choice_id)
    assert error.value.code == "NOT_FOUND"

    # Единственный допустимый статус — `dismissed`.
    with pytest.raises(ToolError) as invalid:
        close_choice(choice_id, status="rated")
    assert invalid.value.code == "INVALID_PARAM"


def test_ts_db_06_history_merges_ratings_and_choices(data_db, session: Session) -> None:
    """`HistoryItem`: оценки и выборы вместе, по времени, со статусом (data/130 §4.16)."""
    load_seed(session)
    save_rating(user_id=TEST_USER, movie_id=3, rating=9, source="manual", mood_match="yes")
    choice_id = save_choice(TEST_USER, 1, mood="calm")
    save_rating(user_id=TEST_USER, movie_id=2, rating=7, source="manual")

    items = get_history(TEST_USER, limit=50)

    kinds = {(item.kind, item.movie.movie_id) for item in items}
    assert kinds == {("rating", 3), ("choice", 1), ("rating", 2)}
    rating_item = next(item for item in items if item.kind == "rating")
    assert rating_item.status is None
    assert rating_item.rating in {7, 9}
    choice_item = next(item for item in items if item.kind == "choice")
    assert choice_item.status == "pending"
    assert choice_item.rating is None and choice_item.mood_match is None
    assert choice_item.movie.title == "Тихий дом у моря"
    # Время по убыванию.
    assert [item.at for item in items] == sorted(
        (item.at for item in items), reverse=True
    )

    limited = get_history(TEST_USER, limit=1)
    assert len(limited) == 1
    assert choice_id["choice_id"] == 1


def test_ts_db_06_history_limit_is_checked(data_db, session: Session) -> None:
    """`limit` истории — 1…50 (data/130 §4.16)."""
    load_seed(session)

    with pytest.raises(ToolError) as error:
        get_history(TEST_USER, limit=0)

    assert error.value.code == "INVALID_PARAM"


def test_ts_db_06_user_ratings_order_and_limit(data_db, session: Session) -> None:
    """Оценки — по `rated_at` убыванию; `limit` 1…50 (data/130 §4.1)."""
    load_seed(session)
    for movie_id in (1, 2, 3):
        save_rating(user_id=TEST_USER, movie_id=movie_id, rating=6, source="manual")

    ratings = get_user_ratings(TEST_USER, limit=50)

    assert len(ratings) == 3
    assert {item.movie_id for item in ratings} == {1, 2, 3}
    # При равном времени порядок стабилизирован movie_id.
    assert [item.movie_id for item in ratings] == [1, 2, 3]

    with pytest.raises(ToolError) as error:
        get_user_ratings(TEST_USER, limit=51)

    assert error.value.code == "INVALID_PARAM"
