"""Скрипт загрузки: аргументы, отчёт и коды завершения — data/110.

Логики загрузки в скрипте нет: проверяются разбор аргументов, строка отчёта
и соответствие кода завершения причине (§1–§2). Сеть подменяется моком TMDB.
"""

from __future__ import annotations

import pytest

from cinematch.data.clients.tmdb import IngestError
from scripts.load_catalog import (
    EXIT_DATABASE,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_UNAUTHORIZED,
    EXIT_UNAVAILABLE,
    UNAUTHORIZED_TEXT,
    UNAVAILABLE_TEXT,
    build_parser,
    format_report,
    main,
)
from tests.data.conftest import ExternalCalls
from tests.data.external import TmdbMock, pages_of


def _report_line(captured: str) -> str:
    """Строка отчёта среди вывода: журнал пишет свои JSON-строки рядом."""
    for line in captured.splitlines():
        if line.startswith("load_id="):
            return line
    raise AssertionError(f"строки отчёта нет в выводе: {captured!r}")


def test_defaults_follow_data_110() -> None:
    """Умолчания совпадают с ADR-041: 500 страниц, 1970 год, 100 голосов."""
    args = build_parser().parse_args([])

    assert (args.pages, args.from_year, args.min_votes, args.resume) == (500, 1970, 100, False)


def test_flags_are_parsed() -> None:
    """Все четыре параметра командной строки принимаются."""
    args = build_parser().parse_args(
        ["--pages", "5", "--from-year", "2000", "--min-votes", "500", "--resume"]
    )

    assert (args.pages, args.from_year, args.min_votes, args.resume) == (5, 2000, 500, True)


@pytest.mark.parametrize(
    ("duration_s", "expected"),
    [
        (0.0, "0s"),
        (41.0, "41s"),
        (412.8, "6m52s"),
        (3725.0, "1h02m05s"),
    ],
)
def test_report_duration_format(duration_s: float, expected: str) -> None:
    """Длительность печатается как в примере data/110 §1."""
    line = format_report(
        {
            "load_id": 3,
            "status": "completed",
            "pages_done": 500,
            "movies_upserted": 9987,
            "movies_skipped": 13,
            "last_page": 500,
            "duration_s": duration_s,
        }
    )

    assert line == (
        "load_id=3 status=completed pages=500 films_upserted=9987 "
        f"films_skipped=13 duration={expected}"
    )


def test_success_prints_report_and_returns_zero(
    tmdb: tuple[TmdbMock, ExternalCalls], capsys: pytest.CaptureFixture[str]
) -> None:
    """Успех — exit code 0 и отчёт с `status=completed` (data/110 §2)."""
    mock, _calls = tmdb
    mock.page_results = pages_of(2, per_page=2)

    assert main(["--pages", "2"]) == EXIT_OK

    line = _report_line(capsys.readouterr().out)
    assert "status=completed" in line
    assert "pages=2" in line
    assert "films_upserted=4" in line
    assert "films_skipped=0" in line


def test_missing_key_returns_two(
    tmdb: tuple[TmdbMock, ExternalCalls],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нет ключа — exit code 2 и дословный текст data/110 §2."""
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.setattr("cinematch.data.api._parse_env_file", lambda _path: {})

    assert main(["--pages", "1"]) == EXIT_UNAUTHORIZED

    assert UNAUTHORIZED_TEXT in capsys.readouterr().err


def test_unauthorized_response_returns_two(
    tmdb: tuple[TmdbMock, ExternalCalls], capsys: pytest.CaptureFixture[str]
) -> None:
    """401 от TMDB — тот же код 2; отчёт напечатан со `status=failed`."""
    mock, _calls = tmdb
    mock.page_results = pages_of(1, per_page=1)
    mock.fail["/genre/movie/list"] = [401]

    assert main(["--pages", "1"]) == EXIT_UNAUTHORIZED

    captured = capsys.readouterr()
    assert UNAUTHORIZED_TEXT in captured.err
    assert "status=failed" in _report_line(captured.out)


def test_unavailable_returns_four(
    tmdb: tuple[TmdbMock, ExternalCalls], capsys: pytest.CaptureFixture[str]
) -> None:
    """Пять неудач подряд — exit code 4 и совет про `--resume`."""
    mock, _calls = tmdb
    mock.page_results = pages_of(2, per_page=1)
    mock.page_failures[2] = [500, 500, 500, 500, 500]

    assert main(["--pages", "2"]) == EXIT_UNAVAILABLE

    captured = capsys.readouterr()
    assert UNAVAILABLE_TEXT in captured.err
    assert "status=partial" in _report_line(captured.out)


def test_interrupt_returns_one(
    tmdb: tuple[TmdbMock, ExternalCalls], capsys: pytest.CaptureFixture[str]
) -> None:
    """`Ctrl+C` — exit code 1, номер страницы и совет `--resume`."""
    mock, _calls = tmdb
    mock.page_results = pages_of(2, per_page=1)
    normal = mock.detail_for

    def interrupted(tmdb_id: int) -> dict | None:
        if tmdb_id == 1020:
            raise KeyboardInterrupt
        return normal(tmdb_id)

    mock.detail_for = interrupted

    assert main(["--pages", "2"]) == EXIT_PARTIAL

    captured = capsys.readouterr()
    assert "Загрузка прервана на странице 1. Запустите с --resume." in captured.err
    assert "status=partial" in _report_line(captured.out)


def test_database_error_returns_three(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ошибка базы — exit code 3 и текст «Не удалось записать каталог: ….»."""
    from cinematch.overall.errors import ServiceError

    def failing(*_args: object, **_kwargs: object) -> int:
        raise ServiceError("DATA_UNAVAILABLE")

    monkeypatch.setattr("scripts.load_catalog.init_database", failing)

    assert main(["--pages", "1"]) == EXIT_DATABASE

    assert "Не удалось записать каталог: База данных недоступна. Попробуйте позже." in (
        capsys.readouterr().err
    )


def test_progress_line_goes_to_stderr(
    tmdb: tuple[TmdbMock, ExternalCalls], capsys: pytest.CaptureFixture[str]
) -> None:
    """Строка прогресса — stderr без переносов, отчёт — stdout (data/110 §1)."""
    mock, _calls = tmdb
    mock.page_results = pages_of(2, per_page=2)

    assert main(["--pages", "2"]) == EXIT_OK

    captured = capsys.readouterr()
    assert "page 1/2  films 2  skipped 0  elapsed " in captured.err
    assert "page 2/2  films 4  skipped 0  elapsed " in captured.err
    # Отчёт — в stdout последней строкой; журнал пишет туда же свои записи.
    assert captured.err.endswith("\n")
    assert "load_id=1 status=completed" in _report_line(captured.out)


def test_bad_pages_argument_exits_with_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Значение вне границ — ответ argparse и код 2, загрузка не начинается."""
    with pytest.raises(SystemExit) as error:
        main(["--pages", "0"])

    assert error.value.code == 2
    assert "--pages" in capsys.readouterr().err


def test_ingest_error_without_report_still_exits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`IngestError` без отчёта не роняет скрипт: код причины сохраняется."""

    def failing(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise IngestError("unauthorized", "тест")

    monkeypatch.setattr("scripts.load_catalog.ingest_movies", failing)

    assert main(["--pages", "1"]) == EXIT_UNAUTHORIZED
    assert UNAUTHORIZED_TEXT in capsys.readouterr().err
