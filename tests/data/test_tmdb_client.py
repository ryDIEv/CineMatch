"""Нормализация ответа TMDB — data/130 §7.2, §7.4.

Проверяются чистые функции клиента: заголовок, год, длительность, рейтинг,
жанры, актёры и справочник жанров. База и сеть не нужны.
"""

from __future__ import annotations

import pytest

from cinematch.data.clients.tmdb import (
    BACKOFF_S,
    MAX_ATTEMPTS,
    MIN_INTERVAL_S,
    REQUEST_TIMEOUT_S,
    US_CERTIFICATIONS,
    movie_title,
    movie_year,
    normalize_cast,
    normalize_genre,
    normalize_genres,
    normalize_movie,
)
from tests.data.external import details, discover_item


def test_ts_db_01_request_limits_follow_section_7_4() -> None:
    """Темп, тайм-аут, число попыток и ряд пауз — числа из таблицы §7.4."""
    assert MIN_INTERVAL_S >= 0.025
    assert REQUEST_TIMEOUT_S == 10.0
    assert MAX_ATTEMPTS == 5
    assert BACKOFF_S == (1, 2, 4, 8, 16)


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"title": "Название", "original_title": "Original"}, "Название"),
        ({"title": "  ", "original_title": "Original"}, "Original"),
        ({"title": "", "original_title": ""}, "Без названия"),
        ({}, "Без названия"),
    ],
)
def test_ts_db_01_title_fallback_chain(item: dict, expected: str) -> None:
    """`title` → `original_title` → «Без названия» (§7.2)."""
    assert movie_title(item) == expected


@pytest.mark.parametrize(
    ("date", "expected"),
    [
        ("2014-05-17", 2014),
        ("1999-12-31", 1999),
        ("", None),
        ("20", None),
        ("abcd-01-01", None),
    ],
)
def test_ts_db_01_year_from_release_date(date: str, expected: int | None) -> None:
    """Первые 4 символа `release_date`; пустая или нечисловая дата — `NULL`."""
    assert movie_year({"release_date": date}) == expected


def test_ts_db_01_movie_without_id_is_skipped() -> None:
    """Фильм без `id` пропускается и считается в `movies_skipped` (§7.2)."""
    assert normalize_movie({"title": "Без id"}, details(1)) is None
    # `bool` — не идентификатор: `True` не станет `tmdb_id = 1`.
    assert normalize_movie({"id": True, "title": "Флаг"}, None) is None


def test_ts_db_01_movie_without_title_is_skipped() -> None:
    """Пустые `title` и `original_title` — пропуск (§7.2, последний абзац)."""
    assert normalize_movie({"id": 10, "title": "", "original_title": ""}, None) is None


def test_ts_db_01_movie_without_genres_is_kept() -> None:
    """Фильм без жанров сохраняется: его просто не найдёт жанровый фильтр (§7.2)."""
    record = normalize_movie(
        discover_item(11, title="Без жанров"), details(11, genres=[], cast=[])
    )

    assert record is not None
    assert record.genres == ()
    assert record.cast == ()


def test_ts_db_01_normalization_uses_discover_and_details() -> None:
    """`runtime`, `overview`, жанры и актёры — из деталей; остальное — из `discover`."""
    record = normalize_movie(
        discover_item(12, title="Фильм", release_date="2001-02-03", vote_count=500),
        details(
            12,
            runtime=0,
            overview="",
            genres=[{"id": 18, "name": "Драма"}],
            cast=[
                {"id": 1, "name": "Первый", "character": "A"},
                {"id": 2, "name": "Второй", "character": None},
            ],
        ),
    )

    assert record is not None
    assert record.year == 2001
    assert record.vote_count == 500
    # `runtime = 0` и пустой `overview` — NULL (§7.2).
    assert record.runtime is None
    assert record.overview is None
    assert [(genre.tmdb_id, genre.name) for genre in record.genres] == [(18, "Драма")]
    assert [actor.billing_order for actor in record.cast] == [0, 1]
    assert record.cast[1].character is None


def test_ts_db_01_only_first_ten_cast_members() -> None:
    """В `movie_cast` попадают первые 10 актёров `credits.cast` (data/120 §4)."""
    cast = [{"id": index, "name": f"Актёр {index}", "character": None} for index in range(15)]

    record = normalize_movie(discover_item(13, title="Много актёров"), details(13, cast=cast))

    assert record is not None
    assert [actor.tmdb_id for actor in record.cast] == list(range(10))
    assert [actor.billing_order for actor in record.cast] == list(range(10))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"id": 1, "name": "Жанр"}, (1, "Жанр")),
        ({"id": "1", "name": "Жанр"}, None),
        ({"id": 1, "name": "  "}, None),
        ({"name": "Жанр"}, None),
    ],
)
def test_ts_db_01_genre_requires_id_and_name(raw: dict, expected: tuple | None) -> None:
    """Жанр без числового `id` или названия пропускается (§7.2)."""
    genre = normalize_genre(raw)

    if expected is None:
        assert genre is None
    else:
        assert (genre.tmdb_id, genre.name) == expected


def test_ts_db_01_genre_list_has_no_duplicates() -> None:
    """Справочник без повторов: второй `tmdb_id` отбрасывается."""
    genres = normalize_genres(
        [
            {"id": 18, "name": "Драма"},
            {"id": 18, "name": "Драма (дубль)"},
            {"id": 35, "name": "Комедия"},
        ]
    )

    assert [(genre.tmdb_id, genre.name) for genre in genres] == [
        (18, "Драма"),
        (35, "Комедия"),
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"id": 5, "name": "Имя", "character": "Роль"}, (5, "Имя", "Роль")),
        ({"id": 5, "name": "Имя", "character": "  "}, (5, "Имя", None)),
        ({"id": 5, "name": "Имя"}, (5, "Имя", None)),
        ({"name": "Имя"}, None),
        ({"id": 5, "name": ""}, None),
    ],
)
def test_ts_db_01_cast_requires_id_and_name(raw: dict, expected: tuple | None) -> None:
    """Актёр без `id` или имени пропускается; пустая роль — `NULL` (data/120 §4)."""
    actor = normalize_cast(raw)

    if expected is None:
        assert actor is None
    else:
        assert (actor.tmdb_id, actor.name, actor.character) == expected


def test_ts_db_11_us_table_matches_adr_018() -> None:
    """Таблица `US → age_rating` совпадает с ADR-018, лишних значений нет."""
    assert US_CERTIFICATIONS == {
        "G": "0+",
        "PG": "6+",
        "PG-13": "12+",
        "R": "16+",
        "NC-17": "18+",
    }
