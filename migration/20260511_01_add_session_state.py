"""
Add session_states table to persist agent session state per chat.
"""
from yoyo import step

__depends__ = {"20260510_01_chats_and_messages"}

steps = [
    step(
        """
        CREATE TABLE session_states (
            chat_id    UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            state      JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (chat_id, user_id)
        )
        """,
        "DROP TABLE session_states",
    )
]
