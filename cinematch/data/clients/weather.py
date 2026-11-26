"""Клиент Open-Meteo: геокодирование и погода — data/130 §8.

Запросы (§8.1): `search` отдаёт до пяти вариантов города, `forecast` — текущую
температуру, код WMO и признак дня. Ключ не нужен, тайм-аут 3 с (NFR-DB-04),
повторов нет: погода не должна задерживать ход.

Кэш живёт в памяти процесса (§8.4): погода — по координатам с точностью 3
знака на 30 минут, геокодирование — по нормализованному запросу на 24 часа,
вытеснение по давности использования. Отрицательный кэш не ведётся: отказ
сервиса не запоминается, следующий вызов пробует снова.

Отказ сервиса не превращается в ошибку: `forecast` возвращает `None`,
журнал получает `event=weather_unavailable` без координат и города.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from cinematch.overall.logging import write_event

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 3.0

# Закрытый перечень `Weather.condition` — data/130 §8.2.
Condition = Literal[
    "clear",
    "cloudy",
    "fog",
    "drizzle",
    "rain",
    "snow",
    "thunderstorm",
    "unknown",
]

# Кэш §8.4: TTL и пределы размера.
WEATHER_TTL_S = 30 * 60
WEATHER_CACHE_SIZE = 1000
GEOCODING_TTL_S = 24 * 60 * 60
GEOCODING_CACHE_SIZE = 500

# Коды WMO → `condition` — §8.3; «прочие» дают `unknown`.
WMO_CONDITIONS: dict[int, Condition] = {
    0: "clear",
    1: "clear",
    2: "cloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "drizzle",
    57: "drizzle",
    61: "rain",
    63: "rain",
    65: "rain",
    66: "rain",
    67: "rain",
    80: "rain",
    81: "rain",
    82: "rain",
    71: "snow",
    73: "snow",
    75: "snow",
    77: "snow",
    85: "snow",
    86: "snow",
    95: "thunderstorm",
    96: "thunderstorm",
    99: "thunderstorm",
}


@dataclass(frozen=True, slots=True)
class CityResult:
    """Вариант города из геокодирования: вход `CityVariant` (data/130 §4.12)."""

    name: str
    region: str | None
    country: str
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class WeatherNow:
    """Погода по координатам до подстановки города: `Weather` собирает код."""

    condition: Condition
    temp_c: float
    is_day: bool


class _TtlCache[V]:
    """Кэш в памяти с TTL и вытеснением по давности использования (§8.4)."""

    def __init__(self, size: int, ttl_s: float, clock: Callable[[], float]) -> None:
        self._size = size
        self._ttl_s = ttl_s
        self._clock = clock
        self._values: OrderedDict[object, tuple[float, V]] = OrderedDict()

    def get(self, key: object) -> V | None:
        entry = self._values.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if self._clock() - stored_at >= self._ttl_s:
            del self._values[key]
            return None
        # Обращение делает запись свежайшей: вытесняются давно не нужные.
        self._values.move_to_end(key)
        return value

    def set(self, key: object, value: V) -> None:
        self._values[key] = (self._clock(), value)
        self._values.move_to_end(key)
        while len(self._values) > self._size:
            self._values.popitem(last=False)

    def clear(self) -> None:
        self._values.clear()


class WeatherClient:
    """Клиент Open-Meteo с кэшем; сеть подменяется транспортом в тестах."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._weather_cache: _TtlCache[WeatherNow] = _TtlCache(
            WEATHER_CACHE_SIZE, WEATHER_TTL_S, clock
        )
        self._geocoding_cache: _TtlCache[tuple[CityResult, ...]] = _TtlCache(
            GEOCODING_CACHE_SIZE, GEOCODING_TTL_S, clock
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> WeatherClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def reset_cache(self) -> None:
        """Очистить кэш — нужен тестам и перезапуску в одном процессе."""
        self._weather_cache.clear()
        self._geocoding_cache.clear()

    def search_city(self, query: str) -> list[CityResult]:
        """До пяти вариантов города; пустой список — не найдено (§8.1).

        Ключ кэша — нормализованный запрос (§8.4). Сетевой отказ тоже даёт
        пустой список: `resolve_city` не бросает исключение наружу.
        """
        key = normalize_query(query)
        cached = self._geocoding_cache.get(key)
        if cached is not None:
            return list(cached)

        try:
            response = self._client.get(
                GEOCODING_URL,
                params={
                    "name": query,
                    "count": 5,
                    "language": "ru",
                    "format": "json",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        if not isinstance(payload, Mapping):
            return []

        results: list[CityResult] = []
        for raw in list(payload.get("results") or [])[:5]:
            if not isinstance(raw, Mapping):
                continue
            city = normalize_city(raw)
            if city is not None:
                results.append(city)
        if results:
            # Пустой ответ не запоминается: «не найдено» — не отказ, но и
            # отрицательный кэш §8.4 не ведётся, следующий вызов пробует снова.
            self._geocoding_cache.set(key, tuple(results))
        return results

    def forecast(self, latitude: float, longitude: float) -> WeatherNow | None:
        """Текущая погода или `None` при отказе сервиса (§8.4)."""
        key = cache_key(latitude, longitude)
        cached = self._weather_cache.get(key)
        if cached is not None:
            return cached

        try:
            response = self._client.get(
                FORECAST_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,weather_code,is_day",
                    "timezone": "auto",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            # §8.4: запись без координат и города; отказ не запоминается.
            write_event("weather_unavailable")
            return None

        weather = normalize_weather(payload) if isinstance(payload, Mapping) else None
        if weather is None:
            write_event("weather_unavailable")
            return None
        self._weather_cache.set(key, weather)
        return weather


def normalize_query(query: str) -> str:
    """Ключ кэша геокодирования: `lower`, сжатые пробелы, обрезка (§8.4)."""
    return " ".join(query.lower().split())


def cache_key(latitude: float, longitude: float) -> tuple[float, float]:
    """Ключ кэша погоды: координаты, округлённые до 3 знаков (§8.4)."""
    return round(latitude, 3), round(longitude, 3)


def condition_for(weather_code: object) -> Condition:
    """Код WMO → `condition`; неизвестный или нечисловой код — `unknown` (§8.3)."""
    if isinstance(weather_code, bool) or not isinstance(weather_code, int):
        return "unknown"
    return WMO_CONDITIONS.get(weather_code, "unknown")


def normalize_city(raw: Mapping[str, Any]) -> CityResult | None:
    """Вариант города из `results[]`: нужны название, страна и координаты."""
    name = _text(raw.get("name"))
    country = _text(raw.get("country"))
    latitude = _number(raw.get("latitude"))
    longitude = _number(raw.get("longitude"))
    if name is None or country is None or latitude is None or longitude is None:
        return None
    return CityResult(
        name=name,
        region=_text(raw.get("admin1")),
        country=country,
        latitude=latitude,
        longitude=longitude,
    )


def normalize_weather(payload: Mapping[str, Any]) -> WeatherNow | None:
    """`current` ответа forecast → `WeatherNow`; нет температуры — `None`."""
    current = payload.get("current")
    if not isinstance(current, Mapping):
        return None
    temperature = _number(current.get("temperature_2m"))
    if temperature is None:
        return None
    return WeatherNow(
        condition=condition_for(current.get("weather_code")),
        temp_c=round(temperature, 1),
        is_day=current.get("is_day") == 1,
    )


def _text(value: object) -> str | None:
    """Непустая обрезанная строка или `None`."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _number(value: object) -> float | None:
    """Число из ответа Open-Meteo; `bool` и строки — `None`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
