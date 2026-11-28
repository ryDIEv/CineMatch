"""Загрузка каталога CineMatch из TMDB — data/110, UC-9 (data/060).

Тонкая обёртка (data/150 §1, п. 7): разбор аргументов, вызов `ingest_movies`,
печать отчёта и код завершения. Логики загрузки здесь нет.

Запуск:

    python -m scripts.load_catalog [--pages N] [--from-year Y] [--min-votes V] [--resume]

Коды завершения — data/110 §2:

| Код | Когда |
|---|---|
| 0 | `completed` |
| 1 | `partial`: `Ctrl+C` |
| 2 | нет `TMDB_API_KEY`, ответ 401 |
| 3 | ошибка базы |
| 4 | `partial`: TMDB недоступен после 5 повторов |
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from cinematch.data.api import ingest_movies
from cinematch.data.clients.tmdb import IngestError
from cinematch.data.db import init_database
from cinematch.overall.errors import ServiceError

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_UNAUTHORIZED = 2
EXIT_DATABASE = 3
EXIT_UNAVAILABLE = 4

UNAUTHORIZED_TEXT = "Ключ TMDB не принят (401). Проверьте TMDB_API_KEY."
UNAVAILABLE_TEXT = "TMDB не отвечает. Повторите позже или запустите с --resume."


def _bounded_int(minimum: int, maximum: int) -> Callable[[str], int]:
    """Тип аргумента с границами: ошибка формата — ответ argparse (data/110).

    Границы те же, что проверяет `ingest_movies` (data/130 §4.19); здесь они
    отсекаются до открытия базы, чтобы опечатка не начинала загрузку.
    """

    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(f"ожидается целое число: {raw!r}") from None
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(f"допустимо от {minimum} до {maximum}: {value}")
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    """Аргументы командной строки — таблица data/110."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.load_catalog",
        description="Загрузить или обновить каталог фильмов CineMatch из TMDB.",
    )
    parser.add_argument(
        "--pages",
        type=_bounded_int(1, 500),
        default=500,
        metavar="N",
        help="число страниц discover/movie, 1…500 (по умолчанию 500)",
    )
    parser.add_argument(
        "--from-year",
        type=_bounded_int(1900, 2030),
        default=1970,
        metavar="Y",
        help="нижняя граница года выхода, 1900…2030 (по умолчанию 1970)",
    )
    parser.add_argument(
        "--min-votes",
        type=_bounded_int(0, 100_000),
        default=100,
        metavar="V",
        help="минимальное число голосов, 0…100 000 (по умолчанию 100)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="продолжить последнюю незавершённую загрузку",
    )
    return parser


def format_report(report: dict[str, object]) -> str:
    """Итоговая строка stdout — data/110 §1.

    Длительность усекается до целых секунд: в примере документа `412.8`
    печатается как `6m52s`, а не округляется до `6m53s`.
    """
    duration = float(report["duration_s"])
    minutes, seconds = divmod(int(duration), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        duration_text = f"{hours}h{minutes:02d}m{seconds:02d}s"
    elif minutes:
        duration_text = f"{minutes}m{seconds:02d}s"
    else:
        duration_text = f"{seconds}s"
    return (
        f"load_id={report['load_id']} status={report['status']} pages={report['pages_done']} "
        f"films_upserted={report['movies_upserted']} "
        f"films_skipped={report['movies_skipped']} duration={duration_text}"
    )


def _database_failure(message: str) -> str:
    """Строка ошибки базы — data/110 §2: «Не удалось записать каталог: <текст>.».

    Текст `ServiceError` уже заканчивается точкой, поэтому вторая не ставится.
    """
    tail = message if message.endswith((".", "!", "?")) else f"{message}."
    return f"Не удалось записать каталог: {tail}"


def main(argv: list[str] | None = None) -> int:
    """Разобрать аргументы, подготовить базу, загрузить каталог, напечатать отчёт."""
    args = build_parser().parse_args(argv)

    # ADR-029: `alembic upgrade head` перед загрузкой каталога; тот же порядок,
    # что и при старте backend (build.md §3, шаги 3–5).
    try:
        init_database()
    except ServiceError as error:
        print(_database_failure(error.message), file=sys.stderr)
        return EXIT_DATABASE

    try:
        report = ingest_movies(
            pages=args.pages,
            from_year=args.from_year,
            min_votes=args.min_votes,
            resume=args.resume,
        )
    except IngestError as error:
        if error.report is not None:
            print(format_report(error.report))
        if error.reason == "unauthorized":
            print(UNAUTHORIZED_TEXT, file=sys.stderr)
            return EXIT_UNAUTHORIZED
        if error.reason == "interrupted":
            page = error.report["last_page"] if error.report else 0
            print(f"Загрузка прервана на странице {page}. Запустите с --resume.", file=sys.stderr)
            return EXIT_PARTIAL
        print(UNAVAILABLE_TEXT, file=sys.stderr)
        return EXIT_UNAVAILABLE
    except ServiceError as error:
        print(_database_failure(error.message), file=sys.stderr)
        return EXIT_DATABASE

    print(format_report(report))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
