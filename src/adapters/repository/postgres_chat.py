from __future__ import annotations

import uuid
import psycopg_pool
from src.models.agent import Message, Role, MessageToolCall


class PostgresChatRepository:
    def __init__(self, pool: psycopg_pool.AsyncConnectionPool) -> None:
        self._pool = pool

    async def create_chat(self, user_id: uuid.UUID) -> uuid.UUID:
        chat_id = uuid.uuid4()
        chat_name = f"New Chat {chat_id}"
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO users (id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (user_id,),
                )
                await cur.execute(
                    "INSERT INTO chats (id, user_id, name) VALUES (%s, %s, %s)",
                    (chat_id, user_id, chat_name),
                )
        return chat_id

    async def get_chats_by_user(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT id FROM chats WHERE user_id = %s", (user_id,))
                rows = await cur.fetchall()
                return [row[0] for row in rows]

    async def get_chat_by_id(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Message]:
        query = """
            SELECT m.id, m.role, m.content, m.tool_call_id,
                   t.id as tc_id, t.name as tc_name, t.argument as tc_arg
            FROM messages m
            LEFT JOIN message_tool_calls t ON m.id = t.message_id
            JOIN chats c ON m.chat_id = c.id
            WHERE c.id = %s AND c.user_id = %s
            ORDER BY m.created_at ASC
        """
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (chat_id, user_id))
                rows = await cur.fetchall()

                messages_map = {}
                for row in rows:
                    m_id, role, content, tool_call_id, tc_id, tc_name, tc_arg = row
                    if m_id not in messages_map:
                        messages_map[m_id] = Message(
                            role=Role(role),
                            content=content,
                            tool_call_id=tool_call_id,
                            tool_calls=[],
                        )
                    if tc_id:
                        messages_map[m_id].tool_calls.append(
                            MessageToolCall(id=tc_id, name=tc_name, arguments=tc_arg)
                        )
                return list(messages_map.values())

    async def append_message(
        self, chat_id: uuid.UUID, user_id: uuid.UUID, message: Message
    ) -> None:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    msg_id = uuid.uuid4()
                    await cur.execute(
                        """
                        INSERT INTO messages (id, chat_id, role, content, tool_call_id)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            msg_id,
                            chat_id,
                            message.role.value,
                            message.content,
                            message.tool_call_id,
                        ),
                    )

                    if message.tool_calls:
                        for tc in message.tool_calls:
                            await cur.execute(
                                """
                                INSERT INTO message_tool_calls (id, message_id, name, argument)
                                VALUES (%s, %s, %s, %s)
                                """,
                                (tc.id, msg_id, tc.name, tc.arguments),
                            )

    async def delete_chat(self, chat_id: uuid.UUID, user_id: uuid.UUID) -> None:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM chats WHERE id = %s AND user_id = %s",
                    (chat_id, user_id),
                )
