# 08 · Sync worker, геокодинг и CLI

## `ProviderSyncWorker`

`src/service/worker.py`. Фоновый воркер, который периодически собирает каталог услуг у всех включённых провайдеров и сохраняет в Postgres.

```python
class ProviderSyncWorker:
    def __init__(self,
        providers: list[CloudProvider],
        repository: ServiceRepository,
        interval_seconds: float = 3600.0,
        geocoder: Geocoder | None = None,
    ): ...
```

### `run_once()`
```python
await asyncio.gather(*[self._sync_provider(p) for p in self._providers])
```
Все провайдеры синкаются параллельно. Падение одного логируется (`logger.exception`), но не валит другие.

### `_sync_provider(provider)`
1. `package = await provider.fetch()` — получить `ServicePackage` (см. [03-providers.md](03-providers.md)).
2. `await self._geocode_package(package)` — обогатить услуги координатами регионов.
3. `await self._repository.save_package(package)` — UPSERT в Postgres.

### `_geocode_package(package)`
Собирает все уникальные регионы со всех услуг пакета (`set comprehension`), один раз вызывает `geocoder.geocode_batch(regions)`, затем заполняет `service.region_coords` для каждой услуги — берёт координаты только тех регионов, что у неё перечислены.

### `run_forever()`
Бесконечный цикл:
```python
while True:
    try:
        await self.run_once()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Sync iteration failed")
    await asyncio.sleep(self._interval)
```

`interval_seconds` берётся из `SYNC_INTERVAL_SECONDS`, дефолт — 3600 (1 час).

В `Application.run_forever()` воркер запускается параллельно с uvicorn:
```python
await asyncio.gather(server.serve(), self.worker.run_forever())
```

---

## `NominatimGeocoder`

`src/adapters/geocoding/nominatim.py`. Преобразует названия регионов/городов в `(lat, lon)`.

### Стратегия (LRU-cache → static → Nominatim)
1. **In-memory cache** — `self._cache: dict[str, tuple[float, float] | None]`. Ключ — `region.strip().lower()`.
2. **Static table** — ~30 российских городов забиты в `_STATIC` (Москва, Питер, Новосибирск, Екатеринбург, Краснодар, …). Substring-match: «москва» в «г. Москва» — найдёт.
3. **Nominatim fallback** — `https://nominatim.openstreetmap.org/search`. Соблюдается ToS: `asyncio.Semaphore(1)` + `await asyncio.sleep(1.0)` после каждого запроса (≤1 req/sec). User-Agent: `provider-ranking-agent/1.0 (contact: ops@t1cloud.ru)`.

Если ничего не нашлось — кешируется `None`, чтобы не дёргать Nominatim снова.

### `geocode_batch(regions: list[str]) -> dict[str, RegionCoord | None]`
Используется воркером. Дедуплицирует входной список (`dict.fromkeys`), идёт последовательно (последовательность важна — Nominatim не любит параллель), возвращает `RegionCoord(region, lat, lon)` или `None`.

---

## CLI (`main.py`)

Точка входа — `typer`-приложение с тремя командами:

```bash
uv run python main.py migrate
uv run python main.py sync-once
uv run python main.py run
```

| Команда | Что делает |
|---|---|
| `migrate` | Применить миграции через `Application.migrate()` (yoyo-migrations). |
| `sync-once` | `Application.run_once()` — открыть пул, один проход воркера, закрыть пул. Удобно для CI / cron. |
| `run` | `Application.run_forever()` — открыть пул, запустить HTTP-сервер и воркер параллельно. |

`load_dotenv()` вызывается на старте, поэтому `.env` рядом с `main.py` подхватывается автоматически.

Логирование: `logging.basicConfig(level=INFO)`, `httpx` и `httpcore` понижены до WARNING (иначе спам от каждого запроса).

---

## Жизненный цикл пула соединений

В `Application.__init__` пул создаётся с `open=False` — это lazy-инициализация. Реальный `open()` делается в `run_once()` / `run_forever()`:

```python
async def run_forever(self) -> None:
    await self.pool.open()
    fastapi_app = self.create_fastapi_app()
    ...
    try:
        await asyncio.gather(server.serve(), self.worker.run_forever())
    finally:
        await self.pool.close()
```

`PostgresServiceRepository` НЕ использует этот пул — он работает через одноразовые `psycopg.AsyncConnection.connect(dsn)`. Это упрощение: sync редкий, и переоткрытие соединения не критично.

Пул используется только `PostgresChatRepository` — там нагрузка более высокая (каждый ход агента = несколько SELECT/INSERT).

---

## Логи

Все ключевые шаги логируются:
- `Worker started` / `Syncing %d provider(s)` / `Got %d services from %s` / `Saved package for provider %s` — статус синка.
- `Geocoded %r -> %.6f, %.6f` / `Nominatim geocoding failed for %r` — геокодинг.
- В `AgentOrchestrator`: `Incoming user message`, `Loaded state`, `Phase RESET`, `Clarifying attempt N/2`, `Done: phase_after=...`.
- В sub-агентах: `Extraction: components=N missing=M`, `Confirmation: CONFIRM/PATCH/RESTART`, `SynthesisAgent: tool loop завершён`, `AgentToolExecutor: раунд %d, вызванные тулы: [...]`.

Уровень `INFO` по умолчанию даёт читаемый, но не перегруженный лог.

---

## Расширение

**Запуск только воркера без HTTP** — сделать `Application.run_worker_only()`:
```python
async def run_worker_only(self):
    await self.pool.open()
    try:
        await self.worker.run_forever()
    finally:
        await self.pool.close()
```
И добавить команду в Typer.

**Cron-based вместо встроенного цикла** — заменить `run_forever` на `run-once` и поставить cron / systemd timer.

**Параллельный геокодинг** — `geocode_batch` сейчас последовательный из-за ToS Nominatim. При переходе на собственный geocode (например, Yandex Maps API) можно распараллелить.
