"""
Geo Routes
==========

REST endpoints for geocoding (forward + reverse) using Azure Maps,
plus on-demand polygon fetching for jazida mining processes and CPRM occurrences.

GET /geo/jazida/poligono
    Returns the GeoJSON FeatureCollection for a single ANM processo.
    Cached in Redis under ``poligono:jazida:{id}`` (TTL 7 days).
    On miss: fetches from OpenSearch mr_jazidas_v001, extracts shapes, stores.

GET /geo/cprm/poligono
    Returns a FeatureCollection for one CPRM occurrence (buffer polygon from index).
    Cached under ``poligono:cprm:{id_ocorrencia}``.

GET /geo/ferrovia/geometria
    LineString / MultiLineString de um trecho (mr_ferrovias_v001), sob demanda.

GET /geo/porto/poligono
    Polígono de um porto (mr_portos_v001), sob demanda.
"""

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, status

from app.core.logging import get_logger
from mcp_servers.geo.services import azure_maps

logger = get_logger(__name__)

router = APIRouter()


# ── Request / Response schemas ───────────────────────────────────────────────

class GeocodeRequest(BaseModel):
    endereco: str | None = Field(
        default=None,
        min_length=3,
        description="Endereço para geocodificação direta (forward)",
    )
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    limite: int = Field(default=5, ge=1, le=10)


class GeocodeResultItem(BaseModel):
    lat: float
    lon: float
    label: str
    municipio: str | None = None
    uf: str | None = None


class GeocodeResponse(BaseModel):
    tipo: str  # "forward" | "reverse"
    resultados: list[GeocodeResultItem]


# ── Endpoint ─────────────────────────────────────────────────────────────────

@router.post(
    "/geocode",
    response_model=GeocodeResponse,
    summary="Geocodificação (endereço→coords) ou reversa (coords→endereço)",
)
async def geocode(body: GeocodeRequest):
    is_reverse = body.endereco is None and body.lat is not None and body.lon is not None

    if not body.endereco and not is_reverse:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Forneça 'endereco' para busca direta ou 'lat'+'lon' para reversa.",
        )

    if is_reverse:
        logger.info("geocode reverse", lat=body.lat, lon=body.lon)
        try:
            data = await azure_maps.search_reverse(lat=body.lat, lon=body.lon)
        except Exception as e:
            logger.error("geocode reverse failed", error=str(e))
            raise HTTPException(status_code=502, detail=f"Azure Maps error: {e}")

        if not data.get("encontrado"):
            return GeocodeResponse(tipo="reverse", resultados=[])

        return GeocodeResponse(
            tipo="reverse",
            resultados=[
                GeocodeResultItem(
                    lat=body.lat,
                    lon=body.lon,
                    label=data.get("endereco", f"{body.lat}, {body.lon}"),
                    municipio=data.get("municipio"),
                    uf=data.get("uf"),
                )
            ],
        )

    # Forward geocoding
    logger.info("geocode forward", endereco=body.endereco, limite=body.limite)
    try:
        data = await azure_maps.search_fuzzy(
            query=body.endereco,
            limit=body.limite,
        )
    except Exception as e:
        logger.error("geocode forward failed", error=str(e))
        raise HTTPException(status_code=502, detail=f"Azure Maps error: {e}")

    items: list[GeocodeResultItem] = []
    for r in data.get("resultados", []):
        coords = r.get("coordenadas", {})
        items.append(
            GeocodeResultItem(
                lat=coords.get("lat", 0),
                lon=coords.get("lon", 0),
                label=r.get("endereco", ""),
                municipio=r.get("municipio"),
                uf=r.get("uf"),
            )
        )

    return GeocodeResponse(tipo="forward", resultados=items)


# ── On-demand jazida polygon ──────────────────────────────────────────────────

_POLIGONO_TTL = 86400 * 7  # 7 days — ANM polygons never change


@router.get(
    "/jazida/poligono",
    summary="Polígono GeoJSON de uma jazida ANM (on-demand, cached)",
    responses={
        200: {"description": "GeoJSON FeatureCollection com os shapes do processo"},
        404: {"description": "Processo não encontrado ou sem geometria"},
        503: {"description": "OpenSearch indisponível"},
    },
)
async def get_jazida_poligono(processo_id: str):
    """
    Fetch the GeoJSON polygon(s) for a single ANM processo on demand.

    Cache strategy (TTL 7 days):
        Redis hit  → return immediately (sub-ms)
        Redis miss → query OpenSearch anm_v003, extract shapes, store in Redis
    """
    import json
    from mcp_servers.common.redis_cache import get_redis_cache

    # Cache key uses upper-case for readability; query uses lower-case (index normalizer)
    pid = processo_id.strip()
    pid_upper = pid.upper()
    cache_key = f"poligono:jazida:{pid_upper}"

    redis_cache = await get_redis_cache()

    # ── L1: Redis ────────────────────────────────────────────────────────
    cached = await redis_cache.get(cache_key)
    if cached:
        logger.info("jazida_poligono: cache HIT", processo_id=pid_upper)
        return json.loads(cached)

    # ── L2: OpenSearch ───────────────────────────────────────────────────
    logger.info("jazida_poligono: cache MISS — querying OpenSearch", processo_id=pid_upper)

    try:
        from mcp_servers.common.opensearch_client import get_opensearch
        from mcp_servers.jazidas.queries.geo import extract_geojson_features, build_feature_collection

        os_service = await get_opensearch()
        index = "mr_jazidas_v001"
        # numero_processo is a keyword field — exact match, case-sensitive
        # Try both original casing and uppercase to be safe
        query = {
            "query": {
                "bool": {
                    "should": [
                        {"term": {"numero_processo": pid}},
                        {"term": {"numero_processo": pid_upper}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "_source": [
                "numero_processo",
                "geom",
                "substancias_desc",
                "area_ha",
                "fase",
                "ativo",
                "titular",
                "uf",
                "municipio",
            ],
            "size": 1,
        }
        result = await os_service.search(index, query)
        hits = result.get("hits", {}).get("hits", [])

    except Exception as e:
        logger.error("jazida_poligono: OpenSearch error", error=str(e), processo_id=pid_upper)
        raise HTTPException(status_code=503, detail=f"OpenSearch indisponível: {e}")

    if not hits:
        raise HTTPException(
            status_code=404,
            detail=f"Processo '{pid_upper}' não encontrado no índice ANM.",
        )

    features = extract_geojson_features(hits)
    if not features:
        raise HTTPException(
            status_code=404,
            detail=f"Processo '{pid_upper}' não possui geometria de polígono.",
        )

    feature_collection = build_feature_collection(features)

    # ── Store in Redis (fire-and-forget, non-blocking) ───────────────────
    try:
        await redis_cache.set(cache_key, json.dumps(feature_collection, ensure_ascii=False), ttl=_POLIGONO_TTL)
        logger.info("jazida_poligono: cached", processo_id=pid_upper, features=len(features))
    except Exception as e:
        logger.warning("jazida_poligono: failed to cache", error=str(e))

    return feature_collection


@router.get(
    "/cprm/poligono",
    summary="Polígono GeoJSON de uma ocorrência CPRM (on-demand, cached)",
    responses={
        200: {"description": "GeoJSON FeatureCollection (ex.: buffer em torno do ponto)"},
        404: {"description": "Ocorrência não encontrada ou sem polígono indexado"},
        503: {"description": "OpenSearch indisponível"},
    },
)
async def get_cprm_poligono(id_ocorrencia: str):
    """
    Fetch the indexed polygon for a CPRM mineral occurrence (e.g. buffer around Point).

    ``id_ocorrencia`` matches the OpenSearch document ``_id`` in ``mr_cprm_v001``.
    """
    import json

    from opensearchpy.exceptions import NotFoundError

    from mcp_servers.common.redis_cache import get_redis_cache

    oid = id_ocorrencia.strip()
    if not oid.isdigit():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="id_ocorrencia deve ser numérico (ex.: 40362).",
        )

    cache_key = f"poligono:cprm:{oid}"
    redis_cache = await get_redis_cache()

    cached = await redis_cache.get(cache_key)
    if cached:
        logger.info("cprm_poligono: cache HIT", id_ocorrencia=oid)
        return json.loads(cached)

    logger.info("cprm_poligono: cache MISS — OpenSearch get", id_ocorrencia=oid)

    try:
        from mcp_servers.common.opensearch_client import get_opensearch

        os_service = await get_opensearch()
        hit = await os_service.get("mr_cprm_v001", oid)
    except NotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Ocorrência CPRM '{oid}' não encontrada.",
        )
    except Exception as e:
        logger.error("cprm_poligono: OpenSearch error", error=str(e), id_ocorrencia=oid)
        raise HTTPException(status_code=503, detail=f"OpenSearch indisponível: {e}")

    source = hit.get("_source") or {}
    poly = source.get("poligono")
    geom_type = poly.get("type") if isinstance(poly, dict) else None
    if geom_type not in ("Polygon", "MultiPolygon"):
        raise HTTPException(
            status_code=404,
            detail=f"Ocorrência '{oid}' não possui polígono indexado.",
        )

    feature = {
        "type": "Feature",
        "properties": {
            "id_ocorrencia": source.get("id_ocorrencia") or oid,
            "nome": source.get("nome"),
            "substancia_principal": source.get("substancia_principal"),
            "poligono_fonte": source.get("poligono_fonte"),
            "uf": source.get("uf"),
            "municipio": source.get("municipio"),
        },
        "geometry": poly,
    }
    feature_collection = {"type": "FeatureCollection", "features": [feature]}

    try:
        await redis_cache.set(
            cache_key, json.dumps(feature_collection, ensure_ascii=False), ttl=_POLIGONO_TTL
        )
        logger.info("cprm_poligono: cached", id_ocorrencia=oid)
    except Exception as e:
        logger.warning("cprm_poligono: failed to cache", error=str(e))

    return feature_collection


@router.get(
    "/car/poligono",
    summary="Polígono GeoJSON de um imóvel CAR (on-demand, cached)",
    responses={
        200: {"description": "GeoJSON FeatureCollection com o polígono do imóvel"},
        404: {"description": "Imóvel não encontrado ou sem geometria"},
        503: {"description": "OpenSearch indisponível"},
    },
)
async def get_car_poligono(cod_car: str):
    """
    Fetch the GeoJSON polygon for a CAR rural property from mr_sicar_v001.

    Cache strategy (TTL 7 days):
        Redis hit  → return immediately
        Redis miss → query OpenSearch mr_sicar_v001 by cod_car, extract geometry
    """
    import json
    from mcp_servers.common.redis_cache import get_redis_cache

    cod = cod_car.strip()
    if not cod:
        raise HTTPException(status_code=400, detail="cod_car não pode ser vazio.")

    cache_key = f"poligono:car:{cod}"
    redis_cache = await get_redis_cache()

    cached = await redis_cache.get(cache_key)
    if cached:
        logger.info("car_poligono: cache HIT", cod_car=cod)
        return json.loads(cached)

    logger.info("car_poligono: cache MISS — querying OpenSearch", cod_car=cod)

    try:
        from mcp_servers.common.opensearch_client import get_opensearch

        os_service = await get_opensearch()
        query = {
            "query": {"term": {"cod_car": cod}},
            "_source": ["cod_car", "geom", "area_ha", "municipio", "uf", "tipo_imovel", "status_car", "nome_proprietario"],
            "size": 1,
        }
        result = await os_service.search("mr_sicar_v001", query)
        hits = result.get("hits", {}).get("hits", [])
    except Exception as e:
        logger.error("car_poligono: OpenSearch error", error=str(e), cod_car=cod)
        raise HTTPException(status_code=503, detail=f"OpenSearch indisponível: {e}")

    if not hits:
        raise HTTPException(status_code=404, detail=f"Imóvel CAR '{cod}' não encontrado.")

    source = hits[0].get("_source") or {}
    geom = source.get("geom")
    if not geom or not isinstance(geom, dict) or geom.get("type") not in ("Polygon", "MultiPolygon"):
        raise HTTPException(status_code=404, detail=f"Imóvel CAR '{cod}' não possui geometria de polígono.")

    feature = {
        "type": "Feature",
        "properties": {
            "cod_car":    source.get("cod_car") or cod,
            "area_ha":    source.get("area_ha"),
            "municipio":  source.get("municipio"),
            "uf":         source.get("uf"),
            "tipo":       source.get("tipo_imovel"),
            "status":     source.get("status_car"),
            "proprietario": source.get("nome_proprietario"),
        },
        "geometry": geom,
    }
    feature_collection = {"type": "FeatureCollection", "features": [feature]}

    try:
        await redis_cache.set(cache_key, json.dumps(feature_collection, ensure_ascii=False), ttl=_POLIGONO_TTL)
        logger.info("car_poligono: cached", cod_car=cod)
    except Exception as e:
        logger.warning("car_poligono: failed to cache", error=str(e))

    return feature_collection


@router.get(
    "/ferrovia/geometria",
    summary="GeoJSON de um trecho ferroviário (on-demand, cached)",
    responses={
        200: {"description": "GeoJSON FeatureCollection (linha)"},
        404: {"description": "Trecho não encontrado ou sem geometria"},
        503: {"description": "OpenSearch indisponível"},
    },
)
async def get_ferrovia_geometria(
    ferrovia_id: str,
    latitude: float | None = None,
    longitude: float | None = None,
):
    """Carrega geometria da malha ferroviária só quando o mapa precisa desenhar."""
    import json

    from mcp_servers.common.redis_cache import get_redis_cache
    from mcp_servers.common.opensearch_client import get_opensearch
    from mcp_servers.geo.queries.ferrovias import executar_obter_geometria_ferrovia

    fid = ferrovia_id.strip()
    if not fid:
        raise HTTPException(status_code=400, detail="ferrovia_id vazio.")

    cache_key = f"poligono:ferrovia:{fid}"
    redis_cache = await get_redis_cache()
    cached = await redis_cache.get(cache_key)
    if cached:
        logger.info("ferrovia_geometria: cache HIT", ferrovia_id=fid)
        return json.loads(cached)

    try:
        os_service = await get_opensearch()
        result = await executar_obter_geometria_ferrovia(
            os_service,
            ferrovia_id=fid,
            latitude=latitude,
            longitude=longitude,
            incluir_geometria=True,
        )
    except Exception as e:
        logger.error("ferrovia_geometria: OpenSearch error", error=str(e), ferrovia_id=fid)
        raise HTTPException(status_code=503, detail=f"OpenSearch indisponível: {e}") from e

    if not result.get("sucesso"):
        raise HTTPException(
            status_code=404,
            detail=result.get("mensagem", "Trecho não encontrado."),
        )
    feature = result.get("feature")
    if not isinstance(feature, dict):
        raise HTTPException(status_code=404, detail="Trecho sem geometria indexada.")

    payload = {"type": "FeatureCollection", "features": [feature]}
    try:
        await redis_cache.set(
            cache_key, json.dumps(payload, ensure_ascii=False), ttl=_POLIGONO_TTL
        )
    except Exception as e:
        logger.warning("ferrovia_geometria: failed to cache", error=str(e))

    return payload


@router.get(
    "/porto/poligono",
    summary="Polígono GeoJSON de um porto (on-demand, cached)",
    responses={
        200: {"description": "GeoJSON FeatureCollection"},
        404: {"description": "Porto não encontrado ou sem polígono"},
        503: {"description": "OpenSearch indisponível"},
    },
)
async def get_porto_poligono(
    codigo: str | None = None,
    nome: str | None = None,
    uf: str | None = None,
):
    """Carrega polígono do porto só quando o mapa precisa desenhar."""
    import json

    from mcp_servers.common.redis_cache import get_redis_cache
    from mcp_servers.common.opensearch_client import get_opensearch
    from mcp_servers.geo.queries.portos import executar_obter_poligono_porto

    if not codigo and not nome:
        raise HTTPException(status_code=400, detail="Informe codigo ou nome.")

    cache_key = f"poligono:porto:{(codigo or nome or '').strip().lower()}:{(uf or '').upper()}"
    redis_cache = await get_redis_cache()
    cached = await redis_cache.get(cache_key)
    if cached:
        logger.info("porto_poligono: cache HIT", codigo=codigo, nome=nome)
        return json.loads(cached)

    try:
        os_service = await get_opensearch()
        result = await executar_obter_poligono_porto(
            os_service,
            codigo=codigo,
            nome=nome,
            uf=uf,
            incluir_geometria=True,
        )
    except Exception as e:
        logger.error("porto_poligono: OpenSearch error", error=str(e))
        raise HTTPException(status_code=503, detail=f"OpenSearch indisponível: {e}") from e

    if not result.get("sucesso") or not result.get("encontrado"):
        raise HTTPException(
            status_code=404,
            detail=result.get("mensagem", "Porto não encontrado ou sem polígono."),
        )
    feature = result.get("feature")
    if not isinstance(feature, dict):
        raise HTTPException(status_code=404, detail="Porto sem polígono no índice.")

    payload = {"type": "FeatureCollection", "features": [feature]}
    try:
        await redis_cache.set(
            cache_key, json.dumps(payload, ensure_ascii=False), ttl=_POLIGONO_TTL
        )
    except Exception as e:
        logger.warning("porto_poligono: failed to cache", error=str(e))

    return payload
