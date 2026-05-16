"""
Azure Maps REST Client
=======================

Async HTTP wrapper for Azure Maps APIs:
    - Route Directions (truck/car)
    - Route Range (isochrone)
    - Search Fuzzy (forward geocoding)
    - Search Reverse (reverse geocoding)

Uses aiohttp with connection pooling and retries via tenacity.
"""

import logging
import re
import ssl
from typing import Any, Literal

import aiohttp
import certifi
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from mcp_servers.common.config import mcp_settings

logger = logging.getLogger("mcp.geo.azure_maps")

_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        _session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return _session


async def close_session() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


def _base_params() -> dict[str, str]:
    return {
        "api-version": "1.0",
        "subscription-key": mcp_settings.azure_maps_subscription_key,
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
    reraise=True,
)
async def _get_json(url: str, params: dict) -> dict[str, Any]:
    session = _get_session()
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json()


# ======================================================================
# Route Directions
# ======================================================================


async def route_directions(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    mode: Literal["truck", "car"] = "truck",
    avoid_tolls: bool = False,
) -> dict[str, Any]:
    """
    Call Azure Maps Route Directions API.

    Returns parsed summary + polyline for a truck or car route.
    """
    base = mcp_settings.azure_maps_base_url.rstrip("/")
    url = f"{base}/route/directions/json"

    params = {
        **_base_params(),
        "query": f"{origin_lat},{origin_lon}:{dest_lat},{dest_lon}",
        "routeRepresentation": "polyline",
        "computeTravelTimeFor": "all",
        "traffic": "true",
    }

    if mode == "truck":
        params["travelMode"] = "truck"
        params["vehicleWeight"] = str(mcp_settings.truck_weight_kg)
        params["vehicleHeight"] = str(mcp_settings.truck_height_m)
        params["vehicleWidth"] = str(mcp_settings.truck_width_m)
        params["vehicleLength"] = str(mcp_settings.truck_length_m)
        params["vehicleAxleWeight"] = str(mcp_settings.truck_axle_weight_kg)
    else:
        params["travelMode"] = "car"

    if avoid_tolls:
        params["avoid"] = "tollRoads"

    data = await _get_json(url, params)

    if not data.get("routes"):
        return {"sucesso": False, "mensagem": "Nenhuma rota encontrada."}

    route = data["routes"][0]
    summary = route["summary"]
    dist_km = round(summary["lengthInMeters"] / 1000, 1)
    dur_min = round(summary["travelTimeInSeconds"] / 60, 1)
    traffic_min = round(summary.get("trafficDelayInSeconds", 0) / 60, 1)

    polyline: list[dict[str, float]] = []
    for leg in route.get("legs", []):
        for pt in leg.get("points", []):
            polyline.append({"lat": pt["latitude"], "lon": pt["longitude"]})

    dur_h = int(dur_min // 60)
    dur_m = int(dur_min % 60)
    dur_str = f"{dur_h}h{dur_m:02d}min" if dur_h else f"{dur_m}min"

    return {
        "distancia_km": dist_km,
        "duracao_min": dur_min,
        "atraso_trafego_min": traffic_min,
        "modo": mode,
        "resumo": f"{dist_km} km • ~{dur_str} ({mode})",
        "polyline": polyline,
        "origem": {"lat": origin_lat, "lon": origin_lon},
        "destino": {"lat": dest_lat, "lon": dest_lon},
    }


# ======================================================================
# Route Range (Isochrone)
# ======================================================================


async def route_range(
    lat: float,
    lon: float,
    criterio: Literal["tempo", "distancia"] = "tempo",
    valor: float = 60,
    mode: Literal["truck", "car"] = "truck",
) -> dict[str, Any]:
    """
    Call Azure Maps Route Range API.

    Returns a GeoJSON polygon of the reachable area from a point.
    """
    base = mcp_settings.azure_maps_base_url.rstrip("/")
    url = f"{base}/route/range/json"

    params = {
        **_base_params(),
        "query": f"{lat},{lon}",
    }

    if criterio == "tempo":
        params["timeBudgetInSec"] = str(int(valor * 60))
    else:
        params["distanceBudgetInMeters"] = str(int(valor * 1000))

    if mode == "truck":
        params["travelMode"] = "truck"
        params["vehicleWeight"] = str(mcp_settings.truck_weight_kg)
        params["vehicleHeight"] = str(mcp_settings.truck_height_m)
        params["vehicleWidth"] = str(mcp_settings.truck_width_m)
        params["vehicleLength"] = str(mcp_settings.truck_length_m)
    else:
        params["travelMode"] = "car"

    data = await _get_json(url, params)

    boundary = data.get("reachableRange", {}).get("boundary", [])
    if not boundary:
        return {"sucesso": False, "mensagem": "Não foi possível calcular a isócrona."}

    coords = [[pt["longitude"], pt["latitude"]] for pt in boundary]
    if coords[0] != coords[-1]:
        coords.append(coords[0])

    feature = {
        "type": "Feature",
        "properties": {
            "tipo": "isocrona",
            "criterio": criterio,
            "valor": valor,
            "modo": mode,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [coords],
        },
    }

    return {
        "centro": {"lat": lat, "lon": lon},
        "criterio": criterio,
        "valor": valor,
        "modo": mode,
        "feature": feature,
    }


# ======================================================================
# Helpers
# ======================================================================

_ESTADO_PARA_UF: dict[str, str] = {
    "Acre": "AC", "Alagoas": "AL", "Amapá": "AP", "Amazonas": "AM",
    "Bahia": "BA", "Ceará": "CE", "Distrito Federal": "DF",
    "Espírito Santo": "ES", "Goiás": "GO", "Maranhão": "MA",
    "Mato Grosso": "MT", "Mato Grosso do Sul": "MS", "Minas Gerais": "MG",
    "Pará": "PA", "Paraíba": "PB", "Paraná": "PR", "Pernambuco": "PE",
    "Piauí": "PI", "Rio de Janeiro": "RJ", "Rio Grande do Norte": "RN",
    "Rio Grande do Sul": "RS", "Rondônia": "RO", "Roraima": "RR",
    "Santa Catarina": "SC", "São Paulo": "SP", "Sergipe": "SE",
    "Tocantins": "TO",
}

# Sigla → nome completo (normalização de consultas tipo "Ouro Preto/MG")
_UF_PARA_ESTADO: dict[str, str] = {uf: nome for nome, uf in _ESTADO_PARA_UF.items()}


def geocode_query_cache_key(raw: str) -> str:
    """Chave estável para cache (inclui normalização Cidade/UF → Cidade, Estado, Brasil)."""
    normalized, _, _ = _parse_br_geocode_hints(raw)
    return normalized.casefold().strip()


def _parse_br_geocode_hints(raw: str) -> tuple[str, str | None, str | None]:
    """
    Extrai UF (sigla) e município/localidade provável de padrões brasileiros
    comuns em texto livre, e devolve a query reescrita para o Azure.

    Ex.: "Rua dos Geólogos, 100, Ouro Preto/MG"
         → ("Rua dos Geólogos, 100, Ouro Preto, Minas Gerais, Brasil", "MG", "Ouro Preto")

    Sem padrão ``.../UF`` devolve (raw.strip(), None, None).
    """
    text = raw.strip()
    m = re.search(r"/\s*([A-Z]{2})\s*$", text, re.IGNORECASE)
    if not m:
        return text, None, None
    uf = m.group(1).upper()
    if uf not in _UF_PARA_ESTADO:
        return text, None, None
    prefix = text[: m.start()].strip()
    locality: str | None = None
    if prefix:
        locality = prefix.split(",")[-1].strip() or None
    estado = _UF_PARA_ESTADO[uf]
    normalized = f"{prefix}, {estado}, Brasil" if prefix else f"{estado}, Brasil"
    return normalized, uf, locality


def _rank_geocode_results(
    rows: list[dict[str, Any]],
    uf_hint: str | None,
    locality_hint: str | None,
) -> list[dict[str, Any]]:
    """
    Prioriza hits no UF pedido e no município (evita homônimos tipo
    "Rua Ouro Preto" em outro estado quando o usuário disse "Ouro Preto/MG").
    """
    if not rows:
        return rows

    def score(r: dict[str, Any]) -> float:
        s = 0.0
        ruf = (r.get("uf") or "").upper()
        if uf_hint and ruf == uf_hint.upper():
            s += 1_000.0
        mun = (r.get("municipio") or "").strip().lower()
        if locality_hint:
            loc = locality_hint.strip().lower()
            if mun and loc:
                if mun == loc:
                    s += 500.0
                elif loc in mun or mun in loc:
                    s += 200.0
        conf = r.get("confianca")
        if isinstance(conf, (int, float)):
            s += float(conf)
        return s

    ranked = sorted(rows, key=score, reverse=True)
    if uf_hint:
        filtered = [r for r in ranked if (r.get("uf") or "").upper() == uf_hint.upper()]
        if filtered:
            return filtered
    return ranked


def _extract_uf(addr: dict) -> str | None:
    """
    Returns the 2-letter Brazilian state code from an Azure Maps address dict.

    Azure Maps sometimes returns:
      - countrySubdivisionCode = "MG"          (ideal)
      - countrySubdivisionCode = None           (missing for reverse geocoding)
      - countrySubdivision     = "Minas Gerais" (full name fallback)
    """
    code = addr.get("countrySubdivisionCode")
    if code and len(code) == 2:
        return code.upper()

    full_name = addr.get("countrySubdivision", "")
    return _ESTADO_PARA_UF.get(full_name)


# ======================================================================
# Search Fuzzy (Forward Geocoding)
# ======================================================================


async def search_fuzzy(
    query: str,
    country: str = "BR",
    limit: int = 5,
) -> dict[str, Any]:
    """Forward geocoding via Azure Maps Search Address (structured query string)."""
    base = mcp_settings.azure_maps_base_url.rstrip("/")
    url = f"{base}/search/address/json"

    normalized, uf_hint, locality_hint = _parse_br_geocode_hints(query)
    if normalized != query.strip():
        logger.info(
            "search_fuzzy: BR query normalized %r → %r (uf_hint=%s locality_hint=%s)",
            query,
            normalized,
            uf_hint,
            locality_hint,
        )
    # Pedir mais candidatos quando há UF/localidade no texto — o 1º hit do
    # Azure costuma errar estado em endereços ambíguos ("… Ouro Preto/MG").
    fetch_limit = max(int(limit), 8) if (uf_hint or locality_hint) else max(int(limit), 1)

    params = {
        **_base_params(),
        "query": normalized,
        "countrySet": country,
        "limit": str(fetch_limit),
        "language": "pt-BR",
    }

    data = await _get_json(url, params)

    results: list[dict[str, Any]] = []
    for r in data.get("results", []):
        addr = r.get("address", {})
        pos = r.get("position", {})
        results.append({
            "endereco": addr.get("freeformAddress", ""),
            "coordenadas": {
                "lat": pos.get("lat"),
                "lon": pos.get("lon"),
            },
            "tipo": r.get("entityType") or r.get("type", ""),
            "confianca": (
                r.get("matchConfidence", {}).get("score")
                if isinstance(r.get("matchConfidence"), dict) else None
            ),
            "municipio": addr.get("municipality"),
            "uf": _extract_uf(addr),
            "cep": addr.get("postalCode"),
        })

    ranked = _rank_geocode_results(results, uf_hint, locality_hint)
    trimmed = ranked[: max(1, int(limit))]

    return {
        "total": len(trimmed),
        "resultados": trimmed,
    }


# ======================================================================
# Search Reverse (Reverse Geocoding)
# ======================================================================


async def search_reverse(
    lat: float,
    lon: float,
) -> dict[str, Any]:
    """Reverse geocoding via Azure Maps Search Reverse."""
    base = mcp_settings.azure_maps_base_url.rstrip("/")
    url = f"{base}/search/address/reverse/json"

    params = {
        **_base_params(),
        "query": f"{lat},{lon}",
        "language": "pt-BR",
    }

    data = await _get_json(url, params)

    addresses = data.get("addresses", [])
    if not addresses:
        return {
            "encontrado": False,
            "mensagem": f"Nenhum endereço encontrado para ({lat}, {lon}).",
        }

    addr = addresses[0].get("address", {})
    return {
        "encontrado": True,
        "endereco": addr.get("freeformAddress", ""),
        "coordenadas": {"lat": lat, "lon": lon},
        "municipio": addr.get("municipality"),
        "uf": _extract_uf(addr),
        "cep": addr.get("postalCode"),
    }
