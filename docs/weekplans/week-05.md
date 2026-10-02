# Неделя 5 · 28.09–04.10 · Чтение литературы

## Цель

Разобраться в теме, чтобы на следующей неделе написать ТЗ и начать обзор источников для ПЗ. Код проекта на этой неделе не пишется.

## Результат

По каждому модулю конспекты в `docs/research/`, добавленные через общий PR.

| Модуль | Файл | Ветка |
|---|---|---|
| Данные | `docs/research/data.md` | `research/data-week5` |
| ИИ-агенты | `docs/research/agents.md` | `research/agents-week5` |
| Интерфейс | `docs/research/ui.md` | `research/ui-week5` |

## Сроки

| Когда | Что |
|---|---|
| до пт 02.10 | PR с конспектом открыт |
| до сб 03.10 | Каждый PR просмотрен хотя бы одним другим участником |
| до вс 04.10 | Созвон, правки |
| до пн 05.10 | Черновики ТЗ по модулям |

---

## Модуль данных

**Источники**

1. TMDB — [Getting Started](https://developer.themoviedb.org/docs/getting-started), [FAQ](https://developer.themoviedb.org/docs/faq), [discover/movie](https://developer.themoviedb.org/reference/discover-movie), [условия использования](https://www.themoviedb.org/api-terms-of-use)
2. SQLite — [When to use SQLite](https://www.sqlite.org/whentouse.html), [WAL](https://www.sqlite.org/wal.html), [Query Planning](https://www.sqlite.org/queryplanner.html)
3. Open-Meteo — [Forecast API](https://open-meteo.com/en/docs), [Geocoding API](https://open-meteo.com/en/docs/geocoding-api)
4. Google — [Recommendation Systems](https://developers.google.com/machine-learning/recommendation): обзор и content-based filtering

**Вопросы**

- Чем TMDB лучше альтернатив: OMDb, Кинопоиск, Wikidata, MovieLens?
- Почему SQLite, а не PostgreSQL?
- Как рассчитывать веса профиля предпочтений из оценок?

## Модуль ИИ-агентов

**Источники**

1. Anthropic — [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
2. Tool use — [документация Claude](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview), [пример functions в GigaChat](https://github.com/ai-forever/gigachat/blob/main/examples/example_functions.ipynb)
3. [ReAct](https://arxiv.org/abs/2210.03629) — аннотация, введение, раздел 2
4. OWASP — [LLM01: Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
5. [InteRecAgent](https://arxiv.org/abs/2308.16505) — аннотация, введение, архитектура

**Вопросы**

- Чем агент отличается от workflow?
- Как устроен вызов инструмента: что отправляем модели, что она возвращает?
- Какие меры защиты от prompt injection применимы к проекту?
- Какой LLM API использовать в MVP?

## Модуль интерфейса

**Источники**

1. [aiogram 3](https://docs.aiogram.dev/) — обработчики, inline-кнопки, callback-запросы
2. [Telegram Bot API](https://core.telegram.org/bots/api) — лимиты сообщений и кнопок
3. [rich](https://rich.readthedocs.io/) — оформление вывода в консоли
4. Аналоги — [moodcast](https://github.com/DamianEhrenburg/moodcast), [Movie-Magic](https://github.com/itsyaba/Movie-Magic), Кинопоиск, JustWatch, Letterboxd

**Вопросы**

- Нужна ли FSM в aiogram, если состояние диалога хранит модуль агентов?
- Как показать три фильма и варианты ответа в консоли и в Telegram?
- Чем CineMatch отличается от аналогов? (сравнительная таблица)

## Общее для всех: работа на GitHub

Git — это команды на своём компьютере. GitHub — платформа поверх него: pull request, ревью, задачи, доска проекта. Здесь только то, чем команда будет пользоваться весь семестр. Ориентир — 3–4 часа.

**Интерактивные курсы GitHub Skills** — проходятся прямо в своём репозитории, бот проверяет каждый шаг:

1. [Review pull requests](https://github.com/skills/review-pull-requests) — как оставлять комментарии к строкам, предлагать правки, одобрять или запрашивать изменения
2. [Resolve merge conflicts](https://github.com/skills/resolve-merge-conflicts) — откуда берутся конфликты и как их решать

**Документация GitHub:**

3. [GitHub flow](https://docs.github.com/en/get-started/using-github/github-flow) — процесс «ветка → PR → ревью → слияние», по которому работает команда
4. [Quickstart for GitHub Issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/learning-about-issues/quickstart) — задачи, метки, исполнители, связь задачи с PR через `Closes #N`
5. [Quickstart for Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/quickstart-for-projects) — доска задач проекта
6. [Resolving a merge conflict on GitHub](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/resolving-a-merge-conflict-on-github) — решение конфликта прямо в браузере

**Статьи на русском:**

7. [Лучший Pull Request](https://habr.com/ru/articles/272531/) (Хабр) — каким должен быть PR, чтобы его было удобно ревьюить
8. [Code review — улучшаем процесс](https://habr.com/ru/articles/489880/) (Хабр) — как проводить ревью и писать комментарии
9. [Практическое занятие «Процесс Pull request на GitHub»](https://starkovden.github.io/Pull-request-workflows.html) — пошаговый разбор с упражнением

**Видео (по желанию):** [Pull request на практике](https://www.youtube.com/watch?v=G_HKJJLozUc), [Уроки по Git #8: Pull request](https://www.youtube.com/watch?v=YRTEelEOD-Q).

**К концу недели каждый умеет:**

- создать ветку, открыть PR и связать его с задачей через `Closes #N`
- оставить комментарий к конкретной строке и предложить правку (suggestion) в чужом PR
- одобрить PR или запросить изменения
- решить конфликт слияния
- передвинуть свою задачу по доске проекта

Проверка на практике — PR с конспектом: каждый получает хотя бы один комментарий к строке и сам оставляет хотя бы один в чужом PR.

---

## Шаблон конспекта

```markdown
# Конспект: <модуль>

## <Название источника>
- Ссылка:
- Главное (3–5 пунктов своими словами):
- Что берём в проект:

## Ответы на вопросы недели
1.

## Открытые вопросы для созвона
-

## Список источников для ПЗ
1. <Название>. — URL: <ссылка> (дата обращения: ДД.ММ.ГГГГ).
```

Пишем своими словами и коротко. Если ответ не найден — так и указываем, это тоже результат.

## Повестка созвона · сб 03.10, 21:00

1. Главное из прочитанного — по 10 минут на модуль.
2. Выбор LLM API для MVP.
3. Структура ТЗ и ПЗ, задачи на неделю 6.
