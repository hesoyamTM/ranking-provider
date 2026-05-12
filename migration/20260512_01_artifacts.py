"""
Add artifacts table to persist structured ranking results per chat.
"""
from yoyo import step

__depends__ = {"20260511_01_add_session_state"}

steps = [
    step(
        """
        CREATE TABLE artifacts (
            id         UUID PRIMARY KEY,
            chat_id    UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            payload    JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "DROP TABLE artifacts",
    ),
    step(
        "CREATE INDEX artifacts_chat_id_created_at_idx ON artifacts (chat_id, created_at DESC)",
        "DROP INDEX artifacts_chat_id_created_at_idx",
    ),
]
