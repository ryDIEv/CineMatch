"""TS-DB-07: погода и геокодирование — data/130 §4.3, §4.12, §8.

Проверяются `Weather` и `None`, коды WMO (§8.3), кэш 30 минут по координатам
(3 знака), кэш геокодирования 24 часа, неоднозначный город, тайм-аут и отказ
сервиса без исключения наружу, отсутствие города без запроса (§8.4).
"""

from __future__ import annotations

import httpx
import pytest

from cinematch.data import api as data_api
from cinematch.data.api import get_weather, resolve_city, save_user_settings
from cinematch.data.clients.tmdb import IngestError
from cinematch.data.clients.weather import WeatherClient, cache_key, condition_for
from tests.data.external import WeatherMock, city


def _user(data_db) -> None:
    """Пользователь с согласием: без него `save_user_settings` — `ValueError`."""
    from cinematch.data.api import save_consent

    save_consent("web:local", "web")


def test_ts_db_07_city_resolution_returns_five_variants(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """Неоднозначный город: до пяти вариантов с регионом и страной (§4.12)."""
    mock, _client = open_meteo
    mock.geocoding["москва"] = [
        city("Москва", admin1="Москва"),
        city("Москва", admin1="Тверская область", latitude=56.8, longitude=35.9),
        city("Москва", admin1=None, country="США", latitude=46.7, longitude=-117.0),
    ]

    variants = resolve_city("Москва")

    assert [item.name for item in variants] == ["Москва", "Москва", "Москва"]
    assert [item.region for item in variants] == ["Москва", "Тверская область", None]
    assert variants[2].country == "США"
    assert variants[0].latitude == pytest.approx(55.7522)
    assert mock.geocoding_calls == ["Москва"]


def test_ts_db_07_city_not_found_is_empty_list(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """Пустой ответ геокодирования — `[]`, решение `CITY_NOT_FOUND` за оркестратором."""
    mock, _client = open_meteo
    mock.geocoding.clear()

    assert resolve_city("Атлантида") == []


def test_ts_db_07_geocoding_cache_hits_within_24h(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """Кэш геокодирования: ключ — нормализованный запрос, повторного запроса нет."""
    mock, _client = open_meteo
    mock.geocoding["казань"] = [city("Казань", admin1="Татарстан")]

    resolve_city("Казань")
    resolve_city("  КАЗАНЬ  ")

    assert mock.geocoding_calls == ["Казань"]


def test_ts_db_07_empty_geocoding_is_not_cached(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """Отрицательный кэш не ведётся: «не найдено» не запоминается (§8.4)."""
    mock, _client = open_meteo

    assert resolve_city("Атлантида") == []
    assert resolve_city("Атлантида") == []

    assert mock.geocoding_calls == ["Атлантида", "Атлантида"]


def test_ts_db_07_weather_returns_documented_fields(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """`Weather` — город из `users.city`, код WMO, температура до 1 знака, день."""
    mock, _client = open_meteo
    _user(None)
    save_user_settings("web:local", "Казань", 55.7887, 49.1221)
    mock.forecast[cache_key(55.7887, 49.1221)] = {
        "current": {"temperature_2m": 9.44, "weather_code": 61, "is_day": 0}
    }

    weather = get_weather("web:local")

    assert weather is not None
    assert weather.city == "Казань"
    assert weather.condition == "rain"
    assert weather.temp_c == pytest.approx(9.4)
    assert weather.is_day is False


def test_ts_db_07_weather_cache_30_minutes(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """Кэш погоды: повтор в пределах 30 минут не идёт в сеть (§8.4)."""
    mock, _client = open_meteo
    _user(None)
    save_user_settings("web:local", "Москва", 55.75, 37.62)

    first = get_weather("web:local")
    second = get_weather("web:local")

    assert first == second
    assert len(mock.forecast_calls) == 1


def test_ts_db_07_weather_cache_expires(open_meteo: tuple[WeatherMock, WeatherClient]) -> None:
    """По истечении 30 минут — новый запрос, отказ не запоминается (§8.4)."""
    mock, _client = open_meteo
    now = {"value": 1000.0}
    client = WeatherClient(transport=mock.transport(), clock=lambda: now["value"])
    data_api._weather_client = client
    try:
        _user(None)
        save_user_settings("web:local", "Москва", 55.75, 37.62)
        get_weather("web:local")
        now["value"] += 30 * 60
        get_weather("web:local")
    finally:
        client.close()

    assert len(mock.forecast_calls) == 2


def test_ts_db_07_no_city_means_no_request(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """`None` без запроса, если города нет (§8.4)."""
    mock, _client = open_meteo
    _user(None)

    assert get_weather("web:local") is None
    assert mock.forecast_calls == []

    # Нет и самого пользователя — тоже `None`.
    assert get_weather("web:unknown") is None
    assert mock.forecast_calls == []


def test_ts_db_07_service_outage_returns_none_without_raising(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """Отказ сервиса — `None`, а не исключение наружу: погода не ломает ход (§4.3)."""
    mock, _client = open_meteo
    _user(None)
    save_user_settings("web:local", "Москва", 55.75, 37.62)
    mock.fail_paths["forecast"] = 503

    assert get_weather("web:local") is None


def test_ts_db_07_timeout_returns_none(open_meteo: tuple[WeatherMock, WeatherClient]) -> None:
    """Тайм-аут 3 с — тоже `None`; повторов нет (§8.1)."""
    mock, _client = open_meteo
    _user(None)
    save_user_settings("web:local", "Москва", 55.75, 37.62)

    def timing_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    client = WeatherClient(transport=httpx.MockTransport(timing_out))
    data_api._weather_client = client
    try:
        assert get_weather("web:local") is None
    finally:
        client.close()

    assert mock.forecast_calls == []


def test_ts_db_07_geocoding_outage_is_empty_list(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """Отказ геокодирования — `[]`; оркестратор покажет `CITY_NOT_FOUND`."""
    mock, _client = open_meteo
    mock.fail_paths["geocoding"] = 503

    assert resolve_city("Москва") == []


@pytest.mark.parametrize(
    ("code", "condition"),
    [
        (0, "clear"),
        (1, "clear"),
        (2, "cloudy"),
        (3, "cloudy"),
        (45, "fog"),
        (48, "fog"),
        (51, "drizzle"),
        (55, "drizzle"),
        (57, "drizzle"),
        (61, "rain"),
        (65, "rain"),
        (82, "rain"),
        (71, "snow"),
        (77, "snow"),
        (86, "snow"),
        (95, "thunderstorm"),
        (99, "thunderstorm"),
        (7, "unknown"),
        (100, "unknown"),
    ],
)
def test_ts_db_07_all_wmo_codes_map_to_conditions(code: int, condition: str) -> None:
    """Все WMO-коды таблицы §8.3, прочие — `unknown`."""
    assert condition_for(code) == condition


def test_ts_db_07_weather_request_params(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """`forecast` получает `current` и `timezone=auto`; `search` — `count=5`, `language=ru`."""
    mock, _client = open_meteo
    requests: list[httpx.Request] = []
    original = mock.handle

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return original(request)

    client = WeatherClient(transport=httpx.MockTransport(recording))
    data_api._weather_client = client
    try:
        _user(None)
        save_user_settings("web:local", "Москва", 55.75, 37.62)
        get_weather("web:local")
        resolve_city("Москва")
    finally:
        client.close()

    forecast = next(r for r in requests if r.url.host == "api.open-meteo.com")
    assert forecast.url.params["current"] == "temperature_2m,weather_code,is_day"
    assert forecast.url.params["timezone"] == "auto"
    search = next(r for r in requests if r.url.host == "geocoding-api.open-meteo.com")
    assert search.url.params["count"] == "5"
    assert search.url.params["language"] == "ru"


def test_ts_db_07_unknown_wmo_code_gives_unknown(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """Неизвестный код в ответе — `condition = unknown`, а не исключение (§8.3)."""
    mock, _client = open_meteo
    _user(None)
    save_user_settings("web:local", "Москва", 55.75, 37.62)
    mock.forecast[cache_key(55.75, 37.62)] = {
        "current": {"temperature_2m": 3.0, "weather_code": 12345, "is_day": 1}
    }

    weather = get_weather("web:local")

    assert weather is not None
    assert weather.condition == "unknown"
    assert weather.is_day is True


def test_ts_db_07_invalid_weather_payload_is_none(
    open_meteo: tuple[WeatherMock, WeatherClient]
) -> None:
    """Ответ без `current.temperature_2m` — `None` и запись в журнал (§8.4)."""
    mock, _client = open_meteo
    _user(None)
    save_user_settings("web:local", "Москва", 55.75, 37.62)
    mock.forecast[cache_key(55.75, 37.62)] = {"current": {"weather_code": 61}}

    assert get_weather("web:local") is None


def test_ts_db_07_ingest_error_is_not_weather_error() -> None:
    """`IngestError` относится к загрузке; погода его не использует (§6)."""
    with pytest.raises(IngestError):
        raise IngestError("unauthorized", "тест")
