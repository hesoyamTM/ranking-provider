import json

import httpx
from src.application.app import Settings
from src.models.service_package import UserQuery


class RankingAgent:
    def __init__(self, settings: Settings, scorer=None, geo_service=None):
        self.settings = settings
        self.scorer = scorer
        self.geo_service = geo_service

    async def get_response(self, user_text: str, candidates: list) -> str:
        extraction_result = await self._process_step(user_text)

        if "question" in extraction_result:
            return extraction_result["question"]

        data = extraction_result["data"]

        if data.get("location_name"):
            coords = await self.geo_service.get_coordinates(data["location_name"])
            data["target_lat"] = coords.get("lat", 0.0)
            data["target_lon"] = coords.get("lon", 0.0)
        else:
            data["target_lat"] = 0.0
            data["target_lon"] = 0.0

        query_obj = UserQuery(**data)

        """
        !!!
        """

        ranked_list = self.scorer.rank_marketplace_resources(
            query=query_obj,
            candidates=candidates
        )

        return query_obj


    async def _process_step(self, user_text: str) -> dict:
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

        system_prompt = """
        Ты — аналитик данных в облачном провайдере. Твоя цель — извлечь параметры запроса в строгий JSON формат.

        ПРАВИЛА ИЗВЛЕЧЕНИЯ:
        1. clean_intent: Краткая суть (например, "VDS для VPN").
        2. required_tags: Список технологий (например, ["python", "docker"]).
        3. location_name: Город или регион.
        4. max_budget: Число в РУБЛЯХ. 
           - Если пользователь указал доллары ($), конвертируй в рубли по курсу 100 (для стабильности теста).
           - Если бюджет "неважно "или "любой" — null.
           - Если город или регион "неважно "или "любой" — null.
        
        ФОРМАТ ОТВЕТА:
        - Если данных критически мало (непонятно, что нужно, не хватает), верни: 
          {"question": "Текст вежливого уточняющего вопроса"}
        - Если данных достаточно, верни:
          {
            "data": {
              "clean_intent": str,
              "required_tags": list,
              "max_budget": float,
              "location_name": str,
              "target_lat": float,
              "target_lon": float
            }
          }
        
        Запрещено добавлять лишний текст, только чистый JSON.
        """

        payload = {
            "modelUri": f"gpt://{self.settings.folder_id}/yandexgpt/latest",
            "completionOptions": {"stream": False, "temperature": 0.1},
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_text}
            ]
        }

        headers = {
            "Authorization": f"Api-Key {self.settings.yandex_api_key}",
            "x-folder-id": self.settings.folder_id,
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0)) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()

            res_data = response.json()
            raw_text = res_data['result']['alternatives'][0]['message']['text']

            clean_text = raw_text.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(clean_text)