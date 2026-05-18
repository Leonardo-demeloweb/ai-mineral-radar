"""
migrate_geoquimica_mapping.py — Corrige mapping de mr_geoquimica_v001 sem re-baixar OGC
=====================================================================================

Se ``bot_geoquimica --index --recreate`` rodou **sem** ``setup_indices`` antes,
o índice pode ter sido auto-criado sem ``location: geo_point`` — queries geo
do MCP falham, mas os **77K documentos** já estão no cluster.

Este script:
  1. Reindexa ``mr_geoquimica_v001`` → índice temporário com mapping oficial
  2. Recria ``mr_geoquimica_v001`` com ``setup_indices.py``
  3. Reindexa de volta e apaga o temporário

Não chama a API SGB (~30 min economizados).

Uso (``backend/`` + venv do backend)::

    python scripts/migrate_geoquimica_mapping.py
    python scripts/migrate_geoquimica_mapping.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts.setup_indices import ALL_INDICES, create_index, get_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("migrate_geoquimica")

INDEX = "mr_geoquimica_v001"
STAGING = "mr_geoquimica_v001__staging"


def _location_type(client, index: str) -> str | None:
    if not client.indices.exists(index=index):
        return None
    mapping = client.indices.get_mapping(index=index)
    return (
        mapping.get(index, {})
        .get("mappings", {})
        .get("properties", {})
        .get("location", {})
        .get("type")
    )


def migrate(dry_run: bool) -> int:
    client = get_client()

    if not client.indices.exists(index=INDEX):
        log.error("Índice %s não existe. Rode bot_geoquimica --index.", INDEX)
        return 1

    src_count = client.count(index=INDEX)["count"]
    loc_type = _location_type(client, INDEX)
    log.info("Estado: %s docs | location.type=%s", src_count, loc_type)

    if loc_type == "geo_point":
        log.info("Mapping OK — nada a fazer.")
        return 0

    if dry_run:
        log.info(
            "DRY-RUN: reindex %s (%d docs) → %s → recriar %s → reindex → apagar staging",
            INDEX,
            src_count,
            STAGING,
            INDEX,
        )
        return 0

    if client.indices.exists(index=STAGING):
        client.indices.delete(index=STAGING)

    body = ALL_INDICES[INDEX]["body"]
    create_index(client, STAGING, body, recreate=False)
    log.info("Reindex %s → %s ...", INDEX, STAGING)
    r1 = client.reindex(
        body={"source": {"index": INDEX}, "dest": {"index": STAGING}},
        wait_for_completion=True,
        refresh=True,
    )
    staging_count = client.count(index=STAGING)["count"]
    log.info("Staging: %d docs (created=%s)", staging_count, r1.get("created"))

    if staging_count < src_count * 0.99:
        log.error("Staging incompleto (%d < %d). Abortando.", staging_count, src_count)
        return 1

    client.indices.delete(index=INDEX)
    log.info("Índice antigo %s removido.", INDEX)

    create_index(client, INDEX, body, recreate=False)
    log.info("Reindex %s → %s ...", STAGING, INDEX)
    r2 = client.reindex(
        body={"source": {"index": STAGING}, "dest": {"index": INDEX}},
        wait_for_completion=True,
        refresh=True,
    )
    final_count = client.count(index=INDEX)["count"]
    client.indices.delete(index=STAGING)

    new_type = _location_type(client, INDEX)
    log.info(
        "Migração OK: %s = %d docs | location.type=%s | created=%s",
        INDEX,
        final_count,
        new_type,
        r2.get("created"),
    )
    return 0 if new_type == "geo_point" else 1


def main() -> None:
    p = argparse.ArgumentParser(description="Corrige mapping geoquímica sem re-ingest OGC")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    raise SystemExit(migrate(args.dry_run))


if __name__ == "__main__":
    main()
