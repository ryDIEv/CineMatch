"""Журнал: конверт agents/140, белый список payload и обезличивание user_id."""

from __future__ import annotations

import json
import re

from cinematch.overall.logging import (
    ENVELOPE_FIELDS,
    PAYLOAD_FIELDS,
    build_record,
    hash_user_id,
    write_event,
)


def test_envelope_has_documented_fields() -> None:
    record = build_record("MovieChosen", user_id="web:local", payload={"count": 1})

    assert tuple(record) == ENVELOPE_FIELDS
    assert record["schema_version"] == 1
    assert record["event"] == "MovieChosen"
    assert re.fullmatch(r"[0-9a-f-]{36}", record["event_id"])
    assert record["payload"] == {"count": 1}


def test_user_id_is_hashed_to_twelve_chars() -> None:
    record = build_record("RequestClassified", user_id="web:local")

    assert record["user_id"] == hash_user_id("web:local")
    assert len(record["user_id"]) == 12
    assert record["user_id"] != "web:local"
    assert "web:local" not in json.dumps(record)


def test_no_user_id_means_no_field_value() -> None:
    assert build_record("backend_ready")["user_id"] is None


def test_payload_whitelist_drops_unknown_fields() -> None:
    record = build_record(
        "error",
        payload={
            "code": "DATA_UNAVAILABLE",
            "layer": "data",
            "retry": False,
            "message": "текст сообщения пользователя",
            "api_key": "sk-secret",
            "prompt": "системный промпт",
        },
    )

    assert record["payload"] == {"code": "DATA_UNAVAILABLE", "layer": "data", "retry": False}
    assert set(record["payload"]) <= set(PAYLOAD_FIELDS)


def test_paths_and_long_texts_are_dropped() -> None:
    record = build_record(
        "tool_call",
        payload={"tool": "/data/cinematch.db", "action": "я" * 300, "step": "Поиск"},
    )

    assert record["payload"] == {"step": "Поиск"}


def test_api_keys_and_auth_headers_are_dropped() -> None:
    """Ключи вида sk-… и заголовки авторизации не попадают в журнал (NFR-AG-08)."""
    record = build_record(
        "llm_call",
        payload={
            "action": "sk-proj-AbCdEf123456789",
            "code": "Bearer abc.def.ghi",
            "tool": "sk_live_51HabcXYZ",
            "layer": "basic dXNlcjpwYXNz",
            "step": "Поиск",
        },
    )

    assert record["payload"] == {"step": "Поиск"}


def test_write_event_is_one_json_line() -> None:
    import io

    stream = io.StringIO()
    record = write_event(
        "ContextExtracted",
        user_id="web:local",
        payload={"stage": "dialog"},
        stream=stream,
    )

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == record
