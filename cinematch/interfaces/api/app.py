"""FastAPI-приложение CineMatch: маршруты `/api/v1` (documents/ui/130).

Порядок старта backend — build.md §3: конфигурация, готовность базы, маршруты,
запись `backend ready`. Промпты идут вместе с модулем агентов в следующей задаче.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from cinematch.data.api import get_catalog_size
from cinematch.data.db import init_database
from cinematch.overall.config import Settings, load_settings
from cinematch.overall.logging import write_event


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Проверить конфигурацию, подготовить базу и записать готовность.

    `init_database()` — стык модулей: шаги 3–5 порядка старта (build.md §3)
    выполняет модуль данных, вызывает их API-слой (data/120 §9, §4.18).
    """
    settings: Settings = load_settings()
    app.state.settings = settings
    init_database(settings.db_path)
    write_event("backend_ready", payload={"stage": "startup", "count": get_catalog_size()})
    yield


def create_app() -> FastAPI:
    """Собрать приложение: маршруты и проверка конфигурации на старте."""
    application = FastAPI(
        title="CineMatch",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    @application.get("/api/v1/health")
    def health() -> dict[str, object]:
        """Проверка работы: `{"status": "ok", "catalog_size": N}` (ui/130)."""
        return {"status": "ok", "catalog_size": get_catalog_size()}

    return application


app = create_app()
