# 06 · Persistence (Postgres + pgvector)

## База

В качестве БД используется PostgreSQL с расширением **pgvector**. В `docker-compose.yml` стоит образ `pgvector/pgvector:pg17`. Расширение включается миграцией:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

DSN читается из `POSTGRES_DSN`, по умолчанию `postgresql://postgres:postgres@localhost:5432/provider_ranking`.

---

## Миграции (yoyo-migrations)

Папка `migration/`, каждая миграция — Python-модуль с `__depends__: set[str]` и `steps: list[step(forward, backward)]`. Управление через `Application.migrate()`:

```python
dsn = self.settings.postgres_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
backend = get_backend(dsn)
migrations = read_migrations(str(self.settings.migrations_dir))
with backend.lock():
    backend.apply_migrations(backend.to_apply(migrations))
```

Запуск:
```bash
uv run python main.py migrate
```

### Список миграций

| Файл | depends_on | Что делает |
|---|---|---|
| `20260509_01_init.py` | — | Включает `pgvector`. Создаёт `providers`, `services` с `embedding vector(384)`, `ivfflat`-индекс по `embedding` и индекс по `category`. |
| `20260509_02_extend_price_precision.py` | `01_init` | `ALTER COLUMN price_from_rub TYPE NUMERIC(18, 8)` — нужно для per-minute/per-gb микро-цен. |
| `20260509_03_add_regions.py` | `02_extend_price_precision` | Добавляет `services.regions TEXT[]` и `services.region_coords JSONB`. |
| `20260510_01_chats_and_messages.py` | `03_add_regions` | Создаёт `role_type` enum, `users`, `chats`, `messages`, `message_tool_calls`. |
| `20260511_01_add_session_state.py` | `10_01_chats_and_messages` | Создаёт `session_states (chat_id, user_id, state JSONB)`. |

Каждая миграция содержит обратный шаг (DROP) для отката.

---

## Схема БД

### Каталог

```sql
CREATE TABLE providers (
    provider_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    base_platform TEXT NOT NULL,
    regions       TEXT[] NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE services (
    id              BIGSERIAL PRIMARY KEY,
    service_id      TEXT NOT NULL,
    provider_id     TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL,
    pricing_model   TEXT NOT NULL,
    price_from_rub  NUMERIC(18, 8) NOT NULL,
    price_unit      TEXT NOT NULL,
    compliance_tags TEXT[] NOT NULL DEFAULT '{}',
    tech_tags       TEXT[] NOT NULL DEFAULT '{}',
    regions         TEXT[] NOT NULL DEFAULT '{}',
    region_coords   JSONB  NOT NULL DEFAULT '[]',
    embedding       vector(384),                 -- зарезервировано под локальный поиск
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider_id, service_id)
);

CREATE INDEX services_embedding_idx
    ON services USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX services_category_idx ON services (category);
```

### Чаты и сообщения

```sql
CREATE TYPE role_type AS ENUM ('system', 'user', 'assistant', 'tool');

CREATE TABLE users (
    id UUID PRIMARY KEY
);

CREATE TABLE chats (
    id      UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name    TEXT NOT NULL
);

CREATE TABLE messages (
    id           UUID PRIMARY KEY,
    chat_id      UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role         role_type NOT NULL,
    content      TEXT NOT NULL,
    tool_call_id TEXT,                              -- только для role='tool'
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE message_tool_calls (
    id         TEXT PRIMARY KEY,                     -- внешний id от LLM
    message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    argument   TEXT NOT NULL                         -- JSON-строка, как требует OpenAI
);
```

### Состояние сессии

```sql
CREATE TABLE session_states (
    chat_id    UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    state      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chat_id, user_id)
);
```

---

## Репозитории

### `PostgresServiceRepository`
`src/adapters/repository/postgres.py`. Используется `ProviderSyncWorker` и `AgentToolExecutor` (через `ProviderRepository`-протокол).

- **`save_package(package)`** — UPSERT провайдера и UPSERT всех его услуг в одной транзакции. Стратегия — `ON CONFLICT (provider_id) DO UPDATE` и `ON CONFLICT (provider_id, service_id) DO UPDATE`. Поле `region_coords` сериализуется как JSON-строка.
- **`list_providers()`** — `SELECT … ORDER BY provider_id`. Используется в tool `get_providers` для whitelist'а.

Открывает соединение каждый раз через `psycopg.AsyncConnection.connect(dsn)`. Это сознательное упрощение — sync redken идёт раз в час, пул здесь избыточен.

### `PostgresChatRepository`
`src/adapters/repository/postgres_chat.py`. Использует `psycopg_pool.AsyncConnectionPool` (открывается в `Application.run_*`). Реализует `ChatRepository`-протокол:

| Метод | SQL |
|---|---|
| `create_chat(user_id)` | INSERT into users ON CONFLICT DO NOTHING + INSERT into chats |
| `get_chats_by_user(user_id)` | SELECT id FROM chats WHERE user_id = ... |
| `get_chat_by_id(chat_id, user_id)` | LEFT JOIN messages + message_tool_calls, восстанавливает `Message.tool_calls` |
| `append_message(chat_id, user_id, message)` | Транзакция: INSERT в messages + INSERT каждого tool_call |
| `delete_chat(chat_id, user_id)` | DELETE FROM chats — CASCADE удаляет всё связанное |
| `get_session_state` / `save_session_state` / `clear_session_state` | CRUD над `session_states.state` (JSONB) |

### `InMemoryChatRepository`
Альтернативная in-memory реализация — используется в тестах. Подменяется в `application/app.py` через DI.

---

## SessionState ↔ JSONB

Сериализация:
```python
await cur.execute(
    """INSERT INTO session_states (chat_id, user_id, state, updated_at)
       VALUES (%s, %s, %s, NOW())
       ON CONFLICT (chat_id, user_id) DO UPDATE
       SET state = EXCLUDED.state, updated_at = NOW()""",
    (chat_id, user_id, json.dumps(state.model_dump(), ensure_ascii=False)),
)
```

Десериализация:
```python
data = row[0]
if isinstance(data, str):
    data = json.loads(data)
return SessionState.model_validate(data)
```

Pydantic v2 корректно обрабатывает enum (`Phase`) и вложенные `ComponentSpec`. Тип в БД — `JSONB`, поэтому psycopg может вернуть либо строку, либо dict — код обрабатывает оба случая.

---

## Резервное копирование и аналитика

Структура `services` плоская и удобна для BI:
- Топ услуг по категориям: `SELECT category, COUNT(*) FROM services GROUP BY category;`
- Сравнение цен:
  ```sql
  SELECT provider_id, name, price_from_rub, price_unit
    FROM services
    WHERE category = 'Compute' AND 'managed_db' = ANY(tech_tags)
    ORDER BY price_from_rub;
  ```

История диалогов (`messages` + `message_tool_calls`) — отличный материал для retrospective: можно реконструировать каждую LLM-trajectory.

---

## Потенциальные улучшения

1. **Заполнение `services.embedding`** — превратить колонку из заглушки в рабочий локальный поиск (сейчас ivfflat-индекс впустую).
2. **`messages.tool_call_id` → FK** на `message_tool_calls.id`.
3. **Партиционирование `messages`** по `chat_id` или дате, если диалогов будет много.
4. **Soft-delete** для чатов (сейчас CASCADE-удаление без аудита).
