"""Конфигурация: обязательные переменные и порядок тайм-аутов (config.md §1–2)."""

from __future__ import annotations

import pytest

from cinematch.overall.config import ConfigError, load_settings

BASE_ENV = {
    "TMDB_API_KEY": "tmdb",
    "LLM_API_KEY": "llm",
    "LLM_MODEL": "model",
}


def test_missing_required_variable_names_it() -> None:
    env = {k: v for k, v in BASE_ENV.items() if k != "LLM_MODEL"}

    with pytest.raises(ConfigError, match=r"^CONFIG_MISSING LLM_MODEL$"):
        load_settings(environ=env, env_file=None)


def test_missing_tmdb_key_names_it() -> None:
    env = {k: v for k, v in BASE_ENV.items() if k != "TMDB_API_KEY"}

    with pytest.raises(ConfigError, match=r"^CONFIG_MISSING TMDB_API_KEY$"):
        load_settings(environ=env, env_file=None)


def test_defaults_come_from_config_doc() -> None:
    settings = load_settings(environ=BASE_ENV, env_file=None)

    assert settings.db_path == "storage/cinematch.db"
    assert settings.user_id == "web:local"
    assert settings.llm_base_url == "https://routerai.ru/api/v1"
    assert settings.llm_timeout_s == 20
    assert settings.turn_timeout_s == 45
    assert settings.agent_timeout_s == 60
    # LLM_CLASSIFIER_MODEL по умолчанию равен LLM_MODEL.
    assert settings.llm_classifier_model == "model"


def test_timeout_order_is_checked() -> None:
    env = {**BASE_ENV, "LLM_TIMEOUT_S": "50", "TURN_TIMEOUT_S": "45", "AGENT_TIMEOUT_S": "60"}

    with pytest.raises(ConfigError) as error:
        load_settings(environ=env, env_file=None)

    assert str(error.value) == (
        "CONFIG_INVALID LLM_TIMEOUT_S: нужно LLM_TIMEOUT_S < TURN_TIMEOUT_S < AGENT_TIMEOUT_S"
    )


def test_timeout_order_rejects_equal_values() -> None:
    env = {**BASE_ENV, "LLM_TIMEOUT_S": "45", "TURN_TIMEOUT_S": "45", "AGENT_TIMEOUT_S": "60"}

    with pytest.raises(ConfigError, match="CONFIG_INVALID"):
        load_settings(environ=env, env_file=None)


def test_non_numeric_timeout_is_reported_by_name() -> None:
    env = {**BASE_ENV, "TURN_TIMEOUT_S": "сорок пять"}

    with pytest.raises(ConfigError) as error:
        load_settings(environ=env, env_file=None)

    assert str(error.value) == "CONFIG_INVALID TURN_TIMEOUT_S: ожидается целое число"


def test_process_env_wins_over_dotenv(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CINEMATCH_USER_ID=web:from-file\n", encoding="utf-8")
    env = {**BASE_ENV, "CINEMATCH_USER_ID": "web:from-env"}

    settings = load_settings(environ=env, env_file=env_file)

    assert settings.user_id == "web:from-env"


def test_dotenv_fills_value_absent_in_env(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# комментарий\nCINEMATCH_USER_ID=web:from-file\nLLM_TIMEOUT_S=10\n",
        encoding="utf-8",
    )

    settings = load_settings(environ=BASE_ENV, env_file=env_file)

    assert settings.user_id == "web:from-file"
    assert settings.llm_timeout_s == 10


def test_empty_env_value_means_default(tmp_path) -> None:
    """Пустая строка в .env — значение по умолчанию (config.md §1)."""
    env_file = tmp_path / ".env"
    env_file.write_text("CINEMATCH_DB_PATH=\n", encoding="utf-8")

    settings = load_settings(environ=BASE_ENV, env_file=env_file)

    assert settings.db_path == "storage/cinematch.db"
