# Сборка проекта по документации

*Общая часть. Документ отвечает на вопрос «что набрать руками и в каком порядке», не обращаясь к авторам модулей.*

Цель документа: человек или ИИ-агент программирования, прочитав только этот репозиторий, собирает работающее приложение. Если для какого-то файла нет документа, по которому он пишется, — это дефект документации, а не свобода реализации.

## 1. Что должно получиться

Один backend-процесс (FastAPI + модуль агентов + модуль данных) и один frontend (React), запускаемые `docker compose up`:

```
Браузер → nginx (frontend) → FastAPI /api/v1 → оркестратор → агент (Pydantic AI) → RouterAI
                                                              ├→ инструменты модуля данных → SQLite
                                                              ├→ TMDB (загрузка каталога)
                                                              └→ Open-Meteo (погода)
```

## 2. Структура репозитория

```
pyproject.toml            зависимости и настройки инструментов (uv)
uv.lock                   lock-файл, коммитится
docker-compose.yml        backend + frontend + том данных
Dockerfile.backend        python:3.14-slim, запуск uvicorn
Dockerfile.frontend       node:24-alpine → nginx:alpine
.env.example              переменные по documents/overall/config.md
AGENTS.md                 правила для ИИ-агента программирования
cinematch/
├── overall/              общий код
│   ├── config.py         чтение и проверка переменных  ← overall/config.md
│   ├── contracts.py      Pydantic-модели               ← overall/contracts.md
│   ├── errors.py         коды ошибок и тексты          ← overall/contracts.md §6
│   └── logging.py        структура записи журнала      ← agents/075 NFR-AG-13
├── agents/               модуль ИИ-агентов
│   ├── orchestrator.py   этапы, инварианты, сборка ответа ← agents/146
│   ├── classifier.py     классификатор запросов           ← agents/120, 125, ADR-005
│   ├── agent.py          агент CineMatch на Pydantic AI    ← agents/145 ADR-026, 027
│   ├── tools.py          реестр инструментов и проверки    ← agents/130, 070
│   ├── guardrails.py     проверка ответа и лимиты          ← agents/150
│   ├── trace.py          сборка трассы                     ← agents/120
│   └── prompts/          system_agent.md, classifier.md, confirm_yes_no.md,
│                         extract_context.md, retry_invalid_output.md ← agents/125
├── data/                 модуль данных
│   ├── db.py             подключение SQLite (WAL)          ← data/120, ADR-011
│   ├── models.py         SQLAlchemy-модели                 ← data/120
│   ├── api.py            функции-инструменты               ← data/130
│   ├── repositories/     запросы к таблицам                ← data/120, 130
│   ├── clients/tmdb.py   загрузка каталога                 ← data/130, data/060 UC-9
│   ├── clients/weather.py погода и геокодирование          ← data/130
│   └── services/profile.py профиль предпочтений            ← data/145 ADR-013
└── interfaces/
    ├── api/              FastAPI: app.py, routes/, sse.py, current_user.py ← ui/130
    └── web/              React + TypeScript + Vite (package.json, src/)   ← ui/110, 115
migrations/               Alembic, первая миграция ← data/120, ADR-029
scripts/load_catalog.py   CLI загрузки каталога  ← data/110, UC-9
tests/                    проверки по scenarios TS-* ← documents/testing
documents/                проектная документация (этот комплект)
documents/testing/        сценарии TS-*, 30 диалогов, 20 атак, методика оценки
documents/future/         черновики будущего функционала, не входят в MVP
docs/                     vision, дорожная карта, ТЗ
storage/                  локальные данные, в git не попадает
```

Правила размещения:

1. Модуль — пакет верхнего уровня в `cinematch/`. Никаких файлов модуля вне его пакета.
2. Общие модели и конфигурация живут только в `cinematch/overall/`; модули импортируют их, а не копируют.
3. Промпты — только файлы в `cinematch/agents/prompts/`, читаются загрузчиком при старте; строки промптов в коде не пишутся.
4. Документация модуля — `documents/<модуль>/`; общая — `documents/overall/`. Совпадение имён `overall` в коде и документации intentional: это один и тот же слой.

## 3. Порядок старта backend

1. Прочитать `.env` и переменные окружения; проверить обязательные (`TMDB_API_KEY`, `LLM_API_KEY`, `LLM_MODEL`). При отсутствии — остановиться с сообщением `CONFIG_MISSING <имя>`.
2. Загрузить промпты из `cinematch/agents/prompts/`; отсутствие файла — ошибка старта.
3. Открыть SQLite по `CINEMATCH_DB_PATH`, включить WAL и внешние ключи.
4. Применить миграции: `alembic upgrade head`.
5. Выполнить `purge_expired()` — удалить просроченные сессии.
6. Зарегистрировать маршруты, включая `/api/v1/health`, который отдаёт `{"status": "ok", "catalog_size": N}`.
7. Записать в журнал `backend ready` и начать принимать запросы.

Если каталог пуст, backend стартует: `/health` вернёт `catalog_size: 0`, а запрос подбора получит `CATALOG_EMPTY` по контракту.

## 4. Команды

Установка и локальный запуск:

```bash
cp .env.example .env          # вписать TMDB_API_KEY, LLM_API_KEY, LLM_MODEL
uv sync
uv run alembic upgrade head
uv run python -m scripts.load_catalog
uv run uvicorn cinematch.interfaces.api.app:app --reload
```

Docker:

```bash
docker compose up --build -d
docker compose run --rm backend python -m scripts.load_catalog
curl -s localhost:8080/api/v1/health
```

Проверки:

```bash
uv run pytest -q
python scripts/check_docs.py     # проверка документации и ссылок
```

Параметры загрузки каталога (число страниц, годы, порог голосов) — [110 модуля данных](../data/110.ui-ux.md). Тестовые сценарии и ожидаемые результаты — `documents/testing/` (в подготовке; до его появления — [095 модуля агентов](../agents/095.requirements-check.md)).

## 5. Зависимости

1. Единственный источник — `pyproject.toml`; версии фиксируются, `uv.lock` коммитится.
2. Python 3.14 (ADR-028). Backend-зависимости: FastAPI, uvicorn, Pydantic v2, pydantic-ai, SQLAlchemy 2.0, Alembic, httpx, sse-starlette.
3. Frontend: React, TypeScript, Vite; сборка в статику, раздача nginx; прокси `/api` на backend, `proxy_buffering off` для SSE.
4. Новую зависимость добавляют только вместе с записью в ADR модуля, который её использует.

## 6. Карта «файл → документ»

| Файл | Документ, по которому пишется |
|---|---|
| `cinematch/overall/config.py` | [config.md](config.md) |
| `cinematch/overall/contracts.py`, `errors.py` | [contracts.md](contracts.md) |
| `cinematch/overall/logging.py` | [075 агентов](../agents/075.nfr.md) NFR-AG-13 |
| `cinematch/agents/orchestrator.py` | [146 агентов](../agents/146.logic-scheme.md), [130 агентов](../agents/130.api-contracts.md) |
| `cinematch/agents/classifier.py` | [120](../agents/120.data-model.md), [125](../agents/125.prompts.md) |
| `cinematch/agents/agent.py` | [125](../agents/125.prompts.md), [145](../agents/145.architecture-decisions.md) ADR-026, ADR-027 |
| `cinematch/agents/tools.py` | [130](../agents/130.api-contracts.md), [070](../agents/070.role-matrix.md) |
| `cinematch/agents/guardrails.py` | [150](../agents/150.component-diagram.md), [075](../agents/075.nfr.md) |
| `cinematch/agents/prompts/*` | [125](../agents/125.prompts.md) |
| `cinematch/data/db.py`, `models.py` | [120 данных](../data/120.data-model.md) |
| `cinematch/data/api.py` | [130 данных](../data/130.api-contracts.md) |
| `cinematch/data/clients/*` | [130 данных](../data/130.api-contracts.md), [060 данных](../data/060.use-cases.md) UC-9 |
| `cinematch/data/services/profile.py` | [145 данных](../data/145.architecture-decisions.md) ADR-013 |
| `cinematch/interfaces/api/*` | [130 интерфейса](../ui/130.api-contracts.md), [140 интерфейса](../ui/140.event-schema.md) |
| `cinematch/interfaces/web/*` | [110 интерфейса](../ui/110.ui-ux.md), [115 интерфейса](../ui/115.live-prototype.md) |
| `migrations/versions/*` | [120 данных](../data/120.data-model.md) |
| `scripts/load_catalog.py` | [110 данных](../data/110.ui-ux.md), [060 данных](../data/060.use-cases.md) UC-9 |
| `tests/*` | [095](../agents/095.requirements-check.md), [testing](../testing/README.md) |

## 7. Как этим пользоваться ИИ-агенту программирования

Порядок чтения и запреты — [AGENTS.md](../../AGENTS.md). Коротко:

1. Прочитать `README.md`, затем `documents/overall/contracts.md`, `config.md`, этот документ.
2. Прочитать документацию своего модуля (`documents/agents/…` или `documents/data/…`) и контракт соседнего модуля, с которым идёт обмен.
3. Писать код по карте раздела 6. Не добавлять поля, коды и значения, которых нет в `contracts.md`.
4. После изменений — `uv run pytest -q` и `python scripts/check_docs.py`; при изменении контракта — ADR и одобрение всех трёх модулей.

## 8. Приёмка «сборка по документам»

Проверяют двое: участник другого модуля и человек, не участвовавший в проектировании. На каждый вопрос — один ответ по ссылке из документации.

1. Какие команды выполняются от чистого клона до открытия страницы в браузере?
2. В каком порядке стартует backend и что происходит при пустом каталоге?
3. Какие переменные обязательны и что при их отсутствии?
4. Какие модели описывает `contracts.py` и какие поля обязательны?
5. Что возвращает `/start` на каждом этапе и куда он не попадает?
6. Какие события `progress` и в каком порядке приходят на одно сообщение?
7. Какие коды ошибок существуют, кто их получает и какой текст видит пользователь?
8. По какому документу пишется каждый файл из структуры раздела 2?
