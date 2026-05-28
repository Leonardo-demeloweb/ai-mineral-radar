"""
Substance Resolution Module
=============================

Resolves user search terms into substance names or tipo-uso descriptions
for filtering on mr_jazidas_v001.

Resolution strategy (ordered):
    1. Check Redis cache
    2. If term is a use-case ("para pavimentação", "construção") → k-NN on mr_tipo_uso_v001 first
    3. BM25 STRONG match on mr_substancias_v001 (top_score ≥ BM25_STRONG_SCORE) →
       confia 100% no texto, ignora k-NN (evita drift semântico tipo
       "terras raras" puxando "minério de ferro")
    4. k-NN semantic search on mr_substancias_v001 com validação textual:
       candidato precisa compartilhar pelo menos 1 token significativo com o termo,
       a não ser que score ≥ KNN_HIGH_CONFIDENCE
    5. Fallback to BM25 fraco em mr_substancias_v001 (score >= MATCH_MIN_SCORE)
    6. If still nothing → tipo-uso (k-NN then BM25)

Indices used:
    - mr_substancias_v001: 862 docs (catálogo oficial ANM; nome, nome_normalizado, tipo_uso, embedding ✓)
    - mr_tipo_uso_v001:     26 docs (descricao, grupo, categoria, embedding ✓)

Result fields used in mr_jazidas_v001 filter:
    - substancias_desc.keyword ← nome_normalizado from mr_substancias_v001
    - uso_substancia            ← descricao from mr_tipo_uso_v001
"""

import logging
import unicodedata
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.common.embeddings import EmbeddingService
from mcp_servers.common.redis_cache import RedisCache
from mcp_servers.jazidas.schemas import (
    ResolucaoSubstancia,
    SubstanciaMatch,
    TipoUsoMatch,
)

logger = logging.getLogger("mcp.jazidas.substancia")

# Index names
INDEX_SUBSTANCIA = "mr_substancias_v001"
INDEX_TIPO_USO = "mr_tipo_uso_v001"

# Thresholds
KNN_MIN_SCORE_SUBSTANCIA = 0.85   # mr_substancias_v001 (862 docs) — higher bar
KNN_HIGH_CONFIDENCE      = 0.95   # acima disso: aceita mesmo sem overlap textual (era 0.90 — causava drift "terras raras → ferro")
KNN_MIN_SCORE_TIPO_USO   = 0.72   # mr_tipo_uso_v001 (26 docs) — lower bar (small corpus)
KNN_MIN_SCORE = KNN_MIN_SCORE_SUBSTANCIA   # backwards compat alias
MATCH_MIN_SCORE   = 1.0        # Minimum BM25 score to consider a match
# BM25_STRONG_SCORE calibrado para mr_substancias_v001 (~862 docs, corpus pequeno).
# Scores observados: "ouro"=2.8, "monazita"=3.1, "terras raras"=4.6, "bastnasita"=3.0.
# Um score >= 2.5 indica match textual preciso; pula k-NN (evita drift semântico).
# Era 6.0 — nunca atingido, fazendo k-NN rodar sempre e causar drift ("terras raras" → "ferro").
BM25_STRONG_SCORE = 2.5
KNN_K = 10                     # Number of k-NN neighbors to retrieve
MATCH_SIZE = 10                # Number of BM25 results to retrieve
MAX_IDS = 4                    # Máximo de IDs no terms filter (reduzido de 8 para limitar blast radius do k-NN)
MAX_TIPO_USO_IDS = 3           # Limit tipo-uso k-NN to avoid over-broad wildcard filters

# Stopwords e tokens descartados na validação textual de candidatos k-NN
_TOKEN_STOPWORDS = {
    "para", "com", "sem", "tipo", "uso", "outros", "outras",
    "minerio", "minerais", "mineral", "substancia", "substancias",
    "rocha", "rochas", "pedra", "pedras",
}


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _significant_tokens(text: str, min_len: int = 4) -> set[str]:
    """
    Tokens significativos (>= ``min_len`` chars), lowercase, sem acento, sem stopwords.
    Usado para validar consistência textual entre termo do usuário e candidato k-NN.
    """
    if not text:
        return set()
    stripped = _strip_accents(text).replace(",", " ").replace("/", " ").replace("-", " ")
    return {
        tok for tok in stripped.split()
        if len(tok) >= min_len and tok not in _TOKEN_STOPWORDS
    }


def _tokens_share_stem(query_tokens: set[str], cand_tokens: set[str], min_len: int = 4) -> bool:
    """
    True se algum par (q, c) tem stem compartilhado: o menor é prefixo do maior,
    e o menor tem pelo menos ``min_len`` chars.

    Captura variações morfológicas legítimas (monazita ↔ monazítica,
    terras ↔ terra) sem aceitar matches lexicais ruidosos
    (monazita vs tonalito, monazita vs goyazita).
    """
    for q in query_tokens:
        for c in cand_tokens:
            shorter, longer = (q, c) if len(q) <= len(c) else (c, q)
            if len(shorter) >= min_len and longer.startswith(shorter):
                return True
    return False

# Keywords that indicate a use-case term ("X para Y") → resolve via tipo-uso first
_USO_KEYWORDS = {
    "pavimentação", "pavimento", "construção", "construcao", "fundação", "fundacao",
    "revestimento", "aterro", "enchimento", "lastro", "concreto", "argamassa",
    "industrial", "siderurgia", "cerâmica", "ceramica", "vidro", "fertilizante",
    "cimento", "agregado", "joalheria", "gema", "ourivesaria", "joia",
    "balneoterapia", "engarrafamento", "radioativo", "energético", "energetico",
}


class SubstanciaResolver:
    """
    Resolves user search terms into substance/tipo-uso IDs.

    Combines semantic (k-NN) and textual (BM25) search with caching.

    Usage:
        resolver = SubstanciaResolver(os_service, embedding_service, redis_cache)
        result = await resolver.resolver("areia lavada")
        # result.ids_substancia = [200207, 200200, 200201]
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

    async def resolver(self, termo: str) -> ResolucaoSubstancia:
        """
        Resolve a search term into substance names or tipo-uso descriptions.

        Strategy:
            1. Check Redis cache
            2. If term describes a use-case → try BM25 on mr_tipo_uso_v001 first
            3. Try k-NN on mr_substancias_v001 (semantic)
            4. Fallback to BM25 on mr_substancias_v001 (exact/fuzzy)
            5. If still nothing → try BM25 on mr_tipo_uso_v001

        Args:
            termo: User search term (e.g., "areia lavada", "material para pavimentação")

        Returns:
            ResolucaoSubstancia with resolved names/descriptions and method used
        """
        termo = termo.strip()
        if not termo:
            return ResolucaoSubstancia(termo_original=termo)

        # 1. Check cache
        cached_ids = await self.cache.get_substance_ids(termo)
        if cached_ids:
            logger.debug(f"Cache hit for '{termo}': {cached_ids}")
            return ResolucaoSubstancia(
                ids_substancia=cached_ids,
                metodo="cache",
                termo_original=termo,
            )

        # Detect use-case terms ("pedra para pavimentação", "material de construção").
        # When the term describes a USE rather than a substance name, resolve via
        # tipo-uso FIRST to avoid false positives (e.g. gemstones matching "pedra").
        termo_lower = termo.lower()
        is_uso_term = any(kw in termo_lower for kw in _USO_KEYWORDS)

        if is_uso_term:
            logger.debug(f"'{termo}' detected as use-case term → trying tipo-uso k-NN first")
            result = await self._buscar_tipo_uso_knn(termo)
            if result.encontrou:
                await self._cache_resultado(termo, result)
                return result
            # Fallback to BM25 if k-NN misses
            result = await self._buscar_tipo_uso_match(termo)
            if result.encontrou:
                await self._cache_resultado(termo, result)
                return result

        # 2. Tentar BM25 PRIMEIRO — quando há match textual forte (nome canônico
        #    do catálogo ANM como "terras raras", "ouro", "ferro", "litio"), o
        #    BM25 dá precisão. k-NN só é necessário para variações lexicais.
        bm25_strong = await self._buscar_substancia_match(termo)
        if bm25_strong.encontrou and bm25_strong.matches_substancia:
            top_score = bm25_strong.matches_substancia[0].score
            if top_score >= BM25_STRONG_SCORE:
                logger.info(
                    f"BM25 strong match for '{termo}' "
                    f"(top_score={top_score:.2f} ≥ {BM25_STRONG_SCORE}) → skipping k-NN"
                )
                await self._cache_resultado(termo, bm25_strong)
                return bm25_strong

        # 3. k-NN semantic search on substances (com validação textual contra drift)
        result = await self._buscar_substancia_knn(termo)
        if result.encontrou:
            await self._cache_resultado(termo, result)
            return result

        # 4. Fallback to BM25 weaker matches (1.0 ≤ score < BM25_STRONG_SCORE)
        if bm25_strong.encontrou:
            await self._cache_resultado(termo, bm25_strong)
            return bm25_strong

        # 4. Try tipo-uso (k-NN then BM25) — only if not already tried above
        if not is_uso_term:
            result = await self._buscar_tipo_uso_knn(termo)
            if result.encontrou:
                await self._cache_resultado(termo, result)
                return result
            result = await self._buscar_tipo_uso_match(termo)
            if result.encontrou:
                await self._cache_resultado(termo, result)
                return result

        # Nothing found
        logger.warning(f"No resolution for '{termo}'")
        return ResolucaoSubstancia(termo_original=termo, metodo="none")

    # ==================================================================
    # SUBSTANCE SEARCH (mr_substancias_v001)
    # ==================================================================

    async def _buscar_substancia_knn(self, termo: str) -> ResolucaoSubstancia:
        """Semantic k-NN search on mr_substancias_v001."""
        vector = await self.embeddings.embed(termo)
        if not vector:
            logger.debug(f"Embedding unavailable for '{termo}', skipping k-NN")
            return ResolucaoSubstancia(termo_original=termo)

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
            "_source": ["nome", "nome_normalizado"],
        }

        # Validação textual: candidato k-NN com score "médio" precisa compartilhar
        # pelo menos 1 token significativo com o termo do usuário. Evita drift do
        # tipo "terras raras" → "minério de ferro" (alta similaridade semântica em
        # textos geológicos mas substância totalmente distinta no catálogo ANM).
        termo_tokens = _significant_tokens(termo)

        try:
            result = await self.os.search_with_meta(INDEX_SUBSTANCIA, query)
            hits = result.get("hits", [])

            matches: list[SubstanciaMatch] = []
            ids: list[str] = []
            rejected: list[tuple[str, float]] = []

            for hit in hits:
                score = hit.get("_score", 0)
                if score < KNN_MIN_SCORE:
                    continue
                source = hit.get("_source", {})
                # nome_normalizado = lowercase/no-accent, matches substancias_desc.keyword
                nome_norm = source.get("nome_normalizado") or source.get("nome", "").lower()
                nome_disp = source.get("nome", nome_norm)

                if not nome_norm or nome_norm in ids:
                    continue

                # ── Validação textual de drift ────────────────────────────────
                # Aceita sempre se score >= KNN_HIGH_CONFIDENCE (alta confiança).
                # Caso contrário, exige overlap de pelo menos 1 token significativo
                # entre termo do usuário e nome do candidato.
                if score < KNN_HIGH_CONFIDENCE and termo_tokens:
                    cand_tokens = _significant_tokens(nome_disp) | _significant_tokens(nome_norm)
                    if cand_tokens and not (termo_tokens & cand_tokens):
                        rejected.append((nome_disp, score))
                        continue

                ids.append(nome_norm)
                matches.append(
                    SubstanciaMatch(id=nome_norm, nome=nome_disp, score=round(score, 4))
                )

            if rejected:
                logger.info(
                    f"k-NN drift filter rejeitou {len(rejected)} candidato(s) "
                    f"para '{termo}': "
                    + ", ".join(f"{n}({s:.3f})" for n, s in rejected[:5])
                )

            if ids:
                logger.info(
                    f"k-NN substância '{termo}': {len(ids)} matches "
                    f"(top: {matches[0].nome} score={matches[0].score})"
                )
                return ResolucaoSubstancia(
                    ids_substancia=ids[:MAX_IDS],
                    matches_substancia=matches[:MAX_IDS],
                    metodo="knn",
                    termo_original=termo,
                )

        except Exception as e:
            logger.error(f"k-NN substância search failed: {e}")

        return ResolucaoSubstancia(termo_original=termo)

    async def _buscar_substancia_match(self, termo: str) -> ResolucaoSubstancia:
        """
        BM25 text match on mr_substancias_v001.nome.

        Aplica DOIS filtros anti-ruído contra fuzziness do BM25:
          1. Threshold relativo: score >= max(MATCH_MIN_SCORE, 0.6 * top_score).
             Descarta matches obviamente menos relevantes (ex.: "ferro" score
             1.95 quando top "terras raras" tem 4.57).
          2. Overlap de stem: candidato precisa compartilhar prefixo (>=4 chars)
             com algum token do termo. Evita falsos-positivos de fuzziness AUTO
             (ex.: "monazita" → "tonalito"/"goyazita" via troca de 2 chars).
        """
        query: dict[str, Any] = {
            "size": MATCH_SIZE,
            "query": {
                "match": {
                    "nome": {
                        "query": termo,
                        "fuzziness": "AUTO",
                    }
                }
            },
            "_source": ["nome", "nome_normalizado"],
        }

        try:
            result = await self.os.search_with_meta(INDEX_SUBSTANCIA, query)
            hits = result.get("hits", [])
            if not hits:
                return ResolucaoSubstancia(termo_original=termo)

            top_score = hits[0].get("_score", 0)
            relative_min_score = max(MATCH_MIN_SCORE, top_score * 0.6)
            termo_tokens = _significant_tokens(termo)

            matches: list[SubstanciaMatch] = []
            ids: list[str] = []
            rejected: list[tuple[str, float, str]] = []

            for hit in hits:
                score = hit.get("_score", 0)
                if score < relative_min_score:
                    continue
                source = hit.get("_source", {})
                nome_norm = source.get("nome_normalizado") or source.get("nome", "").lower()
                nome_disp = source.get("nome", nome_norm)

                if not nome_norm or nome_norm in ids:
                    continue

                # Filtro de stem: rejeita fuzzy noise (monazita → tonalito).
                # Permite o termo único (mesma stem ele consigo) e variações
                # morfológicas legítimas (monazita ↔ monazítica).
                if termo_tokens:
                    cand_tokens = _significant_tokens(nome_disp) | _significant_tokens(nome_norm)
                    if cand_tokens and not _tokens_share_stem(termo_tokens, cand_tokens):
                        rejected.append((nome_disp, score, "no-stem-overlap"))
                        continue

                ids.append(nome_norm)
                matches.append(
                    SubstanciaMatch(id=nome_norm, nome=nome_disp, score=round(score, 4))
                )

            if rejected:
                logger.info(
                    f"BM25 stem filter rejeitou {len(rejected)} candidato(s) "
                    f"para '{termo}' (relative_min={relative_min_score:.2f}): "
                    + ", ".join(f"{n}({s:.2f}/{why})" for n, s, why in rejected[:5])
                )

            if ids:
                logger.info(
                    f"BM25 substância '{termo}': {len(ids)} matches "
                    f"(top: {matches[0].nome} score={matches[0].score})"
                )
                return ResolucaoSubstancia(
                    ids_substancia=ids[:MAX_IDS],
                    matches_substancia=matches[:MAX_IDS],
                    metodo="match",
                    termo_original=termo,
                )

        except Exception as e:
            logger.error(f"BM25 substância search failed: {e}")

        return ResolucaoSubstancia(termo_original=termo)

    # ==================================================================
    # TIPO-USO SEARCH (mr_tipo_uso_v001)
    # ==================================================================

    async def _buscar_tipo_uso_knn(self, termo: str) -> ResolucaoSubstancia:
        """Semantic k-NN search on mr_tipo_uso_v001.embedding."""
        vector = await self.embeddings.embed(termo)
        if not vector:
            logger.debug(f"Embedding unavailable for '{termo}', skipping tipo-uso k-NN")
            return ResolucaoSubstancia(termo_original=termo)

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
            "_source": ["descricao"],
        }

        try:
            result = await self.os.search_with_meta(INDEX_TIPO_USO, query)
            hits = result.get("hits", [])

            matches: list[TipoUsoMatch] = []
            ids: list[str] = []

            for hit in hits:
                score = hit.get("_score", 0)
                if score < KNN_MIN_SCORE_TIPO_USO:
                    continue
                source = hit.get("_source", {})
                ds_uso = source.get("descricao", "")

                if ds_uso and ds_uso not in ids:
                    ids.append(ds_uso)
                    matches.append(
                        TipoUsoMatch(id=ds_uso, descricao=ds_uso, score=round(score, 4))
                    )

            if ids:
                logger.info(
                    f"k-NN tipo-uso '{termo}': {len(ids)} matches "
                    f"(top: {matches[0].descricao} score={matches[0].score})"
                )
                return ResolucaoSubstancia(
                    ids_tipo_uso=ids[:MAX_TIPO_USO_IDS],
                    matches_tipo_uso=matches[:MAX_TIPO_USO_IDS],
                    metodo="tipo_uso_knn",
                    termo_original=termo,
                )

        except Exception as e:
            logger.error(f"k-NN tipo-uso search failed: {e}")

        return ResolucaoSubstancia(termo_original=termo)

    async def _buscar_tipo_uso_match(self, termo: str) -> ResolucaoSubstancia:
        """BM25 text match on mr_tipo_uso_v001.descricao."""
        query: dict[str, Any] = {
            "size": MATCH_SIZE,
            "query": {
                "multi_match": {
                    "query": termo,
                    "fields": ["descricao", "descricao.keyword^2"],
                    "fuzziness": "AUTO",
                }
            },
            "_source": ["descricao"],
        }

        try:
            result = await self.os.search_with_meta(INDEX_TIPO_USO, query)
            hits = result.get("hits", [])

            matches: list[TipoUsoMatch] = []
            ids: list[str] = []

            for hit in hits:
                score = hit.get("_score", 0)
                if score < MATCH_MIN_SCORE:
                    continue
                source = hit.get("_source", {})
                ds_uso = source.get("descricao", "")

                if ds_uso and ds_uso not in ids:
                    ids.append(ds_uso)
                    matches.append(
                        TipoUsoMatch(id=ds_uso, descricao=ds_uso, score=round(score, 4))
                    )

            if ids:
                logger.info(
                    f"BM25 tipo-uso '{termo}': {len(ids)} matches "
                    f"(top: {matches[0].descricao} score={matches[0].score})"
                )
                return ResolucaoSubstancia(
                    ids_tipo_uso=ids[:MAX_IDS],
                    matches_tipo_uso=matches[:MAX_IDS],
                    metodo="tipo_uso_match",
                    termo_original=termo,
                )

        except Exception as e:
            logger.error(f"BM25 tipo-uso search failed: {e}")

        return ResolucaoSubstancia(termo_original=termo)

    # ==================================================================
    # CACHE
    # ==================================================================

    async def _cache_resultado(self, termo: str, result: ResolucaoSubstancia) -> None:
        """Cache resolved IDs for future lookups."""
        ids = result.ids_filter
        if ids:
            await self.cache.set_substance_ids(termo, ids)
            logger.debug(f"Cached '{termo}' → {len(ids)} IDs ({result.metodo})")
