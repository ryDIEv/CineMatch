"""`GET /api/v1/health` — ui/130, build.md §3 п. 6."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cinematch.interfaces.api.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TMDB_API_KEY", "tmdb")
    monkeypatch.setenv("LLM_API_KEY", "llm")
    monkeypatch.setenv("LLM_MODEL", "model")
    return TestClient(create_app())


def test_health_returns_status_and_catalog_size(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "catalog_size": 0}


def test_docs_are_served_under_api(client: TestClient) -> None:
    assert client.get("/api/openapi.json").status_code == 200
