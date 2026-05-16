"""
Resolução genérica de locais para rotas (sem hardcode por nome)
===============================================================

Cadeia (por endpoint):
  1. ``known_places.yaml`` (opcional, curadoria)
  2. Portos públicos (registry + OpenSearch)
  3. Busca textual em ``mr_jazidas_v001`` (titular/município/substância)
  4. Azure Maps Search com dica de UF extraída da pergunta
  5. Se geocode suspeito (UF inconsistente ou muito longe do par), re-tenta jazidas/Azure

Usado por ``calcular_rota`` / ``comparar_rotas`` — não pelo LLM diretamente.
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

import yaml

from mcp_servers.geo.queries.portos import resolve_endereco_via_portos_index
from mcp_servers.geo.services import azure_maps
from mcp_servers.geo.services.portos_registry import resolve_endereco_if_public_port

logger = logging.getLogger("mcp.geo.place_resolver")

_KNOWN_PLACES_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "known_places.yaml"
)

_UF_SIGLAS = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
})

_ESTADO_PARA_UF: dict[str, str] = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM",
    "bahia": "BA", "ceara": "CE", "distrito federal": "DF",
    "espirito santo": "ES", "goias": "GO", "maranhao": "MA",
    "mato grosso": "MT", "mato grosso do sul": "MS", "minas gerais": "MG",
    "para": "PA", "paraiba": "PB", "parana": "PR", "pernambuco": "PE",
    "piaui": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN",
    "rio grande do sul": "RS", "rondonia": "RO", "roraima": "RR",
    "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE",
    "tocantins": "TO",
}

_MINING_PLACE_RE = re.compile(
    r"\b(mina|minas|jazida|complexo|projeto)\b",
    re.IGNORECASE,
)

_PORT_PLACE_RE = re.compile(r"\bporto\b", re.IGNORECASE)

_UF_SUFFIX_RE = re.compile(r"[,/]\s*([A-Z]{2})\s*(?:,|$|\))", re.IGNORECASE)

_known_places_cache: list[dict[str, Any]] | None = None


def _strip_accents(text: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _load_known_places() -> list[dict[str, Any]]:
    global _known_places_cache
    if _known_places_cache is not None:
        return _known_places_cache
    places: list[dict[str, Any]] = []
    try:
        if _KNOWN_PLACES_PATH.is_file():
            raw = yaml.safe_load(_KNOWN_PLACES_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("places"), list):
                places = [p for p in raw["places"] if isinstance(p, dict)]
    except Exception as e:
        logger.warning("known_places.yaml: falha ao carregar (%s)", e)
    _known_places_cache = places
    return places


def parse_uf_hint_from_text(text: str) -> str | None:
    """Extrai sigla UF de sufixos ``..., PA`` ou ``/MG``."""
    m = _UF_SUFFIX_RE.search(text or "")
    if m:
        uf = m.group(1).upper()
        if uf in _UF_SIGLAS:
            return uf
    lower = _strip_accents(text or "")
    for nome, uf in _ESTADO_PARA_UF.items():
        if re.search(rf"\b{re.escape(nome)}\b", lower):
            return uf
    return None


def enrich_route_query(place: str, user_message: str = "") -> str:
    """
    Enriquece texto do plano de rota com UF/país quando inferível da pergunta.
    Não depende de nomes fixos — só padrões linguísticos e co-ocorrência na frase.
    """
    t = (place or "").strip()
    if not t:
        return t
    uf = parse_uf_hint_from_text(t)
    if not uf and user_message:
        idx = user_message.lower().find(t.lower()[: min(24, len(t))])
        if idx >= 0:
            window = user_message[max(0, idx - 20) : idx + len(t) + 50]
            uf = parse_uf_hint_from_text(window)
    if uf and not re.search(rf"\b{uf}\b", t, re.I):
        if not re.search(r"\bbrasil\b", t, re.I):
            return f"{t}, {uf}, Brasil"
        return f"{t}, {uf}"
    if not re.search(r"\bbrasil\b", t, re.I):
        return f"{t}, Brasil"
    return t


def _match_known_place(endereco: str) -> dict[str, Any] | None:
    key = _strip_accents(endereco)
    for entry in _load_known_places():
        keys = entry.get("keys") or []
        for k in keys:
            if _strip_accents(str(k)) in key or key in _strip_accents(str(k)):
                lat, lon = entry.get("lat"), entry.get("lon")
                if lat is None or lon is None:
                    continue
                nome = entry.get("nome") or k
                mun = entry.get("municipio") or ""
                uf = entry.get("uf") or ""
                label = f"{nome} — {mun}/{uf}".rstrip("/")
                return {
                    "lat": float(lat),
                    "lon": float(lon),
                    "endereco_consultado": endereco,
                    "endereco_resolvido": label,
                    "fonte": "known_places",
                    "uf": uf or None,
                }
    return None


async def _resolve_via_jazidas_text(
    os_service: Any,
    endereco: str,
    *,
    uf_hint: str | None = None,
) -> dict[str, Any] | None:
    """Centróide ANM por busca textual (titular/município/substância)."""
    from mcp_servers.jazidas.queries.detalhes import (
        build_processo_por_texto_query,
        _texto_busca_processo,
    )

    termo = _texto_busca_processo(endereco) or endereco.strip()
    if len(termo) < 3:
        return None
    if not (_MINING_PLACE_RE.search(endereco) or len(termo) >= 5):
        return None

    query = build_processo_por_texto_query(endereco, size=5)
    if uf_hint:
        query["query"] = {
            "bool": {
                "must": [query["query"]],
                "filter": [{"term": {"uf": uf_hint.upper()}}],
            }
        }

    try:
        result = await os_service.search_with_meta("mr_jazidas_v001", query)
    except Exception as e:
        logger.debug("place_resolver jazidas: %s", e)
        return None

    for hit in result.get("hits") or []:
        src = hit.get("_source") or {}
        loc = src.get("location") or {}
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat is None or lon is None:
            continue
        proc = src.get("numero_processo") or ""
        mun = src.get("municipio") or ""
        uf = src.get("uf") or ""

        # Metadados ricos para o popup do pin no mapa.
        titular = src.get("titular") or {}
        titular_nome = (
            titular.get("nome")
            or titular.get("razao_social")
            or src.get("razao_social")
            or None
        )
        subst_list = src.get("substancias_desc") or []
        substancia_str = ", ".join(subst_list[:3]) if subst_list else None
        fase = src.get("fase") or None
        area_ha = src.get("area_ha") or None

        detalhes: dict = {}
        if proc:
            detalhes["processo"] = proc
        if substancia_str:
            detalhes["substancia"] = substancia_str
        if fase:
            detalhes["fase"] = fase
        if area_ha:
            detalhes["area_ha"] = area_ha
        if f"{mun}/{uf}".strip("/"):
            detalhes["municipio"] = f"{mun}/{uf}".strip("/")
        if titular_nome:
            detalhes["titulares"] = [titular_nome]

        return {
            "lat": float(lat),
            "lon": float(lon),
            "endereco_consultado": endereco,
            "endereco_resolvido": f"Processo {proc} — {mun}/{uf}".rstrip("/"),
            "fonte": "jazidas_texto",
            "uf": uf or None,
            "detalhes": detalhes if detalhes else None,
        }
    return None


def _endpoint_from_geocode(
    endereco: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    coords = row.get("coordenadas") or {}
    return {
        "lat": float(coords["lat"]),
        "lon": float(coords["lon"]),
        "endereco_consultado": endereco,
        "endereco_resolvido": row.get("endereco"),
        "fonte": "geocodificado",
        "uf": (row.get("uf") or "").upper() or None,
    }


def _geocode_suspect(
    resolved: dict[str, Any],
    *,
    uf_hint: str | None,
    peer: dict[str, Any] | None,
    query: str,
) -> bool:
    """Heurísticas genéricas de homônimo / geocode errado."""
    ruf = (resolved.get("uf") or "").upper()
    if uf_hint and ruf and ruf != uf_hint.upper():
        return True
    parsed = parse_uf_hint_from_text(query)
    if parsed and ruf and ruf != parsed.upper():
        return True
    lat = float(resolved["lat"])
    if _MINING_PLACE_RE.search(query) and lat < -12.0 and not (uf_hint or parsed):
        return True
    if peer and peer.get("lat") is not None and peer.get("lon") is not None:
        d = _haversine_km(
            float(peer["lat"]), float(peer["lon"]),
            lat, float(resolved["lon"]),
        )
        if d > 2800:
            return True
    return False


async def resolve_place_for_route(
    os_service: Any,
    endereco: str,
    *,
    user_message: str = "",
    peer: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """
    Resolve endpoint para ``calcular_rota``. Mesmo contrato que ``_resolve_endpoint``.
    """
    if not endereco or not str(endereco).strip():
        return None, {
            "sucesso": False,
            "mensagem": "Endereço vazio para resolução.",
        }

    query = enrich_route_query(str(endereco).strip(), user_message)
    uf_hint = parse_uf_hint_from_text(query)

    hit = _match_known_place(query)
    if hit is not None:
        return hit, None

    looks_like_mine = bool(_MINING_PLACE_RE.search(query))
    looks_like_port = bool(_PORT_PLACE_RE.search(query))

    # Só pesquisa portos quando o texto não parece mina/jazida/complexo.
    # Evita falso-positivo: "Mina do Salobo" → porto homónimo no índice.
    if looks_like_port and not looks_like_mine:
        porto = await resolve_endereco_via_portos_index(os_service, query)
        if porto is None:
            porto = resolve_endereco_if_public_port(query)
        if porto is not None:
            porto.setdefault("uf", parse_uf_hint_from_text(porto.get("endereco_resolvido") or ""))
            return porto, None

    # Pesquisa jazidas/titular em mr_jazidas_v001 quando parece mina ou não é porto óbvio.
    if looks_like_mine or not looks_like_port:
        jaz = await _resolve_via_jazidas_text(os_service, query, uf_hint=uf_hint)
        if jaz is not None:
            if not peer or not _geocode_suspect(jaz, uf_hint=uf_hint, peer=peer, query=query):
                return jaz, None

    # Para portos ambíguos (texto não tem "porto" mas também não tem "mina"),
    # tenta portos depois de jazidas.
    if not looks_like_mine and not looks_like_port:
        porto = await resolve_endereco_via_portos_index(os_service, query)
        if porto is None:
            porto = resolve_endereco_if_public_port(query)
        if porto is not None:
            porto.setdefault("uf", parse_uf_hint_from_text(porto.get("endereco_resolvido") or ""))
            return porto, None

    try:
        geo_result = await azure_maps.search_fuzzy(query=query, country="BR", limit=5)
    except Exception as e:
        return None, {
            "sucesso": False,
            "mensagem": f"Falha ao geocodificar '{endereco}': {e}",
        }

    rows = geo_result.get("resultados") or []
    if not rows:
        jaz = await _resolve_via_jazidas_text(os_service, query, uf_hint=uf_hint)
        if jaz:
            return jaz, None
        return None, {
            "sucesso": False,
            "mensagem": (
                f"Não foi possível resolver '{endereco}'. "
                "Especifique município/UF (ex.: Nome/PA) ou número de processo ANM."
            ),
        }

    resolved = _endpoint_from_geocode(query, rows[0])
    if _geocode_suspect(resolved, uf_hint=uf_hint, peer=peer, query=query):
        jaz = await _resolve_via_jazidas_text(os_service, query, uf_hint=uf_hint)
        if jaz is not None:
            logger.info(
                "place_resolver: geocode suspeito para %r — usando jazidas_texto",
                endereco,
            )
            return jaz, None
        for row in rows[1:]:
            alt = _endpoint_from_geocode(query, row)
            if not _geocode_suspect(alt, uf_hint=uf_hint, peer=peer, query=query):
                return alt, None

    return resolved, None
