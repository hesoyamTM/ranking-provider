# 04 · Диалоговый пайплайн (multi-agent FSM)

Главная фишка сервиса — диалог разрезан на 5 фаз. Каждая фаза — отдельный `sub-agent` со своим system-prompt и своей tool-schema. Маршрутизацию между фазами делает `AgentOrchestrator` (`src/service/agent/orchestrator.py`).

## Конечный автомат

```
                   ┌──────────────────┐
                   │   EXTRACTING     │ ◄────── первое сообщение / после RESTART
                   └────────┬─────────┘
                            │ ExtractionAgent.update(state, message, history)
                            │     → upsert_components (function-call)
                            ▼
                    total_missing == 0 ?
                    ┌───────┴───────┐
                  NO                YES
                    │                 │
                    ▼                 ▼
            ┌───────────────┐  ┌──────────────┐
            │  CLARIFYING   │  │  CONFIRMING  │
            └───────┬───────┘  └──────┬───────┘
                    │                 │  ConfirmationAgent.decide(state, user_msg)
       attempts<2  │ attempts==2     │
       вопросы    │ принудительно   │
                    │     CONFIRM    │
                    └──────┐  ┌──────┘
                           ▼  ▼
                ┌─────────────────────┐
                │      RANKING        │  ← переходная фаза (мгновенно идём дальше)
                └─────────┬───────────┘
                          ▼
                ┌─────────────────────┐
                │   SYNTHESIZING      │  ← SynthesisAgent.stream(state)
                │  tool loop:         │
                │  - rank_by_rag × N  │
                │  - get_providers ×1 │
                │  - финальный stream │
                └─────────┬───────────┘
                          ▼
                  ┌──────────────┐
                  │     DONE     │ ◄── любой следующий вопрос → EXTRACTING заново
                  └──────────────┘
```

`Phase` определён в `src/models/session.py`. Переход — через `state.transition(new_phase, reason)`, который пишет в `phase_log: list[tuple[from, to]]`.

---

## AgentOrchestrator

`src/service/agent/orchestrator.py`. Контракт:

```python
def send_message(chat_id, user_id, user_message) -> AsyncGenerator[str, None]
```

Алгоритм `_handle`:
1. **Append user message** в `ChatRepository`.
2. **Загрузить** `history` и `SessionState` (или новый).
3. **Классифицировать intent** через `IntentClassifier` (CONTINUE / RESTART). При RESTART — `clear_session_state` и пустой `SessionState`.
4. **Маршрутизация** `_route()` — асинхронный генератор. Чанки одновременно копятся в `chunks: list[str]` и стримятся клиенту.
5. **Сохранить** обновлённое `state` и полную assistant-реплику (склейка `chunks`).

Логика `_route`:

```python
if state.phase == CONFIRMING:
    decision = ConfirmationAgent.decide(state, user_message)
    if confirm → goto RANKING (→ SYNTHESIZING)
    if restart → clear state, fallthrough в EXTRACTING
    if patch   → state.transition(EXTRACTING), user_message = decision.patch_text

if state.phase in (EXTRACTING, CLARIFYING, DONE):
    new_state = ExtractionAgent.update(state, user_message, history)
    if total_missing > 0 and attempts < 2:
        attempts++; transition(CLARIFYING)
        yield from ClarificationAgent.stream(state)
        return
    transition(CONFIRMING)
    yield from ConfirmationAgent.render_spec(state)
    return

if state.phase == RANKING:
    yield from _run_ranking_and_synthesis(state)  # transition(SYNTHESIZING) + stream

if state.phase == SYNTHESIZING:
    yield from SynthesisAgent.stream(state)
    transition(DONE)
```

`_MAX_CLARIFICATION_ATTEMPTS = 2` — жёсткий гард: даже если LLM упорно не извлекает поля, после 2 попыток мы идём в CONFIRMING с тем, что есть.

---

## Sub-агенты

### 1. `IntentClassifier` (`intent_classifier.py`)
- **Цель.** Понять: пользователь продолжает текущий сценарий или хочет начать заново.
- **Контекст.** `history[-4:]` + последняя реплика.
- **Решение.** Через function-call: `intent_continue()` или `intent_restart()`. Других ответов нет.
- **Промпт.** `prompts/intent.md` — описывает триггеры RESTART («начнём заново», резкая смена темы) и дефолт CONTINUE.
- **Температура.** 0.0.

### 2. `ExtractionAgent` (`extraction_agent.py`)
- **Цель.** Извлечь / запатчить `list[ComponentSpec]` из новой реплики + текущего `SessionState`.
- **Tool.** `upsert_components` — единственный, всегда вызывается. Schema полностью описывает структуру `ComponentSpec` (включая `specs`, `assumptions`).
- **Императивный merge.** После tool-call:
  - Старые компоненты индексируются по `name.lower()`.
  - Для каждого нового: `merged_specs = old.specs | new.specs` (старые не теряются, новые перезаписывают).
  - Если LLM забыл вернуть `assumptions`/`location_name`/`max_budget_rub` — берётся из старого.
  - `missing` пересчитывается через `_compute_missing` (см. таксономию ниже).
- **Промпт.** `prompts/extraction.md` — описывает корректировку терминологии («ВПС» → `vds/vps/compute`), разбивку систем на компоненты, политику «подбери сам» и патч-семантику.
- **Температура.** 0.0.

#### Таксономия required-полей (`_taxonomy.py`)
Маппинг тегов → обязательные поля:
```python
"compute": ["vcpu", "ram", "region"]
"managed_db": ["engine", "size_gb", "region"]
"object_storage": ["expected_volume_gb", "region"]
"redis": ["size_gb", "region"]
"cdn": ["expected_traffic_tb"]
"kubernetes": ["nodes_count", "region"]
"gpu": ["gpu_type", "vram_gb", "region"]
"load_balancer": ["region"]
# … (см. полный список в _taxonomy.py)
```

Также там — `FIELD_HINTS` (человекочитаемая подсказка) и `FIELD_SUGGESTIONS` (быстрые варианты ответа для UI-чипов).

### 3. `ClarificationAgent` (`clarification_agent.py`)
- **Цель.** Задать 1–3 коротких вопроса по `missing`-полям.
- **Особенность.** Бюджет — приоритет №1: если `max_budget_total_rub is None` и ни у одного компонента нет своего бюджета, вопрос про бюджет вставляется ПЕРВЫМ.
- **Output.** Текстовый стрим (markdown-список с `- `). После основного ответа стримит спец-маркер:
  ```
  \n__SUGGESTIONS__:[{"field": "vcpu", "question": "сколько vCPU нужно", "suggestions": ["2 vCPU", "4 vCPU", …]}, …]\n
  ```
  Фронтенд рендерит это как кнопки-чипы под сообщением.
- **Промпт.** `prompts/clarification.md`.
- **Температура.** 0.3.

### 4. `ConfirmationAgent` (`confirmation_agent.py`)
Два режима в одном агенте:

**`render_spec(state)`** — стримит карточку спецификации (markdown с компонентами, локацией, бюджетом, assumptions) и заканчивает вопросом «всё верно?».

**`decide(state, user_message)`** — парсит ответ пользователя через function-calls:
- `confirm()` → `ConfirmAction.CONFIRM` → переход в RANKING.
- `patch_spec(updates: str)` → `ConfirmAction.PATCH` → `state.transition(EXTRACTING)`, `user_message = patch_text` → следующий проход пройдёт через ExtractionAgent с этой правкой.
- `restart()` → `ConfirmAction.RESTART` → `clear_session_state` + reset.

Если LLM не вызвал ни один tool — трактуется как `PATCH` с исходным текстом.

- **Промпт.** `prompts/confirmation.md` (определяет оба mode и точный markdown-формат карточки).
- **Температура.** 0.2 (render) / 0.0 (decide).

### 5. `SynthesisAgent` (`synthesis_agent.py`)
Финальная фаза. Не общается с пользователем напрямую — действует через **agent tool loop**.

**Tools** (объявлены в `AgentToolExecutor.TOOLS`):
- `get_providers()` → JSON-список разрешённых провайдеров (whitelist для финального ответа).
- `rank_by_rag(clean_intent, required_tags?, max_budget_rub?, location_name?)` → Markdown-блок с подходящими услугами от Yandex RAG-агента.

**Алгоритм:**
1. Сериализует все компоненты в JSON, кладёт как user-message.
2. Запускает `AgentToolExecutor.run_tool_loop_with_status`:
   - `max_rounds = max(6, len(components) + 2)`.
   - На каждом раунде: LLM вызывает тулы → исполняем → результаты как `role=TOOL` → следующая итерация.
   - Если на раунде нет tool_calls — выход.
   - Текстовые статусы (`"Анализирую задачу..."`, `"Ищу подходящие услуги для «<intent>»..."`) — стримятся отдельно префиксом `__STATUS__:` для UI.
3. После tool loop — финальный стрим: `LLM.stream(messages, temperature=0.4)` уже без tools. Чанки идут в `text/plain` ответ клиенту.

**Промпт.** `prompts/synthesis.md` — задаёт:
- Обязательные шаги (вызвать `rank_by_rag` для каждого компонента + `get_providers`).
- Строгий формат итогового Markdown (одиночный запрос vs многокомпонентная система).
- Запрет показывать `_internal_*` поля и score.

---

## Tool loop с трансляцией статусов

`src/service/agent/tools.py:run_tool_loop_with_status`:

```python
async def run_tool_loop_with_status(llm, messages, out_messages, temperature, max_rounds):
    yield "Анализирую задачу..."
    current = list(messages)
    for round_num in range(1, max_rounds + 1):
        response = await llm.complete(current, tools=TOOLS, temperature=temperature)
        if not response.tool_calls:
            break
        current.append(Message(role=ASSISTANT, content=..., tool_calls=[...]))
        for tc in response.tool_calls:
            yield _status_for(tc)              # "Ищу подходящие услуги для «база PostgreSQL»..."
            result = await self.execute(tc.name, tc.arguments)
            current.append(Message(role=TOOL, content=result, tool_call_id=tc.id))
    yield "Формирую ответ..."
    out_messages.extend(current)
```

`SynthesisAgent` оборачивает каждый статус: `yield f"__STATUS__:{status}\n"` — фронт детектит префикс и рисует «прогресс-бар» вместо обычного текста.

---

## Промпты

Файлы в `src/service/agent/prompts/`:

| Файл | Где грузится | Что описывает |
|---|---|---|
| `intent.md` | `IntentClassifier` | Триггеры CONTINUE / RESTART |
| `extraction.md` | `ExtractionAgent` | Корректировка терминологии, декомпозиция систем, политика specs/assumptions, патч-семантика |
| `clarification.md` | `ClarificationAgent` | Формат списка вопросов, приоритет бюджета, max 3 вопроса |
| `confirmation.md` | `ConfirmationAgent` | Карточка спецификации + три tool-варианта решения |
| `synthesis.md` | `SynthesisAgent` | Обязательные tool-вызовы, форматы single/multi-component вывода, запрет на `_internal_*` |

Загружаются через `prompts/__init__.py:load_prompt(name)` с `@lru_cache`. После правки промптов — перезапустить процесс.

---

## Идемпотентность и многократные ходы

- Каждый ход — независимая транзакция: загрузили историю + state, прошли через `_route`, сохранили обратно.
- Сообщения сохраняются ДО ответа ассистента (`append_message(user)` идёт до маршрутизации). Это означает, что даже при падении на `_route` пользовательская реплика останется в истории.
- `phase_log` накапливается, не сбрасывается — можно посмотреть всю историю переходов сессии.

---

## Расширение пайплайна

**Добавить новую фазу:**
1. Расширить `Phase`-enum в `models/session.py`.
2. В `AgentOrchestrator._route` добавить ветку.
3. Создать новый `<NewPhase>Agent` с собственным промптом и (опционально) tools.
4. Прокинуть его через DI в `application/app.py`.

**Добавить новый tool в синтез:**
1. В `AgentToolExecutor.TOOLS` описать JSON-schema.
2. В `AgentToolExecutor.execute` добавить ветку диспетчера.
3. В `AgentToolExecutor._STATUS` — человекочитаемое сообщение для UI.
4. В `prompts/synthesis.md` — объяснить LLM, когда и зачем его вызывать.
