"""Классификатор — documents/agents/120 §4, 125 §3–4, ADR-005, ADR-032."""

from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from cinematch.agents.classifier import (
    Classifier,
    ClassifierError,
    parse_consent,
)
from cinematch.agents.prompt_loader import PromptLibrary, load_prompts
from cinematch.overall.contracts import RequestClass


@pytest.fixture(scope="module")
def prompts() -> PromptLibrary:
    return load_prompts()


def _responder(value: str, seen: list[list]) -> FunctionModel:
    """FunctionModel, возвращающий значение структурированного ответа."""

    def respond(messages: list, info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        tool = info.output_tools[0].name
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool, args={"response": value})],
            usage=RequestUsage(input_tokens=12, output_tokens=2),
        )

    return FunctionModel(respond)


def _parts(messages: list, kind: type) -> list[str]:
    return [
        part.content for message in messages for part in message.parts if isinstance(part, kind)
    ]


@pytest.mark.parametrize(
    ("stage", "value"),
    [
        ("dialog", "cinema"),
        ("dialog", "delete_data"),
        ("dialog", "off_topic"),
        ("dialog", "unsafe"),
        ("confirm_delete", "yes"),
        ("confirm_delete", "no"),
    ],
)
async def test_each_class_is_returned(stage: str, value: str, prompts: PromptLibrary) -> None:
    """Каждый класс из contracts.md §2.8 возвращается как RequestClass."""
    seen: list[list] = []
    classifier = Classifier(_responder(value, seen), prompts)

    result = await classifier.classify("текст", stage=stage)  # type: ignore[arg-type]

    assert result.request_class is RequestClass(value)


async def test_prompt_is_system_message_and_text_is_user_message(
    prompts: PromptLibrary,
) -> None:
    """Промпт — системным сообщением, текст пользователя — отдельным `user`."""
    seen: list[list] = []
    classifier = Classifier(_responder("cinema", seen), prompts)

    await classifier.classify("хочу комедию на вечер", stage="dialog")

    messages = seen[0]
    systems = _parts(messages, SystemPromptPart)
    users = _parts(messages, UserPromptPart)
    assert systems == [prompts.get("classifier.md").text]
    assert users == ["хочу комедию на вечер"]
    # Текст пользователя не попал в системный промпт (125 §1, п. 4).
    assert "хочу комедию" not in systems[0]


async def test_confirm_delete_uses_its_own_prompt(prompts: PromptLibrary) -> None:
    """На этапе `confirm_delete` — `confirm_yes_no.md` (ADR-032)."""
    seen: list[list] = []
    classifier = Classifier(_responder("no", seen), prompts)

    await classifier.classify("а что удалится?", stage="confirm_delete")

    assert _parts(seen[0], SystemPromptPart) == [prompts.get("confirm_yes_no.md").text]


async def test_invalid_output_is_retried_once(prompts: PromptLibrary) -> None:
    """Невалидный ответ — один повтор, затем ошибка (120 §4, ADR-005)."""
    calls: list[list] = []

    def respond(messages: list, info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        # Текст вместо структурированного ответа: схема не выполняется.
        return ModelResponse(parts=[TextPart(content="наверное, про кино")])

    classifier = Classifier(FunctionModel(respond), prompts)

    with pytest.raises(ClassifierError):
        await classifier.classify("привет", stage="dialog")

    assert len(calls) == 2


async def test_valid_second_attempt_is_accepted(prompts: PromptLibrary) -> None:
    """Повтор после невалидного ответа разрешён и даёт значение."""
    calls: list[int] = []

    def respond(messages: list, info: AgentInfo) -> ModelResponse:
        calls.append(1)
        if len(calls) == 1:
            return ModelResponse(parts=[TextPart(content="не знаю")])
        tool = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=tool, args={"response": "off_topic"})])

    classifier = Classifier(FunctionModel(respond), prompts)

    result = await classifier.classify("сколько будет 2+2", stage="dialog")

    assert result.request_class is RequestClass.OFF_TOPIC
    assert len(calls) == 2


async def test_consent_stage_does_not_call_the_model(prompts: PromptLibrary) -> None:
    """На этапе `consent` модель не вызывается вовсе (ADR-043)."""
    calls: list[list] = []
    classifier = Classifier(_responder("yes", calls), prompts)

    result = await classifier.classify("принимаю", stage="consent")

    assert result.request_class is RequestClass.YES
    assert calls == []


def test_onboarding_stage_is_not_classified(prompts: PromptLibrary) -> None:
    classifier = Classifier(_responder("cinema", []), prompts)

    with pytest.raises(ValueError, match="onboarding"):
        classifier._agent("onboarding")


async def test_request_classified_event_is_logged(
    prompts: PromptLibrary, capsys: pytest.CaptureFixture[str]
) -> None:
    """`RequestClassified` пишется классификатором, без текста сообщения (135)."""
    classifier = Classifier(_responder("cinema", []), prompts)

    await classifier.classify("секретный текст", stage="dialog", user_id="web:local")

    record = capsys.readouterr().out.strip().splitlines()[-1]
    assert '"event": "RequestClassified"' in record
    assert '"class": "cinema"' in record
    assert "секретный текст" not in record


@pytest.mark.parametrize(
    "message",
    ["да", "Да.", "принимаю", "Принимаю!", "согласен", "согласна", "конечно", "ок", "ОК"],
)
def test_parse_consent_yes(message: str) -> None:
    """Утвердительные фразы из 146 §4 дают `yes`."""
    assert parse_consent(message) is RequestClass.YES


@pytest.mark.parametrize(
    "message",
    ["нет", "не сейчас", "отказываюсь", "не принимаю", "не согласен", "не согласна"],
)
def test_parse_consent_no(message: str) -> None:
    """Отрицательные фразы из 146 §4 дают `no`."""
    assert parse_consent(message) is RequestClass.NO


@pytest.mark.parametrize(
    "message",
    ["", "   ", "а что вы храните?", "позже", "не знаю", "да нет наверное"],
)
def test_parse_consent_ambiguous_is_no(message: str) -> None:
    """Неоднозначный ответ считается отказом (ADR-043)."""
    assert parse_consent(message) is RequestClass.NO


def test_parse_consent_ignores_word_parts() -> None:
    """«прода» не считается словом «да», «около» — словом «ок»."""
    assert parse_consent("продам почку") is RequestClass.NO
    assert parse_consent("около двух часов") is RequestClass.NO
