"""
Ferrovias — OpenSearch (mr_ferrovias_v001)
=========================================

Trechos lineares da malha ferroviária federal (ANTT / ingest_ferrovias).
Usado por ``buscar_ferrovia``, ``ferrovias_proximas`` e ``obter_geometria_ferrovia``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from opensearchpy.exceptions import NotFoundError

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.geo.queries.ferrovias")

INDEX_FERROVIAS = "mr_ferrovias_v001"

# LLMs frequentemente inventam IDs tipo "fca_017" (sigla + OBJECTID do shapefile).
# O índice usa ``ferrovia_id`` = ``antt-{ano}-{layer}-{fid}`` — este padrão resolve
# para ``codigo_sigla`` + ``shapefile_fid`` quando o GET direto falha.
_LEGACY_SIGLA_SHAPEFID = re.compile(r"^([A-Za-z]{1,12})[_-](\d+)$")

_SOURCE_LITE = [
    "ferrovia_id",
    "codigo_sigla",
    "nome",
    "nome_normalizado",
    "uf",
    "operadora",
    "extensao_km",
    "tipo_malha",
    "centroide",
    "ano_referencia",
    "shapefile_layer",
    "fonte",
]


def _format_centroide(src: dict[str, Any]) -> dict[str, float] | None:
    c = src.get("centroide") or {}
    lat, lon = c.get("lat"), c.get("lon")
    if lat is None or lon is None:
        return None
    try:
        return {"lat": float(lat), "lon": float(lon)}
    except (TypeError, ValueError):
        return None


def _format_item(src: dict[str, Any], distancia_km: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ferrovia_id": src.get("ferrovia_id"),
        "codigo_sigla": src.get("codigo_sigla"),
        "nome": src.get("nome"),
        "uf": src.get("uf"),
        "operadora": src.get("operadora"),
        "extensao_km": src.get("extensao_km"),
        "tipo_malha": src.get("tipo_malha"),
        "ano_referencia": src.get("ano_referencia"),
        "shapefile_layer": src.get("shapefile_layer"),
        "fonte": src.get("fonte"),
        "centroide": _format_centroide(src),
    }
    if distancia_km is not None:
        out["distancia_km"] = round(distancia_km, 3)
    return {k: v for k, v in out.items() if v is not None}


async def executar_buscar_ferrovia(
    os_service: OpenSearchService,
    *,
    termo: str | None = None,
    codigo_sigla: str | None = None,
    uf: str | None = None,
    limite: int = 15,
) -> dict[str, Any]:
    """Busca textual ou por sigla/código no índice de ferrovias."""
    limite = max(1, min(int(limite), 50))

    flt: list[dict[str, Any]] = []
    if uf:
        flt.append({"term": {"uf": uf.strip().upper()}})

    if codigo_sigla and str(codigo_sigla).strip():
        cs = str(codigo_sigla).strip().lower()
        body: dict[str, Any] = {
            "size": limite,
            "_source": _SOURCE_LITE,
            "query": {
                "bool": {
                    "must": [
                        {
                            "bool": {
                                "should": [
                                    {"term": {"codigo_sigla": cs}},
                                    {"wildcard": {"codigo_sigla": f"{cs}*"}},
                                ],
                                "minimum_should_match": 1,
                            }
                        }
                    ],
                    "filter": flt,
                }
            },
        }
    elif termo and str(termo).strip():
        t = str(termo).strip()
        body = {
            "size": limite,
            "_source": _SOURCE_LITE,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": t,
                                "fields": [
                                    "nome^2",
                                    "nome.keyword",
                                    "codigo_sigla",
                                    "nome_normalizado",
                                ],
                                "type": "best_fields",
                                "fuzziness": "AUTO",
                            }
                        }
                    ],
                    "filter": flt,
                }
            },
            "min_score": 0.01,
        }
    else:
        return {"total": 0, "ferrovias": [], "mensagem": "Informe termo ou codigo_sigla."}

    try:
        meta = await os_service.search_with_meta(INDEX_FERROVIAS, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("buscar_ferrovia search failed: %s", exc)
        raise

    items: list[dict[str, Any]] = []
    for h in meta.get("hits", []):
        src = h.get("_source") or {}
        items.append(_format_item(src))

    return {
        "total": meta.get("total", 0),
        "retornados": len(items),
        "ferrovias": items,
    }


def _build_proximas_query(
    latitude: float,
    longitude: float,
    raio_km: float,
    uf: str | None,
    limite: int,
) -> dict[str, Any]:
    limite = max(1, min(int(limite), 50))
    raio_km = max(0.5, min(float(raio_km), 500.0))

    filters: list[dict[str, Any]] = [
        {
            "geo_distance": {
                "distance": f"{raio_km}km",
                "centroide": {"lat": latitude, "lon": longitude},
            }
        }
    ]
    if uf:
        filters.append({"term": {"uf": uf.strip().upper()}})

    return {
        "size": limite,
        "_source": _SOURCE_LITE,
        "query": {"bool": {"filter": filters}},
        "sort": [
            {
                "_geo_distance": {
                    "centroide": {"lat": latitude, "lon": longitude},
                    "order": "asc",
                    "unit": "km",
                }
            }
        ],
    }


async def executar_ferrovias_proximas(
    os_service: OpenSearchService,
    *,
    latitude: float,
    longitude: float,
    raio_km: float = 50.0,
    uf: str | None = None,
    limite: int = 20,
) -> dict[str, Any]:
    """
    Trechos de malha cujo centróide está dentro do raio (aproximação para
    \"ferrovia mais próxima\" — a linha real pode estar lateral ao ponto).
    """
    body = _build_proximas_query(latitude, longitude, raio_km, uf, limite)
    try:
        meta = await os_service.search_with_meta(INDEX_FERROVIAS, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ferrovias_proximas failed: %s", exc)
        raise

    items: list[dict[str, Any]] = []
    for h in meta.get("hits", []):
        src = h.get("_source") or {}
        dist: float | None = None
        srt = h.get("sort") or []
        if srt and isinstance(srt[0], (int, float)):
            dist = float(srt[0])
        items.append(_format_item(src, distancia_km=dist))

    return {
        "total": meta.get("total", 0),
        "retornados": len(items),
        "raio_km": raio_km,
        "ferrovias": items,
    }


def _ferrovia_feature_from_source(src: dict[str, Any]) -> dict[str, Any] | None:
    geom = src.get("geom")
    if not isinstance(geom, dict) or "coordinates" not in geom:
        return None
    props = {
        "ferrovia_id": src.get("ferrovia_id"),
        "codigo_sigla": src.get("codigo_sigla"),
        "nome": src.get("nome"),
        "uf": src.get("uf"),
        "extensao_km": src.get("extensao_km"),
        "fonte": src.get("fonte"),
    }
    props = {k: v for k, v in props.items() if v is not None}
    return {
        "type": "Feature",
        "properties": props,
        "geometry": geom,
    }


async def _buscar_source_por_codigo_e_shapefile_fid(
    os_service: OpenSearchService,
    codigo_sigla: str,
    shapefile_fid: int,
) -> dict[str, Any] | None:
    body = {
        "size": 8,
        "query": {
            "bool": {
                "must": [
                    {"term": {"codigo_sigla": codigo_sigla.strip().lower()}},
                    {"term": {"shapefile_fid": int(shapefile_fid)}},
                ]
            }
        },
    }
    try:
        meta = await os_service.search_with_meta(INDEX_FERROVIAS, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ferrovia lookup codigo+fid failed: %s", exc)
        return None
    hits = meta.get("hits") or []
    if not hits:
        return None
    if len(hits) > 1:
        logger.info(
            "ferrovia codigo+fid %s+%s: %d hits — usando o primeiro",
            codigo_sigla, shapefile_fid, len(hits),
        )
    return hits[0].get("_source") or {}


async def _resolve_ferrovia_source(
    os_service: OpenSearchService,
    *,
    ferrovia_id: str,
    latitude: float | None = None,
    longitude: float | None = None,
) -> tuple[dict[str, Any] | None, str, str | None, dict[str, Any] | None]:
    """
    Resolve documento no índice. Retorna (source, resolved_id, resolucao, erro_dict).
    """
    fid = (ferrovia_id or "").strip()
    if not fid and (latitude is None or longitude is None):
        return None, "", None, {
            "sucesso": False,
            "mensagem": "Informe ferrovia_id ou latitude+longitude.",
        }

    resolucao: str | None = None
    src: dict[str, Any] | None = None
    resolved_id = fid

    if fid:
        doc: dict[str, Any] | None = None
        try:
            doc = await os_service.get(INDEX_FERROVIAS, doc_id=fid)
        except NotFoundError:
            doc = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("obter_geometria_ferrovia get %s: %s", fid, exc)
            raise

        if isinstance(doc, dict) and doc.get("found"):
            src = doc.get("_source") or {}
            resolucao = "get_por_ferrovia_id"

    if not src and fid:
        m = _LEGACY_SIGLA_SHAPEFID.match(fid)
        if m:
            code, num_s = m.group(1), m.group(2)
            try:
                sfid = int(num_s, 10)
            except ValueError:
                sfid = -1
            if sfid >= 0:
                src = await _buscar_source_por_codigo_e_shapefile_fid(
                    os_service, code, sfid,
                )
                if src:
                    resolved_id = str(src.get("ferrovia_id") or fid)
                    resolucao = "lookup_codigo_sigla_shapefile_fid"

    if (
        not src
        and latitude is not None
        and longitude is not None
        and -90 <= float(latitude) <= 90
        and -180 <= float(longitude) <= 180
    ):
        prox = await executar_ferrovias_proximas(
            os_service,
            latitude=float(latitude),
            longitude=float(longitude),
            raio_km=80.0,
            uf=None,
            limite=5,
        )
        fer_list = prox.get("ferrovias") or []
        for row in fer_list:
            rid = row.get("ferrovia_id")
            if not rid:
                continue
            try:
                doc2 = await os_service.get(INDEX_FERROVIAS, doc_id=str(rid))
            except NotFoundError:
                continue
            if isinstance(doc2, dict) and doc2.get("found"):
                src = doc2.get("_source") or {}
                resolved_id = str(rid)
                resolucao = "trecho_mais_proximo_por_coordenadas"
                break

    if not src:
        return None, fid, resolucao, {
            "sucesso": False,
            "mensagem": (
                f"Trecho '{fid or '(sem id)'}' não encontrado em {INDEX_FERROVIAS}. "
                "Use o campo ``ferrovia_id`` exato retornado por ``ferrovias_proximas`` "
                "ou ``buscar_ferrovia`` (formato antt-...). Opcionalmente passe "
                "latitude e longitude do processo para obter o trecho mais próximo."
            ),
        }

    return src, resolved_id, resolucao, None


def _ferrovia_metadata_from_source(src: dict[str, Any], resolved_id: str) -> dict[str, Any]:
    return {
        k: v
        for k, v in {
            "ferrovia_id": resolved_id,
            "codigo_sigla": src.get("codigo_sigla"),
            "nome": src.get("nome"),
            "uf": src.get("uf"),
            "extensao_km": src.get("extensao_km"),
            "operadora": src.get("operadora"),
            "tipo_malha": src.get("tipo_malha"),
        }.items()
        if v is not None
    }


async def executar_obter_geometria_ferrovia(
    os_service: OpenSearchService,
    *,
    ferrovia_id: str,
    latitude: float | None = None,
    longitude: float | None = None,
    incluir_geometria: bool = False,
) -> dict[str, Any]:
    """
    Confirma/resolver trecho ferroviário. Por padrão devolve só metadados (sem GeoJSON);
    a geometria para o mapa é GET ``/api/v1/geo/ferrovia/geometria`` (sob demanda).

    Com ``incluir_geometria=True`` (uso interno / REST) inclui ``feature`` GeoJSON.
    """
    src, resolved_id, resolucao, err = await _resolve_ferrovia_source(
        os_service,
        ferrovia_id=ferrovia_id,
        latitude=latitude,
        longitude=longitude,
    )
    if err is not None:
        return err

    meta = _ferrovia_metadata_from_source(src or {}, resolved_id)
    out: dict[str, Any] = {"sucesso": True, **meta}
    if resolucao:
        out["resolucao"] = resolucao
    fid = (ferrovia_id or "").strip()
    if fid and resolved_id != fid:
        out["ferrovia_id_consultado"] = fid
    if latitude is not None and longitude is not None:
        out["latitude"] = float(latitude)
        out["longitude"] = float(longitude)

    if incluir_geometria:
        feature = _ferrovia_feature_from_source(src or {})
        if feature is None:
            return {"sucesso": False, "mensagem": "Documento sem geometria indexada."}
        out["feature"] = feature

    return out
