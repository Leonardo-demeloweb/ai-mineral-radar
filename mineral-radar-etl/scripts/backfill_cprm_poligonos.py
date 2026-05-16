"""
Backfill ``poligono`` + ``poligono_fonte`` em ``mr_cprm_v001`` para documentos
com ``location`` válido e sem ``poligono`` (ex.: indexados antes do buffer no bot).

Usa a mesma função de buffer que ``bots.bot_cprm`` (EPSG:5880, 75 m).

Execução (a partir do diretório ``mineral-radar-etl/``):

    python scripts/backfill_cprm_poligonos.py --dry-run
    python scripts/backfill_cprm_poligonos.py
    python scripts/backfill_cprm_poligonos.py --limit 1000

Requer o mesmo ambiente que os bots (``shapely``, ``pyproj``, ``opensearch-py``).
Use o venv do ``mineral-radar-etl`` (``pip install -r requirements.txt``) ou instale
``shapely`` e ``pyproj`` no interpretador que estiver a usar — o venv do *backend*
costuma não incluir estas libs.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Iterator

import click
from dotenv import load_dotenv
from opensearchpy import OpenSearch, helpers

# Garante imports ``bots.*`` ao correr ``python scripts/...py``
_ETL_ROOT = Path(__file__).resolve().parents[1]
if str(_ETL_ROOT) not in sys.path:
    sys.path.insert(0, str(_ETL_ROOT))

for _env in (_ETL_ROOT / ".env", _ETL_ROOT.parent / ".env"):
    if _env.exists():
        load_dotenv(_env, override=False)
        break

try:
    import shapely  # noqa: F401
    import pyproj  # noqa: F401
except ImportError:
    print(
        "Erro: faltam shapely e pyproj (buffer EPSG:5880, igual ao bot_cprm).\n\n"
        "  Opção A — venv do ETL:\n"
        "    cd mineral-radar-etl && python3 -m venv .venv && source .venv/bin/activate\n"
        "    pip install -r requirements.txt\n\n"
        "  Opção B — no venv atual:\n"
        "    pip install 'shapely>=2' 'pyproj>=3.6'\n",
        file=sys.stderr,
    )
    raise SystemExit(1)

from bots.bot_cprm import (  # noqa: E402
    BUFFER_PONTO_METROS,
    INDEX_CPRM,
    _buffer_point_geojson,
    get_os_client,
)
from bots.common.logging import get_logger  # noqa: E402

log = get_logger(__name__)

SCROLL = "3m"
SCROLL_SIZE = 500
DEFAULT_BATCH = 200


def _location_lonlat(source: dict[str, Any]) -> tuple[float, float] | None:
    """
    Extrai (lon, lat) a partir de ``location`` (geo_point no OpenSearch).

    Formatos suportados (como devolvidos em ``_source``):
    - objeto ``{"lat": …, "lon": …}`` (o que o ``bot_cprm`` grava);
    - array ``[lon, lat]`` (forma comum de ``geo_point`` em JSON);
    - string ``"lat,lon"`` (convenção Elasticsearch/OpenSearch para geo_point);
    - GeoJSON ``{"type": "Point", "coordinates": [lon, lat]}``.
    """
    loc = source.get("location")
    if loc is None:
        return None

    # Array [lon, lat] — típico em geo_point ao serializar _source
    if isinstance(loc, (list, tuple)) and len(loc) >= 2:
        try:
            lon, lat = float(loc[0]), float(loc[1])
        except (TypeError, ValueError):
            return None
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            return lon, lat
        return None

    # String "latitude,longitude" (documentação ES/OS para geo_point)
    if isinstance(loc, str):
        parts = [p.strip() for p in loc.split(",") if p.strip()]
        if len(parts) < 2:
            return None
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except (TypeError, ValueError):
            return None
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            return lon, lat
        return None

    if not isinstance(loc, dict):
        return None

    if "lat" in loc and "lon" in loc:
        try:
            lon, lat = float(loc["lon"]), float(loc["lat"])
        except (TypeError, ValueError):
            return None
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            return lon, lat
        return None

    if (loc.get("type") or "").strip() == "Point":
        coords = loc.get("coordinates") or []
        if len(coords) < 2:
            return None
        try:
            lon, lat = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            return None
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            return lon, lat

    return None


def _missing_poligono_query() -> dict[str, Any]:
    return {
        "bool": {
            "filter": [{"exists": {"field": "location"}}],
            "must_not": [{"exists": {"field": "poligono"}}],
        }
    }


def _count_missing(client: OpenSearch, index: str) -> int:
    body = {"query": _missing_poligono_query()}
    r = client.count(index=index, body=body)
    return int(r.get("count", 0))


def _iter_scroll_hits(client: OpenSearch, index: str) -> Iterator[dict[str, Any]]:
    body = {
        "size": SCROLL_SIZE,
        "_source": ["location", "id_ocorrencia", "nome"],
        "query": _missing_poligono_query(),
    }
    resp = client.search(index=index, body=body, scroll=SCROLL)
    scroll_id = resp["_scroll_id"]
    try:
        while True:
            hits = resp["hits"]["hits"]
            if not hits:
                break
            for h in hits:
                yield h
            resp = client.scroll(scroll_id=scroll_id, scroll=SCROLL)
            scroll_id = resp["_scroll_id"]
    finally:
        try:
            client.clear_scroll(scroll_id=scroll_id)
        except Exception as e:
            log.warning("backfill.clear_scroll", error=str(e))


def _update_actions(
    hits: list[dict[str, Any]],
    index: str,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Constrói ações bulk ``update``; devolve (ações, n_actions, n_skipped).
    """
    actions: list[dict[str, Any]] = []
    skipped = 0

    for hit in hits:
        src = hit.get("_source") or {}
        ll = _location_lonlat(src)
        if ll is None:
            skipped += 1
            log.warning(
                "backfill.skip_no_location",
                _id=hit.get("_id"),
                id_ocorrencia=src.get("id_ocorrencia"),
            )
            continue
        lon, lat = ll
        try:
            poly = _buffer_point_geojson(lon, lat, BUFFER_PONTO_METROS)
        except Exception as e:
            skipped += 1
            log.warning(
                "backfill.skip_buffer_failed",
                _id=hit.get("_id"),
                error=str(e)[:200],
            )
            continue

        actions.append({
            "_op_type": "update",
            "_index": index,
            "_id": hit["_id"],
            "doc": {
                "poligono": poly,
                "poligono_fonte": "buffer_ponto_75m",
            },
        })

    return actions, len(actions), skipped


@click.command()
@click.option("--index", "index_name", default=INDEX_CPRM, show_default=True,
              help="Índice OpenSearch CPRM.")
@click.option("--batch-size", default=DEFAULT_BATCH, show_default=True,
              help="Documentos por bulk update.")
@click.option("--limit", default=0, show_default=True,
              help="Máximo de documentos a atualizar (0 = sem limite).")
@click.option("--dry-run", is_flag=True,
              help="Apenas conta e simula; não grava no OpenSearch.")
def main(index_name: str, batch_size: int, limit: int, dry_run: bool) -> None:
    """Preenche ``poligono`` onde há ``location`` e falta geometria indexada."""
    t0 = time.time()
    client = get_os_client()

    n_missing = _count_missing(client, index_name)
    log.info(
        "backfill.cprm.start",
        index=index_name,
        missing_poligono=n_missing,
        dry_run=dry_run,
        limit=limit or None,
    )

    if n_missing == 0:
        log.info("backfill.cprm.nothing_to_do")
        return

    if dry_run:
        log.info("backfill.cprm.dry_run_done", would_process=min(n_missing, limit or n_missing))
        return

    processed = 0
    total_ok = 0
    total_err = 0
    total_skip = 0
    batch: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal batch, processed, total_ok, total_err, total_skip
        if not batch:
            return
        actions, n_actions, n_skip = _update_actions(batch, index_name)
        total_skip += n_skip
        if n_actions:
            ok, errs = helpers.bulk(client, actions, raise_on_error=False)
            total_ok += ok
            if isinstance(errs, list) and errs:
                total_err += len(errs)
                for e in errs[:3]:
                    log.error("backfill.bulk_error", error=str(e)[:300])
            elif errs and not isinstance(errs, list):
                total_err += int(errs) if errs else 0
        processed += len(batch)
        log.info(
            "backfill.cprm.progress",
            processed=processed,
            bulk_ok=total_ok,
            bulk_errors=total_err,
            skipped_buffer=total_skip,
        )
        batch = []

    for hit in _iter_scroll_hits(client, index_name):
        if limit and processed >= limit:
            break
        batch.append(hit)
        full = len(batch) >= batch_size
        hit_limit = bool(limit) and processed + len(batch) >= limit
        if full or hit_limit:
            flush()
    if batch:
        flush()

    try:
        client.indices.refresh(index=index_name)
    except Exception as e:
        log.warning("backfill.refresh_failed", error=str(e))

    elapsed = round(time.time() - t0, 1)
    n_after = _count_missing(client, index_name)
    log.info(
        "backfill.cprm.done",
        elapsed_s=elapsed,
        updated_ok=total_ok,
        bulk_errors=total_err,
        skipped_no_location_or_buffer=total_skip,
        missing_poligono_remaining=n_after,
    )


if __name__ == "__main__":
    main()
