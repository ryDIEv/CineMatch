"""Агент CineMatch на Pydantic AI — ADR-026, ADR-027, ADR-045, ADR-048.

Один агент со всеми инструментами (ADR-026): модель сама выбирает вызовы, код
исполняет их и проверяет ответ. Тексты промптов — только файлы `prompts/`
(125, ADR-037), строк промпта в коде нет.

Что делает модуль:

* собирает `system_agent.md`, подставляя плейсхолдеры таблицы 125 §2:
  `today`, `city`, `genres`, `questions_left`, `available_minutes`,
  `pending_feedback` и `profile` (ADR-048);
* отдаёт агенту инструменты реестра (`tools.py`) и `extract_context` —
  отдельный вызов модели с ответом по схеме `EveningContext` (ADR-026);
* сливает контекст вечера по правилам 120 §2.0: `null` — «не сказано»,
  `[]` — очистить, непустой список — заменить (ADR-044);
* делает ровно один повтор при невалидном ответе с `retry_invalid_output.md`,
  затем поднимает `LLM_INVALID_OUTPUT` (130 §4, п. 8; ADR-040);
* возвращает `AgentOutput`, найденные карточки, трассу и итоговый контекст;
  вызовы модели и токены считает клиент модели (`llm.py`, NFR-AG-10,
  NFR-AG-13).

Проверка ответа — `guardrails.py`; этапы сессии, согласие, город и удаление —
оркестратор (146).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage

from cinematch.agents.guardrails import validate_output
from cinematch.agents.llm import LlmModels
from cinematch.agents.prompt_loader import PromptLibrary
from cinematch.agents.tools import ToolRegistry
from cinematch.agents.trace import context_summary
from cinematch.data.schemas import PreferenceProfile
from cinematch.overall.contracts import (
    AgentOutput,
    AgentTrace,
    EveningContext,
    MovieCard,
    PendingChoice,
    SessionState,
)
from cinematch.overall.errors import ErrorCode, ServiceError, ToolError

# Значения плейсхолдеров по умолчанию — таблица 125 §2.
PROFILE_EMPTY = "пусто"
CITY_UNKNOWN = "не указан"
MINUTES_UNKNOWN = "не указано"
PENDING_NONE = "нет"

# Вопросов до подборки — 2 (contracts.md §8): остаток = 2 − clarifications_asked.
QUESTIONS_BEFORE_PICK = 2

# Причина повтора, когда ответ не разобрался как `AgentOutput` — 120 §5, п. 1.
SCHEMA_REASON = "ответ не соответствует схеме AgentOutput"

# Месяцы для `{today}`: формат «8 ноября 2026» (125 §2).
MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

# Знаки весов профиля: «Драма +0,352; Ужасы −0,222» (125 §2, ADR-048).
PLUS = "+"
MINUS = "−"


class InvalidOutput(Exception):
    """Ответ не прошёл проверку; вызывающий решает про повтор (130 §4, п. 8)."""

    def __init__(self, reason: str, messages: list[ModelMessage]) -> None:
        self.reason = reason
        self.messages = messages
        super().__init__(reason)


def format_today(moment: date) -> str:
    """Дата для `{today}`: «8 ноября 2026» (125 §2)."""
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year}"


def format_profile(profile: PreferenceProfile | None) -> str:
    """Плейсхолдер `{profile}`: веса жанров или «пусто» при холодном старте.

    Формат — 125 §2: «Драма +0,352; Комедия +0,089; Ужасы −0,222»; десятичный
    разделитель — запятая, минус — типографский (`−`). Порядок ключей задаёт
    модуль данных: |weight| по убыванию (ADR-013, п. 5).
    """
    if profile is None or not profile.genre_weights:
        return PROFILE_EMPTY
    parts: list[str] = []
    for name, weight in profile.genre_weights.items():
        sign = MINUS if weight < 0 else PLUS
        parts.append(f"{name} {sign}{abs(weight):.3f}".replace(".", ","))
    return "; ".join(parts)


def merge_context(current: EveningContext | None, update: EveningContext) -> EveningContext:
    """Слить новый контекст с накопленным — 120 §2.0, ADR-044.

    Скаляры: непустое значение заменяет прежнее, `null` оставляет прежнее.
    Жанровые списки: `null` оставляет прежний, `[]` очищает, непустой список
    заменяет прежний.
    """
    merged = (current or EveningContext()).model_dump()

    for name in ("mood", "energy", "available_minutes", "company", "notes"):
        value = getattr(update, name)
        if value is not None:
            merged[name] = value

    for name in ("genre_wishes", "genre_avoid"):
        value = getattr(update, name)
        if value is not None:
            merged[name] = list(value)

    return EveningContext.model_validate(merged)


@dataclass(slots=True)
class AgentTurn:
    """Результат хода агента для оркестратора (146 §6).

    `messages` — история модели для `SessionState.messages`, `pending` — кэш
    неоценённого выбора после `save_choice`, `movies` — карточки всех поисков
    хода: из них оркестратор собирает `RecommendedMovie` по `movie_ids` ответа
    (contracts.md §2.2).
    """

    output: AgentOutput
    movies: list[MovieCard] = field(default_factory=list)
    trace: list[AgentTrace] = field(default_factory=list)
    context: EveningContext = field(default_factory=EveningContext)
    pending: PendingChoice | None = None
    messages: list[ModelMessage] = field(default_factory=list)


class CineMatchAgent:
    """Агент CineMatch: промпт из файла, инструменты реестра, один повтор."""

    def __init__(self, models: LlmModels, prompts: PromptLibrary, data: Any) -> None:
        self.models = models
        self.prompts = prompts
        self.data = data

    # --- Промпт -----------------------------------------------------------

    def system_prompt(
        self,
        *,
        today: str,
        city: str,
        questions_left: int,
        context: EveningContext,
        profile: PreferenceProfile | None,
        pending_feedback: str = PENDING_NONE,
    ) -> str:
        """Собрать `system_agent.md` по таблице плейсхолдеров 125 §2."""
        minutes = (
            str(context.available_minutes)
            if context.available_minutes is not None
            else MINUTES_UNKNOWN
        )
        return self.prompts.render(
            "system_agent.md",
            today=today,
            city=city or CITY_UNKNOWN,
            genres=", ".join(self.data.get_genres()),
            questions_left=questions_left,
            available_minutes=minutes,
            pending_feedback=pending_feedback,
            profile=format_profile(profile),
        )

    @staticmethod
    def context_block(context: EveningContext) -> str:
        """Накопленный контекст вечера отдельной частью системного сообщения.

        Плейсхолдера для контекста в таблице 125 нет, а 146 §6, п. 1 требует
        передать агенту накопленный `context`; блок повторяет данные сессии и
        не содержит собственных указаний.
        """
        return f"Что уже известно о вечере: {context_summary(context)}."

    # --- extract_context --------------------------------------------------

    async def extract(self, message: str) -> EveningContext:
        """Контекст вечера из сообщения — отдельный вызов модели (ADR-026).

        Промпт `extract_context.md` (125 §5), модель `LLM_CLASSIFIER_MODEL`,
        температура 0 (125 §1; ADR-040). Невалидный ответ — один повтор, затем
        `INVALID_PARAM`: его получает модель как результат инструмента, ход
        продолжается без нового контекста.
        """
        system = self.prompts.render("extract_context.md", genres=", ".join(self.data.get_genres()))
        # Повтор делает код, а не Pydantic AI: он должен быть ровно один.
        extractor: Agent[None, EveningContext] = Agent(
            output_type=EveningContext,
            system_prompt=system,
            retries={"output": 0},
            name="extract-context",
        )
        last_error: UnexpectedModelBehavior | None = None
        for _attempt in range(2):
            try:
                result = await extractor.run(message, model=self.models.classifier)
            except UnexpectedModelBehavior as error:
                last_error = error
                continue
            return result.output
        raise ToolError(
            ErrorCode.INVALID_PARAM,
            "extract_context: не удалось разобрать контекст вечера",
        ) from last_error

    def extract_tool(self, registry: ToolRegistry) -> Callable[..., Any]:
        """Инструмент `extract_context` для модели, привязанный к реестру хода."""

        async def extract_context(message: str) -> dict[str, Any]:
            """Извлечь контекст вечера из сообщения.

            Настроение, энергия, свободное время, компания, пожелания и запреты
            по жанрам, важные детали. Возвращает только то, что человек сказал
            в этом сообщении; накопленный контекст сливается кодом.
            """
            registry.notify("extract_context")
            try:
                update = await self.extract(message)
            except ToolError as error:
                return error.to_result()
            # Контекст обновляется до find_movies: настроение и профиль
            # превращаются в запреты жанров уже в этом ходе (ADR-051).
            registry.context = merge_context(registry.context, update)
            registry.record(
                AgentTrace(
                    step="Контекст",
                    action="extract_context()",
                    summary=context_summary(update),
                )
            )
            return registry.context.model_dump(exclude_none=True)

        return extract_context

    # --- Ход --------------------------------------------------------------

    def build(
        self,
        *,
        registry: ToolRegistry,
        state: SessionState,
        profile: PreferenceProfile | None,
        today: str | None = None,
        city: str | None = None,
        questions_left: int | None = None,
        pending_feedback: str = PENDING_NONE,
        movie_id: int | None = None,
    ) -> Agent[ToolRegistry, AgentOutput]:
        """Собрать агента хода: промпт, инструменты и тип ответа."""
        context = state.context or EveningContext()
        left = (
            questions_left
            if questions_left is not None
            else max(QUESTIONS_BEFORE_PICK - state.clarifications_asked, 0)
        )
        parts = [
            self.system_prompt(
                today=today or format_today(date.today()),
                city=city or CITY_UNKNOWN,
                questions_left=left,
                context=context,
                profile=profile,
                pending_feedback=pending_feedback,
            )
        ]
        if state.context is not None:
            parts.append(self.context_block(context))
        if movie_id is not None:
            # Подсказка при нажатии на карточку: реакцию выбирает модель (130 §1).
            parts.append(f"Человек нажал карточку фильма с movie_id={movie_id}.")

        return Agent(
            output_type=AgentOutput,
            system_prompt=parts,
            # Порядок — 130 §4: extract_context первым, затем инструменты данных.
            tools=[self.extract_tool(registry), *registry.tools()],
            deps_type=ToolRegistry,
            retries={"output": 0},
            name="cinematch",
        )

    async def run(
        self,
        *,
        user_id: str,
        message: str,
        state: SessionState,
        profile: PreferenceProfile | None = None,
        history: list[ModelMessage] | None = None,
        today: str | None = None,
        city: str | None = None,
        questions_left: int | None = None,
        pending_feedback: str = PENDING_NONE,
        movie_id: int | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> AgentTurn:
        """Провести один ход диалога и вернуть ответ, трассу и контекст."""
        registry = ToolRegistry(
            self.data,
            user_id=user_id,
            state=state,
            profile=profile,
            on_progress=_safe_progress(on_progress),
        )
        agent = self.build(
            registry=registry,
            state=state,
            profile=profile,
            today=today,
            city=city,
            questions_left=questions_left,
            pending_feedback=pending_feedback,
            movie_id=movie_id,
        )

        output, messages, reason = await self._first_attempt(agent, registry, message, history)
        # `check` — после ответа модели и до сборки ответа (146 §6, п. 7; 110 §4).
        registry.notify_check()

        if reason is None:
            reason = _reason(self._validate(registry, output))

        if reason is not None:
            output, messages = await self._retry(agent, registry, message, messages, reason)
            registry.notify_check()
            if self._validate(registry, output):
                raise ServiceError(ErrorCode.LLM_INVALID_OUTPUT)

        return AgentTurn(
            output=output,
            movies=registry.found_cards(),
            trace=list(registry.trace),
            context=registry.context,
            pending=registry.pending,
            messages=messages,
        )

    @staticmethod
    def _validate(registry: ToolRegistry, output: AgentOutput) -> list[str]:
        """Проверка ответа по состоянию хода — 120 §5, contracts.md §5."""
        return validate_output(
            output,
            found=registry.found_cards(),
            available=registry.best_found,
            context=registry.context,
        )

    async def _first_attempt(
        self,
        agent: Agent[ToolRegistry, AgentOutput],
        registry: ToolRegistry,
        message: str,
        history: list[ModelMessage] | None,
    ) -> tuple[AgentOutput, list[ModelMessage], str | None]:
        """Первый запрос; невалидная схема — причина повтора, а не ошибка.

        Невалидный ответ не показывается: вместо него ставится заглушка, а
        `reason` заставляет `run` сделать ровно один повтор (120 §5).
        """
        try:
            output, messages = await self._ask(agent, registry, message, history)
        except InvalidOutput as error:
            return _empty_output(), error.messages, SCHEMA_REASON
        return output, messages, None

    async def _retry(
        self,
        agent: Agent[ToolRegistry, AgentOutput],
        registry: ToolRegistry,
        message: str,
        history: list[ModelMessage],
        reason: str,
    ) -> tuple[AgentOutput, list[ModelMessage]]:
        """Второй запрос после невалидного ответа — `retry_invalid_output.md`."""
        instructions = self.prompts.render("retry_invalid_output.md", error=reason)
        try:
            return await self._ask(agent, registry, message, history, instructions=instructions)
        except InvalidOutput as error:
            raise ServiceError(ErrorCode.LLM_INVALID_OUTPUT) from error

    async def _ask(
        self,
        agent: Agent[ToolRegistry, AgentOutput],
        registry: ToolRegistry,
        message: str,
        history: list[ModelMessage] | None,
        *,
        instructions: str | None = None,
    ) -> tuple[AgentOutput, list[ModelMessage]]:
        """Один запрос к агенту с вызовом инструментов; ответ — `AgentOutput`."""
        run: Any = None
        try:
            async with agent.iter(
                message,
                model=self.models.agent,
                deps=registry,
                message_history=history,
                instructions=instructions,
            ) as run:
                async for _node in run:
                    pass
                assert run.result is not None
                return run.result.output, run.all_messages()
        except UnexpectedModelBehavior as error:
            messages = list(run.all_messages()) if run is not None else []
            raise InvalidOutput("ответ не соответствует схеме AgentOutput", messages) from error


def _reason(errors: list[str]) -> str | None:
    """Причины проверки одной строкой для `{error}`; пусто — ответ годен."""
    return "; ".join(errors) if errors else None


def _empty_output() -> AgentOutput:
    """Заглушка вместо невалидного первого ответа: её заменяет повтор."""
    return AgentOutput(kind="message", text="", movie_ids=[], reasons=[])


def _safe_progress(
    on_progress: Callable[[str, str], None] | None,
) -> Callable[[str, str], None] | None:
    """Ошибка обработчика прогресса не прерывает ход (contracts.md §3, п. 2)."""
    if on_progress is None:
        return None

    def emit(step: str, text: str) -> None:
        try:
            on_progress(step, text)
        except Exception:
            return

    return emit
