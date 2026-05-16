"""
Shared utilities for MCP Servers.

Modules:
    config                  - Environment-based settings (MCPSettings)
    opensearch_client       - Async OpenSearch client (HTTP REST for data queries)
    opensearch_mcp_client   - MCP Client for OpenSearch native tools (Streamable HTTP)
    unified_mcp_provider    - UnifiedMCPProvider for LangGraph (all servers via MCP)
    redis_cache             - Redis caching for embeddings and search results
    embeddings              - Azure OpenAI embedding generation
    schemas                 - Shared Pydantic models (GeoPoint, Pagination, etc.)
"""

from mcp_servers.common.config import mcp_settings
from mcp_servers.common.schemas import GeoPoint, PaginationParams, SearchResultMeta, ToolResponse

__all__ = [
    "mcp_settings",
    "GeoPoint",
    "PaginationParams",
    "SearchResultMeta",
    "ToolResponse",
]
