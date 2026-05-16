"""
MCP Servers Configuration
=========================

Centralized config for all MCP Servers.
Loads from environment variables (.env file).
"""

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Find .env file
_env_file = None
_possible_paths = [
    Path(__file__).parent.parent.parent.parent / ".env",  # MineralRadar/.env
    Path(__file__).parent.parent.parent / ".env",          # backend/.env
    Path(".env"),
]
for path in _possible_paths:
    if path.exists():
        _env_file = str(path)
        break


class MCPSettings(BaseSettings):
    """Settings for MCP Servers."""

    model_config = SettingsConfigDict(
        env_file=_env_file,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==================== OpenSearch (HTTP REST — data queries) ====================
    opensearch_endpoint: str = Field(default="")
    opensearch_user: str = Field(default="admin")
    opensearch_password: str = Field(default="")
    opensearch_use_ssl: bool = False
    opensearch_verify_certs: bool = False
    # Agregações pesadas (ex.: ranking CFEM) podem exceder 30s em cluster grande
    opensearch_timeout: int = 120

    # JSON-RPC MCP (LangGraph → MCP servers): tool calls longas devem esperar o OS
    mcp_jsonrpc_timeout: float = 120.0
    mcp_connect_timeout: float = 10.0

    # ==================== OpenSearch MCP Nativo (tool invocation) ====================
    # Endpoint MCP nativo exposto pelo OpenSearch ML Commons
    # Formato: https://<os-endpoint>/_plugins/_ml/mcp/sse
    # Requer: plugins.ml_commons.mcp_server_enabled: true no cluster
    opensearch_mcp_endpoint: str = Field(default="")
    opensearch_mcp_timeout: int = Field(default=30)
    opensearch_mcp_sse_read_timeout: int = Field(default=300)

    # ==================== Redis ====================
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6479)
    redis_username: str = Field(default="default")
    redis_password: str = Field(default="")
    redis_db: int = Field(default=0)
    redis_ssl: bool = False

    # ==================== Azure OpenAI (Embeddings) ====================
    azure_openai_endpoint: str = Field(default="")
    azure_openai_api_key: str = Field(default="")
    azure_openai_api_version: str = Field(default="2024-02-01")
    # Separate stable API version for embeddings (preview versions may lack support)
    azure_openai_embedding_api_version: str = Field(default="2024-02-01")
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_deployment: str = Field(default="supplyradar-embedding")
    embedding_dimensions: int = Field(default=1536)

    # ==================== MCP Server Ports (Streamable HTTP) ====================
    mcp_jazidas_port: int = Field(default=8110)
    mcp_empresas_port: int = Field(default=8111)
    mcp_geo_port: int = Field(default=8112)

    # ==================== MCP Server URLs (for UnifiedMCPProvider) ====================
    # URLs completas usadas pelo LangGraph para conectar a cada MCP Server
    mcp_jazidas_url: str = Field(default="http://localhost:8110/mcp")
    mcp_empresas_url: str = Field(default="http://localhost:8111/mcp")
    mcp_geo_url: str = Field(default="http://localhost:8112/mcp")

    # ==================== Azure OpenAI (Chat — LangGraph) ====================
    azure_openai_chat_deployment: str = Field(
        default="supplyradar-chat-dev", description="Azure OpenAI deployment name for chat/agent LLM"
    )
    azure_openai_chat_prod_deployment: str = Field(
        default="supplyradar-chat-dev", description="Azure OpenAI prod deployment name"
    )
    azure_openai_chat_temperature: float = Field(default=0.1)
    azure_openai_chat_max_tokens: int = Field(default=4096)

    # ==================== MongoDB (geocode persistent cache) ====================
    mongodb_uri: str = Field(default="mongodb://localhost:27017")
    mongodb_database: str = Field(default="supplyradar")
    mongodb_user: str = Field(default="")
    mongodb_password: str = Field(default="")

    @property
    def mongodb_connection_string(self) -> str:
        from urllib.parse import quote_plus
        if self.mongodb_user and self.mongodb_password:
            user = quote_plus(self.mongodb_user)
            password = quote_plus(self.mongodb_password)
            if "mongodb://" in self.mongodb_uri:
                return self.mongodb_uri.replace("mongodb://", f"mongodb://{user}:{password}@")
            if "mongodb+srv://" in self.mongodb_uri:
                return self.mongodb_uri.replace("mongodb+srv://", f"mongodb+srv://{user}:{password}@")
        return self.mongodb_uri

    # ==================== Metals-API (preços em tempo real) ====================
    metals_api_key: str = Field(default="")
    metals_api_base_url: str = Field(default="https://metalapi.com/api/v1")
    # TTL longo no free plan (100 req/mês = ~3/dia). Cada chamada com variação = 2 req.
    cache_metals_price_ttl: int = Field(default=3600)  # 1h — conserva quota free

    # ==================== Azure Maps ====================
    azure_maps_subscription_key: str = Field(default="")
    azure_maps_base_url: str = Field(default="https://atlas.microsoft.com")

    # ==================== Truck Defaults (Azure Maps Route) ====================
    truck_weight_kg: int = Field(default=40000)
    truck_height_m: float = Field(default=4.5)
    truck_width_m: float = Field(default=2.6)
    truck_length_m: float = Field(default=18.75)
    truck_axle_weight_kg: int = Field(default=12000)

    # ==================== Cache TTL (seconds) ====================
    cache_embedding_ttl: int = Field(default=86400)     # 24h
    cache_search_ttl: int = Field(default=3600)          # 1h
    cache_substancia_ttl: int = Field(default=86400)     # 24h
    cache_empresa_ttl: int = Field(default=86400)        # 24h
    cache_geo_municipio_ttl: int = Field(default=86400)  # 24h
    cache_geo_poligono_ttl: int = Field(default=604800)  # 7d
    cache_geo_rota_ttl: int = Field(default=3600)        # 1h
    cache_geo_geocode_ttl: int = Field(default=86400)    # 24h

    # ==================== Search Defaults ====================
    default_page_size: int = Field(default=10)
    max_page_size: int = Field(default=50)
    default_geo_radius_km: int = Field(default=50)
    max_geo_radius_km: int = Field(default=500)


mcp_settings = MCPSettings()
