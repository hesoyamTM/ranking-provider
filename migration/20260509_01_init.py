"""
Initial schema: providers, services with pgvector embeddings.
"""

from yoyo import step

__depends__ = {}

steps = [
    step("CREATE EXTENSION IF NOT EXISTS vector"),
    step(
        """
        CREATE TABLE providers (
            provider_id   TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            base_platform TEXT NOT NULL,
            regions       TEXT[] NOT NULL DEFAULT '{}',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "DROP TABLE providers",
    ),
    step(
        """
        CREATE TABLE services (
            id              BIGSERIAL PRIMARY KEY,
            service_id      TEXT NOT NULL,
            provider_id     TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
            category        TEXT NOT NULL,
            name            TEXT NOT NULL,
            description     TEXT NOT NULL,
            pricing_model   TEXT NOT NULL,
            price_from_rub  NUMERIC(14, 2) NOT NULL,
            price_unit      TEXT NOT NULL,
            compliance_tags TEXT[] NOT NULL DEFAULT '{}',
            tech_tags       TEXT[] NOT NULL DEFAULT '{}',
            embedding       vector(384),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (provider_id, service_id)
        )
        """,
        "DROP TABLE services",
    ),
    step(
        "CREATE INDEX services_embedding_idx ON services USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)",
        "DROP INDEX services_embedding_idx",
    ),
    step(
        "CREATE INDEX services_category_idx ON services (category)",
        "DROP INDEX services_category_idx",
    ),
]
