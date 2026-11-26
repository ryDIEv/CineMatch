"""Запросы каталога: фильмы, жанры, актёры — data/120 §3–4, data/130 §4.4–4.6.

`MovieCard` собирается здесь: `poster_url` из `poster_path` (data/120 §3),
жанры — по возрастанию `genres.tmdb_id` (data/120 §4).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import Row, and_, func, literal, or_, select
from sqlalchemy.orm import Session

from cinematch.data.models import (
    Genre,
    Movie,
    MovieCast,
    MovieGenre,
    Person,
    UserChoice,
    UserRating,
)
from cinematch.overall.contracts import MovieCard

POSTER_BASE = "https://image.tmdb.org/t/p/w342"

# Порог голосов и обязательный рейтинг — data/130 §5, ADR-041.
MIN_VOTE_COUNT = 100


@dataclass(frozen=True, slots=True)
class Candidate:
    """Строка кандидата `find_movies` до пересортировки (data/130 §4.4)."""

    movie_id: int
    tmdb_id: int
    title: str
    year: int | None
    runtime: int | None
    rating: float | None
    vote_count: int
    age_rating: str | None
    poster_path: str | None
    matched: int


def catalog_size(session: Session) -> int:
    """`SELECT COUNT(*) FROM movies` — data/130 §4.20."""
    return int(session.scalar(select(func.count()).select_from(Movie)) or 0)


def catalog_is_empty(session: Session) -> bool:
    """`SELECT 1 FROM movies LIMIT 1` пуст — каталог не загружен."""
    return session.scalar(select(Movie.id).limit(1)) is None


def genre_names(session: Session) -> list[str]:
    """Названия жанров по возрастанию `tmdb_id` — data/130 §4.21."""
    return list(session.scalars(select(Genre.name).order_by(Genre.tmdb_id)))


def genre_ids_by_norm(session: Session, norms: Sequence[str]) -> dict[str, int]:
    """Сопоставить нормализованные названия жанров их `id` (data/130 §4.4, п. 4)."""
    if not norms:
        return {}
    rows = session.execute(
        select(Genre.name_norm, Genre.id).where(Genre.name_norm.in_(norms))
    ).all()
    return {name_norm: genre_id for name_norm, genre_id in rows}


def _matched_subquery(genre_ids: Sequence[int]) -> object:
    """Подзапрос числа совпавших жанров; логика — ИЛИ (data/130 §4.4, п. 3)."""
    return (
        select(MovieGenre.movie_id, func.count().label("matched"))
        .where(MovieGenre.genre_id.in_(genre_ids))
        .group_by(MovieGenre.movie_id)
        .subquery("gm")
    )


def find_candidates(
    session: Session,
    *,
    user_id: str,
    genre_ids: Sequence[int] = (),
    exclude_genre_ids: Sequence[int] = (),
    year_from: int | None = None,
    year_to: int | None = None,
    min_rating: float | None = None,
    max_runtime: int | None = None,
    exclude_ids: Sequence[int] = (),
    limit: int,
) -> list[Candidate]:
    """Кандидаты `find_movies` с запасом по порядку SQL — data/130 §4.4.

    `limit` — уже вычисленный размер выдачи `min(3 × limit, 45)` (ADR-049).
    Условия, которые в документе записаны как `:param IS NULL OR …`, добавляются
    в SQL только при заданном значении (data/130 §1, п. 6).
    """
    matched = _matched_subquery(genre_ids) if genre_ids else None
    if matched is not None:
        query = select(Movie, matched.c.matched).outerjoin(
            matched, matched.c.movie_id == Movie.id
        )
        matched_column = func.coalesce(matched.c.matched, 0)
    else:
        # Без `genres` подзапрос не выполняется: matched = 0 у всех строк (data/130 §4.4).
        matched_column = literal(0)
        query = select(Movie, matched_column.label("matched"))

    # Рейтинг и порог голосов — всегда (data/130 §4.4, п. 1).
    conditions = [
        Movie.rating.is_not(None),
        Movie.vote_count >= MIN_VOTE_COUNT,
        Movie.id.not_in(select(UserRating.movie_id).where(UserRating.user_id == user_id)),
    ]
    if year_from is not None:
        conditions.append(Movie.year >= year_from)
    if year_to is not None:
        conditions.append(Movie.year <= year_to)
    if min_rating is not None:
        conditions.append(Movie.rating >= min_rating)
    if max_runtime is not None:
        conditions.append(Movie.runtime.is_not(None))
        conditions.append(Movie.runtime <= max_runtime)
    if exclude_ids:
        conditions.append(Movie.id.not_in(list(exclude_ids)))
    if matched is not None:
        conditions.append(matched.c.matched > 0)
    for genre_id in exclude_genre_ids:
        conditions.append(
            ~(
                select(MovieGenre.movie_id)
                .where(MovieGenre.movie_id == Movie.id)
                .where(MovieGenre.genre_id == genre_id)
                .exists()
            )
        )

    query = (
        query.where(*conditions)
        .order_by(
            matched_column.desc(),
            Movie.rating.desc(),
            Movie.vote_count.desc(),
            Movie.id.asc(),
        )
        .limit(limit)
    )

    return [
        Candidate(
            movie_id=movie.id,
            tmdb_id=movie.tmdb_id,
            title=movie.title,
            year=movie.year,
            runtime=movie.runtime,
            rating=movie.rating,
            vote_count=movie.vote_count,
            age_rating=movie.age_rating,
            poster_path=movie.poster_path,
            matched=int(matched_count or 0),
        )
        for movie, matched_count in session.execute(query).all()
    ]


def genre_names_for_movies(
    session: Session, movie_ids: Iterable[int]
) -> dict[int, list[str]]:
    """Жанры фильмов по возрастанию `genres.tmdb_id` — data/120 §4."""
    ids = list(movie_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(MovieGenre.movie_id, Genre.name)
        .join(Genre, Genre.id == MovieGenre.genre_id)
        .where(MovieGenre.movie_id.in_(ids))
        .order_by(Genre.tmdb_id)
    ).all()
    grouped: dict[int, list[str]] = defaultdict(list)
    for movie_id, name in rows:
        grouped[movie_id].append(name)
    return dict(grouped)


def poster_url(poster_path: str | None) -> str | None:
    """`poster_url` из `poster_path` — data/120 §3."""
    if not poster_path:
        return None
    return f"{POSTER_BASE}{poster_path}"


def card_from_movie(movie: Movie, genres: list[str]) -> MovieCard:
    """Собрать `MovieCard` из строки `movies` (contracts §2.1)."""
    return MovieCard(
        movie_id=movie.id,
        tmdb_id=movie.tmdb_id,
        title=movie.title,
        year=movie.year,
        runtime=movie.runtime,
        rating=movie.rating,
        vote_count=movie.vote_count,
        age_rating=movie.age_rating,
        genres=genres,
        poster_url=poster_url(movie.poster_path),
    )


def card_from_candidate(candidate: Candidate, genres: list[str]) -> MovieCard:
    """`MovieCard` из строки кандидата: второй запрос к `movies` не нужен."""
    return MovieCard(
        movie_id=candidate.movie_id,
        tmdb_id=candidate.tmdb_id,
        title=candidate.title,
        year=candidate.year,
        runtime=candidate.runtime,
        rating=candidate.rating,
        vote_count=candidate.vote_count,
        age_rating=candidate.age_rating,
        genres=genres,
        poster_url=poster_url(candidate.poster_path),
    )


def cards_for_movies(session: Session, movie_ids: Sequence[int]) -> dict[int, MovieCard]:
    """Карточки для набора фильмов одним запросом жанров (data/146 §3, п. 5)."""
    if not movie_ids:
        return {}
    movies = session.scalars(select(Movie).where(Movie.id.in_(movie_ids))).all()
    genres = genre_names_for_movies(session, [movie.id for movie in movies])
    return {movie.id: card_from_movie(movie, genres.get(movie.id, [])) for movie in movies}


def movie_exists(session: Session, movie_id: int) -> bool:
    """Есть ли фильм в каталоге (data/130 §4.5, §4.7, §4.8)."""
    return session.scalar(select(Movie.id).where(Movie.id == movie_id)) is not None


def movie_by_id(session: Session, movie_id: int) -> Movie | None:
    """Строка `movies` для `get_movie_with_credits` (data/130 §4.5)."""
    return session.get(Movie, movie_id)


def actors_for_movie(session: Session, movie_id: int) -> list[str]:
    """Имена первых 10 актёров по `billing_order` — data/130 §4.5."""
    return list(
        session.scalars(
            select(Person.name)
            .join(MovieCast, MovieCast.person_id == Person.id)
            .where(MovieCast.movie_id == movie_id)
            .order_by(MovieCast.billing_order)
            .limit(10)
        )
    )


def search_by_title(
    session: Session, title_norm: str, year: int | None, limit: int = 5
) -> list[Movie]:
    """Поиск по `title_norm`: точное, с начала, подстрока — data/130 §4.6."""
    exact = Movie.title_norm == title_norm
    prefix = Movie.title_norm.startswith(title_norm, autoescape=True)
    contains = Movie.title_norm.contains(title_norm, autoescape=True)
    query = select(Movie).where(or_(exact, prefix, contains))
    if year is not None:
        query = query.where(Movie.year == year)
    query = query.order_by(
        exact.desc(),
        prefix.desc(),
        Movie.vote_count.desc(),
        Movie.id.asc(),
    ).limit(limit)
    return list(session.scalars(query))


def ratings_with_genres(session: Session, user_id: str, limit: int) -> list[Row]:
    """Оценки пользователя по убыванию `rated_at` — data/130 §4.1."""
    return list(
        session.execute(
            select(
                UserRating.movie_id,
                Movie.title,
                Movie.year,
                UserRating.rating,
                UserRating.mood_match,
                UserRating.rated_at,
            )
            .join(Movie, Movie.id == UserRating.movie_id)
            .where(UserRating.user_id == user_id)
            .order_by(UserRating.rated_at.desc(), UserRating.movie_id.asc())
            .limit(limit)
        ).all()
    )


def all_ratings(session: Session, user_id: str) -> list[tuple[int, int]]:
    """Все оценки `(movie_id, rating)` — профиль считается по всем (ADR-013)."""
    return list(
        session.execute(
            select(UserRating.movie_id, UserRating.rating).where(
                UserRating.user_id == user_id
            )
        ).all()
    )


def movie_genre_pairs(session: Session, movie_ids: Sequence[int]) -> list[tuple[int, str]]:
    """Пары `(movie_id, название жанра)` для профиля (ADR-013)."""
    if not movie_ids:
        return []
    return list(
        session.execute(
            select(MovieGenre.movie_id, Genre.name)
            .join(Genre, Genre.id == MovieGenre.genre_id)
            .where(MovieGenre.movie_id.in_(movie_ids))
        ).all()
    )


def movie_actor_pairs(session: Session, movie_ids: Sequence[int]) -> list[tuple[int, str]]:
    """Пары `(movie_id, имя актёра)` по `movie_cast` (ADR-013)."""
    if not movie_ids:
        return []
    return list(
        session.execute(
            select(MovieCast.movie_id, Person.name)
            .join(Person, Person.id == MovieCast.person_id)
            .where(MovieCast.movie_id.in_(movie_ids))
        ).all()
    )


def mood_penalty_genres(session: Session, user_id: str, mood: str) -> set[str]:
    """Жанры с `mood_match = 'no'` при том же настроении — ADR-050.

    Выборка: `user_ratings`, соединённая с `user_choices` по `(user_id, movie_id)`,
    где выбор `rated`, оценка `mood_match = 'no'`, а `user_choices.mood` равен
    текущему настроению.
    """
    rows = session.execute(
        select(Genre.name)
        .join(MovieGenre, MovieGenre.genre_id == Genre.id)
        .join(UserRating, UserRating.movie_id == MovieGenre.movie_id)
        .join(
            UserChoice,
            and_(
                UserChoice.user_id == UserRating.user_id,
                UserChoice.movie_id == UserRating.movie_id,
            ),
        )
        .where(UserRating.user_id == user_id)
        .where(UserRating.mood_match == "no")
        .where(UserChoice.status == "rated")
        .where(UserChoice.mood == mood)
        .distinct()
    ).all()
    return {name for (name,) in rows}
