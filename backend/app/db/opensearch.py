"""
OpenSearch Connection
=====================

Async OpenSearch client for search, vectors, and geospatial queries.
"""

import base64

from opensearchpy import AsyncOpenSearch

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _basic_auth_headers(user: str, password: str) -> dict[str, str]:
    """Build Authorization header using UTF-8 credentials."""
    raw = f"{user}:{password}".encode("utf-8")
    token = base64.b64encode(raw).decode("ascii")
    return {"authorization": f"Basic {token}"}


class OpenSearchClient:
    """
    OpenSearch client wrapper with connection lifecycle management.

    Provides low-level search, msearch, count and get primitives.
    Domain-specific queries (full-text, k-NN, geo) are built and
    executed by the MCP server tools in mcp_servers/.

    Usage:
        # At startup
        await opensearch_client.connect()

        # At shutdown
        await opensearch_client.disconnect()
    """

    def __init__(self):
        self.client: AsyncOpenSearch | None = None

    async def connect(self) -> None:
        """Establish connection to OpenSearch."""
        if not settings.opensearch_endpoint:
            logger.warning("OpenSearch endpoint not configured - search disabled")
            return

        try:
            # Parse endpoint
            endpoint = settings.opensearch_endpoint
            if not endpoint.startswith("http"):
                endpoint = f"https://{endpoint}"

            self.client = AsyncOpenSearch(
                hosts=[endpoint],
                headers=_basic_auth_headers(
                    settings.opensearch_user,
                    settings.opensearch_password,
                ),
                use_ssl=settings.opensearch_use_ssl,
                verify_certs=settings.opensearch_verify_certs,
                timeout=settings.opensearch_timeout,
            )

            # Verify connection
            info = await self.client.info()
            cluster_name = info.get("cluster_name", "unknown")
            version = info.get("version", {}).get("number", "unknown")

            logger.info(
                "OpenSearch connected",
                cluster=cluster_name,
                version=version,
            )
        except Exception as e:
            logger.error("OpenSearch connection failed", error=str(e))
            self.client = None
            raise

    async def disconnect(self) -> None:
        """Close OpenSearch connection."""
        if self.client:
            await self.client.close()
            self.client = None
            logger.info("OpenSearch disconnected")

    # ==================== Search Methods ====================

    async def search(
        self,
        index: str,
        body: dict,
        **kwargs,
    ) -> dict:
        """
        Execute a search query.
        
        Args:
            index: Index name or pattern
            body: Query DSL body
            **kwargs: Additional OpenSearch parameters
        
        Returns:
            Search results
        """
        if not self.client:
            raise RuntimeError("OpenSearch not connected")
        
        return await self.client.search(index=index, body=body, **kwargs)

    async def get(
        self,
        index: str,
        id: str,
        **kwargs,
    ) -> dict:
        """Get a document by ID."""
        if not self.client:
            raise RuntimeError("OpenSearch not connected")
        
        return await self.client.get(index=index, id=id, **kwargs)

    async def count(
        self,
        index: str,
        body: dict | None = None,
        **kwargs,
    ) -> int:
        """Count documents matching a query."""
        if not self.client:
            raise RuntimeError("OpenSearch not connected")
        
        result = await self.client.count(index=index, body=body, **kwargs)
        return result.get("count", 0)

    async def msearch(
        self,
        body: list,
        **kwargs,
    ) -> dict:
        """
        Execute multiple searches in one request.
        
        Args:
            body: List of header/query pairs
        
        Returns:
            Multiple search results
        """
        if not self.client:
            raise RuntimeError("OpenSearch not connected")
        
        return await self.client.msearch(body=body, **kwargs)


# Singleton instance
opensearch_client = OpenSearchClient()


async def get_opensearch_client() -> OpenSearchClient:
    """FastAPI dependency to get OpenSearch client."""
    return opensearch_client
