# CineMatch — Модуль интерфейса

| | |
|---|---|
| Ответственный | Участник проекта, реализующий модуль интерфейса |
| Версия | 0.2 |
| Дата | 03.10.2026 |
| Vision модуля | [vision_ui.md](../../docs/vision/vision_ui.md) |
| Состав документации | [documents/README.md](../README.md) |

Модуль интерфейса — то, что видит пользователь, и то, как приложение запускается. Он включает веб-приложение (чат с агентами, экраны «История и оценки» и «Мои данные»), API-слой поверх модулей агентов и данных и конфигурацию Docker Compose. Логики подбора в модуле нет: каждое сообщение передаётся агентам, их ответ отображается. Интерактивный прототип — [prototype/index.html](prototype/index.html).

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

Диаграммы UML (10): исходники PlantUML и изображения SVG — в папке [`diagrams/`](diagrams/).
