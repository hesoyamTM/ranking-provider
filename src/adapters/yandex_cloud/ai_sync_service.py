import httpx
import json
from typing import List, Dict, Any

class YandexCloudClient:
    def __init__(self, api_key: str, folder_id: str):
        self.headers = {
            "Authorization": f"Api-Key {api_key}",
            "x-folder-id": folder_id,
            "OpenAI-Project": folder_id,
            "OpenAI-Beta": "assistants=v1"
        }
        self.base_url = "https://ai.api.cloud.yandex.net/v1"

    async def upload_json(self, data: List[Dict]) -> str:
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/files",
                headers=self.headers,
                files={"file": ("data.json", content, "application/json")},
                data={"purpose": "assistants"}
            )
            resp.raise_for_status()
            return resp.json()["id"]

    async def get_vs_files(self, vs_id: str) -> List[str]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/vector_stores/{vs_id}/files", headers=self.headers)
            resp.raise_for_status()
            return [f["id"] for f in resp.json().get("data", [])]

    async def remove_file(self, vs_id: str, file_id: str):
        async with httpx.AsyncClient() as client:
            await client.delete(f"{self.base_url}/vector_stores/{vs_id}/files/{file_id}", headers=self.headers)
            await client.delete(f"{self.base_url}/files/{file_id}", headers=self.headers)

    async def attach_file(self, vs_id: str, file_id: str):
        async with httpx.AsyncClient() as client:
            await client.post(f"{self.base_url}/vector_stores/{vs_id}/files", headers=self.headers, json={"file_id": file_id})

async def sync_agent_trends(db_repository: Any, api_key: str, folder_id: str, vs_id: str):
    client = YandexCloudClient(api_key, folder_id)

    trends = await db_repository.get_trends_json()
    if not trends:
        return None

    new_id = await client.upload_json(trends)

    old_ids = await client.get_vs_files(vs_id)
    for oid in old_ids:
        if oid != new_id:
            await client.remove_file(vs_id, oid)

    await client.attach_file(vs_id, new_id)
    return new_id