"""Загрузчик промптов модуля ИИ-агентов.

Единственный источник текстов — documents/agents/125.prompts.md; файлы лежат в
`cinematch/agents/prompts/` и читаются при старте backend (build.md §3, п. 2).
Строки промптов в коде не пишутся (NFR-AG-11, ADR-037).

Загрузчик проверяет при старте:

* все пять файлов на месте — отсутствие файла останавливает backend;
* в шапке каждого файла `version: 1.2` (125 §1, п. 1);
* в тексте нет плейсхолдеров, которых нет в таблице 125, — опечатка в имени
  плейсхолдера иначе молча дошла бы до модели.

Подстановка — только плейсхолдеров из таблицы 125; текст пользователя в
промпт не подставляется (125 §1, п. 4).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Версия промптов — documents/agents/125.prompts.md, шапка документа.
PROMPTS_VERSION = "1.2"

# Пять файлов и их плейсхолдеры — 125 §2–6; `{genres}` объявлен для
# `system_agent.md` таблицей §2 и используется в `extract_context.md`.
PROMPT_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "system_agent.md": (
        "today",
        "city",
        "genres",
        "questions_left",
        "available_minutes",
        "pending_feedback",
        "profile",
    ),
    "classifier.md": (),
    "confirm_yes_no.md": (),
    "extract_context.md": ("genres",),
    "retry_invalid_output.md": ("error",),
}

PROMPT_NAMES: tuple[str, ...] = tuple(PROMPT_PLACEHOLDERS)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_HEADER_RE = re.compile(r"\Aversion:\s*(?P<version>\S+)\s*\n---\s*\n", re.MULTILINE)
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class PromptError(RuntimeError):
    """Ошибка загрузки или подстановки промпта: backend не стартует."""


@dataclass(frozen=True, slots=True)
class Prompt:
    """Один файл промпта: имя, версия и текст после шапки."""

    name: str
    version: str
    text: str
    placeholders: tuple[str, ...]

    def render(self, **values: object) -> str:
        """Подставить значения плейсхолдеров, объявленных для этого файла.

        Требуются значения для всех плейсхолдеров, которые встречаются в тексте;
        неизвестное имя — ошибка, а не тихая подстановка.
        """
        allowed = set(self.placeholders)
        unknown = set(values) - allowed
        if unknown:
            raise PromptError(
                f"{self.name}: неизвестный плейсхолдер {{{sorted(unknown)[0]}}}; "
                f"таблица 125 разрешает только {', '.join(self.placeholders) or '—'}"
            )

        text = self.text
        for token in _PLACEHOLDER_RE.findall(self.text):
            if token not in allowed:
                raise PromptError(f"{self.name}: неизвестный плейсхолдер {{{token}}}")
            if token not in values:
                raise PromptError(f"{self.name}: нет значения для {{{token}}}")
            text = text.replace("{" + token + "}", str(values[token]))
        return text


@dataclass(frozen=True, slots=True)
class PromptLibrary:
    """Все промпты, прочитанные при старте; поиск по имени файла."""

    prompts: Mapping[str, Prompt]

    def get(self, name: str) -> Prompt:
        try:
            return self.prompts[name]
        except KeyError:
            raise PromptError(f"промпт не загружен: {name}") from None

    def render(self, name: str, **values: object) -> str:
        return self.get(name).render(**values)


def load_prompts(directory: Path | None = None) -> PromptLibrary:
    """Прочитать все пять промптов при старте и проверить шапки и плейсхолдеры.

    Вызывается API-слоем в порядке старта backend (build.md §3, п. 2). Любая
    ошибка — исключение `PromptError`, backend не начинает принимать запросы.
    """
    folder = PROMPTS_DIR if directory is None else Path(directory)
    prompts: dict[str, Prompt] = {}
    for name in PROMPT_NAMES:
        path = folder / name
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise PromptError(f"файл промпта не найден: {name} ({path})") from None

        header = _HEADER_RE.match(raw)
        if header is None:
            raise PromptError(f"{name}: нет шапки «version: 1.2» и строки «---»")
        version = header.group("version")
        if version != PROMPTS_VERSION:
            raise PromptError(
                f"{name}: версия {version}, ожидается {PROMPTS_VERSION} "
                "(documents/agents/125.prompts.md)"
            )

        body = raw[header.end() :]
        allowed = set(PROMPT_PLACEHOLDERS[name])
        for token in _PLACEHOLDER_RE.findall(body):
            if token not in allowed:
                raise PromptError(f"{name}: неизвестный плейсхолдер {{{token}}}")

        prompts[name] = Prompt(
            name=name,
            version=version,
            text=body,
            placeholders=PROMPT_PLACEHOLDERS[name],
        )

    return PromptLibrary(prompts=prompts)
