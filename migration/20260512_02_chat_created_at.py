"""
Add created_at column to chats table; backfill from earliest message.
"""
from yoyo import step

__depends__ = {"20260512_01_artifacts"}

steps = [
    step(
        "ALTER TABLE chats ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "ALTER TABLE chats DROP COLUMN created_at",
    ),
    step(
        """
        UPDATE chats
        SET created_at = COALESCE(
            (SELECT MIN(created_at) FROM messages WHERE messages.chat_id = chats.id),
            chats.created_at
        )
        """,
        "SELECT 1",
    ),
    step(
        "CREATE INDEX chats_user_id_created_at_idx ON chats (user_id, created_at DESC)",
        "DROP INDEX chats_user_id_created_at_idx",
    ),
]
