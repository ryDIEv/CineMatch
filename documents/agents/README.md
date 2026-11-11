# CineMatch — Модуль ИИ-агентов

| | |
|---|---|
| Ответственный | Участник проекта, реализующий модуль ИИ-агентов |
| Версия | 0.5 |
| Дата | 11.11.2026 |
| Vision модуля | [vision_llm.md](../../docs/vision/vision_llm.md) |
| Общие контракты | [documents/overall](../overall/README.md): [contracts](../overall/contracts.md), [config](../overall/config.md), [build](../overall/build.md) |
| Состав документации | [documents/README.md](../README.md) |

Модуль ведёт диалог с пользователем. Он состоит из оркестратора (обычный код), классификатора запросов и ИИ-агента CineMatch. Оркестратор отвечает за то, что нельзя доверить модели: согласие, выбор города, удаление данных, лимиты и проверку ответа. Агент сам решает, что спросить, какие инструменты вызвать и какие фильмы предложить. Обоснование архитектуры — в [обзоре](../rationale.md#42-архитектурная-схема), решения 08.11.2026 — [ADR-030…ADR-040](145.architecture-decisions.md#решения-08112026), решения 11.11.2026 — [ADR-043…ADR-046](145.architecture-decisions.md#решения-11112026).

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
| E. Проектирование интерфейсов | [110 — UI/UX: диалоговый дизайн](110.ui-ux.md) |
| E. Проектирование интерфейсов | [115 — Live-прототип: эталонные диалоги](115.live-prototype.md) |
| F. Проектирование данных | [120 — Data Model: структуры модуля](120.data-model.md) |
| F. Проектирование данных | [125 — Промпты](125.prompts.md) |
| G. Контракты и события | [130 — API Contracts](130.api-contracts.md) |
| G. Контракты и события | [135 — Event Catalog](135.event-catalog.md) |
| G. Контракты и события | [140 — Event Schema](140.event-schema.md) |
| H. Архитектура и логика | [145 — ADR](145.architecture-decisions.md) |
| H. Архитектура и логика | [146 — Logic Scheme](146.logic-scheme.md) |
| H. Архитектура и логика | [150 — Component Diagram](150.component-diagram.md) |
| I. Интеграция и развёртывание | [155 — Sequence-диаграммы](155.sequence-diagrams.md) |
| I. Интеграция и развёртывание | [160 — Integration Plan](160.integration-plan.md) |
| I. Интеграция и развёртывание | [165 — Deployment Diagram](165.deployment-diagram.md) |

Документ `125` добавлен 08.11.2026 и в типовой состав блоков не входит: промпты — артефакт реализации, а не этап проектирования. Диаграммы UML (11): исходники PlantUML и изображения SVG — в папке [`diagrams/`](diagrams/).
