"""Общие приспособления тестов модуля данных (docs/testing/ts-db.md).

Каждый тест получает временную базу `tmp_path`, схему из миграции
`0001_initial` (data/120 §9) и фикстуру `tests/fixtures/seed_movies.json`
(23 фильма, ADR-042). Сеть запрещена: тесты работают только с базой.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from cinematch.data import api as data_api
from cinematch.data import db
from cinematch.data.clients.weather import WeatherClient
from cinematch.data.db import init_database, utc_now
from cinematch.data.models import Genre, Movie, MovieCast, MovieGenre, Person, User, normalize_text
from tests.data.external import TmdbMock, WeatherMock

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SEED_MOVIES = FIXTURES / "seed_movies.json"
TEST_USER = "web:local"


@pytest.fixture
def data_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Engine]:
    """Временная база с применённой миграцией; движок подменяет движок модуля."""
    path = tmp_path / "cinematch.db"
    monkeypatch.setenv("CINEMATCH_DB_PATH", str(path))
    init_database(path)
    engine = db.get_engine()
    try:
        yield engine
    finally:
        db._engine = None
        db._session_factory = None
        db._configured_path = None
        engine.dispose()


@pytest.fixture
def session(data_db: Engine) -> Iterator[Session]:
    """Сессия для подготовки данных и прямой проверки SQL."""
    factory = sessionmaker(bind=data_db, expire_on_commit=False)
    current = factory()
    try:
        yield current
    finally:
        current.rollback()
        current.close()


def load_seed(session: Session, *, user_id: str = TEST_USER) -> list[Movie]:
    """Вставить фикстуру 23 фильмов, жанры, актёров и пользователя (ADR-042)."""
    payload = json.loads(SEED_MOVIES.read_text(encoding="utf-8"))
    genre_names: list[str] = []
    for item in payload:
        for name in item["genres"]:
            if name not in genre_names:
                genre_names.append(name)

    for order, name in enumerate(sorted(genre_names), start=1):
        session.add(Genre(tmdb_id=100 + order, name=name, name_norm=normalize_text(name)))

    person_names: list[str] = []
    for item in payload:
        for name in item.get("actors", []):
            if name not in person_names:
                person_names.append(name)
    for order, name in enumerate(person_names, start=1):
        session.add(Person(tmdb_id=500 + order, name=name))

    session.add(
        User(
            user_id=user_id,
            source="web",
            consent_at=utc_now(),
            city="Москва",
            latitude=55.75,
            longitude=37.62,
            created_at=utc_now(),
        )
    )
    session.flush()

    genres_by_name = {genre.name: genre for genre in session.query(Genre).all()}
    people_by_name = {person.name: person for person in session.query(Person).all()}

    movies: list[Movie] = []
    for item in payload:
        movie = Movie(
            id=item["movie_id"],
            tmdb_id=item["tmdb_id"],
            title=item["title"],
            title_norm=normalize_text(item["title"]),
            original_title=None,
            year=item["year"],
            runtime=item["runtime"],
            rating=item["rating"],
            vote_count=item["vote_count"],
            popularity=None,
            age_rating=item["age_rating"],
            overview=item.get("overview"),
            poster_path=None,
            adult=0,
            updated_at=utc_now(),
        )
        session.add(movie)
        movies.append(movie)
        for name in item["genres"]:
            session.add(MovieGenre(movie_id=movie.id, genre_id=genres_by_name[name].id))
        for order, name in enumerate(item.get("actors", [])):
            session.add(
                MovieCast(
                    movie_id=movie.id,
                    person_id=people_by_name[name].id,
                    billing_order=order,
                    character=None,
                )
            )

    session.commit()
    return movies


def ids(cards: Iterable[object]) -> list[int]:
    """Идентификаторы карточек или строк в порядке выдачи."""
    return [getattr(card, "movie_id", card) for card in cards]


@dataclass
class ExternalCalls:
    """Записанные паузы клиента TMDB: проверка темпа и ряда повторов."""

    sleeps: list[float] = field(default_factory=list)


@pytest.fixture
def tmdb(
    monkeypatch: pytest.MonkeyPatch, data_db: Engine
) -> Iterator[tuple[TmdbMock, ExternalCalls]]:
    """Подменить транспорт TMDB и паузы: сеть и задержки в тестах не участвуют.

    `TMDB_API_KEY` задаётся здесь же: иначе загрузка остановится до запросов
    (data/110 §2, `CONFIG_MISSING`).
    """
    mock = TmdbMock()
    calls = ExternalCalls()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    monkeypatch.setattr(data_api, "TMDB_TRANSPORT", mock.transport())
    monkeypatch.setattr(data_api, "SLEEP", calls.sleeps.append)
    try:
        yield mock, calls
    finally:
        monkeypatch.setattr(data_api, "TMDB_TRANSPORT", None)


@pytest.fixture
def open_meteo(
    monkeypatch: pytest.MonkeyPatch, data_db: Engine
) -> Iterator[tuple[WeatherMock, WeatherClient]]:
    """Подменить транспорт Open-Meteo и сбросить кэш погоды процесса (§8.4)."""
    mock = WeatherMock()
    client = WeatherClient(transport=mock.transport())
    monkeypatch.setattr(data_api, "WEATHER_TRANSPORT", None)
    monkeypatch.setattr(data_api, "_weather_client", client)
    try:
        yield mock, client
    finally:
        client.close()
        monkeypatch.setattr(data_api, "_weather_client", None)
