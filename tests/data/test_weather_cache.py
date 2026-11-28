"""Кэш Open-Meteo — data/130 §8.4.

Кэш живёт в процессе: погода — по координатам до 3 знаков на 30 минут,
геокодирование — по нормализованному запросу на 24 часа. Пределы — 1 000 и
500 записей; вытесняются давно использованные. Отрицательного кэша нет.
"""

from __future__ import annotations

import httpx
import pytest

from cinematch.data.clients.weather import (
    GEOCODING_CACHE_SIZE,
    GEOCODING_TTL_S,
    WEATHER_CACHE_SIZE,
    WEATHER_TTL_S,
    WeatherClient,
    cache_key,
    normalize_query,
)
from tests.data.external import WeatherMock, city


class Clock:
    """Управляемые часы: TTL проверяется без ожидания."""

    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


def test_ts_db_07_cache_limits_follow_document() -> None:
    """TTL и размеры кэшей — числа из таблицы §8.4."""
    assert WEATHER_TTL_S == 30 * 60
    assert WEATHER_CACHE_SIZE == 1000
    assert GEOCODING_TTL_S == 24 * 60 * 60
    assert GEOCODING_CACHE_SIZE == 500


def test_ts_db_07_coordinates_are_rounded_to_three_digits() -> None:
    """Ключ погоды — координаты до 3 знаков: 55.7501 и 55.7504 — один ключ."""
    assert cache_key(55.7501, 37.6156) == cache_key(55.7504, 37.6156)
    assert cache_key(55.7501, 37.6156) != cache_key(55.7611, 37.6156)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Москва", "москва"),
        ("  Москва  ", "москва"),
        ("Нижний   Новгород", "нижний новгород"),
        ("MADRID", "madrid"),
    ],
)
def test_ts_db_07_normalized_query_is_cache_key(query: str, expected: str) -> None:
    """Ключ геокодирования: `lower`, сжатые пробелы, обрезка (§8.4)."""
    assert normalize_query(query) == expected


def test_ts_db_07_weather_cache_expires_exactly_at_ttl() -> None:
    """Через 30 минут — новый запрос; на секунду раньше — кэш (§8.4)."""
    mock = WeatherMock()
    clock = Clock()
    client = WeatherClient(transport=mock.transport(), clock=clock)
    try:
        client.forecast(55.75, 37.62)
        clock.value += WEATHER_TTL_S - 1
        client.forecast(55.75, 37.62)
        assert len(mock.forecast_calls) == 1

        clock.value += 1
        client.forecast(55.75, 37.62)
        assert len(mock.forecast_calls) == 2
    finally:
        client.close()


def test_ts_db_07_geocoding_cache_expires_after_24_hours() -> None:
    """Кэш городов живёт 24 часа, затем запрос повторяется (§8.4)."""
    mock = WeatherMock()
    mock.geocoding["казань"] = [city("Казань")]
    clock = Clock()
    client = WeatherClient(transport=mock.transport(), clock=clock)
    try:
        client.search_city("Казань")
        clock.value += GEOCODING_TTL_S - 1
        client.search_city("Казань")
        assert mock.geocoding_calls == ["Казань"]

        clock.value += 1
        client.search_city("Казань")
        assert mock.geocoding_calls == ["Казань", "Казань"]
    finally:
        client.close()


def test_ts_db_07_cache_evicts_least_recently_used() -> None:
    """При переполнении вытесняется давно использованная запись (§8.4).

    Размер взят маленьким: проверяется правило, а не тысяча записей.
    """
    from cinematch.data.clients.weather import _TtlCache

    cache: _TtlCache[str] = _TtlCache(size=2, ttl_s=60, clock=lambda: 0.0)
    cache.set("a", "1")
    cache.set("b", "2")
    cache.get("a")  # `a` становится свежей, значит вытеснится `b`
    cache.set("c", "3")

    assert cache.get("a") == "1"
    assert cache.get("b") is None
    assert cache.get("c") == "3"


def test_ts_db_07_outage_is_not_cached() -> None:
    """Отказ не запоминается: следующий вызов снова идёт в сеть (§8.4)."""
    state = {"fail": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"current": {"temperature_2m": 5.0, "weather_code": 0, "is_day": 1}},
        )

    client = WeatherClient(transport=httpx.MockTransport(handler))
    try:
        assert client.forecast(55.75, 37.62) is None
        state["fail"] = False
        weather = client.forecast(55.75, 37.62)
    finally:
        client.close()

    assert weather is not None
    assert weather.condition == "clear"


def test_ts_db_07_geocoding_failure_is_not_cached() -> None:
    """Отказ геокодирования тоже не кэшируется: следующий вызов пробует снова."""
    attempts = {"value": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["value"] += 1
        if attempts["value"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"results": [city("Казань")]})

    client = WeatherClient(transport=httpx.MockTransport(handler))
    try:
        assert client.search_city("Казань") == []
        assert [item.name for item in client.search_city("Казань")] == ["Казань"]
    finally:
        client.close()

    assert attempts["value"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "не объект",
        {"results": "строка"},
        {"results": [None, 1, "город"]},
    ],
)
def test_ts_db_07_unexpected_geocoding_payload_gives_empty_list(
    payload: object,
) -> None:
    """Неожидаемый JSON геокодирования — `[]`, а не исключение (§4.12)."""
    client = WeatherClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    try:
        assert client.search_city("Москва") == []
    finally:
        client.close()
