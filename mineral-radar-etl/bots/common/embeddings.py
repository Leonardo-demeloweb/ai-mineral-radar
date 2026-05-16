"""
embeddings.py — Helpers compartilhados de Azure OpenAI para os bots ETL.

Usado pelos índices que dependem de busca semântica k-NN:
  - mr_substancias_v001 (bot_scm.py)
  - mr_tipo_uso_v001     (bot_scm.py)
  - mr_cnae_v001         (bot_cnae.py)

Outros índices não usam embeddings — a busca semântica é feita sobre os
catálogos acima e o resultado é aplicado como filtro nos índices principais.
"""
from __future__ import annotations

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

EMBEDDING_DIM = 1536  # text-embedding-3-small
ZERO_VECTOR: list[float] = [0.0] * EMBEDDING_DIM


def get_embedding_client():
    """Retorna cliente Azure OpenAI para embeddings, ou None se não configurado."""
    endpoint = settings.azure_openai_endpoint
    key = settings.azure_openai_key
    if not endpoint or not key:
        return None
    try:
        from openai import AzureOpenAI

        return AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=key,
            api_version="2024-02-01",
        )
    except ImportError:
        log.warning("openai package not installed — skipping embeddings")
        return None


def embed_batch(client, texts: list[str], deployment: str) -> list[list[float]]:
    """
    Gera embeddings para uma lista de textos.
    Retorna vetores zero em caso de falha (para não parar o ETL).
    """
    try:
        resp = client.embeddings.create(input=texts, model=deployment)
        return [item.embedding for item in resp.data]
    except Exception as exc:
        log.warning("embed_batch.error", error=str(exc)[:200], batch_size=len(texts))
        return [ZERO_VECTOR] * len(texts)
