"""
validate_opensearch_cluster.py — Smoke test OpenSearch (cliente síncrono)
==========================================================================

Confirma ligação ao cluster, contagens por índice e probes geoquímicas nas
mesmas coordenadas usadas pela tool ``geoquimica_proxima`` (``mr_geoquimica_v001``).

Uso (venv do **backend**, não o do mineral-radar-etl)::

    cd backend
    source .venv/bin/activate
    source ../.env   # ou .env local
    python scripts/validate_opensearch_cluster.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

try:
    from opensearchpy import OpenSearch
except ImportError as exc:
    print(
        "ERRO: opensearch-py não encontrado.\n"
        "  cd backend && source .venv/bin/activate && pip install -r requirements.txt\n"
        f"  ({exc})",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

from mcp_servers.jazidas.queries.detalhes import _normalize_numero_processo, build_processo_query
from mcp_servers.jazidas.queries.geoquimica import INDEX_GEO, build_geoquimica_geo_query
from scripts.setup_indices import get_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("validate_os")

INDICES = (
    "mr_geoquimica_v001",
    "mr_cprm_v001",
    "mr_jazidas_v001",
    "mr_cfem_v001",
    "mr_ferrovias_v001",
)

CARAJAS_LAT = -6.52
CARAJAS_LON = -50.21
PARAISO_LAT = -20.92
PARAISO_LON = -46.99


def _count(client: OpenSearch, index: str, query: dict) -> int:
    return int(client.count(index=index, body={"query": query})["count"])


def main() -> int:
    try:
        client = get_client()
    except Exception as exc:
        log.error("Falha ao ligar ao OpenSearch: %s", exc)
        log.error("Confirme OPENSEARCH_* no .env (mesmo ficheiro que o MCP usa).")
        return 1

    info = client.info()
    log.info(
        "Cluster: %s | OpenSearch: %s",
        info.get("cluster_name", "?"),
        (info.get("version") or {}).get("number", "?"),
    )

    if client.indices.exists(index=INDEX_GEO):
        try:
            env_raw = client.search(
                index=INDEX_GEO,
                body={
                    "size": 0,
                    "query": {"match_all": {}},
                    "aggs": {
                        "bbox": {
                            "geo_bounds": {
                                "field": "location",
                                "wrap_longitude": True,
                            }
                        }
                    },
                },
            )
            bbox = (env_raw.get("aggregations") or {}).get("bbox") or {}
            b = bbox.get("bounds")
            if b:
                log.info(
                    "mr_geoquimica_v001 envelope: top_left=%s bottom_right=%s",
                    b.get("top_left"),
                    b.get("bottom_right"),
                )
            else:
                log.warning("mr_geoquimica_v001: geo_bounds vazio")
        except Exception as exc:
            log.warning("mr_geoquimica_v001 geo_bounds: %s", exc)

    for idx in INDICES:
        if not client.indices.exists(index=idx):
            log.warning("%s: índice inexistente neste cluster", idx)
            continue
        try:
            n = _count(client, idx, {"match_all": {}})
            log.info("%s: %s documentos", idx, f"{n:,}".replace(",", "."))
        except Exception as exc:
            log.error("%s: count falhou: %s", idx, exc)

    # Confirma que ``location`` é geo_point (índice criado sem setup_indices quebra geo)
    try:
        mapping = client.indices.get_mapping(index=INDEX_GEO)
        loc_type = (
            mapping.get(INDEX_GEO, {})
            .get("mappings", {})
            .get("properties", {})
            .get("location", {})
            .get("type")
        )
        if loc_type != "geo_point":
            log.error(
                "mr_geoquimica_v001: campo location=%s (esperado geo_point). "
                "Rode: python -m scripts.setup_indices --index %s --recreate "
                "e depois bot_geoquimica --index --recreate",
                loc_type,
                INDEX_GEO,
            )
            return 1
    except Exception as exc:
        log.warning("Não foi possível ler mapping de %s: %s", INDEX_GEO, exc)

    for label, lat, lon, raio in (
        ("Carajás/PA (50 km)", CARAJAS_LAT, CARAJAS_LON, 50.0),
        ("Paraíso/MG (25 km)", PARAISO_LAT, PARAISO_LON, 25.0),
    ):
        try:
            body_all = build_geoquimica_geo_query(lat, lon, raio, analito=None, size=1)
            n_all = _count(client, INDEX_GEO, body_all["query"])
            for sym in ("AU", "CU"):
                body = build_geoquimica_geo_query(lat, lon, raio, analito=sym, size=1)
                n_sym = _count(client, INDEX_GEO, body["query"])
                log.info(
                    "mr_geoquimica_v001 %s raio=%skm analito=%s → %s amostras",
                    label,
                    raio,
                    sym,
                    f"{n_sym:,}".replace(",", "."),
                )
            log.info(
                "mr_geoquimica_v001 %s raio=%skm (sem filtro analito) → %s amostras",
                label,
                raio,
                f"{n_all:,}".replace(",", "."),
            )
        except Exception as exc:
            log.error(
                "mr_geoquimica_v001 probe %s falhou: %s — verifique mapping geo_point em location",
                label,
                exc,
            )

    proc = "830.123/2019"
    np = _normalize_numero_processo(proc)
    try:
        raw = client.search(index="mr_jazidas_v001", body=build_processo_query(proc))
        hits = raw.get("hits", {}).get("hits", [])
        if hits:
            src = hits[0].get("_source") or {}
            loc = src.get("location") or {}
            log.info(
                "mr_jazidas_v001 processo %s (%s): encontrado | location=%s",
                proc,
                np,
                json.dumps(loc, ensure_ascii=False),
            )
            plat, plon = loc.get("lat"), loc.get("lon")
            if plat is not None and plon is not None:
                body_cu = build_geoquimica_geo_query(
                    float(plat), float(plon), 25.0, analito="CU", size=1
                )
                n_cu = _count(client, INDEX_GEO, body_cu["query"])
                log.info(
                    "Geoquímica Cu em 25 km do processo → %s amostras",
                    f"{n_cu:,}".replace(",", "."),
                )
        else:
            log.warning(
                "mr_jazidas_v001: processo %s (%s) não encontrado neste índice",
                proc,
                np,
            )
    except Exception as exc:
        log.error("mr_jazidas_v001 lookup processo: %s", exc)

    log.info("Validação concluída.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
