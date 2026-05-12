# 01 · Архитектура

## Общая картина

Сервис построен по принципу **слоистой / гексагональной архитектуры**. Главная цель — отделить бизнес-логику (диалог, ранжирование, синхронизация) от внешних систем (LLM-провайдер, Postgres, REST-сборщики). Все границы выражены через `typing.Protocol` — конкретные реализации внедряются в `src/application/app.py` (composition root).

```
┌──────────────────────────────────────────────────────────────────┐
│                       controller / restapi                       │  ← FastAPI router, DTO, HTML-UI
└──────────────────────────────────────────────────────────────────┘
                                  │ depends on
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                              service                             │  ← Use-cases: ChatService,
│   ┌────────────────────────────────────────────────────────┐     │     AgentOrchestrator,
│   │  agent/   chat/   scorer.py   worker.py   protocols    │     │     ProviderSyncWorker,
│   └────────────────────────────────────────────────────────┘     │     ScoringService
└──────────────────────────────────────────────────────────────────┘
        │ uses Protocols (LLMClient, RAGService, Geocoder,
        │                  ChatRepository, ProviderRepository, CloudProvider)
        ▼
┌──────────────────────────────────────────────────────────────────┐
│                              adapters                            │  ← Реальные интеграции:
│  providers/  llm/  rag/  geocoding/  repository/                 │     YandexGPT, Yandex RAG,
└──────────────────────────────────────────────────────────────────┘     Postgres, Nominatim, …
                                  │ depends on
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                              models                              │  ← Domain entities (Pydantic):
│  Provider, Service, ServicePackage, Message, SessionState, …    │     Single source of truth
└──────────────────────────────────────────────────────────────────┘
```

**Правила зависимостей:**
- `models/` — ни от чего не зависит, кроме stdlib и pydantic.
- `service/` — зависит только от `models/` и от собственных протоколов.
- `adapters/` — реализуют протоколы из `service/`, могут тащить любые внешние SDK.
- `controller/` — тонкий слой над `service/`.
- `application/` — единственное место, где собирается граф зависимостей.

## Composition root

`src/application/app.py` создаёт класс `Application`. В конструкторе:

1. Поднимаются два OpenAI-совместимых клиента:
   - `openai_client` → `llm.api.cloud.yandex.net/v1` (chat completions).
   - `ai_studio_client` → `ai.api.cloud.yandex.net/v1` (Responses API / RAG-агент).
2. Создаются адаптеры провайдеров по feature-флагам (`use_t1_local_provider`, `use_cloudru_provider`, …).
3. Поднимается `psycopg_pool.AsyncConnectionPool` (lazy — `open=False`).
4. Инстанцируются:
   - `PostgresServiceRepository` — каталог провайдеров/услуг.
   - `PostgresChatRepository` — история чатов + `session_states`.
   - `NominatimGeocoder` — геокодинг с кешем и rate-limit.
   - `YandexGPTAdapter` — `LLMClient`.
   - `YandexRAGAdapter` — `RAGService`.
   - `ScoringService` — обёртка над RAG.
   - `AgentToolExecutor` — диспетчер тулов агента.
   - 5 sub-агентов (`IntentClassifier`, `ExtractionAgent`, `ClarificationAgent`, `ConfirmationAgent`, `SynthesisAgent`).
   - `AgentOrchestrator`, `ChatService`, `ProviderSyncWorker`.
5. Метод `run_forever()` запускает HTTP-сервер (uvicorn) и воркер синхронизации параллельно через `asyncio.gather`.

## Жизненный цикл запроса

### Диалоговый запрос
1. `POST /api/v1/chats/{chat_id}/messages` → `router.send_message`.
2. `_resolve_user_id` достаёт/создаёт `user_id` из cookie.
3. Проверка владения чатом (`chat_service.get_history`).
4. `agent.send_message(...)` возвращает `AsyncGenerator[str, None]`.
5. FastAPI оборачивает его в `StreamingResponse(media_type="text/plain")` и шлёт клиенту по мере поступления чанков.

### Что делает `AgentOrchestrator`
Подробно в [04-agent-pipeline.md](04-agent-pipeline.md). Кратко:
- Загружает историю и `SessionState` (или создаёт пустой).
- Классифицирует intent (CONTINUE / RESTART).
- Маршрутизирует на нужного sub-агента по `state.phase`.
- Каждый sub-агент стримит свои чанки, статусы и подсказки.
- В конце сохраняет обновлённый `SessionState` и полное сообщение ассистента.

### Фоновая синхронизация
- `ProviderSyncWorker.run_forever()` бесконечно: `run_once()` → `asyncio.sleep(interval)`.
- `run_once()` запускает `_sync_provider` параллельно по всем провайдерам.
- В каждом `_sync_provider`: `provider.fetch()` → геокодинг регионов → `repository.save_package()`.

## Контракты (Protocol-интерфейсы)

| Файл | Протокол | Реализации |
|---|---|---|
| `service/agent/protocols.py` | `LLMClient` | `YandexGPTAdapter` |
| `service/agent/protocols.py` | `ChatRepository` | `PostgresChatRepository`, `InMemoryChatRepository` |
| `service/agent/protocols.py` | `ProviderRepository` | `PostgresServiceRepository` |
| `service/agent/protocols.py` | `Geocoder` | `NominatimGeocoder` |
| `service/agent/protocols.py` | `RelevanceScorer` | `ScoringService` |
| `service/protocols.py` | `RAGService` | `YandexRAGAdapter` |
| `service/cloud_provider.py` | `CloudProvider` | `T1Local`, `T1Web`, `CloudRu`, `YandexCloud`, `Selectel`, `VkCloud` |
| `service/repository.py` | `ServiceRepository` | `PostgresServiceRepository` |
| `service/worker.py` | `Geocoder` (batch) | `NominatimGeocoder.geocode_batch` |
| `controller/.../protocols.py` | `ChatService`, `RankingAgent` | `ChatService`, `AgentOrchestrator` |

## Почему такие границы

- **Lock-in минимизирован.** Замена YandexGPT на любую OpenAI-совместимую LLM — это смена `base_url` и (если нужно) другой `LLMClient`. Замена RAG на локальный pgvector-поиск — переписать `ScoringService`, остальной код не трогать.
- **Тестируемость.** В `tests/` уже есть скелет тестов скорера; для агентов можно подменить `LLMClient` фейком, который возвращает заранее сконструированные `LLMResponse`.
- **Параллельная разработка.** Провайдеры независимы — добавление нового (например, `SberCloud`) — это новый файл в `adapters/providers/...` и опциональный флаг в `Settings`.

## Что в коде стоит знать

- **`AsyncGenerator` повсюду.** Каждый «этап» агента — генератор. `AgentOrchestrator` собирает чанки и параллельно стримит их клиенту и копит для записи в БД.
- **Pydantic v2** используется и для domain-моделей, и для DTO. `SessionState.model_dump_json()` напрямую кладётся в JSONB.
- **Промпты — `.md`-файлы** в `service/agent/prompts/`, загружаются с `@lru_cache`. Это позволяет править формулировки без рестарта при разработке (нужно сбросить кеш / перезапустить процесс) и удобно для review.
- **CLI** на Typer — один entrypoint, три команды. Каждая создаёт свой event loop.

## Где НЕ соблюдено разделение

- В `application/app.py` есть прямой `import` всех конкретных `*WebProvider` — это сознательно, т.к. это composition root.
- `service/agent/protocols.py` импортирует `Service` из `service/scorer.py` (`ScoredService`-обёртка) — это упрощение, не реверс зависимости (scorer всё ещё чисто-доменный, без I/O).
- `ranking_orchestrator.py` (`RankingOrchestrator`) и `_filter.py` / `_formatter.py` остались от прежней реализации скоринга «вручную». В текущем пайплайне они не используются — финальный синтез ходит сразу через `ScoringService.rank_by_rag`. Удалять не стали, чтобы не ломать историю миграций / возможные ветки.
