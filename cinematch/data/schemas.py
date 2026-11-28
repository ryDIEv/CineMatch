"""Типы возврата функций модуля данных — data/130 §2.

`MovieCard`, `CityVariant` и `SessionState` уже определены в общем контракте и
здесь не повторяются. Остальные структуры описаны в data/130 (§4.5, §4.6, §4.1,
§8.2) и ADR-013; поля и значения перечней повторяют документ без расширений.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from cinematch.overall.contracts import MovieCard


class DataModel(BaseModel):
    """Базовая модель ответов модуля данных: лишние поля — ошибка."""

    model_config = ConfigDict(extra="forbid")


class MovieDetails(MovieCard):
    """`MovieCard` + актёры и описание — data/130 §4.5."""

    actors: list[str]
    overview: str | None = None


class MovieSummary(DataModel):
    """Усечённая карточка для поиска — data/130 §4.6."""

    movie_id: int
    title: str
    year: int | None = None


class RatedMovie(DataModel):
    """Оценка пользователя — data/130 §4.1."""

    movie_id: int
    title: str
    year: int | None = None
    genres: list[str]
    rating: int
    mood_match: Literal["yes", "partly", "no"] | None = None
    rated_at: str


class PreferenceProfile(DataModel):
    """Веса жанров и актёров — ADR-013, data/130 §4.2."""

    genre_weights: dict[str, float]
    actor_weights: dict[str, float]
    ratings_count: int


class Weather(DataModel):
    """Погода города пользователя — data/130 §8.2.

    `condition` — закрытый перечень из §8.2; перевод WMO-кодов — §8.3.
    Русскую формулировку выбирает агент, модуль отдаёт английский код.
    """

    city: str
    condition: Literal[
        "clear",
        "cloudy",
        "fog",
        "drizzle",
        "rain",
        "snow",
        "thunderstorm",
        "unknown",
    ]
    temp_c: float
    is_day: bool


class PendingFeedback(DataModel):
    """Неоценённый выбор с карточкой — data/130 §4.15."""

    choice_id: int
    movie: MovieCard
    chosen_at: str


class HistoryItem(DataModel):
    """Элемент истории оценок и выборов — data/130 §4.16."""

    kind: Literal["rating", "choice"]
    movie: MovieCard
    rating: int | None = None
    mood_match: Literal["yes", "partly", "no"] | None = None
    status: Literal["pending", "rated", "dismissed"] | None = None
    at: str
