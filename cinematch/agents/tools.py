"""Реестр инструментов агента: подстановка служебных аргументов и проверки.

Границы — [070](../documents/agents/070.role-matrix.md) и
[130 §4](../documents/agents/130.api-contracts.md#4-инструменты-агента):

* модель передаёт только содержательные параметры, а `user_id`, `exclude_ids`,
  `mood`, `choice_id` и `source` подставляет код (ADR-008, ADR-050);
* `exclude_genres` объединяется с `SessionState.context.genre_avoid`, поэтому
  снять запрет пользователя модель не может (130 §4, п. 1);
* автоматические запреты жанров — из таблицы настроения и профиля с весом
  ≤ −0,15, кроме жанров из `genre_wishes` (ADR-051);
* `max_runtime` не ослабляется: при заданном `available_minutes` действует
  он, иначе — ограничение из таблицы настроения (120 §2, §2.1);
* ошибки модели (`INVALID_PARAM`, `UNKNOWN_GENRE`, `NOT_FOUND`) возвращаются
  результатом `{"error": {"code", "message"}}`, а не исключением наружу
  (contracts.md §6, п. 1);
* трассу строит код: шаг, `action` и `summary` — из вызова и результата
  (contracts.md §2.7; agents/120 §5).

Функции модуля данных синхронные (ADR-047); Pydantic AI выполняет их в пуле
потоков, поэтому методы реестра синхронные.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from pydantic import Field, ValidationError

from cinematch.agents import trace as trace_builder
from cinematch.data.schemas import (
    MovieDetails,
    MovieSummary,
    PreferenceProfile,
    RatedMovie,
    Weather,
)
from cinematch.overall.contracts import (
    EveningContext,
    MovieCard,
    PendingChoice,
    SessionState,
)
from cinematch.overall.errors import ServiceError, ToolError

type ProgressFn = Callable[[str, str], None]

# Фраза шага `check` — 110 §4.
CHECK_PROGRESS = "Проверяю подборку…"

# Пределы `exclude_genres` — 130 §4.1, ADR-051.
MAX_EXCLUDE_GENRES = 5

# Профильный порог автоматического запрета — ADR-051.
PROFILE_BAN_THRESHOLD = -0.15

# Таблица «настроение → критерии», 125 §2, правило 6. Первая строка —
# запрещённые жанры, вторая — ограничение длительности; длительность
# применяется только при пустом `available_minutes` (ADR-051).
MOOD_CRITERIA: dict[str, tuple[tuple[str, ...], int | None]] = {
    "tired": (("триллер", "ужасы"), 100),
    "sad": (("ужасы", "боевик"), None),
    "calm": (("боевик", "триллер"), 120),
    "tense": (("триллер", "ужасы"), 120),
    "cheerful": ((), None),
    "other": (("боевик", "триллер"), 120),
}

# Фразы событий `progress` для инструментов — 110 §4. Набор закрытый.
TOOL_PROGRESS: dict[str, str] = {
    "extract_context": "Разбираю, что вы описали…",
    "get_user_ratings": "Смотрю ваши оценки…",
    "get_user_profile": "Смотрю ваши оценки…",
    "get_weather": "Проверяю погоду…",
    "find_movies": "Ищу фильмы…",
    "get_movie_with_credits": "Уточняю детали…",
    "search_movie": "Уточняю детали…",
    "save_choice": "Сохраняю…",
    "save_rating": "Сохраняю…",
    "close_choice": "Сохраняю…",
}


def normalize_genre(name: str) -> str:
    """Нормализация названия жанра: `lower`, `ё → е`, сжатие пробелов."""
    return " ".join(name.lower().replace("ё", "е").split())


class ToolRegistry:
    """Инструменты агента для одного хода поверх модуля данных.

    Состояние хода: пользователь, сессия (`last_shown_ids`, `shown_ids`,
    `pending`) и накопленный контекст. `context` реестр обновляет сам после
    `extract_context`, чтобы запреты настроения и профиля действовали уже в
    этом ходе; в сессию контекст записывает оркестратор (120 §2.0).
    """

    def __init__(
        self,
        data: Any,
        *,
        user_id: str,
        state: SessionState,
        profile: PreferenceProfile | None = None,
        on_progress: ProgressFn | None = None,
    ) -> None:
        self.data = data
        self.user_id = user_id
        self.state = state
        self.profile = profile or PreferenceProfile(
            genre_weights={}, actor_weights={}, ratings_count=0
        )
        self.on_progress = on_progress

        self.context: EveningContext = state.context or EveningContext()
        self.pending: PendingChoice | None = state.pending
        self.trace: list[trace_builder.AgentTrace] = []
        # Карточки всех поисков хода: ответ проверяется только по ним
        # (contracts.md §5, п. 5). Ход может искать дважды, ослабляя критерии
        # (120 §2.1), поэтому хранится объединение, а не только последняя выдача.
        self.found: dict[int, MovieCard] = {}
        # Размер лучшей выдачи хода: по нему проверка решает, нужно ли ровно три
        # фильма (120 §5, п. 4). Ослабление критериев (120 §2.1) расширяет
        # выдачу, поэтому лучшая выдача — это и есть последняя.
        self.best_found = 0

        # Справочник жанров: автоматические запреты добавляются только
        # названиями из него, иначе модуль данных ответил бы `UNKNOWN_GENRE`.
        self._genres: list[str] = list(self.data.get_genres())
        self._canonical = {normalize_genre(name): name for name in self._genres}

    # --- Вспомогательное ---------------------------------------------------

    def notify(self, tool: str) -> None:
        """Событие `progress(tool, <фраза>)` перед вызовом инструмента — 110 §4."""
        if self.on_progress is not None:
            self.on_progress("tool", TOOL_PROGRESS[tool])

    def notify_check(self) -> None:
        """Событие `progress(check, <фраза>)` после ответа модели — 110 §4."""
        if self.on_progress is not None:
            self.on_progress("check", CHECK_PROGRESS)

    def record(self, entry: trace_builder.AgentTrace | None) -> None:
        """Добавить шаг трассы, если документ задаёт шаг для этого действия."""
        if entry is not None:
            self.trace.append(entry)

    def found_cards(self) -> list[MovieCard]:
        """Карточки, из которых модель могла выбирать фильмы в этом ходе."""
        return list(self.found.values())

    @staticmethod
    def _error(error: ToolError) -> dict[str, dict[str, str]]:
        """Результат инструмента для модели при её ошибке — contracts.md §6, п. 1."""
        return error.to_result()

    def _automatic_excludes(self) -> list[tuple[str, float]]:
        """Автоматические запреты в порядке отбрасывания — ADR-051.

        Возвращаются пары «жанр, вес» по возрастанию веса: сначала профильные
        запреты с наименьшим |weight|, затем запреты настроения (их вес 0).
        Жанры из `genre_wishes` не запрещаются: явное пожелание сильнее правила.
        Повторы схлопываются — жанр может быть и в настроении, и в профиле.
        """
        wished = {normalize_genre(name) for name in self.context.genre_wishes or []}
        mood_bans, _runtime = MOOD_CRITERIA.get(self.context.mood or "", ((), None))
        profile_bans = [
            name
            for name, weight in self.profile.genre_weights.items()
            if weight <= PROFILE_BAN_THRESHOLD
        ]
        # Порядок отбрасывания: профильные по возрастанию |weight|, затем
        # запреты настроения (ADR-051).
        profile_bans.sort(key=lambda name: abs(self.profile.genre_weights[name]))

        result: list[tuple[str, float]] = []
        seen: set[str] = set()
        for name in [*profile_bans, *mood_bans]:
            key = normalize_genre(name)
            canonical = self._canonical.get(key)
            if canonical is None or key in wished or key in seen:
                continue
            seen.add(key)
            result.append((canonical, abs(self.profile.genre_weights.get(name, 0.0))))
        return result

    def _effective_runtime(self, requested: int | None) -> int | None:
        """Длительность: время пользователя жёстко, иначе — таблица настроения.

        Ослабление критериев (120 §2.1) не трогает `max_runtime`, поэтому
        запрошенное моделью значение может только уменьшить ограничение.
        """
        if self.context.available_minutes is not None:
            return self.context.available_minutes
        _bans, mood_runtime = MOOD_CRITERIA.get(self.context.mood or "", ((), None))
        if mood_runtime is None:
            return requested
        if requested is None:
            return mood_runtime
        return min(requested, mood_runtime)

    def _exclude_genres(self, requested: list[str] | None) -> list[str] | None:
        """Объединить `exclude_genres` модели с запретами пользователя и кода."""
        merged: list[str] = []
        seen: set[str] = set()

        def add(name: str) -> None:
            key = normalize_genre(name)
            if key not in seen:
                seen.add(key)
                merged.append(name)

        for name in [*(requested or []), *(self.context.genre_avoid or [])]:
            add(self._canonical.get(normalize_genre(name), name))

        automatic = self._automatic_excludes()
        while len(merged) + len(automatic) > MAX_EXCLUDE_GENRES and automatic:
            # Отбрасываем самый слабый автоматический запрет: профильные идут
            # первыми по возрастанию |weight|, затем запреты настроения.
            # Пользовательские запреты не отбрасываются никогда (ADR-051).
            automatic.pop(0)
        for name, _weight in automatic:
            add(name)
        return merged or None

    # --- Инструменты: 130 §4 ----------------------------------------------

    def get_user_ratings(
        self,
        limit: Annotated[int, Field(ge=1, le=50, description="Сколько оценок вернуть")] = 20,
    ) -> list[RatedMovie] | dict[str, dict[str, str]]:
        """Последние оценки пользователя: что смотрел и как оценил."""
        self.notify("get_user_ratings")
        try:
            ratings = self.data.get_user_ratings(self.user_id, limit)
        except ToolError as error:
            return self._error(error)
        self.record(
            trace_builder.trace_entry(
                "get_user_ratings",
                f"get_user_ratings(limit: {limit})",
                f"оценок: {len(ratings)}",
            )
        )
        return ratings

    def get_user_profile(self) -> PreferenceProfile:
        """Веса жанров и актёров из оценок пользователя.

        Вызывай, только если человек спрашивает о своём вкусе; для подборки
        профиль уже подставлен в промпт.
        """
        self.notify("get_user_profile")
        profile = self.data.get_user_profile(self.user_id)
        self.record(
            trace_builder.trace_entry(
                "get_user_profile",
                "get_user_profile()",
                f"оценок: {profile.ratings_count}, жанров: {len(profile.genre_weights)}",
            )
        )
        return profile

    def get_weather(self) -> Weather | None:
        """Погода в городе пользователя; null — данных нет, тогда погоду не упоминай."""
        self.notify("get_weather")
        weather = self.data.get_weather(self.user_id)
        self.record(
            trace_builder.trace_entry(
                "get_weather", "get_weather()", trace_builder.weather_summary(weather)
            )
        )
        return weather

    def find_movies(
        self,
        genres: Annotated[
            list[str] | None,
            Field(
                max_length=5,
                description=(
                    "Желаемые жанры (genre_wishes), русские названия из справочника; "
                    "достаточно совпадения с одним"
                ),
            ),
        ] = None,
        exclude_genres: Annotated[
            list[str] | None,
            Field(
                max_length=5,
                description=(
                    "Запрещённые жанры (genre_avoid); фильм с любым из них не возвращается"
                ),
            ),
        ] = None,
        year_from: Annotated[int | None, Field(ge=1900)] = None,
        year_to: Annotated[int | None, Field(le=2030)] = None,
        min_rating: Annotated[float | None, Field(ge=0, le=10)] = None,
        max_runtime: Annotated[
            int | None, Field(ge=30, le=600, description="Максимальная длительность в минутах")
        ] = None,
        limit: Annotated[int, Field(ge=3, le=30)] = 15,
    ) -> list[MovieCard] | dict[str, dict[str, str]]:
        """Найти фильмы по критериям.

        Жанры объединяются по ИЛИ, выше идут фильмы с большим числом
        совпавших жанров, затем по рейтингу. Порядок учитывает вкус
        пользователя: фильмы его любимых жанров идут выше. Уже показанные в
        сессии и оценённые пользователем исключаются автоматически. Все
        параметры необязательные.
        """
        self.notify("find_movies")
        exclude = self._exclude_genres(exclude_genres)
        runtime = self._effective_runtime(max_runtime)
        try:
            movies = self.data.find_movies(
                user_id=self.user_id,
                genres=genres,
                exclude_genres=exclude,
                year_from=year_from,
                year_to=year_to,
                min_rating=min_rating,
                max_runtime=runtime,
                exclude_ids=list(self.state.shown_ids),
                mood=self.context.mood,
                limit=limit,
            )
        except ToolError as error:
            return self._error(error)
        except ServiceError:
            # CATALOG_EMPTY и DATA_UNAVAILABLE получает пользователь: их
            # обрабатывает оркестратор (contracts.md §6).
            raise

        self.found.update((card.movie_id, card) for card in movies)
        self.best_found = max(self.best_found, len(movies))
        self.record(
            trace_builder.trace_entry(
                "find_movies",
                trace_builder.find_movies_action(
                    genres=genres,
                    exclude_genres=exclude,
                    year_from=year_from,
                    year_to=year_to,
                    min_rating=min_rating,
                    max_runtime=runtime,
                ),
                f"найдено {len(movies)} фильмов",
            )
        )
        return movies

    def get_movie_with_credits(
        self, movie_id: Annotated[int, Field(description="Идентификатор фильма из каталога")]
    ) -> MovieDetails | dict[str, dict[str, str]]:
        """Карточка фильма с актёрами и описанием."""
        self.notify("get_movie_with_credits")
        try:
            details = self.data.get_movie_with_credits(movie_id)
        except ToolError as error:
            return self._error(error)
        self.record(
            trace_builder.trace_entry(
                "get_movie_with_credits",
                f"get_movie_with_credits(movie_id: {movie_id})",
                f"карточка «{details.title}»",
            )
        )
        return details

    def search_movie(
        self,
        title: Annotated[str, Field(description="Название фильма, как его назвал человек")],
        year: Annotated[int | None, Field(ge=1900, le=2030)] = None,
    ) -> list[MovieSummary] | dict[str, dict[str, str]]:
        """Найти фильм по названию, не больше пяти совпадений."""
        self.notify("search_movie")
        try:
            found = self.data.search_movie(title, year)
        except ToolError as error:
            return self._error(error)
        self.record(
            trace_builder.trace_entry(
                "search_movie",
                f"search_movie(title: {title})",
                f"найдено {len(found)}",
            )
        )
        return found

    def save_choice(
        self, movie_id: Annotated[int, Field(description="Фильм из последней подборки")]
    ) -> dict[str, int] | dict[str, dict[str, str]]:
        """Сохранить выбор фильма из последней подборки."""
        self.notify("save_choice")
        if movie_id not in self.state.last_shown_ids:
            # Фильм выбирают следующим сообщением, когда last_shown_ids — уже
            # прошлая подборка (data/130 §4.7).
            return self._error(
                ToolError("NOT_FOUND", "Фильм не входит в последнюю подборку. Уточните выбор.")
            )
        try:
            result = self.data.save_choice(self.user_id, movie_id, mood=self.context.mood)
        except ToolError as error:
            return self._error(error)
        self.pending = PendingChoice(choice_id=result["choice_id"], movie_id=movie_id)
        return result

    def save_rating(
        self,
        movie_id: Annotated[int, Field(description="Идентификатор фильма из каталога")],
        rating: Annotated[int, Field(ge=1, le=10, description="Оценка от 1 до 10")],
        mood_match: Annotated[str | None, Field(description="yes, partly, no или null")] = None,
    ) -> dict[str, Any] | dict[str, dict[str, str]]:
        """Сохранить оценку 1–10 и подошёл ли фильм под настроение."""
        self.notify("save_rating")
        # `source` подставляет код: после выбора из подборки — after_choice,
        # иначе — manual (130 §4, п. 6).
        source = (
            "after_choice"
            if self.pending is not None and self.pending.movie_id == movie_id
            else "manual"
        )
        try:
            result = self.data.save_rating(
                user_id=self.user_id,
                movie_id=movie_id,
                rating=rating,
                source=source,
                mood_match=mood_match,
            )
        except (ToolError, ValidationError, ValueError) as error:
            if isinstance(error, ToolError):
                return self._error(error)
            return self._error(ToolError("INVALID_PARAM", "mood_match: недопустимое значение"))
        if self.pending is not None and self.pending.movie_id == movie_id:
            self.pending = None
        return result

    def close_choice(self) -> dict[str, bool] | dict[str, dict[str, str]]:
        """Закрыть неоценённый выбор без оценки."""
        self.notify("close_choice")
        if self.pending is None:
            return self._error(ToolError("NOT_FOUND", "Ожидающего выбора нет."))
        choice_id = self.pending.choice_id
        try:
            result = self.data.close_choice(choice_id, status="dismissed")
        except ToolError as error:
            return self._error(error)
        self.pending = None
        return result

    def tools(self) -> list[Callable[..., Any]]:
        """Список инструментов агента в порядке описания 130 §4."""
        return [
            self.get_user_ratings,
            self.get_user_profile,
            self.get_weather,
            self.find_movies,
            self.get_movie_with_credits,
            self.search_movie,
            self.save_choice,
            self.save_rating,
            self.close_choice,
        ]
