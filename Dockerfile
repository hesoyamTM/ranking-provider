# syntax=docker/dockerfile:1.7

FROM --platform=$TARGETPLATFORM python:3.13-slim AS base

ARG TARGETPLATFORM
ARG TARGETOS
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

FROM base AS deps

COPY pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-${TARGETPLATFORM} \
    uv sync --no-dev --no-install-project

FROM deps AS final

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-${TARGETPLATFORM} \
    uv sync --no-dev

ENTRYPOINT ["uv", "run", "python", "main.py"]
CMD ["run"]
