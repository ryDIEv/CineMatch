"""Клиент модели — documents/agents/llm.py, config.md §2, ADR-040, NFR-AG-04, NFR-AG-10."""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ModelConnectionError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from cinematch.agents.classifier import Classifier
from cinematch.agents.llm import (
    AGENT_TEMPERATURE,
    CLASSIFIER_TEMPERATURE,
    MAX_MODEL_CALLS,
    MeteredModel,
    TurnUsage,
)
from cinematch.agents.prompt_loader import load_prompts
from cinematch.overall.config import load_settings
from cinematch.overall.errors import ErrorCode, ServiceError

ENV = {
    "TMDB_API_KEY": "tmdb",
    "LLM_API_KEY": "secret-key",
    "LLM_MODEL": "deepseek/deepseek-v4.1-flash-1017",
    "LLM_CLASSIFIER_MODEL": "qwen/qwen3.8-flash",
}


@pytest.fixture
def settings():
    return load_settings(environ=ENV, env_file=None)


def _messages() -> list[ModelRequest]:
    return [ModelRequest(parts=[UserPromptPart(content="привет")])]


def _response() -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content="ответ")],
        usage=RequestUsage(input_tokens=30, output_tokens=7),
    )


def test_models_come_from_configuration(settings) -> None:
    """Адрес, ключ и модели — только из конфигурации (NFR-AG-12, ADR-038)."""
    from cinematch.agents.llm import LlmClient

    client = LlmClient(settings)
    agent = client.model("agent")
    classifier = client.model("classifier")

    assert agent.model_name == "deepseek/deepseek-v4.1-flash-1017"
    assert classifier.model_name == "qwen/qwen3.8-flash"
    assert agent.base_url == "https://routerai.ru/api/v1/"
    assert agent.settings == {
        "temperature": AGENT_TEMPERATURE,
        "timeout": 20,
        "max_retries": 0,
    }
    assert classifier.settings == {
        "temperature": CLASSIFIER_TEMPERATURE,
        "timeout": 20,
        "max_retries": 0,
    }


def test_classifier_model_defaults_to_agent_model() -> None:
    """`LLM_CLASSIFIER_MODEL` по умолчанию равен `LLM_MODEL` (config.md §1)."""
    env = {k: v for k, v in ENV.items() if k != "LLM_CLASSIFIER_MODEL"}
    settings = load_settings(environ=env, env_file=None)

    from cinematch.agents.llm import LlmClient

    assert LlmClient(settings).model("classifier").model_name == ENV["LLM_MODEL"]


async def test_usage_counts_calls_and_tokens() -> None:
    """Учёт: каждый вызов и токены каждого ответа (NFR-AG-13)."""
    usage = TurnUsage()
    model = MeteredModel(FunctionModel(lambda messages, info: _response()), usage)

    await model.request(_messages(), None, ModelRequestParameters())

    assert usage.model_calls == 1
    assert usage.tokens_in == 30
    assert usage.tokens_out == 7


async def test_transport_error_is_retried_once() -> None:
    """Ошибка модели — один повтор, затем успех (NFR-AG-04)."""
    attempts: list[int] = []

    def respond(messages: list, info: AgentInfo) -> ModelResponse:
        attempts.append(1)
        if len(attempts) == 1:
            raise ModelConnectionError("routerai", "нет сети")
        return _response()

    usage = TurnUsage()
    model = MeteredModel(FunctionModel(respond), usage)

    response = await model.request(_messages(), None, ModelRequestParameters())

    assert response.parts
    assert len(attempts) == 2
    # Обе попытки — вызовы модели: NFR-AG-10 считает их все.
    assert usage.model_calls == 2
    assert usage.tokens_in == 30


async def test_second_transport_error_is_llm_unavailable() -> None:
    """Вторая ошибка подряд — `LLM_UNAVAILABLE` (config.md §2, contracts.md §6)."""

    def respond(messages: list, info: AgentInfo) -> ModelResponse:
        raise ModelConnectionError("routerai", "нет сети")

    usage = TurnUsage()
    model = MeteredModel(FunctionModel(respond), usage)

    with pytest.raises(ServiceError) as error:
        await model.request(_messages(), None, ModelRequestParameters())

    assert error.value.code is ErrorCode.LLM_UNAVAILABLE
    assert usage.model_calls == 2


async def test_ninth_call_is_limit_exceeded() -> None:
    """Не более 8 вызовов модели на сообщение (contracts.md §8, NFR-AG-10)."""
    usage = TurnUsage()
    model = MeteredModel(FunctionModel(lambda messages, info: _response()), usage)

    for _ in range(MAX_MODEL_CALLS):
        await model.request(_messages(), None, ModelRequestParameters())

    with pytest.raises(ServiceError) as error:
        await model.request(_messages(), None, ModelRequestParameters())

    assert error.value.code is ErrorCode.LIMIT_EXCEEDED
    assert usage.model_calls == MAX_MODEL_CALLS


async def test_classifier_counts_usage_on_the_same_turn() -> None:
    """Классификатор и агент делят счётчик хода: лимит общий (146 §9)."""
    prompts = load_prompts()
    usage = TurnUsage()

    def respond(messages: list, info: AgentInfo) -> ModelResponse:
        from pydantic_ai.messages import ToolCallPart

        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name,
                    args={"response": "cinema"},
                )
            ],
            usage=RequestUsage(input_tokens=40, output_tokens=1),
        )

    model = MeteredModel(FunctionModel(respond), usage)
    classifier = Classifier(model, prompts)

    await classifier.classify("хочу кино", stage="dialog")

    assert usage.model_calls == 1
    assert usage.tokens_in == 40
    assert usage.tokens_out == 1


def test_secret_key_never_reaches_log_or_repr(settings, capsys) -> None:
    """Ключ не попадает ни в журнал, ни в представление модели (NFR-AG-08, TS-AG-10)."""
    from cinematch.agents.llm import LlmClient

    client = LlmClient(settings)
    client.for_turn()

    assert settings.llm_api_key == "secret-key"
    assert "secret-key" not in repr(client.model("agent"))
    assert "secret-key" not in capsys.readouterr().out
