"""Чтение и проверка переменных окружения.

Единственный источник значений — documents/overall/config.md §1–2.
Переменная окружения процесса важнее значения из `.env`: файл лишь дополняет
окружение и не перекрывает уже заданные переменные (build.md §3, п. 1).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REQUIRED_VARS = ("TMDB_API_KEY", "LLM_API_KEY", "LLM_MODEL")

DEFAULT_DB_PATH = "storage/cinematch.db"
DEFAULT_USER_ID = "web:local"
DEFAULT_LLM_BASE_URL = "https://routerai.ru/api/v1"
DEFAULT_LLM_TIMEOUT_S = 20
DEFAULT_TURN_TIMEOUT_S = 45
DEFAULT_AGENT_TIMEOUT_S = 60

ENV_FILE = Path(".env")


class ConfigError(RuntimeError):
    """Ошибка конфигурации при старте backend.

    Текст исключения начинается с `CONFIG_MISSING` или `CONFIG_INVALID` —
    это то, что попадает в журнал и stderr (config.md §1, §2).
    """


@dataclass(frozen=True, slots=True)
class Settings:
    """Значения конфигурации после проверки."""

    tmdb_api_key: str
    db_path: str
    user_id: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_classifier_model: str
    llm_timeout_s: int
    turn_timeout_s: int
    agent_timeout_s: int


def _parse_env_file(path: Path) -> dict[str, str]:
    """Разобрать `.env` без внешних зависимостей.

    Понимает строки `ИМЯ=значение`, комментарии `#`, пустые строки и
    необязательные кавычки вокруг значения. Неизвестные переменные не
    отбрасываются: проверка списка — задача `_read`.
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return values

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values


def _read(
    name: str,
    *,
    values: Mapping[str, str],
    default: str | None,
) -> str:
    """Взять значение: окружение важнее `.env`, затем значение по умолчанию."""
    value = values.get(name, "")
    if value:
        return value
    if default is not None:
        return default
    raise ConfigError(f"CONFIG_MISSING {name}")


def _read_int(name: str, *, values: Mapping[str, str], default: int) -> int:
    raw = values.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"CONFIG_INVALID {name}: ожидается целое число") from None


def load_settings(
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = ENV_FILE,
) -> Settings:
    """Прочитать `.env` и окружение, проверить обязательные значения и тайм-ауты."""
    process_env = os.environ if environ is None else environ
    file_env: dict[str, str] = {}
    if env_file is not None:
        file_env = _parse_env_file(env_file)

    # Окружение процесса важнее файла.
    values: dict[str, str] = {**file_env, **process_env}

    llm_model = _read("LLM_MODEL", values=values, default=None)
    settings = Settings(
        tmdb_api_key=_read("TMDB_API_KEY", values=values, default=None),
        db_path=_read("CINEMATCH_DB_PATH", values=values, default=DEFAULT_DB_PATH),
        user_id=_read("CINEMATCH_USER_ID", values=values, default=DEFAULT_USER_ID),
        llm_base_url=_read("LLM_BASE_URL", values=values, default=DEFAULT_LLM_BASE_URL),
        llm_api_key=_read("LLM_API_KEY", values=values, default=None),
        llm_model=llm_model,
        # LLM_CLASSIFIER_MODEL по умолчанию равен LLM_MODEL (config.md §1).
        llm_classifier_model=_read("LLM_CLASSIFIER_MODEL", values=values, default=llm_model),
        llm_timeout_s=_read_int("LLM_TIMEOUT_S", values=values, default=DEFAULT_LLM_TIMEOUT_S),
        turn_timeout_s=_read_int("TURN_TIMEOUT_S", values=values, default=DEFAULT_TURN_TIMEOUT_S),
        agent_timeout_s=_read_int(
            "AGENT_TIMEOUT_S", values=values, default=DEFAULT_AGENT_TIMEOUT_S
        ),
    )

    check_timeouts(settings)
    return settings


def check_timeouts(settings: Settings) -> None:
    """Условие согласованности из config.md §2: LLM < TURN < AGENT."""
    if not (settings.llm_timeout_s < settings.turn_timeout_s < settings.agent_timeout_s):
        raise ConfigError(
            "CONFIG_INVALID LLM_TIMEOUT_S: нужно LLM_TIMEOUT_S < TURN_TIMEOUT_S < AGENT_TIMEOUT_S"
        )
