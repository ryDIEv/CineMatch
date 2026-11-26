"""TS-DB-05: профиль предпочтений — ADR-013, data/130 §4.2.

Фикстура — 23 фильма; ожидаемые веса берутся из примера ADR-013 на пяти
оценках и пересчитываются по формуле документа для остальных случаев.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from cinematch.data.api import find_movies, get_user_profile, get_user_ratings, save_rating
from cinematch.data.services.profile import normalized_rating, trusted_weight
from tests.data.conftest import TEST_USER, ids, load_seed


def test_ts_db_05_cold_start_has_empty_dicts(data_db, session: Session) -> None:
    """Холодный старт: пустые словари и `ratings_count = 0` (data/130 §4.2)."""
    load_seed(session)

    profile = get_user_profile(TEST_USER)

    assert profile.genre_weights == {}
    assert profile.actor_weights == {}
    assert profile.ratings_count == 0


def test_ts_db_05_formula_matches_adr_example(data_db, session: Session) -> None:
    """Веса из примера ADR-013 на пяти оценках: 0.352, 0.089, −0.222."""
    load_seed(session)

    # A — фильм 1 (Драма, Комедия), B — 3 (Драма), C — 9 (Ужасы, Комедия),
    # D — 17 (Драма), E — 10 (Ужасы): таблица ADR-013.
    for movie_id, rating in ((1, 9), (3, 7), (9, 4), (17, 10), (10, 2)):
        save_rating(user_id=TEST_USER, movie_id=movie_id, rating=rating, source="manual")

    profile = get_user_profile(TEST_USER)

    assert profile.ratings_count == 5
    assert profile.genre_weights == {"Драма": 0.352, "Комедия": 0.089, "Ужасы": -0.222}
    # Оба актёра фикстуры есть во всех пяти фильмах: n = 5,
    # raw = 1.0 / 5 = 0.2; c = 5 / 8; weight = 0.125.
    assert profile.actor_weights["Анна Петрова"] == pytest.approx(0.125)
    assert profile.actor_weights["Иван Сергеев"] == pytest.approx(0.125)
    # Олег Морозов — только фильм 21, он не оценён: в профиль не попадает.
    assert "Олег Морозов" not in profile.actor_weights
    assert list(profile.actor_weights)[:2] == ["Анна Петрова", "Иван Сергеев"]


def test_ts_db_05_new_rating_changes_profile(data_db, session: Session) -> None:
    """Новая оценка сразу меняет профиль: таблицы профиля нет (ADR-013)."""
    load_seed(session)
    save_rating(user_id=TEST_USER, movie_id=3, rating=10, source="manual")

    first = get_user_profile(TEST_USER)
    assert first.ratings_count == 1
    # n = 1: r = (10 − 5.5) / 4.5 = 1.0; c = 1 / 4; weight = 0.25.
    assert first.genre_weights["Драма"] == 0.25

    save_rating(user_id=TEST_USER, movie_id=17, rating=10, source="manual")

    second = get_user_profile(TEST_USER)
    assert second.ratings_count == 2
    # n = 2: raw = 1.0; c = 2 / 5; weight = 0.4.
    assert second.genre_weights["Драма"] == 0.4


def test_ts_db_05_repeat_rating_does_not_count_twice(data_db, session: Session) -> None:
    """Повторная оценка перезаписывает строку, профиль считает фильм один раз."""
    load_seed(session)
    save_rating(user_id=TEST_USER, movie_id=3, rating=10, source="manual")
    save_rating(user_id=TEST_USER, movie_id=3, rating=2, source="manual")

    profile = get_user_profile(TEST_USER)

    assert profile.ratings_count == 1
    # r = (2 − 5.5) / 4.5 = −0.777…; c = 0.25; weight ≈ −0.194.
    assert profile.genre_weights["Драма"] == pytest.approx(-0.194)


def test_ts_db_05_limit_of_ratings_does_not_affect_profile(data_db, session: Session) -> None:
    """`limit` в `get_user_ratings` ограничивает выдачу, а не профиль (ADR-013, п. 1)."""
    load_seed(session)
    for movie_id, rating in ((3, 10), (17, 9), (15, 8), (20, 7), (1, 6)):
        save_rating(user_id=TEST_USER, movie_id=movie_id, rating=rating, source="manual")

    limited = get_user_ratings(TEST_USER, limit=2)
    profile = get_user_profile(TEST_USER)

    assert len(limited) == 2
    assert profile.ratings_count == 5
    # Драма есть у всех пяти: raw = 2.777… / 5 = 0.555…; c = 5 / 8; 0.347.
    assert profile.genre_weights["Драма"] == pytest.approx(0.347)
    # Комедия — только фильм 1 с оценкой 6: r = 0.111; c = 0.25; 0.028.
    assert profile.genre_weights["Комедия"] == pytest.approx(0.028)


def test_ts_db_05_keys_sorted_by_abs_weight_then_name(data_db, session: Session) -> None:
    """Порядок ключей: |weight| по убыванию, затем название (ADR-013, п. 5)."""
    load_seed(session)
    for movie_id, rating in ((1, 9), (3, 7), (9, 4), (17, 10), (10, 2)):
        save_rating(user_id=TEST_USER, movie_id=movie_id, rating=rating, source="manual")

    weights = list(get_user_profile(TEST_USER).genre_weights)

    assert weights == ["Драма", "Ужасы", "Комедия"]


def test_ts_db_05_profile_changes_find_movies_order(data_db, session: Session) -> None:
    """Профиль меняет порядок `find_movies`, но не состав фильтров (ADR-013, п. 4)."""
    load_seed(session)

    before = ids(find_movies(user_id=TEST_USER, genres=["Драма"], limit=3))
    assert before == [17, 15, 1]

    # Фильм 12 (Приключения, Семейный) с оценкой 10 даёт вес «Приключениям»;
    # в выдаче по «Драме» фильм 20 (Драма + Приключения) обгоняет более рейтинговые.
    save_rating(user_id=TEST_USER, movie_id=12, rating=10, source="manual")

    after = ids(find_movies(user_id=TEST_USER, genres=["Драма"], limit=30))
    assert after[0] == 20
    assert set(before) <= set(after)


def test_ts_db_05_formula_helpers() -> None:
    """Границы формулы: нейтральная оценка 5,5 даёт 0, края — ±1 (ADR-013)."""
    assert normalized_rating(5) == pytest.approx(-0.111, abs=1e-3)
    assert normalized_rating(6) == pytest.approx(0.111, abs=1e-3)
    assert normalized_rating(1) == pytest.approx(-1.0)
    assert normalized_rating(10) == pytest.approx(1.0)
    # `trusted_weight(сумма r, n)`: n = 1 — четверть raw, n = 10 — 77 % raw.
    assert trusted_weight(0.0, 1) == 0.0
    assert trusted_weight(1.0, 1) == 0.25
    assert trusted_weight(10.0, 10) == 0.769
    assert trusted_weight(-10.0, 10) == -0.769
