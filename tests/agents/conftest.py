"""Тесты модуля агентов: загрузчик промптов, модель и классификатор.

Реальные запросы запрещены на весь набор: модели подменяются `TestModel` и
`FunctionModel`, ключи не читаются (AGENTS.md, documents/testing/ts-ag.md).
"""

from __future__ import annotations

from pydantic_ai import models

# Запрет реальных запросов к модели на весь набор тестов: до импорта кода,
# который создаёт модели, и до выполнения любого теста.
models.ALLOW_MODEL_REQUESTS = False
