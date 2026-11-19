"""Коды ошибок CineMatch и тексты для пользователя.

Единственная таблица — documents/overall/contracts.md §6; здесь тексты
повторяются дословно и не переписываются. Два вида исключений:

* `ToolError` — ошибка, которую получает модель как результат инструмента
  (`{"error": {"code": ..., "message": ...}}`); пользователю не показывается
  (contracts.md §6, п. 1; data/130 §6).
* `ServiceError` — ошибка, которую получает пользователь; текст берётся из
  таблицы дословно (contracts.md §6, п. 2; data/130 §6).
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Закрытый перечень кодов — contracts.md §6."""

    INVALID_PARAM = "INVALID_PARAM"
    UNKNOWN_GENRE = "UNKNOWN_GENRE"
    NOT_FOUND = "NOT_FOUND"
    CITY_NOT_FOUND = "CITY_NOT_FOUND"
    CATALOG_EMPTY = "CATALOG_EMPTY"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"
    INVALID_INPUT = "INVALID_INPUT"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    SESSION_BUSY = "SESSION_BUSY"


# Дословные тексты для пользователя — contracts.md §6, колонка «Текст пользователю».
# Для `INVALID_PARAM`, `UNKNOWN_GENRE`, `NOT_FOUND` текста нет: их получает модель.
USER_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.CITY_NOT_FOUND: "Не нашёл такой город. Напишите название ещё раз.",
    ErrorCode.CATALOG_EMPTY: "Каталог фильмов пуст. Запустите загрузку каталога.",
    ErrorCode.DATA_UNAVAILABLE: "База данных недоступна. Попробуйте позже.",
    ErrorCode.LLM_UNAVAILABLE: "Сервис подбора временно недоступен. Попробуйте через минуту.",
    ErrorCode.LLM_INVALID_OUTPUT: (
        "Не получилось подобрать фильмы. Попробуйте переформулировать запрос."
    ),
    ErrorCode.LIMIT_EXCEEDED: "Подбор занял слишком много времени. Попробуйте уточнить запрос.",
    ErrorCode.INPUT_TOO_LONG: "Сообщение длиннее 1000 символов. Сократите его.",
    ErrorCode.INVALID_INPUT: "Пустое сообщение.",
    ErrorCode.AGENT_TIMEOUT: "Агент не ответил. Попробуйте ещё раз.",
    ErrorCode.SESSION_BUSY: "Дождитесь ответа на предыдущее сообщение.",
}

# Кто получает ошибку — contracts.md §6, колонка «Кто получает».
MODEL_CODES: frozenset[ErrorCode] = frozenset(
    {ErrorCode.INVALID_PARAM, ErrorCode.UNKNOWN_GENRE, ErrorCode.NOT_FOUND}
)


class ToolError(Exception):
    """Ошибка, которую получает модель; пользователю не показывается.

    Только коды `INVALID_PARAM`, `UNKNOWN_GENRE`, `NOT_FOUND` (contracts.md §6).
    Сериализуется в результат инструмента как
    `{"error": {"code": ..., "message": ...}}` (data/130 §6, п. 1).
    """

    def __init__(self, code: ErrorCode | str, message: str) -> None:
        self.code = ErrorCode(code)
        if self.code not in MODEL_CODES:
            raise ValueError(
                f"{self.code} получает пользователь, а не модель; используйте ServiceError"
            )
        self.message = message
        super().__init__(f"{self.code}: {message}")

    def to_result(self) -> dict[str, dict[str, str]]:
        """Форма результата инструмента для модели — data/130 §6, п. 1."""
        return {"error": {"code": str(self.code), "message": self.message}}


class ServiceError(Exception):
    """Ошибка, которую получает пользователь; текст берётся из таблицы §6."""

    def __init__(self, code: ErrorCode | str, message: str | None = None) -> None:
        self.code = ErrorCode(code)
        if self.code in MODEL_CODES:
            raise ValueError(
                f"{self.code} получает модель, а не пользователь; используйте ToolError"
            )
        self.message = message if message is not None else USER_MESSAGES[self.code]
        super().__init__(f"{self.code}: {self.message}")
