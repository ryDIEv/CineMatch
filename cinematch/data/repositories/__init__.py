"""Запросы к базе по группам таблиц — data/120, data/130.

SQL живёт только здесь и в `db.py` (data/150 §1). Функции получают уже
проверенные значения (ADR-015) и не заворачивают ошибки в `ServiceError`:
это делает фасад `cinematch/data/api.py` или `db.translate_db_errors`.
"""

from __future__ import annotations

from . import catalog, choices, ratings, sessions, users

__all__ = ["catalog", "choices", "ratings", "sessions", "users"]
