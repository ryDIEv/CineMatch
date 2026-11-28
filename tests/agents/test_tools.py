"""Реестр инструментов агента — agents/130 §4, ADR-008, ADR-050, ADR-051.

Проверяется то, что документ закрепляет за кодом, а не за моделью: подстановка
`user_id`, `exclude_ids` и `mood`, объединение `exclude_genres` с запретами
пользователя, автоматические запреты жанров из настроения и профиля, проверка
`save_choice` по `last_shown_ids`, ошибки модели как результат инструмента и
шаги трассы по таблице contracts §2.7.
"""

from __future__ import annotations

import pytest

from cinematch.agents.tools import (
    MAX_EXCLUDE_GENRES,
    PROFILE_BAN_THRESHOLD,
    ToolRegistry,
    normalize_genre,
)
from cinematch.data.schemas import PreferenceProfile
from cinematch.overall.contracts import EveningContext, PendingChoice, SessionState
from cinematch.overall.errors import ErrorCode, ServiceError, ToolError
from tests.fakes.fake_data import DEFAULT_USER_ID, FakeDataModule, fake_user_with_ratings


def _registry(
    data: FakeDataModule,
    *,
    state: SessionState | None = None,
    profile: PreferenceProfile | None = None,
    progress: list[tuple[str, str]] | None = None,
) -> ToolRegistry:
    """Реестр хода; журнал прогресса — список, в который пишет обработчик."""
    collector = progress if progress is not None else []
    return ToolRegistry(
        data,
        user_id=DEFAULT_USER_ID,
        state=state or SessionState(stage="dialog"),
        profile=profile,
        on_progress=lambda step, text: collector.append((step, text)),
    )


def _profile(**weights: float) -> PreferenceProfile:
    """Профиль с заданными весами жанров и без весов актёров."""
    return PreferenceProfile(genre_weights=dict(weights), actor_weights={}, ratings_count=4)


def test_normalize_genre_is_case_and_yo_insensitive() -> None:
    """Названия сравниваются без регистра и различия «е»/«ё» (data/130 §4.4)."""
    assert normalize_genre("  Ужасы ") == "ужасы"
    assert normalize_genre("Приключения") == normalize_genre("приключения")
    assert normalize_genre("Фэнтези") == normalize_genre("фэнтези")


def test_find_movies_injects_service_arguments() -> None:
    """`user_id`, `exclude_ids` и `mood` подставляет код, модель их не передаёт."""
    data = fake_user_with_ratings()
    state = SessionState(stage="dialog", shown_ids=[4, 5])

    registry = _registry(
        data,
        state=state,
        profile=None,
    )
    registry.context = EveningContext(mood="tired")

    registry.find_movies(genres=["Драма"])

    call = data.last_call("find_movies").arguments
    assert call["user_id"] == DEFAULT_USER_ID
    assert call["exclude_ids"] == [4, 5]
    assert call["mood"] == "tired"


def test_exclude_genres_merges_user_avoid_and_cannot_be_lifted() -> None:
    """`exclude_genres` модели объединяется с `context.genre_avoid` (130 §4, п. 1)."""
    data = fake_user_with_ratings()
    registry = _registry(data)
    registry.context = EveningContext(genre_avoid=["Ужасы"])

    registry.find_movies(genres=["Комедия"], exclude_genres=["Триллер"])

    exclude = data.last_call("find_movies").arguments["exclude_genres"]
    assert "Триллер" in exclude
    assert "Ужасы" in exclude


def test_mood_bans_and_runtime_are_added_by_code() -> None:
    """Запреты и длительность из таблицы настроения — ADR-051, 125 §2."""
    data = fake_user_with_ratings()
    registry = _registry(data)
    registry.context = EveningContext(mood="tired")

    registry.find_movies(genres=["Драма"])

    call = data.last_call("find_movies").arguments
    assert set(call["exclude_genres"]) == {"Триллер", "Ужасы"}
    assert call["max_runtime"] == 100


def test_available_minutes_overrides_mood_runtime() -> None:
    """Свободное время жёстко и не ослабляется таблицей настроения (120 §2.1)."""
    data = fake_user_with_ratings()
    registry = _registry(data)
    registry.context = EveningContext(mood="tired", available_minutes=90)

    registry.find_movies(max_runtime=150)

    assert data.last_call("find_movies").arguments["max_runtime"] == 90


def test_profile_genres_below_threshold_are_excluded() -> None:
    """Жанры профиля с весом ≤ −0,15 автоматически запрещены (ADR-051)."""
    data = fake_user_with_ratings()
    profile = _profile(Драма=0.352, Ужасы=PROFILE_BAN_THRESHOLD)
    registry = _registry(data, profile=profile)

    registry.find_movies(genres=["Драма"])

    exclude = data.last_call("find_movies").arguments["exclude_genres"]
    assert "Ужасы" in exclude
    assert "Драма" not in exclude


def test_genre_wishes_exempt_automatic_ban() -> None:
    """Явное пожелание снимает автоматический запрет (ADR-051)."""
    data = fake_user_with_ratings()
    profile = _profile(Ужасы=-0.5)
    registry = _registry(data, profile=profile)
    registry.context = EveningContext(genre_wishes=["Ужасы"])

    registry.find_movies(genres=["Ужасы"])

    exclude = data.last_call("find_movies").arguments["exclude_genres"]
    assert exclude is None or "Ужасы" not in exclude


def test_exclude_genres_fits_limit_by_dropping_automatic_bans() -> None:
    """При переполнении первыми отбрасываются профильные запреты (ADR-051).

    Пользовательские запреты не отбрасываются никогда, а запреты, которых нет
    в справочнике жанров, не передаются вовсе: иначе модуль данных ответил бы
    `UNKNOWN_GENRE`.
    """
    data = fake_user_with_ratings()
    profile = _profile(**{"Ужасы": -0.9, "Триллер": -0.8, "Фантастика": -0.4, "Приключения": -0.3})
    registry = _registry(data, profile=profile)
    registry.context = EveningContext(genre_avoid=["Семейный", "Драма"], mood="sad")

    registry.find_movies()

    exclude = data.last_call("find_movies").arguments["exclude_genres"]
    assert len(exclude) == MAX_EXCLUDE_GENRES
    assert {"Семейный", "Драма"} <= set(exclude), "запреты пользователя не отбрасываются"
    # Слабее всех профильный запрет «Приключения» — он и уходит первым.
    assert "Приключения" not in exclude
    assert "Ужасы" in exclude


def test_tool_error_is_returned_to_model_as_result() -> None:
    """`UNKNOWN_GENRE` получает модель результатом, а не исключением (contracts §6)."""
    data = fake_user_with_ratings()
    registry = _registry(data)

    result = registry.find_movies(genres=["Несуществующий жанр"])

    assert isinstance(result, dict)
    assert result["error"]["code"] == ErrorCode.UNKNOWN_GENRE
    assert registry.found == {}


def test_catalog_empty_reaches_user_not_model() -> None:
    """`CATALOG_EMPTY` — ошибка пользователя, её обрабатывает оркестратор (§6)."""
    data = fake_user_with_ratings()

    def empty(**_: object) -> list:
        raise ServiceError(ErrorCode.CATALOG_EMPTY)

    data.find_movies = empty  # type: ignore[method-assign]
    registry = _registry(data)

    with pytest.raises(ServiceError) as error:
        registry.find_movies(genres=["Драма"])

    assert error.value.code is ErrorCode.CATALOG_EMPTY


def test_find_movies_collects_cards_for_validation() -> None:
    """Проверка ответа видит карточки всех поисков хода (contracts §5, п. 5)."""
    data = fake_user_with_ratings()
    registry = _registry(data)

    first = registry.find_movies(genres=["Драма"], max_runtime=95)
    registry.find_movies(genres=["Комедия"])

    assert {card.movie_id for card in first} <= set(registry.found)
    assert registry.best_found >= len(first)


def test_save_choice_rejects_movie_outside_last_selection() -> None:
    """`save_choice` принимает только фильм последней подборки (130 §4, п. 4)."""
    data = fake_user_with_ratings()
    data.set_last_shown(DEFAULT_USER_ID, [1, 2, 3])
    registry = _registry(data, state=SessionState(stage="dialog", last_shown_ids=[1, 2, 3]))

    result = registry.save_choice(9)

    assert result["error"]["code"] == ErrorCode.NOT_FOUND
    assert data.calls_of("save_choice") == []


def test_save_choice_passes_mood_and_sets_pending() -> None:
    """Выбор сохраняет настроение и становится `pending` (ADR-050, 130 §4)."""
    data = fake_user_with_ratings()
    data.set_last_shown(DEFAULT_USER_ID, [1, 2, 3])
    registry = _registry(data, state=SessionState(stage="dialog", last_shown_ids=[1, 2, 3]))
    registry.context = EveningContext(mood="calm")

    result = registry.save_choice(2)

    assert result["choice_id"] == 1
    assert data.last_call("save_choice").arguments["mood"] == "calm"
    assert registry.pending == PendingChoice(choice_id=1, movie_id=2)


def test_save_rating_source_is_after_choice_for_pending() -> None:
    """`source` подставляет код: после выбора — `after_choice` (130 §4, п. 6)."""
    data = fake_user_with_ratings()
    registry = _registry(
        data,
        state=SessionState(stage="dialog", pending=PendingChoice(choice_id=7, movie_id=2)),
    )
    data._insert_choice(DEFAULT_USER_ID, 2, None)

    result = registry.save_rating(movie_id=2, rating=9, mood_match="yes")

    assert result["saved"] is True
    assert data.last_call("save_rating").arguments["source"] == "after_choice"
    assert registry.pending is None


def test_save_rating_invalid_mood_match_is_model_error() -> None:
    """Неверное значение `mood_match` — `INVALID_PARAM`, а не падение хода."""
    data = fake_user_with_ratings()
    registry = _registry(data)

    result = registry.save_rating(movie_id=1, rating=9, mood_match="наверное")

    assert result["error"]["code"] == ErrorCode.INVALID_PARAM


def test_close_choice_without_pending_is_not_found() -> None:
    """`close_choice` без ожидающего выбора — `NOT_FOUND` для модели (§6)."""
    registry = _registry(fake_user_with_ratings())

    assert registry.close_choice()["error"]["code"] == ErrorCode.NOT_FOUND


def test_trace_follows_contract_table() -> None:
    """Шаг трассы задаёт код по инструменту, а не модель (contracts §2.7)."""
    data = fake_user_with_ratings()
    data.set_weather(DEFAULT_USER_ID, None)
    registry = _registry(data)

    registry.get_user_ratings()
    registry.get_weather()
    registry.find_movies(genres=["Драма"])

    steps = [entry.step for entry in registry.trace]
    assert steps == ["Оценки", "Погода", "Поиск"]
    assert registry.trace[-1].summary.startswith("найдено ")


def test_write_tools_do_not_add_trace_steps() -> None:
    """У инструментов записи шага в таблице contracts §2.7 нет."""
    data = fake_user_with_ratings()
    registry = _registry(
        data,
        state=SessionState(stage="dialog", pending=PendingChoice(choice_id=3, movie_id=1)),
    )

    registry.close_choice()

    assert registry.trace == []


def test_progress_phrases_come_from_ui_document() -> None:
    """Фразы событий `tool` — 110 §4; набор закрытый."""
    data = fake_user_with_ratings()
    events: list[tuple[str, str]] = []
    registry = _registry(data, progress=events)

    registry.find_movies(genres=["Драма"])
    registry.notify_check()

    assert events == [("tool", "Ищу фильмы…"), ("check", "Проверяю подборку…")]


def test_get_user_profile_returns_weights() -> None:
    """`get_user_profile` отдаёт веса жанров из оценок (data/130 §4.2)."""
    data = fake_user_with_ratings(scenario="ts-ag-13")
    registry = _registry(data)

    profile = registry.get_user_profile()

    assert profile.ratings_count == 4
    assert profile.genre_weights["Драма"] > 0
    assert profile.genre_weights["Ужасы"] <= PROFILE_BAN_THRESHOLD


def test_tools_list_matches_documented_order() -> None:
    """Список инструментов — по таблице 130 §4; инструмента удаления нет."""
    data = fake_user_with_ratings()
    registry = _registry(data)

    names = [tool.__name__ for tool in registry.tools()]

    assert names == [
        "get_user_ratings",
        "get_user_profile",
        "get_weather",
        "find_movies",
        "get_movie_with_credits",
        "search_movie",
        "save_choice",
        "save_rating",
        "close_choice",
    ]
    assert "delete_user_data" not in names


def test_tool_error_requires_model_code() -> None:
    """Кодом для модели может быть только `INVALID_PARAM`, `UNKNOWN_GENRE`, `NOT_FOUND`."""
    with pytest.raises(ValueError):
        ToolError(ErrorCode.LLM_UNAVAILABLE, "не та ошибка")
