"""Функции-инструменты модуля данных для агента и оркестратора.

Единственная точка входа модуля: эти функции импортируют модуль агентов и
API-слой (data/150 §1, п. 3). Сигнатуры, проверки, ошибки и тексты — дословно
по documents/data/130 §3–4; коды ошибок и тексты — contracts.md §6.

Функции синхронные (ADR-047): Pydantic AI выполняет их в пуле потоков, а
оркестратор вызывает служебные функции через `asyncio.to_thread`.

`get_weather` и `resolve_city` (§4.3, §4.12) работают через `clients/weather.py`,
`ingest_movies` (§4.19) — через `clients/tmdb.py`; сеть и паузы подменяются
модульными `WEATHER_TRANSPORT`, `TMDB_TRANSPORT`, `SLEEP` только в тестах.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time

import httpx
from sqlalchemy.orm import Session

from cinematch.data import db
from cinematch.data.clients import tmdb as tmdb_client
from cinematch.data.clients.tmdb import IngestError, MovieRecord, TmdbClient
from cinematch.data.clients.weather import WeatherClient, WeatherNow
from cinematch.data.models import normalize_text
from cinematch.data.repositories import catalog, choices, loads, ratings, sessions, users
from cinematch.data.schemas import (
    HistoryItem,
    MovieDetails,
    MovieSummary,
    PendingFeedback,
    PreferenceProfile,
    RatedMovie,
    Weather,
)
from cinematch.data.services.profile import build_profile
from cinematch.overall.config import ENV_FILE, _parse_env_file
from cinematch.overall.contracts import CityVariant, Mood, MovieCard, SessionState
from cinematch.overall.errors import ErrorCode, ServiceError, ToolError
from cinematch.overall.logging import write_event

MOODS = {"calm", "cheerful", "sad", "tired", "tense", "other"}
MOOD_MATCHES = {"yes", "partly", "no"}
RATING_SOURCES = {"after_choice", "manual"}
USER_SOURCES = {"web"}

# Подмена внешних транспортов и пауз — только для тестов (сеть и задержки).
# Клиенты создаются с этими значениями; в обычной работе они `None` и `time.sleep`.
TMDB_TRANSPORT: httpx.BaseTransport | None = None
WEATHER_TRANSPORT: httpx.BaseTransport | None = None
SLEEP = time.sleep

# Кэш погоды живёт в процессе (data/130 §8.4); клиент создаётся при первом вызове.
_weather_client: WeatherClient | None = None

# Пределы параметров — data/130 §4.
MAX_GENRES = 5
MAX_GENRE_LEN = 40
MIN_YEAR = 1900
MAX_YEAR = 2030
MIN_RUNTIME = 30
MAX_RUNTIME_PARAM = 600
MAX_EXCLUDE_IDS = 50
MIN_LIMIT = 3
MAX_LIMIT = 30
DEFAULT_LIMIT = 15
MAX_CANDIDATES = 45


def _invalid(message: str) -> ToolError:
    """`INVALID_PARAM` — параметр вне диапазона или неверного типа."""
    return ToolError(ErrorCode.INVALID_PARAM, message)


def _require_int(value: object, name: str, low: int, high: int) -> int:
    """Целое в диапазоне `low…high` (bool не считается целым)."""
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _invalid(f"{name}: ожидается целое {low}…{high}")
    return value


def _require_number(value: object, name: str, low: float, high: float) -> float:
    """Число в диапазоне `low…high` (bool не считается числом)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"{name}: ожидается число {low}…{high}")
    if not low <= value <= high:
        raise _invalid(f"{name}: ожидается число {low}…{high}")
    return float(value)


def _require_str(value: object, name: str, max_length: int) -> str:
    """Обрезанная непустая строка не длиннее `max_length`."""
    if not isinstance(value, str):
        raise _invalid(f"{name}: ожидается строка")
    trimmed = value.strip()
    if not trimmed or len(trimmed) > max_length:
        raise _invalid(f"{name}: ожидается строка 1…{max_length} символов")
    return trimmed


def _require_positive_int(value: object, name: str) -> int:
    """Целое больше нуля — проверки `movie_id` и `choice_id` (data/130 §4.5–4.9)."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _invalid(f"{name}: ожидается целое больше нуля")
    return value


def _genre_norms(names: list[str] | None) -> list[str]:
    """Проверить список жанров и вернуть нормализованные названия (data/130 §4.4)."""
    if names is None:
        return []
    if not isinstance(names, list) or len(names) > MAX_GENRES:
        raise _invalid(f"genres: ожидается до {MAX_GENRES} названий")
    norms: list[str] = []
    for name in names:
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_GENRE_LEN:
            raise _invalid(f"genres: каждое название — строка 1…{MAX_GENRE_LEN} символов")
        norms.append(normalize_text(name))
    return norms


def _resolve_genres(session: Session, norms: list[str]) -> list[int]:
    """Названия жанров → id; неизвестный жанр — `UNKNOWN_GENRE` (data/130 §4.4)."""
    if not norms:
        return []
    known = catalog.genre_names(session)
    by_norm = catalog.genre_ids_by_norm(session, norms)
    resolved: list[int] = []
    for norm in norms:
        genre_id = by_norm.get(norm)
        if genre_id is None:
            raise ToolError(
                ErrorCode.UNKNOWN_GENRE,
                f"Жанр «{norm}» отсутствует. Допустимые: " + ", ".join(known[:20]),
            )
        if genre_id not in resolved:
            resolved.append(genre_id)
    return resolved


# --- Инструменты агента: data/130 §4.1–4.9 ---------------------------------


def get_user_ratings(user_id: str, limit: int = 20) -> list[RatedMovie]:
    """Оценки пользователя по убыванию `rated_at` — data/130 §4.1."""
    limit = _require_int(limit, "limit", 1, 50)
    with db.translate_db_errors(), db.session_scope() as session:
        rows = catalog.ratings_with_genres(session, user_id, limit)
        genres = catalog.genre_names_for_movies(session, [row.movie_id for row in rows])
        return [
            RatedMovie(
                movie_id=row.movie_id,
                title=row.title,
                year=row.year,
                genres=genres.get(row.movie_id, []),
                rating=row.rating,
                mood_match=row.mood_match,
                rated_at=row.rated_at,
            )
            for row in rows
        ]


def get_user_profile(user_id: str) -> PreferenceProfile:
    """Профиль по всем оценкам, считается при чтении — data/130 §4.2, ADR-013."""
    with db.translate_db_errors(), db.session_scope() as session:
        return build_profile(session, user_id)


def _tmdb_api_key() -> str:
    """Ключ `TMDB_API_KEY`: окружение важнее `.env` (config.md §1, п. 6).

    Пустое значение — остановка загрузки: `CONFIG_MISSING TMDB_API_KEY`,
    exit code 2 (data/110 §2).
    """
    raw = os.environ.get("TMDB_API_KEY", "").strip()
    if not raw:
        raw = _parse_env_file(ENV_FILE).get("TMDB_API_KEY", "").strip()
    if not raw:
        raise IngestError("unauthorized", "CONFIG_MISSING TMDB_API_KEY")
    return raw


def _weather() -> WeatherClient:
    """Клиент Open-Meteo процесса; кэш переживает вызовы (data/130 §8.4)."""
    global _weather_client
    if _weather_client is None:
        _weather_client = WeatherClient(transport=WEATHER_TRANSPORT)
    return _weather_client


def get_weather(user_id: str) -> Weather | None:
    """Погода города пользователя — data/130 §4.3, §8.

    `None`, если города нет или сервис недоступен: погода — подсказка, а не
    условие подбора, поэтому отказа сети наружу не выходит (§4.3).
    """
    with db.translate_db_errors(), db.session_scope() as session:
        user = users.get_user(session, user_id)
        if user is None or not user.city:
            return None
        city = user.city
        latitude = user.latitude
        longitude = user.longitude

    if latitude is None or longitude is None:
        return None
    current: WeatherNow | None = _weather().forecast(latitude, longitude)
    if current is None:
        return None
    return Weather(
        city=city,
        condition=current.condition,
        temp_c=current.temp_c,
        is_day=current.is_day,
    )


def find_movies(
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
    limit: int = DEFAULT_LIMIT,
) -> list[MovieCard]:
    """Кандидаты подборки с пересортировкой по профилю — data/130 §4.4.

    SQL берёт `min(3 × limit, 45)` кандидатов, затем функция пересортировывает их
    по `score = matched + Σ weight_g` со штрафом −1,0 за жанр, получивший
    `mood_match = no` при том же настроении (ADR-049, ADR-050).
    """
    genre_norms = _genre_norms(genres)
    exclude_norms = _genre_norms(exclude_genres)

    if year_from is not None:
        year_from = _require_int(year_from, "year_from", MIN_YEAR, MAX_YEAR)
    if year_to is not None:
        year_to = _require_int(year_to, "year_to", MIN_YEAR, MAX_YEAR)
    if year_from is not None and year_to is not None and year_from > year_to:
        raise _invalid("year_from: должен быть не больше year_to")
    if min_rating is not None:
        min_rating = _require_number(min_rating, "min_rating", 0, 10)
    if max_runtime is not None:
        max_runtime = _require_int(max_runtime, "max_runtime", MIN_RUNTIME, MAX_RUNTIME_PARAM)
    if exclude_ids is None:
        exclude_ids = []
    if not isinstance(exclude_ids, list) or len(exclude_ids) > MAX_EXCLUDE_IDS:
        raise _invalid(f"exclude_ids: ожидается до {MAX_EXCLUDE_IDS} целых")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in exclude_ids):
        raise _invalid("exclude_ids: ожидаются целые идентификаторы")
    if mood is not None and mood not in MOODS:
        raise _invalid("mood: недопустимое значение")
    limit = _require_int(limit, "limit", MIN_LIMIT, MAX_LIMIT)

    candidates_limit = min(3 * limit, MAX_CANDIDATES)

    with db.translate_db_errors(), db.session_scope() as session:
        genre_ids = _resolve_genres(session, genre_norms)
        exclude_genre_ids = _resolve_genres(session, exclude_norms)

        # Порядок шагов — data/146 §3: сначала жанры (UNKNOWN_GENRE), затем
        # пустой каталог (CATALOG_EMPTY), затем SQL.
        if catalog.catalog_is_empty(session):
            raise ServiceError(ErrorCode.CATALOG_EMPTY)

        rows = catalog.find_candidates(
            session,
            user_id=user_id,
            genre_ids=genre_ids,
            exclude_genre_ids=exclude_genre_ids,
            year_from=year_from,
            year_to=year_to,
            min_rating=min_rating,
            max_runtime=max_runtime,
            exclude_ids=exclude_ids,
            limit=candidates_limit,
        )
        if not rows:
            return []

        genres_by_movie = catalog.genre_names_for_movies(
            session, [row.movie_id for row in rows]
        )
        profile = build_profile(session, user_id)
        penalized = (
            catalog.mood_penalty_genres(session, user_id, mood) if mood is not None else set()
        )

        scored: list[tuple[float, int, float, int, int, catalog.Candidate]] = []
        for row in rows:
            movie_genres = genres_by_movie.get(row.movie_id, [])
            score = row.matched + sum(
                profile.genre_weights.get(name, 0.0) for name in movie_genres
            )
            penalty = sum(1 for name in movie_genres if name in penalized)
            score -= float(penalty)
            # Веса округлены до 3 знаков (ADR-013), поэтому равенство score —
            # настоящее равенство, а не расхождение сложения float на 1e-16:
            # иначе правило «фильм со штрафом ниже» перестаёт работать.
            scored.append(
                (
                    round(score, 6),
                    penalty,
                    row.rating or 0.0,
                    row.vote_count,
                    row.movie_id,
                    row,
                )
            )

        # При равенстве score: без штрафа выше, затем rating DESC, vote_count DESC,
        # id ASC — прежний SQL-порядок (ADR-049, ADR-050).
        scored.sort(key=lambda item: (-item[0], item[1], -item[2], -item[3], item[4]))

        return [
            catalog.card_from_candidate(row, genres_by_movie.get(row.movie_id, []))
            for *_rest, row in scored[:limit]
        ]


def get_movie_with_credits(movie_id: int) -> MovieDetails:
    """Карточка, первые 10 актёров и описание — data/130 §4.5."""
    movie_id = _require_positive_int(movie_id, "movie_id")
    with db.translate_db_errors(), db.session_scope() as session:
        movie = catalog.movie_by_id(session, movie_id)
        if movie is None:
            raise ToolError(ErrorCode.NOT_FOUND, "Фильм не найден.")
        genres = catalog.genre_names_for_movies(session, [movie_id]).get(movie_id, [])
        card = catalog.card_from_movie(movie, genres)
        return MovieDetails(
            **card.model_dump(),
            actors=catalog.actors_for_movie(session, movie_id),
            overview=movie.overview,
        )


def search_movie(title: str, year: int | None = None) -> list[MovieSummary]:
    """Поиск по `title_norm`, не больше 5 результатов — data/130 §4.6."""
    title = _require_str(title, "title", 100)
    if year is not None:
        year = _require_int(year, "year", MIN_YEAR, MAX_YEAR)
    with db.translate_db_errors(), db.session_scope() as session:
        rows = catalog.search_by_title(session, normalize_text(title), year)
        return [
            MovieSummary(movie_id=row.id, title=row.title, year=row.year) for row in rows
        ]


def save_choice(user_id: str, movie_id: int, mood: Mood | None = None) -> dict[str, int]:
    """Выбрать фильм: прежний `pending` → `dismissed`, новый — `pending`.

    Проверку по `last_shown_ids` выполняет реестр инструментов до вызова
    (data/130 §4.7); здесь — остальные проверки и запись `mood` (ADR-050).
    """
    movie_id = _require_positive_int(movie_id, "movie_id")
    if mood is not None and mood not in MOODS:
        raise _invalid("mood: недопустимое значение")
    with db.translate_db_errors(), db.session_scope() as session:
        if not catalog.movie_exists(session, movie_id):
            raise ToolError(ErrorCode.NOT_FOUND, "Фильм не найден.")
        choices.dismiss_pending(session, user_id)
        choice_id = choices.insert_pending(
            session, user_id=user_id, movie_id=movie_id, mood=mood
        )
        return {"choice_id": choice_id}


def save_rating(
    *,
    user_id: str,
    movie_id: int,
    rating: int,
    source: str,
    mood_match: str | None = None,
) -> dict[str, object]:
    """Upsert оценки; `pending` по фильму закрывается как `rated` — data/130 §4.8."""
    movie_id = _require_positive_int(movie_id, "movie_id")
    rating = _require_int(rating, "rating", 1, 10)
    if source not in RATING_SOURCES:
        raise _invalid("source: ожидается after_choice или manual")
    if mood_match is not None and mood_match not in MOOD_MATCHES:
        raise _invalid("mood_match: ожидается yes, partly, no или None")
    with db.translate_db_errors(), db.session_scope() as session:
        if not catalog.movie_exists(session, movie_id):
            raise ToolError(ErrorCode.NOT_FOUND, "Фильм не найден.")
        ratings.upsert_rating(
            session,
            user_id=user_id,
            movie_id=movie_id,
            rating=rating,
            source=source,
            mood_match=mood_match,
        )
        closed_choice_id = ratings.close_pending_choice_for_movie(session, user_id, movie_id)
        return {"saved": True, "closed_choice_id": closed_choice_id}


def close_choice(choice_id: int, status: str = "dismissed") -> dict[str, bool]:
    """Закрыть выбор без оценки — data/130 §4.9.

    Документ проверяет только `status` (`INVALID_PARAM`) и наличие строки в
    `pending` (`NOT_FOUND`); `choice_id` подставляет код из `SessionState.pending`,
    поэтому диапазон здесь не проверяется.
    """
    if status != "dismissed":
        raise _invalid("status: единственное значение — dismissed")
    with db.translate_db_errors(), db.session_scope() as session:
        if not choices.close_choice(session, choice_id):
            raise ToolError(ErrorCode.NOT_FOUND, "Выбор не найден.")
        return {"closed": True}


# --- Служебные функции: data/130 §4.10–4.21 --------------------------------


def get_user(user_id: str) -> dict[str, object] | None:
    """Пользователь или `None` — data/130 §4.10."""
    with db.translate_db_errors(), db.session_scope() as session:
        user = users.get_user(session, user_id)
        if user is None:
            return None
        return {
            "user_id": user.user_id,
            "source": user.source,
            "consent_at": user.consent_at,
            "city": user.city,
            "latitude": user.latitude,
            "longitude": user.longitude,
            "created_at": user.created_at,
        }


def save_consent(user_id: str, source: str) -> None:
    """Создать пользователя с согласием; повтор не перезаписывает время (§4.11)."""
    if source not in USER_SOURCES:
        raise _invalid("source: ожидается web")
    with db.translate_db_errors(), db.session_scope() as session:
        users.save_consent(session, user_id, source)


def resolve_city(name: str) -> list[CityVariant]:
    """Варианты города — data/130 §4.12, §8.1.

    До пяти вариантов из Open-Meteo Geocoding; пустой список — город не найден,
    оркестратор отвечает `CITY_NOT_FOUND` (contracts §6). Отказ сервиса тоже
    даёт пустой список: `resolve_city` не бросает сетевое исключение наружу.
    """
    query = _require_str(name, "name", 100)
    return [
        CityVariant(
            name=city.name,
            region=city.region,
            country=city.country,
            latitude=city.latitude,
            longitude=city.longitude,
        )
        for city in _weather().search_city(query)
    ]


def save_user_settings(user_id: str, city: str, latitude: float, longitude: float) -> None:
    """Сохранить город и координаты — data/130 §4.13."""
    city = _require_str(city, "city", 100)
    latitude = _require_number(latitude, "latitude", -90, 90)
    longitude = _require_number(longitude, "longitude", -180, 180)
    with db.translate_db_errors(), db.session_scope() as session:
        if not users.save_settings(session, user_id, city, latitude, longitude):
            raise ValueError("пользователь не найден: согласие обязательно (ADR-002)")


def get_session_state(user_id: str) -> SessionState | None:
    """Прочитать состояние; просроченное или повреждённое удаляет (§4.14)."""
    with db.translate_db_errors(), db.session_scope() as session:
        row = sessions.get_session(session, user_id)
        if row is None:
            return None
        if row.expires_at <= db.utc_now():
            sessions.delete_session(session, user_id)
            return None
        try:
            return SessionState.model_validate_json(row.state_json)
        except ValueError:
            # Повреждённый JSON: удалить строку, записать DATA_UNAVAILABLE (data/120 §6, п. 3).
            sessions.delete_session(session, user_id)
            write_event(
                "error",
                user_id=user_id,
                payload={"code": "DATA_UNAVAILABLE", "layer": "data"},
            )
            return None


def save_session_state(user_id: str, state: SessionState) -> None:
    """Upsert состояния; каждое сохранение продлевает сессию (§4.14)."""
    with db.translate_db_errors(), db.session_scope() as session:
        sessions.save_session(session, user_id, state.model_dump_json())


def clear_session_state(user_id: str) -> None:
    """Удалить состояние; отсутствие — не ошибка (§4.14)."""
    with db.translate_db_errors(), db.session_scope() as session:
        sessions.delete_session(session, user_id)


def get_pending_feedback(user_id: str) -> PendingFeedback | None:
    """Ожидающий выбор с карточкой фильма — data/130 §4.15."""
    with db.translate_db_errors(), db.session_scope() as session:
        choice = choices.pending_choice(session, user_id)
        if choice is None:
            return None
        card = catalog.cards_for_movies(session, [choice.movie_id]).get(choice.movie_id)
        if card is None:
            return None
        return PendingFeedback(choice_id=choice.id, movie=card, chosen_at=choice.chosen_at)


def get_history(user_id: str, limit: int = 50) -> list[HistoryItem]:
    """Оценки и выборы по убыванию времени — data/130 §4.16."""
    limit = _require_int(limit, "limit", 1, 50)
    with db.translate_db_errors(), db.session_scope() as session:
        rows = choices.history_rows(session, user_id, limit)
        movie_ids = [row.movie_id for _kind, row in rows]
        cards = catalog.cards_for_movies(session, movie_ids)
        items: list[HistoryItem] = []
        for kind, row in rows:
            card = cards.get(row.movie_id)
            if card is None:
                continue
            if kind == "rating":
                items.append(
                    HistoryItem(
                        kind="rating",
                        movie=card,
                        rating=row.rating,
                        mood_match=row.mood_match,
                        status=None,
                        at=row.rated_at,
                    )
                )
            else:
                items.append(
                    HistoryItem(
                        kind="choice",
                        movie=card,
                        rating=None,
                        mood_match=None,
                        status=row.status,
                        at=row.chosen_at,
                    )
                )
        return items


def delete_user_data(user_id: str) -> dict[str, dict[str, int]]:
    """Удалить данные пользователя одной транзакцией — data/130 §4.17."""
    with db.translate_db_errors(), db.session_scope() as session:
        counts = {
            "user_ratings": ratings.count_ratings(session, user_id),
            "user_choices": choices.count_choices(session, user_id),
            "sessions": sessions.count_sessions(session, user_id),
            "users": 1 if users.get_user(session, user_id) is not None else 0,
        }
        users.delete_user(session, user_id)
        return {"deleted_counts": counts}


def purge_expired() -> int:
    """Удалить сессии с `expires_at <= now` — data/130 §4.18.

    Событие `SessionExpired` пакетное: `user_id = null`, в `payload` только
    число удалённых строк (data/140 §1, п. 2).
    """
    with db.translate_db_errors(), db.session_scope() as session:
        removed = sessions.purge_expired(session)
    if removed:
        # Пакетная запись: `user_id = null`, в `payload` только число строк
        # (data/140 §1, п. 2). Пустой результат не публикуется: событие
        # «сессия истекла» без истёкших сессий ничего не сообщает, а старт
        # backend вызывает `purge_expired` каждый раз (build.md §3, шаг 5).
        write_event("SessionExpired", payload={"sessions": removed})
    return removed


def ingest_movies(
    pages: int = 500,
    from_year: int = 1970,
    min_votes: int = 100,
    resume: bool = False,
) -> dict[str, object]:
    """Загрузка каталога из TMDB — data/130 §4.19, §7; UC-9 (data/060).

    Алгоритм — data/146 §2: запись `catalog_loads` (`running` или найденная
    при `resume`), справочник жанров одной транзакцией, затем страницы: одна
    транзакция на страницу, `last_page` фиксируется после коммита.

    Отчёт: `{load_id, status, pages_done, movies_upserted, movies_skipped,
    last_page, duration_s}` (§4.19). Прерывание `Ctrl+C` коммитит текущую
    страницу и даёт `partial`; 401 — `failed` и `IngestError`, исчерпанные
    повторы — `partial` и `IngestError` (§7.4–§7.5).
    """
    pages = _require_int(pages, "pages", 1, 500)
    from_year = _require_int(from_year, "from_year", MIN_YEAR, MAX_YEAR)
    min_votes = _require_int(min_votes, "min_votes", 0, 100_000)
    if not isinstance(resume, bool):
        raise _invalid("resume: ожидается логическое значение")

    started = time.monotonic()
    api_key = _tmdb_api_key()
    params = loads.params_json(pages, from_year, min_votes)

    with db.translate_db_errors(), db.session_scope() as session:
        load = loads.find_resumable(session, params) if resume else None
        if load is None:
            load = loads.create_load(session, params)
            start_page = 1
            resumed = False
        else:
            loads.mark_running(session, load)
            start_page = load.last_page + 1
            resumed = True
        load_id = load.id

    write_event(
        "CatalogLoadStarted",
        payload={
            "load_id": load_id,
            "pages": pages,
            "from_year": from_year,
            "min_votes": min_votes,
            "resumed": resumed,
        },
    )

    progress_shown = False

    def finish(status: str) -> dict[str, object]:
        """Терминальный статус и отчёт §4.19; вызывается ровно один раз."""
        if progress_shown:
            _end_progress()
        duration_s = round(time.monotonic() - started, 1)
        with db.translate_db_errors(), db.session_scope() as session:
            row = loads.by_id(session, load_id)
            assert row is not None
            loads.finish(session, row, status)
            result: dict[str, object] = {
                "load_id": row.id,
                "status": status,
                "pages_done": max(row.last_page - start_page + 1, 0),
                "movies_upserted": row.movies_upserted,
                "movies_skipped": row.movies_skipped,
                "last_page": row.last_page,
                "duration_s": duration_s,
            }
        write_event(
            "CatalogLoadFinished",
            payload={
                "load_id": result["load_id"],
                "status": status,
                "pages_done": result["pages_done"],
                "movies_upserted": result["movies_upserted"],
                "movies_skipped": result["movies_skipped"],
                "last_page": result["last_page"],
                "duration_s": duration_s,
            },
        )
        return result

    client = TmdbClient(api_key, transport=TMDB_TRANSPORT, sleep=SLEEP)
    try:
        genres = tmdb_client.normalize_genres(client.genre_list())
        with db.translate_db_errors(), db.session_scope() as session:
            catalog.upsert_genres(session, genres)

        for page in range(start_page, pages + 1):
            items = client.discover_page(page, min_votes=min_votes, from_year=from_year)
            if not items:
                # Страницы кончились: это успех, а не ошибка (data/146 §2).
                break

            records: list[MovieRecord] = []
            skipped = 0
            for item in items:
                tmdb_id = item.get("id")
                if isinstance(tmdb_id, bool) or not isinstance(tmdb_id, int):
                    skipped += 1
                    continue
                details = client.movie_details(tmdb_id)
                if details is None:
                    # 404 на детали — фильм пропускается (§7.4).
                    skipped += 1
                    continue
                record = tmdb_client.normalize_movie(item, details)
                if record is None:
                    skipped += 1
                    continue
                records.append(record)

            with db.translate_db_errors(), db.session_scope() as session:
                genre_ids = catalog.genre_ids_by_tmdb(session)
                inserted = 0
                for record in records:
                    _, created = catalog.upsert_movie(session, record, genre_ids)
                    inserted += 1 if created else 0
                row = loads.by_id(session, load_id)
                assert row is not None
                loads.save_progress(
                    session,
                    row,
                    last_page=page,
                    upserted=len(records),
                    skipped=skipped,
                )
                shown_upserted = row.movies_upserted
                shown_skipped = row.movies_skipped

            write_event(
                "MoviesUpserted",
                payload={
                    "load_id": load_id,
                    "page": page,
                    "movies": len(records),
                    "inserted": inserted,
                    "updated": len(records) - inserted,
                },
            )
            _print_progress(
                page=page,
                pages=pages,
                films=shown_upserted,
                skipped=shown_skipped,
                started=started,
            )
            progress_shown = True
    except KeyboardInterrupt:
        # Текущая порция уже закоммичена; прерывание не теряет страницу (§7.5, п. 3).
        result = finish("partial")
        raise IngestError(
            "interrupted",
            f"Загрузка прервана на странице {result['last_page']}.",
            report=result,
        ) from None
    except IngestError as error:
        status = "failed" if error.reason == "unauthorized" else "partial"
        raise IngestError(error.reason, error.message, report=finish(status)) from error
    except ServiceError:
        # Ошибка базы: уже зафиксированные порции остаются (data/146 §2, п. 4).
        # Сбой записи статуса не должен скрыть исходную ошибку базы.
        with contextlib.suppress(ServiceError):
            finish("failed")
        raise
    finally:
        client.close()

    return finish("completed")


def _print_progress(
    *, page: int, pages: int, films: int, skipped: int, started: float
) -> None:
    """Строка прогресса в stderr без переносов — data/110 §1.

    Печатается на каждой странице независимо от терминала: при перенаправлении
    вывода `\\r` — разделитель, а отчёт всё равно идёт в stdout.
    """
    elapsed = int(time.monotonic() - started)
    hours, rest = divmod(elapsed, 3600)
    minutes, seconds = divmod(rest, 60)
    stamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
    sys.stderr.write(
        f"\rpage {page}/{pages}  films {films}  skipped {skipped}  elapsed {stamp}"
    )
    sys.stderr.flush()


def _end_progress() -> None:
    """Закрыть строку прогресса перед отчётом или ошибкой (data/110 §1)."""
    sys.stderr.write("\n")
    sys.stderr.flush()


def get_catalog_size() -> int:
    """`SELECT COUNT(*) FROM movies` — data/130 §4.20."""
    with db.translate_db_errors(), db.session_scope() as session:
        return catalog.catalog_size(session)


def get_genres() -> list[str]:
    """`SELECT name FROM genres ORDER BY tmdb_id` — data/130 §4.21."""
    with db.translate_db_errors(), db.session_scope() as session:
        return catalog.genre_names(session)


__all__ = [
    "clear_session_state",
    "close_choice",
    "delete_user_data",
    "find_movies",
    "get_catalog_size",
    "get_genres",
    "get_history",
    "get_movie_with_credits",
    "get_pending_feedback",
    "get_session_state",
    "get_user",
    "get_user_profile",
    "get_user_ratings",
    "get_weather",
    "ingest_movies",
    "purge_expired",
    "resolve_city",
    "save_choice",
    "save_consent",
    "save_rating",
    "save_session_state",
    "save_user_settings",
    "search_movie",
]
