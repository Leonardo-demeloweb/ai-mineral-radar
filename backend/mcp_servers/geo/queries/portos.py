"""
Portos — OpenSearch (mr_portos_v001)
====================================

Catálogo indexado por ``ingest_portos`` (polígono + acesso rodoviário).
Usado por ``buscar_porto``, ``porto_por_coordenada``, ``obter_poligono_porto``
e pelo guardrail de rotas (``resolve_endereco_via_portos_index``).
"""

from __future__ import annotations

import logging
from typing import Any

from opensearchpy.exceptions import NotFoundError

from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.geo.services.portos_registry import iter_resolve_trials_from_endereco

logger = logging.getLogger("mcp.geo.queries.portos")

INDEX_PORTOS = "mr_portos_v001"

_SOURCE_BUSCA = [
    "codigo",
    "nome",
    "uf",
    "municipio",
    "tipo",
    "esfera",
    "acesso_rodoviario",
    "centroide",
    "aliases",
    "cargas_principais",
]


def _acesso_lat_lon(src: dict[str, Any]) -> tuple[float, float] | None:
    acc = src.get("acesso_rodoviario") or {}
    cen = src.get("centroide") or {}
    lat = acc.get("lat")
    lon = acc.get("lon")
    if lat is None or lon is None:
        lat = cen.get("lat")
        lon = cen.get("lon")
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _format_porto_list_item(src: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    ll = _acesso_lat_lon(src)
    out: dict[str, Any] = {
        "codigo": src.get("codigo", ""),
        "nome": src.get("nome", ""),
        "uf": src.get("uf", ""),
        "municipio": src.get("municipio", ""),
        "tipo": src.get("tipo"),
        "esfera": src.get("esfera"),
        "aliases": src.get("aliases"),
        "cargas_principais": src.get("cargas_principais") or [],
    }
    if ll:
        out["centro"] = {"lat": ll[0], "lon": ll[1]}
    if score is not None:
        out["_score"] = round(score, 4)
    return out


def _source_to_resolve_hit(src: dict[str, Any], endereco_consultado: str) -> dict[str, Any] | None:
    """Mesmo contrato que ``resolve_endereco_if_public_port``."""
    ll = _acesso_lat_lon(src)
    if not ll:
        return None
    lat, lon = ll
    nome = str(src.get("nome", ""))
    mun = str(src.get("municipio", ""))
    uf = str(src.get("uf", ""))
    cargas = src.get("cargas_principais") or []
    detalhes: dict[str, Any] = {
        "municipio": f"{mun}/{uf}".strip("/"),
    }
    if src.get("tipo"):
        detalhes["tipo"] = src["tipo"]
    if cargas:
        detalhes["substancia"] = ", ".join(str(c) for c in cargas[:3])
    return {
        "lat": lat,
        "lon": lon,
        "endereco_consultado": endereco_consultado,
        "endereco_resolvido": f"{nome} — {mun}/{uf}",
        "fonte": "mr_portos_v001",
        "detalhes": detalhes,
    }


async def _get_by_codigo(os_service: OpenSearchService, codigo: str) -> dict[str, Any] | None:
    cid = codigo.strip().upper()
    if not cid or len(cid) > 8:
        return None
    try:
        r = await os_service.get(INDEX_PORTOS, doc_id=cid)
    except NotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("portos get %s: %s", cid, exc)
        return None
    if not r.get("found"):
        return None
    return r.get("_source") or {}


async def _search_one_porto(
    os_service: OpenSearchService,
    termo: str,
    uf: str | None,
) -> dict[str, Any] | None:
    t = termo.strip()
    if not t:
        return None

    cod_candidate = t.replace(" ", "").upper()
    if 2 <= len(cod_candidate) <= 6 and cod_candidate.isalnum():
        got = await _get_by_codigo(os_service, cod_candidate)
        if got:
            return got

    must: list[dict[str, Any]] = [
        {
            "multi_match": {
                "query": t,
                "fields": ["nome^2", "aliases", "municipio", "codigo"],
                "type": "best_fields",
                "fuzziness": "AUTO",
            }
        }
    ]
    flt: list[dict[str, Any]] = []
    if uf:
        flt.append({"term": {"uf": uf.strip().upper()}})

    body: dict[str, Any] = {
        "size": 1,
        "_source": _SOURCE_BUSCA,
        "query": {"bool": {"must": must, "filter": flt}},
        "min_score": 0.01,
    }
    try:
        resp = await os_service.search(INDEX_PORTOS, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("portos search failed: %s", exc)
        return None

    hits = resp.get("hits", {}).get("hits", [])
    if not hits:
        return None
    return hits[0].get("_source") or {}


async def resolve_endereco_via_portos_index(
    os_service: OpenSearchService | None,
    endereco: str,
) -> dict[str, Any] | None:
    """
    Resolve texto livre contra ``mr_portos_v001`` (L1 para rotas).

    Retorna o mesmo dict que ``resolve_endereco_if_public_port`` ou None.
    """
    if os_service is None or not getattr(os_service, "client", None):
        return None

    trials = iter_resolve_trials_from_endereco(endereco)
    if not trials:
        return None

    for term, uf in trials:
        try:
            src = await _search_one_porto(os_service, term, uf)
        except Exception as exc:  # noqa: BLE001
            logger.debug("porto trial %r: %s", term, exc)
            continue
        if not src:
            continue
        hit = _source_to_resolve_hit(src, endereco)
        if hit:
            return hit
    return None


async def executar_buscar_porto(
    os_service: OpenSearchService,
    *,
    termo: str | None = None,
    codigo: str | None = None,
    uf: str | None = None,
    limite: int = 10,
) -> dict[str, Any]:
    """Busca textual / código no índice de portos."""
    limite = max(1, min(limite, 50))

    if codigo:
        src = await _get_by_codigo(os_service, codigo)
        if not src:
            return {"total": 0, "portos": []}
        return {"total": 1, "portos": [_format_porto_list_item(src, None)]}

    if not termo or not str(termo).strip():
        return {"total": 0, "portos": [], "mensagem": "Informe termo ou codigo."}

    t = str(termo).strip()
    must: list[dict[str, Any]] = [
        {
            "multi_match": {
                "query": t,
                "fields": ["nome^2", "aliases", "municipio", "codigo"],
                "type": "best_fields",
                "fuzziness": "AUTO",
            }
        }
    ]
    flt: list[dict[str, Any]] = []
    if uf:
        flt.append({"term": {"uf": uf.strip().upper()}})

    body: dict[str, Any] = {
        "size": limite,
        "_source": _SOURCE_BUSCA,
        "query": {"bool": {"must": must, "filter": flt}},
        "min_score": 0.01,
    }
    resp = await os_service.search(INDEX_PORTOS, body)
    hits = resp.get("hits", {}).get("hits", [])
    portos = [_format_porto_list_item(h.get("_source", {}), h.get("_score")) for h in hits]
    return {"total": len(portos), "portos": portos}


async def executar_porto_por_coordenada(
    os_service: OpenSearchService,
    *,
    latitude: float,
    longitude: float,
    incluir_poligono: bool = False,
) -> dict[str, Any]:
    """Point-in-polygon sobre ``poligono`` (quando existir no documento)."""
    body: dict[str, Any] = {
        "size": 5,
        "_source": (
            [*_SOURCE_BUSCA, "poligono", "area_km2"]
            if incluir_poligono
            else _SOURCE_BUSCA
        ),
        "query": {
            "bool": {
                "must": [
                    {"exists": {"field": "poligono"}},
                    {
                        "geo_shape": {
                            "field": "poligono",
                            "relation": "contains",
                            "shape": {"type": "point", "coordinates": [longitude, latitude]},
                        }
                    },
                ]
            }
        },
    }
    resp = await os_service.search(INDEX_PORTOS, body)
    hits = resp.get("hits", {}).get("hits", [])
    if not hits:
        return {"encontrado": False, "porto": None}

    h0 = hits[0]
    src = h0.get("_source", {})
    out: dict[str, Any] = {
        "encontrado": True,
        "porto": _format_porto_list_item(src, h0.get("_score")),
    }
    if incluir_poligono and src.get("poligono"):
        out["feature"] = {
            "type": "Feature",
            "geometry": src["poligono"],
            "properties": {
                "camada": "porto",
                "codigo": src.get("codigo"),
                "nome": src.get("nome"),
                "uf": src.get("uf"),
            },
        }
    return out


async def executar_obter_poligono_porto(
    os_service: OpenSearchService,
    *,
    codigo: str | None = None,
    nome: str | None = None,
    uf: str | None = None,
    incluir_geometria: bool = False,
) -> dict[str, Any]:
    """
    Confirma porto com polígono indexado. Por padrão só metadados (sem GeoJSON);
    geometria no mapa via GET ``/api/v1/geo/porto/poligono`` (sob demanda).
    """
    src: dict[str, Any] | None = None
    if codigo:
        src = await _get_by_codigo(os_service, codigo)
    elif nome:
        src = await _search_one_porto(os_service, nome, uf)
    else:
        return {"sucesso": False, "mensagem": "Informe codigo ou nome."}

    if not src or not src.get("poligono"):
        return {
            "sucesso": True,
            "encontrado": False,
            "mensagem": "Porto sem polígono no índice ou não encontrado.",
        }

    loc = src.get("acesso_rodoviario") or src.get("centroide") or {}
    out: dict[str, Any] = {
        "sucesso": True,
        "encontrado": True,
        "codigo": src.get("codigo"),
        "nome": src.get("nome"),
        "uf": src.get("uf"),
        "municipio": src.get("municipio"),
    }
    if loc.get("lat") is not None and loc.get("lon") is not None:
        out["centro"] = {"lat": loc.get("lat"), "lon": loc.get("lon")}

    if incluir_geometria:
        out["feature"] = {
            "type": "Feature",
            "geometry": src["poligono"],
            "properties": {
                "camada": "porto",
                "codigo": src.get("codigo"),
                "nome": src.get("nome"),
                "uf": src.get("uf"),
                "municipio": src.get("municipio"),
                "centro": out.get("centro"),
            },
        }

    return out
