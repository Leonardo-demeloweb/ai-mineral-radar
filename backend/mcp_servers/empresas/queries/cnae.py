"""
CNAE Resolution Module
========================

Resolves user search terms into CNAE codes for filtering on rfb_cnpj_v003.

Resolution strategy (ordered):
    1. Try k-NN semantic search on rfb_cnae_v001 (best for vague terms)
    2. Fallback to BM25 match on rfb_cnae_v001 (for exact names)

Index used:
    - rfb_cnae_v001: 2.394 docs (codigo, nomeClasse, nomeSubclasse, embedding)

Analogue of SubstanciaResolver (mcp_servers.jazidas.queries.substancia)
but for economic activity codes (CNAE) instead of mineral substances.
"""

import json
import logging
from typing import Any

from mcp_servers.common.config import mcp_settings
from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.common.embeddings import EmbeddingService
from mcp_servers.common.redis_cache import RedisCache
from mcp_servers.empresas.schemas import ResolucaoCnae, CnaeMatch

logger = logging.getLogger("mcp.empresas.cnae")

# Index name
INDEX_CNAE = "mr_cnae_v001"

# Thresholds
KNN_MIN_SCORE = 0.70          # Minimum k-NN score to consider a match
MATCH_MIN_SCORE = 1.0          # Minimum BM25 score to consider a match
KNN_K = 10                     # Number of k-NN neighbors to retrieve
MATCH_SIZE = 10                # Number of BM25 results to retrieve
MAX_CODES = 20                 # Maximum number of CNAE codes to return


class CnaeResolver:
    """
    Resolves user search terms into CNAE codes.

    Combines semantic (k-NN) and textual (BM25) search with caching.

    Usage:
        resolver = CnaeResolver(os_service, embedding_service, redis_cache)
        result = await resolver.resolver("transporte de minérios")
        # result.codigos = ["4930-2/01", "4930-2/02"]
        # result.metodo = "knn"
    """

    def __init__(
        self,
        os_service: OpenSearchService,
        embedding_service: EmbeddingService,
        redis_cache: RedisCache,
    ):
        self.os = os_service
        self.embeddings = embedding_service
        self.cache = redis_cache

    # ==================================================================
    # PUBLIC API
    # ==================================================================

    async def resolver(self, termo: str) -> ResolucaoCnae:
        """
        Resolve a search term into CNAE codes.

        Strategy:
            1. Check cache for previously resolved codes
            2. Try k-NN on rfb_cnae_v001
            3. Fallback to BM25 match on rfb_cnae_v001

        Args:
            termo: User search term (e.g., "transporte de minérios",
                   "extração de areia", "logística rodoviária")

        Returns:
            ResolucaoCnae with resolved codes and method used
        """
        termo = termo.strip()
        if not termo:
            return ResolucaoCnae(termo_original=termo)

        # 1. Check cache
        cached = await self._get_cached(termo)
        if cached:
            logger.debug(f"Cache hit for '{termo}': {cached}")
            return ResolucaoCnae(
                codigos=cached,
                metodo="cache",
                termo_original=termo,
            )

        # 2. Try k-NN semantic search
        result = await self._buscar_knn(termo)
        if result.encontrou:
            await self._cache_resultado(termo, result)
            return result

        # 3. Fallback to BM25 match
        result = await self._buscar_match(termo)
        if result.encontrou:
            await self._cache_resultado(termo, result)
            return result

        # Nothing found
        logger.warning(f"No CNAE resolution for '{termo}'")
        return ResolucaoCnae(termo_original=termo, metodo="none")

    @staticmethod
    def from_codigos(codigos: list[str]) -> ResolucaoCnae:
        """
        Create a ResolucaoCnae from explicit CNAE codes (no resolution needed).

        Used when the user provides codes directly via ``codigos_cnae`` parameter.

        Args:
            codigos: List of CNAE codes (e.g., ["4930-2/01", "0810-0/99"])

        Returns:
            ResolucaoCnae with method="direto"
        """
        clean = [c.strip() for c in codigos if c and c.strip()]
        return ResolucaoCnae(
            codigos=clean[:MAX_CODES],
            metodo="direto",
            termo_original=", ".join(clean[:5]),
        )

    # ==================================================================
    # SEMANTIC SEARCH (k-NN)
    # ==================================================================

    async def _buscar_knn(self, termo: str) -> ResolucaoCnae:
        """Semantic k-NN search on rfb_cnae_v001."""
        vector = await self.embeddings.embed(termo)
        if not vector:
            logger.debug(f"Embedding unavailable for '{termo}', skipping k-NN")
            return ResolucaoCnae(termo_original=termo)

        query: dict[str, Any] = {
            "size": KNN_K,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": vector,
                        "k": KNN_K,
                    }
                }
            },
            "_source": ["codigo", "nomeClasse", "nomeSubclasse"],
        }

        try:
            result = await self.os.search_with_meta(INDEX_CNAE, query)
            hits = result.get("hits", [])
            return self._parse_hits(hits, KNN_MIN_SCORE, "knn", termo)
        except Exception as e:
            logger.error(f"k-NN CNAE search failed: {e}")
            return ResolucaoCnae(termo_original=termo)

    # ==================================================================
    # TEXT SEARCH (BM25)
    # ==================================================================

    async def _buscar_match(self, termo: str) -> ResolucaoCnae:
        """BM25 text match on rfb_cnae_v001 (nomeClasse + nomeSubclasse)."""
        query: dict[str, Any] = {
            "size": MATCH_SIZE,
            "query": {
                "multi_match": {
                    "query": termo,
                    "fields": [
                        "nomeClasse^2",
                        "nomeSubclasse^1.5",
                        "notasExplicativas",
                    ],
                    "fuzziness": "AUTO",
                }
            },
            "_source": ["codigo", "nomeClasse", "nomeSubclasse"],
        }

        try:
            result = await self.os.search_with_meta(INDEX_CNAE, query)
            hits = result.get("hits", [])
            return self._parse_hits(hits, MATCH_MIN_SCORE, "match", termo)
        except Exception as e:
            logger.error(f"BM25 CNAE search failed: {e}")
            return ResolucaoCnae(termo_original=termo)

    # ==================================================================
    # PARSING
    # ==================================================================

    @staticmethod
    def _parse_hits(
        hits: list[dict],
        min_score: float,
        metodo: str,
        termo: str,
    ) -> ResolucaoCnae:
        """Extract CNAE codes from search hits above the score threshold."""
        matches: list[CnaeMatch] = []
        codigos: list[str] = []

        for hit in hits:
            score = hit.get("_score", 0)
            if score < min_score:
                continue
            source = hit.get("_source", {})
            codigo = source.get("codigo", "")
            nome = source.get("nomeSubclasse") or source.get("nomeClasse", "")

            if codigo and codigo not in codigos:
                codigos.append(codigo)
                matches.append(
                    CnaeMatch(codigo=codigo, nome=nome, score=round(score, 4))
                )

        if codigos:
            logger.info(
                f"{metodo} CNAE '{termo}': {len(codigos)} matches "
                f"(top: {matches[0].nome} code={matches[0].codigo} "
                f"score={matches[0].score})"
            )
            return ResolucaoCnae(
                codigos=codigos[:MAX_CODES],
                matches=matches[:MAX_CODES],
                metodo=metodo,
                termo_original=termo,
            )

        return ResolucaoCnae(termo_original=termo)

    # ==================================================================
    # CACHE
    # ==================================================================

    async def _get_cached(self, termo: str) -> list[str] | None:
        """Check Redis cache for previously resolved CNAE codes."""
        key = f"cnae:{termo.lower().strip()}"
        raw = await self.cache.get(key)
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    async def _cache_resultado(self, termo: str, result: ResolucaoCnae) -> None:
        """Cache resolved CNAE codes for future lookups."""
        if not result.codigos:
            return
        key = f"cnae:{termo.lower().strip()}"
        payload = json.dumps(result.codigos)
        stored = await self.cache.set(
            key, payload, ttl=mcp_settings.cache_empresa_ttl
        )
        if stored:
            logger.debug(
                f"Cached '{termo}' → {len(result.codigos)} CNAEs ({result.metodo})"
            )
