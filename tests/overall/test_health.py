"""`GET /api/v1/health` — ui/130, build.md §3 п. 6."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cinematch.data.db import init_database
from cinematch.interfaces.api.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("TMDB_API_KEY", "tmdb")
    monkeypatch.setenv("LLM_API_KEY", "llm")
    monkeypatch.setenv("LLM_MODEL", "model")
    # Реальная база: health спрашивает `SELECT COUNT(*) FROM movies`
    # (data/130 §4.20), поэтому схема должна быть создана (build.md §3).
    db_path = tmp_path / "cinematch.db"
    monkeypatch.setenv("CINEMATCH_DB_PATH", str(db_path))
    init_database(db_path)
    return TestClient(create_app())


def test_health_returns_status_and_catalog_size(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "catalog_size": 0}


def test_docs_are_served_under_api(client: TestClient) -> None:
    assert client.get("/api/openapi.json").status_code == 200
