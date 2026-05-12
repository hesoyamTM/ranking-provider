# 05 · RAG-ранжирование и скоринг

## Архитектурное решение

Семантический подбор услуг **полностью делегирован Yandex AI Studio**: там сконфигурирован RAG-агент с собственным knowledge base. Локальное приложение знает только `agent_id` и шлёт текстовый запрос — получает готовый Markdown-ответ.

Преимущества:
- Не надо самим строить и поддерживать embeddings/индекс.
- Knowledge base обновляется отдельно (в Yandex AI Studio), без перерасчёта эмбеддингов в Postgres.
- LLM-агент сам решает, какие провайдеры/услуги релевантны.

Недостатки:
- Чёрный ящик: нет контроля над семантической функцией, фильтрами по бюджету.
- Латентность зависит от Yandex.

В коде уже зарезервирован запасной путь — `services.embedding vector(384)` + `ivfflat`-индекс в Postgres (см. миграцию `20260509_01_init.py`). Сейчас он не используется, но переключиться на локальный поиск можно без миграции данных.

---

## Слои

```
SynthesisAgent.stream(state)
        │
        ▼ tool loop
AgentToolExecutor.execute("rank_by_rag", {clean_intent, required_tags, max_budget_rub, location_name})
        │
        ▼ _rank() → _build_query()
ScoringService.rank_by_rag(user_query: UserQuery)
        │
        ▼
YandexRAGAdapter.ask(query.clean_intent)
        │
        ▼ POST /v1/responses {prompt: {id: agent_id}, input: query}
Yandex AI Studio
        │
        ▼
str (markdown с услугами по провайдерам)
```

---

## `ScoringService` (`src/service/scorer.py`)

Тонкая обёртка над RAG-адаптером:

```python
class ScoringService:
    def __init__(self, rag: RAGService) -> None:
        self._rag = rag

    async def rank_by_rag(self, user_query: UserQuery) -> str:
        raw = await self._rag.ask(user_query.clean_intent)
        return self._parse_response(raw)

    @staticmethod
    def _parse_response(raw: str) -> str:
        return raw  # сейчас identity, но точка расширения
```

Контракт `RelevanceScorer` (см. `service/agent/protocols.py`):
```python
async def rank_by_rag(self, user_query: UserQuery) -> str: ...
```

Возвращает строку — `SynthesisAgent` отдаёт её LLM как результат tool-вызова. Дальше LLM сам решает, как интерпретировать (форматировать в итоговый ответ).

---

## `YandexRAGAdapter` (`src/adapters/rag/yandex_rag.py`)

```python
class YandexRAGAdapter:
    def __init__(self, client: AsyncOpenAI, agent_id: str):
        self._client = client       # AsyncOpenAI(base_url=YANDEX_AI_STUDIO_BASE_URL)
        self._agent_id = agent_id   # YANDEX_RAG_AGENT_ID

    async def ask(self, query: str) -> str:
        response = await self._client.responses.create(
            prompt={"id": self._agent_id},
            input=query,
        )
        return response.output_text
```

Используется OpenAI **Responses API** (`/v1/responses`), которое Yandex AI Studio поддерживает. `prompt.id` указывает на сконфигурированного агента (с системным промптом и привязанным knowledge base). `project=YANDEX_FOLDER_ID` пробрасывается в клиента — Yandex использует это как идентификатор каталога.

---

## Подготовка запроса в `AgentToolExecutor`

`tools.py:_build_query`:
```python
async def _build_query(self, arguments: dict[str, Any]) -> UserQuery:
    location_name = arguments.get("location_name") or ""
    lat, lon = 0.0, 0.0
    if location_name:
        coords = await self._geocoder.geocode(location_name)
        if coords:
            lat, lon = coords
    return UserQuery(
        clean_intent=arguments["clean_intent"],
        required_tags=arguments.get("required_tags") or [],
        max_budget=arguments.get("max_budget_rub"),
        location_name=location_name,
        target_lat=lat,
        target_lon=lon,
    )
```

`target_lat/target_lon` сейчас не используются RAG-агентом, но проставляются для совместимости со старым `RankingOrchestrator`-пайплайном.

---

## Что попадает в финальный ответ

`SynthesisAgent` получает от LLM tool-loop серию markdown-блоков (по одному на компонент) + JSON со списком провайдеров. По промпту `synthesis.md` LLM обязан:

1. Упоминать **только** провайдеров из `get_providers` (whitelist).
2. Не показывать `_internal_*` поля (если вдруг RAG-агент их пробросит).
3. Соблюдать один из двух форматов:
   - **Single-component** — 1 компонент → ТОП-3 провайдеров, по каждому 1–3 услуги.
   - **Multi-component** — ≥2 компонентов → ТОП-3 провайдеров для всей системы, для каждого — разбивка по компонентам с примерной суммарной стоимостью.

---

## Legacy: `RankingOrchestrator` + `_filter.py` + `_formatter.py`

Эти файлы остались от предыдущей итерации, в которой агент сам считал score (semantic + tags + proximity), фильтровал по бюджету и формировал JSON для LLM. В текущем синтезе они **не задействованы** — LLM ходит напрямую в RAG.

Что они умеют (на случай возврата к локальному скорингу):

- **`_filter.py:validate_and_filter(ranked, arguments)`** — фильтрует `ScoredService` по бюджету (с пересчётом цены к ₽/мес через `monthly_price()`), отбраковывает NaN/inf-score и низкий `final_score < 0.35`, оставляет топ-15. Возвращает метрики качества: `top_final_score`, `tag_coverage`, `needs_refinement`, `refinement_hint`.
- **`_formatter.py:format_for_llm` / `format_plan_for_llm`** — собирают JSON-payload для финального LLM-вызова с разбиением по провайдерам (`group_by_provider`, `group_plan_by_provider`), prefix-инференцией провайдера из `service_id` (`PROVIDER_PREFIXES`).
- **`ranking_orchestrator.py:RankingOrchestrator.rank(state)`** — параллельный fan-out по компонентам через `asyncio.gather`, два режима (`rank_by_services` / `rank_by_providers` — тоже legacy).

Эти классы и тесты в `tests/test_scorer.py` (на старый `RAGScoringService`) — кандидаты на удаление или возрождение, если будет принято решение вернуться к локальному скорингу с pgvector.

---

## Расширение

**Переход на локальный pgvector-скоринг:**
1. Реализовать `LocalEmbeddingScorer` с тем же интерфейсом `RelevanceScorer.rank_by_rag(user_query) -> str` (можно сразу формировать Markdown).
2. Добавить адаптер эмбеддингов (например, `bge-m3` через REST).
3. В `ProviderSyncWorker` после сохранения услуг — обновлять `services.embedding`.
4. В скорере — `SELECT … ORDER BY embedding <-> $query_emb LIMIT 50`.
5. Подменить `ScoringService` в `application/app.py`.

Никаких изменений в `SynthesisAgent` / `AgentToolExecutor` / промптах не потребуется.
