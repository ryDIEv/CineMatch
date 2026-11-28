"""Агент CineMatch — ADR-026, ADR-027, ADR-044, ADR-048, agents/120, agents/130.

Тесты не ходят в сеть: модель подменяется `FunctionModel`, модуль данных —
заглушкой `FakeDataModule` (AGENTS.md, documents/testing/ts-ag.md). Проверяется
то, за что отвечает код, а не модель: сборка промпта и плейсхолдеров, один
повтор при невалидном ответе, проверка ответа по выдаче `find_movies`,
слияние контекста, отсутствие лишних вызовов и пределы.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cinematch.agents.agent import (
    MINUTES_UNKNOWN,
    PENDING_NONE,
    PROFILE_EMPTY,
    AgentTurn,
    CineMatchAgent,
    format_profile,
    format_today,
    merge_context,
)
from cinematch.agents.guardrails import (
    MAX_REASON_LENGTH,
    MAX_TEXT_LENGTH,
    validate_output,
)
from cinematch.agents.llm import LlmModels, MeteredModel, TurnUsage
from cinematch.agents.prompt_loader import PromptLibrary, load_prompts
from cinematch.data.schemas import PreferenceProfile
from cinematch.overall.contracts import (
    AgentOutput,
    EveningContext,
    MovieCard,
    PendingChoice,
    SessionState,
)
from cinematch.overall.errors import ErrorCode, ServiceError, ToolError
from tests.fakes.fake_data import DEFAULT_USER_ID, fake_user_with_ratings


@pytest.fixture(scope="module")
def prompts() -> PromptLibrary:
    return load_prompts()


def _is_extractor(info: AgentInfo) -> bool:
    """Вызов `extract_context` узнаётся по схеме `EveningContext`."""
    properties = info.output_tools[0].parameters_json_schema.get("properties", {})
    return "mood" in properties


@dataclass
class Script:
    """Сценарий вызовов модели: шаги агента и ответы `extract_context`.

    Один шаг — `("tool", имя, аргументы)` или `("answer", AgentOutput)`.
    Когда шаги кончаются, модель отвечает последним ответом: так тест задаёт
    только то, что важно, и не зависит от числа внутренних вызовов.
    """

    steps: list[tuple[str, Any, Any]] = field(default_factory=list)
    contexts: list[dict[str, Any]] = field(default_factory=list)
    seen: list[list[ModelMessage]] = field(default_factory=list)
    calls: int = 0
    extract_calls: int = 0

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.seen.append(messages)
        output_tool = info.output_tools[0].name

        if _is_extractor(info):
            index = min(self.extract_calls, len(self.contexts) - 1)
            self.extract_calls += 1
            payload = self.contexts[index] if self.contexts else {}
            return ModelResponse(parts=[ToolCallPart(tool_name=output_tool, args=payload)])

        index = min(self.calls, len(self.steps) - 1)
        self.calls += 1
        step = self.steps[index] if self.steps else ("answer", None, None)

        if step[0] == "tool":
            return ModelResponse(parts=[ToolCallPart(tool_name=step[1], args=step[2])])
        if step[0] == "invalid":
            return ModelResponse(parts=[TextPart(content=str(step[1]))])

        output: AgentOutput = step[1]
        return ModelResponse(parts=[ToolCallPart(tool_name=output_tool, args=output.model_dump())])

    def tool_results(self) -> list[Any]:
        """Содержимое всех результатов инструментов в истории вызовов."""
        return [
            part.content
            for messages in self.seen
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]

    def system_texts(self) -> list[str]:
        """Тексты системных сообщений первого вызова агента."""
        for messages in self.seen:
            parts = [
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, SystemPromptPart)
            ]
            if parts:
                return parts
        return []


def _agent(
    script: Script,
    data: Any,
    prompts: PromptLibrary,
    *,
    usage: TurnUsage | None = None,
) -> CineMatchAgent:
    """Агент с одной и той же подменённой моделью для агента и классификатора."""
    turn_usage = usage if usage is not None else TurnUsage()
    model = MeteredModel(FunctionModel(script), turn_usage)
    return CineMatchAgent(LlmModels(agent=model, classifier=model), prompts, data)


def _answer(
    text: str = "Расскажите, какое у вас настроение?",
    *,
    kind: str = "question",
    movie_ids: list[int] | None = None,
    reasons: list[str] | None = None,
) -> AgentOutput:
    return AgentOutput(
        kind=kind,  # type: ignore[arg-type]
        text=text,
        movie_ids=movie_ids or [],
        reasons=reasons or [],
    )


async def test_full_selection_returns_three_movies_from_search(
    prompts: PromptLibrary,
) -> None:
    """Подбор: контекст → погода → поиск → ответ ровно тремя фильмами (TS-AG-02)."""
    data = fake_user_with_ratings()
    script = Script(
        contexts=[{"mood": "tired", "available_minutes": 90, "company": "alone"}],
        steps=[
            ("tool", "extract_context", {"message": "устал после пар, есть полтора часа"}),
            ("tool", "get_weather", {}),
            (
                "tool",
                "find_movies",
                {"genres": ["Драма", "Комедия"], "max_runtime": 90},
            ),
            ("answer", None, None),
        ],
    )
    # Последний ответ собирается из фактической выдачи, как это делает модель.
    found_ids = [
        card.movie_id for card in data.find_movies(user_id=DEFAULT_USER_ID, max_runtime=90)[:3]
    ]
    data.clear_calls()
    script.steps[-1] = (
        "answer",
        _answer(
            "Вот три спокойных фильма до полутора часов:",
            kind="recommendations",
            movie_ids=found_ids,
            reasons=["Подходит под вечер."] * 3,
        ),
        None,
    )
    agent = _agent(script, data, prompts)

    turn = await agent.run(
        user_id=DEFAULT_USER_ID,
        message="устал после пар, есть полтора часа",
        state=SessionState(stage="dialog"),
        city="Москва",
        today="28 ноября 2026",
    )

    assert isinstance(turn, AgentTurn)
    assert turn.output.kind == "recommendations"
    assert turn.output.movie_ids == found_ids
    assert set(found_ids) <= {card.movie_id for card in turn.movies}
    assert turn.context.mood == "tired"
    assert turn.context.available_minutes == 90

    call = data.last_call("find_movies").arguments
    assert call["user_id"] == DEFAULT_USER_ID
    assert call["max_runtime"] == 90
    assert {"Триллер", "Ужасы"} <= set(call["exclude_genres"])
    assert call["mood"] == "tired"

    steps = [entry.step for entry in turn.trace]
    assert steps[:3] == ["Контекст", "Погода", "Поиск"]


async def test_prompt_placeholders_are_filled(prompts: PromptLibrary) -> None:
    """Плейсхолдеры `{today}`, `{city}`, `{profile}` и времени — по 125 §2."""
    data = fake_user_with_ratings()
    script = Script(steps=[("answer", _answer(), None)])
    agent = _agent(script, data, prompts)
    profile = PreferenceProfile(
        genre_weights={"Драма": 0.352, "Ужасы": -0.222}, actor_weights={}, ratings_count=4
    )

    await agent.run(
        user_id=DEFAULT_USER_ID,
        message="привет",
        state=SessionState(stage="dialog"),
        profile=profile,
        city="Казань",
        today="8 ноября 2026",
    )

    prompt = script.system_texts()[0]
    assert "Сегодня: 8 ноября 2026." in prompt
    assert "Город пользователя: Казань." in prompt
    assert "Драма +0,352; Ужасы −0,222" in prompt
    assert "{today}" not in prompt
    assert "{profile}" not in prompt


async def test_profile_is_not_requested_by_tool_during_selection(
    prompts: PromptLibrary,
) -> None:
    """Для подборки профиль подставлен кодом; `get_user_profile` не вызывается."""
    data = fake_user_with_ratings(scenario="ts-ag-13")
    profile = data.get_user_profile(DEFAULT_USER_ID)
    data.clear_calls()
    script = Script(
        steps=[
            ("tool", "find_movies", {"genres": ["Драма"]}),
            ("answer", _answer("Вот что нашлось:", kind="message"), None),
        ]
    )
    agent = _agent(script, data, prompts)

    await agent.run(
        user_id=DEFAULT_USER_ID,
        message="что-нибудь на вечер",
        state=SessionState(stage="dialog"),
        profile=profile,
    )

    assert data.calls_of("get_user_profile") == []
    exclude = data.last_call("find_movies").arguments["exclude_genres"]
    assert "Ужасы" in exclude


async def test_question_about_taste_calls_profile_and_answers_it(
    prompts: PromptLibrary,
) -> None:
    """TS-AG-16: «что я люблю?» → `get_user_profile`, ответ по положительным весам."""
    data = fake_user_with_ratings(scenario="ts-ag-13")
    profile = data.get_user_profile(DEFAULT_USER_ID)
    positive = [name for name, weight in profile.genre_weights.items() if weight > 0]
    data.clear_calls()
    script = Script(
        steps=[
            ("tool", "get_user_profile", {}),
            (
                "answer",
                _answer(
                    "Вы любите " + ", ".join(name.lower() for name in positive) + ".",
                    kind="message",
                ),
                None,
            ),
        ]
    )
    agent = _agent(script, data, prompts)

    turn = await agent.run(
        user_id=DEFAULT_USER_ID,
        message="Что я люблю?",
        state=SessionState(stage="dialog"),
        profile=profile,
    )

    assert len(data.calls_of("get_user_profile")) == 1
    returned = script.tool_results()[0]
    assert set(returned.genre_weights) == set(profile.genre_weights)
    assert positive, "сценарий TS-AG-13 даёт положительный вес драме"
    for name in positive:
        assert name.lower() in turn.output.text.lower()
    assert "Ужасы" not in turn.output.text


async def test_empty_profile_question_gets_honest_answer(prompts: PromptLibrary) -> None:
    """Пустой профиль — «оценок ещё нет», а не выдуманный вкус (TS-AG-16)."""
    data = fake_user_with_ratings()
    profile = data.get_user_profile(DEFAULT_USER_ID)
    data.clear_calls()
    script = Script(
        steps=[
            ("tool", "get_user_profile", {}),
            (
                "answer",
                _answer("Оценок пока нет, вкус ещё не из чего построить.", kind="message"),
                None,
            ),
        ]
    )
    agent = _agent(script, data, prompts)

    turn = await agent.run(
        user_id=DEFAULT_USER_ID,
        message="Что я люблю?",
        state=SessionState(stage="dialog"),
        profile=profile,
    )

    returned = script.tool_results()[0]
    assert returned.genre_weights == {}
    assert returned.ratings_count == 0
    assert "оценок" in turn.output.text.lower()


async def test_invalid_output_is_retried_once_with_retry_prompt(
    prompts: PromptLibrary,
) -> None:
    """Невалидный ответ — один повтор с `retry_invalid_output.md` (130 §4, п. 8)."""
    data = fake_user_with_ratings()
    script = Script(
        steps=[
            ("invalid", "это не JSON", None),
            ("answer", _answer("Вот что можно посмотреть?", kind="question"), None),
        ]
    )
    agent = _agent(script, data, prompts)

    turn = await agent.run(
        user_id=DEFAULT_USER_ID,
        message="привет",
        state=SessionState(stage="dialog"),
    )

    assert turn.output.text == "Вот что можно посмотреть?"
    assert script.calls == 2
    # Второй запрос несёт инструкцию повтора с причиной.
    retry_instructions = [
        getattr(message, "instructions", None)
        for message in script.seen[-1]
        if getattr(message, "instructions", None)
    ]
    assert retry_instructions
    assert "не прошёл проверку" in retry_instructions[0]


async def test_second_invalid_output_raises_llm_invalid_output(
    prompts: PromptLibrary,
) -> None:
    """Второй невалидный ответ пользователю не показывается — `LLM_INVALID_OUTPUT`."""
    data = fake_user_with_ratings()
    script = Script(
        steps=[
            ("invalid", "первый мусор", None),
            ("invalid", "второй мусор", None),
        ]
    )
    agent = _agent(script, data, prompts)

    with pytest.raises(ServiceError) as error:
        await agent.run(
            user_id=DEFAULT_USER_ID,
            message="привет",
            state=SessionState(stage="dialog"),
        )

    assert error.value.code is ErrorCode.LLM_INVALID_OUTPUT
    assert script.calls == 2


async def test_movie_outside_search_is_rejected_and_retried(
    prompts: PromptLibrary,
) -> None:
    """Фильм вне `find_movies` — ошибка проверки, один повтор (contracts §5, п. 5)."""
    data = fake_user_with_ratings()
    script = Script(
        steps=[
            ("tool", "find_movies", {"genres": ["Драма"]}),
            (
                "answer",
                _answer(
                    "Вот:",
                    kind="recommendations",
                    movie_ids=[999],
                    reasons=["Выдуманный фильм."],
                ),
                None,
            ),
            (
                "answer",
                _answer(
                    "Вот:",
                    kind="recommendations",
                    movie_ids=[999],
                    reasons=["Выдуманный фильм."],
                ),
                None,
            ),
        ]
    )
    agent = _agent(script, data, prompts)

    with pytest.raises(ServiceError) as error:
        await agent.run(
            user_id=DEFAULT_USER_ID,
            message="что-нибудь",
            state=SessionState(stage="dialog"),
        )

    assert error.value.code is ErrorCode.LLM_INVALID_OUTPUT
    # Причина повтора называет именно отсутствие фильма в выдаче.
    retry = [
        getattr(message, "instructions", None)
        for message in script.seen[-1]
        if getattr(message, "instructions", None)
    ]
    assert retry and "movie_ids" in retry[0]


def test_merge_context_scalars_keep_previous_values() -> None:
    """Пример 1 из 120 §2.0: «нет, два часа» меняет только время."""
    current = EveningContext(mood="tired", available_minutes=90, genre_avoid=["Ужасы"])

    merged = merge_context(current, EveningContext(available_minutes=120))

    assert merged.mood == "tired"
    assert merged.available_minutes == 120
    assert merged.genre_avoid == ["Ужасы"]


def test_merge_context_empty_list_lifts_previous_ban() -> None:
    """Пример 2 из 120 §2.0: «ладно, ужасы можно» очищает запрет."""
    current = EveningContext(genre_avoid=["Ужасы"], mood="calm")

    merged = merge_context(current, EveningContext(genre_avoid=[]))

    assert merged.genre_avoid == []
    assert merged.mood == "calm"


def test_merge_context_empty_list_clears_wishes() -> None:
    """Пример 3 из 120 §2.0: «давай любой жанр» очищает пожелания."""
    merged = merge_context(EveningContext(genre_wishes=["Драма"]), EveningContext(genre_wishes=[]))

    assert merged.genre_wishes == []


def test_merge_context_non_empty_list_replaces_previous() -> None:
    """Непустой список заменяет прежний, а не дополняет его."""
    merged = merge_context(
        EveningContext(genre_wishes=["Драма", "Комедия"]),
        EveningContext(genre_wishes=["Ужасы"]),
    )

    assert merged.genre_wishes == ["Ужасы"]


async def test_context_is_merged_with_session_state(prompts: PromptLibrary) -> None:
    """`extract_context` дополняет накопленный контекст, а не затирает его."""
    data = fake_user_with_ratings()
    state = SessionState(
        stage="dialog",
        context=EveningContext(mood="tired", available_minutes=90, genre_avoid=["Ужасы"]),
    )
    script = Script(
        contexts=[{"available_minutes": 120}],
        steps=[
            ("tool", "extract_context", {"message": "нет, два часа"}),
            ("answer", _answer("Хорошо, два часа. Что предпочитаете?", kind="question"), None),
        ],
    )
    agent = _agent(script, data, prompts)

    turn = await agent.run(
        user_id=DEFAULT_USER_ID,
        message="нет, два часа",
        state=state,
    )

    assert turn.context.mood == "tired"
    assert turn.context.available_minutes == 120
    assert turn.context.genre_avoid == ["Ужасы"]
    # Контекст из сессии передан агенту отдельным блоком.
    assert any("устал" in text for text in script.system_texts())


async def test_new_context_is_used_by_find_movies_in_same_turn(
    prompts: PromptLibrary,
) -> None:
    """Запреты настроения действуют уже в этом ходе (ADR-051, 146 §6, п. 3)."""
    data = fake_user_with_ratings()
    script = Script(
        contexts=[{"mood": "tired"}],
        steps=[
            ("tool", "extract_context", {"message": "устал"}),
            ("tool", "find_movies", {"genres": ["Драма"]}),
            ("answer", _answer("Вот что нашлось:", kind="message"), None),
        ],
    )
    agent = _agent(script, data, prompts)

    await agent.run(
        user_id=DEFAULT_USER_ID,
        message="устал",
        state=SessionState(stage="dialog"),
    )

    call = data.last_call("find_movies").arguments
    assert call["mood"] == "tired"
    assert {"Триллер", "Ужасы"} <= set(call["exclude_genres"])
    assert call["max_runtime"] == 100


async def test_weakening_keeps_runtime_and_bans(prompts: PromptLibrary) -> None:
    """Ослабление критериев не трогает время и запреты (120 §2.1)."""
    data = fake_user_with_ratings()
    state = SessionState(
        stage="dialog",
        context=EveningContext(available_minutes=95, genre_avoid=["Ужасы"]),
    )
    script = Script(
        steps=[
            ("tool", "find_movies", {"genres": ["Драма"], "year_from": 2020, "min_rating": 8}),
            ("tool", "find_movies", {"genres": ["Драма"], "year_from": 2020}),
            ("tool", "find_movies", {"genres": ["Драма"]}),
            (
                "answer",
                _answer("Подошёл только один фильм. Ослабить критерии?", kind="question"),
                None,
            ),
        ]
    )
    agent = _agent(script, data, prompts)

    await agent.run(
        user_id=DEFAULT_USER_ID,
        message="драма после 2020 года, на полтора часа",
        state=state,
    )

    calls = data.calls_of("find_movies")
    assert len(calls) == 3
    for call in calls:
        assert call.arguments["max_runtime"] == 95
        assert "Ужасы" in call.arguments["exclude_genres"]
    # Ослабление снимает годы и рейтинг, а не жанр и не время.
    assert calls[1].arguments["min_rating"] is None
    assert calls[2].arguments["year_from"] is None


async def test_search_without_results_allows_empty_answer(prompts: PromptLibrary) -> None:
    """Ноль результатов — ответ без карточек, а не выдуманные фильмы (120 §2.1)."""
    data = fake_user_with_ratings()
    script = Script(
        steps=[
            ("tool", "find_movies", {"year_from": 2027, "max_runtime": 40}),
            (
                "answer",
                _answer(
                    "По этим критериям ничего не нашлось. Можно убрать год или добавить времени.",
                    kind="message",
                ),
                None,
            ),
        ]
    )
    agent = _agent(script, data, prompts)

    turn = await agent.run(
        user_id=DEFAULT_USER_ID,
        message="комедия ужасов 2027 года на 40 минут",
        state=SessionState(stage="dialog"),
    )

    assert turn.movies == []
    assert turn.output.movie_ids == []


async def test_progress_events_follow_documented_steps(prompts: PromptLibrary) -> None:
    """События хода: `tool` перед инструментом, `check` после ответа (110 §4)."""
    data = fake_user_with_ratings()
    events: list[tuple[str, str]] = []
    script = Script(
        steps=[
            ("tool", "get_weather", {}),
            ("answer", _answer("Какая погода вам нравится?", kind="question"), None),
        ]
    )
    agent = _agent(script, data, prompts)

    await agent.run(
        user_id=DEFAULT_USER_ID,
        message="привет",
        state=SessionState(stage="dialog"),
        on_progress=lambda step, text: events.append((step, text)),
    )

    assert events[0] == ("tool", "Проверяю погоду…")
    assert events[-1] == ("check", "Проверяю подборку…")


async def test_progress_handler_error_does_not_break_turn(prompts: PromptLibrary) -> None:
    """Ошибка обработчика прогресса не прерывает ход (contracts §3, п. 2)."""
    data = fake_user_with_ratings()
    script = Script(steps=[("answer", _answer("Что смотрим?", kind="question"), None)])
    agent = _agent(script, data, prompts)

    def broken(step: str, text: str) -> None:
        raise RuntimeError("SSE закрыт")

    turn = await agent.run(
        user_id=DEFAULT_USER_ID,
        message="привет",
        state=SessionState(stage="dialog"),
        on_progress=broken,
    )

    assert turn.output.kind == "question"


async def test_model_calls_are_counted_per_turn(prompts: PromptLibrary) -> None:
    """Расход считается клиентом модели, включая `extract_context` (NFR-AG-10)."""
    data = fake_user_with_ratings()
    usage = TurnUsage()
    script = Script(
        contexts=[{"mood": "calm"}],
        steps=[
            ("tool", "extract_context", {"message": "спокойный вечер"}),
            ("tool", "find_movies", {}),
            ("answer", _answer("Вот что нашлось:", kind="message"), None),
        ],
    )
    agent = _agent(script, data, prompts, usage=usage)

    await agent.run(
        user_id=DEFAULT_USER_ID,
        message="спокойный вечер",
        state=SessionState(stage="dialog"),
    )

    assert usage.model_calls == 4  # три вызова агента и один extract_context
    assert usage.model_calls <= 8


async def test_pending_choice_is_returned_to_orchestrator(prompts: PromptLibrary) -> None:
    """`save_choice` обновляет кэш выбора для оркестратора (120 §3)."""
    data = fake_user_with_ratings()
    data.set_last_shown(DEFAULT_USER_ID, [1, 2, 3])
    state = SessionState(stage="dialog", last_shown_ids=[1, 2, 3])
    script = Script(
        steps=[
            ("tool", "save_choice", {"movie_id": 2}),
            ("answer", _answer("Хороший выбор. Приятного просмотра!", kind="message"), None),
        ]
    )
    agent = _agent(script, data, prompts)

    turn = await agent.run(
        user_id=DEFAULT_USER_ID,
        message="Беру второй",
        state=state,
        movie_id=2,
    )

    assert turn.pending == PendingChoice(choice_id=1, movie_id=2)
    assert data.last_call("save_choice").arguments["movie_id"] == 2


async def test_tool_error_reaches_model_as_result(prompts: PromptLibrary) -> None:
    """Ошибка инструмента — результат `{"error": ...}`, ход продолжается (§6)."""
    data = fake_user_with_ratings()
    script = Script(
        steps=[
            ("tool", "save_choice", {"movie_id": 9}),
            ("answer", _answer("Уточните, какой фильм вы выбрали?", kind="question"), None),
        ]
    )
    agent = _agent(script, data, prompts)

    turn = await agent.run(
        user_id=DEFAULT_USER_ID,
        message="беру девятый",
        state=SessionState(stage="dialog", last_shown_ids=[1, 2, 3]),
    )

    results = script.tool_results()
    assert results[0]["error"]["code"] == ErrorCode.NOT_FOUND
    assert turn.output.kind == "question"


async def test_extract_context_failure_becomes_tool_error(prompts: PromptLibrary) -> None:
    """Невалидный `extract_context` дважды — `INVALID_PARAM` модели, ход идёт дальше."""
    data = fake_user_with_ratings()

    class BrokenExtractor(Script):
        def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if _is_extractor(info):
                self.seen.append(messages)
                return ModelResponse(parts=[TextPart(content="не контекст")])
            return super().__call__(messages, info)

    script = BrokenExtractor(
        steps=[
            ("tool", "extract_context", {"message": "какой-то текст"}),
            ("answer", _answer("Что смотрим?", kind="question"), None),
        ]
    )
    agent = _agent(script, data, prompts)

    turn = await agent.run(
        user_id=DEFAULT_USER_ID,
        message="какой-то текст",
        state=SessionState(stage="dialog"),
    )

    assert script.extract_calls == 0
    results = script.tool_results()
    assert results[0]["error"]["code"] == ErrorCode.INVALID_PARAM
    assert turn.output.kind == "question"


def test_validate_output_rejects_too_long_reason() -> None:
    """Объяснение длиннее 240 символов — ошибка проверки (contracts §8)."""
    card = MovieCard(
        movie_id=1,
        tmdb_id=1,
        title="Фильм",
        vote_count=10,
        genres=["Драма"],
    )
    output = AgentOutput(
        kind="recommendations",
        text="Вот фильм:",
        movie_ids=[1],
        reasons=["я" * (MAX_REASON_LENGTH + 1)],
    )

    errors = validate_output(output, found=[card])

    assert any("reasons" in error for error in errors)


def test_validate_output_accepts_fewer_movies_when_search_is_short() -> None:
    """Меньше трёх допустимо, если поиск вернул меньше (contracts §5, п. 2)."""
    cards = [
        MovieCard(movie_id=index, tmdb_id=index, title="Фильм", vote_count=1, genres=["Драма"])
        for index in (1, 2)
    ]
    output = AgentOutput(
        kind="recommendations",
        text="Подошли два фильма.",
        movie_ids=[1, 2],
        reasons=["Раз.", "Два."],
    )

    assert validate_output(output, found=cards, available=2) == []


def test_validate_output_requires_three_movies_from_full_search() -> None:
    """Если поиск вернул три и больше, в ответе ровно три (contracts §5, п. 1)."""
    cards = [
        MovieCard(movie_id=index, tmdb_id=index, title="Фильм", vote_count=1, genres=["Драма"])
        for index in (1, 2, 3)
    ]
    output = AgentOutput(kind="recommendations", text="Вот один.", movie_ids=[1], reasons=["Раз."])

    errors = validate_output(output, found=cards, available=3)

    assert any("ровно 3" in error for error in errors)


def test_validate_output_rejects_text_over_limit() -> None:
    """Текст ответа не длиннее 1200 символов (contracts §8)."""
    output = AgentOutput(kind="message", text="я" * (MAX_TEXT_LENGTH + 1), movie_ids=[], reasons=[])

    errors = validate_output(output, found=[])

    assert any("text" in error for error in errors)


def test_format_profile_matches_placeholder_table() -> None:
    """`{profile}` — «Драма +0,352; Ужасы −0,222» или «пусто» (125 §2)."""
    assert format_profile(None) == PROFILE_EMPTY
    assert (
        format_profile(PreferenceProfile(genre_weights={}, actor_weights={}, ratings_count=0))
        == PROFILE_EMPTY
    )
    assert (
        format_profile(
            PreferenceProfile(
                genre_weights={"Драма": 0.352, "Ужасы": -0.222},
                actor_weights={},
                ratings_count=4,
            )
        )
        == "Драма +0,352; Ужасы −0,222"
    )


def test_format_today_is_russian_date() -> None:
    """`{today}` — «8 ноября 2026» (125 §2)."""
    assert format_today(date(2026, 11, 8)) == "8 ноября 2026"


def test_placeholder_defaults_match_documents() -> None:
    """Значения по умолчанию плейсхолдеров — 125 §2."""
    assert MINUTES_UNKNOWN == "не указано"
    assert PENDING_NONE == "нет"


def test_tool_error_for_model_codes_only() -> None:
    """`ToolError` принимает только коды для модели (contracts §6)."""
    with pytest.raises(ValueError):
        ToolError(ErrorCode.DATA_UNAVAILABLE, "не для модели")
