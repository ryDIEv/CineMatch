"""Служебные функции data/130 §4.6, §4.10–4.14, не входящие в TS-DB-01…11.

Сценарии `documents/testing/ts-db.md` покрывают фильтры, профиль, выборы,
удаление и схему; поиск по названию, согласие, настройки города и сессии
проверяются здесь — по правилам §4.6 и §4.10–4.14 того же контракта.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from cinematch.data.api import (
    clear_session_state,
    get_pending_feedback,
    get_session_state,
    get_user,
    get_user_ratings,
    get_weather,
    ingest_movies,
    purge_expired,
    resolve_city,
    save_consent,
    save_session_state,
    save_user_settings,
    search_movie,
)
from cinematch.data.models import SessionRow, User
from cinematch.data.schemas import Weather
from cinematch.overall.contracts import SessionState
from cinematch.overall.errors import ToolError
from tests.data.conftest import TEST_USER, load_seed

PAST = "2020-01-01T00:00:00Z"


def test_search_movie_order_and_limit(data_db, session: Session) -> None:
    """Точное совпадение, затем начало строки, затем подстрока; ≤ 5 (§4.6)."""
    load_seed(session)

    exact = search_movie("Северный свет")
    assert [item.movie_id for item in exact] == [3, 4]

    substring = search_movie("дом")
    # «Дом у дороги» начинается с запроса — выше «Тихого дома у моря».
    assert [item.movie_id for item in substring] == [7, 1]
    assert substring[0].title == "Дом у дороги"

    assert len(search_movie("о")) <= 5


def test_search_movie_year_and_empty_result(data_db, session: Session) -> None:
    """`year` сужает выдачу; пусто — `[]`, а не ошибка (§4.6)."""
    load_seed(session)

    assert [item.movie_id for item in search_movie("Северный свет", year=1987)] == [4]
    assert [item.movie_id for item in search_movie("Северный свет", year=2000)] == []
    assert search_movie("абракадабра") == []


@pytest.mark.parametrize(
    ("title", "year"),
    [("", None), ("   ", None), ("я" * 101, None), ("дом", 1899)],
)
def test_search_movie_invalid_params(
    data_db, session: Session, title: str, year: int | None
) -> None:
    """`title` 1…100 после обрезки, `year` 1900…2030 — `INVALID_PARAM` (§4.6)."""
    load_seed(session)

    with pytest.raises(ToolError) as error:
        search_movie(title, year=year)

    assert error.value.code == "INVALID_PARAM"


def test_get_user_none_and_fields(data_db, session: Session) -> None:
    """`get_user` отдаёт ровно поля §4.10; неизвестный — `None`."""
    assert get_user(TEST_USER) is None

    load_seed(session)

    user = get_user(TEST_USER)
    assert user is not None
    assert set(user) == {
        "user_id",
        "source",
        "consent_at",
        "city",
        "latitude",
        "longitude",
        "created_at",
    }
    assert user["user_id"] == TEST_USER and user["source"] == "web"


def test_save_consent_keeps_first_times(data_db, session: Session) -> None:
    """Повторное согласие не перезаписывает `consent_at` и `created_at` (§4.11)."""
    save_consent(TEST_USER, "web")
    row = session.get(User, TEST_USER)
    assert row is not None
    row.consent_at = PAST
    row.created_at = PAST
    session.commit()

    save_consent(TEST_USER, "web")
    session.expire_all()

    row = session.get(User, TEST_USER)
    assert row is not None
    assert row.consent_at == PAST and row.created_at == PAST
    # Город на этапе согласия не заполняется — его сохраняет §4.13.
    assert row.city is None and row.latitude is None


def test_save_consent_rejects_unknown_source(data_db) -> None:
    """`source` проверяется по `users.source` — единственное значение `web`."""
    with pytest.raises(ToolError) as error:
        save_consent(TEST_USER, "telegram")

    assert error.value.code == "INVALID_PARAM"
    assert get_user(TEST_USER) is None


def test_save_user_settings_updates_city(data_db, session: Session) -> None:
    """Город и координаты сохраняются вместе (data/120 §5, §4.13)."""
    load_seed(session)

    save_user_settings(TEST_USER, "Казань", 55.7963, 49.1088)
    session.expire_all()

    user = get_user(TEST_USER)
    assert user is not None
    assert (user["city"], user["latitude"], user["longitude"]) == ("Казань", 55.7963, 49.1088)


def test_save_user_settings_requires_user(data_db) -> None:
    """Без согласия пользователя нет — программная ошибка `ValueError` (§4.13)."""
    with pytest.raises(ValueError):
        save_user_settings("web:ghost", "Казань", 55.7963, 49.1088)


@pytest.mark.parametrize(
    ("city", "latitude", "longitude"),
    [("", 55.0, 49.0), ("Казань", 91.0, 49.0), ("Казань", 55.0, -181.0)],
)
def test_save_user_settings_invalid_params(
    data_db, session: Session, city: str, latitude: float, longitude: float
) -> None:
    """`city` 1…100, широта −90…90, долгота −180…180 — `INVALID_PARAM`."""
    load_seed(session)

    with pytest.raises(ToolError) as error:
        save_user_settings(TEST_USER, city, latitude, longitude)

    assert error.value.code == "INVALID_PARAM"


def test_session_state_absent_and_clear(data_db) -> None:
    """Нет строки — `None`; `clear_session_state` без строки не ошибка (§4.14)."""
    assert get_session_state(TEST_USER) is None

    clear_session_state(TEST_USER)

    assert get_session_state(TEST_USER) is None


def test_session_state_is_extended_by_save(data_db, session: Session) -> None:
    """Каждое сохранение продлевает сессию на 24 часа (§4.14)."""
    load_seed(session)
    save_session_state(TEST_USER, SessionState(stage="dialog"))
    row = session.get(SessionRow, TEST_USER)
    assert row is not None
    row.expires_at = PAST
    session.commit()

    save_session_state(TEST_USER, SessionState(stage="dialog", clarifications_asked=1))
    session.expire_all()

    row = session.get(SessionRow, TEST_USER)
    assert row is not None
    assert row.expires_at > PAST
    restored = get_session_state(TEST_USER)
    assert restored is not None and restored.clarifications_asked == 1


def test_purge_expired_without_rows(data_db) -> None:
    """Пустая таблица сессий — `0`, без ошибки (§4.18)."""
    assert purge_expired() == 0


def test_empty_user_data_is_empty(data_db, session: Session) -> None:
    """Без оценок и выборов — пустые списки, а не ошибки (§4.1, §4.15)."""
    load_seed(session)

    assert get_user_ratings(TEST_USER) == []
    assert get_pending_feedback(TEST_USER) is None


@pytest.mark.parametrize(("name",), [("",), ("   ",), ("я" * 101,)])
def test_resolve_city_validates_name_before_stub(data_db, name: str) -> None:
    """`name` проверяется по контракту §4.12, даже пока клиента Open-Meteo нет."""
    with pytest.raises(ToolError) as error:
        resolve_city(name)

    assert error.value.code == "INVALID_PARAM"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pages": 0},
        {"pages": 501},
        {"from_year": 1899},
        {"from_year": 2031},
        {"min_votes": -1},
        {"min_votes": 100_001},
        {"resume": "yes"},
    ],
)
def test_ingest_movies_validates_params_before_stub(data_db, kwargs: dict) -> None:
    """Границы `ingest_movies` — контракт §4.19, проверяются до заглушки."""
    with pytest.raises(ToolError) as error:
        ingest_movies(**kwargs)

    assert error.value.code == "INVALID_PARAM"


def test_stubs_raise_not_implemented_with_valid_params(data_db) -> None:
    """С корректными параметрами заглушки честно падают `NotImplementedError`."""
    with pytest.raises(NotImplementedError):
        ingest_movies()
    with pytest.raises(NotImplementedError):
        resolve_city("Москва")
    with pytest.raises(NotImplementedError):
        get_weather(TEST_USER)


def test_weather_type_matches_data_130() -> None:
    """`Weather` — поля и закрытый перечень §8.2; лишнее значение — ошибка."""
    weather = Weather(city="Москва", condition="rain", temp_c=9.4, is_day=False)
    assert weather.model_dump() == {
        "city": "Москва",
        "condition": "rain",
        "temp_c": 9.4,
        "is_day": False,
    }
    with pytest.raises(ValidationError):
        Weather(city="Москва", condition="storm", temp_c=9.4, is_day=False)
