"""Промпты загружаются при старте backend — build.md §3, п. 2."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cinematch.agents.prompt_loader import PROMPT_NAMES
from cinematch.data.db import init_database
from cinematch.interfaces.api.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("TMDB_API_KEY", "tmdb")
    monkeypatch.setenv("LLM_API_KEY", "llm")
    monkeypatch.setenv("LLM_MODEL", "model")
    db_path = tmp_path / "cinematch.db"
    monkeypatch.setenv("CINEMATCH_DB_PATH", str(db_path))
    init_database(db_path)
    return TestClient(create_app())


def test_startup_loads_all_prompts(client: TestClient) -> None:
    """Все пять промптов прочитаны до приёма запросов (125 §1)."""
    with client:
        prompts = client.app.state.prompts

    assert tuple(prompts.prompts) == PROMPT_NAMES
    assert prompts.get("system_agent.md").version == "1.2"
