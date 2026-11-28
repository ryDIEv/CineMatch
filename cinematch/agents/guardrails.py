"""Проверка ответа агента и пределы — agents/120 §5, contracts.md §5 и §8.

Проверка не вызывает модель: она либо принимает ответ, либо возвращает
причины, с которыми оркестратор делает один повтор (`retry_invalid_output.md`),
а после второго невалидного ответа — `LLM_INVALID_OUTPUT` (ADR-040, 120 §5).

Порядок проверок — 120 §5:

1. схема `AgentOutput` — её проверяет Pydantic при разборе ответа модели;
2. все `movie_id` есть в результате `find_movies` текущего хода;
3. длина `text` ≤ 1200 и каждого `reason` ≤ 240;
4. число фильмов: ровно 3, если поиск вернул не меньше трёх, иначе — сколько
   вернул поиск;
5. в `kind = question` не больше одного вопроса;
6. ни один фильм не содержит жанр из `genre_avoid`; автоматические запреты
   приходят в том же `exclude_genres`, поэтому отдельного шага нет
   (120 §5, п. 7; ADR-051).

Свободное время не ослабляется никогда: фильм длиннее `available_minutes` —
ошибка проверки (contracts.md §5, п. 6; 120 §2.1).
"""

from __future__ import annotations

from cinematch.agents.tools import normalize_genre
from cinematch.overall.contracts import AgentOutput, EveningContext, MovieCard

# Пределы — contracts.md §8.
MAX_TEXT_LENGTH = 1200
MAX_REASON_LENGTH = 240
MOVIES_IN_RECOMMENDATION = 3


def validate_output(
    output: AgentOutput,
    *,
    found: list[MovieCard],
    available: int | None = None,
    context: EveningContext | None = None,
) -> list[str]:
    """Причины, по которым ответ не прошёл проверку; пусто — ответ годен.

    `found` — все карточки, которые `find_movies` вернул за этот ход: модель
    может искать повторно, ослабляя критерии (120 §2.1), поэтому допустимы
    фильмы из любого поиска хода. `available` — размер лучшей выдачи хода:
    по нему решается, требует ли документ ровно три фильма.
    """
    errors: list[str] = []
    by_id = {card.movie_id: card for card in found}
    shows_movies = bool(output.movie_ids) or output.kind == "recommendations"

    unknown = [movie_id for movie_id in output.movie_ids if movie_id not in by_id]
    if unknown:
        errors.append(
            "movie_ids: фильм "
            + ", ".join(str(movie_id) for movie_id in unknown)
            + " отсутствует в результате find_movies текущего хода"
        )

    if len(set(output.movie_ids)) != len(output.movie_ids):
        errors.append("movie_ids: фильмы не должны повторяться")

    if len(output.text) > MAX_TEXT_LENGTH:
        errors.append(f"text: длиннее {MAX_TEXT_LENGTH} символов")
    if any(len(reason) > MAX_REASON_LENGTH for reason in output.reasons):
        errors.append(f"reasons: каждый элемент не длиннее {MAX_REASON_LENGTH} символов")

    if len(output.reasons) != len(output.movie_ids):
        errors.append("reasons: длина должна совпадать с длиной movie_ids")

    search_size = len(found) if available is None else available
    if shows_movies and search_size >= MOVIES_IN_RECOMMENDATION:
        if len(output.movie_ids) != MOVIES_IN_RECOMMENDATION:
            errors.append(
                f"movie_ids: поиск вернул {search_size} фильмов, в ответе должно быть ровно "
                f"{MOVIES_IN_RECOMMENDATION}"
            )
    elif len(output.movie_ids) > search_size:
        errors.append(f"movie_ids: поиск вернул {search_size} фильмов, показать больше нельзя")

    if output.kind == "question" and output.text.count("?") > 1:
        errors.append("text: в вопросе не больше одного знака «?»")

    if context is not None:
        errors.extend(_context_errors(output, by_id, context))

    return errors


def _context_errors(
    output: AgentOutput, by_id: dict[int, MovieCard], context: EveningContext
) -> list[str]:
    """Запрещённые жанры и свободное время — жёсткие ограничения (contracts.md §5)."""
    errors: list[str] = []
    avoid = {normalize_genre(name) for name in context.genre_avoid or []}
    for movie_id in output.movie_ids:
        card = by_id.get(movie_id)
        if card is None:
            continue
        if avoid and {normalize_genre(name) for name in card.genres} & avoid:
            errors.append(f"movie_ids: фильм {movie_id} содержит запрещённый жанр")
        if (
            context.available_minutes is not None
            and card.runtime is not None
            and card.runtime > context.available_minutes
        ):
            errors.append(f"movie_ids: фильм {movie_id} длиннее {context.available_minutes} минут")
    return errors
