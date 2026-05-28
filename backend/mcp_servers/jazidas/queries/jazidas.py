"""
Jazidas Query Module
=====================

Orchestrates the search flow for ``buscar_jazidas`` against mr_jazidas_v001.

Schema mr_jazidas_v001 (key fields):
    numero_processo  → str  (e.g. "123/2020")
    ativo            → bool
    fase             → keyword (normalized, e.g. "concessao de lavra")
    situacao         → keyword
    substancias      → keyword[]  (coded, currently empty — filled by SCM ETL)
    substancias_desc → keyword[]  (normalized names, e.g. ["marmore","calcario"])
    uf               → keyword
    municipio        → keyword
    area_ha          → float
    titular.nome     → text/keyword
    titular.cnpj_basico → keyword
    titular.cnae_principal → keyword (RFB, pós-enriquecimento bot_empresas)
    location         → geo_point  (centroid)
    geom             → geo_shape  (polygon)
    dt_requerimento  → date
    indexed_at       → date
"""

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.jazidas.queries.geo import (
    SHAPES_SOURCE_FULL,
    SHAPES_SOURCE_MINIMAL,
    build_mapa_response_with_municipios,
)
from mcp_servers.jazidas.schemas import ResolucaoSubstancia

logger = logging.getLogger("mcp.jazidas.queries.jazidas")

INDEX_ANM = "mr_jazidas_v001"
MAX_RESULTS = 200


def expand_cnae_titular_codigos(csv: str) -> list[str]:
    """
    Expande tokens CNAE do utilizador para formas frequentes no índice
    (match exato em ``titular.cnae_principal`` — keyword).

    Aceita ``07.29-4``, ``0729-4/00``, ``0729400``, lista CSV.
    """
    variants: list[str] = []
    for part in csv.split(","):
        raw = part.strip()
        if not raw:
            continue
        variants.append(raw)
        digits = "".join(c for c in raw if c.isdigit())
        if len(digits) >= 4:
            d7 = (digits + "0000000")[:7]
            if d7 not in variants:
                variants.append(d7)
            if len(d7) == 7:
                dashed = f"{d7[:4]}-{d7[4]}/{d7[5:]}"
                if dashed not in variants:
                    variants.append(dashed)
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


SOURCE_FIELDS = [
    "numero_processo",
    "ativo",
    "fase",
    "situacao",
    "substancias_desc",
    "uso_substancia",
    "uf",
    "municipio",
    "codigo_ibge",
    "area_ha",
    "titular",
    "dt_requerimento",
    "location",
    # Restrições geoespaciais pré-computadas (TI + UC)
    "n_restricoes_ti",
    "n_restricoes_uc",
    "restricoes_geo",
]


# ==================== Query Builder ====================


def build_jazidas_query(
    resolucao: ResolucaoSubstancia,
    latitude: float | None = None,
    longitude: float | None = None,
    raio_km: float = 50.0,
    uf: str | None = None,
    municipio: str | None = None,
    codigo_ibge: str | None = None,
    fase: str | None = None,
    apenas_ativos: bool = False,
    incluir_geometria: bool = False,
    area_min_ha: float | None = None,
    area_max_ha: float | None = None,
    cnae_titular_codigos: list[str] | None = None,
) -> dict[str, Any]:
    """
    Build the mr_jazidas_v001 query.

    Substance matching:
        - If resolucao.encontrou → uses resolucao.campo_filter / ids_filter
          (legacy path for when substance lookup indices are available)
        - Otherwise → direct match/term on substancias_desc using termo_original
          (omitido se ``termo_original`` vazio — ex.: busca só por CNAE do titular)

    CNAE do titular:
        - ``cnae_titular_codigos``: filtro ``terms`` em ``titular.cnae_principal``
          (documentos sem enriquecimento RFB ficam de fora).
    """
    must_clauses: list[dict] = []
    filter_clauses: list[dict] = []

    # Substance filter
    if resolucao.encontrou and resolucao.ids_filter:
        # Semantic resolution succeeded.
        if resolucao.campo_filter == "uso_substancia":
            # uso_substancia is a keyword that may contain concatenated values
            # (e.g. "Construção civil, Industrial"), so wildcard per resolved term.
            should_clauses = [
                {"wildcard": {"uso_substancia": {"value": f"*{v}*", "case_insensitive": True}}}
                for v in resolucao.ids_filter
            ]
            filter_clauses.append(
                {"bool": {"should": should_clauses, "minimum_should_match": 1}}
            )
        else:
            # substancias_desc.keyword — exact normalized names, terms filter is correct.
            filter_clauses.append(
                {"terms": {resolucao.campo_filter: resolucao.ids_filter}}
            )
    elif resolucao.termo_original and resolucao.termo_original.strip():
        # Direct text match on substancias_desc (normalized, no accents)
        termo = _normalize_termo(resolucao.termo_original)
        filter_clauses.append({
            "bool": {
                "should": [
                    {"term": {"substancias_desc.keyword": termo}},
                    {"match": {"substancias_desc": {"query": termo, "fuzziness": "AUTO"}}},
                ],
                "minimum_should_match": 1,
            }
        })

    # Geo distance filter — field is "location" in mr_jazidas_v001
    has_geo = _has_valid_geo(latitude, longitude)
    if has_geo:
        filter_clauses.append({
            "geo_distance": {
                "distance": f"{raio_km}km",
                "location": {"lat": latitude, "lon": longitude},
            }
        })

    if apenas_ativos:
        filter_clauses.append({"term": {"ativo": True}})

    if uf:
        filter_clauses.append({"term": {"uf": uf.upper()}})

    if codigo_ibge:
        # Exact IBGE code filter — most precise way to filter by municipality
        filter_clauses.append({"term": {"codigo_ibge": str(codigo_ibge).strip()}})
    elif municipio:
        filter_clauses.append({"match": {"municipio": municipio}})

    if fase:
        fase_norm = _normalize_termo(fase)
        filter_clauses.append({"match": {"fase": fase_norm}})

    if area_min_ha is not None or area_max_ha is not None:
        range_clause: dict[str, Any] = {}
        if area_min_ha is not None:
            range_clause["gte"] = float(area_min_ha)
        if area_max_ha is not None:
            range_clause["lte"] = float(area_max_ha)
        filter_clauses.append({"range": {"area_ha": range_clause}})

    if cnae_titular_codigos:
        filter_clauses.append({
            "terms": {"titular.cnae_principal": cnae_titular_codigos},
        })

    source_fields = list(SOURCE_FIELDS)
    if incluir_geometria:
        source_fields.extend(SHAPES_SOURCE_FULL)
    else:
        source_fields.extend(SHAPES_SOURCE_MINIMAL)

    sort = _build_sort(has_geo, latitude, longitude, area_min_ha, area_max_ha)

    bool_query: dict[str, Any] = {}
    if must_clauses:
        bool_query["must"] = must_clauses
    if filter_clauses:
        bool_query["filter"] = filter_clauses

    return {
        "size": MAX_RESULTS,
        "query": {
            "bool": bool_query if bool_query else {"must": {"match_all": {}}},
        },
        "_source": source_fields,
        "sort": sort,
    }


# ==================== Result Formatter ====================


def format_jazidas_results(hits: list[dict]) -> list[dict]:
    return [_format_jazida(hit) for hit in hits]


def _format_jazida(hit: dict) -> dict[str, Any]:
    source = hit.get("_source", {})
    sort_values = hit.get("sort", [])
    titular = source.get("titular") or {}

    distancia_km = None
    if sort_values and isinstance(sort_values[0], (int, float)):
        distancia_km = round(sort_values[0], 2)

    loc = source.get("location") or {}

    n_ti = int(source.get("n_restricoes_ti") or 0)
    n_uc = int(source.get("n_restricoes_uc") or 0)

    return {
        "ds_processo": source.get("numero_processo", ""),
        "fase": source.get("fase"),
        "situacao": source.get("situacao"),
        "area_ha": source.get("area_ha"),
        "ativo": source.get("ativo", False),
        "substancias": source.get("substancias_desc", []),
        "uso_substancia": source.get("uso_substancia"),
        "municipios": [source.get("municipio")] if source.get("municipio") else [],
        "uf": [source.get("uf")] if source.get("uf") else [],
        "localizacao": loc if loc else None,
        "distancia_km": distancia_km,
        "titulares": [titular.get("nome") or titular.get("razao_social", "")] if titular else [],
        "cnpj_titulares": [titular.get("cnpj_basico")] if titular.get("cnpj_basico") else [],
        "cnae_titular": titular.get("cnae_principal"),
        "dt_protocolo": source.get("dt_requerimento"),
        "n_restricoes_ti": n_ti,
        "n_restricoes_uc": n_uc,
        "tem_restricoes": (n_ti + n_uc) > 0,
        # _restricoes_geo_raw: consumido por _enrich_restricoes_geo, removido depois
        "_restricoes_geo_raw": source.get("restricoes_geo") or [],
    }


# ==================== Orchestrator ====================


async def executar_busca_jazidas(
    os_service: OpenSearchService,
    resolucao: ResolucaoSubstancia,
    latitude: float | None = None,
    longitude: float | None = None,
    raio_km: float = 50.0,
    uf: str | None = None,
    municipio: str | None = None,
    codigo_ibge: str | None = None,
    fase: str | None = None,
    apenas_ativos: bool = False,
    incluir_geometria: bool = False,
    area_min_ha: float | None = None,
    area_max_ha: float | None = None,
    cnae_titular_codigos: list[str] | None = None,
) -> dict[str, Any]:
    query = build_jazidas_query(
        resolucao=resolucao,
        latitude=latitude,
        longitude=longitude,
        raio_km=raio_km,
        uf=uf,
        municipio=municipio,
        codigo_ibge=codigo_ibge,
        fase=fase,
        apenas_ativos=apenas_ativos,
        incluir_geometria=incluir_geometria,
        area_min_ha=area_min_ha,
        area_max_ha=area_max_ha,
        cnae_titular_codigos=cnae_titular_codigos,
    )

    has_geo = _has_valid_geo(latitude, longitude)
    _area_log: list[str] = []
    if area_min_ha is not None:
        _area_log.append(f"area_min_ha={area_min_ha}")
    if area_max_ha is not None:
        _area_log.append(f"area_max_ha={area_max_ha}")
    _area_s = ", ".join(_area_log) if _area_log else "area=—"
    logger.info(
        f"Querying {INDEX_ANM} — "
        f"termo='{resolucao.termo_original}', "
        f"geo={'(' + str(latitude) + ',' + str(longitude) + ')' if has_geo else 'off'}, "
        f"raio={raio_km}km, uf={uf}, {_area_s}"
    )

    anm_result = await os_service.search_with_meta(INDEX_ANM, query)
    total = anm_result.get("total", 0)
    hits = anm_result.get("hits", [])

    logger.info(f"Found {total} processes ({len(hits)} fetched)")

    empty_mapa = {"pontos": [], "total_pontos": 0}

    if not hits:
        return {
            "total": 0,
            "resultados": [],
            "mapa": empty_mapa,
            "resolucao": _build_resolucao_meta(resolucao),
        }

    resultados = format_jazidas_results(hits)

    # Enriquecimento determinístico de restrições (2 queries batch — ~5ms cada)
    await _enrich_restricoes_geo(os_service, resultados)

    # CNPJ enrichment (best-effort — non-blocking)
    empresas_lookup: dict = {}
    try:
        empresas_lookup = await _enrich_cnpj_addresses(os_service, hits)
    except Exception:
        pass
    if empresas_lookup:
        _apply_address_enrichment(resultados, empresas_lookup)

    mapa = await build_mapa_response_with_municipios(
        os_service=os_service,
        hits=hits,
        incluir_geometria=incluir_geometria,
    )
    _enrich_mapa_pontos(mapa, hits, empresas_lookup)

    return {
        "total": total,
        "resultados": resultados,
        "mapa": mapa,
        "resolucao": _build_resolucao_meta(resolucao),
    }


# ==================== Helpers ====================


def _has_valid_geo(lat: float | None, lon: float | None) -> bool:
    return lat is not None and lon is not None


def _build_sort(
    has_geo: bool,
    lat: float | None,
    lon: float | None,
    area_min_ha: float | None = None,
    area_max_ha: float | None = None,
) -> list[dict]:
    if has_geo:
        return [{
            "_geo_distance": {
                "location": {"lat": lat, "lon": lon},
                "order": "asc",
                "unit": "km",
            }
        }]
    # When filtering by area (no geo centre), sort by area_ha DESC so the
    # largest processes appear first — matches user expectation for "liste todos
    # com mais de X ha" queries.
    if area_min_ha is not None or area_max_ha is not None:
        return [{"area_ha": {"order": "desc"}}]
    return [{"_score": {"order": "desc"}}]


def _normalize_termo(termo: str) -> str:
    """Normalize to lowercase, remove accents for keyword matching."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", termo.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _build_resolucao_meta(resolucao: ResolucaoSubstancia) -> dict:
    return {
        "metodo": resolucao.metodo,
        "ids": resolucao.ids_filter,
        "termo": resolucao.termo_original,
    }


def _ensure_list(val: Any) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


# ==================== Restrições Geoespaciais — Enriquecimento Determinístico ====================


_NIVEL_ORDEM = {"critico": 4, "alto": 3, "medio": 2, "baixo": 1, "nenhum": 0}
_FASES_CRITICAS = {"Homologada", "Regularizada"}
_FASES_ALTAS    = {"Delimitada", "Declarada", "Em Estudo"}


def _nivel_restricao(tis: list[dict], ucs: list[dict]) -> str:
    for ti in tis:
        if (ti.get("fase_funai") or "") in _FASES_CRITICAS:
            return "critico"
    for ti in tis:
        if (ti.get("fase_funai") or "") in _FASES_ALTAS:
            return "alto"
    if tis:
        return "alto"
    for uc in ucs:
        if (uc.get("grupo") or "").startswith("Proteção"):
            return "alto"
    if ucs:
        return "medio"
    return "nenhum"


async def _enrich_restricoes_geo(
    os_service: OpenSearchService,
    resultados: list[dict],
) -> None:
    """
    Enriquecimento determinístico de restrições geoespaciais (TI + UC).

    Estratégia batch: 2 queries fixas independente do número de resultados.
      1. Coleta todos os IDs de TI e UC de _restricoes_geo_raw em todos os resultados
      2. Batch-fetch mr_terras_indigenas_v001 (1 query)
      3. Batch-fetch mr_ucs_v001 (1 query)
      4. Injeta struct `restricoes` em cada resultado; remove campo temporário

    Custo: 2 queries em índices pequenos (~657 TIs, ~2073 UCs) → < 5ms cada.
    """
    # Coleta IDs únicos de todos os resultados
    all_ti_ids: set[str] = set()
    all_uc_ids: set[str] = set()
    for r in resultados:
        for entry in r.get("_restricoes_geo_raw") or []:
            parts = entry.split(":", 2)
            if parts[0] == "TI" and len(parts) >= 3:
                all_ti_ids.add(parts[1])
            elif parts[0] == "UC" and len(parts) >= 3:
                all_uc_ids.add(parts[1])

    # Batch-fetch TIs
    ti_map: dict[str, dict] = {}
    if all_ti_ids:
        try:
            raw = await os_service.search("mr_terras_indigenas_v001", {
                "size": len(all_ti_ids),
                "_source": ["id_ti", "nome", "fase_funai", "etnia", "area_ha", "uf"],
                "query": {"terms": {"id_ti": list(all_ti_ids)}},
            })
            ti_map = {
                h["_source"]["id_ti"]: h["_source"]
                for h in raw.get("hits", {}).get("hits", [])
            }
        except Exception as e:
            logger.warning("TI enrichment failed (non-blocking): %s", e)

    # Batch-fetch UCs
    uc_map: dict[str, dict] = {}
    if all_uc_ids:
        try:
            raw = await os_service.search("mr_ucs_v001", {
                "size": len(all_uc_ids),
                "_source": ["cod_cnuc", "nome", "categoria", "grupo", "esfera", "area_ha"],
                "query": {"terms": {"cod_cnuc": list(all_uc_ids)}},
            })
            uc_map = {
                h["_source"]["cod_cnuc"]: h["_source"]
                for h in raw.get("hits", {}).get("hits", [])
            }
        except Exception as e:
            logger.warning("UC enrichment failed (non-blocking): %s", e)

    # Injeta struct restricoes em cada resultado
    for r in resultados:
        raw_entries: list[str] = r.pop("_restricoes_geo_raw", [])

        if not raw_entries:
            r["restricoes"] = {"nivel": "nenhum", "terras_indigenas": [], "unidades_conservacao": []}
            continue

        tis_result: list[dict] = []
        ucs_result: list[dict] = []
        outras: list[str] = []

        for entry in raw_entries:
            parts = entry.split(":", 2)
            if parts[0] == "TI" and len(parts) >= 3:
                detail = ti_map.get(parts[1], {})
                tis_result.append({
                    "id_ti":      parts[1],
                    "nome":       detail.get("nome") or parts[2],
                    "fase_funai": detail.get("fase_funai"),
                    "etnia":      detail.get("etnia"),
                    "area_ha":    detail.get("area_ha"),
                    "uf":         detail.get("uf"),
                })
            elif parts[0] == "UC" and len(parts) >= 3:
                detail = uc_map.get(parts[1], {})
                ucs_result.append({
                    "cod_cnuc":  parts[1],
                    "nome":      detail.get("nome") or parts[2],
                    "categoria": detail.get("categoria"),
                    "grupo":     detail.get("grupo"),
                    "esfera":    detail.get("esfera"),
                    "area_ha":   detail.get("area_ha"),
                })
            else:
                outras.append(entry)

        nivel = _nivel_restricao(tis_result, ucs_result)
        r["restricoes"] = {
            "nivel":               nivel,
            "terras_indigenas":    tis_result,
            "unidades_conservacao": ucs_result,
            **({"outras": outras} if outras else {}),
        }

    logger.info(
        "Restricoes enriquecidas — ti_ids=%d, uc_ids=%d",
        len(all_ti_ids), len(all_uc_ids),
    )


# ==================== CNPJ Address Enrichment ====================


async def _enrich_cnpj_addresses(
    os_service: OpenSearchService,
    hits: list[dict],
) -> dict[str, dict]:
    """Best-effort CNPJ enrichment from mr_empresas_v001."""
    cnpjs = []
    for hit in hits:
        titular = (hit.get("_source") or {}).get("titular") or {}
        cnpj = titular.get("cnpj_basico")
        if cnpj and cnpj not in cnpjs:
            cnpjs.append(cnpj)
    if not cnpjs:
        return {}

    try:
        query = {
            "size": len(cnpjs),
            "query": {"terms": {"cnpj_basico": cnpjs}},
            "_source": ["cnpj_basico", "razao_social", "contato"],
        }
        result = await os_service.search_with_meta("mr_empresas_v001", query)
        return {
            h["_source"]["cnpj_basico"]: h["_source"]
            for h in result.get("hits", [])
            if h.get("_source", {}).get("cnpj_basico")
        }
    except Exception as e:
        logger.warning("CNPJ enrichment failed (non-blocking): %s", e)
        return {}


def _apply_address_enrichment(
    resultados: list[dict],
    empresas: dict[str, dict],
) -> None:
    for resultado in resultados:
        for cnpj in resultado.get("cnpj_titulares", []):
            emp = empresas.get(str(cnpj))
            if not emp:
                continue
            contato = emp.get("contato") or {}
            if contato.get("telefone"):
                resultado["telefone"] = contato["telefone"]
            if contato.get("email"):
                resultado["email"] = contato["email"]
            if contato.get("endereco"):
                resultado["endereco"] = contato["endereco"]
            resultado["razao_social_titular"] = emp.get("razao_social", "")
            break


def _enrich_mapa_pontos(
    mapa: dict,
    hits: list[dict],
    empresas: dict[str, dict],
) -> None:
    pontos = mapa.get("pontos", [])
    if not pontos:
        return

    hit_lookup: dict[str, dict] = {}
    for hit in hits:
        source = hit.get("_source", {})
        processo = source.get("numero_processo", "")
        titular = source.get("titular") or {}
        sort_values = hit.get("sort", [])
        distancia_km = None
        if sort_values and isinstance(sort_values[0], (int, float)):
            distancia_km = round(sort_values[0], 2)

        cnpj = titular.get("cnpj_basico")
        hit_lookup[processo] = {
            "substancias": source.get("substancias_desc", []),
            "municipios": [source.get("municipio")] if source.get("municipio") else [],
            "uf": [source.get("uf")] if source.get("uf") else [],
            "titulares": [titular.get("nome") or titular.get("razao_social", "")] if titular else [],
            "cnpj_titulares": [cnpj] if cnpj else [],
            "area_ha": source.get("area_ha"),
            "distancia_km": distancia_km,
        }

        if empresas and cnpj:
            emp = empresas.get(str(cnpj))
            if emp:
                contato = emp.get("contato") or {}
                hit_lookup[processo]["endereco"] = contato.get("endereco")

    for ponto in pontos:
        processo = ponto.get("processo", "")
        extra = hit_lookup.get(processo)
        if extra:
            for k, v in extra.items():
                if v is not None:
                    ponto[k] = v
