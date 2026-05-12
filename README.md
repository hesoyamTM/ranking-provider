# Provider Ranking Agent

Диалоговый агент для подбора и сравнения облачных услуг российских провайдеров (T1 Cloud, Cloud.ru, Yandex Cloud, Selectel, VK Cloud). Сервис общается с пользователем на естественном языке, итеративно собирает спецификацию инфраструктуры, после чего семантически ранжирует подходящие услуги по каждому компоненту и формирует Markdown-рекомендацию.

---

## TL;DR

```bash
# 1. Развернуть БД (Postgres + pgvector)
docker compose up -d db

# 2. Скопировать переменные окружения и заполнить YANDEX_*
cp .env.example .env

# 3. Применить миграции и запустить агент + воркер синхронизации
uv run python main.py migrate
uv run python main.py run
```

После старта откройте `http://localhost:8000/` — это встроенный чат-UI.

---

## Содержание документации

Корневой `README.md` (этот файл) даёт общее представление. Подробности каждого блока — в `docs/`:

| Файл | Что внутри |
|---|---|
| [docs/01-architecture.md](docs/01-architecture.md) | Слои приложения, dependency graph, ключевые решения |
| [docs/02-data-model.md](docs/02-data-model.md) | Domain models: `Provider`, `Service`, `SessionState`, `Message`, `Score` |
| [docs/03-providers.md](docs/03-providers.md) | Источники данных по каждому провайдеру (PDF/HTML/API + LLM-извлечение) |
| [docs/04-agent-pipeline.md](docs/04-agent-pipeline.md) | Конечный автомат диалога, sub-агенты, prompts, tool loop |
| [docs/05-rag-scoring.md](docs/05-rag-scoring.md) | RAG-ранжирование через Yandex AI Studio, фильтры, форматтер |
| [docs/06-persistence.md](docs/06-persistence.md) | Postgres-схема, yoyo-миграции, репозитории, pgvector |
| [docs/07-rest-api.md](docs/07-rest-api.md) | REST-эндпоинты, стриминг, cookie-идентификация, HTML-UI |
| [docs/08-sync-worker.md](docs/08-sync-worker.md) | Периодический sync, геокодинг, CLI-команды |
| [docs/09-configuration.md](docs/09-configuration.md) | Переменные окружения, feature flags |
| [docs/10-deployment.md](docs/10-deployment.md) | Dockerfile, docker-compose, GitHub Actions deploy |

---

## Что делает сервис

1. **Собирает каталог услуг** провайдеров (фоновый воркер `ProviderSyncWorker`):
   - T1 Cloud — PDF-документы (локальные или скачанные с `t1-cloud.ru`).
   - Cloud.ru — HTML-обход каталога + PDF тарифов.
   - Selectel — REST-эндпоинты `api.selectel.ru`.
   - Yandex Cloud — публичный API `yandex.cloud/api/priceList`.
   - VK Cloud — HTML pricelist.
   - Извлечение нормализуется LLM (YandexGPT) либо детерминированно по типу источника.
   - Результат — `Provider` + список `Service` (категория, цена, теги, регионы).
   - Регионы геокодятся через `NominatimGeocoder` (статическая таблица + OSM fallback).
   - Сохраняется в Postgres (с pgvector в схеме под будущие эмбеддинги).

2. **Ведёт диалог с пользователем** (FastAPI + стриминг):
   - Конечный автомат `AgentOrchestrator` управляет фазами:
     `EXTRACTING → CLARIFYING → CONFIRMING → RANKING → SYNTHESIZING → DONE`.
   - Каждая фаза — отдельный sub-агент с собственным промптом и tool schema:
     `IntentClassifier`, `ExtractionAgent`, `ClarificationAgent`, `ConfirmationAgent`, `SynthesisAgent`.
   - `SessionState` персистится в `session_states` после каждого хода.

3. **Ранжирует услуги** (`SynthesisAgent`):
   - LLM в tool-loop вызывает `rank_by_rag` (Yandex AI Studio RAG-агент) для каждого компонента.
   - Получает список провайдеров через `get_providers` (whitelist для финального ответа).
   - Формирует итоговый Markdown по строгому формату из `prompts/synthesis.md`.

4. **Отдаёт результат** клиенту через `text/plain` streaming response. Фронтенд (`index.html`) рендерит чат, статусы (`__STATUS__:...`) и подсказки-кнопки (`__SUGGESTIONS__:...`).

---

## High-level pipeline

```
            ┌──────────────────────────────────────────────────────────┐
            │                  ProviderSyncWorker (фон)                 │
            │  fetch() ─► Geocode regions ─► PostgresServiceRepository  │
            └──────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                            ┌────────────────────┐
                            │ Postgres + pgvector│
                            │  providers/services│
                            └────────────────────┘
                                       ▲
                                       │ get_providers / списки услуг
                                       │
┌──────────┐  POST /chats/{id}/messages ┌─────────────────────────────────┐
│  Client  │ ──────────────────────────►│        FastAPI router            │
│ (HTML UI)│ ◄── stream text/plain ─────│  /chats, /chats/{id}/messages    │
└──────────┘                            └─────────────────────────────────┘
                                                       │
                                                       ▼
                                         ┌───────────────────────────┐
                                         │    ChatService            │
                                         │    AgentOrchestrator      │
                                         └────────────┬──────────────┘
                                                      │
       Intent ─► Extraction ─► (Clarify ↻) ─► Confirm ─► Synthesis(tool loop)
                                                      │
                                       ┌──────────────┼──────────────┐
                                       ▼              ▼              ▼
                                 YandexGPT      Yandex RAG      Nominatim
                                 (chat+tools)   (responses)     (geocode)
```

---

## Стек

- **Python 3.13**, `uv` для управления зависимостями.
- **FastAPI + Uvicorn** — REST + стриминг.
- **PostgreSQL + pgvector** — каталог услуг и история чатов.
- **yoyo-migrations** — миграции БД.
- **YandexGPT** (через OpenAI-совместимый `openai` SDK) — LLM для классификации, извлечения, синтеза.
- **Yandex AI Studio Responses API** — RAG-агент для семантического поиска услуг.
- **Pydantic v2** — domain models / DTO.
- **httpx**, **pypdf**, **BeautifulSoup4** — сбор данных у провайдеров.
- **Typer** — CLI (`migrate`, `sync-once`, `run`).
- **Docker** + GitHub Actions — деплой.

---

## CLI

```bash
uv run python main.py migrate     # применить миграции
uv run python main.py sync-once   # разово синхронизировать каталог
uv run python main.py run         # миграции + воркер + HTTP-сервер
```

См. [docs/08-sync-worker.md](docs/08-sync-worker.md) и [docs/10-deployment.md](docs/10-deployment.md).

---

## Структура репозитория

```
.
├── main.py                  # Typer CLI entrypoint
├── docker-compose.yml       # Postgres (pgvector) + pgAdmin
├── Dockerfile               # uv-based multi-stage build
├── migration/               # yoyo-migrations (yyyy-mm-dd_NN_*.py)
├── data/                    # сырьё для T1 / VK провайдеров (PDF/HTML)
├── docs/                    # подробная документация (см. таблицу выше)
└── src/
    ├── application/app.py   # Composition root (DI, lifecycle)
    ├── config/config.py     # Settings.from_env()
    ├── models/              # Domain: Provider, Service, SessionState, Message
    ├── controller/          # FastAPI router + DTO + HTML-фронтенд
    ├── service/             # Use-cases: chat, scorer, worker, agent/*
    │   └── agent/           # AgentOrchestrator + sub-agents + prompts + tools
    └── adapters/            # Внешние интеграции:
        ├── providers/       #   T1/CloudRu/Yandex/Selectel/VK
        ├── llm/             #   YandexGPT (OpenAI-compatible)
        ├── rag/             #   Yandex AI Studio agent
        ├── geocoding/       #   Nominatim
        └── repository/      #   Postgres / in-memory
```

---

## Ключевые архитектурные решения

1. **Hexagonal-style layering.** `service/` зависит только от протоколов (`Protocol` из `typing`), реальные интеграции — в `adapters/`. Это позволяет подменять Postgres на in-memory, YandexGPT на любой OpenAI-совместимый клиент, RAG на mock. См. [docs/01-architecture.md](docs/01-architecture.md).

2. **Multi-agent FSM вместо одного промпта.** Диалог разрезан на 5 фаз — каждая решает узкую задачу и имеет свой `system prompt` (`src/service/agent/prompts/*.md`). Это даёт детерминированный flow и упрощает отладку. См. [docs/04-agent-pipeline.md](docs/04-agent-pipeline.md).

3. **Function calling вместо парсинга свободного текста.** Все «решения» LLM (intent, патч спеки, подтверждение) выражаются через tool_calls с JSON-схемой — нет regex-постпроцессинга.

4. **Stateful SessionState в JSONB.** Состояние диалога (компоненты, missing-поля, фаза, попытки уточнения) сериализуется в Postgres JSONB. Pydantic-модель — single source of truth.

5. **RAG-агент как чёрный ящик.** Семантический поиск делегирован Yandex AI Studio (`prompt.id` указывает на сконфигурированного агента). Локальные эмбеддинги в `services.embedding` пока не используются, но схема уже подготовлена (vector(384) + ivfflat index).

6. **Streaming-first UX.** Каждый sub-агент возвращает `AsyncGenerator[str, None]`. Статусы tool-вызовов проксируются клиенту префиксами `__STATUS__:` и `__SUGGESTIONS__:` — UI отрисовывает их отдельно от текста ответа.

7. **Provider sync — pull, не push.** Каждый адаптер сам идёт за каталогом (PDF/HTML/REST). `ProviderSyncWorker` параллелит через `asyncio.gather`. См. [docs/03-providers.md](docs/03-providers.md).

8. **Идентификация пользователя без логина.** `user_id` — это `uuid5(NAMESPACE, User-Agent)`, сохраняется в `httponly` cookie на год. Подходит для демо/MVP; для прод-сценария замените на JWT/OAuth.

---

## Тесты

```bash
uv run pytest
```

В репозитории есть `tests/test_scorer.py` (на старую версию `RAGScoringService`); актуальная имплементация `ScoringService` использует RAG-агент целиком и не парсит JSON — тесты подлежат обновлению.

---

## Лицензия / контрибуция

Внутренний проект. Перед коммитом проверяйте `uv run python -m compileall src` и `pytest`.
