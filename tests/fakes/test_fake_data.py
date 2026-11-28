"""Проверки заглушки модуля данных для тестов агента.

Заглушка обязана повторять сигнатуры `cinematch/data/api.py` (data/130 §3–4),
отдавать предсказуемую выдачу и считать профиль по формуле ADR-013, чтобы
TS-AG-13, TS-AG-14 и TS-AG-16 опирались на неё, а не на настоящую базу.
"""

from __future__ import annotations

import inspect

import pytest

from cinematch.data.schemas import Weather
from cinematch.overall.contracts import SessionState
from cinematch.overall.errors import ServiceError, ToolError
from tests.fakes.fake_data import (
    DEFAULT_USER_ID,
    FakeDataModule,
    fake_user_with_ratings,
)

# Сигнатуры из data/130 §3–4: имя → (параметры, значения по умолчанию).
# Порядок параметров и значения по умолчанию — часть контракта.
EXPECTED_SIGNATURES: dict[str, tuple[list[str], dict[str, object]]] = {
    "get_user_ratings": (["user_id", "limit"], {"limit": 20}),
    "get_user_profile": (["user_id"], {}),
    "get_weather": (["user_id"], {}),
    "find_movies": (
        [
            "user_id",
            "genres",
            "exclude_genres",
            "year_from",
            "year_to",
            "min_rating",
            "max_runtime",
            "exclude_ids",
            "mood",
            "limit",
        ],
        {
            "genres": None,
            "exclude_genres": None,
            "year_from": None,
            "year_to": None,
            "min_rating": None,
            "max_runtime": None,
            "exclude_ids": None,
            "mood": None,
            "limit": 15,
        },
    ),
    "get_movie_with_credits": (["movie_id"], {}),
    "search_movie": (["title", "year"], {"year": None}),
    "save_choice": (["user_id", "movie_id", "mood"], {"mood": None}),
    "save_rating": (
        ["user_id", "movie_id", "rating", "source", "mood_match"],
        {"mood_match": None},
    ),
    "close_choice": (["choice_id", "status"], {"status": "dismissed"}),
    "get_user": (["user_id"], {}),
    "save_consent": (["user_id", "source"], {}),
    "resolve_city": (["name"], {}),
    "save_user_settings": (["user_id", "city", "latitude", "longitude"], {}),
    "get_session_state": (["user_id"], {}),
    "save_session_state": (["user_id", "state"], {}),
    "clear_session_state": (["user_id"], {}),
    "get_pending_feedback": (["user_id"], {}),
    "get_history": (["user_id", "limit"], {"limit": 50}),
    "delete_user_data": (["user_id"], {}),
    "purge_expired": ([], {}),
    "ingest_movies": (
        ["pages", "from_year", "min_votes", "resume"],
        {"pages": 500, "from_year": 1970, "min_votes": 100, "resume": False},
    ),
    "get_catalog_size": ([], {}),
    "get_genres": ([], {}),
}

# Функции, у которых все параметры — только именованные (data/130 §4.4, §4.8).
KEYWORD_ONLY = {"find_movies", "save_rating"}


@pytest.fixture
def fake() -> FakeDataModule:
    """Заглушка с пользователем и каталогом фикстуры, без оценок."""
    return FakeDataModule()


def test_signatures_match_data_contract() -> None:
    """Имена, порядок и значения по умолчанию совпадают с data/130 §3–4."""
    for name, (params, defaults) in EXPECTED_SIGNATURES.items():
        signature = inspect.signature(getattr(FakeDataModule, name))
        actual = [param for param in signature.parameters if param != "self"]
        assert actual == params, name
        for param_name, default in defaults.items():
            assert signature.parameters[param_name].default == default, (name, param_name)


def test_keyword_only_parameters() -> None:
    """`find_movies` и `save_rating` принимают все параметры только по имени."""
    for name in KEYWORD_ONLY:
        signature = inspect.signature(getattr(FakeDataModule, name))
        kinds = {param.kind for key, param in signature.parameters.items() if key != "self"}
        assert kinds == {inspect.Parameter.KEYWORD_ONLY}, name


def test_signatures_match_real_data_module() -> None:
    """Сигнатуры заглушки совпадают с настоящим `cinematch/data/api.py`.

    Сравниваются имена и порядок параметров, их вид, значения по умолчанию,
    аннотации параметров и типа возврата — то есть контракт data/130 §4.
    Классы возврата берутся из `cinematch/overall` и `cinematch/data/schemas.py`,
    поэтому структуры карточек и профиля тоже совпадают.
    """
    from cinematch.data import api as data_api

    # Каждая функция модуля данных есть в заглушке: ничего не забыто.
    assert set(EXPECTED_SIGNATURES) == set(data_api.__all__)

    for name in EXPECTED_SIGNATURES:
        fake_signature = inspect.signature(getattr(FakeDataModule, name))
        real_signature = inspect.signature(getattr(data_api, name))
        fake_params = {
            key: value for key, value in fake_signature.parameters.items() if key != "self"
        }
        real_params = real_signature.parameters
        assert list(fake_params) == list(real_params), name
        assert str(fake_signature.return_annotation) == str(real_signature.return_annotation), name
        for param_name, real in real_params.items():
            fake = fake_params[param_name]
            assert fake.kind is real.kind, (name, param_name)
            assert fake.default == real.default, (name, param_name)
            assert str(fake.annotation) == str(real.annotation), (name, param_name)


def test_functions_are_synchronous() -> None:
    """Функции модуля данных синхронные — ADR-047."""
    for name in EXPECTED_SIGNATURES:
        assert not inspect.iscoroutinefunction(getattr(FakeDataModule, name)), name


def test_catalog_comes_from_fixture(fake: FakeDataModule) -> None:
    """Каталог — 23 фильма фикстуры, жанры и карточки на месте (ADR-042)."""
    assert fake.get_catalog_size() == 23
    assert "Драма" in fake.get_genres()
    card = fake.get_movie_with_credits(1)
    assert card.title == "Тихий дом у моря"
    assert card.actors == ["Анна Петрова", "Иван Сергеев"]
    assert card.overview and "Тихий дом" in card.overview


def test_default_order_is_rating_then_id(fake: FakeDataModule) -> None:
    """Порядок без критериев — `rating` по убыванию, затем `id`."""
    cards = fake.find_movies(user_id=DEFAULT_USER_ID, limit=30)

    assert [card.movie_id for card in cards][:5] == [21, 5, 17, 15, 1]
    # Равный рейтинг 7.0: id 6, 16, 22, 23 — по возрастанию id.
    sevens = [card.movie_id for card in cards if card.rating == 7.0]
    assert sevens == [6, 16, 22, 23]


def test_genres_use_or_logic(fake: FakeDataModule) -> None:
    """Жанры объединяются по «ИЛИ», без оценки числа совпадений."""
    cards = fake.find_movies(user_id=DEFAULT_USER_ID, genres=["Комедия", "Драма"], limit=30)
    found = {card.movie_id for card in cards}

    assert {1, 2, 6, 9, 17} <= found
    assert 5 not in found and 7 not in found
    # Имена нормализуются: регистр не важен.
    lower = fake.find_movies(user_id=DEFAULT_USER_ID, genres=["комедия"], limit=30)
    upper = fake.find_movies(user_id=DEFAULT_USER_ID, genres=["Комедия"], limit=30)
    assert {card.movie_id for card in lower} == {card.movie_id for card in upper}


def test_exclude_genres_and_exclude_ids(fake: FakeDataModule) -> None:
    """`exclude_genres` отбрасывает любой совпавший жанр, `exclude_ids` — фильм."""
    cards = fake.find_movies(
        user_id=DEFAULT_USER_ID,
        exclude_genres=["Ужасы", "Триллер"],
        exclude_ids=[1, 2],
        limit=30,
    )
    found = {card.movie_id for card in cards}

    assert not {7, 9, 10, 13, 18, 23} & found
    assert not {1, 2} & found
    assert 6 in found


def test_rated_and_untimeable_movies_are_skipped(fake: FakeDataModule) -> None:
    """Оценённые исключаются; фильм без `runtime` не проходит `max_runtime`."""
    fake.save_rating(user_id=DEFAULT_USER_ID, movie_id=5, rating=10, source="manual")

    rated = fake.find_movies(user_id=DEFAULT_USER_ID, limit=30)
    assert 5 not in {card.movie_id for card in rated}

    short = fake.find_movies(user_id=DEFAULT_USER_ID, max_runtime=90, limit=30)
    found = {card.movie_id for card in short}
    # 22 — «Без хронометража» (runtime = None): условие её не берёт.
    assert 22 not in found
    assert all((card.runtime or 0) <= 90 for card in short)


def test_year_and_rating_filters(fake: FakeDataModule) -> None:
    """Годовые рамки и `min_rating` — те же условия, что в data/130 §4.4."""
    by_year = fake.find_movies(user_id=DEFAULT_USER_ID, year_from=2020, limit=30)
    assert {card.movie_id for card in by_year} == {5, 7, 9, 14, 16, 19, 21, 22, 23}

    by_rating = fake.find_movies(user_id=DEFAULT_USER_ID, min_rating=7.9, limit=30)
    assert {card.movie_id for card in by_rating} == {5, 15, 17, 21}


def test_mood_is_accepted_but_does_not_reorder(fake: FakeDataModule) -> None:
    """`mood` принимается и попадает в журнал, но порядок не меняет."""
    without = fake.find_movies(user_id=DEFAULT_USER_ID, limit=5)
    with_mood = fake.find_movies(user_id=DEFAULT_USER_ID, mood="tired", limit=5)

    assert [card.movie_id for card in without] == [card.movie_id for card in with_mood]
    assert fake.last_call("find_movies").arguments["mood"] == "tired"


def test_profile_formula_on_ts_ag_13_factory() -> None:
    """Фабрика TS-AG-13: «ужасы» ≤ −0,15, «драма» положительна (ADR-013)."""
    fake = fake_user_with_ratings(scenario="ts-ag-13")

    profile = fake.get_user_profile(DEFAULT_USER_ID)

    assert profile.ratings_count == 4
    # Драма: r = 0.778, 1.0, 1.0 → raw 0.926, c = 0.5 → 0.463.
    assert profile.genre_weights["Драма"] == pytest.approx(0.463)
    # Комедия: r = 0.778, 1.0 → raw 0.889, c = 0.4 → 0.356.
    assert profile.genre_weights["Комедия"] == pytest.approx(0.356)
    # Ужасы: r = −0.778, c = 0.25 → −0.194: порог −0,15 пройден.
    assert profile.genre_weights["Ужасы"] == pytest.approx(-0.194)
    assert profile.genre_weights["Ужасы"] <= -0.15


def test_profile_is_empty_on_cold_start() -> None:
    """Пустой профиль: пустые словари и `ratings_count = 0` (data/130 §4.2)."""
    fake = fake_user_with_ratings()

    profile = fake.get_user_profile(DEFAULT_USER_ID)

    assert profile.genre_weights == {}
    assert profile.actor_weights == {}
    assert profile.ratings_count == 0


def test_profile_actor_weights_use_cast() -> None:
    """Актёры считаются по тем же оценкам; в фикстуре они общие."""
    fake = fake_user_with_ratings(scenario="ts-ag-13")

    profile = fake.get_user_profile(DEFAULT_USER_ID)

    assert profile.actor_weights["Анна Петрова"] > 0
    assert len(profile.actor_weights) <= 20


def test_ts_ag_13_horror_is_excluded_and_drama_stays() -> None:
    """После оценок TS-AG-13 подборка без ужасов, но с драмой."""
    fake = fake_user_with_ratings(scenario="ts-ag-13")

    cards = fake.find_movies(user_id=DEFAULT_USER_ID, exclude_genres=["Ужасы"], limit=30)
    found = {card.movie_id for card in cards}

    # Оценённые 1, 2, 3 и 10 исключены как оценённые (data/130 §4.4, п. 2).
    assert not {1, 2, 3, 9, 10, 18} & found
    assert {4, 8, 13} <= found  # драма есть


def test_ts_ag_14_factory_marks_mood_match_no() -> None:
    """Фабрика TS-AG-14: выбор при настроении и оценка с `mood_match = no`."""
    fake = fake_user_with_ratings(scenario="ts-ag-14", mood="tired", genre="Ужасы")

    ratings = fake.get_user_ratings(DEFAULT_USER_ID)
    profile = fake.get_user_profile(DEFAULT_USER_ID)

    assert ratings and all(item.mood_match == "no" for item in ratings)
    assert "Ужасы" in profile.genre_weights
    # Оценка закрыла выбор как `rated`: ожидающего нет (data/130 §4.8).
    assert fake.get_pending_feedback(DEFAULT_USER_ID) is None
    assert [choice.status for choice in fake.choices[DEFAULT_USER_ID]] == ["rated", "rated"]
    # Настроение выбора сохранено (ADR-050).
    assert {choice.mood for choice in fake.choices[DEFAULT_USER_ID]} == {"tired"}


def test_ts_ag_14_requires_mood() -> None:
    """Без настроения сценарий TS-AG-14 невозможен — явная ошибка подготовки."""
    with pytest.raises(ValueError, match="mood"):
        fake_user_with_ratings(scenario="ts-ag-14")


def test_weather_is_none_until_set() -> None:
    """Погода — подсказка: по умолчанию `None`, затем задаётся тестом (§4.3)."""
    fake = FakeDataModule()
    assert fake.get_weather(DEFAULT_USER_ID) is None

    fake.set_weather(
        DEFAULT_USER_ID,
        Weather(city="Москва", condition="rain", temp_c=9.0, is_day=False),
    )

    weather = fake.get_weather(DEFAULT_USER_ID)
    assert weather is not None and weather.condition == "rain"
    assert fake.last_call("get_weather").arguments == {"user_id": DEFAULT_USER_ID}


def test_session_state_round_trip_keeps_last_shown() -> None:
    """`save_session_state` запоминает подборку, `clear` удаляет (§4.14)."""
    fake = FakeDataModule()
    state = SessionState(stage="dialog", last_shown_ids=[1, 2, 3])

    fake.save_session_state(DEFAULT_USER_ID, state)
    stored = fake.get_session_state(DEFAULT_USER_ID)
    assert stored is not None and stored.last_shown_ids == [1, 2, 3]
    assert fake.last_shown[DEFAULT_USER_ID] == [1, 2, 3]

    fake.clear_session_state(DEFAULT_USER_ID)
    assert fake.get_session_state(DEFAULT_USER_ID) is None


def test_user_lifecycle_and_delete() -> None:
    """Согласие, город, удаление данных — data/130 §4.10–4.17 без базы."""
    fake = FakeDataModule(with_user=False)
    assert fake.get_user(DEFAULT_USER_ID) is None

    fake.save_consent(DEFAULT_USER_ID, "web")
    user = fake.get_user(DEFAULT_USER_ID)
    assert user is not None and user["consent_at"] and user["city"] is None

    fake.save_user_settings(DEFAULT_USER_ID, "Казань", 55.79, 49.12)
    assert fake.get_user(DEFAULT_USER_ID)["city"] == "Казань"

    fake.save_rating(user_id=DEFAULT_USER_ID, movie_id=1, rating=9, source="manual")
    counts = fake.delete_user_data(DEFAULT_USER_ID)

    assert counts["deleted_counts"]["user_ratings"] == 1
    assert counts["deleted_counts"]["users"] == 1
    assert fake.get_user(DEFAULT_USER_ID) is None
    # Идемпотентность: повтор даёт нули (data/130 §4.17).
    assert fake.delete_user_data(DEFAULT_USER_ID)["deleted_counts"]["users"] == 0


def test_resolve_city_and_search_movie(fake: FakeDataModule) -> None:
    """Геокодирование и поиск по названию — data/130 §4.6, §4.12."""
    assert [city.name for city in fake.resolve_city("москва")] == ["Москва"]
    assert fake.resolve_city("Атлантида") == []

    found = fake.search_movie("северный свет")
    assert [item.movie_id for item in found] == [3, 4]
    assert fake.search_movie("северный свет", year=1987)[0].movie_id == 4


def test_save_choice_requires_last_shown(fake: FakeDataModule) -> None:
    """`save_choice` вне `last_shown_ids` — `NOT_FOUND` (data/130 §4.7)."""
    fake.set_last_shown(DEFAULT_USER_ID, [1, 2, 3])

    with pytest.raises(ToolError) as error:
        fake.save_choice(DEFAULT_USER_ID, 5)

    assert error.value.code == "NOT_FOUND"

    result = fake.save_choice(DEFAULT_USER_ID, 2, mood="calm")
    assert result == {"choice_id": 1}
    assert fake.choices[DEFAULT_USER_ID][0].mood == "calm"

    # Второй выбор закрывает прежний `pending` и получает новый id.
    second = fake.save_choice(DEFAULT_USER_ID, 3)
    assert second["choice_id"] == 2
    assert [choice.status for choice in fake.choices[DEFAULT_USER_ID]] == ["dismissed", "pending"]


def test_save_choice_rejects_unknown_movie(fake: FakeDataModule) -> None:
    """Неизвестный фильм — `NOT_FOUND`, неверный id — `INVALID_PARAM`."""
    fake.set_last_shown(DEFAULT_USER_ID, [999])

    with pytest.raises(ToolError) as missing:
        fake.save_choice(DEFAULT_USER_ID, 999)
    assert missing.value.code == "NOT_FOUND"

    with pytest.raises(ToolError) as invalid:
        fake.save_choice(DEFAULT_USER_ID, 0)
    assert invalid.value.code == "INVALID_PARAM"


def test_calls_journal_records_name_and_arguments(fake: FakeDataModule) -> None:
    """Журнал `calls` хранит имя функции и аргументы вместе с умолчаниями."""
    fake.find_movies(
        user_id=DEFAULT_USER_ID,
        genres=["Драма"],
        exclude_genres=["Ужасы"],
        max_runtime=120,
        exclude_ids=[4, 5],
        mood="tired",
    )

    call = fake.last_call("find_movies")
    assert call.name == "find_movies"
    assert call.arguments["user_id"] == DEFAULT_USER_ID
    assert call.arguments["genres"] == ["Драма"]
    assert call.arguments["exclude_genres"] == ["Ужасы"]
    assert call.arguments["max_runtime"] == 120
    assert call.arguments["exclude_ids"] == [4, 5]
    assert call.arguments["mood"] == "tired"
    # Незаданные параметры тоже видны — со значениями по умолчанию.
    assert call.arguments["year_from"] is None
    assert call.arguments["limit"] == 15


def test_calls_journal_filters_by_name(fake: FakeDataModule) -> None:
    """`calls_of` и `clear_calls` помогают проверять реестр инструментов."""
    fake.get_user_profile(DEFAULT_USER_ID)
    fake.find_movies(user_id=DEFAULT_USER_ID, limit=3)
    fake.save_rating(
        user_id=DEFAULT_USER_ID, movie_id=1, rating=9, source="manual", mood_match="yes"
    )

    assert [call.name for call in fake.calls] == [
        "get_user_profile",
        "find_movies",
        "save_rating",
    ]
    assert fake.calls_of("find_movies")[0].arguments["limit"] == 3
    assert fake.last_call("save_rating").arguments["mood_match"] == "yes"

    fake.clear_calls()
    assert fake.calls == []
    with pytest.raises(LookupError):
        fake.last_call()


def test_factory_clears_preparation_calls() -> None:
    """Журнал фабрики пуст: подготовка данных не мешает проверкам агента."""
    fake = fake_user_with_ratings(scenario="ts-ag-13")

    assert fake.calls == []


def test_tool_errors_use_contract_codes(fake: FakeDataModule) -> None:
    """Коды ошибок — из data/130 §6: `INVALID_PARAM`, `UNKNOWN_GENRE`."""
    with pytest.raises(ToolError) as unknown:
        fake.find_movies(user_id=DEFAULT_USER_ID, genres=["Киберпанк"])
    assert unknown.value.code == "UNKNOWN_GENRE"
    assert "Драма" in unknown.value.message

    with pytest.raises(ToolError) as invalid:
        fake.find_movies(user_id=DEFAULT_USER_ID, limit=2)
    assert invalid.value.code == "INVALID_PARAM"


def test_empty_catalog_raises_service_error() -> None:
    """Пустой каталог — `ServiceError(CATALOG_EMPTY)` (data/130 §4.4, п. 7)."""
    fake = FakeDataModule(movies=[])

    with pytest.raises(ServiceError) as error:
        fake.find_movies(user_id=DEFAULT_USER_ID)

    assert error.value.code == "CATALOG_EMPTY"


def test_horror_weight_from_one_low_rating() -> None:
    """Одна оценка 2 даёт жанру вес −0,194 — порог −0,15 пройден (ADR-013, п. 11)."""
    fake = FakeDataModule()
    fake.save_rating(user_id=DEFAULT_USER_ID, movie_id=10, rating=2, source="manual")

    profile = fake.get_user_profile(DEFAULT_USER_ID)

    assert profile.genre_weights == {"Ужасы": -0.194}
