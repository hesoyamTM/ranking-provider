# 07 · REST API и фронтенд

FastAPI-приложение собирается в `Application.create_fastapi_app()`:

```python
app = FastAPI(title="Provider Ranking Agent")
app.include_router(get_router(self.chat_service, self.agent), prefix="/api/v1")
app.include_router(get_html_router())
```

- `/api/v1/chats/*` — REST для чатов (`router.py`).
- `/` — статический HTML-фронтенд (`index.html`).

---

## Идентификация пользователя

`user_id` извлекается из cookie `user_id` (HttpOnly, max-age=1 год). Если cookie нет:
- генерируется `uuid5(NAMESPACE, User-Agent)` — стабильный id для одного браузера;
- если `User-Agent` пустой — `uuid4`;
- cookie сразу устанавливается в ответе.

```python
_USER_ID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
_COOKIE_NAME = "user_id"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
```

Это решение для **демо/MVP**: нет логина, нет JWT, нет CSRF-защиты. В продакшен — заменить на полноценную аутентификацию.

---

## Эндпоинты

### `POST /api/v1/chats`
Создать новый чат для текущего пользователя.

**Response 201:**
```json
{ "chat_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479" }
```

### `GET /api/v1/chats`
Список идентификаторов чатов пользователя.

**Response 200:**
```json
{ "chats": ["f47ac10b-...", "..."] }
```

### `GET /api/v1/chats/{chat_id}`
История сообщений чата (в OpenAI-совместимом виде).

**Response 200:**
```json
{
  "chat_id": "...",
  "messages": [
    {"role": "user", "content": "нужен ВПС"},
    {"role": "assistant", "content": "Чтобы подобрать оптимально..."},
    {"role": "assistant", "content": "", "tool_calls": [
      {"id": "call_1", "type": "function",
       "function": {"name": "rank_by_rag", "arguments": "{\"clean_intent\": ...}"}}
    ]},
    {"role": "tool", "content": "...", "tool_call_id": "call_1"}
  ]
}
```

**Response 404** — если чат не принадлежит пользователю.

### `POST /api/v1/chats/{chat_id}/messages`
Отправить сообщение в чат. Тело — `{"text": "..."}`. Ответ — `StreamingResponse(media_type="text/plain")`.

В стриме могут встречаться спец-маркеры:

| Префикс / маркер | Значение |
|---|---|
| обычный текст | чанк ответа ассистента (markdown) |
| `__STATUS__:<msg>\n` | статус прогресса (`"Анализирую задачу..."`, `"Ищу подходящие услуги для «база PostgreSQL»..."`). Фронтенд показывает отдельно от текста. |
| `__SUGGESTIONS__:<json>\n` | кнопки-чипы для быстрого ответа: `[{"field": "vcpu", "question": "сколько vCPU нужно", "suggestions": ["2 vCPU", "4 vCPU", ...]}, ...]`. |

**Response 404** — чат не найден.

---

## HTML-фронтенд

Файл `src/controller/restapi/v1/ranking/index.html` (около 1000 строк) — single-file React-less UI на чистом JS:
- Сайдбар со списком чатов.
- Окно чата с markdown-рендером (используется `marked.js` через CDN).
- Поле ввода + кнопки-чипы (рендерятся из `__SUGGESTIONS__`).
- Прогресс-бар во время `__STATUS__`-стрима.
- Авто-прокрутка по мере поступления чанков.

Отдаётся по `GET /` как `text/html`:

```python
@router.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(content=_INDEX_HTML_PATH.read_text(encoding="utf-8"))
```

---

## Контракты controller-слоя

`src/controller/restapi/v1/ranking/protocols.py` определяет два протокола, которые роутер ожидает:

```python
class ChatService(Protocol):
    async def create_chat(user_id) -> uuid.UUID: ...
    async def get_chats(user_id) -> list[uuid.UUID]: ...
    async def get_history(chat_id, user_id) -> list[Message] | None: ...

class RankingAgent(Protocol):
    def send_message(chat_id, user_id, text) -> AsyncGenerator[str, None]: ...
```

Реализации — `ChatService` и `AgentOrchestrator` соответственно. Это позволяет тестировать роутер с фейками.

---

## DTO (`dto.py`)

```python
class ChatCreatedResponse(BaseModel): chat_id: uuid.UUID
class ChatListResponse(BaseModel):    chats: list[uuid.UUID]
class ChatHistoryResponse(BaseModel): chat_id: uuid.UUID; messages: list[dict[str, Any]]
```

---

## Безопасность (TODO)

- Нет CORS-настроек: по умолчанию FastAPI не разрешает кросс-доменные запросы; для интеграции с внешним фронтом понадобится `CORSMiddleware`.
- Cookie без `Secure=True` — заведомо для localhost. В продакшене — включить `secure=True` и HTTPS.
- Rate-limit отсутствует — стоит добавить (например, через `slowapi`) хотя бы на `POST /chats/{id}/messages`, так как каждый запрос порождает несколько LLM-вызовов.
- Прямой запрос к чужому `chat_id` отдаст 404 (проверка владения через `get_chats_by_user`), но user_id всё-таки выводимый (известен `User-Agent`), что не подходит для приватных данных.
