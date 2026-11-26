"""TS-DB-02: повтор и продолжение после прерывания — data/130 §7.3, §7.5.

Проверяются: загрузка с середины, отсутствие дублей, неизменность `movies.id`,
`start_page = last_page + 1`, отличие чужого запуска по `params_json`,
`Ctrl+C` (текущая порция закоммичена, `partial`) и продолжение через `--resume`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cinematch.data.api import get_catalog_size, ingest_movies
from cinematch.data.clients.tmdb import IngestError
from cinematch.data.models import CatalogLoad, Movie
from tests.data.conftest import ExternalCalls
from tests.data.external import TmdbMock, details, discover_item, pages_of


def _loads(session: Session) -> list[CatalogLoad]:
    return list(session.scalars(select(CatalogLoad).order_by(CatalogLoad.id)))


def test_ts_db_02_interrupt_keeps_committed_page_and_resumes(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """`Ctrl+C` на второй странице: `partial`, затем продолжение с `last_page + 1`."""
    mock, _calls = tmdb
    mock.page_results = pages_of(3, per_page=2)
    normal = mock.detail_for

    def failing_detail(tmdb_id: int) -> dict | None:
        if tmdb_id == 1040:
            # Первый фильм третьей страницы: две первые уже зафиксированы.
            raise KeyboardInterrupt
        return normal(tmdb_id)

    mock.detail_for = failing_detail

    with pytest.raises(IngestError) as error:
        ingest_movies(pages=3)

    assert error.value.reason == "interrupted"
    report = error.value.report
    assert report is not None
    assert report["status"] == "partial"
    assert report["last_page"] == 2
    assert report["movies_upserted"] == 4

    row = _loads(session)[0]
    assert row.status == "partial"
    assert row.finished_at is not None

    # Продолжение: та же запись, старт со второй страницы, дублей нет.
    mock.detail_for = normal
    resumed = ingest_movies(pages=3, resume=True)

    assert resumed["load_id"] == report["load_id"]
    assert resumed["status"] == "completed"
    assert resumed["last_page"] == 3
    assert resumed["movies_upserted"] == 6
    assert get_catalog_size() == 6
    assert [item.tmdb_id for item in session.scalars(select(Movie).order_by(Movie.id))] == [
        1000,
        1001,
        1020,
        1021,
        1040,
        1041,
    ]
    assert len(_loads(session)) == 1


def test_ts_db_02_resume_skips_already_loaded_pages(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """Продолжение не повторяет зафиксированные страницы: запросы начинаются со 2-й."""
    mock, _calls = tmdb
    mock.page_results = pages_of(3, per_page=2)
    normal = mock.detail_for
    mock.detail_for = lambda tmdb_id: (
        (_ for _ in ()).throw(KeyboardInterrupt()) if tmdb_id == 1020 else normal(tmdb_id)
    )

    with pytest.raises(IngestError):
        ingest_movies(pages=3)

    mock.detail_for = normal
    mock.calls.clear()
    ingest_movies(pages=3, resume=True)

    requested_pages = [
        int(request.url.params["page"])
        for request in mock.calls
        if request.url.path.endswith("/discover/movie")
    ]
    assert requested_pages == [2, 3]


def test_ts_db_02_resume_ignores_other_params(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """`--resume` с другими параметрами — новый запуск с первой страницы (§7.5, п. 2)."""
    mock, _calls = tmdb
    mock.page_results = pages_of(2, per_page=1)
    normal = mock.detail_for
    mock.detail_for = lambda tmdb_id: (
        (_ for _ in ()).throw(KeyboardInterrupt()) if tmdb_id == 1020 else normal(tmdb_id)
    )

    with pytest.raises(IngestError):
        ingest_movies(pages=2, min_votes=100)

    mock.detail_for = normal
    report = ingest_movies(pages=2, min_votes=200, resume=True)

    assert report["status"] == "completed"
    assert report["last_page"] == 2
    assert len(_loads(session)) == 2


def test_ts_db_02_idempotent_reload_keeps_user_data(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """Перезагрузка обновляет фильм, но не меняет `movies.id` (§7.3, п. 4)."""
    mock, _calls = tmdb
    mock.page_results = lambda page: [discover_item(1000, title="Старое")] if page == 1 else []
    ingest_movies(pages=1)
    movie_id = session.scalars(select(Movie.id)).one()

    mock.page_results = lambda page: [discover_item(1000, title="Новое")] if page == 1 else []
    mock.detail_for = lambda tmdb_id: details(tmdb_id, runtime=140)
    ingest_movies(pages=1)
    session.expire_all()

    movie = session.scalars(select(Movie)).one()
    assert movie.id == movie_id
    assert movie.title == "Новое"
    assert movie.runtime == 140
    assert get_catalog_size() == 1


def test_ts_db_02_partial_load_resumes_after_unavailable(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """Исчерпанные повторы дают `partial` и отчёт; повтор с `resume` завершает."""
    mock, _calls = tmdb
    mock.page_results = pages_of(2, per_page=1)
    # Пять ответов 503 на вторую страницу — повторы исчерпаны (§7.4).
    mock.page_failures[2] = [503, 503, 503, 503, 503]

    with pytest.raises(IngestError) as error:
        ingest_movies(pages=2)

    assert error.value.reason == "unavailable"
    assert error.value.report is not None
    assert error.value.report["status"] == "partial"
    assert error.value.report["last_page"] == 1

    resumed = ingest_movies(pages=2, resume=True)
    assert resumed["status"] == "completed"
    assert resumed["last_page"] == 2
    assert get_catalog_size() == 2


def test_ts_db_02_counts_skip_and_upsert_across_resume(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """Счётчики накапливаются постранично и переживают продолжение (data/120 §7)."""
    mock, _calls = tmdb
    mock.page_results = pages_of(2, per_page=2)
    normal = mock.detail_for
    mock.detail_for = lambda tmdb_id: None if tmdb_id == 1001 else normal(tmdb_id)

    ingest_movies(pages=1)
    row = _loads(session)[0]
    assert (row.movies_upserted, row.movies_skipped) == (1, 1)

    report = ingest_movies(pages=2, resume=True)
    assert report["movies_upserted"] == 3
    assert report["movies_skipped"] == 1
    assert int(session.scalar(select(func.count()).select_from(Movie)) or 0) == 3
