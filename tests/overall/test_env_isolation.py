"""Изоляция тестов от `.env` разработчика — `tests/conftest.py`.

Проверка фикстуры `isolated_env`: тест не должен видеть файл `.env` в корне
репозитория и не должен писать базу в `storage/` или по пути из `.env`.
Причина появления — падение `test_ingest_error_without_report_still_exits`
с `OSError: Read-only file system: '/data'` при `CINEMATCH_DB_PATH=/data/…`
в `.env` разработчика.
"""

from __future__ import annotations

from pathlib import Path

from cinematch.data import api as data_api
from cinematch.data import db
from cinematch.overall import config

REPO_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def test_dotenv_of_developer_is_not_read(tmp_path: Path) -> None:
    """`ENV_FILE` во всех трёх модулях — отсутствующий файл, а не `.env`."""
    for module in (config, db, data_api):
        env_file = module.ENV_FILE
        assert env_file != REPO_ENV_FILE, module.__name__
        assert not env_file.exists(), module.__name__
        assert env_file.parent == tmp_path, module.__name__


def test_load_settings_does_not_read_dotenv() -> None:
    """Значение по умолчанию `env_file` тоже подменено: `.env` не участвует.

    Параметр `environ` передан без `CINEMATCH_DB_PATH`: если бы файл читался,
    в `db_path` попал бы путь из `.env` разработчика, а не значение по
    умолчанию (config.md §1, п. 2).
    """
    settings = config.load_settings(
        environ={"TMDB_API_KEY": "tmdb", "LLM_API_KEY": "llm", "LLM_MODEL": "model"}
    )

    assert settings.db_path == config.DEFAULT_DB_PATH
    assert settings.llm_api_key == "llm"


def test_database_path_points_to_tmp_path(tmp_path: Path) -> None:
    """`resolve_db_path()` берёт временный путь из окружения, а не из `.env`."""
    assert db.resolve_db_path() == tmp_path / "cinematch.db"


def test_required_variables_have_test_values() -> None:
    """Обязательные переменные заданы фикстурой: старт конфигурации не падает."""
    settings = config.load_settings()

    assert settings.tmdb_api_key
    assert settings.llm_api_key
    assert settings.llm_model
    assert settings.llm_classifier_model == settings.llm_model
