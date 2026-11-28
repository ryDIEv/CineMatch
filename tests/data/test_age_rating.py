"""TS-DB-11: возрастная маркировка по ADR-018.

Правило: `adult = true` → `18+`; блок `RU` (типы 3, 4, 5, 6, 1); пустой RU →
блок `US` по таблице; пусто в обоих → `NULL`. Значения RU нормализуются,
нераспознанные — `NULL`. Сеть не нужна: проверяются чистые функции клиента.
"""

from __future__ import annotations

import pytest

from cinematch.data.clients.tmdb import age_rating, normalize_russian_certification


def _details(
    *,
    ru: list[dict[str, object]] | None = None,
    us: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Ответ деталей с `release_dates.results[]` — §7.1, шаг 3."""
    results: list[dict[str, object]] = []
    if ru is not None:
        results.append({"iso_3166_1": "RU", "release_dates": ru})
    if us is not None:
        results.append({"iso_3166_1": "US", "release_dates": us})
    return {"release_dates": {"results": results}}


def test_ts_db_11_russian_certification_wins() -> None:
    """Блок RU заполнен — US не используется (ADR-018, п. 1)."""
    details = _details(
        ru=[{"type": 3, "certification": "16+"}],
        us=[{"type": 3, "certification": "PG-13"}],
    )

    assert age_rating(details, adult=False) == "16+"


@pytest.mark.parametrize("kind", [3, 4, 5, 6, 1])
def test_ts_db_11_russian_types_in_documented_order(kind: int) -> None:
    """Первая непустая `certification` в порядке `type` 3, 4, 5, 6, 1."""
    details = _details(
        ru=[
            {"type": 1, "certification": "18+"},
            {"type": kind, "certification": "12+"},
        ]
    )

    # Для `type = 1` единственное значение — «18+», иначе срабатывает `kind`.
    expected = "18+" if kind == 1 else "12+"
    assert age_rating(details, adult=False) == expected


def test_ts_db_11_empty_russian_certification_falls_back_to_us() -> None:
    """Пустые `certification` в RU — берётся US (ADR-018, п. 2)."""
    details = _details(
        ru=[{"type": 3, "certification": ""}, {"type": 4, "certification": None}],
        us=[{"type": 3, "certification": "PG"}],
    )

    assert age_rating(details, adult=False) == "6+"


@pytest.mark.parametrize(
    ("us_value", "expected"),
    [
        ("G", "0+"),
        ("PG", "6+"),
        ("PG-13", "12+"),
        ("R", "16+"),
        ("NC-17", "18+"),
        ("NR", None),
        ("UR", None),
        ("TV-14", None),
        ("", None),
    ],
)
def test_ts_db_11_us_table(us_value: str, expected: str | None) -> None:
    """Таблица `US → age_rating`; `NR`, `UR`, `TV-*` — `NULL` (ADR-018)."""
    details = _details(us=[{"type": 3, "certification": us_value}])

    assert age_rating(details, adult=False) == expected


def test_ts_db_11_adult_is_always_18_plus() -> None:
    """`adult = true` → `18+` независимо от сертификатов (ADR-018, п. 4)."""
    details = _details(ru=[{"type": 3, "certification": "6+"}])

    assert age_rating(details, adult=True) == "18+"


def test_ts_db_11_no_certificates_gives_null() -> None:
    """Пусты оба блока — `NULL`, фильм остаётся в каталоге (ADR-018, п. 3)."""
    assert age_rating(_details(), adult=False) is None
    assert age_rating({}, adult=False) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0+", "0+"),
        ("6+", "6+"),
        ("12+", "12+"),
        ("16+", "16+"),
        ("18+", "18+"),
        (" 12+ ", "12+"),
        ("12", "12+"),
        ("16", "16+"),
        ("10", "12+"),
        ("14", "16+"),
        ("21", "18+"),
        ("0", "0+"),
        ("без ограничений", None),
        ("", None),
    ],
)
def test_ts_db_11_russian_values_are_normalized(value: str, expected: str | None) -> None:
    """RU приводится к ближайшему значению перечня, иное — `NULL` (ADR-018)."""
    assert normalize_russian_certification(value) == expected
