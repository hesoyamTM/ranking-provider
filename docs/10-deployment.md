# 10 · Deployment

## Локально через docker-compose

`docker-compose.yml` поднимает Postgres с pgvector и pgAdmin:

```yaml
services:
  db:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: provider_ranking
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck: ...

  pgadmin:
    image: dpage/pgadmin4:latest
    environment:
      PGADMIN_DEFAULT_EMAIL: admin@admin.com
      PGADMIN_DEFAULT_PASSWORD: admin
    ports: ["5050:80"]
```

Запуск:
```bash
docker compose up -d db
cp .env.example .env  # заполнить YANDEX_*
uv run python main.py migrate
uv run python main.py run
```

В compose-файле также есть закомментированные сервисы `migrate` и `agent` — это шаблон для полностью контейнерного запуска. Чтобы поднять весь стек в docker:
1. Раскомментировать секции `migrate` и `agent`.
2. Поправить env (особенно `POSTGRES_DSN: postgresql://postgres:postgres@db:5432/provider_ranking`).
3. `docker compose up -d --build`.

---

## Dockerfile

Multi-stage сборка на `python:3.13-slim` с `uv`:

```dockerfile
FROM python:3.13-slim AS base
RUN apt-get install -y libpq-dev
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

FROM base AS deps
COPY pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv,... uv sync --no-dev --no-install-project

FROM deps AS final
COPY . .
RUN --mount=type=cache,... uv sync --no-dev

ENTRYPOINT ["uv", "run", "python", "main.py"]
CMD ["run"]
```

Особенности:
- `--platform=$TARGETPLATFORM` + buildx — собирается под `linux/amd64` и `linux/arm64`.
- Зависимости и сорцы — в разных стадиях; кеш зависимостей не инвалидируется при изменении кода.
- `UV_COMPILE_BYTECODE=1` ускоряет холодный старт контейнера.
- ENTRYPOINT фиксирует CLI, CMD задаёт команду по умолчанию (`run`). Можно переопределить:
  ```bash
  docker run --rm ... hestm/provider-ranking:latest migrate
  docker run --rm ... hestm/provider-ranking:latest sync-once
  ```

---

## CI/CD (GitHub Actions)

`.github/workflows/deploy.yaml` — workflow с ручным запуском (`workflow_dispatch`):

1. **Build job:**
   - `docker/setup-qemu` + `setup-buildx` для multi-arch.
   - Login в Docker Hub (`DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` — секреты).
   - `docker/build-push-action@v6`:
     - tags: `hestm/provider-ranking:${{ github.sha }}` и `:latest`.
     - `cache-from/to: type=gha` — кеш слоёв в GitHub Actions cache.

2. **Deploy job:**
   - `appleboy/ssh-action` подключается к серверу.
   - Через `sed -i` подменяет тег образа в `compose.yaml` на новый SHA.
   - `docker compose pull` + `docker compose up -d --remove-orphans` + `docker image prune -f`.

**Секреты GitHub:**
- `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`
- `SSH_HOST`, `SSH_USER`, `SSH_KEY`, `SSH_PORT` (опционально, дефолт 22)

**Concurrency:** `cancel-in-progress: false` — параллельные ручные деплои встают в очередь.

---

## Production checklist

При выходе в прод стоит сделать:

1. **HTTPS.** Поставить nginx / Caddy / Traefik перед uvicorn, выпустить сертификат. В роутере (`router.py:_resolve_user_id`) включить `secure=True` для cookie.
2. **Реальная аутентификация.** Заменить `uuid5(User-Agent)` на JWT / OAuth (см. предупреждения в [07-rest-api.md](07-rest-api.md)).
3. **Rate-limiting** для `POST /chats/{id}/messages` — каждый запрос порождает несколько вызовов в Yandex.
4. **CORS** — настроить `CORSMiddleware`, если фронт будет на отдельном домене.
5. **Health-checks.** Добавить `/healthz` и `/readyz` (отдельный роут, без зависимостей).
6. **Метрики.** `prometheus-fastapi-instrumentator` + scrape ranking_*_total и latency для LLM-вызовов.
7. **Логи в JSON** — заменить `basicConfig` на структурный logger (например, `structlog`) и пайплайн в ELK/Loki.
8. **Бэкап Postgres** — pg_dump cron или managed PG.
9. **Secrets management** — не таскать `YANDEX_API_KEY` в `.env` на сервере; использовать vault / GitHub OIDC + Yandex IAM.
10. **graceful shutdown.** Сейчас `Application.run_forever` закрывает пул в `finally`, но `worker.run_forever` ловит только `CancelledError`. При SIGTERM от uvicorn-сигнала корутины должны корректно завершиться (uvicorn это делает). Тестировать `docker stop` сценарий перед прод-релизом.

---

## Локальная отладка

```bash
# Только БД
docker compose up -d db

# Применить миграции (читает MIGRATIONS_DIR + POSTGRES_DSN)
uv run python main.py migrate

# Разовая синхронизация — посмотреть, что приходит из провайдеров
T1_WEB_PROVIDER=true uv run python main.py sync-once

# Полный запуск
uv run python main.py run

# Открыть UI
open http://localhost:8000/

# Подключиться к pgAdmin
open http://localhost:5050/  # admin@admin.com / admin
```

Для отладки SQL — удобнее `psql -h localhost -U postgres -d provider_ranking` напрямую.
