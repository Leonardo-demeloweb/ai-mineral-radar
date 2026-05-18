"""
migrate_autuacoes_index.py — Corrige índice legado mr_autoacoes_v001 → mr_autuacoes_v001
==========================================================================================

O ETL antigo (bot_autoacoes.py) gravou no nome com typo. O MCP usa o canônico
``mr_autuacoes_v001``. Em alguns clusters existe apenas um alias apontando para o
índice físico errado — este script:

  1. Remove o alias ``mr_autuacoes_v001`` (se existir)
  2. Cria ``mr_autuacoes_v001`` com mapeamento de ``setup_indices.py``
  3. Reindexa de ``mr_autoacoes_v001`` → ``mr_autuacoes_v001``
  4. Apaga ``mr_autoacoes_v001``

Uso (a partir de ``backend/``, com ``.venv`` e ``.env``)::

    python scripts/migrate_autuacoes_index.py
    python scripts/migrate_autuacoes_index.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts.setup_indices import ALL_INDICES, get_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("migrate_autuacoes")

LEGACY = "mr_autoacoes_v001"
CANONICAL = "mr_autuacoes_v001"


def _resolve(client, name: str) -> tuple[str | None, bool]:
    """
    Retorna (índice_físico, is_alias).
    ``exists`` em alias retorna True; ``get_alias`` revela o backing index.
    """
    if not client.indices.exists(index=name):
        return None, False
    try:
        info = client.indices.get_alias(index=name)
    except Exception:
        info = {}
    if name in info:
        return name, False
    for idx, meta in info.items():
        if name in meta.get("aliases", {}):
            return idx, True
    return name, False


def migrate(dry_run: bool) -> int:
    client = get_client()

    legacy_phys, _ = _resolve(client, LEGACY)
    canon_phys, canon_is_alias = _resolve(client, CANONICAL)

    log.info(
        "Estado inicial: legacy_phys=%s canonical=%s (alias=%s)",
        legacy_phys,
        canon_phys,
        canon_is_alias,
    )

    if canon_phys == CANONICAL and not canon_is_alias:
        count = client.count(index=CANONICAL)["count"]
        if legacy_phys is None:
            log.info("Nada a fazer — %s já é índice físico com %d docs.", CANONICAL, count)
            return 0

    if legacy_phys is None and canon_phys is None:
        log.error("Nenhum índice de autuações encontrado. Rode: python -m bots.bot_autuacoes --all")
        return 1

    source = legacy_phys or (canon_phys if canon_is_alias else None) or LEGACY
    if source == CANONICAL and not canon_is_alias:
        log.info("Fonte já é o índice canônico.")
        return 0

    if dry_run:
        src_count = client.count(index=source)["count"]
        log.info(
            "DRY-RUN: remover alias → criar %s → reindex %s (%d docs) → delete %s",
            CANONICAL,
            source,
            src_count,
            source if source != CANONICAL else LEGACY,
        )
        return 0

    # 1) Remove alias canônico no índice legado
    if client.indices.exists(index=CANONICAL):
        alias_info = client.indices.get_alias(index="*", ignore_unavailable=True)
        actions = []
        for idx, meta in alias_info.items():
            if CANONICAL in meta.get("aliases", {}):
                actions.append({"remove": {"index": idx, "alias": CANONICAL}})
        if actions:
            client.indices.update_aliases(body={"actions": actions})
            log.info("Alias %s removido de %d índice(s).", CANONICAL, len(actions))

    # 2) Cria índice canônico se ainda não existir como físico
    if not client.indices.exists(index=CANONICAL):
        body = ALL_INDICES[CANONICAL]["body"]
        client.indices.create(index=CANONICAL, body=body)
        log.info("Índice %s criado.", CANONICAL)
    elif _resolve(client, CANONICAL)[0] != CANONICAL:
        log.error("Nome %s ainda não é índice físico. Abortando.", CANONICAL)
        return 1

    # 3) Reindex
    src_count = client.count(index=source)["count"]
    log.info("Reindex %s → %s (%d docs)...", source, CANONICAL, src_count)
    resp = client.reindex(
        body={"source": {"index": source}, "dest": {"index": CANONICAL}},
        wait_for_completion=True,
        refresh=True,
    )
    created = resp.get("created", resp.get("total", 0))
    log.info("Reindex concluído: created=%s failures=%s", created, resp.get("failures", []))

    dst_count = client.count(index=CANONICAL)["count"]
    if dst_count < src_count * 0.99:
        log.error("Contagem destino (%d) < origem (%d). Não apagando legado.", dst_count, src_count)
        return 1

    # 4) Apaga legado (só o índice físico com typo)
    to_delete = source if source != CANONICAL else LEGACY
    if to_delete != CANONICAL and client.indices.exists(index=to_delete):
        client.indices.delete(index=to_delete)
        log.info("Índice legado %s removido.", to_delete)

    log.info("Migração OK: %s = %d docs", CANONICAL, dst_count)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Migra mr_autoacoes_v001 → mr_autuacoes_v001")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    raise SystemExit(migrate(args.dry_run))


if __name__ == "__main__":
    main()
