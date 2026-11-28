"""Клиент TMDB: запросы, темп, повторы, нормализация — data/130 §7.

Запросы (§7.1): справочник жанров, страница `discover`, детали фильма с
`append_to_response=credits,release_dates`. Все запросы синхронные (ADR-047),
ключ — параметр `api_key`, `language=ru-RU` и `Accept: application/json`.

Правила (§7.4): не больше 40 запросов/с (пауза ≥ 25 мс), тайм-аут 10 с,
ответ 429 — пауза из `Retry-After`, ответ 5xx или тайм-аут — ряд 1, 2, 4, 8, 16 с;
после пяти неудачных попыток загрузка останавливается. 404 на детали — `None`
(фильм пропускается), 401 — `IngestError` и остановка.

Клиент не знает о базе: он отдаёт нормализованные `MovieRecord`, `GenreRef`,
`CastRef`; запись выполняет репозиторий каталога (data/150 §1, п. 6).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from cinematch.overall.logging import write_event

BASE_URL = "https://api.themoviedb.org/3"
LANGUAGE = "ru-RU"
REQUEST_TIMEOUT_S = 10.0

# Темп: ≤ 40 запросов/с — пауза между запросами не меньше 25 мс (NFR-DB-05).
MIN_INTERVAL_S = 0.025

# Пять неудачных попыток на один запрос — загрузка останавливается (data/130 §7.4).
MAX_ATTEMPTS = 5
BACKOFF_S = (1, 2, 4, 8, 16)

# Маркировка: закрытый перечень contracts.md §2.1, правило — ADR-018.
AGE_RATINGS = ("0+", "6+", "12+", "16+", "18+")
CERTIFICATION_TYPES = (3, 4, 5, 6, 1)
US_CERTIFICATIONS = {
    "G": "0+",
    "PG": "6+",
    "PG-13": "12+",
    "R": "16+",
    "NC-17": "18+",
}


class IngestError(Exception):
    """Остановка загрузки; видит только сопровождающий — data/130 §6.

    `reason = "unauthorized"` — ответ 401 или нет `TMDB_API_KEY` (exit code 2);
    `reason = "unavailable"` — TMDB не ответил после пяти попыток (exit code 4).
    `report` заполняет `ingest_movies`, чтобы скрипт напечатал отчёт и при отказе.
    """

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        report: dict[str, object] | None = None,
    ) -> None:
        self.reason = reason
        self.message = message
        self.report = report
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GenreRef:
    """Жанр из ответа TMDB: `id` и русское название (data/130 §7.1)."""

    tmdb_id: int
    name: str


@dataclass(frozen=True, slots=True)
class CastRef:
    """Актёр из `credits.cast`: `billing_order` — его индекс в массиве (data/120 §4)."""

    tmdb_id: int
    name: str
    character: str | None
    billing_order: int = 0


@dataclass(frozen=True, slots=True)
class MovieRecord:
    """Фильм после нормализации §7.2 — вход репозитория каталога."""

    tmdb_id: int
    title: str
    original_title: str | None
    year: int | None
    runtime: int | None
    rating: float | None
    vote_count: int
    popularity: float | None
    age_rating: str | None
    overview: str | None
    poster_path: str | None
    adult: int
    genres: tuple[GenreRef, ...]
    cast: tuple[CastRef, ...]


class TmdbClient:
    """Синхронный клиент TMDB с темпом и повторами (§7.4).

    `sleep` и `clock` подменяются в тестах: сеть и паузы не должны замедлять
    прогон. `transport` — `httpx.MockTransport` для ответов без сети.
    """

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = REQUEST_TIMEOUT_S,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key = api_key
        self._sleep = sleep
        self._clock = clock
        self._last_request = float("-inf")
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"Accept": "application/json"},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TmdbClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- Запросы §7.1 ------------------------------------------------------

    def genre_list(self) -> list[dict[str, Any]]:
        """`GET /genre/movie/list` — справочник жанров целиком."""
        payload = self._get("/genre/movie/list", {"language": LANGUAGE})
        return list(payload.get("genres") or [])

    def discover_page(
        self, page: int, *, min_votes: int, from_year: int
    ) -> list[dict[str, Any]]:
        """`GET /discover/movie` — до 20 фильмов страницы (§7.1, шаг 2)."""
        payload = self._get(
            "/discover/movie",
            {
                "language": LANGUAGE,
                "sort_by": "popularity.desc",
                "vote_count.gte": min_votes,
                "primary_release_date.gte": f"{from_year}-01-01",
                "include_adult": "false",
                "page": page,
            },
        )
        return list(payload.get("results") or [])

    def movie_details(self, tmdb_id: int) -> dict[str, Any] | None:
        """`GET /movie/{id}` с актёрами и сертификатами; 404 → `None` (§7.4)."""
        return self._get(
            f"/movie/{tmdb_id}",
            {"language": LANGUAGE, "append_to_response": "credits,release_dates"},
            allow_not_found=True,
        )

    # --- Темп и повторы ----------------------------------------------------

    def _pace(self) -> None:
        """Пауза до 25 мс с прошлого запроса: не больше 40 запросов/с."""
        now = self._clock()
        wait = MIN_INTERVAL_S - (now - self._last_request)
        if wait > 0:
            self._sleep(wait)
        self._last_request = self._clock()

    def _get(
        self,
        path: str,
        params: Mapping[str, object],
        *,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        """Запрос с повторами; 401 — `IngestError`, 404 — `None` при разрешении."""
        failure = 0
        while True:
            self._pace()
            response: httpx.Response | None
            try:
                response = self._client.get(path, params={**params, "api_key": self._api_key})
            except httpx.HTTPError:
                response = None

            if response is not None and response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    # Битый JSON — тот же ряд повторов, что и 5xx (§7.4).
                    response = None
                else:
                    if isinstance(payload, dict):
                        return payload
                    response = None

            if response is not None and response.status_code == 401:
                write_event(
                    "ingest_error",
                    payload={"code": "TMDB_UNAUTHORIZED", "layer": "data"},
                )
                raise IngestError("unauthorized", "TMDB ответил 401: ключ не принят")

            if response is not None and response.status_code == 404:
                if allow_not_found:
                    return None
                # Неописанный ответ: повторять бессмысленно, загрузка останавливается.
                raise IngestError("unavailable", "TMDB ответил 404 на запрос списка")

            if response is not None and response.status_code == 429:
                # `Retry-After` — точная пауза; без него действует ряд повторов.
                delay = self._retry_after(response, failure)
            elif response is None or response.status_code >= 500:
                delay = BACKOFF_S[min(failure, len(BACKOFF_S) - 1)]
            else:
                raise IngestError(
                    "unavailable", f"TMDB ответил {response.status_code} на {path}"
                )

            failure += 1
            if failure >= MAX_ATTEMPTS:
                raise IngestError(
                    "unavailable", f"TMDB не ответил на {path} после {MAX_ATTEMPTS} попыток"
                )
            self._sleep(delay)

    @staticmethod
    def _retry_after(response: httpx.Response, failure: int = 0) -> float:
        """Пауза 429: секунды из `Retry-After`, иначе ряд повторов (§7.4)."""
        header = response.headers.get("Retry-After", "").strip()
        try:
            seconds = float(header)
        except ValueError:
            return BACKOFF_S[min(failure, len(BACKOFF_S) - 1)]
        return max(seconds, 0.0)


# --- Нормализация §7.2 -----------------------------------------------------


def normalize_genre(raw: Mapping[str, Any]) -> GenreRef | None:
    """Жанр справочника: без числового `id` или названия — пропуск."""
    tmdb_id = raw.get("id")
    name = raw.get("name")
    if isinstance(tmdb_id, bool) or not isinstance(tmdb_id, int):
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    return GenreRef(tmdb_id=tmdb_id, name=name.strip())


def normalize_cast(raw: Mapping[str, Any], *, billing_order: int = 0) -> CastRef | None:
    """Актёр `credits.cast`: нужен числовой `id` и имя.

    `billing_order` — индекс элемента в исходном массиве (data/120 §4):
    передаёт `normalize_movie`, а не сам ответ TMDB.
    """
    tmdb_id = raw.get("id")
    name = raw.get("name")
    if isinstance(tmdb_id, bool) or not isinstance(tmdb_id, int):
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    character = raw.get("character")
    return CastRef(
        tmdb_id=tmdb_id,
        name=name.strip(),
        character=character.strip() if isinstance(character, str) and character.strip() else None,
        billing_order=billing_order,
    )


def _text(value: object) -> str | None:
    """Непустая обрезанная строка или `None` (§7.2: пустая строка — NULL)."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _int(value: object) -> int | None:
    """Целое из ответа TMDB; `bool` и прочее — `None`."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _float(value: object) -> float | None:
    """Число из ответа TMDB; `bool` и строки — `None`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def movie_title(item: Mapping[str, Any]) -> str:
    """`title`, затем `original_title`, затем «Без названия» — §7.2, contracts §2.1.

    Проверка «оба пусты» — в `normalize_movie`: такой фильм пропускается,
    поэтому «Без названия» здесь достижимо только для прямого вызова.
    """
    return _text(item.get("title")) or _text(item.get("original_title")) or "Без названия"


def movie_year(item: Mapping[str, Any]) -> int | None:
    """Первые 4 символа `release_date`; пустая или нечисловая дата — `None`."""
    date = _text(item.get("release_date"))
    if date is None or len(date) < 4:
        return None
    head = date[:4]
    if not head.isdigit():
        return None
    return int(head)


def normalize_movie(
    item: Mapping[str, Any], details: Mapping[str, Any] | None
) -> MovieRecord | None:
    """Собрать `MovieRecord` из ответа `discover` и деталей фильма (§7.2).

    `None` — фильм без `id` или с пустыми `title` и `original_title`: такой
    фильм пропускается и считается в `movies_skipped` (§7.2, последний абзац).
    Фильм без жанров сохраняется — жанровый фильтр его просто не найдёт.
    """
    # §7.2: `movies.tmdb_id` — `id` из `discover`; фильм без него пропускается.
    tmdb_id = _int(item.get("id"))
    if tmdb_id is None:
        return None
    if not (_text(item.get("title")) or _text(item.get("original_title"))):
        return None

    adult = bool(item.get("adult"))
    # §7.2: `vote_count` — из `discover`; детали нужны, если страница его не отдала.
    vote_count = _int(item.get("vote_count"))
    if vote_count is None:
        vote_count = _int((details or {}).get("vote_count")) or 0
    rating = _float(item.get("vote_average"))
    if vote_count == 0:
        # §7.2: `0` при `vote_count = 0` — NULL.
        rating = None

    runtime = _int((details or {}).get("runtime"))
    if runtime == 0:
        runtime = None

    genres: list[GenreRef] = []
    for raw in (details or {}).get("genres") or []:
        genre = normalize_genre(raw)
        if genre is not None and genre not in genres:
            genres.append(genre)

    cast: list[CastRef] = []
    seen: set[int] = set()
    credits = (details or {}).get("credits") or {}
    for index, raw in enumerate(list(credits.get("cast") or [])[:10]):
        actor = normalize_cast(raw, billing_order=index)
        # Один человек в cast дважды — остаётся первое вхождение (data/120 §4);
        # `billing_order` — индекс именно в исходном массиве, поэтому он может
        # идти с пропуском (0, 2, …), но остаётся в границах 0…9.
        if actor is not None and actor.tmdb_id not in seen:
            seen.add(actor.tmdb_id)
            cast.append(actor)

    return MovieRecord(
        tmdb_id=tmdb_id,
        title=movie_title(item),
        original_title=_text(item.get("original_title")),
        year=movie_year(item),
        runtime=runtime,
        rating=rating,
        vote_count=vote_count,
        popularity=_float(item.get("popularity")),
        age_rating=age_rating(details or {}, adult=adult),
        overview=_text((details or {}).get("overview")),
        poster_path=_text(item.get("poster_path")),
        adult=1 if adult else 0,
        genres=tuple(genres),
        cast=tuple(cast),
    )


def age_rating(details: Mapping[str, Any], *, adult: bool) -> str | None:
    """Возрастная маркировка из `release_dates` по правилу ADR-018.

    `adult = true` → `18+`; затем блок `RU` (типы 3, 4, 5, 6, 1), затем `US`
    с преобразованием; пусто в обоих — `NULL`.
    """
    if adult:
        return "18+"

    blocks = {
        str(block.get("iso_3166_1")): block
        for block in ((details.get("release_dates") or {}).get("results") or [])
    }
    russian = _certification(blocks.get("RU"))
    if russian is not None:
        return normalize_russian_certification(russian)
    american = _certification(blocks.get("US"))
    if american is not None:
        return US_CERTIFICATIONS.get(american)
    return None


def _certification(block: Mapping[str, Any] | None) -> str | None:
    """Первая непустая `certification` в порядке `type` 3, 4, 5, 6, 1 (ADR-018)."""
    if block is None:
        return None
    dates = list(block.get("release_dates") or [])
    for kind in CERTIFICATION_TYPES:
        for entry in dates:
            if _int(entry.get("type")) != kind:
                continue
            value = _text(entry.get("certification"))
            if value:
                return value
    return None


def normalize_russian_certification(value: str) -> str | None:
    """Привести маркировку RU к ближайшему значению перечня (ADR-018).

    Перечень закрыт: `0+`, `6+`, `12+`, `16+`, `18+`. Значение без плюса
    (`12`, `16`) приводится к ближайшему из перечня; при равном расстоянии
    выбирается старшая маркировка — так фильм не получает менее строгую
    оценку. Нераспознанное значение — `NULL` («маркировка не определена»).
    """
    cleaned = value.strip().replace(" ", "")
    if cleaned in AGE_RATINGS:
        return cleaned
    digits = cleaned.removesuffix("+")
    if not digits.isdigit():
        return None
    age = int(digits)
    boundaries = (0, 6, 12, 16, 18)
    # Ключ сортировки: сначала расстояние, затем старшая маркировка.
    nearest = min(range(len(boundaries)), key=lambda i: (abs(age - boundaries[i]), -boundaries[i]))
    return AGE_RATINGS[nearest]


def normalize_genres(raw_genres: Iterable[Mapping[str, Any]]) -> list[GenreRef]:
    """Справочник жанров без повторов; негодные записи пропускаются."""
    result: list[GenreRef] = []
    seen: set[int] = set()
    for raw in raw_genres:
        genre = normalize_genre(raw)
        if genre is not None and genre.tmdb_id not in seen:
            seen.add(genre.tmdb_id)
            result.append(genre)
    return result
