"""Заглушка модуля данных для тестов модуля ИИ-агентов.

Назначение: тесты агента не должны поднимать SQLite и миграции. Заглушка
повторяет **сигнатуры** `cinematch/data/api.py` (data/130 §3–4) — имена
функций, параметры, значения по умолчанию и аннотации типов, — но держит
данные в памяти и упрощает поведение.

Функции синхронные, как требует ADR-047: Pydantic AI выполняет их в пуле
потоков, оркестратор вызывает служебные функции через `asyncio.to_thread`.

Что упрощено намеренно (это логика модуля данных, её проверяет TS-DB):

* `find_movies` не пересортировывает выдачу по профилю и не штрафует жанры
  за `mood_match = no` (ADR-049, ADR-050) — порядок только `rating`, затем
  `id`; `mood` принимается и записывается в журнал, но на выдачу не влияет;
* нет порога голосов `vote_count >= 100`, кэша погоды и загрузки TMDB;
* сессии не истекают: `purge_expired` всегда возвращает 0.

Всё остальное предсказуемо: время записей монотонное (база `2026-11-28T18:00:00Z`
плюс секунда на запись), `get_user_profile` считается по формуле ADR-013,
каталог берётся из `tests/fixtures/seed_movies.json` (ADR-042).

Каждый вызов функции попадает в `FakeDataModule.calls` — по этому журналу
тесты проверяют, что передал реестр инструментов (`mood`, `exclude_ids`,
`exclude_genres`).
"""

from __future__ import annotations

import copy
import functools
import inspect
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from cinematch.data.schemas import (
    HistoryItem,
    MovieDetails,
    MovieSummary,
    PendingFeedback,
    PreferenceProfile,
    RatedMovie,
    Weather,
)
from cinematch.overall.contracts import CityVariant, Mood, MovieCard, SessionState
from cinematch.overall.errors import ErrorCode, ServiceError, ToolError

SEED_MOVIES = Path(__file__).resolve().parents[1] / "fixtures" / "seed_movies.json"

# Пользователь по умолчанию совпадает с `CINEMATCH_USER_ID` (overall/config.md).
DEFAULT_USER_ID = "web:local"

MOODS: frozenset[str] = frozenset({"calm", "cheerful", "sad", "tired", "tense", "other"})
MOOD_MATCHES: frozenset[str] = frozenset({"yes", "partly", "no"})
RATING_SOURCES: frozenset[str] = frozenset({"after_choice", "manual"})
USER_SOURCES: frozenset[str] = frozenset({"web"})

# База времени записей: детерминированные метки вместо `now()`.
BASE_TIME = datetime(2026, 11, 28, 18, 0, 0, tzinfo=UTC)

# Формула профиля — ADR-013.
NEUTRAL_RATING = 5.5
RATING_SCALE = 4.5
TRUST_COEFFICIENT = 3
MAX_ACTORS = 20

type FakeScenario = Literal["empty", "ts-ag-13", "ts-ag-14"]

# TS-AG-13: три оценки 9–10 в «драме» и одна 2 в «ужасах».
TS_AG_13_RATINGS: tuple[tuple[int, int], ...] = ((1, 9), (2, 10), (3, 10), (10, 2))


def _normalize(name: str) -> str:
    """Нормализация названий: `lower`, `ё → е`, сжатие пробелов (data/130 §4.4)."""
    return " ".join(name.lower().replace("ё", "е").split())


def _invalid(message: str) -> ToolError:
    """`INVALID_PARAM` — параметр вне диапазона или неверного типа."""
    return ToolError(ErrorCode.INVALID_PARAM, message)


def _require_int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _invalid(f"{name}: ожидается целое {low}…{high}")
    return value


def _require_number(value: object, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"{name}: ожидается число {low}…{high}")
    if not low <= value <= high:
        raise _invalid(f"{name}: ожидается число {low}…{high}")
    return float(value)


def _require_str(value: object, name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{name}: ожидается строка")
    trimmed = value.strip()
    if not trimmed or len(trimmed) > max_length:
        raise _invalid(f"{name}: ожидается строка 1…{max_length} символов")
    return trimmed


def _require_positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _invalid(f"{name}: ожидается целое больше нуля")
    return value


@dataclass
class FakeCall:
    """Запись журнала: имя функции и её аргументы (с значениями по умолчанию)."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Rating:
    """Оценка пользователя — строка `user_ratings` (data/120 §7)."""

    rating: int
    source: str
    mood_match: str | None
    rated_at: str


@dataclass
class _Choice:
    """Выбор пользователя — строка `user_choices` (data/120 §8)."""

    choice_id: int
    movie_id: int
    mood: str | None
    status: str
    chosen_at: str
    closed_at: str | None = None


def _journal[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Записать вызов в `self.calls`: имя функции и связанные аргументы.

    `functools.wraps` сохраняет `__wrapped__`, поэтому `inspect.signature`
    видит исходную сигнатуру и тест сравнивает её с data/130 §4.
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        owner = args[0]
        bound = inspect.signature(func).bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = {
            key: copy.deepcopy(value) for key, value in bound.arguments.items() if key != "self"
        }
        owner.calls.append(FakeCall(name=func.__name__, arguments=arguments))
        return func(*args, **kwargs)

    return wrapper


class FakeDataModule:
    """Функции модуля данных в памяти — сигнатуры и коды ошибок как в data/130.

    Данные: каталог из фикстуры, пользователи, оценки, выборы, сессии, погода
    и варианты городов. Состояние живёт в экземпляре, поэтому каждый тест
    получает чистую копию.
    """

    def __init__(
        self,
        *,
        movies: Sequence[dict[str, Any]] | None = None,
        seed_path: Path | None = None,
        with_user: bool = True,
        user_id: str = DEFAULT_USER_ID,
        city: str = "Москва",
    ) -> None:
        payload = (
            list(movies)
            if movies is not None
            else json.loads((seed_path or SEED_MOVIES).read_text(encoding="utf-8"))
        )
        self.movies: list[MovieCard] = [self._card(item) for item in payload]
        self._details: dict[int, dict[str, Any]] = {item["movie_id"]: item for item in payload}
        self._genres: list[str] = self._collect_genres(payload)

        self.users: dict[str, dict[str, Any]] = {}
        self.ratings: dict[str, dict[int, _Rating]] = {}
        self.choices: dict[str, list[_Choice]] = {}
        self.sessions: dict[str, SessionState] = {}
        self.weather: dict[str, Weather] = {}
        self.last_shown: dict[str, list[int]] = {}

        # Варианты `resolve_city`: по умолчанию один город, как в тестах data.
        self.cities: list[CityVariant] = [
            CityVariant(
                name="Москва",
                region=None,
                country="Россия",
                latitude=55.75,
                longitude=37.62,
            )
        ]

        self.calls: list[FakeCall] = []
        self._tick = 0
        self._next_choice_id = 1
        self._next_load_id = 1

        if with_user:
            self._create_user(user_id, city=city)

    # --- Вспомогательное ---------------------------------------------------

    @staticmethod
    def _card(item: dict[str, Any]) -> MovieCard:
        return MovieCard(
            movie_id=item["movie_id"],
            tmdb_id=item["tmdb_id"],
            title=item["title"],
            year=item["year"],
            runtime=item["runtime"],
            rating=item["rating"],
            vote_count=item["vote_count"],
            age_rating=item.get("age_rating"),
            genres=list(item["genres"]),
            poster_url=item.get("poster_url"),
        )

    @staticmethod
    def _collect_genres(payload: Sequence[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        for item in payload:
            for name in item["genres"]:
                if name not in names:
                    names.append(name)
        return names

    def _now(self) -> str:
        """Следующая метка времени: `BASE_TIME` + секунда на запись."""
        self._tick += 1
        moment = BASE_TIME + timedelta(seconds=self._tick)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _create_user(self, user_id: str, *, city: str = "Москва") -> None:
        moment = self._now()
        self.users[user_id] = {
            "user_id": user_id,
            "source": "web",
            "consent_at": moment,
            "city": city,
            "latitude": 55.75,
            "longitude": 37.62,
            "created_at": moment,
        }

    def _card_by_id(self, movie_id: int) -> MovieCard | None:
        return next((card for card in self.movies if card.movie_id == movie_id), None)

    def _rated_ids(self, user_id: str) -> set[int]:
        return set(self.ratings.get(user_id, {}))

    def _insert_choice(self, user_id: str, movie_id: int, mood: str | None) -> int:
        """Новый `pending`; прежние ожидающие закрываются как `dismissed`."""
        moment = self._now()
        choices = self.choices.setdefault(user_id, [])
        for choice in choices:
            if choice.status == "pending":
                choice.status = "dismissed"
                choice.closed_at = moment
        choice = _Choice(
            choice_id=self._next_choice_id,
            movie_id=movie_id,
            mood=mood,
            status="pending",
            chosen_at=moment,
        )
        self._next_choice_id += 1
        choices.append(choice)
        return choice.choice_id

    # --- Журнал вызовов ----------------------------------------------------

    def clear_calls(self) -> None:
        """Очистить журнал: в тесте видны только вызовы после подготовки."""
        self.calls.clear()

    def calls_of(self, name: str) -> list[FakeCall]:
        """Все вызовы функции `name` в порядке выполнения."""
        return [call for call in self.calls if call.name == name]

    def last_call(self, name: str | None = None) -> FakeCall:
        """Последний вызов (или последний вызов функции `name`)."""
        if name is None:
            if not self.calls:
                raise LookupError("журнал вызовов пуст")
            return self.calls[-1]
        found = self.calls_of(name)
        if not found:
            raise LookupError(f"в журнале нет вызовов {name}")
        return found[-1]

    def set_last_shown(self, user_id: str, movie_ids: Sequence[int]) -> None:
        """Задать `last_shown_ids` последней подборки — проверка `save_choice`."""
        self.last_shown[user_id] = list(movie_ids)

    def set_weather(self, user_id: str, weather: Weather | None) -> None:
        """Задать погоду города; `None` — сервис недоступен (data/130 §4.3)."""
        if weather is None:
            self.weather.pop(user_id, None)
        else:
            self.weather[user_id] = weather

    # --- Инструменты агента: data/130 §4.1–4.9 -----------------------------

    @_journal
    def get_user_ratings(self, user_id: str, limit: int = 20) -> list[RatedMovie]:
        """Оценки пользователя по убыванию `rated_at` — data/130 §4.1."""
        limit = _require_int(limit, "limit", 1, 50)
        rows = sorted(
            self.ratings.get(user_id, {}).items(),
            key=lambda item: item[1].rated_at,
            reverse=True,
        )
        result: list[RatedMovie] = []
        for movie_id, rating in rows[:limit]:
            card = self._card_by_id(movie_id)
            if card is None:
                continue
            result.append(
                RatedMovie(
                    movie_id=movie_id,
                    title=card.title,
                    year=card.year,
                    genres=list(card.genres),
                    rating=rating.rating,
                    mood_match=rating.mood_match,
                    rated_at=rating.rated_at,
                )
            )
        return result

    @_journal
    def get_user_profile(self, user_id: str) -> PreferenceProfile:
        """Профиль по всем оценкам по формуле ADR-013 — data/130 §4.2."""
        ratings = self.ratings.get(user_id, {})
        if not ratings:
            return PreferenceProfile(genre_weights={}, actor_weights={}, ratings_count=0)

        normalized = {
            movie_id: (rating.rating - NEUTRAL_RATING) / RATING_SCALE
            for movie_id, rating in ratings.items()
        }
        genre_pairs: list[tuple[int, str]] = []
        actor_pairs: list[tuple[int, str]] = []
        for movie_id in ratings:
            card = self._card_by_id(movie_id)
            if card is None:
                continue
            genre_pairs.extend((movie_id, name) for name in card.genres)
            item = self._details.get(movie_id, {})
            actors = list(item.get("actors", []))[:10]
            actor_pairs.extend((movie_id, name) for name in actors)

        return PreferenceProfile(
            genre_weights=self._weights(genre_pairs, normalized),
            actor_weights=dict(list(self._weights(actor_pairs, normalized).items())[:MAX_ACTORS]),
            ratings_count=len(ratings),
        )

    @staticmethod
    def _weights(
        pairs: Sequence[tuple[int, str]], normalized: dict[int, float]
    ) -> dict[str, float]:
        """`weight = round(raw × n / (n + 3), 3)`, |weight| ≤ 1 (ADR-013)."""
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        for movie_id, name in pairs:
            value = normalized.get(movie_id)
            if value is None:
                continue
            totals[name] = totals.get(name, 0.0) + value
            counts[name] = counts.get(name, 0) + 1
        weighted = {
            name: round(
                max(
                    -1.0,
                    min(
                        1.0,
                        (totals[name] / counts[name]) * (counts[name] / (counts[name] + 3)),
                    ),
                ),
                3,
            )
            for name in totals
        }
        # Порядок ключей: |weight| по убыванию, затем название (ADR-013, п. 5).
        return dict(sorted(weighted.items(), key=lambda item: (-abs(item[1]), item[0])))

    @_journal
    def get_weather(self, user_id: str) -> Weather | None:
        """Погода города пользователя; `None` — города нет или сервис отказал."""
        return self.weather.get(user_id)

    @_journal
    def find_movies(
        self,
        *,
        user_id: str,
        genres: list[str] | None = None,
        exclude_genres: list[str] | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        min_rating: float | None = None,
        max_runtime: int | None = None,
        exclude_ids: list[int] | None = None,
        mood: Mood | None = None,
        limit: int = 15,
    ) -> list[MovieCard]:
        """Кандидаты подборки: фильтры data/130 §4.4, порядок `rating`, затем `id`.

        Пересортировки по профилю и штрафа за настроение нет — это логика
        модуля данных (ADR-049, ADR-050); `mood` только фиксируется в журнале.
        """
        genre_norms = self._genre_norms(genres)
        exclude_norms = self._genre_norms(exclude_genres)

        if year_from is not None:
            year_from = _require_int(year_from, "year_from", 1900, 2030)
        if year_to is not None:
            year_to = _require_int(year_to, "year_to", 1900, 2030)
        if year_from is not None and year_to is not None and year_from > year_to:
            raise _invalid("year_from: должен быть не больше year_to")
        if min_rating is not None:
            min_rating = _require_number(min_rating, "min_rating", 0, 10)
        if max_runtime is not None:
            max_runtime = _require_int(max_runtime, "max_runtime", 30, 600)
        if exclude_ids is None:
            exclude_ids = []
        if not isinstance(exclude_ids, list) or len(exclude_ids) > 50:
            raise _invalid("exclude_ids: ожидается до 50 целых")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in exclude_ids):
            raise _invalid("exclude_ids: ожидаются целые идентификаторы")
        if mood is not None and mood not in MOODS:
            raise _invalid("mood: недопустимое значение")
        limit = _require_int(limit, "limit", 3, 30)

        self._resolve_genres(genre_norms)
        self._resolve_genres(exclude_norms)
        if not self.movies:
            raise ServiceError(ErrorCode.CATALOG_EMPTY)

        rated = self._rated_ids(user_id)
        excluded = set(exclude_ids)
        result: list[MovieCard] = []
        for card in self.movies:
            if card.movie_id in rated or card.movie_id in excluded:
                continue
            card_genres = {_normalize(name) for name in card.genres}
            if genre_norms and not card_genres.intersection(genre_norms):
                continue
            if exclude_norms and card_genres.intersection(exclude_norms):
                continue
            if max_runtime is not None and (card.runtime is None or card.runtime > max_runtime):
                continue
            if year_from is not None and (card.year is None or card.year < year_from):
                continue
            if year_to is not None and (card.year is None or card.year > year_to):
                continue
            if min_rating is not None and (card.rating is None or card.rating < min_rating):
                continue
            result.append(card)

        result.sort(key=lambda card: (-(card.rating or 0.0), card.movie_id))
        # Копии: вызывающий не может испортить каталог заглушки.
        return [card.model_copy() for card in result[:limit]]

    @_journal
    def get_movie_with_credits(self, movie_id: int) -> MovieDetails:
        """Карточка, первые 10 актёров и описание — data/130 §4.5."""
        movie_id = _require_positive_int(movie_id, "movie_id")
        card = self._card_by_id(movie_id)
        if card is None:
            raise ToolError(ErrorCode.NOT_FOUND, "Фильм не найден.")
        item = self._details.get(movie_id, {})
        return MovieDetails(
            **card.model_dump(),
            actors=list(item.get("actors", []))[:10],
            overview=item.get("overview"),
        )

    @_journal
    def search_movie(self, title: str, year: int | None = None) -> list[MovieSummary]:
        """Поиск по нормализованному названию, не больше 5 результатов (§4.6)."""
        title = _require_str(title, "title", 100)
        if year is not None:
            year = _require_int(year, "year", 1900, 2030)
        query = _normalize(title)

        found: list[tuple[int, int, MovieCard]] = []
        for card in self.movies:
            if year is not None and card.year != year:
                continue
            name = _normalize(card.title)
            if query == name:
                rank = 0
            elif name.startswith(query):
                rank = 1
            elif query in name:
                rank = 2
            else:
                continue
            found.append((rank, -card.vote_count, card))

        found.sort(key=lambda item: (item[0], item[1], item[2].movie_id))
        return [
            MovieSummary(movie_id=card.movie_id, title=card.title, year=card.year)
            for _rank, _votes, card in found[:5]
        ]

    @_journal
    def save_choice(self, user_id: str, movie_id: int, mood: Mood | None = None) -> dict[str, int]:
        """Выбрать фильм из последней подборки; `mood` сохраняется (§4.7).

        Проверку по `last_shown_ids` в рабочем коде делает реестр инструментов;
        заглушка выполняет её сама — тестам агента так проще проверить отказ.
        """
        movie_id = _require_positive_int(movie_id, "movie_id")
        if mood is not None and mood not in MOODS:
            raise _invalid("mood: недопустимое значение")
        if self._card_by_id(movie_id) is None:
            raise ToolError(ErrorCode.NOT_FOUND, "Фильм не найден.")
        if movie_id not in self.last_shown.get(user_id, []):
            raise ToolError(ErrorCode.NOT_FOUND, "Фильм не входит в последнюю подборку.")
        return {"choice_id": self._insert_choice(user_id, movie_id, mood)}

    @_journal
    def save_rating(
        self,
        *,
        user_id: str,
        movie_id: int,
        rating: int,
        source: str,
        mood_match: str | None = None,
    ) -> dict[str, object]:
        """Upsert оценки; `pending` по фильму закрывается как `rated` (§4.8)."""
        movie_id = _require_positive_int(movie_id, "movie_id")
        rating = _require_int(rating, "rating", 1, 10)
        if source not in RATING_SOURCES:
            raise _invalid("source: ожидается after_choice или manual")
        if mood_match is not None and mood_match not in MOOD_MATCHES:
            raise _invalid("mood_match: ожидается yes, partly, no или None")
        if self._card_by_id(movie_id) is None:
            raise ToolError(ErrorCode.NOT_FOUND, "Фильм не найден.")

        self.ratings.setdefault(user_id, {})[movie_id] = _Rating(
            rating=rating,
            source=source,
            mood_match=mood_match,
            rated_at=self._now(),
        )
        closed_choice_id: int | None = None
        for choice in self.choices.get(user_id, []):
            if choice.movie_id == movie_id and choice.status == "pending":
                choice.status = "rated"
                choice.closed_at = self._now()
                closed_choice_id = choice.choice_id
        return {"saved": True, "closed_choice_id": closed_choice_id}

    @_journal
    def close_choice(self, choice_id: int, status: str = "dismissed") -> dict[str, bool]:
        """Закрыть выбор без оценки — data/130 §4.9."""
        if status != "dismissed":
            raise _invalid("status: единственное значение — dismissed")
        for choices in self.choices.values():
            for choice in choices:
                if choice.choice_id == choice_id and choice.status == "pending":
                    choice.status = "dismissed"
                    choice.closed_at = self._now()
                    return {"closed": True}
        raise ToolError(ErrorCode.NOT_FOUND, "Выбор не найден.")

    # --- Служебные функции: data/130 §4.10–4.21 ----------------------------

    @_journal
    def get_user(self, user_id: str) -> dict[str, object] | None:
        """Пользователь или `None` — data/130 §4.10."""
        user = self.users.get(user_id)
        return dict(user) if user is not None else None

    @_journal
    def save_consent(self, user_id: str, source: str) -> None:
        """Создать пользователя с согласием; повтор не меняет время (§4.11)."""
        if source not in USER_SOURCES:
            raise _invalid("source: ожидается web")
        if user_id in self.users:
            return
        moment = self._now()
        self.users[user_id] = {
            "user_id": user_id,
            "source": source,
            "consent_at": moment,
            "city": None,
            "latitude": None,
            "longitude": None,
            "created_at": moment,
        }

    @_journal
    def resolve_city(self, name: str) -> list[CityVariant]:
        """Варианты города; пустой список — город не найден (§4.12)."""
        query = _normalize(_require_str(name, "name", 100))
        return [
            city.model_copy(deep=True) for city in self.cities if query in _normalize(city.name)
        ]

    @_journal
    def save_user_settings(
        self, user_id: str, city: str, latitude: float, longitude: float
    ) -> None:
        """Сохранить город и координаты — data/130 §4.13."""
        city = _require_str(city, "city", 100)
        latitude = _require_number(latitude, "latitude", -90, 90)
        longitude = _require_number(longitude, "longitude", -180, 180)
        if user_id not in self.users:
            raise ValueError("пользователь не найден: согласие обязательно (ADR-002)")
        self.users[user_id].update({"city": city, "latitude": latitude, "longitude": longitude})

    @_journal
    def get_session_state(self, user_id: str) -> SessionState | None:
        """Прочитать состояние диалога — data/130 §4.14 (срок жизни не истекает)."""
        state = self.sessions.get(user_id)
        return state.model_copy(deep=True) if state is not None else None

    @_journal
    def save_session_state(self, user_id: str, state: SessionState) -> None:
        """Upsert состояния; последняя подборка запоминается для `save_choice`."""
        self.sessions[user_id] = state.model_copy(deep=True)
        self.last_shown[user_id] = list(state.last_shown_ids)

    @_journal
    def clear_session_state(self, user_id: str) -> None:
        """Удалить состояние; отсутствие — не ошибка (§4.14)."""
        self.sessions.pop(user_id, None)

    @_journal
    def get_pending_feedback(self, user_id: str) -> PendingFeedback | None:
        """Ожидающий выбор с карточкой фильма — data/130 §4.15."""
        for choice in self.choices.get(user_id, []):
            if choice.status == "pending":
                card = self._card_by_id(choice.movie_id)
                if card is None:
                    return None
                return PendingFeedback(
                    choice_id=choice.choice_id,
                    movie=card.model_copy(deep=True),
                    chosen_at=choice.chosen_at,
                )
        return None

    @_journal
    def get_history(self, user_id: str, limit: int = 50) -> list[HistoryItem]:
        """Оценки и выборы по убыванию времени — data/130 §4.16."""
        limit = _require_int(limit, "limit", 1, 50)
        rows: list[tuple[str, HistoryItem]] = []
        for movie_id, rating in self.ratings.get(user_id, {}).items():
            card = self._card_by_id(movie_id)
            if card is None:
                continue
            rows.append(
                (
                    rating.rated_at,
                    HistoryItem(
                        kind="rating",
                        movie=card.model_copy(deep=True),
                        rating=rating.rating,
                        mood_match=rating.mood_match,
                        status=None,
                        at=rating.rated_at,
                    ),
                )
            )
        for choice in self.choices.get(user_id, []):
            card = self._card_by_id(choice.movie_id)
            if card is None:
                continue
            rows.append(
                (
                    choice.chosen_at,
                    HistoryItem(
                        kind="choice",
                        movie=card.model_copy(deep=True),
                        rating=None,
                        mood_match=None,
                        status=choice.status,
                        at=choice.chosen_at,
                    ),
                )
            )
        rows.sort(key=lambda item: item[0], reverse=True)
        return [item for _at, item in rows[:limit]]

    @_journal
    def delete_user_data(self, user_id: str) -> dict[str, dict[str, int]]:
        """Удалить данные пользователя; отсутствующий — нули (§4.17)."""
        counts = {
            "user_ratings": len(self.ratings.get(user_id, {})),
            "user_choices": len(self.choices.get(user_id, [])),
            "sessions": 1 if user_id in self.sessions else 0,
            "users": 1 if user_id in self.users else 0,
        }
        self.users.pop(user_id, None)
        self.ratings.pop(user_id, None)
        self.choices.pop(user_id, None)
        self.sessions.pop(user_id, None)
        self.weather.pop(user_id, None)
        self.last_shown.pop(user_id, None)
        return {"deleted_counts": counts}

    @_journal
    def purge_expired(self) -> int:
        """Просроченные сессии; в заглушке сессии не истекают — всегда 0."""
        return 0

    @_journal
    def ingest_movies(
        self, pages: int = 500, from_year: int = 1970, min_votes: int = 100, resume: bool = False
    ) -> dict[str, object]:
        """Отчёт загрузки каталога без сети — data/130 §4.19.

        Заглушка каталог не меняет: тесты агента проверяют диалог, а загрузку
        проверяет TS-DB. Возвращается отчёт той же формы.
        """
        pages = _require_int(pages, "pages", 1, 500)
        _require_int(from_year, "from_year", 1900, 2030)
        _require_int(min_votes, "min_votes", 0, 100_000)
        if not isinstance(resume, bool):
            raise _invalid("resume: ожидается логическое значение")
        load_id = self._next_load_id
        self._next_load_id += 1
        return {
            "load_id": load_id,
            "status": "completed",
            "pages_done": pages,
            "movies_upserted": 0,
            "movies_skipped": 0,
            "last_page": pages,
            "duration_s": 0.0,
        }

    @_journal
    def get_catalog_size(self) -> int:
        """`COUNT(*)` каталога — data/130 §4.20."""
        return len(self.movies)

    @_journal
    def get_genres(self) -> list[str]:
        """Названия жанров справочника — data/130 §4.21."""
        return list(self._genres)

    # --- Проверки жанров ---------------------------------------------------

    @staticmethod
    def _genre_norms(names: list[str] | None) -> list[str]:
        """Проверить и нормализовать до 5 названий (data/130 §4.4)."""
        if names is None:
            return []
        if not isinstance(names, list) or len(names) > 5:
            raise _invalid("genres: ожидается до 5 названий")
        norms: list[str] = []
        for name in names:
            if not isinstance(name, str) or not name.strip() or len(name) > 40:
                raise _invalid("genres: каждое название — строка 1…40 символов")
            norms.append(_normalize(name))
        return norms

    def _resolve_genres(self, norms: Sequence[str]) -> None:
        """Неизвестное название — `UNKNOWN_GENRE` со списком допустимых."""
        known = {_normalize(name) for name in self._genres}
        for norm in norms:
            if norm not in known:
                raise ToolError(
                    ErrorCode.UNKNOWN_GENRE,
                    f"Жанр «{norm}» отсутствует. Допустимые: " + ", ".join(self._genres[:20]),
                )


def fake_user_with_ratings(
    data: FakeDataModule | None = None,
    *,
    user_id: str = DEFAULT_USER_ID,
    scenario: FakeScenario = "empty",
    mood: Mood | None = None,
    genre: str = "Ужасы",
    city: str = "Москва",
) -> FakeDataModule:
    """Готовая заглушка с пользователем и оценками под сценарии TS-AG.

    * `empty` — холодный старт: пользователь есть, оценок нет;
    * `ts-ag-13` — три оценки 9–10 в «драме» и одна 2 в «ужасах»: профиль
      даёт «ужасам» вес ≤ −0,15, «драме» — положительный (ADR-013);
    * `ts-ag-14` — один-два фильма жанра `genre` выбраны при настроении `mood`
      и оценены с `mood_match = "no"`; без `mood` сценарий невозможен.

    Журнал вызовов очищается: подготовка данных не должна попадать в проверки
    того, что передал реестр инструментов.
    """
    fake = data if data is not None else FakeDataModule(with_user=False)
    if user_id not in fake.users:
        fake.save_consent(user_id, "web")
        fake.save_user_settings(user_id, city, 55.75, 37.62)
    elif fake.users[user_id]["city"] != city:
        fake.save_user_settings(user_id, city, 55.75, 37.62)

    if scenario == "empty":
        pass
    elif scenario == "ts-ag-13":
        for movie_id, rating in TS_AG_13_RATINGS:
            fake.save_rating(
                user_id=user_id,
                movie_id=movie_id,
                rating=rating,
                source="manual",
            )
    elif scenario == "ts-ag-14":
        if mood is None:
            raise ValueError("сценарию ts-ag-14 нужен mood: настроение выбора (ADR-050)")
        picked = [
            card.movie_id
            for card in fake.movies
            if _normalize(genre) in map(_normalize, card.genres)
        ][:2]
        if not picked:
            raise ValueError(f"в каталоге заглушки нет фильмов жанра «{genre}»")
        fake.set_last_shown(user_id, picked)
        for movie_id, rating in zip(picked, (1, 2), strict=False):
            fake.save_choice(user_id, movie_id, mood=mood)
            fake.save_rating(
                user_id=user_id,
                movie_id=movie_id,
                rating=rating,
                source="after_choice",
                mood_match="no",
            )
    else:  # pragma: no cover - защита от опечатки в сценарии
        raise ValueError(f"неизвестный сценарий: {scenario}")

    fake.clear_calls()
    return fake
