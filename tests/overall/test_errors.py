"""Коды и тексты ошибок: дословно по contracts.md §6 и разделение получателей."""

from __future__ import annotations

import pytest

from cinematch.overall.errors import (
    MODEL_CODES,
    USER_MESSAGES,
    ErrorCode,
    ServiceError,
    ToolError,
)

# Тексты — contracts.md §6, колонка «Текст пользователю», дословно.
EXPECTED_MESSAGES = {
    "CITY_NOT_FOUND": "Не нашёл такой город. Напишите название ещё раз.",
    "CATALOG_EMPTY": "Каталог фильмов пуст. Запустите загрузку каталога.",
    "DATA_UNAVAILABLE": "База данных недоступна. Попробуйте позже.",
    "LLM_UNAVAILABLE": "Сервис подбора временно недоступен. Попробуйте через минуту.",
    "LLM_INVALID_OUTPUT": "Не получилось подобрать фильмы. Попробуйте переформулировать запрос.",
    "LIMIT_EXCEEDED": "Подбор занял слишком много времени. Попробуйте уточнить запрос.",
    "INPUT_TOO_LONG": "Сообщение длиннее 1000 символов. Сократите его.",
    "INVALID_INPUT": "Пустое сообщение.",
    "AGENT_TIMEOUT": "Агент не ответил. Попробуйте ещё раз.",
    "SESSION_BUSY": "Дождитесь ответа на предыдущее сообщение.",
}

ALL_CODES = {
    "INVALID_PARAM",
    "UNKNOWN_GENRE",
    "NOT_FOUND",
    "CITY_NOT_FOUND",
    "CATALOG_EMPTY",
    "DATA_UNAVAILABLE",
    "LLM_UNAVAILABLE",
    "LLM_INVALID_OUTPUT",
    "LIMIT_EXCEEDED",
    "INPUT_TOO_LONG",
    "INVALID_INPUT",
    "AGENT_TIMEOUT",
    "SESSION_BUSY",
}


def test_code_catalog_matches_contracts_table() -> None:
    assert {code.value for code in ErrorCode} == ALL_CODES


def test_user_messages_match_table() -> None:
    assert {code.value: text for code, text in USER_MESSAGES.items()} == EXPECTED_MESSAGES


def test_model_codes_have_no_user_text() -> None:
    assert {code.value for code in MODEL_CODES} == {"INVALID_PARAM", "UNKNOWN_GENRE", "NOT_FOUND"}
    assert MODEL_CODES.isdisjoint(USER_MESSAGES)


def test_tool_error_serializes_for_model() -> None:
    error = ToolError("UNKNOWN_GENRE", "Такого жанра нет в справочнике.")

    assert error.code is ErrorCode.UNKNOWN_GENRE
    assert error.to_result() == {
        "error": {"code": "UNKNOWN_GENRE", "message": "Такого жанра нет в справочнике."}
    }


def test_tool_error_rejects_user_codes() -> None:
    with pytest.raises(ValueError, match="получает пользователь"):
        ToolError("LLM_UNAVAILABLE", "нет")


def test_service_error_takes_text_from_table() -> None:
    error = ServiceError("CATALOG_EMPTY")

    assert error.message == "Каталог фильмов пуст. Запустите загрузку каталога."
    assert str(error).startswith("CATALOG_EMPTY: ")


def test_service_error_rejects_model_codes() -> None:
    with pytest.raises(ValueError, match="получает модель"):
        ServiceError("NOT_FOUND")


def test_unknown_code_is_rejected() -> None:
    with pytest.raises(ValueError):
        ServiceError("SOMETHING_ELSE")
