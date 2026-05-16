"""
Fornecedores Query Module
==========================

Orchestrates the 3-step cross-index flow for ``buscar_fornecedores``:

    Passo 1 → Resolve substance term → nomes normalizados (SubstanciaResolver, already done)
    Passo 2 → Search mr_jazidas_v001 (substance names + geo + filters)
    Passo 3 → Batch lookup mr_empresas_v001 → contacts, partners

Indices:
    - mr_substancias_v001 / mr_tipo_uso_v001 (step 1, via SubstanciaResolver)
    - mr_jazidas_v001 (step 2) — snake_case schema (bot_anm_direto)
    - mr_empresas_v001 (step 3)

Field mapping (mr_jazidas_v001):
    numero_processo, ativo, area_ha, dt_requerimento, fase,
    substancias_desc (keyword[]), uf, municipio, location (geo_point),
    titular {nome, razao_social, cnpj_basico, situacao_rfb, cnae_principal}
"""

import logging
from typing import Any

from mcp_servers.common.formatters import format_cnpj
from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.jazidas.queries.geo import (
    SHAPES_SOURCE_FULL,
    SHAPES_SOURCE_MINIMAL,
    build_mapa_response,
    build_mapa_response_with_municipios,
)
from mcp_servers.jazidas.schemas import ResolucaoSubstancia

logger = logging.getLogger("mcp.jazidas.fornecedores")

# ==================== Constants ====================

INDEX_ANM = "mr_jazidas_v001"
INDEX_CNPJ = "mr_empresas_v001"

# Max results to fetch from anm_v003 in a single query
MAX_ANM_RESULTS = 200

# Max CNPJs to lookup in a single msearch
MAX_CNPJ_BATCH = 50

# Fields to retrieve from mr_jazidas_v001 (step 2)
ANM_SOURCE_FIELDS = [
    "numero_processo",
    "ativo",
    "area_ha",
    "dt_requerimento",
    "fase",
    "substancias_desc",
    "uf",
    "municipio",
    "location",
    "titular",
]

# Fields to retrieve from rfb_cnpj_v003 (step 3) — without sócios
CNPJ_SOURCE_FIELDS = [
    "empresa.cnpjBasico",
    "empresa.razaoSocial",
    "empresa.capitalSocial",
    "cnpjOrdem",
    "cnpjDv",
    "nomeFantasia",
    "ddd1",
    "telefone1",
    "ddd2",
    "telefone2",
    "correioEletronico",
    "tipoLogradouro",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "uf",
    "situacaoCadastral.descricao",
]

# Fields with sócios (when incluir_socios=True)
CNPJ_SOCIOS_FIELDS = CNPJ_SOURCE_FIELDS + [
    "socios.nomeSocioRazaoSocial",
    "socios.qualificacaoSocio.descricao",
]


# ==================== Step 2: anm_v003 Query ====================


def build_anm_query(
    resolucao: ResolucaoSubstancia,
    latitude: float,
    longitude: float,
    raio_km: float,
    uf: str | None = None,
    fase: str | None = None,
    apenas_ativos: bool = False,
    incluir_geometria: bool = False,
) -> dict[str, Any]:
    """
    Build the anm_v003 bool query for step 2.

    Combines:
        - terms filter on idSubstancias/idTipoUsoSubstancias (from step 1)
        - geo_distance on root localizacao
        - Optional filters: btAtivo, siglasUF, faseProcesso

    Sort: by geo_distance ascending (nearest first).

    When ``incluir_geometria=True``, also fetches nested shapes data
    (polygon geometries for map rendering).
    """
    must_clauses: list[dict] = []
    filter_clauses: list[dict] = []

    # Substance/tipo-uso filter (from resolution)
    if resolucao.encontrou:
        filter_clauses.append(
            {"terms": {resolucao.campo_filter: resolucao.ids_filter}}
        )

    # Geo distance filter
    must_clauses.append(
        {
            "geo_distance": {
                "distance": f"{raio_km}km",
                "location": {"lat": latitude, "lon": longitude},
            }
        }
    )

    # Active processes only
    if apenas_ativos:
        filter_clauses.append({"term": {"ativo": True}})

    # UF filter
    if uf:
        filter_clauses.append({"term": {"uf": uf.upper()}})

    # Phase filter (text match for flexibility: "Concessão de Lavra" etc.)
    if fase:
        filter_clauses.append({"match": {"fase": fase}})

    # _source: always fetch base fields; add shapes if geometry requested
    source_fields = list(ANM_SOURCE_FIELDS)  # copy
    if incluir_geometria:
        source_fields.extend(SHAPES_SOURCE_FULL)
    else:
        # Always fetch minimal shapes (centroid per polygon)
        source_fields.extend(SHAPES_SOURCE_MINIMAL)

    query: dict[str, Any] = {
        "size": MAX_ANM_RESULTS,
        "query": {
            "bool": {
                "must": must_clauses,
                "filter": filter_clauses,
            }
        },
        "_source": source_fields,
        "sort": [
            {
                "_geo_distance": {
                    "location": {"lat": latitude, "lon": longitude},
                    "order": "asc",
                    "unit": "km",
                }
            }
        ],
    }

    return query


# ==================== Step 2 → 3: Extract CNPJs ====================


def _normalize_cnpj_basico(raw_cnpj: str) -> str:
    """
    Normalize a CNPJ to its 8-digit basic form.

    ANM stores cnpjTitulares in varying formats:
      - 8 digits: "08608142"           → already basic
      - 14 digits: "08608142000160"    → take first 8
      - Formatted: "08.608.142/0001-60" → strip non-digits, take first 8

    rfb_cnpj_v003 indexes empresa.cnpjBasico as 8 digits.
    """
    digits = "".join(ch for ch in raw_cnpj if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else digits


def extract_cnpjs(hits: list[dict]) -> list[str]:
    """
    Extract unique cnpj_basico values from mr_jazidas_v001 hits.

    The ``titular.cnpj_basico`` field already stores the 8-digit basic CNPJ
    enriched by bot_anm_direto from mr_empresas_v001.
    """
    seen: set[str] = set()
    cnpjs: list[str] = []
    for hit in hits:
        source = hit.get("_source", {})
        titular = source.get("titular") or {}
        cnpj = titular.get("cnpj_basico")
        if cnpj and cnpj not in seen:
            seen.add(cnpj)
            cnpjs.append(cnpj)
    return cnpjs


# ==================== Step 3: rfb_cnpj_v003 Batch Lookup ====================


def build_cnpj_msearch(
    cnpjs: list[str],
    incluir_socios: bool = False,
) -> list[dict]:
    """
    Build an msearch body for batch CNPJ lookup.

    Returns list of alternating header/body dicts for OpenSearch msearch API.
    Each pair: {"index": "rfb_cnpj_v003"} + {"query": ..., "_source": ...}
    """
    source_fields = CNPJ_SOCIOS_FIELDS if incluir_socios else CNPJ_SOURCE_FIELDS

    body: list[dict] = []
    for cnpj in cnpjs[:MAX_CNPJ_BATCH]:
        # Header
        body.append({"index": INDEX_CNPJ})
        # Body
        body.append(
            {
                "size": 1,
                "query": {"term": {"empresa.cnpjBasico": cnpj}},
                "_source": source_fields,
            }
        )
    return body


def parse_cnpj_results(
    msearch_response: dict,
    cnpjs: list[str],
) -> dict[str, dict]:
    """
    Parse msearch response into a dict: cnpjBasico → formatted company data.

    Args:
        msearch_response: Raw OpenSearch msearch response
        cnpjs: List of CNPJ codes (same order as msearch queries)

    Returns:
        Dict mapping cnpjBasico → empresa data dict
    """
    empresas: dict[str, dict] = {}
    responses = msearch_response.get("responses", [])

    for i, resp in enumerate(responses):
        if i >= len(cnpjs):
            break
        cnpj = cnpjs[i]
        hits = resp.get("hits", {}).get("hits", [])
        if hits:
            source = hits[0].get("_source", {})
            empresas[cnpj] = _format_empresa(source)

    return empresas


def _format_empresa(source: dict) -> dict:
    """Format rfb_cnpj_v003 source into a clean, flat company dict."""
    empresa_data = source.get("empresa", {})

    telefone = _build_telefone(source)
    endereco = _build_endereco(source)
    socios = _extract_socios(source)

    cnpj_basico = empresa_data.get("cnpjBasico", "")
    cnpj_ordem = source.get("cnpjOrdem", "")
    cnpj_dv = source.get("cnpjDv", "")

    return {
        "razao_social": empresa_data.get("razaoSocial", ""),
        "cnpj_basico": cnpj_basico,
        "cnpj_completo": format_cnpj(cnpj_basico, cnpj_ordem, cnpj_dv),
        "cnpj_ordem": cnpj_ordem,
        "nome_fantasia": source.get("nomeFantasia", ""),
        "capital_social": empresa_data.get("capitalSocial"),
        "situacao_rfb": (source.get("situacaoCadastral") or {}).get("descricao", ""),
        "contato": {
            "telefone": telefone,
            "email": source.get("correioEletronico"),
            "endereco": endereco or None,
        },
        "socios": socios or None,
    }


def _build_telefone(source: dict) -> str | None:
    """Build phone string: '(11) 25428111'."""
    ddd1 = source.get("ddd1", "")
    tel1 = source.get("telefone1", "")
    if ddd1 and tel1:
        return f"({ddd1}) {tel1}"
    return tel1 or None


def _build_endereco(source: dict) -> str:
    """Build display address: 'AVENIDA PAULISTA, 671, BELA VISTA, CIDADE, UF, CEP 01000-000'."""
    parts = []
    tipo = str(source.get("tipoLogradouro") or "").strip()
    logradouro = str(source.get("logradouro") or "").strip()
    if tipo and logradouro:
        parts.append(f"{tipo} {logradouro}")
    elif logradouro:
        parts.append(logradouro)
    elif tipo:
        parts.append(tipo)
    for campo in ["numero", "complemento", "bairro"]:
        val = source.get(campo)
        if val and str(val).strip():
            parts.append(str(val).strip())
    municipio_raw = source.get("municipio")
    municipio = ""
    if isinstance(municipio_raw, dict):
        municipio = municipio_raw.get("nome", "")
    elif isinstance(municipio_raw, str):
        municipio = municipio_raw
    uf = source.get("uf", "")
    cep = source.get("cep", "")
    for val in [municipio, uf]:
        if val and str(val).strip():
            parts.append(str(val).strip())
    if cep:
        parts.append(f"CEP {cep}")
    return ", ".join(parts)


def _extract_socios(source: dict) -> list[str]:
    """Extract partner names from nested socios field."""
    return [
        socio["nomeSocioRazaoSocial"]
        for socio in source.get("socios", [])
        if socio.get("nomeSocioRazaoSocial")
    ]


# ==================== Merge: Process + Empresa ====================


def merge_processos_empresas(
    hits: list[dict],
    empresas: dict[str, dict],
    incluir_contatos: bool = True,
    incluir_socios: bool = False,
) -> list[dict]:
    """
    Merge anm_v003 process hits with rfb_cnpj_v003 company data.

    Returns a list of combined fornecedor dicts, one per process.
    Each dict includes: processo, contato (optional), socios (optional).
    """
    return [
        _build_fornecedor(hit, empresas, incluir_contatos, incluir_socios)
        for hit in hits
    ]


def _build_fornecedor(
    hit: dict,
    empresas: dict[str, dict],
    incluir_contatos: bool,
    incluir_socios: bool,
) -> dict[str, Any]:
    """Build a single fornecedor dict from a mr_jazidas_v001 hit + empresa data."""
    source = hit.get("_source", {})
    sort_values = hit.get("sort", [])

    # Distance from geo_distance sort (first sort value = km)
    distancia_km = sort_values[0] if sort_values else None

    # Titular object embedded in the jazidas document
    titular_obj = source.get("titular") or {}
    titular_cnpj = titular_obj.get("cnpj_basico")
    titular_nome = titular_obj.get("nome") or titular_obj.get("razao_social")

    # -- Base process data --
    processo = _build_processo_data(source, distancia_km)

    # -- Titular + empresa enrichment --
    titular, contato, socios = _build_titular_data(
        titular_nome, titular_cnpj, empresas, incluir_contatos, incluir_socios
    )
    processo["titular"] = titular

    # -- Final result --
    resultado: dict[str, Any] = {"processo": processo}
    if incluir_contatos:
        resultado["contato"] = contato
    if incluir_socios:
        resultado["socios"] = socios
    return resultado


def _build_processo_data(source: dict, distancia_km: float | None) -> dict[str, Any]:
    """Extract and format base process data from mr_jazidas_v001 _source."""
    municipio = source.get("municipio")
    uf = source.get("uf")
    return {
        "ds_processo": source.get("numero_processo", ""),
        "fase": source.get("fase"),
        "area_ha": source.get("area_ha"),
        "ativo": bool(source.get("ativo", False)),
        "substancias": _ensure_list(source.get("substancias_desc")),
        "tipos_uso": [],
        "municipios": [municipio] if municipio else [],
        "uf": [uf] if uf else [],
        "localizacao": source.get("location"),
        "distancia_km": round(distancia_km, 2) if distancia_km is not None else None,
    }


def _build_titular_data(
    nome: str | None,
    cnpj: str | None,
    empresas: dict[str, dict],
    incluir_contatos: bool,
    incluir_socios: bool,
) -> tuple[dict[str, Any], dict | None, list | None]:
    """Build titular dict and optionally extract contato/socios from empresa data."""
    titular: dict[str, Any] = {"nome": nome, "cnpj_basico": cnpj}
    contato = None
    socios = None

    if cnpj and cnpj in empresas:
        emp = empresas[cnpj]
        titular["razao_social"] = emp.get("razao_social")
        titular["situacao_rfb"] = emp.get("situacao_rfb")
        if incluir_contatos:
            contato = emp.get("contato")
        if incluir_socios:
            socios = emp.get("socios")

    return titular, contato, socios


# ==================== Polygon Variant (Step 2 alt) ====================


def build_anm_query_polygon(
    resolucao: ResolucaoSubstancia,
    geometry: dict[str, Any],
    uf: str | None = None,
    fase: str | None = None,
    apenas_ativos: bool = False,
    incluir_geometria: bool = False,
) -> dict[str, Any]:
    """
    Variante de ``build_anm_query`` que filtra por polígono GeoJSON em vez
    de raio circular (``geo_distance``). Usada quando o usuário pede
    fornecedores DENTRO de uma isócrona ou polígono específico.

    Sem ordenação por distância (não há referência radial); usa _score
    para priorizar relevância da substância.
    """
    from mcp_servers.common.geo_filters import geojson_to_geo_polygon_filter

    must_clauses: list[dict] = []
    filter_clauses: list[dict] = []

    if resolucao.encontrou:
        filter_clauses.append(
            {"terms": {resolucao.campo_filter: resolucao.ids_filter}}
        )

    filter_clauses.append(
        geojson_to_geo_polygon_filter(geometry, field="location")
    )

    if apenas_ativos:
        filter_clauses.append({"term": {"ativo": True}})
    if uf:
        filter_clauses.append({"term": {"uf": uf.upper()}})
    if fase:
        filter_clauses.append({"match": {"fase": fase}})

    source_fields = list(ANM_SOURCE_FIELDS)
    if incluir_geometria:
        source_fields.extend(SHAPES_SOURCE_FULL)
    else:
        source_fields.extend(SHAPES_SOURCE_MINIMAL)

    return {
        "size": MAX_ANM_RESULTS,
        "query": {
            "bool": {
                "must": must_clauses,
                "filter": filter_clauses,
            }
        },
        "_source": source_fields,
    }


# ==================== Full Orchestrator (Polygon) ====================


async def executar_busca_fornecedores_por_poligono(
    os_service: OpenSearchService,
    resolucao: ResolucaoSubstancia,
    geometry: dict[str, Any],
    uf: str | None = None,
    fase: str | None = None,
    apenas_ativos: bool = False,
    incluir_contatos: bool = True,
    incluir_socios: bool = False,
    incluir_geometria: bool = False,
) -> dict[str, Any]:
    """
    Mesma orquestração 3-step de ``executar_busca_fornecedores``, mas com
    filtro por polígono no Passo 2 (anm_v003) — substitui ``geo_distance``
    por ``geo_polygon``. Mantém o cross-walk com rfb_cnpj_v003 para
    contatos/sócios e a geração de mapa.

    Retorna o mesmo shape de ``executar_busca_fornecedores``, sem
    ``distancia_km`` nos resultados (não há referência radial).
    """
    query = build_anm_query_polygon(
        resolucao=resolucao,
        geometry=geometry,
        uf=uf,
        fase=fase,
        apenas_ativos=apenas_ativos,
        incluir_geometria=incluir_geometria,
    )

    geom_type = geometry.get("type", "?")
    logger.info(
        f"Step 2 (polygon): Querying {INDEX_ANM} — "
        f"substances={resolucao.ids_filter[:5]}, geom={geom_type}, "
        f"geometry={'on' if incluir_geometria else 'off'}"
    )
    anm_result = await os_service.search_with_meta(INDEX_ANM, query)
    hits = anm_result.get("hits", [])
    logger.info(
        f"Step 2 (polygon): Found {anm_result.get('total', 0)} processes "
        f"in bounding_box ({len(hits)} fetched) — applying PIP filter"
    )

    # Estágio 2: PIP exato — elimina falsos-positivos do bounding box
    from mcp_servers.common.geo_filters import filter_hits_by_polygon
    hits = filter_hits_by_polygon(hits, geometry)
    total = len(hits)
    logger.info(f"Step 2 (polygon): {total} processes after PIP filter")

    if not hits:
        return {
            "total": 0,
            "total_cnpjs": 0,
            "resultados": [],
            "mapa": build_mapa_response([], incluir_geometria=incluir_geometria),
            "resolucao": {
                "metodo": resolucao.metodo,
                "ids": resolucao.ids_filter,
                "termo": resolucao.termo_original,
            },
        }

    cnpjs = extract_cnpjs(hits)
    logger.info(
        f"Step 2→3 (polygon): Extracted {len(cnpjs)} unique CNPJs "
        f"from {len(hits)} processes"
    )

    empresas: dict[str, dict] = {}
    if cnpjs and (incluir_contatos or incluir_socios):
        msearch_body = build_cnpj_msearch(cnpjs, incluir_socios=incluir_socios)
        msearch_response = await os_service.msearch(msearch_body)
        empresas = parse_cnpj_results(msearch_response, cnpjs[:MAX_CNPJ_BATCH])
        logger.info(
            f"Step 3 (polygon): Enriched {len(empresas)}/{len(cnpjs)} "
            f"companies from {INDEX_CNPJ}"
        )

    resultados = merge_processos_empresas(
        hits=hits,
        empresas=empresas,
        incluir_contatos=incluir_contatos,
        incluir_socios=incluir_socios,
    )

    mapa = await build_mapa_response_with_municipios(
        os_service=os_service,
        hits=hits,
        incluir_geometria=incluir_geometria,
    )

    return {
        "total": total,
        "total_cnpjs": len(cnpjs),
        "resultados": resultados,
        "mapa": mapa,
        "resolucao": {
            "metodo": resolucao.metodo,
            "ids": resolucao.ids_filter,
            "termo": resolucao.termo_original,
        },
    }


# ==================== Full Orchestrator ====================


async def executar_busca_fornecedores(
    os_service: OpenSearchService,
    resolucao: ResolucaoSubstancia,
    latitude: float,
    longitude: float,
    raio_km: float,
    uf: str | None = None,
    fase: str | None = None,
    apenas_ativos: bool = False,
    incluir_contatos: bool = True,
    incluir_socios: bool = False,
    incluir_geometria: bool = False,
) -> dict[str, Any]:
    """
    Execute the complete 3-step cross-index flow.

    Args:
        incluir_geometria: If True, fetches polygon shapes from anm_v003
            and returns GeoJSON FeatureCollection in the ``mapa`` field.
            Heavier response (~5-50 KB per polygon), use for map rendering.

    Returns:
        {
            "total": int,           # Total processes found in anm_v003
            "total_cnpjs": int,     # Unique CNPJs extracted
            "resultados": [...],    # Merged fornecedor dicts
            "mapa": {               # Map data (points + optional geometry)
                "pontos": [...],
                "total_pontos": int,
                "geometrias_jazidas": {...},      # Mining polygons (if requested)
                "total_geometrias_jazidas": int,
                "geometrias_municipios": {...},    # Municipality boundaries (if requested)
                "total_geometrias_municipios": int,
            },
            "resolucao": {...},     # Substance resolution metadata
        }
    """
    # ── Step 2: Search anm_v003 ──
    query = build_anm_query(
        resolucao=resolucao,
        latitude=latitude,
        longitude=longitude,
        raio_km=raio_km,
        uf=uf,
        fase=fase,
        apenas_ativos=apenas_ativos,
        incluir_geometria=incluir_geometria,
    )

    logger.info(
        f"Step 2: Querying {INDEX_ANM} — "
        f"substances={resolucao.ids_filter[:5]}, "
        f"geo=({latitude},{longitude}), raio={raio_km}km, "
        f"geometry={'on' if incluir_geometria else 'off'}"
    )
    anm_result = await os_service.search_with_meta(INDEX_ANM, query)
    total = anm_result.get("total", 0)
    hits = anm_result.get("hits", [])

    logger.info(f"Step 2: Found {total} processes ({len(hits)} fetched)")

    if not hits:
        return {
            "total": 0,
            "total_cnpjs": 0,
            "resultados": [],
            "mapa": build_mapa_response([], incluir_geometria=incluir_geometria),
            "resolucao": {
                "metodo": resolucao.metodo,
                "ids": resolucao.ids_filter,
                "termo": resolucao.termo_original,
            },
        }

    # ── Step 2→3: Extract unique CNPJs ──
    cnpjs = extract_cnpjs(hits)
    logger.info(f"Step 2→3: Extracted {len(cnpjs)} unique CNPJs from {len(hits)} processes")

    # ── Step 3: Batch lookup in rfb_cnpj_v003 ──
    empresas: dict[str, dict] = {}
    if cnpjs and (incluir_contatos or incluir_socios):
        msearch_body = build_cnpj_msearch(cnpjs, incluir_socios=incluir_socios)
        msearch_response = await os_service.msearch(msearch_body)
        empresas = parse_cnpj_results(msearch_response, cnpjs[:MAX_CNPJ_BATCH])
        logger.info(f"Step 3: Enriched {len(empresas)}/{len(cnpjs)} companies from {INDEX_CNPJ}")

    # ── Merge process + empresa data ──
    resultados = merge_processos_empresas(
        hits=hits,
        empresas=empresas,
        incluir_contatos=incluir_contatos,
        incluir_socios=incluir_socios,
    )

    # ── Map data (points for all + geometry optional + município boundaries) ──
    mapa = await build_mapa_response_with_municipios(
        os_service=os_service,
        hits=hits,
        incluir_geometria=incluir_geometria,
    )

    return {
        "total": total,
        "total_cnpjs": len(cnpjs),
        "resultados": resultados,
        "mapa": mapa,
        "resolucao": {
            "metodo": resolucao.metodo,
            "ids": resolucao.ids_filter,
            "termo": resolucao.termo_original,
        },
    }


# ==================== Helpers ====================


def _ensure_list(val: Any) -> list:
    """Ensure a value is a list (handles single string vs array from OpenSearch)."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]
