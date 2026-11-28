"""Клиент модели: подключение к RouterAI через Pydantic AI и учёт расхода.

Адрес, ключ и модели берутся только из конфигурации (`overall/config.md §1`),
смена модели не требует правки кода (NFR-AG-12). Здесь же ограничения
документов:

* тайм-аут одного запроса — `LLM_TIMEOUT_S`, ровно один повтор на ошибку
  модели или сети, затем `LLM_UNAVAILABLE` (config.md §2; NFR-AG-04);
* не более 8 вызовов модели на сообщение (contracts.md §8; NFR-AG-10).

Учёт ведётся на каждый ход: `TurnUsage` собирает число вызовов модели и
токены, а `MeteredModel` — обёртка Pydantic AI — пополняет его на каждом
запросе, включая повторный.

Температуры — ADR-040: агент 0.3, классификатор и `extract_context` 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider
from pydantic_ai.settings import ModelSettings

from cinematch.overall.config import Settings
from cinematch.overall.errors import ErrorCode, ServiceError

# Температуры — ADR-040 (documents/agents/120 §6).
AGENT_TEMPERATURE = 0.3
CLASSIFIER_TEMPERATURE = 0.0

# Не более 8 вызовов модели на сообщение — contracts.md §8, NFR-AG-10.
MAX_MODEL_CALLS = 8

# Один повтор запроса к модели — config.md §2, NFR-AG-04.
MODEL_RETRIES = 1

# Имя провайдера для истории сообщений и телеметрии (ADR-027).
PROVIDER_NAME = "routerai"

ModelRole = Literal["agent", "classifier"]


@dataclass(slots=True)
class TurnUsage:
    """Расход одного хода: вызовы модели и токены (contracts.md §8).

    Создаётся оркестратором в начале хода; в журнал хода попадают
    `model_calls`, `tokens_in`, `tokens_out` (146 §10).
    """

    model_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    def register_call(self) -> None:
        """Учесть вызов модели; девятый вызов за ход — `LIMIT_EXCEEDED`."""
        if self.model_calls >= MAX_MODEL_CALLS:
            raise ServiceError(ErrorCode.LIMIT_EXCEEDED)
        self.model_calls += 1

    def add_tokens(self, tokens_in: int, tokens_out: int) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out


@dataclass(init=False)
class MeteredModel(WrapperModel):
    """Обёртка модели: считает вызовы и токены, делает один повтор.

    Повтор выполняется здесь, на одном запросе к модели, поэтому он не
    размножается по циклу агента: каждая попытка учитывается отдельно, а
    исчерпанный лимит вызовов прерывает ход до обращения к сети.
    """

    usage: TurnUsage

    def __init__(self, wrapped: Model, usage: TurnUsage) -> None:
        super().__init__(wrapped)
        self.usage = usage

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Запрос к модели с одним повтором и учётом расхода — NFR-AG-04, NFR-AG-13."""
        self.usage.register_call()
        try:
            response = await self.wrapped.request(
                messages, model_settings, model_request_parameters
            )
        except ModelAPIError as first_error:
            self.usage.register_call()
            try:
                response = await self.wrapped.request(
                    messages, model_settings, model_request_parameters
                )
            except ModelAPIError:
                raise ServiceError(ErrorCode.LLM_UNAVAILABLE) from first_error

        self.usage.add_tokens(response.usage.input_tokens, response.usage.output_tokens)
        return response


@dataclass(frozen=True, slots=True)
class LlmModels:
    """Две модели хода: агент и классификатор (`LLM_MODEL`, `LLM_CLASSIFIER_MODEL`)."""

    agent: Model
    classifier: Model


class LlmClient:
    """Создаёт модели по конфигурации и привязывает их к расходу хода.

    Клиент — единственное место с ключом и тайм-аутом (agents/150 §1, п. 2).
    Базовые модели создаются один раз при старте; на ход выдаются обёртки с
    отдельным счётчиком, поэтому лимит 8 вызовов считается на сообщение.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._models: dict[ModelRole, OpenAIChatModel] = {}

    def _build(self, role: ModelRole) -> OpenAIChatModel:
        """Модель Chat Completions у OpenAI-совместимого провайдера (ADR-027, ADR-038)."""
        model_name = (
            self.settings.llm_model if role == "agent" else self.settings.llm_classifier_model
        )
        temperature = AGENT_TEMPERATURE if role == "agent" else CLASSIFIER_TEMPERATURE
        provider = OpenAICompatibleProvider(
            PROVIDER_NAME,
            base_url=self.settings.llm_base_url,
            api_key=self.settings.llm_api_key,
        )
        return OpenAIChatModel(
            model_name,
            provider=provider,
            settings=ModelSettings(
                temperature=temperature,
                timeout=self.settings.llm_timeout_s,
                # Повтор делает MeteredModel; повтор SDK отключён, иначе их было бы два.
                max_retries=0,
            ),
        )

    def model(self, role: ModelRole) -> OpenAIChatModel:
        """Базовая модель роли; создаётся при первом обращении и кэшируется."""
        if role not in self._models:
            self._models[role] = self._build(role)
        return self._models[role]

    def for_turn(self, usage: TurnUsage | None = None) -> LlmModels:
        """Модели на один ход: агент с температурой 0.3, классификатор — 0."""
        turn_usage = usage if usage is not None else TurnUsage()
        return LlmModels(
            agent=MeteredModel(self.model("agent"), turn_usage),
            classifier=MeteredModel(self.model("classifier"), turn_usage),
        )
