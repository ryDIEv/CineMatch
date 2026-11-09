# CineMatch — Модуль данных

| | |
|---|---|
| Ответственный | Участник проекта, реализующий модуль данных |
| Версия | 0.4 |
| Дата | 09.11.2026 |
| Vision модуля | [vision_db.md](../../docs/vision/vision_db.md) |
| Состав документации | [documents/README.md](../README.md) |

Модуль данных — единственная часть системы, которая работает с базой данных и внешними источниками данных. Остальные модули не выполняют SQL-запросов и не обращаются к TMDB и Open-Meteo, а вызывают функции модуля: инструменты (их вызывает агент CineMatch) и служебные функции (их вызывают оркестратор и API-слой).

Документация модуля описывает две вещи, по которым пишется код без догадок: **SQLite** (схема, ограничения, индексы, миграции, правила записи) и **TMDB API** (запросы загрузки, нормализация, ограничение частоты, повторы, возрастная маркировка). Остальное — функции, погода, события — описано настолько, насколько нужно для сборки.

## Источники истины

| Что | Где | Где этого нет |
|---|---|---|
| Поля `MovieCard`, `RecommendedMovie`, коды и тексты ошибок, пределы | [overall/contracts.md](../overall/contracts.md) | в документах модуля |
| Переменные окружения, тайм-ауты, путь к базе | [overall/config.md](../overall/config.md) | в `.env.example` |
| Структура пакетов, команды, порядок старта | [overall/build.md](../overall/build.md) | в README |
| Правила «контекст → критерии поиска», жизненный цикл сессии | [120 агентов](../agents/120.data-model.md) | в модуле данных |
| Схема `SessionState` | [contracts §2.5](../overall/contracts.md#25-sessionstate--состояние-диалога) | в `state_json` |
| Схема таблиц SQLite | [120](120.data-model.md) | в тексте остальных документов |
| Сигнатуры функций и проверки параметров | [130](130.api-contracts.md) | — |
| Запросы к TMDB и Open-Meteo | [130 §7–8](130.api-contracts.md#7-загрузка-каталога) | — |
| Архитектурные решения модуля | [145](145.architecture-decisions.md) | — |

Правило: если два документа расходятся — прав `overall/`; расхождение исправляется в том же изменении.

## Порядок чтения

1. [010 — Глоссарий](010.glossary.md), [020 — System Context](020.system-context.md) — границы модуля.
2. [120 — Data Model](120.data-model.md) — схема SQLite и правила заполнения.
3. [130 — API Contracts](130.api-contracts.md) — функции, параметры, запросы к TMDB и Open-Meteo.
4. [145 — ADR](145.architecture-decisions.md) — решения, включая формулу профиля (ADR-013) и возрастную маркировку (ADR-018).
5. [075 — NFR](075.nfr.md), [095 — Traceability Matrix](095.requirements-check.md) — числа и проверки.
6. Остальные документы — по мере необходимости; контракт соседнего модуля — [130 агентов](../agents/130.api-contracts.md).

Каждый документ описывает основной функционал (MVP) и в конце содержит раздел «Будущий функционал».

| Блок | Документ |
|---|---|
| A. Контекст и границы системы | [010 — Глоссарий](010.glossary.md) |
| A. Контекст и границы системы | [020 — System Context](020.system-context.md) |
| A. Контекст и границы системы | [030 — Actors](030.actors.md) |
| B. Анализ предметной области | [040 — Event Storming](040.event-storming.md) |
| B. Анализ предметной области | [050 — Entities + Lifecycle](050.entities.md) |
| B. Анализ предметной области | [060 — Use Cases](060.use-cases.md) |
| C. Требования и роли | [070 — RBAC](070.role-matrix.md) |
| C. Требования и роли | [075 — NFR](075.nfr.md) |
| C. Требования и роли | [080 — User Stories](080.user-stories.md) |
| C. Требования и роли | [090 — Epics / Features](090.features-epics.md) |
| D. Верификация | [095 — Traceability Matrix](095.requirements-check.md) |
| E. Проектирование интерфейсов | [110 — UI/UX](110.ui-ux.md) |
| E. Проектирование интерфейсов | [115 — Live-прототип](115.live-prototype.md) |
| F. Проектирование данных | [120 — Data Model](120.data-model.md) |
| G. Контракты и события | [130 — API Contracts](130.api-contracts.md) |
| G. Контракты и события | [135 — Event Catalog](135.event-catalog.md) |
| G. Контракты и события | [140 — Event Schema](140.event-schema.md) |
| H. Архитектура и логика | [145 — ADR](145.architecture-decisions.md) |
| H. Архитектура и логика | [146 — Logic Scheme](146.logic-scheme.md) |
| H. Архитектура и логика | [150 — Component Diagram](150.component-diagram.md) |
| I. Интеграция и развёртывание | [155 — Sequence-диаграммы](155.sequence-diagrams.md) |
| I. Интеграция и развёртывание | [160 — Integration Plan](160.integration-plan.md) |
| I. Интеграция и развёртывание | [165 — Deployment Diagram](165.deployment-diagram.md) |

Диаграммы UML (12): исходники PlantUML и изображения SVG — в папке [`diagrams/`](diagrams/).
