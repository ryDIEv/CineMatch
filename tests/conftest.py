"""Общая изоляция тестов от окружения разработчика.

Тесты не должны зависеть от `.env` в корне репозитория: файл принадлежит
разработчику, в нём бывают боевые ключи и пути контейнера (например,
`CINEMATCH_DB_PATH=/data/cinematch.db`). До появления этой фикстуры
`init_database()` читал `.env` и пытался создать каталог `/data`, которого на
машине разработчика нет, — тест падал с `OSError: Read-only file system`.

Фикстура `isolated_env` действует автоматически на каждый тест:

* `cinematch.overall.config.ENV_FILE` указывает на несуществующий файл в
  `tmp_path`, поэтому `.env` не читается. Менять нужно ещё и
  `load_settings.__kwdefaults__`: путь к файлу зашит в значение по умолчанию
  параметра `env_file` (`config.md` §1, п. 6), и одного `monkeypatch.setattr`
  модуля недостаточно;
* `cinematch.data.db.ENV_FILE` и `cinematch.data.api.ENV_FILE` — те же модули
  импортируют имя по значению (`from … import ENV_FILE`), поэтому патчатся
  отдельно;
* `CINEMATCH_DB_PATH` — файл в `tmp_path`: база не попадает в `storage/`;
* `TMDB_API_KEY`, `LLM_API_KEY`, `LLM_MODEL` — тестовые значения, чтобы старт
  конфигурации не зависел от `.env`.

Фикстуры, которым нужны другие значения, задают их сами: `monkeypatch.setenv`
в `tests/data/conftest.py` (`data_db`, `tmdb`) и `tests/overall/test_health.py`
перекрывают значения этой фикстуры, потому что выполняются позже. Тесты,
которым значение нужно убрать, вызывают `monkeypatch.delenv` — `monkeypatch`
возвращает исходное состояние после теста независимо от порядка.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cinematch.data import api as data_api
from cinematch.data import db
from cinematch.overall import config

# Тестовые значения обязательных переменных (config.md §1): реальные ключи
# в тестах не нужны, сеть запрещена (AGENTS.md, documents/testing/ts-ag.md).
TEST_TMDB_API_KEY = "test-tmdb-key"
TEST_LLM_API_KEY = "test-llm-key"
TEST_LLM_MODEL = "test-model"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Отвязать тесты от `.env` разработчика и дать каждому свою базу."""
    absent_env_file = tmp_path / "no-such.env"

    # config.py: и атрибут модуля, и значение по умолчанию параметра `env_file`.
    monkeypatch.setattr(config, "ENV_FILE", absent_env_file)
    defaults = config.load_settings.__kwdefaults__
    if defaults is not None and "env_file" in defaults:
        monkeypatch.setitem(defaults, "env_file", absent_env_file)

    # Модули данных импортировали имя по значению — патчим их атрибуты.
    monkeypatch.setattr(db, "ENV_FILE", absent_env_file)
    monkeypatch.setattr(data_api, "ENV_FILE", absent_env_file)

    monkeypatch.setenv("CINEMATCH_DB_PATH", str(tmp_path / "cinematch.db"))
    monkeypatch.setenv("TMDB_API_KEY", TEST_TMDB_API_KEY)
    monkeypatch.setenv("LLM_API_KEY", TEST_LLM_API_KEY)
    monkeypatch.setenv("LLM_MODEL", TEST_LLM_MODEL)
    yield
