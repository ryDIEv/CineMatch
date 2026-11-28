"""Сборка трассы решений — contracts.md §2.7, agents/120 §5.

Шаг, `action` и `summary` формирует код из имени инструмента и его результата;
модель их не пишет (130 §4, п. 7). Перечень шагов закрыт (`TraceStep`), поэтому
здесь есть только инструменты, для которых документ задаёт шаг:

| Инструмент | Шаг |
|---|---|
| `extract_context` | «Контекст» |
| `get_weather` | «Погода» |
| `get_user_ratings`, `get_user_profile` | «Оценки» |
| `find_movies`, `search_movie`, `get_movie_with_credits` | «Поиск» |
| проверка ответа | «Проверка» |

Инструменты записи (`save_choice`, `save_rating`, `close_choice`) в таблице
contracts §2.7 шага не имеют, поэтому записей в трассе не создают.

В трассу не попадают системные промпты, ключи, `user_id` и служебные пути.
"""

from __future__ import annotations

from cinematch.data.schemas import Weather
from cinematch.overall.contracts import AgentTrace, EveningContext, TraceStep

# Шаг трассы по имени инструмента — contracts.md §2.7.
TOOL_STEPS: dict[str, TraceStep] = {
    "extract_context": "Контекст",
    "get_weather": "Погода",
    "get_user_ratings": "Оценки",
    "get_user_profile": "Оценки",
    "find_movies": "Поиск",
    "search_movie": "Поиск",
    "get_movie_with_credits": "Поиск",
}

# Погода: английский код модуля данных → русская формулировка (data/130 §8.2).
WEATHER_CONDITIONS: dict[str, str] = {
    "clear": "ясно",
    "cloudy": "облачно",
    "fog": "туман",
    "drizzle": "морось",
    "rain": "дождь",
    "snow": "снег",
    "thunderstorm": "гроза",
    "unknown": "погода без уточнения",
}

# Настроение и компания — значения `EveningContext` (contracts.md §2.4).
MOOD_WORDS: dict[str, str] = {
    "calm": "спокойное настроение",
    "cheerful": "весёлое настроение",
    "sad": "грустное настроение",
    "tired": "устал",
    "tense": "напряжённый день",
    "other": "настроение без уточнения",
}

COMPANY_WORDS: dict[str, str] = {
    "alone": "один",
    "partner": "с партнёром",
    "friends": "с друзьями",
    "family": "с семьёй",
}


def context_summary(context: EveningContext) -> str:
    """Сводка контекста вечера для шага «Контекст» — пример 130 §3.

    Например: «устал, 90 минут, один, без ужасов».
    """
    parts: list[str] = []
    if context.mood is not None:
        parts.append(MOOD_WORDS[context.mood])
    if context.available_minutes is not None:
        parts.append(f"{context.available_minutes} минут")
    if context.company is not None:
        parts.append(COMPANY_WORDS[context.company])
    if context.genre_wishes:
        parts.append("жанры: " + ", ".join(name.lower() for name in context.genre_wishes))
    if context.genre_avoid:
        parts.append("без " + ", ".join(name.lower() for name in context.genre_avoid))
    if context.notes is not None:
        parts.append(context.notes)
    return ", ".join(parts) if parts else "контекст не уточнён"


def weather_summary(weather: Weather | None) -> str:
    """Сводка погоды для шага «Погода»: «Москва, дождь, +9°» (130 §3).

    `None` — город не задан или сервис недоступен (data/130 §4.3).
    """
    if weather is None:
        return "погода недоступна"
    condition = WEATHER_CONDITIONS.get(weather.condition, WEATHER_CONDITIONS["unknown"])
    temperature = f"{weather.temp_c:+.0f}".replace("-", "−")
    return f"{weather.city}, {condition}, {temperature}°"


def find_movies_action(
    *,
    genres: list[str] | None,
    exclude_genres: list[str] | None,
    year_from: int | None,
    year_to: int | None,
    min_rating: float | None,
    max_runtime: int | None,
) -> str:
    """Действие шага «Поиск»: критерии, с которыми фильмы реально искались.

    Параметры — уже с подстановкой кода (`exclude_ids`, `mood`, автоматические
    запреты), поэтому трасса показывает то, что получил модуль данных.
    """
    parts: list[str] = []
    if genres:
        parts.append("жанры: " + ", ".join(name.lower() for name in genres))
    if exclude_genres:
        parts.append("без " + ", ".join(name.lower() for name in exclude_genres))
    if year_from is not None and year_to is not None:
        parts.append(f"годы: {year_from}–{year_to}")
    elif year_from is not None:
        parts.append(f"с {year_from} года")
    elif year_to is not None:
        parts.append(f"до {year_to} года")
    if min_rating is not None:
        parts.append(f"рейтинг ≥ {min_rating:g}")
    if max_runtime is not None:
        parts.append(f"длительность ≤ {max_runtime}")
    return "find_movies(" + "; ".join(parts) + ")" if parts else "find_movies()"


def step_for(tool: str) -> TraceStep | None:
    """Шаг трассы для инструмента; `None` — инструмент записи без шага."""
    return TOOL_STEPS.get(tool)


def trace_entry(tool: str, action: str, summary: str) -> AgentTrace | None:
    """Запись трассы для инструмента или `None`, если шага нет в документе."""
    step = step_for(tool)
    if step is None:
        return None
    return AgentTrace(step=step, action=action, summary=summary)
