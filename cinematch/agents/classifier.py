"""Классификатор запросов и локальный разбор согласия.

Классификатор возвращает одно значение из закрытого перечня
`RequestClass` (contracts.md §2.8). Промпт — отдельный файл, идёт системным
сообщением; текст пользователя — отдельным сообщением с ролью `user` и в
промпт не подставляется (125 §1, п. 4; ADR-005, ADR-032).

Этапы (120 §4):

| Этап | Промпт | Значения |
|---|---|---|
| `dialog` | `classifier.md` | `cinema`, `delete_data`, `off_topic`, `unsafe` |
| `confirm_delete` | `confirm_yes_no.md` | `yes`, `no` |
| `consent` | не вызывается: `parse_consent` | `yes`, `no` |

Невалидный ответ модели — один повтор с тем же промптом и тем же сообщением;
после второго невалидного ответа классификатор поднимает `ClassifierError`:
`cinema` не вызывается, пользователь получает отказ по теме (120 §4, ADR-005).
Модель на этапе `consent` не вызывается вовсе (ADR-043).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import Model

from cinematch.agents.prompt_loader import PromptLibrary
from cinematch.overall.contracts import RequestClass, Stage
from cinematch.overall.logging import write_event

# Промпт по этапу — 120 §4, 130 §5, ADR-032.
PROMPT_BY_STAGE: dict[str, str] = {
    "dialog": "classifier.md",
    "confirm_delete": "confirm_yes_no.md",
}

type DialogClass = Literal["cinema", "delete_data", "off_topic", "unsafe"]
type YesNo = Literal["yes", "no"]

# Утвердительные и отрицательные фразы согласия — 146 §4; список закрытый,
# всё остальное считается отказом (ADR-043).
CONSENT_YES: tuple[str, ...] = ("да", "принимаю", "согласен", "согласна", "конечно", "ок")
CONSENT_NO: tuple[str, ...] = ("нет", "не сейчас", "отказываюсь", "не принимаю")

# Слова отрицания перед утвердительной фразой: «не согласен» — это отказ.
NEGATIONS: frozenset[str] = frozenset({"не", "нет", "ни"})

# Знаки, которые не несут смысла при разборе короткой фразы согласия.
_PUNCTUATION = "\t\n\r,.;:!?…—–-\"'«»()[]{}"


class ClassifierError(RuntimeError):
    """Классификатор дважды вернул невалидный ответ.

    Оркестратор отвечает отказом по теме и не вызывает агента (120 §4).
    """


def normalize_phrase(message: str) -> str:
    """Привести фразу к сравнимому виду: строчные буквы, без пунктуации."""
    lowered = message.lower().replace("ё", "е")
    cleaned = "".join(" " if char in _PUNCTUATION else char for char in lowered)
    return " ".join(cleaned.split())


def _contains(tokens: list[str], phrase: str, *, negated: bool = False) -> bool:
    """Найти фразу целыми словами; `negated` — только без отрицания перед ней.

    Отрицание перед утвердительной фразой («не согласен») — отказ (ADR-043).
    """
    words = phrase.split()
    size = len(words)
    for start in range(len(tokens) - size + 1):
        if tokens[start : start + size] != words:
            continue
        if negated and start > 0 and tokens[start - 1] in NEGATIONS:
            continue
        return True
    return False


def parse_consent(message: str) -> RequestClass:
    """Разобрать текстовое согласие без модели — ADR-043, 146 §4.

    `YES` — «да», «принимаю», «согласен/согласна», «конечно», «ок»;
    `NO` — «нет», «не сейчас», «отказываюсь», «не принимаю»; всё остальное —
    `NO`. Отрицательные фразы и отрицание перед утвердительной («не согласен»)
    проверяются первыми, поэтому отказ не становится согласием.
    """
    tokens = normalize_phrase(message).split()
    if not tokens:
        return RequestClass.NO
    if any(_contains(tokens, phrase) for phrase in CONSENT_NO):
        return RequestClass.NO
    if any(_contains(tokens, phrase, negated=True) for phrase in CONSENT_YES):
        return RequestClass.YES
    return RequestClass.NO


@dataclass(frozen=True, slots=True)
class Classification:
    """Результат классификации вместе с длительностью для журнала."""

    request_class: RequestClass
    duration_ms: int


class Classifier:
    """Классификатор по этапам: промпт из файла, ответ по схеме `RequestClass`."""

    def __init__(self, model: Model, prompts: PromptLibrary) -> None:
        self.model = model
        self.prompts = prompts
        self._agents: dict[str, Agent[None, Any]] = {}

    def _agent(self, stage: str) -> Agent[None, Any]:
        """Агент этапа: системный промпт из файла, ответ ограничен схемой.

        Модель передаётся на запуск, а не при создании: клиент модели выдаёт
        обёртку с расходом на каждый ход (llm.py), поэтому один экземпляр
        классификатора обслуживает разные ходы.
        """
        if stage not in PROMPT_BY_STAGE:
            raise ValueError(f"на этапе {stage!r} классификатор не вызывается")
        if stage not in self._agents:
            output_type = DialogClass if stage == "dialog" else YesNo
            # Повтор делаем сами: он должен быть ровно один и с тем же промптом.
            self._agents[stage] = Agent(
                output_type=output_type,
                system_prompt=self.prompts.get(PROMPT_BY_STAGE[stage]).text,
                retries={"output": 0},
                name=f"classifier-{stage}",
            )
        return self._agents[stage]

    async def classify(
        self,
        message: str,
        *,
        stage: Stage,
        user_id: str | None = None,
    ) -> Classification:
        """Классифицировать сообщение; на этапе `consent` модель не вызывается.

        Событие `RequestClassified` пишется здесь же: издатель — классификатор
        (135). Текст сообщения в журнал не попадает.
        """
        if stage == "consent":
            return Classification(parse_consent(message), 0)

        started = time.monotonic()
        value = await self._ask(self._agent(stage), message)
        classification = Classification(
            request_class=RequestClass(value),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        write_event(
            "RequestClassified",
            user_id=user_id,
            payload={
                "class": str(classification.request_class),
                "duration_ms": classification.duration_ms,
            },
        )
        return classification

    async def _ask(self, agent: Agent[None, Any], message: str) -> str:
        """Один запрос и один повтор при невалидном ответе — ADR-005, NFR-AG-04."""
        try:
            result = await agent.run(message, model=self.model)
        except UnexpectedModelBehavior:
            try:
                result = await agent.run(message, model=self.model)
            except UnexpectedModelBehavior as second_error:
                raise ClassifierError(
                    "классификатор дважды вернул невалидный ответ"
                ) from second_error
        return str(result.output)
