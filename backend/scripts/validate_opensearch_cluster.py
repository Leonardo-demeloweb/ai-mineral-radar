"""
validate_opensearch_cluster.py — Smoke test OpenSearch (mesmo cliente do MCP)
=============================================================================

Confirma ligação ao cluster, contagens por índice e probes geoquímicas nas
mesmas coordenadas usadas pela tool ``geoquimica_proxima`` (``mr_geoquimica_v001``).

Uso (a partir da pasta ``backend/``, com ``.venv`` e ``.env`` carregados)::

    source .venv/bin/activate
    python scripts/validate_opensearch_cluster.py

Saída útil quando o chat diz "zero resultados" em Carajás ou MG: distingue
índice vazio / cluster errado / credenciais de filtro demasiado restritivo.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

# Garantir imports ``mcp_servers.*`` como nos outros scripts
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.jazidas.queries.detalhes import _normalize_numero_processo, build_processo_query
from mcp_servers.jazidas.queries.geoquimica import INDEX_GEO, build_geoquimica_geo_query

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("validate_os")

# Índices críticos para CPRM geoquímica + jazidas (processo de exemplo)
INDICES = (
    "mr_geoquimica_v001",
    "mr_cprm_v001",
    "mr_jazidas_v001",
    "mr_cfem_v001",
    "mr_ferrovias_v001",
)

# Canaã dos Carajás / Serra dos Carajás (centro aproximado para 50 km)
CARAJAS_LAT = -6.52
CARAJAS_LON = -50.21

# São Sebastião do Paraíso — região típica de processos MG (ajuste se necessário)
PARAISO_LAT = -20.92
PARAISO_LON = -46.99


async def _count_query(os_service: OpenSearchService, index: str, q: dict) -> int:
    return await os_service.count(index, body={"query": q})


async def main() -> int:
    os_service = OpenSearchService()
    try:
        await os_service.connect()
    except Exception as exc:
        log.error("Falha ao ligar ao OpenSearch: %s", exc)
        log.error("Confirme OPENSEARCH_* no .env (mesmo ficheiro que o MCP usa).")
        return 1

    client = os_service.client
    if not client:
        return 1

    info = await client.info()
    cluster = info.get("cluster_name", "?")
    version = (info.get("version") or {}).get("number", "?")
    log.info("Cluster: %s | OpenSearch: %s", cluster, version)

    # Envelope geográfico real dos pontos em mr_geoquimica_v001 (útil quando
    # contagens locais = 0: o subconjunto indexado pode não cobrir PA/MG).
    try:
        if await client.indices.exists(index=INDEX_GEO):
            env_raw = await os_service.search(
                INDEX_GEO,
                {
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
            if bbox.get("bounds"):
                b = bbox["bounds"]
                log.info(
                    "mr_geoquimica_v001 envelope: top_left=%s bottom_right=%s",
                    b.get("top_left"),
                    b.get("bottom_right"),
                )
            else:
                log.warning(
                    "mr_geoquimica_v001: geo_bounds vazio (campo location ausente ou sem coords?)"
                )
    except Exception as exc:
        log.warning("mr_geoquimica_v001 geo_bounds: %s", exc)

    for idx in INDICES:
        try:
            exists = await client.indices.exists(index=idx)
        except Exception as exc:
            log.warning("%s: exists check failed: %s", idx, exc)
            continue
        if not exists:
            log.warning("%s: índice inexistente neste cluster", idx)
            continue
        try:
            n = await os_service.count(idx, body={"query": {"match_all": {}}})
        except Exception as exc:
            log.error("%s: count match_all falhou: %s", idx, exc)
            continue
        log.info("%s: %s documentos", idx, f"{n:,}".replace(",", "."))

    # --- Geoquímica: mesma query que a tool MCP ---
    for label, lat, lon, raio in (
        ("Carajás/PA (50 km)", CARAJAS_LAT, CARAJAS_LON, 50.0),
        ("Paraíso/MG (25 km)", PARAISO_LAT, PARAISO_LON, 25.0),
    ):
        body_all = build_geoquimica_geo_query(lat, lon, raio, analito=None, size=1)
        q_all = body_all["query"]
        n_all = await _count_query(os_service, INDEX_GEO, q_all)
        for sym in ("AU", "CU"):
            body = build_geoquimica_geo_query(lat, lon, raio, analito=sym, size=1)
            n_sym = await _count_query(os_service, INDEX_GEO, body["query"])
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

    # Processo exemplo (normalização igual a ``detalhes_processo``)
    proc = "830.123/2019"
    np = _normalize_numero_processo(proc)
    pq = build_processo_query(proc)
    try:
        raw = await os_service.search("mr_jazidas_v001", pq)
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
            plat = loc.get("lat")
            plon = loc.get("lon")
            if plat is not None and plon is not None:
                body_cu = build_geoquimica_geo_query(
                    float(plat), float(plon), 25.0, analito="CU", size=1
                )
                n_cu = await _count_query(os_service, INDEX_GEO, body_cu["query"])
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

    await os_service.disconnect()
    log.info("Validação concluída.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
