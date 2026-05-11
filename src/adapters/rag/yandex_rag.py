from __future__ import annotations

import logging

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class YandexRAGAdapter:
    """Адаптер для вызова агента Yandex AI Studio через OpenAI Responses API.

    Yandex AI Studio предоставляет OpenAI-совместимый эндпоинт
    ``https://ai.api.cloud.yandex.net/v1``. Агент идентифицируется
    параметром ``prompt.id`` (идентификатор промпта/агента в студии),
    идентификатор каталога передаётся в клиент как ``project``.
    """

    def __init__(self, client: AsyncOpenAI, agent_id: str) -> None:
        self._client = client
        self._agent_id = agent_id

    async def ask(self, query: str) -> str:
        """Отправляет запрос агенту и возвращает текст ответа."""
        logger.info("Sending query to Yandex RAG agent %s", self._agent_id)
        response = await self._client.responses.create(
            prompt={"id": self._agent_id},
            input=query,
        )
        logger.info(
            "Received response from Yandex RAG agent %s: %s",
            self._agent_id,
            response.output_text,
        )
        return response.output_text
