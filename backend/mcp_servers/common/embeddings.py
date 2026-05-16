"""
Embedding Service for MCP Servers
===================================

Generates text embeddings via Azure OpenAI with Redis caching.
Used for semantic search in auxiliary indices (rfb_cnae_v001, anm_substancia_v001).
"""

import logging
from openai import AsyncAzureOpenAI

from mcp_servers.common.config import mcp_settings
from mcp_servers.common.redis_cache import get_redis_cache

logger = logging.getLogger("mcp.embeddings")


class EmbeddingService:
    """
    Azure OpenAI embedding generator with caching.
    
    Flow:
        1. Check Redis cache for existing embedding
        2. If cache miss → call Azure OpenAI API
        3. Cache the result for 24h
        4. Return vector (list of 1536 floats)
    
    Usage:
        service = EmbeddingService()
        vector = await service.embed("areia lavada")
    """

    def __init__(self):
        if not mcp_settings.azure_openai_endpoint:
            logger.warning("Azure OpenAI not configured — embeddings disabled")
            self.client = None
        else:
            # Use stable embedding API version (preview versions may not support embeddings)
            self.client = AsyncAzureOpenAI(
                azure_endpoint=mcp_settings.azure_openai_endpoint,
                api_key=mcp_settings.azure_openai_api_key,
                api_version=mcp_settings.azure_openai_embedding_api_version,
            )
        self.model = mcp_settings.embedding_model          # model name (for cache key)
        self.deployment = mcp_settings.embedding_deployment  # Azure deployment name
        self.dimensions = mcp_settings.embedding_dimensions

    async def embed(self, text: str) -> list[float] | None:
        """
        Generate embedding for a text string.
        Returns cached result if available.
        
        Args:
            text: Text to embed (e.g., "areia lavada", "transporte rodoviário")
            
        Returns:
            List of floats (1536 dimensions) or None if unavailable
        """
        if not self.client:
            return None

        text = text.strip()
        if not text:
            return None

        # 1. Check cache
        cache = await get_redis_cache()
        cached = await cache.get_embedding(text, self.model)
        if cached:
            logger.debug(f"Embedding cache hit: '{text[:50]}'")
            return cached

        # 2. Generate via Azure OpenAI (model= deve ser o nome do deployment no Azure)
        try:
            response = await self.client.embeddings.create(
                input=text,
                model=self.deployment,
            )
            vector = response.data[0].embedding
            logger.debug(f"Embedding generated: '{text[:50]}' → {len(vector)} dims")

            # 3. Cache for next time
            await cache.set_embedding(text, self.model, vector)

            return vector
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return None

    async def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        """
        Generate embeddings for multiple texts.
        Checks cache individually, batches API calls for misses.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors (same order as input)
        """
        if not self.client:
            return [None] * len(texts)

        cache = await get_redis_cache()
        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        # 1. Check cache for each text
        for i, text in enumerate(texts):
            text = text.strip()
            if not text:
                continue
            cached = await cache.get_embedding(text, self.model)
            if cached:
                results[i] = cached
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        if not uncached_texts:
            return results

        # 2. Batch API call for cache misses
        try:
            response = await self.client.embeddings.create(
                input=uncached_texts,
                model=self.model,
            )
            for j, embedding_data in enumerate(response.data):
                idx = uncached_indices[j]
                vector = embedding_data.embedding
                results[idx] = vector
                # Cache each result
                await cache.set_embedding(uncached_texts[j], self.model, vector)

            logger.info(f"Batch embedding: {len(uncached_texts)} generated, {len(texts) - len(uncached_texts)} cached")
        except Exception as e:
            logger.error(f"Batch embedding failed: {e}")

        return results


# Singleton
_embedding_service: EmbeddingService | None = None


async def get_embedding_service() -> EmbeddingService:
    """Get or create the Embedding service singleton."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
