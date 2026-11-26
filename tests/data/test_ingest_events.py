"""Журнал загрузки каталога — data/140 §1.

Три события: `CatalogLoadStarted` на старте, `MoviesUpserted` на каждую
закоммиченную страницу, `CatalogLoadFinished` в конце. `user_id = null`:
загрузка не относится к одному пользователю. В `payload` только счётчики и
идентификаторы — без названий фильмов и ключа (NFR-DB-10).
"""

from __future__ import annotations

import json

import pytest

from cinematch.data.api import ingest_movies
from tests.data.conftest import ExternalCalls
from tests.data.external import TmdbMock, pages_of


def _records(captured: str) -> list[dict[str, object]]:
    """Записи журнала среди stdout: отчёт и прогресс отбрасываются."""
    records = []
    for line in captured.splitlines():
        if not line.startswith("{"):
            continue
        records.append(json.loads(line))
    return records


def _of(records: list[dict[str, object]], event: str) -> list[dict[str, object]]:
    return [record for record in records if record["event"] == event]


def test_ts_db_01_load_writes_documented_events(
    tmdb: tuple[TmdbMock, ExternalCalls], capsys: pytest.CaptureFixture[str]
) -> None:
    """Старт, две страницы, финиш — по одной записи на каждое событие (§1)."""
    mock, _calls = tmdb
    mock.page_results = pages_of(2, per_page=2)
    capsys.readouterr()

    report = ingest_movies(pages=2)

    records = _records(capsys.readouterr().out)
    started = _of(records, "CatalogLoadStarted")
    pages = _of(records, "MoviesUpserted")
    finished = _of(records, "CatalogLoadFinished")

    assert len(started) == 1
    assert started[0]["payload"] == {
        "load_id": report["load_id"],
        "pages": 2,
        "from_year": 1970,
        "min_votes": 100,
        "resumed": False,
    }
    assert [item["payload"]["page"] for item in pages] == [1, 2]
    assert [item["payload"]["movies"] for item in pages] == [2, 2]
    assert [item["payload"]["inserted"] for item in pages] == [2, 2]
    assert [item["payload"]["updated"] for item in pages] == [0, 0]

    assert len(finished) == 1
    assert finished[0]["payload"] == {
        "load_id": report["load_id"],
        "status": "completed",
        "pages_done": 2,
        "movies_upserted": 4,
        "movies_skipped": 0,
        "last_page": 2,
        "duration_s": report["duration_s"],
    }


def test_ts_db_01_load_events_have_no_user_and_no_titles(
    tmdb: tuple[TmdbMock, ExternalCalls], capsys: pytest.CaptureFixture[str]
) -> None:
    """`user_id = null`, названий фильмов и ключа в записях нет (§2, п. 2 и 4)."""
    mock, _calls = tmdb
    mock.page_results = pages_of(1, per_page=1)
    capsys.readouterr()

    ingest_movies(pages=1)

    output = capsys.readouterr().out
    for record in _records(output):
        if record["event"] == "MoviesUpserted":
            assert record["user_id"] is None
    assert '"user_id": null' in output
    assert "Фильм 1000" not in output
    assert "test-key" not in output


def test_ts_db_01_partial_load_still_writes_finished(
    tmdb: tuple[TmdbMock, ExternalCalls], capsys: pytest.CaptureFixture[str]
) -> None:
    """Отказ на второй странице — `CatalogLoadFinished` со `status = partial`."""
    from cinematch.data.clients.tmdb import IngestError

    mock, _calls = tmdb
    mock.page_results = pages_of(2, per_page=1)
    mock.page_failures[2] = [500, 500, 500, 500, 500]
    capsys.readouterr()

    with pytest.raises(IngestError):
        ingest_movies(pages=2)

    records = _records(capsys.readouterr().out)
    finished = _of(records, "CatalogLoadFinished")
    assert len(finished) == 1
    assert finished[0]["payload"]["status"] == "partial"
    assert finished[0]["payload"]["last_page"] == 1
    # Неудачная страница не дала `MoviesUpserted`: порция не зафиксирована (§2, п. 3).
    assert [item["payload"]["page"] for item in _of(records, "MoviesUpserted")] == [1]
