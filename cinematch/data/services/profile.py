"""Профиль предпочтений — ADR-013, data/130 §4.2.

Профиль считается при каждом чтении по **всем** оценкам пользователя; таблицы
профиля нет. `mood_match` в формулу не входит (ADR-013, п. 3): настроение
учитывается отдельно штрафом в `find_movies` (ADR-050).

```text
r      = (rating − 5.5) / 4.5   — нормированная оценка, [−1, 1]
raw    = Σ r_i / n
c      = n / (n + 3)
weight = round(raw × c, 3)
```
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from cinematch.data.repositories import catalog
from cinematch.data.schemas import PreferenceProfile

# До 20 актёров в `actor_weights` — ADR-013.
MAX_ACTORS = 20


def normalized_rating(rating: int) -> float:
    """Нормированная оценка `r = (rating − 5.5) / 4.5` (ADR-013)."""
    return (rating - 5.5) / 4.5


def trusted_weight(total: float, count: int) -> float:
    """Вес `round(raw × n / (n + 3), 3)`, в пределах [−1, 1] (ADR-013)."""
    raw = total / count
    weight = raw * (count / (count + 3))
    return round(max(-1.0, min(1.0, weight)), 3)


def _weights(pairs: list[tuple[int, str]], rating_by_movie: dict[int, float]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for movie_id, name in pairs:
        normalized = rating_by_movie.get(movie_id)
        if normalized is None:
            continue
        totals[name] += normalized
        counts[name] += 1
    weighted = {name: trusted_weight(totals[name], counts[name]) for name in totals}
    # Порядок ключей фиксирован: |weight| по убыванию, затем название (ADR-013, п. 5).
    return dict(sorted(weighted.items(), key=lambda item: (-abs(item[1]), item[0])))


def build_profile(session: Session, user_id: str) -> PreferenceProfile:
    """Посчитать профиль по всем оценкам пользователя (ADR-013)."""
    ratings = catalog.all_ratings(session, user_id)
    if not ratings:
        # Холодный старт: пустые словари, ratings_count = 0 (data/130 §4.2).
        return PreferenceProfile(genre_weights={}, actor_weights={}, ratings_count=0)

    rating_by_movie = {
        movie_id: normalized_rating(rating) for movie_id, rating in ratings
    }
    movie_ids = [movie_id for movie_id, _rating in ratings]

    genre_weights = _weights(catalog.movie_genre_pairs(session, movie_ids), rating_by_movie)
    actor_weights = _weights(catalog.movie_actor_pairs(session, movie_ids), rating_by_movie)
    # В `actor_weights` — до 20 актёров с наибольшим |weight| (ADR-013).
    actor_weights = dict(list(actor_weights.items())[:MAX_ACTORS])

    return PreferenceProfile(
        genre_weights=genre_weights,
        actor_weights=actor_weights,
        ratings_count=len(ratings),
    )
