"""Модели контракта: примеры из contracts.md §2 и запрет лишних полей."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cinematch.overall.contracts import (
    CONTRACT_VERSION,
    AgentOutput,
    AgentResponse,
    AgentTrace,
    CityVariant,
    EveningContext,
    MovieCard,
    PendingChoice,
    RecommendedMovie,
    RequestClass,
    SessionState,
)

# Пример из contracts.md §2.3 (AgentResponse).
AGENT_RESPONSE_EXAMPLE = {
    "contract_version": "0.4.0",
    "type": "recommendations",
    "text": "За окном дождь — вот три спокойных фильма до полутора часов:",
    "movies": [
        {
            "movie_id": 101,
            "tmdb_id": 447332,
            "title": "Тихий дом у моря",
            "year": 2014,
            "runtime": 98,
            "rating": 7.8,
            "vote_count": 1240,
            "age_rating": "12+",
            "genres": ["Драма", "Комедия"],
            "poster_url": "https://image.tmdb.org/t/p/w342/abc.jpg",
            "reason": "Вы просили спокойное и до полутора часов — здесь 98 минут и тихая история.",
        }
    ],
    "trace": [
        {
            "step": "Контекст",
            "action": "extract_context()",
            "summary": "устал, 90 минут, один, без ужасов",
        },
        {
            "step": "Погода",
            "action": "get_weather()",
            "summary": "Москва, дождь, +9°",
        },
        {
            "step": "Поиск",
            "action": "find_movies(жанры: драма, комедия; длительность ≤ 100)",
            "summary": "найдено 14 фильмов",
        },
    ],
    "stage": "dialog",
    "error_code": None,
}

# Пример карточки из contracts.md §2.1 без `reason`.
MOVIE_CARD_EXAMPLE = {
    "movie_id": 101,
    "tmdb_id": 447332,
    "title": "Тихий дом у моря",
    "year": 2014,
    "runtime": 98,
    "rating": 7.8,
    "vote_count": 1240,
    "age_rating": "12+",
    "genres": ["Драма", "Комедия"],
    "poster_url": "https://image.tmdb.org/t/p/w342/abc.jpg",
}


def test_contract_version_is_0_4_0() -> None:
    assert CONTRACT_VERSION == "0.4.0"
    assert AGENT_RESPONSE_EXAMPLE["contract_version"] == CONTRACT_VERSION


def test_agent_response_example_parses() -> None:
    response = AgentResponse.model_validate(AGENT_RESPONSE_EXAMPLE)

    assert response.type == "recommendations"
    assert response.stage == "dialog"
    assert response.error_code is None
    assert len(response.movies) == 1
    assert response.movies[0].reason.startswith("Вы просили спокойное")
    assert [step.step for step in response.trace] == ["Контекст", "Погода", "Поиск"]


def test_error_response_example_parses() -> None:
    error = AgentResponse.model_validate(
        {
            "contract_version": "0.4.0",
            "type": "error",
            "text": "Сервис подбора временно недоступен. Попробуйте через минуту.",
            "movies": [],
            "trace": [],
            "stage": "dialog",
            "error_code": "LLM_UNAVAILABLE",
        }
    )

    assert error.type == "error"
    assert error.error_code == "LLM_UNAVAILABLE"


def test_extra_field_is_rejected() -> None:
    extra = {**MOVIE_CARD_EXAMPLE, "explanation": "лишнее поле"}

    with pytest.raises(ValidationError):
        MovieCard.model_validate(extra)


def test_recommended_movie_requires_reason() -> None:
    with pytest.raises(ValidationError):
        RecommendedMovie.model_validate(MOVIE_CARD_EXAMPLE)


def test_closed_enums_are_enforced() -> None:
    with pytest.raises(ValidationError):
        AgentTrace.model_validate({"step": "Магия", "action": "x", "summary": "y"})
    with pytest.raises(ValidationError):
        EveningContext.model_validate({"mood": "happy"})
    with pytest.raises(ValidationError):
        MovieCard.model_validate({**MOVIE_CARD_EXAMPLE, "age_rating": "21+"})


def test_evening_context_null_and_empty_lists_differ() -> None:
    silent = EveningContext.model_validate({"genre_wishes": None})
    cleared = EveningContext.model_validate({"genre_wishes": []})

    assert silent.genre_wishes is None
    assert cleared.genre_wishes == []


def test_evening_context_limits() -> None:
    with pytest.raises(ValidationError):
        EveningContext.model_validate({"available_minutes": 10})
    with pytest.raises(ValidationError):
        EveningContext.model_validate({"notes": "я" * 201})


def test_session_state_fields() -> None:
    state = SessionState.model_validate(
        {
            "stage": "dialog",
            "pending": {"choice_id": 7, "movie_id": 205},
            "city_options": [
                {
                    "name": "Казань",
                    "region": "Татарстан",
                    "country": "Россия",
                    "latitude": 55.79,
                    "longitude": 49.12,
                }
            ],
            "messages": [{"role": "user", "content": "устал"}],
            "context": {"mood": "tired"},
            "last_shown_ids": [101],
            "shown_ids": [101, 205],
            "clarifications_asked": 1,
        }
    )

    assert state.pending == PendingChoice(choice_id=7, movie_id=205)
    assert state.city_options[0].name == "Казань"
    assert state.messages[0]["role"] == "user"


def test_pending_choice_has_only_documented_fields() -> None:
    with pytest.raises(ValidationError):
        PendingChoice.model_validate({"choice_id": 7, "movie_id": 205, "status": "pending"})


def test_city_variant_region_may_be_null() -> None:
    variant = CityVariant.model_validate(
        {
            "name": "Москва",
            "region": None,
            "country": "Россия",
            "latitude": 55.75,
            "longitude": 37.62,
        }
    )

    assert variant.region is None


def test_agent_output_fields() -> None:
    output = AgentOutput.model_validate(
        {
            "kind": "recommendations",
            "text": "Вот три фильма:",
            "movie_ids": [101, 102, 103],
            "reasons": ["раз", "два", "три"],
        }
    )

    assert output.kind == "recommendations"
    assert len(output.reasons) == len(output.movie_ids)


def test_request_class_values() -> None:
    assert {item.value for item in RequestClass} == {
        "cinema",
        "delete_data",
        "off_topic",
        "unsafe",
        "yes",
        "no",
    }
