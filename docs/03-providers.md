# 03 · Адаптеры провайдеров

Все адаптеры лежат в `src/adapters/providers/<name>/provider.py` и реализуют единственный контракт:

```python
class CloudProvider(Protocol):
    async def fetch(self) -> ServicePackage: ...
```

`ServicePackage` = `Provider` + `list[Service]`. Адаптер не знает ничего о БД и геокодинге — это работа `ProviderSyncWorker` (см. [08-sync-worker.md](08-sync-worker.md)).

Все адаптеры активируются независимыми feature-флагами в `.env`:

| Флаг | Значение по умолчанию | Адаптер |
|---|---|---|
| `T1_LOCAL_PROVIDER` | `false` | `T1LocalCloudProvider` |
| `T1_WEB_PROVIDER` | `true` | `T1WebCloudProvider` |
| `CLOUDRU_WEB_PROVIDER` | `true` | `CloudRuWebProvider` |
| `YANDEX_CLOUD_WEB_PROVIDER` | `true` | `YandexCloudWebProvider` |
| `SELECTEL_WEB_PROVIDER` | `true` | `SelectelWebProvider` |
| `VK_CLOUD_WEB_PROVIDER` | `false` | `VkCloudWebProvider` |

---

## T1 Cloud — два режима

### `T1LocalCloudProvider`
Источник: локальная папка `data/t1/*.pdf` (см. `T1_DATA_DIR`). В репозитории лежат публичные приложения к договору T1 Cloud:
- `Prilozhenie_1_Tarifnoe_prilozhenie_*.pdf` — тарифное приложение (одна строка таблицы = одна услуга).
- `Prilozhenie_6.X_*.pdf` — описания конкретных услуг (VDC, S3, CDN, K8s, MS PostgreSQL, …).

Алгоритм:
1. Сканирует папку, классифицирует PDF: «тариф» (`prilozhenie_1`, `tarifn`) vs «описание услуги».
2. Для тарифного: текст PDF режется по regex `r'\d{1,2}\.\s+(Группа услуг|Услуга …)'` (см. `_split_tariff_groups`) → каждая группа в отдельный LLM-запрос с промптом `TARIFF_PROMPT`. LLM возвращает массив объектов `Service` (одна строка таблицы → одна услуга).
3. Для описаний: целиком текст PDF + `SERVICE_PROMPT`. LLM решает, одну услугу извлечь или несколько вариантов.
4. Все вызовы LLM идут через `asyncio.Semaphore(max_concurrent)` (по умолчанию 4 одновременно).

Парсинг ответа LLM — `_parse_llm_json`: снимает markdown-фенсы, валидирует через `Service.model_validate`. Дубли по `service_id` отбрасываются.

### `T1WebCloudProvider`
Те же промпты (`SERVICE_PROMPT`, `TARIFF_PROMPT`), но PDF тянутся с `t1-cloud.ru/documents/services` и `/documents/rates`:
1. HTTP GET индексных страниц, regex `_PDF_HREF_RE` выдёргивает ссылки на `.pdf`.
2. Чёрный список (`_SKIP_STEMS`) — пользовательское соглашение / политики.
3. Каждый PDF: `httpx` скачивает, `pypdf` извлекает текст, дальше — как в local.

User-Agent: `Mozilla/5.0 (provider-ranking-agent/1.0)`. Таймаут 30s, follow_redirects=True.

---

## Cloud.ru — HTML+PDF гибрид

`CloudRuWebProvider` обходит `cloud.ru/products` для HTML-описаний услуг и `cloud.ru/documents/tariffs/<platform>` (`evolution`/`vmware`/`advanced`/`ml-space`/`crs`) для тарифных PDF.

Логика:
- Из HTML каталога regex `_PRODUCT_HREF_RE` собирает `/products/<slug>`.
- Из индексов тарифов regex `_TARIFF_PAGE_HREF_RE` собирает per-service страницы; на каждой regex `_PDF_HREF_RE` ловит CDN-ссылку `https://cdn.cloud.ru/docs/.../*.pdf` (HTML рендерит только первые 10 строк, поэтому полная таблица — только в PDF).
- HTML описаний услуг конвертируется в plain-text (`_html_to_text`: убираются `<script>/<style>`, теги, схлопываются пробелы), обрезается до 16k символов.
- PDF тарифов — до 56k.
- Дальше — LLM-извлечение по тем же промптам, что у T1 (`_parse_llm_json`).

---

## Yandex Cloud — публичный JSON API

`YandexCloudWebProvider` ходит на `https://yandex.cloud/api/priceList/getPriceList` (один POST, JSON-ответ с полным прайсом). LLM не нужен — данные структурированы:
- Из ответа достаются категории и услуги.
- `pricing_model` определяется по строке `pricingUnit` через таблицу `_PRICING_MODEL_RULES` (substring-matching: `hour` → `per-hour`, `gb` → `per-gb`, …).
- `price_from_rub` — минимум положительной цены среди всех `rates[i].unitPrice` для услуги (см. `_rates_min_price`).
- `service_id` строится из имени через `_to_camel` (CamelCase, латиница).

---

## Selectel — REST с несколькими эндпоинтами

`SelectelWebProvider` агрегирует несколько ресурсов `api.selectel.ru`:

- **Flat-prices** (`/prices/ones`, `/prices/vmware`, `/prices/craas`, `/prices/mks`, `/prices/dbaas`, `/prices/storage`, `/prices/vpc`, `/prices/netdisk`) — items типа `{resource, group, currency, value, unit}`. Категория и префикс service_id жёстко прописаны в `_FLAT_PRICE_ENDPOINTS`.
- **Services** (`/servers/v2/pub/service?model=...`) — items с `price_collection.RUB.{hour,month,...}`, теги через `tag_list`.
- **Colocation** — отдельный per-uuid детальный запрос (`/servers/v2/pub/service/colocation/{uuid}`), нужен `addition[]` массив.
- **Billing prices** (`/v2/billing/prices`) — фильтр по `status_id ∈ {2, 3, 4}` (active/orderable).

Concurrency для colocation-details: `asyncio.Semaphore(8)`. LLM не используется — нормализация полностью детерминированная.

---

## VK Cloud — HTML парсинг

`VkCloudWebProvider` парсит `https://cloud.vk.com/pricelist/` через BeautifulSoup:
- Каждая `<table>` на странице → группа услуг (название из ближайшего предыдущего `<h2>/<h3>`).
- Категория угадывается по эвристике `_CATEGORY_RULES` (substring match на тексте секции).
- Цена очищается `_clean_price` (`Decimal`, обрезание разделителей).
- `verify=False` для SSL — у сайта на момент написания были проблемы с сертификатом.

---

## Пайплайн извлечения через LLM

T1 Local / T1 Web / Cloud.ru используют общий хелпер:

```python
def _parse_llm_json(content: str, source: str) -> list[dict]:
    # 1. Снимает ```json … ``` фенсы
    # 2. Парсит как JSON
    # 3. Возвращает list[dict] (если получили одиночный объект — оборачивает в список)
```

И общие промпты в `src/adapters/providers/t1_local/provider.py`:
- `SERVICE_PROMPT` — для PDF с описанием одной услуги.
- `TARIFF_PROMPT` — для тарифных таблиц (одна строка → один сервис, чёткие префиксы service_id по группам).

Оба промпта требуют **строго JSON** без markdown, температура `0.0`.

---

## Параллелизм и устойчивость

- `ProviderSyncWorker` запускает все провайдеры через `asyncio.gather(*[..])` — упал один, остальные не падают.
- Внутри провайдера — `asyncio.Semaphore` ограничивает одновременные LLM/HTTP-вызовы.
- Если `Service.model_validate` упал на каком-то объекте — логируется `exception`, объект пропускается, остальные сохраняются.
- Если LLM вернул мусор и JSON не парсится — `_parse_llm_json` логирует error и возвращает пустой список.

---

## Как добавить нового провайдера

1. Создать `src/adapters/providers/<name>/provider.py` с классом, реализующим `async def fetch() -> ServicePackage`.
2. Экспортировать его в `__init__.py` пакета.
3. В `src/config/config.py` добавить флаг `use_<name>_provider`.
4. В `src/application/app.py` в блоке инициализации `providers` добавить ветку `if settings.use_<name>_provider: providers.append(...)`.
5. В `.env.example` задокументировать флаг.

Никаких других изменений (БД, агент, REST) не требуется — `provider_id` появится в `get_providers` автоматически.
