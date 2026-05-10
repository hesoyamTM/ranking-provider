import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    postgres_dsn: str
    yandex_api_key: str
    yandex_model: str
    yandex_base_url: str
    data_dir: Path
    migrations_dir: Path
    sync_interval_seconds: float
    embedding_model: str
    host: str
    port: int
    use_t1_local_provider: bool
    use_t1_web_provider: bool
    use_cloudru_provider: bool
    use_yandex_cloud_provider: bool
    use_selectel_provider: bool
    use_vk_cloud_provider: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        folder_id = os.environ.get("YANDEX_FOLDER_ID", "")
        model_name = os.environ.get("YANDEX_MODEL", "yandexgpt-5/latest")
        model = f"gpt://{folder_id}/{model_name}" if folder_id else model_name
        return cls(
            postgres_dsn=os.environ.get(
                "POSTGRES_DSN",
                "postgresql://postgres:postgres@localhost:5432/provider_ranking",
            ),
            yandex_api_key=os.environ.get("YANDEX_API_KEY", ""),
            yandex_model=model,
            yandex_base_url=os.environ.get(
                "YANDEX_BASE_URL", "https://llm.api.cloud.yandex.net/v1"
            ),
            data_dir=Path(os.environ.get("T1_DATA_DIR", "data/t1")).resolve(),
            migrations_dir=Path(
                os.environ.get("MIGRATIONS_DIR", "migration")
            ).resolve(),
            sync_interval_seconds=float(
                os.environ.get("SYNC_INTERVAL_SECONDS", "3600")
            ),
            embedding_model=os.environ.get(
                "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
            ),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8000")),
            use_t1_local_provider=os.environ.get("T1_LOCAL_PROVIDER", "false").lower()
            == "true",
            use_t1_web_provider=os.environ.get("T1_WEB_PROVIDER", "false").lower()
            == "true",
            use_cloudru_provider=os.environ.get("CLOUDRU_WEB_PROVIDER", "false").lower()
            == "true",
            use_yandex_cloud_provider=os.environ.get(
                "YANDEX_CLOUD_WEB_PROVIDER", "false"
            ).lower()
            == "true",
            use_selectel_provider=os.environ.get(
                "SELECTEL_WEB_PROVIDER", "false"
            ).lower()
            == "true",
            use_vk_cloud_provider=os.environ.get(
                "VK_CLOUD_WEB_PROVIDER", default="false"
            ).lower()
            == "true",
        )
