"""
CPRM Afloramentos Query Module
================================

Consulta on-demand à OGC API Features do SGB/CPRM para recuperar
afloramentos geológicos próximos a uma coordenada.

Endpoint:
    https://geoservicos.sgb.gov.br/ogcapi/collections/geologia/afloramentos/items

Total na base: ~350.568 afloramentos (pontos levantados em campo)

Campos retornados:
    id_afloramento, tipo_afloramento, descricao, rochas, municipio, uf,
    geologo, data_cadastro, projeto, folha, codigo_folha, sureg,
    metodo_geoposicionamento, numero_campo, toponimia

Estratégia de busca:
    1. Converte raio_km em BBOX (bounding box em graus) com folga de +10%
    2. Requisita OGC API Features com BBOX filter (serverside) + limit
    3. Pós-filtra por distância geodésica exata (Haversine)
    4. Ordena por distância crescente
    5. Agrupa por tipo de rocha para resumo litológico

Performance: ~200–700ms por chamada (latência rede + parsing JSON)
"""

from __future__ import annotations

import logging
import math
from typing import Any

import httpx

logger = logging.getLogger("mcp.jazidas.queries.afloramentos")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

CPRM_AFLORAMENTOS_URL = (
    "https://geoservicos.sgb.gov.br/ogcapi/collections/"
    "geologia/afloramentos/items"
)

MAX_AFLORAMENTOS_DEFAULT = 25
# BBOX folga de 10% para compensar borda dos quadrantes
BBOX_MARGIN = 1.10
# Timeout HTTP (segundos)
HTTP_TIMEOUT = 15.0
# Fator de conversão graus por km (latitude)
KM_PER_DEG_LAT = 111.0


# ─────────────────────────────────────────────────────────────────────────────
# Geo helpers
# ─────────────────────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância geodésica em km entre dois pontos (Haversine)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _bbox_from_point(lat: float, lon: float, raio_km: float) -> tuple[float, float, float, float]:
    """
    Retorna (min_lon, min_lat, max_lon, max_lat) de um quadrado
    circunscrito ao círculo de raio raio_km, com margem de BBOX_MARGIN.
    """
    r = raio_km * BBOX_MARGIN
    dlat = r / KM_PER_DEG_LAT
    dlon = r / (KM_PER_DEG_LAT * math.cos(math.radians(lat)) + 1e-9)
    return (
        lon - dlon,
        lat - dlat,
        lon + dlon,
        lat + dlat,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Formatter
# ─────────────────────────────────────────────────────────────────────────────

def _format_afloramento(feat: dict, dist_km: float) -> dict[str, Any]:
    """Converte feature GeoJSON em dict estruturado para a tool."""
    p = feat.get("properties") or {}
    geom = feat.get("geometry") or {}
    coords = geom.get("coordinates") or []

    return {
        "id_afloramento":          p.get("id_afloramento"),
        "tipo_afloramento":        p.get("tipo_afloramento") or None,
        "rochas":                  p.get("rochas") or None,
        "descricao":               (p.get("descricao") or "")[:250] or None,
        "municipio":               p.get("municipio"),
        "uf":                      p.get("uf"),
        "projeto":                 p.get("projeto"),
        "folha":                   p.get("folha"),
        "geologo":                 p.get("geologo"),
        "data_cadastro":           (p.get("data_cadastro") or "")[:10] or None,
        "metodo_geoposicionamento": p.get("metodo_geoposicionamento"),
        "numero_campo":            p.get("numero_campo"),
        "toponimia":               p.get("toponimia"),
        "distancia_km":            round(dist_km, 2),
        "location": {
            "lon": coords[0] if len(coords) >= 2 else None,
            "lat": coords[1] if len(coords) >= 2 else None,
        },
    }


def _resumo_litologico(
    afloramentos: list[dict],
    total_bbox: int,
    raio_km: float,
) -> dict[str, Any]:
    """
    Resumo litológico da área: distribuição de tipos de rocha,
    tipos de afloramento, e projetos CPRM presentes.
    """
    por_rocha: dict[str, int] = {}
    por_tipo: dict[str, int] = {}
    projetos: set[str] = set()

    for a in afloramentos:
        rochas = a.get("rochas") or "Não informado"
        # rochas pode conter lista separada por vírgula/ponto-e-vírgula
        for r in [x.strip() for x in rochas.replace(";", ",").split(",")]:
            if r:
                por_rocha[r] = por_rocha.get(r, 0) + 1

        tipo = a.get("tipo_afloramento") or "Não informado"
        por_tipo[tipo] = por_tipo.get(tipo, 0) + 1

        proj = a.get("projeto")
        if proj:
            projetos.add(proj)

    # Top 10 tipos de rocha
    top_rochas = dict(
        sorted(por_rocha.items(), key=lambda x: x[1], reverse=True)[:10]
    )
    # Rochas ígneas/metamórficas estratégicas (potencial mineral)
    _ESTRATEGICAS = {
        "granito", "granitóide", "gnaisse", "xisto", "quartzito",
        "basalto", "anortosito", "pegmatito", "skarn", "greisen",
        "carbonatito", "siênito", "gabro", "diabásio",
    }
    rochas_estrategicas = [
        r for r in por_rocha
        if any(e in r.lower() for e in _ESTRATEGICAS)
    ]

    return {
        "total_na_bbox":          total_bbox,
        "total_no_raio":          len(afloramentos),
        "raio_km":                raio_km,
        "tipos_rocha":            top_rochas,
        "tipos_afloramento":      por_tipo,
        "rochas_possivelmente_estrategicas": sorted(rochas_estrategicas),
        "projetos_cprm":          sorted(projetos)[:10],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def executar_afloramentos_proximos(
    lat: float,
    lon: float,
    raio_km: float = 15.0,
    tipo_rocha: str | None = None,
    max_resultados: int = MAX_AFLORAMENTOS_DEFAULT,
) -> dict[str, Any]:
    """
    Consulta on-demand à OGC API Features do CPRM para retornar
    afloramentos geológicos próximos a uma coordenada.

    Fluxo:
      1. Converte raio em BBOX (mais rápido que DWithin no pygeoapi)
      2. Busca até ``max_resultados * 4`` features na BBOX (serverside limit)
      3. Pós-filtra por Haversine e filtra por tipo_rocha se solicitado
      4. Ordena por distância e agrupa para resumo litológico

    Args:
        lat:            Latitude (WGS84)
        lon:            Longitude (WGS84)
        raio_km:        Raio de busca em km (default 15, max 100)
        tipo_rocha:     Filtro textual por tipo de rocha (ex: "granito", "xisto")
        max_resultados: Máximo de afloramentos a retornar (default 25)

    Returns:
        {
          "area": {...},
          "resumo_litologico": {...},
          "afloramentos": [...],
          "mapa": {"pontos": [...]}
        }
    """
    raio_km = min(max(raio_km, 0.5), 100.0)
    max_resultados = max(1, min(max_resultados, 100))

    bbox = _bbox_from_point(lat, lon, raio_km)
    # Pede 4x o limite para ter margem após filtro por raio exato
    server_limit = max(max_resultados * 4, 100)

    params = {
        "bbox":  f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
        "limit": server_limit,
        "f":     "json",
    }

    logger.info(
        "afloramentos: bbox=%s limit=%d raio=%.1fkm rocha=%r",
        params["bbox"], server_limit, raio_km, tipo_rocha,
    )

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(CPRM_AFLORAMENTOS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return {"erro": "Timeout ao consultar OGC API CPRM (>15s). Tente raio menor."}
    except httpx.HTTPStatusError as e:
        return {"erro": f"HTTP {e.response.status_code} ao consultar CPRM WFS."}
    except Exception as e:
        logger.error("afloramentos: falha na requisição: %s", e)
        return {"erro": str(e)}

    features = data.get("features") or []
    total_bbox = data.get("numberMatched") or len(features)

    # Pós-filtro por distância geodésica exata
    dentro_do_raio: list[tuple[float, dict]] = []
    for feat in features:
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        feat_lon, feat_lat = float(coords[0]), float(coords[1])
        dist = _haversine_km(lat, lon, feat_lat, feat_lon)
        if dist <= raio_km:
            dentro_do_raio.append((dist, feat))

    # Filtro por tipo de rocha (case-insensitive substring)
    if tipo_rocha:
        filtro = tipo_rocha.lower()
        dentro_do_raio = [
            (d, f) for d, f in dentro_do_raio
            if filtro in (f.get("properties", {}).get("rochas") or "").lower()
        ]

    # Ordena por distância e limita
    dentro_do_raio.sort(key=lambda x: x[0])
    dentro_do_raio = dentro_do_raio[:max_resultados]

    afloramentos = [_format_afloramento(f, d) for d, f in dentro_do_raio]
    resumo = _resumo_litologico(afloramentos, total_bbox, raio_km)

    # Pontos para o mapa (compatível com map_data / MapaPonto no frontend)
    pontos_mapa = []
    for idx, a in enumerate(afloramentos):
        loc = a.get("location") or {}
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat is None or lon is None:
            continue
        oid = a.get("id_afloramento")
        proc = f"afl-{oid}" if oid is not None else f"afl-i{idx}"
        label = (a.get("rochas") or "Afloramento")[:120]
        mun = a.get("municipio")
        ufv = a.get("uf")
        ponto: dict[str, Any] = {
            "lat": float(lat),
            "lon": float(lon),
            "tipo": "afloramento",
            "processo": proc,
            "label": label,
            "substancias": [label] if label else [],
            "municipios": [str(mun)] if mun else [],
            "uf": [str(ufv)] if ufv else [],
            "distancia_km": a["distancia_km"],
        }
        if a.get("tipo_afloramento"):
            ponto["substancia"] = a["tipo_afloramento"]
        if a.get("descricao"):
            ponto["descricao"] = a["descricao"]
        if a.get("projeto"):
            ponto["projeto"] = a["projeto"]
        pontos_mapa.append(ponto)

    logger.info(
        "afloramentos: bbox=%d features, raio=%d, filtro_rocha=%r → %d retornados",
        len(features), len(dentro_do_raio), tipo_rocha, len(afloramentos),
    )

    return {
        "area": {
            "lat": lat,
            "lon": lon,
            "raio_km": raio_km,
            "filtro_rocha": tipo_rocha,
        },
        "resumo_litologico": resumo,
        "afloramentos":      afloramentos,
        "mapa":              {"pontos": pontos_mapa},
    }
