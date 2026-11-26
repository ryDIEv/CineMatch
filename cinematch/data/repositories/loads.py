"""Журнал загрузок `catalog_loads` — data/120 §7, data/130 §7.5.

По записи восстанавливается прерванный запуск и строится отчёт: `params_json`
отличает чужой запуск, `last_page` указывает на последнюю **зафиксированную**
страницу, счётчики `movies_upserted`/`movies_skipped` накапливаются постранично.
Запись не удаляется: по ней строится история загрузок (data/130 §7.5, п. 5).
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from cinematch.data.db import utc_now
from cinematch.data.models import CatalogLoad

RESUMABLE = ("running", "partial")


def params_json(pages: int, from_year: int, min_votes: int) -> str:
    """Канонический JSON параметров запуска: одинаковые значения — одна строка."""
    return json.dumps(
        {"pages": pages, "from_year": from_year, "min_votes": min_votes},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def find_resumable(session: Session, params: str) -> CatalogLoad | None:
    """Последняя незавершённая загрузка с теми же параметрами (data/130 §7.5)."""
    return session.scalar(
        select(CatalogLoad)
        .where(CatalogLoad.status.in_(RESUMABLE))
        .where(CatalogLoad.params_json == params)
        .order_by(CatalogLoad.id.desc())
        .limit(1)
    )


def by_id(session: Session, load_id: int) -> CatalogLoad | None:
    """Строка загрузки по `id`: отчёт и счётчики читаются из неё."""
    return session.get(CatalogLoad, load_id)


def create_load(session: Session, params: str) -> CatalogLoad:
    """Новая запись `running` с `last_page = 0` (data/146 §2)."""
    load = CatalogLoad(
        started_at=utc_now(),
        finished_at=None,
        status="running",
        params_json=params,
        movies_upserted=0,
        movies_skipped=0,
        last_page=0,
    )
    session.add(load)
    session.flush()
    return load


def mark_running(session: Session, load: CatalogLoad) -> None:
    """Продолжение прерванного запуска: снова `running` (data/146 §2)."""
    load.status = "running"
    load.finished_at = None
    session.flush()


def save_progress(
    session: Session,
    load: CatalogLoad,
    *,
    last_page: int,
    upserted: int,
    skipped: int,
) -> None:
    """Зафиксировать страницу: `last_page` и накопленные счётчики (data/120 §7)."""
    load.last_page = last_page
    load.movies_upserted += upserted
    load.movies_skipped += skipped
    session.flush()


def finish(session: Session, load: CatalogLoad, status: str) -> None:
    """Терминальный статус и `finished_at` — data/120 §7."""
    load.status = status
    load.finished_at = utc_now()
    session.flush()
