"""
Empresas Query Module
======================

Query + formatação para ``buscar_empresas`` no índice **mr_empresas_v001**
(schema plano da RFB filtrada no ETL: ``cnae_principal``, ``cnaes_secundarios``,
``situacao``, ``location`` como ``geo_point``, etc.).

Fluxo (após CnaeResolver em ``tools.py``):
    CNAE (principal OU secundário) + opcional ``geo_distance`` em ``location``
    + filtros UF / situação ativa.

Compat: se um hit ainda vier no formato legado aninhado (``empresa.*``),
``_format_empresa_hit`` tenta o caminho antigo.
"""

import logging
from math import atan2, cos, radians, sin, sqrt
from typing import Any

from mcp_servers.common.formatters import (
    build_contato,
    extract_municipio_nome,
    format_cnpj,
)
from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.empresas.schemas import ResolucaoCnae

logger = logging.getLogger("mcp.empresas.query")

# ==================== Constants ====================

INDEX_CNPJ = "mr_empresas_v001"

# Campo geo_point no índice (ETL: CNEFE + opcional refinamento em runtime no dict de saída)
_GEO_FIELD = "location"

# Max results to fetch per query (pre-pagination — full set cached in Redis)
MAX_RESULTS = 200

# Maximum acceptable drift (km) when replacing OpenSearch coordinates with
# Azure Maps geocoded coordinates. Geocoding should only refine precision
# (e.g., centroid → street level), not teleport a pin to another city.
# Set to 50 km: generous enough for large municipalities but prevents
# cross-city/cross-state errors from ambiguous address geocoding.
_MAX_GEOCODE_DRIFT_KM = 50


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine great-circle distance in km between two WGS-84 points."""
    R = 6_371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

# RFB porte codes → human-readable labels
_PORTE_MAP: dict[str, str] = {
    "00": "Não informado",
    "01": "Microempresa",
    "03": "Empresa de Pequeno Porte",
    "05": "Demais (Médio/Grande)",
}

# Max municipalities to fetch boundaries for
MAX_MUNICIPIOS = 20

# Base _source fields — mr_empresas_v001 (plano)
BASE_SOURCE_FIELDS = [
    "cnpj_basico",
    "cnpj_completo",
    "razao_social",
    "nome_fantasia",
    "capital_social",
    "porte",
    "cnae_principal",
    "cnae_desc",
    "cnaes_secundarios",
    "situacao",
    "uf",
    "municipio",
    _GEO_FIELD,
]

# Contact fields (added when incluir_contatos=True)
CONTATO_SOURCE_FIELDS = [
    "telefone",
    "telefone2",
    "email",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
]


# ==================== Query Builder ====================


def build_empresas_query(
    resolucao: ResolucaoCnae,
    latitude: float | None = None,
    longitude: float | None = None,
    raio_km: float = 30.0,
    uf: str | None = None,
    apenas_ativas: bool = False,
    incluir_contatos: bool = True,
    ordenar_por: str = "distancia",
    incluir_mei: bool = True,
) -> dict[str, Any]:
    """
    Build OpenSearch bool query for ``buscar_empresas`` (``mr_empresas_v001``).

    Combina:
        - CNAE: ``cnae_principal`` OU ``cnaes_secundarios`` (lista de códigos)
        - Opcional ``geo_distance`` no campo ``location``
        - Opcional: ``situacao`` = Ativa, filtro ``uf``

    Ordenação:
        - ``distancia``: ``_geo_distance`` em ``location`` se houver lat/lon
        - ``capital_social``: ``capital_social`` descendente

    Nota: ``mr_empresas_v001`` não indexa ``opcaoMEI``; ``incluir_mei`` é ignorado
    na query (comportamento documentado para não quebrar a API da ferramenta).
    """
    if not incluir_mei:
        logger.debug(
            "build_empresas_query: incluir_mei=False ignorado — índice sem campo opcaoMEI"
        )

    must_clauses: list[dict] = []
    filter_clauses: list[dict] = []

    # ── CNAE filter: primary OR secondary activity must match ──
    if resolucao.encontrou:
        codigos = resolucao.codigos
        must_clauses.append(
            {
                "bool": {
                    "should": [
                        {
                            "terms": {
                                "cnae_principal": codigos,
                                "boost": 2.0,
                            }
                        },
                        {
                            "terms": {
                                "cnaes_secundarios": codigos,
                                "boost": 1.0,
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            }
        )

    # ── Geo distance (optional — allows purely CNAE-based search) ──
    has_geo = latitude is not None and longitude is not None
    if has_geo:
        filter_clauses.append(
            {
                "geo_distance": {
                    "distance": f"{raio_km}km",
                    _GEO_FIELD: {"lat": latitude, "lon": longitude},
                }
            }
        )

    # ── Active companies only (texto legível gravado pelo ETL) ──
    if apenas_ativas:
        filter_clauses.append({"term": {"situacao": "Ativa"}})

    # ── UF filter ──
    if uf:
        filter_clauses.append({"term": {"uf": uf.strip().upper()}})

    # ── _source fields ──
    source_fields = list(BASE_SOURCE_FIELDS)
    if incluir_contatos:
        source_fields.extend(CONTATO_SOURCE_FIELDS)

    # ── Sort ──
    sort: list[dict] | None = None
    if ordenar_por == "capital_social":
        sort = [
            {"capital_social": {"order": "desc", "missing": "_last"}},
        ]
        if has_geo:
            sort.append({
                "_geo_distance": {
                    _GEO_FIELD: {"lat": latitude, "lon": longitude},
                    "order": "asc",
                    "unit": "km",
                }
            })
    elif has_geo:
        sort = [
            {
                "_geo_distance": {
                    _GEO_FIELD: {"lat": latitude, "lon": longitude},
                    "order": "asc",
                    "unit": "km",
                }
            }
        ]

    query: dict[str, Any] = {
        "size": MAX_RESULTS,
        "query": {
            "bool": {
                "must": must_clauses,
                "filter": filter_clauses,
            }
        },
        "_source": source_fields,
    }

    if sort:
        query["sort"] = sort

    return query


# ==================== Result Formatting ====================


def format_empresas_results(
    hits: list[dict],
    incluir_contatos: bool = True,
) -> list[dict]:
    """
    Formata hits do OpenSearch em dicts compatíveis com ``EmpresaResumida``.

    Args:
        hits: Raw hits from search_with_meta
        incluir_contatos: Include contact data in each result

    Returns:
        List of formatted empresa dicts
    """
    return [_format_empresa_hit(hit, incluir_contatos) for hit in hits]


def _is_flat_mr_empresas(source: dict) -> bool:
    """Documento plano indexado pelo ETL (``mr_empresas_v001``)."""
    if not isinstance(source.get("cnpj_basico"), str) or not source.get("cnpj_basico"):
        return False
    emp = source.get("empresa")
    if isinstance(emp, dict) and emp.get("cnpjBasico"):
        return False
    return True


def _source_geo_point(source: dict) -> dict[str, float] | None:
    """Lê ``location`` (preferencial) ou ``localizacao`` legado."""
    loc = source.get(_GEO_FIELD) or source.get("localizacao")
    if not isinstance(loc, dict):
        return None
    lat, lon = loc.get("lat"), loc.get("lon")
    if lat is None or lon is None:
        return None
    try:
        return {"lat": float(lat), "lon": float(lon)}
    except (TypeError, ValueError):
        return None


def _cnpj_triple_from_flat_source(source: dict) -> tuple[str, str, str]:
    """Extrai (basico, ordem, dv) a partir de ``cnpj_completo`` / ``cnpj_basico``."""
    cc = "".join(c for c in str(source.get("cnpj_completo") or "") if c.isdigit())
    if len(cc) == 14:
        return cc[:8], cc[8:12], cc[12:14]
    basico = str(source.get("cnpj_basico") or "").zfill(8)
    return basico, "0001", "00"


def _format_flat_empresa_hit(hit: dict, incluir_contatos: bool) -> dict[str, Any]:
    """Hit no schema plano ``mr_empresas_v001``."""
    source = hit.get("_source", {})
    sort_values = hit.get("sort", [])

    cnpj_basico, cnpj_ordem, cnpj_dv = _cnpj_triple_from_flat_source(source)

    cnaes_sec_raw = source.get("cnaes_secundarios") or []
    cnaes_secundarios: list[dict] = []
    if isinstance(cnaes_sec_raw, list):
        for c in cnaes_sec_raw:
            if isinstance(c, str) and c.strip():
                cnaes_secundarios.append({"codigo": c.strip(), "descricao": None})
            elif isinstance(c, dict) and c.get("codigo"):
                cnaes_secundarios.append(
                    {"codigo": c["codigo"], "descricao": c.get("descricao")}
                )

    situacao = str(source.get("situacao") or "")
    municipio = extract_municipio_nome(source.get("municipio")) or ""

    raw_capital = source.get("capital_social")
    capital_social = None
    if raw_capital is not None:
        try:
            v = float(raw_capital)
            if v > 0:
                capital_social = round(v, 2)
        except (TypeError, ValueError):
            pass

    porte = (source.get("porte") or None) if source.get("porte") else None

    distancia_km = None
    for sv in sort_values:
        if isinstance(sv, (int, float)) and 0 < sv < 100_000:
            distancia_km = round(sv, 2)
            break

    loc_out = _source_geo_point(source)

    result: dict[str, Any] = {
        "cnpj_basico": cnpj_basico,
        "cnpj_completo": format_cnpj(cnpj_basico, cnpj_ordem, cnpj_dv),
        "razao_social": source.get("razao_social") or "",
        "nome_fantasia": source.get("nome_fantasia") or None,
        "cnae_principal": source.get("cnae_principal"),
        "cnae_descricao": source.get("cnae_desc"),
        "situacao": situacao,
        "uf": source.get("uf", "") or "",
        "municipio": municipio,
        "localizacao": loc_out,
        "distancia_km": distancia_km,
    }

    if cnaes_secundarios:
        result["cnaes_secundarios"] = cnaes_secundarios
    if capital_social is not None:
        result["capital_social"] = capital_social
    if porte:
        result["porte"] = porte

    if incluir_contatos:
        for f in ("telefone", "telefone2", "email"):
            v = source.get(f)
            if v:
                result[f] = v

        logra = (source.get("logradouro") or "").strip()
        num = (source.get("numero") or "").strip()
        comp = (source.get("complemento") or "").strip()
        bairro = (source.get("bairro") or "").strip()
        cep_raw = str(source.get("cep") or "").strip()

        street = ", ".join(p for p in [logra, num, comp, bairro] if p)
        if cep_raw:
            street = f"{street} - CEP {cep_raw}" if street else f"CEP {cep_raw}"
        if street:
            result["endereco"] = street
        if cep_raw:
            result["cep"] = cep_raw

    return result


def _format_legacy_nested_empresa_hit(hit: dict, incluir_contatos: bool) -> dict[str, Any]:
    """Formato legado aninhado (``empresa.*``, ``cnaeFiscalPrincipal``)."""
    source = hit.get("_source", {})
    sort_values = hit.get("sort", [])
    empresa_data = source.get("empresa", {}) or {}

    cnpj_basico = empresa_data.get("cnpjBasico", "")
    cnpj_ordem = source.get("cnpjOrdem", "")
    cnpj_dv = source.get("cnpjDv", "")

    cnae_obj = source.get("cnaeFiscalPrincipal") or {}

    cnaes_sec_raw = source.get("cnaeFiscalSecundaria") or []
    cnaes_secundarios: list[dict] = [
        {"codigo": c.get("codigo"), "descricao": c.get("descricao")}
        for c in (cnaes_sec_raw if isinstance(cnaes_sec_raw, list) else [])
        if c.get("codigo")
    ]

    situacao = (source.get("situacaoCadastral") or {}).get("descricao", "")
    municipio = extract_municipio_nome(source.get("municipio")) or ""

    raw_capital = empresa_data.get("capitalSocial")
    capital_social = round(raw_capital, 2) if raw_capital and raw_capital > 0 else None
    porte_raw = empresa_data.get("porteEmpresa") or ""
    porte = _PORTE_MAP.get(porte_raw, porte_raw) if porte_raw else None

    opcao_mei = source.get("opcaoMEI")
    is_mei: bool | None = (opcao_mei == "S") if opcao_mei in ("S", "N") else None
    opcao_simples = source.get("opcaoSimples")
    is_simples: bool | None = (opcao_simples == "S") if opcao_simples in ("S", "N") else None

    distancia_km = None
    for sv in sort_values:
        if isinstance(sv, (int, float)) and 0 < sv < 100_000:
            distancia_km = round(sv, 2)
            break

    result: dict[str, Any] = {
        "cnpj_basico": cnpj_basico,
        "cnpj_completo": format_cnpj(cnpj_basico, cnpj_ordem, cnpj_dv),
        "razao_social": empresa_data.get("razaoSocial", ""),
        "nome_fantasia": source.get("nomeFantasia") or None,
        "cnae_principal": cnae_obj.get("codigo"),
        "cnae_descricao": cnae_obj.get("descricao"),
        "situacao": situacao,
        "uf": source.get("uf", ""),
        "municipio": municipio,
        "localizacao": _source_geo_point(source),
        "distancia_km": distancia_km,
    }

    if cnaes_secundarios:
        result["cnaes_secundarios"] = cnaes_secundarios

    if capital_social is not None:
        result["capital_social"] = capital_social
    if porte:
        result["porte"] = porte
    if is_mei is not None:
        result["is_mei"] = is_mei
    if is_simples is not None:
        result["is_simples"] = is_simples

    if incluir_contatos:
        contato = build_contato(source)
        if contato.get("telefone"):
            result["telefone"] = contato["telefone"]
        if contato.get("telefone2"):
            result["telefone2"] = contato["telefone2"]
        if contato.get("email"):
            result["email"] = contato["email"]
        if contato.get("endereco"):
            result["endereco"] = contato["endereco"]
        cep_raw = source.get("cep", "")
        if cep_raw:
            result["cep"] = cep_raw

    return result


def _format_empresa_hit(hit: dict, incluir_contatos: bool) -> dict[str, Any]:
    """Formata um hit único (plano ``mr_empresas_v001`` ou legado aninhado)."""
    source = hit.get("_source", {})
    if _is_flat_mr_empresas(source):
        return _format_flat_empresa_hit(hit, incluir_contatos)
    return _format_legacy_nested_empresa_hit(hit, incluir_contatos)


# ==================== Map Points ====================


def extract_mapa_pontos(hits: list[dict]) -> list[dict]:
    """
    Extrai pontos leves para o mapa a partir dos hits de ``mr_empresas_v001``.

    Each point includes: lat, lon, tipo, cnpj_basico, nome, cnae.

    Returns:
        List of MapaPontoEmpresa-compatible dicts
    """
    pontos: list[dict] = []
    for hit in hits:
        ponto = _hit_to_mapa_ponto(hit)
        if ponto:
            pontos.append(ponto)
    return pontos


def _hit_to_mapa_ponto(hit: dict) -> dict[str, Any] | None:
    """Converte um hit em ponto de mapa com todos os campos de exibição do popup."""
    source = hit.get("_source", {})
    loc = _source_geo_point(source)
    if not loc:
        return None

    sort_values = hit.get("sort", [])
    distancia_km: float | None = None
    for sv in sort_values:
        if isinstance(sv, (int, float)) and 0 < sv < 100_000:
            distancia_km = round(sv, 2)
            break

    if _is_flat_mr_empresas(source):
        cnae = source.get("cnae_principal")
        cnae_descricao = source.get("cnae_desc")
        razao_social = source.get("razao_social") or ""
        nome_fantasia = source.get("nome_fantasia") or None
        nome = nome_fantasia or razao_social
        cnpj_basico = str(source.get("cnpj_basico") or "")
        b, o, d = _cnpj_triple_from_flat_source(source)
        cnpj_completo = format_cnpj(b, o, d)
        municipio = extract_municipio_nome(source.get("municipio")) or ""
        uf = source.get("uf", "") or ""
        situacao = str(source.get("situacao") or "")
        porte = source.get("porte") or None
        raw_cap = source.get("capital_social")
        capital_social = None
        if raw_cap is not None:
            try:
                v = float(raw_cap)
                if v > 0:
                    capital_social = round(v, 2)
            except (TypeError, ValueError):
                pass
        telefone = source.get("telefone") or None
        email = source.get("email") or None
        # Build endereço display string
        logra = (source.get("logradouro") or "").strip()
        num = (source.get("numero") or "").strip()
        comp = (source.get("complemento") or "").strip()
        bairro = (source.get("bairro") or "").strip()
        cep_raw = str(source.get("cep") or "").strip()
        street = ", ".join(p for p in [logra, num, comp, bairro] if p)
        if cep_raw:
            street = f"{street} - CEP {cep_raw}" if street else f"CEP {cep_raw}"
        endereco = street or None
    else:
        empresa = source.get("empresa", {}) or {}
        cnae_obj = source.get("cnaeFiscalPrincipal") or {}
        cnae = cnae_obj.get("codigo")
        cnae_descricao = cnae_obj.get("descricao")
        cnpj_basico = empresa.get("cnpjBasico", "")
        cnpj_completo = format_cnpj(
            cnpj_basico,
            source.get("cnpjOrdem", ""),
            source.get("cnpjDv", ""),
        )
        razao_social = empresa.get("razaoSocial", "")
        nome_fantasia = source.get("nomeFantasia") or None
        nome = nome_fantasia or razao_social
        municipio = extract_municipio_nome(source.get("municipio")) or ""
        uf = source.get("uf", "")
        situacao = (source.get("situacaoCadastral") or {}).get("descricao", "")
        porte_raw = empresa.get("porteEmpresa") or ""
        porte = _PORTE_MAP.get(porte_raw, porte_raw) if porte_raw else None
        raw_cap = empresa.get("capitalSocial")
        capital_social = round(raw_cap, 2) if raw_cap and raw_cap > 0 else None
        contato = build_contato(source)
        telefone = contato.get("telefone") or None
        email = contato.get("email") or None
        endereco = contato.get("endereco") or None

    if not cnpj_completo:
        return None

    ponto: dict[str, Any] = {
        "lat": loc["lat"],
        "lon": loc["lon"],
        "tipo": "empresa",
        "cnpj_basico": cnpj_basico,
        "cnpj_completo": cnpj_completo,
        "nome": nome,
        "razao_social": razao_social,
        "cnae": cnae,
    }
    if nome_fantasia:
        ponto["nome_fantasia"] = nome_fantasia
    if cnae_descricao:
        ponto["cnae_descricao"] = cnae_descricao
    if municipio:
        ponto["municipio"] = municipio
    if uf:
        ponto["uf"] = uf
    if situacao:
        ponto["situacao"] = situacao
    if porte:
        ponto["porte"] = porte
    if capital_social is not None:
        ponto["capital_social"] = capital_social
    if telefone:
        ponto["telefone"] = telefone
    if email:
        ponto["email"] = email
    if endereco:
        ponto["endereco"] = endereco
    if distancia_km is not None:
        ponto["distancia_km"] = distancia_km
    return ponto


# ==================== Full Orchestrator ====================


# ==================== Polygon Filter (geo_polygon on geo_point) ====================


def build_empresas_por_poligono_query(
    geometry: dict,
    resolucao: ResolucaoCnae | None = None,
    uf: str | None = None,
    apenas_ativas: bool = False,
    incluir_contatos: bool = True,
    incluir_mei: bool = True,
) -> dict[str, Any]:
    """
    Constrói query ``mr_empresas_v001`` que filtra por polígono GeoJSON.

    Estrutura idêntica a ``build_empresas_query``, exceto:
      • Sem geo_distance — substituído por ``geo_bounding_box`` (estágio 1)
      • Sem ordenação por distância — usa _score (relevância CNAE)
    """
    if not incluir_mei:
        logger.debug(
            "build_empresas_por_poligono_query: incluir_mei=False ignorado — "
            "índice sem campo opcaoMEI"
        )

    must_clauses: list[dict] = []
    filter_clauses: list[dict] = []

    if resolucao is not None and resolucao.encontrou:
        codigos = resolucao.codigos
        must_clauses.append({
            "bool": {
                "should": [
                    {"terms": {
                        "cnae_principal": codigos,
                        "boost": 2.0,
                    }},
                    {"terms": {
                        "cnaes_secundarios": codigos,
                        "boost": 1.0,
                    }},
                ],
                "minimum_should_match": 1,
            }
        })

    # ── Polygon filter (substitui geo_distance) ──
    from mcp_servers.common.geo_filters import geojson_to_geo_polygon_filter
    filter_clauses.append(geojson_to_geo_polygon_filter(geometry, field=_GEO_FIELD))

    if apenas_ativas:
        filter_clauses.append({"term": {"situacao": "Ativa"}})
    if uf:
        filter_clauses.append({"term": {"uf": uf.strip().upper()}})

    source_fields = list(BASE_SOURCE_FIELDS)
    if incluir_contatos:
        source_fields.extend(CONTATO_SOURCE_FIELDS)

    return {
        "size": MAX_RESULTS,
        "query": {
            "bool": {
                "must": must_clauses,
                "filter": filter_clauses,
            }
        },
        "_source": source_fields,
    }


async def executar_busca_empresas_por_poligono(
    os_service: OpenSearchService,
    geometry: dict,
    resolucao: ResolucaoCnae | None = None,
    uf: str | None = None,
    apenas_ativas: bool = False,
    incluir_contatos: bool = True,
    incluir_geometria: bool = False,
    incluir_mei: bool = True,
) -> dict[str, Any]:
    """
    Executa a query de empresas dentro de polígono.

    Mesmo formato de retorno de ``executar_busca_empresas`` (total + resultados +
    mapa). Sem campo distancia_km nos resultados (não há referência radial).
    """
    query = build_empresas_por_poligono_query(
        geometry=geometry,
        resolucao=resolucao,
        uf=uf,
        apenas_ativas=apenas_ativas,
        incluir_contatos=incluir_contatos,
        incluir_mei=incluir_mei,
    )

    geom_type = geometry.get("type", "?")
    cnaes = resolucao.codigos[:5] if (resolucao and resolucao.encontrou) else []
    logger.info(
        f"Querying {INDEX_CNPJ} (polygon): "
        f"geom={geom_type}, cnaes={cnaes}, "
        f"uf={uf}, ativas={apenas_ativas}, mei={incluir_mei}"
    )

    result = await os_service.search_with_meta(INDEX_CNPJ, query)
    hits = result.get("hits", [])

    logger.info(
        f"Found {result.get('total', 0)} empresas in bounding_box "
        f"({len(hits)} fetched) — applying PIP filter"
    )

    # Estágio 2: PIP exato — elimina falsos-positivos do bounding box
    from mcp_servers.common.geo_filters import filter_hits_by_polygon
    hits = filter_hits_by_polygon(hits, geometry, localizacao_field=_GEO_FIELD)
    total = len(hits)

    if not hits:
        return {
            "total": 0,
            "resultados": [],
            "mapa": {"pontos": [], "total_pontos": 0},
        }

    resultados = format_empresas_results(hits, incluir_contatos=incluir_contatos)

    # Snapshot of original (pre-geocode) coordinates — used for PIP revert below
    pre_geocode_locs: dict[str, dict] = {
        r["cnpj_completo"]: dict(r["localizacao"])
        for r in resultados
        if r.get("cnpj_completo") and isinstance(r.get("localizacao"), dict)
    }

    mapa = await _build_mapa(
        os_service=os_service,
        hits=hits,
        incluir_geometria=incluir_geometria,
    )
    await _geocode_empresa_addresses(resultados, mapa.get("pontos", []))

    # Post-geocode PIP guard: geocoding can move a pin slightly outside the
    # polygon boundary. Revert any resultado whose coordinates shifted outside.
    from mcp_servers.common.geo_filters import _point_in_geometry
    reverted = 0
    for r in resultados:
        loc = r.get("localizacao")
        if not isinstance(loc, dict):
            continue
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat is None or lon is None:
            continue
        if not _point_in_geometry(float(lat), float(lon), geometry):
            orig = pre_geocode_locs.get(r.get("cnpj_completo", ""))
            if orig:
                r["localizacao"] = orig
                reverted += 1

    if reverted:
        logger.info(
            "executar_busca_empresas_por_poligono: "
            "reverted %d/%d geocoded coords that drifted outside polygon",
            reverted, len(resultados),
        )

    return {
        "total": total,
        "resultados": resultados,
        "mapa": mapa,
    }


async def executar_busca_empresas(
    os_service: OpenSearchService,
    resolucao: ResolucaoCnae,
    latitude: float | None = None,
    longitude: float | None = None,
    raio_km: float = 30.0,
    uf: str | None = None,
    apenas_ativas: bool = False,
    incluir_contatos: bool = True,
    incluir_geometria: bool = False,
    ordenar_por: str = "distancia",
    incluir_mei: bool = True,
) -> dict[str, Any]:
    """
    Execute the complete buscar_empresas query flow (step 2).

    This is called from ``tools.py`` after the CNAE resolution step.

    Args:
        os_service: OpenSearch async client
        resolucao: Resolved CNAE codes (from CnaeResolver)
        latitude: Optional latitude for proximity search
        longitude: Optional longitude for proximity search
        raio_km: Search radius in km (default: 30)
        uf: Optional UF filter
        apenas_ativas: Only active companies
        incluir_contatos: Include contact data
        incluir_geometria: Include municipality boundary polygons for map

    Returns:
        {
            "total": int,               # Total matches in rfb_cnpj_v003
            "resultados": [...],        # Formatted empresa dicts (max 200)
            "mapa": {
                "pontos": [...],        # Lightweight map points
                "total_pontos": int,
                "geometrias_municipios": {...},      # GeoJSON (if requested)
                "total_geometrias_municipios": int,
            },
        }
    """
    # ── Build and execute query ──
    query = build_empresas_query(
        resolucao=resolucao,
        latitude=latitude,
        longitude=longitude,
        raio_km=raio_km,
        uf=uf,
        apenas_ativas=apenas_ativas,
        incluir_contatos=incluir_contatos,
        ordenar_por=ordenar_por,
        incluir_mei=incluir_mei,
    )

    logger.info(
        f"Querying {INDEX_CNPJ}: "
        f"cnaes={resolucao.codigos[:5]}, "
        f"geo=({latitude},{longitude}), raio={raio_km}km, "
        f"uf={uf}, ativas={apenas_ativas}, mei={incluir_mei}"
    )

    result = await os_service.search_with_meta(INDEX_CNPJ, query)
    total = result.get("total", 0)
    hits = result.get("hits", [])

    logger.info(f"Found {total} empresas ({len(hits)} fetched)")

    if not hits:
        return {
            "total": 0,
            "resultados": [],
            "mapa": {"pontos": [], "total_pontos": 0},
        }

    # ── Format results ──
    resultados = format_empresas_results(hits, incluir_contatos=incluir_contatos)

    # ── Build map data ──
    mapa = await _build_mapa(
        os_service=os_service,
        hits=hits,
        incluir_geometria=incluir_geometria,
    )

    # ── Geocode addresses → precise coordinates ──
    await _geocode_empresa_addresses(resultados, mapa.get("pontos", []))

    return {
        "total": total,
        "resultados": resultados,
        "mapa": mapa,
    }


# ==================== Map Building ====================


async def _build_mapa(
    os_service: OpenSearchService,
    hits: list[dict],
    incluir_geometria: bool = False,
) -> dict[str, Any]:
    """
    Build map response for empresa search results.

    Always includes:
        - ``pontos``: lightweight pin points for all results

    When ``incluir_geometria=True``:
        - ``geometrias_municipios``: GeoJSON FeatureCollection with
          municipality boundaries (from ibge_municipio_v001)
    """
    pontos = extract_mapa_pontos(hits)

    response: dict[str, Any] = {
        "pontos": pontos,
        "total_pontos": len(pontos),
    }

    if incluir_geometria and hits:
        municipios_features = await _fetch_municipio_boundaries(os_service, hits)
        response["geometrias_municipios"] = {
            "type": "FeatureCollection",
            "features": municipios_features,
        }
        response["total_geometrias_municipios"] = len(municipios_features)

    return response


async def _fetch_municipio_boundaries(
    os_service: OpenSearchService,
    hits: list[dict],
) -> list[dict]:
    """
    Fetch municipality boundary polygons for the empresa results.

    Reuses the shared ``fetch_municipios_precise`` from the Jazidas module.
    """
    try:
        from mcp_servers.jazidas.queries.municipio import fetch_municipios_precise

        pairs = _extract_municipio_uf_pairs(hits)
        if pairs:
            features = await fetch_municipios_precise(os_service, pairs)
            logger.info(
                f"Fetched {len(features)} municipality boundaries "
                f"from {len(pairs)} unique pairs"
            )
            return features
    except Exception as e:
        logger.warning(f"Failed to fetch municipality boundaries: {e}")

    return []


def _extract_municipio_uf_pairs(hits: list[dict]) -> list[tuple[str, str]]:
    """
    Extract unique (nome_municipio, sigla_uf) pairs from rfb_cnpj_v003 hits.

    rfb_cnpj_v003 uses IBGE enrichment: ``municipio.nome`` (text).
    """
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []

    for hit in hits:
        source = hit.get("_source", {})
        mun = extract_municipio_nome(source.get("municipio")) or ""
        uf = source.get("uf", "")
        if mun and uf:
            key = (mun.lower().strip(), uf.lower().strip())
            if key not in seen:
                seen.add(key)
                pairs.append(key)

    return pairs[:MAX_MUNICIPIOS]


# ==================== Address Geocoding ====================


async def _geocode_empresa_addresses(
    resultados: list[dict],
    pontos: list[dict],
) -> None:
    """
    Geocode empresa addresses via Azure Maps and update lat/lon in-place.

    For each resultado that has an ``endereco`` field, sends it to the
    batch geocoder. The returned precise coordinates replace the original
    municipality centroid in both ``resultados[].localizacao`` and the
    corresponding ``pontos[]`` entry (matched by cnpj_completo).

    Non-blocking: failures are logged and the original coordinates are kept.
    """
    import re as _re
    from mcp_servers.common.geocoder import batch_geocode

    addresses: dict[str, str] = {}
    for r in resultados:
        endereco = r.get("endereco")
        cnpj = r.get("cnpj_completo", "")
        if not endereco or not cnpj:
            continue
        municipio = r.get("municipio", "")
        uf = r.get("uf", "")
        cep = r.get("cep", "")

        # Strip the "- CEP XXXXX" suffix from the display address so the CEP
        # is added as a clean, standalone token at the end — Azure Maps scores
        # CEP as a high-confidence anchor and a textual label ("- CEP") reduces
        # that weight. Final format: "LOGRADOURO, NUM, BAIRRO, CIDADE, UF, CEP, Brasil"
        endereco_clean = _re.sub(r"\s*-\s*CEP\s+[\d\-]+", "", endereco).strip().rstrip(",")

        full_addr = ", ".join(filter(None, [endereco_clean, municipio, uf, cep, "Brasil"]))
        addresses[cnpj] = full_addr

    if not addresses:
        logger.info("geocode_empresas: no addresses to geocode")
        return

    # Log a sample of addresses being sent so we can validate quality
    sample = list(addresses.values())[:3]
    for s in sample:
        logger.info("geocode_empresas sample address → Azure Maps: '%s'", s)

    try:
        geocoded = await batch_geocode(addresses)
    except Exception as e:
        logger.warning("geocode_empresas: batch_geocode failed (non-blocking): %s", e)
        return

    if not geocoded:
        logger.info("geocode_empresas: no addresses resolved")
        return

    # Build a lookup of original coordinates from pontos so we can
    # validate geocoded drift before accepting the new position.
    orig_coords: dict[str, tuple[float, float]] = {}
    for ponto in pontos:
        cnpj = ponto.get("cnpj_completo", "")
        lat = ponto.get("lat")
        lon = ponto.get("lon")
        if cnpj and lat is not None and lon is not None:
            orig_coords[cnpj] = (float(lat), float(lon))

    updated = 0
    skipped_drift = 0
    for ponto in pontos:
        cnpj = ponto.get("cnpj_completo", "")
        coords = geocoded.get(cnpj)
        if not coords:
            continue
        orig = orig_coords.get(cnpj)
        if orig is not None:
            drift_km = _haversine_km(orig[0], orig[1], coords[0], coords[1])
            if drift_km > _MAX_GEOCODE_DRIFT_KM:
                logger.warning(
                    "geocode_empresas: skipping geocode for %s — "
                    "drift %.1f km exceeds %d km threshold",
                    cnpj, drift_km, _MAX_GEOCODE_DRIFT_KM,
                )
                skipped_drift += 1
                continue
        ponto["lat"] = coords[0]
        ponto["lon"] = coords[1]
        updated += 1

    for r in resultados:
        cnpj = r.get("cnpj_completo", "")
        coords = geocoded.get(cnpj)
        if not coords:
            continue
        orig = orig_coords.get(cnpj)
        if orig is not None:
            drift_km = _haversine_km(orig[0], orig[1], coords[0], coords[1])
            if drift_km > _MAX_GEOCODE_DRIFT_KM:
                continue
        r["localizacao"] = {"lat": coords[0], "lon": coords[1]}

    logger.info(
        "geocode_empresas: updated %d/%d pontos (%d skipped — drift > %d km), "
        "%d/%d resultados geocoded",
        updated, len(pontos), skipped_drift, _MAX_GEOCODE_DRIFT_KM,
        len(geocoded), len(addresses),
    )


