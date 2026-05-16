"""
Application Configuration
=========================

Centralized configuration using Pydantic Settings.
Loads from environment variables with validation.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Find .env file - check multiple locations
_env_file = None
_possible_paths = [
    Path(__file__).parent.parent.parent.parent / ".env",  # Project root: MineralRadar/.env
    Path(__file__).parent.parent.parent / ".env",          # Backend root: backend/.env
    Path(".env"),                                           # Current directory
]
for path in _possible_paths:
    if path.exists():
        _env_file = str(path)
        break


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=_env_file,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,  # Allow both field name and alias
    )

    # ==================== Application ====================
    app_name: str = "MineralRadar API"
    app_version: str = "2.0.0"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = Field(default=False)
    api_prefix: str = "/api/v1"

    # ==================== Server ====================
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1

    # ==================== CORS ====================
    # Store as string, parse to list via property
    cors_origins_str: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        alias="cors_origins"
    )
    cors_allow_credentials: bool = True
    cors_allow_methods_str: str = Field(default="*", alias="cors_allow_methods")
    cors_allow_headers_str: str = Field(default="*", alias="cors_allow_headers")

    @property
    def cors_origins(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins_str.split(",")]

    @property
    def cors_allow_methods(self) -> list[str]:
        """Parse CORS methods from comma-separated string."""
        return [m.strip() for m in self.cors_allow_methods_str.split(",")]

    @property
    def cors_allow_headers(self) -> list[str]:
        """Parse CORS headers from comma-separated string."""
        return [h.strip() for h in self.cors_allow_headers_str.split(",")]

    # ==================== Azure AD Authentication ====================
    azure_ad_tenant_id: str = Field(default="")
    azure_ad_client_id: str = Field(default="")
    azure_ad_client_secret: str = Field(default="")
    azure_ad_authority: str = Field(default="")
    azure_ad_scope: str = Field(default="")
    # Application ID URI ou client id da API — audience do access token (ex.: api://supplyradar)
    azure_ad_audience: str = Field(default="", alias="AZURE_AD_AUDIENCE")
    # Com tenant real no .env, o frontend ainda pode usar Bearer dev-token em dev
    # se ENVIRONMENT=development e esta flag for true.
    azure_ad_allow_dev_bearer: bool = Field(default=False, alias="AZURE_AD_ALLOW_DEV_BEARER")

    @property
    def azure_ad_tenant_effectively_set(self) -> bool:
        """
        True se o tenant parece configurado de verdade (não vazio nem placeholder do env.example).
        """
        t = (self.azure_ad_tenant_id or "").strip().lower()
        if not t:
            return False
        placeholders = frozenset({"your-tenant-id", "changeme", "tenant-id-here"})
        return t not in placeholders

    @property
    def azure_ad_authority_url(self) -> str:
        """Constructs the Azure AD authority URL."""
        if self.azure_ad_authority:
            return self.azure_ad_authority
        return f"https://login.microsoftonline.com/{self.azure_ad_tenant_id}"

    # ==================== OpenSearch ====================
    opensearch_endpoint: str = Field(default="")
    opensearch_user: str = Field(default="")
    opensearch_password: str = Field(default="")
    opensearch_use_ssl: bool = False
    opensearch_verify_certs: bool = False
    opensearch_timeout: int = 30

    # ==================== MongoDB ====================
    mongodb_uri: str = Field(default="mongodb://localhost:27017")
    mongodb_database: str = Field(default="mineralradar")
    mongodb_user: str = Field(default="")
    mongodb_password: str = Field(default="")
    mongodb_min_pool_size: int = 5
    mongodb_max_pool_size: int = 50

    @property
    def mongodb_connection_string(self) -> str:
        """Constructs MongoDB connection string with auth if provided."""
        from urllib.parse import quote_plus
        
        if self.mongodb_user and self.mongodb_password:
            # URL encode credentials as required by RFC 3986
            user = quote_plus(self.mongodb_user)
            password = quote_plus(self.mongodb_password)
            
            # Parse URI and inject credentials
            if "mongodb://" in self.mongodb_uri:
                return self.mongodb_uri.replace(
                    "mongodb://",
                    f"mongodb://{user}:{password}@",
                )
            elif "mongodb+srv://" in self.mongodb_uri:
                return self.mongodb_uri.replace(
                    "mongodb+srv://",
                    f"mongodb+srv://{user}:{password}@",
                )
        return self.mongodb_uri

    # ==================== Redis ====================
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6479)
    redis_username: str = Field(default="default")  # Required for Redis 7+
    redis_password: str = Field(default="")
    redis_db: int = Field(default=0)
    redis_ssl: bool = False
    redis_conversation_ttl: int = Field(default=7_200, description="TTL em segundos para o buffer de conversa (padrão: 2h)")

    @property
    def redis_url(self) -> str:
        """Constructs Redis URL with username support for Redis 7+."""
        protocol = "rediss" if self.redis_ssl else "redis"
        if self.redis_password:
            auth = f"{self.redis_username}:{self.redis_password}@"
        else:
            auth = ""
        return f"{protocol}://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ==================== Rate Limiting ====================
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # ==================== Logging ====================
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    # ==================== LLM / Embeddings ====================
    openai_api_key: str = Field(default="")
    azure_openai_endpoint: str = Field(default="")
    azure_openai_api_key: str = Field(default="")
    azure_openai_api_version: str = Field(default="2024-02-01")
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_deployment: str = Field(default="supplyradar-embedding")

    # ==================== LangGraph Agent (Chat LLM) ====================
    azure_openai_chat_deployment: str = Field(default="supplyradar-chat-dev")
    azure_openai_chat_prod_deployment: str = Field(default="supplyradar-chat-dev")
    azure_openai_chat_temperature: float = Field(default=0.1)
    azure_openai_chat_max_tokens: int = Field(default=4096)

    # ==================== Azure Maps ====================
    azure_maps_subscription_key: str = Field(default="")

    # ==================== LangSmith (Tracing) ====================
    langchain_tracing_v2: bool = Field(default=False)
    langchain_api_key: str = Field(default="")
    langchain_project: str = Field(default="supplyradar-dev")
    langchain_endpoint: str = Field(default="https://api.smith.langchain.com")

    # ==================== Feature Flags ====================
    feature_ai_chat_enabled: bool = True
    feature_street_view_enabled: bool = True
    feature_kml_upload_enabled: bool = True

@lru_cache
def get_settings() -> Settings:
    """
    Returns cached settings instance.
    
    Usage:
        from app.core.config import get_settings
        settings = get_settings()
    """
    return Settings()


# Expose settings instance for convenience
settings = get_settings()
