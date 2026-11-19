"""Общие модели CineMatch.

Единственный источник полей, типов и перечней — documents/overall/contracts.md §2.
Здесь они не расширяются: лишнее поле отвергается, закрытые перечни заданы `Literal`.
Пределы длин (§8) проверяет `agents/guardrails.py`, а не модель: проверка ответа
идёт отдельным шагом трассы (agents/150, agents/120 §5).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Версия контрактов, contracts.md §1 и таблица в шапке документа.
CONTRACT_VERSION = "0.4.0"

type AgeRating = Literal["0+", "6+", "12+", "16+", "18+"]
type Mood = Literal["calm", "cheerful", "sad", "tired", "tense", "other"]
type Energy = Literal["low", "medium", "high"]
type Company = Literal["alone", "partner", "friends", "family"]
type Stage = Literal["consent", "onboarding", "dialog", "confirm_delete"]
type ResponseType = Literal[
    "consent",
    "question",
    "recommendations",
    "feedback_question",
    "confirmation",
    "message",
    "error",
]
type OutputKind = Literal[
    "question",
    "recommendations",
    "feedback_question",
    "confirmation",
    "message",
]
type TraceStep = Literal["Контекст", "Погода", "Оценки", "Поиск", "Проверка"]


class ContractModel(BaseModel):
    """Базовая модель контракта: неизвестные поля — ошибка."""

    model_config = ConfigDict(extra="forbid")


class MovieCard(ContractModel):
    """Карточка фильма из локального каталога — contracts.md §2.1."""

    movie_id: int
    tmdb_id: int
    title: str
    year: int | None = None
    runtime: int | None = None
    rating: float | None = None
    vote_count: int
    age_rating: AgeRating | None = None
    genres: list[str]
    poster_url: str | None = None


class RecommendedMovie(MovieCard):
    """Карточка в подборке: `MovieCard` + `reason` — contracts.md §2.2."""

    reason: str


class AgentTrace(ContractModel):
    """Шаг трассы — contracts.md §2.7. Поля формирует код, не модель."""

    step: TraceStep
    action: str
    summary: str


class AgentResponse(ContractModel):
    """Ответ модуля агентов API-слою — contracts.md §2.3."""

    contract_version: str = CONTRACT_VERSION
    type: ResponseType
    text: str
    movies: list[RecommendedMovie]
    trace: list[AgentTrace]
    stage: Stage
    error_code: str | None = None


class EveningContext(ContractModel):
    """Контекст вечера — contracts.md §2.4.

    `null` у жанровых списков — «пользователь не сказал», `[]` — «явно снял
    ограничение»; правила слияния — agents/120 §2.0.
    """

    mood: Mood | None = None
    energy: Energy | None = None
    available_minutes: Annotated[int, Field(ge=30, le=600)] | None = None
    company: Company | None = None
    genre_wishes: list[str] | None = None
    genre_avoid: list[str] | None = None
    notes: Annotated[str, Field(max_length=200)] | None = None


class CityVariant(ContractModel):
    """Вариант города — contracts.md §2.9.

    Используется `SessionState.city_options` и ответом `GET /api/v1/cities`;
    поля совпадают с результатом `resolve_city` (data/130 §4.12).
    """

    name: str
    region: str | None = None
    country: str
    latitude: float
    longitude: float


class PendingChoice(ContractModel):
    """Кэш неоценённого выбора — contracts.md §2.5, поле `pending`.

    Источник истины — таблица выборов (`get_pending_feedback`); здесь только
    `{choice_id, movie_id}`.
    """

    choice_id: int
    movie_id: int


class SessionState(ContractModel):
    """Состояние диалога — contracts.md §2.5.

    `messages` — история в формате Pydantic AI, сериализованная через
    `ModelMessagesTypeAdapter` в JSON-режиме (`model_dump(mode="json")`).
    Пределы (`shown_ids` ≤ 50, `last_shown_ids` ≤ 3, `messages` ≤ 20) соблюдает
    оркестратор, а не модель: он же вытесняет старые записи.
    """

    stage: Stage
    pending: PendingChoice | None = None
    city_options: list[CityVariant] = Field(default_factory=list)
    messages: list[dict] = Field(default_factory=list)
    context: EveningContext | None = None
    last_shown_ids: list[int] = Field(default_factory=list)
    shown_ids: list[int] = Field(default_factory=list)
    clarifications_asked: int = 0


class AgentOutput(ContractModel):
    """Структурированный ответ агента — contracts.md §2.6."""

    kind: OutputKind
    text: str
    movie_ids: list[int]
    reasons: list[str]


class RequestClass(StrEnum):
    """Класс запроса — contracts.md §2.8.

    В документе это закрытый перечень значений, а не структура с полями,
    поэтому класс не заводит полей: значения сравниваются как строки.
    """

    CINEMA = "cinema"
    DELETE_DATA = "delete_data"
    OFF_TOPIC = "off_topic"
    UNSAFE = "unsafe"
    YES = "yes"
    NO = "no"
