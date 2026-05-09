FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

FROM base AS deps

COPY pyproject.toml .
RUN pip install --upgrade pip && pip install -e ".[dev]" 2>/dev/null || pip install -e .

FROM deps AS final

COPY . .

ENTRYPOINT ["python", "main.py"]
CMD ["run"]
