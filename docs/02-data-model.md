# 02 · Модели данных

Все доменные модели лежат в `src/models/`. Используется **Pydantic v2** (для сериализации в JSONB / DTO) и `dataclass` (для лёгких внутренних структур).

---

## Каталог провайдеров (`service_package.py`)

### `Provider`
```python
class Provider(BaseModel):
    provider_id: str         # стабильный PK: "t1-cloud", "yandex-cloud", "selectel", "cloud-ru", "vk-cloud"
    name: str                # человекочитаемое имя: "Yandex Cloud"
    base_platform: str       # тех. платформа: "OpenStack" / "Evolution" / "Selectel" / ...
    regions: list[str]       # ["Москва", "Санкт-Петербург"]
```

### `RegionCoord`
```python
class RegionCoord(BaseModel):
    region: str
    lat: float
    lon: float
```

### `Service`
```python
class Service(BaseModel):
    provider_id: str
    service_id: str                 # CamelCase slug, уникален в рамках provider_id
    category: str                   # Compute / Storage / Database / Networking / ML / CDN / Security / ...
    name: str
    description: str
    pricing_model: str              # per-month / per-hour / per-minute / per-gb / per-request / free / ...
    price_from_rub: Decimal         # NUMERIC(18, 8) в БД — нужна точность для per-second тарифов
    price_unit: str                 # свободная строка: "vCPU/час", "GB·месяц", "минута"
    compliance_tags: list[str]      # ["152-фз", "PCI-DSS", "ISO 27001"]
    tech_tags: list[str]            # ["postgresql", "kubernetes", "gpu"]
    regions: list[str]              # из документации провайдера
    region_coords: list[RegionCoord]# проставляется ProviderSyncWorker
```

### `ServicePackage`
Контейнер, который провайдер-адаптер возвращает из `fetch()`:
```python
class ServicePackage(BaseModel):
    provider: Provider
    services: list[Service]
```

### `UserQuery`
Параметризованный запрос к скореру (используется внутри `AgentToolExecutor._build_query`):
```python
class UserQuery(BaseModel):
    clean_intent: str               # «развёрнутое» описание потребности компонента
    required_tags: list[str]
    max_budget: float | None
    location_name: str
    target_lat: float = 0.0
    target_lon: float = 0.0
```

---

## Диалог и протокол LLM (`agent.py`)

### `Role`
`Enum`: `SYSTEM`, `USER`, `ASSISTANT`, `TOOL` — точно как в OpenAI Chat API.

### `Message`
Каноническое сообщение в истории:
```python
@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[MessageToolCall] = []
    tool_call_id: str | None = None     # только для role=TOOL
    id: uuid.UUID                       # внутренний PK (не отдаётся в LLM)
```
`Message.to_openai_dict()` приводит к dict, который ждёт `chat.completions.create`.

### `MessageToolCall`
Tool-call в ИСТОРИИ (arguments — уже JSON-строка):
```python
@dataclass
class MessageToolCall:
    id: str
    name: str
    arguments: str       # JSON string, как требует OpenAI
```

### `ToolCall`
Tool-call в ПАРСЕНОМ ответе LLM (arguments — уже распарсенный dict):
```python
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
```

### `LLMResponse`
```python
@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
```

### `ScoringWeights` / `RankedResource`
Используются legacy-пайплайном (`RankingOrchestrator`). В актуальном flow не задействованы, но оставлены для совместимости.

---

## Состояние сессии (`session.py`)

Главная модель, которая персистится в `session_states.state` (JSONB).

### `Phase`
```python
class Phase(str, Enum):
    EXTRACTING    = "extracting"     # ждём первое описание / новые поля
    CLARIFYING    = "clarifying"     # задаём вопросы по missing
    CONFIRMING    = "confirming"     # показали карточку, ждём «да/нет/правка»
    RANKING       = "ranking"        # переходное состояние перед синтезом
    SYNTHESIZING  = "synthesizing"   # tool loop + стриминг ответа
    DONE          = "done"           # сценарий завершён — следующая реплика снова в EXTRACTING
```

### `ComponentSpec`
Один компонент инфраструктуры:
```python
class ComponentSpec(BaseModel):
    name: str                       # "Бэкенд-сервер", "База данных"
    clean_intent: str               # 1–3 предложения «что разворачиваем»
    required_tags: list[str]        # ["compute", "docker"] / ["managed_db", "postgresql"]
    location_name: str = ""         # регион конкретно для этого компонента (иначе global)
    max_budget_rub: float | None    # бюджет для компонента (иначе global)
    specs: dict[str, str] = {}      # технические требования: vcpu, ram, engine, size_gb, …
    missing: list[str] = []         # required-поля без значения — заполняет ExtractionAgent
    assumptions: list[str] = []     # поля, которые LLM «дефолтнул» по «подбери сам»
```

### `SessionState`
```python
class SessionState(BaseModel):
    phase: Phase = Phase.EXTRACTING
    components: list[ComponentSpec] = []
    location_name: str = ""                 # общий регион для всей системы
    max_budget_total_rub: float | None      # общий бюджет
    rankings: dict[str, Any] | None         # legacy: результаты RankingOrchestrator
    phase_log: list[tuple[str, str]] = []   # (from_phase, to_phase) — для отладки
    clarification_attempts: int = 0         # лимит уточнений = 2

    def transition(self, new_phase: Phase, reason: str = "") -> None: ...

    @property
    def total_missing(self) -> int:
        return sum(len(c.missing) for c in self.components)
```

#### Инварианты
- `clarification_attempts` сбрасывается при выходе из CLARIFYING (после `total_missing == 0` или после исчерпания лимита).
- `missing` пересчитывается **на каждом upsert** в `ExtractionAgent._compute_missing` исходя из таксономии тегов (см. [04-agent-pipeline.md](04-agent-pipeline.md)).
- `phase_log` пишется только через `transition()` — это даёт читаемый лог переходов на каждый ход.

---

## Скоринг (`scoring_package.py`)

### `Score`
```python
@dataclass(frozen=True, slots=True)
class Score:
    semantic: float
    tags: float
    final_score: float
    proximity: float = 0.0
```

### `ScoredService`
```python
@dataclass(frozen=True, slots=True)
class ScoredService:
    service: Service
    score: Score
```

> **Замечание.** В текущем пайплайне RAG-агент возвращает уже отформатированный Markdown — структуры `Score`/`ScoredService` используются только в legacy-пути (`RankingOrchestrator`) и в `tests/test_scorer.py`.

---

## Отображение в Postgres

| Pydantic | Таблица | Колонки |
|---|---|---|
| `Provider` | `providers` | `provider_id PK`, `name`, `base_platform`, `regions TEXT[]`, `created_at`, `updated_at` |
| `Service` | `services` | `id BIGSERIAL PK`, `service_id`, `provider_id FK`, `category`, `name`, `description`, `pricing_model`, `price_from_rub NUMERIC(18,8)`, `price_unit`, `compliance_tags TEXT[]`, `tech_tags TEXT[]`, `regions TEXT[]`, `region_coords JSONB`, `embedding vector(384)`, `UNIQUE(provider_id, service_id)` |
| `Message` | `messages` + `message_tool_calls` | `id UUID PK`, `chat_id FK`, `role role_type`, `content`, `tool_call_id`, `created_at` |
| `SessionState` | `session_states` | `chat_id`, `user_id`, `state JSONB`, `updated_at`, `PK(chat_id, user_id)` |
| — | `users` | `id UUID PK` |
| — | `chats` | `id UUID PK`, `user_id FK`, `name` |

Подробнее — [06-persistence.md](06-persistence.md).

---

## Сериализация SessionState

`PostgresChatRepository.save_session_state`:
```python
json.dumps(state.model_dump(), ensure_ascii=False)
```
`PostgresChatRepository.get_session_state`:
```python
SessionState.model_validate(json.loads(row[0]) if isinstance(row[0], str) else row[0])
```
Pydantic сам корректно сериализует `Phase`-enum в строку и `Decimal`-поля компонентов (когда они возникают через `ExtractionAgent._apply`, бюджет передаётся как `float`).
