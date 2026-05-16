"""
Utilitários de hash para controle de reindexação incremental.
Usa xxhash (muito mais rápido que MD5/SHA para grandes volumes).
"""
import json
from typing import Any

import xxhash


def hash_record(record: dict[str, Any]) -> str:
    """
    Calcula o hash xxh64 de um registro (dict) para detectar mudanças.
    Campos de controle ETL (hash, ingested_at, indexed_at, etc.) são excluídos.
    """
    EXCLUDE_KEYS = {
        "hash", "ingested_at", "indexed_at", "last_indexed_hash",
        "needs_reindex", "updated_at", "source_file",
    }
    payload = {k: v for k, v in record.items() if k not in EXCLUDE_KEYS}
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return xxhash.xxh64(serialized).hexdigest()


def hash_string(s: str) -> str:
    """Hash xxh64 de uma string."""
    return xxhash.xxh64(s.encode()).hexdigest()
