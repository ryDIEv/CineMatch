"""TS-DB-03: ошибки TMDB и повторы — data/130 §7.4, data/146 §2, data/110 §2.

Сценарий: 401 — `failed` и `IngestError` (exit code 2); 429 — пауза из
`Retry-After` и повтор; 404 на детали — `skipped`; пять 5xx — `partial` и
`IngestError` (exit code 4). Отдельно проверяются темп 40 запросов/с и ряд
пауз 1, 2, 4, 8 с.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cinematch.data.api import get_catalog_size, ingest_movies
from cinematch.data.clients.tmdb import BACKOFF_S, MIN_INTERVAL_S, IngestError
from cinematch.data.models import CatalogLoad
from tests.data.conftest import ExternalCalls
from tests.data.external import TmdbMock, details, discover_item, pages_of


def backoff(sleeps: list[float]) -> list[float]:
    """Только паузы повторов: паузы темпа (≤ 25 мс) отбрасываются (§7.4)."""
    return [value for value in sleeps if value >= BACKOFF_S[0]]


def test_ts_db_03_unauthorized_stops_with_failed(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """401 — остановка, `status = failed`, `IngestError` (exit code 2)."""
    mock, _calls = tmdb
    mock.page_results = pages_of(2, per_page=1)
    mock.fail["/genre/movie/list"] = [401]

    with pytest.raises(IngestError) as error:
        ingest_movies(pages=2)

    assert error.value.reason == "unauthorized"
    assert error.value.report is not None
    assert error.value.report["status"] == "failed"

    row = session.scalars(select(CatalogLoad)).one()
    assert row.status == "failed"
    assert row.finished_at is not None
    # Повторов нет: 401 — отказ ключа, а не временная ошибка.
    assert len([call for call in mock.calls if call.url.path.endswith("/genre/movie/list")]) == 1


def test_ts_db_03_rate_limit_waits_retry_after(
    tmdb: tuple[TmdbMock, ExternalCalls]
) -> None:
    """429 с `Retry-After` — пауза из заголовка и повтор того же запроса."""
    mock, calls = tmdb
    mock.page_results = pages_of(1, per_page=1)
    mock.retry_after = "7"
    mock.fail["/genre/movie/list"] = [429]

    report = ingest_movies(pages=1)

    assert report["status"] == "completed"
    # Пауза 7 с взята из заголовка, а не из ряда повторов.
    assert calls.sleeps[:1] == [7.0]
    genre_calls = [call for call in mock.calls if call.url.path.endswith("/genre/movie/list")]
    assert len(genre_calls) == 2


def test_ts_db_03_rate_limit_without_header_uses_backoff(
    tmdb: tuple[TmdbMock, ExternalCalls]
) -> None:
    """429 без `Retry-After` — пауза из ряда 1, 2, 4, 8, 16 с (§7.4)."""
    mock, calls = tmdb
    mock.page_results = pages_of(1, per_page=1)
    mock.fail["/genre/movie/list"] = [429, 429]

    report = ingest_movies(pages=1)

    assert report["status"] == "completed"
    # Паузы темпа ≤ 25 мс, паузы повторов — от 1 с: ряд повторов видел две паузы.
    assert backoff(calls.sleeps)[:2] == [BACKOFF_S[0], BACKOFF_S[1]]


def test_ts_db_03_server_errors_use_backoff_series(
    tmdb: tuple[TmdbMock, ExternalCalls]
) -> None:
    """5xx — тот же ряд пауз; четыре отказа не исчерпывают пять попыток."""
    mock, calls = tmdb
    mock.page_results = pages_of(1, per_page=1)
    mock.fail["/genre/movie/list"] = [500, 502, 503, 504]

    report = ingest_movies(pages=1)

    assert report["status"] == "completed"
    assert backoff(calls.sleeps)[:4] == [
        BACKOFF_S[0],
        BACKOFF_S[1],
        BACKOFF_S[2],
        BACKOFF_S[3],
    ]


def test_ts_db_03_five_server_errors_stop_with_partial(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """После пяти неудач — `partial`, `IngestError` (exit code 4), отчёт с страницей."""
    mock, calls = tmdb
    mock.page_results = pages_of(2, per_page=1)
    mock.page_failures[2] = [500, 500, 500, 500, 500]

    with pytest.raises(IngestError) as error:
        ingest_movies(pages=2)

    assert error.value.reason == "unavailable"
    assert error.value.report is not None
    assert error.value.report["status"] == "partial"
    assert error.value.report["last_page"] == 1

    row = session.scalars(select(CatalogLoad)).one()
    assert row.status == "partial"
    # Пять попыток, четыре паузы между ними (после пятой — остановка).
    assert len([value for value in calls.sleeps if value in BACKOFF_S]) == 4


def test_ts_db_03_not_found_on_details_skips_film(
    tmdb: tuple[TmdbMock, ExternalCalls]
) -> None:
    """404 на детали — фильм пропускается, счётчик `skipped` (§7.4)."""
    mock, _calls = tmdb
    mock.page_results = lambda page: (
        [discover_item(301, title="Есть"), discover_item(302, title="Удалён")] if page == 1 else []
    )
    mock.detail_for = lambda tmdb_id: None if tmdb_id == 302 else details(tmdb_id)

    report = ingest_movies(pages=1)

    assert report["status"] == "completed"
    assert report["movies_skipped"] == 1
    assert report["movies_upserted"] == 1
    assert get_catalog_size() == 1


def test_ts_db_03_request_pace_is_at_least_25ms(
    tmdb: tuple[TmdbMock, ExternalCalls]
) -> None:
    """Темп ≤ 40 запросов/с: пауза между запросами не меньше 25 мс (§7.4)."""
    mock, calls = tmdb
    mock.page_results = pages_of(1, per_page=3)

    ingest_movies(pages=1)

    # Справочник, страница и три детали — четыре паузы после первого запроса.
    assert len(calls.sleeps) >= 4
    assert all(0 <= value <= MIN_INTERVAL_S for value in calls.sleeps)


def test_ts_db_03_timeout_is_retried(
    tmdb: tuple[TmdbMock, ExternalCalls]
) -> None:
    """Тайм-аут запроса — тот же ряд пауз, до пяти повторов (§7.4)."""
    import httpx

    mock, calls = tmdb
    mock.page_results = pages_of(1, per_page=1)
    original = mock.handle
    attempts = {"value": 0}

    def timing_out(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/genre/movie/list") and attempts["value"] < 2:
            attempts["value"] += 1
            raise httpx.ReadTimeout("timeout", request=request)
        return original(request)

    from cinematch.data import api as data_api

    original_transport = data_api.TMDB_TRANSPORT
    data_api.TMDB_TRANSPORT = httpx.MockTransport(timing_out)
    try:
        report = ingest_movies(pages=1)
    finally:
        data_api.TMDB_TRANSPORT = original_transport

    assert report["status"] == "completed"
    assert backoff(calls.sleeps)[:2] == [BACKOFF_S[0], BACKOFF_S[1]]
