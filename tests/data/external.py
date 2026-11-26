"""Моки TMDB и Open-Meteo для тестов модуля данных (docs/testing/ts-db.md).

Сеть запрещена: `httpx.MockTransport` отвечает по записанному сценарию, поэтому
каждый ответ и каждая пауза проверяемы. Фикстуры повторяют формы ответов §7.1
и §8.1, а не вымышленные структуры.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

GENRES = [
    {"id": 28, "name": "Боевик"},
    {"id": 35, "name": "Комедия"},
    {"id": 18, "name": "Драма"},
    {"id": 27, "name": "Ужасы"},
]


def discover_item(
    tmdb_id: int,
    *,
    title: str = "",
    original_title: str | None = None,
    release_date: str = "2014-05-01",
    vote_average: float = 7.4,
    vote_count: int = 320,
    adult: bool = False,
    poster_path: str | None = "/poster.jpg",
) -> dict[str, Any]:
    """Элемент `results[]` страницы `discover` — поля §7.1, шаг 2."""
    return {
        "id": tmdb_id,
        "title": title,
        "original_title": original_title,
        "release_date": release_date,
        "vote_average": vote_average,
        "vote_count": vote_count,
        "popularity": 12.5,
        "poster_path": poster_path,
        "genre_ids": [28],
        "adult": adult,
        "overview": "краткое описание",
    }


def details(
    tmdb_id: int,
    *,
    runtime: int = 98,
    genres: Sequence[dict[str, Any]] | None = None,
    cast: Sequence[dict[str, Any]] | None = None,
    overview: str | None = "Полное описание",
    release_dates: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ответ `movie/{id}?append_to_response=credits,release_dates` — §7.1, шаг 3."""
    return {
        "id": tmdb_id,
        "runtime": runtime,
        "overview": overview,
        "genres": list(genres if genres is not None else GENRES[:2]),
        "credits": {
            "cast": list(
                cast
                if cast is not None
                else [
                    {"id": 900 + tmdb_id, "name": "Актёр Один", "character": "Герой"},
                    {"id": 800 + tmdb_id, "name": "Актёр Два", "character": ""},
                ]
            )
        },
        "release_dates": {
            "results": list(
                release_dates
                if release_dates is not None
                else [
                    {
                        "iso_3166_1": "RU",
                        "release_dates": [
                            {"type": 3, "certification": "12+"},
                            {"type": 4, "certification": ""},
                        ],
                    }
                ]
            )
        },
    }


@dataclass
class TmdbMock:
    """Сценарий ответов TMDB: страницы, детали, коды ошибок по id и запросу.

    `page_results(page)` отдаёт список фильмов; `detail_for(id)` — детали или
    `None` (404). `fail` — очередь статусов для конкретного пути: каждый ответ
    из неё снимается по одному запросу. `page_failures` — очередь статусов для
    страницы `discover`: проверка повторов и «пять неудач подряд».
    """

    page_results: Callable[[int], list[dict[str, Any]]] = lambda page: []
    detail_for: Callable[[int], dict[str, Any] | None] = lambda tmdb_id: details(tmdb_id)
    fail: dict[str, list[int]] = field(default_factory=dict)
    page_failures: dict[int, list[int]] = field(default_factory=dict)
    retry_after: str | None = None
    calls: list[httpx.Request] = field(default_factory=list)
    details_requested: list[int] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path.removeprefix("/3")

        queue = self.fail.get(path)
        if queue:
            status = queue.pop(0)
            return self._error(status)

        if path == "/genre/movie/list":
            return httpx.Response(200, json={"genres": GENRES})

        if path == "/discover/movie":
            page = int(request.url.params.get("page", "1"))
            page_queue = self.page_failures.get(page)
            if page_queue:
                return self._error(page_queue.pop(0))
            return httpx.Response(200, json={"page": page, "results": self.page_results(page)})

        if path.startswith("/movie/"):
            tmdb_id = int(path.rsplit("/", 1)[1])
            self.details_requested.append(tmdb_id)
            payload = self.detail_for(tmdb_id)
            if payload is None:
                return self._error(404)
            return httpx.Response(200, json=payload)

        return self._error(404)

    def _error(self, status: int) -> httpx.Response:
        headers = {}
        if self.retry_after is not None and status == 429:
            headers["Retry-After"] = self.retry_after
        return httpx.Response(status, headers=headers, json={"status_code": status})

    def paths(self) -> list[str]:
        """Пути запросов в порядке отправки: проверка повторов и темпа."""
        return [request.url.path.removeprefix("/3") for request in self.calls]


def pages_of(
    pages: int, *, per_page: int = 2, first_id: int = 1000
) -> Callable[[int], list[dict[str, Any]]]:
    """Страницы с предсказуемыми `tmdb_id`: страница `p` — `first_id + (p-1)*20…`.

    За страницей `pages` — пустой список: цикл загрузки останавливается сам
    (data/146 §2), поэтому число фильмов известно точно.
    """
    base = first_id

    def page_results(page: int) -> list[dict[str, Any]]:
        if page > pages:
            return []
        start = base + (page - 1) * 20
        return [
            discover_item(start + index, title=f"Фильм {start + index}")
            for index in range(per_page)
        ]

    return page_results


@dataclass
class WeatherMock:
    """Сценарий Open-Meteo: геокодирование и прогноз; счётчики запросов."""

    geocoding: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    forecast: dict[tuple[float, float], dict[str, Any]] = field(default_factory=dict)
    fail_paths: dict[str, int] = field(default_factory=dict)
    geocoding_calls: list[str] = field(default_factory=list)
    forecast_calls: list[tuple[float, float]] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "geocoding-api.open-meteo.com":
            name = request.url.params.get("name", "")
            self.geocoding_calls.append(name)
            status = self.fail_paths.get("geocoding")
            if status:
                return httpx.Response(status)
            return httpx.Response(
                200, json={"results": self.geocoding.get(name.lower(), [])}
            )

        if request.url.host == "api.open-meteo.com":
            latitude = round(float(request.url.params["latitude"]), 3)
            longitude = round(float(request.url.params["longitude"]), 3)
            self.forecast_calls.append((latitude, longitude))
            status = self.fail_paths.get("forecast")
            if status:
                return httpx.Response(status)
            payload = self.forecast.get(
                (latitude, longitude),
                {"current": {"temperature_2m": 9.44, "weather_code": 61, "is_day": 0}},
            )
            return httpx.Response(200, json=payload)

        return httpx.Response(404)


def city(
    name: str,
    *,
    admin1: str | None = "Москва",
    country: str = "Россия",
    latitude: float = 55.7522,
    longitude: float = 37.6156,
) -> dict[str, Any]:
    """Элемент `results[]` геокодирования — поля §8.1."""
    payload: dict[str, Any] = {
        "name": name,
        "country": country,
        "latitude": latitude,
        "longitude": longitude,
    }
    if admin1 is not None:
        payload["admin1"] = admin1
    return payload


def dump(value: object) -> str:
    """JSON-снимок ответа для сообщений об ошибке теста."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
