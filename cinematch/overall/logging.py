"""Структурный журнал CineMatch — одна строка JSON на запись.

Форма записи — конверт events schema: `event`, `schema_version`, `event_id`,
`user_id`, `occurred_at`, `payload` (documents/agents/140.event-schema.md).
Служебные поля хода (шаг, длительность, токены, код и т. п.) лежат в `payload`
по белому списку; всё остальное отбрасывается.

Приватность (NFR-AG-08, NFR-AG-15, TS-AG-10):
* `user_id` пишется как первые 12 символов sha256 от значения — по нему нельзя
  установить личность, но можно сопоставить записи одного пользователя;
* ключи, тексты сообщений и служебные пути в журнал не попадают: их нет в
  белом списке, и значение проверяется на запрещённые подстроки.
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, TextIO

SCHEMA_VERSION = 1

# Поля конверта — agents/140.
ENVELOPE_FIELDS = ("event", "schema_version", "event_id", "user_id", "occurred_at", "payload")

# Белый список служебных полей payload: NFR-AG-13 (время, шаг, длительность,
# токены), AGENTS.md (stage, step, duration_ms, tokens, code) и contracts.md §6 п. 3
# (layer, retry).
PAYLOAD_FIELDS = (
    "stage",
    "step",
    "action",
    "duration_ms",
    "tokens_in",
    "tokens_out",
    "code",
    "layer",
    "retry",
    "count",
    "tool",
    "class",
    "rule",
    "found",
    "deleted_counts",
)


def hash_user_id(user_id: str) -> str:
    """Первые 12 символов sha256: псевдоним пользователя для журнала."""
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]


def _looks_sensitive(value: Any) -> bool:
    """Дешёвая страховка поверх белого списка: пути, токены и длинные тексты.

    Белый список уже исключает поля с текстом сообщений и ключами; эта проверка
    не даёт записать их случайно, если значение попало в разрешённое поле.
    """
    if not isinstance(value, str):
        return False
    if value.startswith(("/", "./", "~/", "\\")) or "\\" in value:
        return True
    # Тексты сообщений и промпты длиннее любого служебного значения.
    if len(value) > 200:
        return True
    # Ключи провайдеров и заголовки авторизации (NFR-AG-08).
    lowered = value.lower()
    if lowered.startswith(("sk-", "sk_", "bearer ", "basic ", "token ")):
        return True
    # Длинные hex-строки без префикса (старые ключи, хэши).
    return len(value) >= 32 and all(c in "0123456789abcdefABCDEF-" for c in value)


def build_record(
    event: str,
    *,
    user_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Собрать запись журнала: конверт + отфильтрованный payload."""
    clean: dict[str, Any] = {}
    for name, value in (payload or {}).items():
        if name not in PAYLOAD_FIELDS or value is None:
            continue
        if _looks_sensitive(value):
            continue
        clean[name] = value

    stamp = occurred_at or datetime.now(UTC)
    return {
        "event": event,
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id or str(uuid.uuid4()),
        "user_id": hash_user_id(user_id) if user_id else None,
        "occurred_at": stamp.isoformat(),
        "payload": clean,
    }


def write_event(
    event: str,
    *,
    user_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    """Записать событие одной строкой JSON и вернуть саму запись."""
    record = build_record(event, user_id=user_id, payload=payload)
    target = stream if stream is not None else sys.stdout
    target.write(json.dumps(record, ensure_ascii=False) + "\n")
    target.flush()
    return record
