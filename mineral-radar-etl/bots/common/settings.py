"""
Configurações globais do ETL via variáveis de ambiente (.env / Docker).
Usa Pydantic Settings v2 — leitura automática de .env e variáveis de ambiente.
"""
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql://mineralradar:mineralradar_secret@localhost:5432/mineralradar",
        description="Connection string PostgreSQL (psycopg3 format)",
    )

    # ── OpenSearch ───────────────────────────────────────────────────────────
    opensearch_url: str = Field(default="http://localhost:9200")
    opensearch_user: str = Field(default="admin")
    opensearch_pass: str = Field(default="admin")

    # ── Azure OpenAI (embeddings) ─────────────────────────────────────────────
    # Aceita tanto AZURE_OPENAI_ENDPOINT quanto AZURE_OPENAI_API_KEY (nomes comuns do SDK Azure)
    azure_openai_endpoint: str = Field(default="", alias="AZURE_OPENAI_ENDPOINT", validation_alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_key: str = Field(default="", alias="AZURE_OPENAI_API_KEY", validation_alias="AZURE_OPENAI_API_KEY")
    # EMBEDDING_DEPLOYMENT → nome do deployment no Azure OpenAI Studio
    azure_openai_deployment_embedding: str = Field(default="text-embedding-ada-002", alias="EMBEDDING_DEPLOYMENT", validation_alias="EMBEDDING_DEPLOYMENT")
    # Dimensão do modelo (1536 para text-embedding-3-small / ada-002, 3072 para large)
    embedding_dimensions: int = Field(default=1536)

    # ── ANM ──────────────────────────────────────────────────────────────────
    anm_base_url: str = Field(default="https://dadosabertos.anm.gov.br")
    anm_user_agent: str = Field(
        default="Mozilla/5.0 (compatible; MineralRadarBot/1.0; +https://mineralradar.com.br)"
    )

    # ── ETL ──────────────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    # Diretório persistente para dados baixados pelos bots (CSVs, ZIPs, shapefiles).
    # Pode ser sobrescrito via ETL_DATA_DIR no .env.
    # Default: ~/.mineralradar/data  (sobrevive a reboots, fora do /tmp)
    etl_data_dir: Path = Field(
        default=Path.home() / ".mineralradar" / "data",
    )

    # ── Metals-API ───────────────────────────────────────────────────────────
    metals_api_key: str = Field(default="")

    # ── INLABS (DOU — Imprensa Nacional) ─────────────────────────────────────
    inlabs_email:    str = Field(default="", alias="INLABS_EMAIL",    validation_alias="INLABS_EMAIL")
    inlabs_password: str = Field(default="", alias="INLABS_PASSWORD", validation_alias="INLABS_PASSWORD")


# Singleton — importar `settings` em qualquer módulo
settings = Settings()

# Garante que o diretório de dados existe
settings.etl_data_dir.mkdir(parents=True, exist_ok=True)
