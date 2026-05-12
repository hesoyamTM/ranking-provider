from __future__ import annotations

import json
import uuid

import psycopg_pool

from src.models.artifact import Artifact, ArtifactPayload


class PostgresArtifactRepository:
    """Хранение артефактов ранжирования в PostgreSQL."""

    def __init__(self, pool: psycopg_pool.AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, artifact: Artifact) -> None:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO artifacts (id, chat_id, user_id, payload, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        artifact.id,
                        artifact.chat_id,
                        artifact.user_id,
                        json.dumps(artifact.payload.model_dump(), ensure_ascii=False),
                        artifact.created_at,
                    ),
                )

    async def get_by_id(
        self, artifact_id: uuid.UUID, user_id: uuid.UUID
    ) -> Artifact | None:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, chat_id, user_id, payload, created_at
                    FROM artifacts
                    WHERE id = %s AND user_id = %s
                    """,
                    (artifact_id, user_id),
                )
                row = await cur.fetchone()
                return self._row_to_artifact(row) if row else None

    async def get_latest_for_chat(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> Artifact | None:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, chat_id, user_id, payload, created_at
                    FROM artifacts
                    WHERE chat_id = %s AND user_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (chat_id, user_id),
                )
                row = await cur.fetchone()
                return self._row_to_artifact(row) if row else None

    async def list_by_chat(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Artifact]:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, chat_id, user_id, payload, created_at
                    FROM artifacts
                    WHERE chat_id = %s AND user_id = %s
                    ORDER BY created_at DESC
                    """,
                    (chat_id, user_id),
                )
                rows = await cur.fetchall()
                return [self._row_to_artifact(r) for r in rows]

    @staticmethod
    def _row_to_artifact(row: tuple) -> Artifact:
        artifact_id, chat_id, user_id, payload, created_at = row
        if isinstance(payload, str):
            payload = json.loads(payload)
        return Artifact(
            id=artifact_id,
            chat_id=chat_id,
            user_id=user_id,
            payload=ArtifactPayload.model_validate(payload),
            created_at=created_at,
        )
