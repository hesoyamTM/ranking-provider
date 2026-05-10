"""
Create chats, messages and message_tool_calls tables.
"""
from yoyo import step

__depends__ = {"20260509_03_add_regions"}

steps = [
    step(
        "CREATE TYPE role_type AS ENUM ('system', 'user', 'assistant', 'tool')",
        "DROP TYPE role_type"
    ),

    step(
        "CREATE TABLE users (id UUID PRIMARY KEY)",
        "DROP TABLE users"
    ),

    step(
        """
        CREATE TABLE chats (
            id      UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name    TEXT NOT NULL
        )
        """,
        "DROP TABLE chats"
    ),

    step(
        """
        CREATE TABLE messages (
            id           UUID PRIMARY KEY,
            chat_id      UUID NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            role         role_type NOT NULL,
            content      TEXT NOT NULL,
            tool_call_id TEXT, 
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "DROP TABLE messages"
    ),

    step(
        """
        CREATE TABLE message_tool_calls (
            id         TEXT PRIMARY KEY,
            message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            name       TEXT NOT NULL,
            argument   TEXT NOT NULL
        )
        """,
        "DROP TABLE message_tool_calls"
    )
]