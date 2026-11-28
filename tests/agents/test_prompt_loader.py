"""Загрузчик промптов — documents/agents/125.prompts.md, build.md §3 п. 2."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cinematch.agents.prompt_loader import (
    PROMPT_NAMES,
    PROMPTS_DIR,
    PROMPTS_VERSION,
    PromptError,
    PromptLibrary,
    load_prompts,
)

EXPECTED_NAMES = (
    "system_agent.md",
    "classifier.md",
    "confirm_yes_no.md",
    "extract_context.md",
    "retry_invalid_output.md",
)


@pytest.fixture(scope="module")
def library() -> PromptLibrary:
    return load_prompts()


def test_prompt_texts_match_125_verbatim(library: PromptLibrary) -> None:
    """Тексты файлов совпадают с блоками `125` дословно (125 §1, п. 1)."""
    import re

    doc = (PROMPTS_DIR.parents[2] / "documents" / "agents" / "125.prompts.md").read_text(
        encoding="utf-8"
    )
    blocks = {}
    for match in re.finditer(r"```text\n(.*?)\n```", doc, re.DOTALL):
        heading = re.findall(r"^## \d+\. `([^`]+)`", doc[: match.start()], re.MULTILINE)[-1]
        blocks[heading] = match.group(1)

    assert set(blocks) == set(EXPECTED_NAMES)
    for name, body in blocks.items():
        header, _, text = body.partition("\n---\n")
        assert header.strip() == "version: 1.2"
        assert library.get(name).text.strip() == text.strip()


def test_all_five_files_are_loaded(library: PromptLibrary) -> None:
    """Все пять файлов из 125 §1 загружаются при старте."""
    assert tuple(library.prompts) == EXPECTED_NAMES
    assert PROMPT_NAMES == EXPECTED_NAMES
    for name in EXPECTED_NAMES:
        assert library.get(name).text.strip()


@pytest.mark.parametrize("name", EXPECTED_NAMES)
def test_version_is_1_2(name: str, library: PromptLibrary) -> None:
    """В шапке каждого файла — `version: 1.2` (125 §1, п. 1)."""
    assert PROMPTS_VERSION == "1.2"
    assert library.get(name).version == "1.2"


def test_system_agent_has_temperature_rule_and_profile(library: PromptLibrary) -> None:
    """Правило 6 с таблицей настроений и плейсхолдер {profile} на месте (125 §2)."""
    text = library.get("system_agent.md").text

    assert "| Настроение | Как подбирать |" in text
    for mood in ("tired", "sad", "calm", "tense", "cheerful", "other"):
        assert f"| {mood} |" in text
    assert "{profile}" in text
    assert "{today}" in text and "{city}" in text and "{pending_feedback}" in text
    assert "{available_minutes}" in text and "{questions_left}" in text


def test_profile_is_substituted(library: PromptLibrary) -> None:
    """Плейсхолдер {profile} подставляется: ADR-048, 125 §2."""
    rendered = library.render(
        "system_agent.md",
        today="8 ноября 2026",
        city="Москва",
        genres="драма, комедия",
        questions_left=2,
        available_minutes=90,
        pending_feedback="нет",
        profile="Драма +0,352; Комедия +0,089; Ужасы −0,222",
    )

    assert "Драма +0,352; Комедия +0,089; Ужасы −0,222" in rendered
    assert "{profile}" not in rendered
    assert "{today}" not in rendered


def test_extract_context_lists_only_genres(library: PromptLibrary) -> None:
    """В `extract_context.md` единственный плейсхолдер — {genres} (125 §2, §5)."""
    prompt = library.get("extract_context.md")

    assert prompt.placeholders == ("genres",)
    assert "{genres}" in prompt.text


def test_retry_prompt_has_error_placeholder(library: PromptLibrary) -> None:
    prompt = library.get("retry_invalid_output.md")

    assert prompt.placeholders == ("error",)
    assert "movie_ids" in prompt.render(error="фильм вне результатов поиска")


def test_classifier_prompts_have_no_placeholders(library: PromptLibrary) -> None:
    """Текст пользователя в промпт не подставляется (125 §1, п. 4)."""
    for name in ("classifier.md", "confirm_yes_no.md"):
        assert library.get(name).placeholders == ()
        assert "{" not in library.get(name).text


def test_missing_file_is_a_startup_error(tmp_path: Path) -> None:
    shutil.copytree(PROMPTS_DIR, tmp_path / "prompts")
    (tmp_path / "prompts" / "classifier.md").unlink()

    with pytest.raises(PromptError, match=r"classifier\.md"):
        load_prompts(tmp_path / "prompts")


def test_wrong_version_is_a_startup_error(tmp_path: Path) -> None:
    folder = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR, folder)
    path = folder / "classifier.md"
    updated = path.read_text(encoding="utf-8").replace("version: 1.2", "version: 1.1")
    path.write_text(updated, encoding="utf-8")

    with pytest.raises(PromptError, match=r"версия 1\.1"):
        load_prompts(folder)


def test_missing_header_is_a_startup_error(tmp_path: Path) -> None:
    folder = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR, folder)
    (folder / "system_agent.md").write_text("Ты — CineMatch.", encoding="utf-8")

    with pytest.raises(PromptError, match="шапки"):
        load_prompts(folder)


def test_unknown_placeholder_in_file_is_a_startup_error(tmp_path: Path) -> None:
    folder = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR, folder)
    path = folder / "classifier.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n{weather}\n", encoding="utf-8")

    with pytest.raises(PromptError, match=r"\{weather\}"):
        load_prompts(folder)


def test_render_rejects_unknown_placeholder(library: PromptLibrary) -> None:
    """Подстановка — только плейсхолдеров из таблицы 125."""
    with pytest.raises(PromptError, match=r"\{weather\}"):
        library.render("classifier.md", weather="дождь")


def test_render_requires_each_placeholder(library: PromptLibrary) -> None:
    with pytest.raises(PromptError, match=r"\{genres\}"):
        library.render("extract_context.md")
