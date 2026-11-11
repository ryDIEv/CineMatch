# tests

Каталог для проверок по сценариям `TS-AG`, `TS-DB`, `TS-UI`. Сценарии — в `documents/testing/`.

- `fixtures/seed_movies.json` — 20 фильмов для тестовой базы (ADR-042). В поставку и том данных не попадает.
- Подмена LLM и e2e-тесты добавляются по `documents/testing/ts-ag.md`; реальные вызовы RouterAI в unit-тестах запрещены.

Запуск после появления кода: `uv run pytest -q`.
