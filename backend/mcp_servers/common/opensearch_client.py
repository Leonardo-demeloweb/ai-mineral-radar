"""
OpenSearch Client for MCP Servers
==================================

Async OpenSearch client shared across all MCP Servers.
Provides search, msearch, count, and get operations.
"""

import base64
import logging

from opensearchpy import AsyncOpenSearch

from mcp_servers.common.config import mcp_settings

logger = logging.getLogger("mcp.opensearch")


def _basic_auth_headers(user: str, password: str) -> dict[str, str]:
    """
    Build Authorization: Basic ... using UTF-8 (RFC 7617).

    opensearch-py's default path uses urllib3.make_headers(), which encodes
    credentials as latin-1. Passwords with non-latin-1 supplementary chars fail;
    characters like £ (U+00A3) differ in UTF-8 vs latin-1 byte sequence, so
    the Base64 header no longer matches what curl and OpenSearch expect → 401.
    """
    raw = f"{user}:{password}".encode("utf-8")
    token = base64.b64encode(raw).decode("ascii")
    return {"authorization": f"Basic {token}"}


class OpenSearchService:
    """
    Async OpenSearch client with lifecycle management.
    
    Usage:
        os_service = OpenSearchService()
        await os_service.connect()
        results = await os_service.search("anm_v003", {"query": {"match_all": {}}})
        await os_service.disconnect()
    """

    def __init__(self):
        self.client: AsyncOpenSearch | None = None

    async def connect(self) -> None:
        """Establish connection to OpenSearch cluster."""
        endpoint = mcp_settings.opensearch_endpoint
        if not endpoint:
            raise RuntimeError("OPENSEARCH_ENDPOINT not configured")

        if not endpoint.startswith("http"):
            endpoint = f"https://{endpoint}"

        self.client = AsyncOpenSearch(
            hosts=[endpoint],
            headers=_basic_auth_headers(
                mcp_settings.opensearch_user,
                mcp_settings.opensearch_password,
            ),
            use_ssl=mcp_settings.opensearch_use_ssl,
            verify_certs=mcp_settings.opensearch_verify_certs,
            timeout=mcp_settings.opensearch_timeout,
        )

        # Verify connection
        info = await self.client.info()
        cluster = info.get("cluster_name", "unknown")
        version = info.get("version", {}).get("number", "unknown")
        logger.info(f"OpenSearch connected: cluster={cluster}, version={version}")

    async def disconnect(self) -> None:
        """Close OpenSearch connection."""
        if self.client:
            await self.client.close()
            self.client = None
            logger.info("OpenSearch disconnected")

    def _ensure_connected(self) -> AsyncOpenSearch:
        """Ensure client is connected, raise if not."""
        if not self.client:
            raise RuntimeError("OpenSearch not connected. Call connect() first.")
        return self.client

    # ==================== Core Operations ====================

    async def search(self, index: str, body: dict, **kwargs) -> dict:
        """Execute a search query against an index."""
        client = self._ensure_connected()
        return await client.search(index=index, body=body, **kwargs)

    async def msearch(self, body: list, **kwargs) -> dict:
        """Execute multiple searches in a single request."""
        client = self._ensure_connected()
        return await client.msearch(body=body, **kwargs)

    async def count(self, index: str, body: dict | None = None, **kwargs) -> int:
        """Count documents matching a query."""
        client = self._ensure_connected()
        result = await client.count(index=index, body=body, **kwargs)
        return result.get("count", 0)

    async def get(self, index: str, doc_id: str, **kwargs) -> dict:
        """Get a document by ID."""
        client = self._ensure_connected()
        return await client.get(index=index, id=doc_id, **kwargs)

    async def get_mapping(self, index: str) -> dict:
        """Get index mapping."""
        client = self._ensure_connected()
        return await client.indices.get_mapping(index=index)

    # ==================== Convenience Helpers ====================

    async def search_hits(self, index: str, body: dict, **kwargs) -> list[dict]:
        """Search and return only the _source documents."""
        result = await self.search(index, body, **kwargs)
        return [hit["_source"] for hit in result.get("hits", {}).get("hits", [])]

    async def search_with_meta(self, index: str, body: dict, **kwargs) -> dict:
        """
        Search and return hits with metadata (total, max_score).
        
        Returns:
            {
                "total": int,
                "hits": [{"_id": str, "_score": float, "_source": dict, "sort": list}],
            }
        """
        result = await self.search(index, body, **kwargs)
        hits_data = result.get("hits", {})
        total = hits_data.get("total", {})
        total_value = total.get("value", 0) if isinstance(total, dict) else total

        return {
            "total": total_value,
            "hits": [
                {
                    "_id": hit.get("_id"),
                    "_score": hit.get("_score"),
                    "_source": hit.get("_source", {}),
                    "sort": hit.get("sort", []),
                }
                for hit in hits_data.get("hits", [])
            ],
        }


# Singleton — shared across all MCP servers running in the same process
_opensearch_service: OpenSearchService | None = None


async def get_opensearch() -> OpenSearchService:
    """Get or create the OpenSearch service singleton."""
    global _opensearch_service
    if _opensearch_service is None:
        _opensearch_service = OpenSearchService()
        await _opensearch_service.connect()
    return _opensearch_service
