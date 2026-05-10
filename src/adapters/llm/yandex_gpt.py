from __future__ import annotations

import json
from typing import Any, AsyncGenerator, ClassVar

from openai import AsyncOpenAI

from src.models.agent import LLMResponse, Message, ToolCall
from src.service.agent.protocols import (
    TOOL_ASK_CLARIFICATION as _TOOL_ASK_CLARIFICATION,
    TOOL_PLAN_SYSTEM as _TOOL_PLAN_SYSTEM,
    TOOL_RANK_SERVICES as _TOOL_RANK_SERVICES,
)


class YandexGPTAdapter:
    """Адаптер LLM (YandexGPT через OpenAI-совместимый API).

    Хранит описание системного промпта и схемы всех доступных агенту
    инструментов (function calling).
    """

    SYSTEM_PROMPT: ClassVar[str] = (
        "Ты — экспертный аналитик облачной инфраструктуры. "
        "Помогаешь подобрать облачные услуги и объясняешь выбор простым языком.\n\n"
        "═══ ОБОГАЩЕНИЕ КОНТЕКСТА ═══\n"
        "Пользователь часто формулирует запрос коротко и неточно. Твоя обязанность:\n"
        "  • РАСШИРЯТЬ запрос до развёрнутого сценария (нагрузка, стек, требования).\n"
        "  • КОРРЕКТИРОВАТЬ терминологию: 'ВПС'→VDS/VPS, 'базу'→managed database, "
        "'нейронку'→GPU compute, 'хранилище'→object storage и т.п.\n"
        "  • ВЫВОДИТЬ теги самостоятельно: Docker→['docker'], "
        "ML→['gpu','cuda','ml'], 152-ФЗ→['fz-152'], PostgreSQL→['postgresql'], "
        "k8s→['kubernetes']. Добавляй синонимы и сопутствующие технологии.\n"
        "  • clean_intent — развёрнутая фраза (1–3 предложения) про сценарий, "
        "нагрузку и ключевые требования.\n\n"
        "═══ ВЫБОР ИНСТРУМЕНТА ═══\n"
        "У тебя три инструмента:\n\n"
        "1. `ask_clarification` — если запрос совсем непонятен и вывести "
        "компоненты невозможно. Используй редко.\n\n"
        "2. `rank_services` — для КОНКРЕТНОГО одиночного запроса: "
        "нужен один тип услуги ('хочу VDS', 'нужна managed БД', "
        "'object storage для бэкапов'). "
        "Параметры: clean_intent, required_tags, location_name, max_budget.\n\n"
        "3. `plan_system` — для АБСТРАКТНОГО или МНОГОКОМПОНЕНТНОГО запроса: "
        "пользователь описывает систему, приложение, платформу или сайт "
        "('хочу сделать сайт', 'нужна платформа для бронирования', "
        "'строю маркетплейс', 'приложение с бэкендом и базой'). "
        "Разбей на независимые компоненты (обычно 2–5) и вызови этот инструмент. "
        "Не спрашивай разрешения — сразу предложи декомпозицию и запусти.\n\n"
        "ПРАВИЛО: если сомневаешься между rank_services и plan_system — "
        "используй plan_system. Лучше декомпозировать лишний раз.\n\n"
        "═══ ПАРАМЕТРЫ rank_services ═══\n"
        "  • clean_intent — развёрнутое описание (1–3 предложения).\n"
        "  • required_tags — список тегов по смыслу запроса.\n"
        "  • location_name — город/регион (пусто если неважно).\n"
        "  • max_budget — бюджет ₽/мес. Доллары → 100 ₽/$. "
        "Если не задан — не передавай.\n\n"
        "═══ ПАРАМЕТРЫ plan_system ═══\n"
        "  • components — список компонентов системы, каждый:\n"
        "      - name: короткое название ('Бэкенд-сервер', 'База данных', …)\n"
        "      - clean_intent: развёрнутое описание этого компонента\n"
        "      - required_tags: теги по смыслу этого компонента\n"
        "  • location_name — общий регион для всех компонентов.\n"
        "  • max_budget_total_rub — общий бюджет на всю систему (опционально).\n\n"
        "Примеры декомпозиции:\n"
        "  • 'сайт бронирования отелей' → "
        "Бэкенд-сервер, База данных, Хранилище медиафайлов, "
        "Балансировщик/CDN, (опционально) Кэш (Redis)\n"
        "  • 'маркетплейс' → "
        "API-сервер, База данных, Поиск, Хранилище, Очередь сообщений\n"
        "  • 'ML-платформа' → "
        "GPU-вычисления, Хранилище датасетов, API-сервер, База данных\n\n"
        "═══ ВАЛИДАЦИЯ ВЫДАЧИ ═══\n"
        "После rank_services: если `_internal_quality.needs_refinement=true` "
        "и есть попытка — перевызови с улучшенными параметрами.\n"
        "После plan_system: если какой-то компонент имеет `needs_refinement=true` "
        "— в итоговом ответе честно укажи, что по этому компоненту "
        "данных мало, и порекомендуй уточнить.\n\n"
        "═══ ФОРМАТ ОТВЕТА — ОДИНОЧНЫЙ ЗАПРОС (rank_services) ═══\n"
        "ВАЖНО: НИКОГДА не показывай score, теги, _internal_* поля и ключи JSON.\n\n"
        "Структура Markdown:\n\n"
        "**Что я понял:** 1–2 предложения о запросе и что скорректировал.\n\n"
        "---\n\n"
        "## 🏆 ТОП провайдеров\n\n"
        "### 🥇 Место 1 — <Провайдер>\n"
        "**Почему этот провайдер?** 2–3 предложения (покрытие, специализация, "
        "преимущества для данного кейса — без score и тегов).\n\n"
        "**Лучшие тарифы:**\n\n"
        "#### <Название услуги> — <Категория>\n"
        "- **Конфигурация:** <из description>\n"
        "- **Стоимость:** ~<monthly_price_rub> ₽/мес "
        "*(от <price_from_rub> ₽ за <price_unit>)*\n"
        "- **Регионы:** <regions>\n"
        "- **Подходит, потому что:** 1–2 предложения конкретики.\n\n"
        "(и так для каждого из топ-3 провайдеров)\n\n"
        "---\n\n"
        "**Итоговая рекомендация:** 2–3 предложения о лучшем выборе.\n\n"
        "═══ ФОРМАТ ОТВЕТА — СИСТЕМНЫЙ ЗАПРОС (plan_system) ═══\n"
        "ВАЖНО: НИКОГДА не показывай score, теги, _internal_* поля и ключи JSON.\n\n"
        "Структура Markdown:\n\n"
        "**Что я понял:** 1–2 предложения о запросе.\n\n"
        "**Я разбил задачу на компоненты:**\n"
        "- Компонент 1: <описание>\n"
        "- Компонент 2: <описание>\n"
        "- …\n\n"
        "---\n\n"
        "## 🏆 ТОП провайдеров для всей системы\n\n"
        "### 🥇 Место 1 — <Провайдер>\n"
        "**Почему этот провайдер?** 2–4 предложения: насколько широко покрывает "
        "компоненты системы, в чём сильные стороны для данного кейса.\n\n"
        "**Компоненты и тарифы:**\n\n"
        "**🔧 <Название компонента>**\n"
        "  - **<Услуга>** — <категория>\n"
        "    - Конфигурация: <из description>\n"
        "    - Стоимость: ~<monthly_price_rub> ₽/мес "
        "*(от <price_from_rub> ₽ за <price_unit>)*\n"
        "    - Регионы: <regions>\n"
        "    - Подходит: 1 предложение.\n\n"
        "(и так для каждого компонента этого провайдера)\n\n"
        "**Примерная стоимость системы у этого провайдера:** "
        "~<сумма monthly_price_rub по компонентам> ₽/мес\n\n"
        "(и так для каждого из топ-3 провайдеров)\n\n"
        "---\n\n"
        "**Итоговая рекомендация:** 3–5 предложений. Какой провайдер лучше "
        "для всей системы и почему. Если один провайдер не покрывает все "
        "компоненты — предложи комбинацию. Без score, тегов, технических полей.\n\n"
        "Все цифры только из monthly_price_rub, price_from_rub, price_unit, regions."
    )

    TOOL_ASK_CLARIFICATION: ClassVar[str] = _TOOL_ASK_CLARIFICATION
    TOOL_RANK_SERVICES: ClassVar[str] = _TOOL_RANK_SERVICES
    TOOL_PLAN_SYSTEM: ClassVar[str] = _TOOL_PLAN_SYSTEM

    TOOLS: ClassVar[list[dict[str, Any]]] = [
        {
            "type": "function",
            "function": {
                "name": TOOL_ASK_CLARIFICATION,
                "description": (
                    "Задать пользователю уточняющий вопрос, если запрос "
                    "совсем непонятен и декомпозиция невозможна."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Текст вежливого уточняющего вопроса.",
                        }
                    },
                    "required": ["question"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_RANK_SERVICES,
                "description": (
                    "Ранжирует услуги по одному конкретному запросу. "
                    "Используй для точечных запросов ('нужен VDS', 'managed PostgreSQL'). "
                    "Для систем и приложений из нескольких частей — используй plan_system."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "clean_intent": {
                            "type": "string",
                            "description": (
                                "Развёрнутое (1–3 предложения) описание сценария: "
                                "что разворачивает пользователь, нагрузка, требования."
                            ),
                        },
                        "required_tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Теги по смыслу запроса (docker, postgresql, "
                                "k8s, gpu, fz-152, ssl, ml и т.п.). "
                                "Добавляй синонимы и сопутствующие теги."
                            ),
                        },
                        "location_name": {
                            "type": "string",
                            "description": "Город или регион (пусто, если неважно).",
                        },
                        "max_budget": {
                            "type": "number",
                            "description": "Бюджет ₽/мес. Не передавай если не задан.",
                        },
                    },
                    "required": ["clean_intent", "required_tags"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_PLAN_SYSTEM,
                "description": (
                    "Декомпозирует абстрактный/многокомпонентный запрос на "
                    "независимые части и запускает ранжирование для каждой. "
                    "Используй для систем, приложений, платформ, сайтов — "
                    "когда пользователю нужно несколько типов услуг сразу."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "components": {
                            "type": "array",
                            "description": "Список компонентов системы (2–6 штук).",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "Короткое название компонента.",
                                    },
                                    "clean_intent": {
                                        "type": "string",
                                        "description": (
                                            "Развёрнутое описание этого конкретного "
                                            "компонента и его требований."
                                        ),
                                    },
                                    "required_tags": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "Теги для этого компонента.",
                                    },
                                },
                                "required": ["name", "clean_intent", "required_tags"],
                            },
                            "minItems": 2,
                        },
                        "location_name": {
                            "type": "string",
                            "description": "Общий регион для всех компонентов.",
                        },
                        "max_budget_total_rub": {
                            "type": "number",
                            "description": (
                                "Общий бюджет ₽/мес на всю систему. "
                                "Не передавай если не задан."
                            ),
                        },
                    },
                    "required": ["components"],
                },
            },
        },
    ]

    def __init__(
        self, client: AsyncOpenAI, model: str, temperature: float = 0.1
    ) -> None:
        self._client = client
        self._model = model
        self._temperature = temperature

    @property
    def system_prompt(self) -> str:
        return self.SYSTEM_PROMPT

    async def chat(self, messages: list[Message]) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[m.to_openai_dict() for m in messages],
            tools=self.TOOLS,
            temperature=self._temperature,
        )
        message = response.choices[0].message

        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            try:
                arguments = (
                    json.loads(tc.function.arguments) if tc.function.arguments else {}
                )
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolCall(id=tc.id, name=tc.function.name, arguments=arguments)
            )

        return LLMResponse(content=message.content, tool_calls=tool_calls)

    async def stream_chat(self, messages: list[Message]) -> AsyncGenerator[str, None]:
        """Финальный стриминговый ответ без инструментов."""
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=[m.to_openai_dict() for m in messages],
            temperature=self._temperature,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
