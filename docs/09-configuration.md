# 09 · Конфигурация

Вся конфигурация — через переменные окружения, читаются в `Settings.from_env()` (`src/config/config.py`).

В корне проекта есть `.env.example` — скопируйте в `.env` и заполните секреты.

---

## Переменные окружения

### База данных
| Переменная | Дефолт | Описание |
|---|---|---|
| `POSTGRES_DSN` | `postgresql://postgres:postgres@localhost:5432/provider_ranking` | DSN для psycopg. Используется во всех репозиториях и в `yoyo migrate` (там DSN преобразуется в `postgresql+psycopg://...`). |
| `MIGRATIONS_DIR` | `migration` | Путь к папке миграций. Резолвится через `Path(...).resolve()`. |

### Yandex (LLM + RAG)
| Переменная | Дефолт | Описание |
|---|---|---|
| `YANDEX_API_KEY` | — | API-ключ Yandex Cloud. **Обязателен.** |
| `YANDEX_FOLDER_ID` | — | Идентификатор каталога Yandex Cloud. Используется как `project` в OpenAI-клиенте и встраивается в `YANDEX_MODEL` (`gpt://{folder}/{model}`). |
| `YANDEX_MODEL` | `yandexgpt-5/latest` | Имя модели. Финальный `model_id` собирается в `gpt://{folder_id}/{model}`, если `folder_id` указан. |
| `YANDEX_BASE_URL` | `https://llm.api.cloud.yandex.net/v1` | OpenAI-совместимый эндпоинт для chat completions. |
| `YANDEX_AI_STUDIO_BASE_URL` | `https://ai.api.cloud.yandex.net/v1` | Эндпоинт Yandex AI Studio (Responses API) для RAG-агента. |
| `YANDEX_RAG_AGENT_ID` | пусто | `prompt.id` агента, сконфигурированного в AI Studio. Без него `rank_by_rag` работать не будет. |

### Сетевые настройки
| Переменная | Дефолт | Описание |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind host для uvicorn. |
| `PORT` | `8000` | Bind port. |

### Воркер синхронизации
| Переменная | Дефолт | Описание |
|---|---|---|
| `SYNC_INTERVAL_SECONDS` | `3600` | Интервал между итерациями `run_forever`. |
| `T1_DATA_DIR` | `data/t1` | Папка с локальными PDF для `T1LocalCloudProvider`. |

### Feature flags (провайдеры)
| Переменная | Дефолт | Адаптер |
|---|---|---|
| `T1_LOCAL_PROVIDER` | `false` | `T1LocalCloudProvider` (читает PDF из `T1_DATA_DIR`) |
| `T1_WEB_PROVIDER` | `false` (в `.env.example` стоит `true`) | `T1WebCloudProvider` (скачивает PDF с `t1-cloud.ru`) |
| `CLOUDRU_WEB_PROVIDER` | `false` (`.env.example`: `true`) | `CloudRuWebProvider` |
| `YANDEX_CLOUD_WEB_PROVIDER` | `false` (`.env.example`: `true`) | `YandexCloudWebProvider` |
| `SELECTEL_WEB_PROVIDER` | `false` (`.env.example`: `true`) | `SelectelWebProvider` |
| `VK_CLOUD_WEB_PROVIDER` | `false` | `VkCloudWebProvider` |

> Если ни один флаг не включён — воркер запустится, но `providers=[]` и ничего не будет синхать. Это допустимый сценарий, если каталог уже в БД и нужны только REST + диалог.

---

## `Settings` dataclass

```python
@dataclass
class Settings:
    postgres_dsn: str
    yandex_api_key: str
    yandex_model: str
    yandex_base_url: str
    yandex_ai_studio_base_url: str
    data_dir: Path
    migrations_dir: Path
    sync_interval_seconds: float
    host: str
    port: int
    use_t1_local_provider: bool
    use_t1_web_provider: bool
    use_cloudru_provider: bool
    use_yandex_cloud_provider: bool
    use_selectel_provider: bool
    use_vk_cloud_provider: bool = True
    yandex_rag_agent_id: str = ""
    yandex_folder_id: str = ""
```

Загружается в `main.py` через `load_dotenv()` + `Settings.from_env()`. Все типы приводятся явно (`bool(...lower() == "true")`, `float(...)`, `int(...)`, `Path(...).resolve()`).

---

## Сборка `YANDEX_MODEL`

В `Settings.from_env()`:
```python
folder_id = os.environ.get("YANDEX_FOLDER_ID", "")
model_name = os.environ.get("YANDEX_MODEL", "yandexgpt-5/latest")
model = f"gpt://{folder_id}/{model_name}" if folder_id else model_name
```

Финальный id вида `gpt://b1g.../yandexgpt-5/latest` Yandex принимает как полную ссылку на модель в указанном каталоге.

---

## Локальный запуск без Yandex

Можно поднять сервис локально без RAG, если временно подменить `YandexRAGAdapter` на мок (например, возвращающий статичный markdown):

```python
class StubRAG:
    async def ask(self, query: str) -> str:
        return "## Сервисы\n- Пример\n- Пример"
rag = StubRAG()
```

И заменить `YandexGPTAdapter` на свой fake `LLMClient` — пайплайн целиком отрабатывает с любым OpenAI-совместимым клиентом.

---

## Известные проблемы конфигурации

- `YANDEX_FOLDER_ID` упоминается в `.env.example` дважды (строки 6 и 9) — вторая ссылка просто перетирает первую, ошибки не вызывает.
- В `.env.example` `T1_WEB_PROVIDER=true` и др. — соответствует «полному» режиму, но в `Settings.from_env()` дефолт `false`. Если запустить без `.env`, провайдеры будут выключены.
- `VK_CLOUD_WEB_PROVIDER` — в dataclass значение по умолчанию `True`, но в `from_env()` подгружается через `.lower() == "true"` с дефолтом `"false"`. Реальный дефолт — `False`, если переменная не задана.
