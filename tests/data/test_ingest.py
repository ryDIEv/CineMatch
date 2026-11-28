"""TS-DB-01: загрузка пяти страниц — data/130 §7, data/146 §2, UC-9.

Мок TMDB отвечает пятью страницами; проверяются отчёт, `catalog_loads`,
нормализация §7.2 (title, year, runtime, rating, age_rating, жанры, актёры),
идемпотентность по `tmdb_id` и отсутствие сети.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cinematch.data.api import get_catalog_size, get_genres, ingest_movies
from cinematch.data.models import CatalogLoad, Genre, Movie, MovieCast, MovieGenre, Person
from tests.data.conftest import ExternalCalls
from tests.data.external import TmdbMock, details, discover_item, pages_of


def _count(session: Session, model: object) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_ts_db_01_loads_five_pages_and_reports(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """5 страниц × 2 фильма: `completed`, счётчики, время, размер базы."""
    mock, _calls = tmdb
    mock.page_results = pages_of(5, per_page=2)

    report = ingest_movies(pages=5)

    assert report["status"] == "completed"
    assert report["pages_done"] == 5
    assert report["last_page"] == 5
    assert report["movies_upserted"] == 10
    assert report["movies_skipped"] == 0
    assert isinstance(report["duration_s"], float)
    assert get_catalog_size() == 10

    row = session.scalars(select(CatalogLoad)).one()
    assert row.id == report["load_id"]
    assert row.status == "completed"
    assert row.finished_at is not None
    assert row.last_page == 5
    assert (row.movies_upserted, row.movies_skipped) == (10, 0)


def test_ts_db_01_normalizes_fields_and_relations(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """Нормализация §7.2: заголовок, год, длительность, рейтинг, маркировка, связи."""
    mock, _calls = tmdb
    mock.page_results = lambda page: [
        discover_item(
            501,
            title="",
            original_title="Original Title",
            release_date="2014-05-17",
            vote_average=7.84,
            vote_count=321,
        ),
        discover_item(502, title="Второй", release_date="", vote_average=0.0, vote_count=0),
    ]
    mock.detail_for = lambda tmdb_id: details(
        tmdb_id,
        runtime=0 if tmdb_id == 502 else 131,
        overview="" if tmdb_id == 502 else "Описание",
        cast=[{"id": 77, "name": "Пётр Первый", "character": "Он"}],
    )

    ingest_movies(pages=1)

    first = session.scalars(select(Movie).where(Movie.tmdb_id == 501)).one()
    assert first.title == "Original Title"
    assert first.title_norm == "original title"
    assert first.original_title == "Original Title"
    assert first.year == 2014
    assert first.runtime == 131
    assert first.rating == pytest.approx(7.84)
    assert first.vote_count == 321
    assert first.age_rating == "12+"
    assert first.overview == "Описание"

    second = session.scalars(select(Movie).where(Movie.tmdb_id == 502)).one()
    assert second.title == "Второй"
    assert second.year is None
    # `runtime = 0` и `vote_average` без голосов — NULL (§7.2).
    assert second.runtime is None
    assert second.rating is None
    # Пустой `overview` — NULL.
    assert second.overview is None

    assert _count(session, Genre) == 4
    # Порядок — по возрастанию `genres.tmdb_id` (18, 27, 28, 35).
    assert get_genres() == ["Драма", "Ужасы", "Боевик", "Комедия"]
    assert _count(session, MovieGenre) == 4
    assert _count(session, Person) == 1
    assert _count(session, MovieCast) == 2


def test_ts_db_01_genres_without_duplicates(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """Один человек в `cast` дважды — остаётся первое вхождение (data/120 §4)."""
    mock, _calls = tmdb
    mock.page_results = lambda page: [discover_item(601, title="Фильм")] if page == 1 else []
    mock.detail_for = lambda tmdb_id: details(
        tmdb_id,
        cast=[
            {"id": 1, "name": "Первый", "character": "A"},
            {"id": 1, "name": "Первый", "character": "B"},
            {"id": 2, "name": "Второй", "character": None},
        ],
    )

    ingest_movies(pages=1)

    casts = session.scalars(select(MovieCast).order_by(MovieCast.billing_order)).all()
    # `billing_order` — индекс в исходном массиве: у второго человека он 2,
    # потому что первое место занял повтор первого (data/120 §4).
    assert [row.billing_order for row in casts] == [0, 2]
    assert casts[0].character == "A"


def test_ts_db_01_skips_detailed_404_and_counts(
    tmdb: tuple[TmdbMock, ExternalCalls]
) -> None:
    """404 на детали — `skipped`, цикл продолжается (§7.4)."""
    mock, _calls = tmdb
    mock.page_results = lambda page: (
        [discover_item(701, title="Есть"), discover_item(702, title="Нет")] if page == 1 else []
    )
    mock.detail_for = lambda tmdb_id: None if tmdb_id == 702 else details(tmdb_id)

    report = ingest_movies(pages=1)

    assert report["status"] == "completed"
    assert report["movies_upserted"] == 1
    assert report["movies_skipped"] == 1
    assert get_catalog_size() == 1


def test_ts_db_01_stops_on_empty_page(tmdb: tuple[TmdbMock, ExternalCalls]) -> None:
    """Пустая страница завершает загрузку успехом, даже если `pages` больше (§7.5)."""
    mock, _calls = tmdb
    mock.page_results = lambda page: [discover_item(801, title="Один")] if page == 1 else []

    report = ingest_movies(pages=50)

    assert report["status"] == "completed"
    assert report["last_page"] == 1
    assert report["pages_done"] == 1


def test_ts_db_01_reload_updates_rows_and_keeps_ids(
    tmdb: tuple[TmdbMock, ExternalCalls], session: Session
) -> None:
    """Повторная загрузка не создаёт дублей: `tmdb_id` — ключ идемпотентности."""
    mock, _calls = tmdb
    mock.page_results = pages_of(1, per_page=2)
    ingest_movies(pages=1)
    first_ids = session.scalars(select(Movie.id).order_by(Movie.id)).all()

    ingest_movies(pages=1)
    session.expire_all()
    second_ids = session.scalars(select(Movie.id).order_by(Movie.id)).all()

    assert first_ids == second_ids
    assert get_catalog_size() == 2


def test_ts_db_01_request_params_follow_section_7_1(
    tmdb: tuple[TmdbMock, ExternalCalls]
) -> None:
    """`discover` получает `sort_by`, порог голосов, границу года и `include_adult`."""
    mock, _calls = tmdb
    mock.page_results = pages_of(1, per_page=1)

    ingest_movies(pages=1, from_year=1999, min_votes=250)

    discover = next(r for r in mock.calls if r.url.path.endswith("/discover/movie"))
    params = discover.url.params
    assert params["language"] == "ru-RU"
    assert params["sort_by"] == "popularity.desc"
    assert params["vote_count.gte"] == "250"
    assert params["primary_release_date.gte"] == "1999-01-01"
    assert params["include_adult"] == "false"
    assert params["api_key"] == "test-key"
    details_call = next(r for r in mock.calls if r.url.path.startswith("/3/movie/"))
    assert details_call.url.params["append_to_response"] == "credits,release_dates"
    assert details_call.url.params["language"] == "ru-RU"
