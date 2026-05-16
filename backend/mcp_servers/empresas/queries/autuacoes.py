"""
Autuações IBAMA Query Module
=============================

Queries para ``mr_autuacoes_v001`` expostas em dois contextos:

    1. Por CNPJ (risco_ambiental_empresa) — retorna histórico completo de
       autuações, embargos e apreensões de uma empresa específica, combinando
       os dados pré-agregados do mr_empresas_v001 com os registros individuais
       do mr_autuacoes_v001.

    2. Por área geográfica (autuacoes_por_area) — retorna infrações dentro de
       um raio em torno de uma coordenada (útil para análise de risco de uma
       jazida ou empreendimento).

Índices:
    - mr_autuacoes_v001  (~55k docs — autuações, embargos, apreensões IBAMA, filtro mineral)
    - mr_empresas_v001   (43k docs — contém resumo pré-agregado por cnpj_basico)

Performance esperada:
    Por CNPJ:  ~15-25ms (term query + small result set)
    Por área:  ~20-40ms (geo_distance filter)
"""

from __future__ import annotations

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.empresas.queries.autuacoes")

INDEX_autuacoes = "mr_autuacoes_v001"
INDEX_EMPRESAS  = "mr_empresas_v001"

MAX_AUTUACOES_LISTA  = 20
MAX_AUTUACOES_GEO    = 30


# ─────────────────────────────────────────────────────────────────────────────
# Query builders
# ─────────────────────────────────────────────────────────────────────────────

def build_autuacoes_por_cnpj_query(cnpj_basico: str, size: int = MAX_AUTUACOES_LISTA) -> dict[str, Any]:
    """Busca autuações de uma empresa por cnpj_basico, ordenadas por data desc."""
    return {
        "size": size,
        "query": {"term": {"cnpj_basico": cnpj_basico}},
        "_source": [
            "id", "tipo", "numero_auto", "nome_autuado",
            "infracao", "fundamentacao", "gravidade", "tipo_infracao",
            "valor_multa", "valor_multa_real", "valor_multa_suspeito",
            "municipio", "uf", "area_ha", "location",
            "dt_autuacao", "dt_julgamento", "status", "cancelado",
            "biomas", "unidade_conservacao",
            "match_origem", "match_keywords",
        ],
        "sort": [
            {"dt_autuacao": {"order": "desc", "missing": "_last"}},
        ],
        "aggs": {
            "por_tipo": {"terms": {"field": "tipo"}},
            "por_uf":   {"terms": {"field": "uf", "size": 10}},
            "valor_real_total": {"sum": {"field": "valor_multa_real"}},
            "ultima_data": {"max": {"field": "dt_autuacao"}},
        },
    }


def build_resumo_ibama_query(cnpj_basico: str) -> dict[str, Any]:
    """Busca o resumo pré-agregado IBAMA armazenado em mr_empresas_v001."""
    return {
        "size": 1,
        "query": {"term": {"_id": cnpj_basico}},
        "_source": [
            "razao_social", "cnpj_basico",
            "n_autuacoes", "n_embargos", "n_apreensoes",
            "valor_total_multa", "ultima_autuacao", "tem_risco_ibama",
        ],
    }


def build_autuacoes_geo_query(
    lat: float,
    lon: float,
    raio_km: float,
    tipo: str | None = None,
    apenas_ativos: bool = False,
    size: int = MAX_AUTUACOES_GEO,
) -> dict[str, Any]:
    """
    Busca autuações dentro de um raio geográfico.

    Filtra por ``location`` (geo_point) usando geo_distance.
    Permite filtrar por tipo (Autuacao / Embargo / Apreensao)
    e excluir registros cancelados.
    """
    filters: list[dict] = [
        {
            "geo_distance": {
                "distance": f"{raio_km}km",
                "location": {"lat": lat, "lon": lon},
            }
        }
    ]

    if tipo:
        filters.append({"term": {"tipo": tipo}})

    if apenas_ativos:
        filters.append({"term": {"cancelado": False}})

    query: dict[str, Any] = {
        "size": size,
        "query": {"bool": {"filter": filters}},
        "_source": [
            "id", "tipo", "nome_autuado", "cnpj_basico",
            "infracao", "gravidade",
            "valor_multa", "valor_multa_real", "valor_multa_suspeito",
            "municipio", "uf", "area_ha", "location",
            "dt_autuacao", "status", "cancelado",
            "biomas", "match_origem",
        ],
        "sort": [
            {
                "_geo_distance": {
                    "location": {"lat": lat, "lon": lon},
                    "order": "asc",
                    "unit": "km",
                }
            }
        ],
        "aggs": {
            "por_tipo":    {"terms": {"field": "tipo"}},
            "por_empresa": {"terms": {"field": "nome_autuado.keyword", "size": 10}},
            "valor_total": {"sum": {"field": "valor_multa_real"}},
        },
    }

    return query


# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────

def _fix_encoding(text: str | None) -> str | None:
    """
    Corrige artefatos de encoding Latin-1 armazenados como sequências de bytes
    mal interpretados (ex: 'Ã§' → 'ç').
    """
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def format_autuacao(hit: dict) -> dict[str, Any]:
    """Formata um hit do mr_autuacoes_v001 para output limpo."""
    s = hit.get("_source", {})

    valor_raw  = s.get("valor_multa")
    valor_real = s.get("valor_multa_real")
    suspeito   = s.get("valor_multa_suspeito", False)

    return {
        "id":           s.get("id"),
        "tipo":         s.get("tipo"),
        "numero_auto":  s.get("numero_auto"),
        "nome_autuado": s.get("nome_autuado"),
        "infracao":     _fix_encoding((s.get("infracao") or "").strip() or None),
        "gravidade":    s.get("gravidade"),
        "tipo_infracao":s.get("tipo_infracao"),
        "valor_multa_brl": valor_real if not suspeito else None,
        "valor_multa_historico": valor_raw if suspeito else None,
        "nota_valor": "Valor histórico em moeda pré-Plano Real (suspeito)" if suspeito else None,
        "municipio":  s.get("municipio"),
        "uf":         s.get("uf"),
        "area_ha":    s.get("area_ha"),
        "dt_autuacao": s.get("dt_autuacao"),
        "dt_julgamento": s.get("dt_julgamento"),
        "status":     s.get("status"),
        "cancelado":  s.get("cancelado"),
        "biomas":     s.get("biomas"),
        "unidade_conservacao": s.get("unidade_conservacao"),
        "match_keywords": s.get("match_keywords"),
    }


def format_autuacao_geo(hit: dict) -> dict[str, Any]:
    """Formata um hit geo com distância calculada."""
    s = hit.get("_source", {})
    sort_vals = hit.get("sort", [])
    dist_km = round(sort_vals[0], 2) if sort_vals else None

    valor_real = s.get("valor_multa_real")
    suspeito   = s.get("valor_multa_suspeito", False)

    return {
        "id":          s.get("id"),
        "tipo":        s.get("tipo"),
        "nome_autuado":s.get("nome_autuado"),
        "cnpj_basico": s.get("cnpj_basico"),
        "infracao":    _fix_encoding((s.get("infracao") or "").strip() or None),
        "gravidade":   s.get("gravidade"),
        "valor_multa_brl": valor_real if not suspeito else None,
        "municipio":   s.get("municipio"),
        "uf":          s.get("uf"),
        "area_ha":     s.get("area_ha"),
        "distancia_km": dist_km,
        "dt_autuacao": s.get("dt_autuacao"),
        "cancelado":   s.get("cancelado"),
        "biomas":      s.get("biomas"),
        "match_origem": s.get("match_origem"),
    }


def format_risco_summary(aggs: dict, total_hits: int) -> dict[str, Any]:
    """Formata o resumo de risco a partir das agregações."""
    por_tipo = {b["key"]: b["doc_count"] for b in aggs.get("por_tipo", {}).get("buckets", [])}
    por_uf   = {b["key"]: b["doc_count"] for b in aggs.get("por_uf", {}).get("buckets", [])}
    valor_real_total = aggs.get("valor_real_total", {}).get("value", 0.0)
    ultima = aggs.get("ultima_data", {}).get("value_as_string")

    nivel = _calcular_nivel_risco(
        n_autuacoes=por_tipo.get("Autuacao", 0),
        n_embargos=por_tipo.get("Embargo", 0),
        valor_total=valor_real_total,
    )

    return {
        "total_registros": total_hits,
        "n_autuacoes":  por_tipo.get("Autuacao", 0),
        "n_embargos":   por_tipo.get("Embargo", 0),
        "n_apreensoes": por_tipo.get("Apreensao", 0),
        "valor_total_multas_brl": round(valor_real_total, 2),
        "ultima_ocorrencia": ultima,
        "estados_afetados": por_uf,
        "nivel_risco": nivel,
    }


def _calcular_nivel_risco(
    n_autuacoes: int,
    n_embargos: int,
    valor_total: float,
) -> str:
    """
    Classifica o nível de risco ambiental:
      - CRÍTICO : embargo ativo ou multa > R$ 10M
      - ALTO    : ≥ 5 autuações ou multa > R$ 1M
      - MÉDIO   : 1-4 autuações ou multa > R$ 100k
      - BAIXO   : registros existem mas valores pequenos
      - SEM RISCO: nenhum registro
    """
    if n_embargos > 0 or valor_total > 10_000_000:
        return "CRÍTICO"
    if n_autuacoes >= 5 or valor_total > 1_000_000:
        return "ALTO"
    if n_autuacoes >= 1 or valor_total > 100_000:
        return "MÉDIO"
    if n_autuacoes > 0:
        return "BAIXO"
    return "SEM_RISCO"


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrators
# ─────────────────────────────────────────────────────────────────────────────

async def executar_risco_ambiental_empresa(
    os_service: OpenSearchService,
    cnpj_basico: str,
    max_registros: int = MAX_AUTUACOES_LISTA,
) -> dict[str, Any]:
    """
    Orquestra a consulta de risco ambiental de uma empresa.

    Passos:
      1. Busca resumo pré-agregado em mr_empresas_v001 (rápido)
      2. Busca registros individuais em mr_autuacoes_v001 (detalhe)
      3. Monta resposta consolidada com nível de risco

    Returns:
        Dict com resumo, nível de risco e lista de autuações individuais.
    """
    # ── Passo 1: Resumo pré-agregado ──────────────────────────────────────
    resumo_pre: dict[str, Any] = {}
    try:
        r_emp = await os_service.search(INDEX_EMPRESAS, build_resumo_ibama_query(cnpj_basico))
        emp_hits = r_emp.get("hits", {}).get("hits", [])
        if emp_hits:
            resumo_pre = emp_hits[0].get("_source", {})
    except Exception as e:
        logger.warning(f"autuacoes: falha ao buscar resumo empresa {cnpj_basico}: {e}")

    # ── Passo 2: Registros individuais ────────────────────────────────────
    query = build_autuacoes_por_cnpj_query(cnpj_basico, size=max_registros)
    try:
        r_auto = await os_service.search(INDEX_autuacoes, query)
    except Exception as e:
        logger.error(f"autuacoes: falha na busca por cnpj {cnpj_basico}: {e}")
        return {"erro": str(e)}

    hits  = r_auto.get("hits", {}).get("hits", [])
    total = r_auto.get("hits", {}).get("total", {})
    total_val = total.get("value", 0) if isinstance(total, dict) else int(total)
    aggs  = r_auto.get("aggregations", {})

    # ── Passo 3: Consolida ────────────────────────────────────────────────
    resumo = format_risco_summary(aggs, total_val)

    # Usa valor pré-agregado do empresas se disponível e maior (mais completo)
    if resumo_pre.get("valor_total_multa"):
        resumo["valor_total_multas_brl"] = resumo_pre["valor_total_multa"]

    registros = [format_autuacao(h) for h in hits]

    logger.info(
        f"autuacoes: cnpj={cnpj_basico} → "
        f"{total_val} registros, nível={resumo['nivel_risco']}, "
        f"valor=R$ {resumo['valor_total_multas_brl']:,.0f}"
    )

    return {
        "cnpj_basico":   cnpj_basico,
        "razao_social":  resumo_pre.get("razao_social"),
        "resumo":        resumo,
        "registros":     registros,
        "total_registros_index": total_val,
        "exibindo":      len(registros),
    }


async def executar_autuacoes_por_area(
    os_service: OpenSearchService,
    lat: float,
    lon: float,
    raio_km: float = 10.0,
    tipo: str | None = None,
    apenas_ativos: bool = False,
) -> dict[str, Any]:
    """
    Busca autuações IBAMA dentro de um raio geográfico.

    Útil para avaliar o histórico ambiental de uma região antes de
    avaliar uma jazida ou empreendimento mineral.

    Returns:
        Dict com resumo da área e lista de infrações próximas.
    """
    query = build_autuacoes_geo_query(lat, lon, raio_km, tipo, apenas_ativos)

    try:
        result = await os_service.search(INDEX_autuacoes, query)
    except Exception as e:
        logger.error(f"autuacoes_geo: falha lat={lat} lon={lon} raio={raio_km}: {e}")
        return {"erro": str(e)}

    hits  = result.get("hits", {}).get("hits", [])
    total = result.get("hits", {}).get("total", {})
    total_val = total.get("value", 0) if isinstance(total, dict) else int(total)
    aggs  = result.get("aggregations", {})

    por_tipo    = {b["key"]: b["doc_count"] for b in aggs.get("por_tipo", {}).get("buckets", [])}
    por_empresa = {b["key"]: b["doc_count"] for b in aggs.get("por_empresa", {}).get("buckets", [])}
    valor_total = aggs.get("valor_total", {}).get("value", 0.0)

    registros = [format_autuacao_geo(h) for h in hits]

    nivel = _calcular_nivel_risco(
        n_autuacoes=por_tipo.get("Autuacao", 0),
        n_embargos=por_tipo.get("Embargo", 0),
        valor_total=valor_total,
    )

    logger.info(
        f"autuacoes_geo: lat={lat} lon={lon} raio={raio_km}km → "
        f"{total_val} registros, nível={nivel}"
    )

    return {
        "area": {
            "lat": lat,
            "lon": lon,
            "raio_km": raio_km,
        },
        "resumo": {
            "total_encontrados": total_val,
            "exibindo": len(registros),
            "n_autuacoes":  por_tipo.get("Autuacao", 0),
            "n_embargos":   por_tipo.get("Embargo", 0),
            "n_apreensoes": por_tipo.get("Apreensao", 0),
            "valor_total_multas_brl": round(valor_total, 2),
            "nivel_risco_area": nivel,
            "principais_infratores": por_empresa,
        },
        "registros": registros,
    }


async def fetch_resumo_ibama_para_empresa(
    os_service: OpenSearchService,
    cnpj_basico: str,
) -> dict[str, Any] | None:
    """
    Busca resumo IBAMA compacto para enriquecer detalhes_empresa.

    Retorna apenas os campos de risco pré-agregados do mr_empresas_v001
    (sem fazer busca no mr_autuacoes_v001) — rápido, ~10ms.

    Returns:
        Dict com n_autuacoes, n_embargos, valor_total_multa, nivel_risco
        ou None se a empresa não tiver registros IBAMA.
    """
    try:
        r = await os_service.search(INDEX_EMPRESAS, build_resumo_ibama_query(cnpj_basico))
        hits = r.get("hits", {}).get("hits", [])
        if not hits:
            return None

        s = hits[0].get("_source", {})
        if not s.get("tem_risco_ibama"):
            return None

        n_aut = s.get("n_autuacoes", 0) or 0
        n_emb = s.get("n_embargos", 0) or 0
        valor = s.get("valor_total_multa", 0.0) or 0.0

        return {
            "n_autuacoes":  n_aut,
            "n_embargos":   n_emb,
            "n_apreensoes": s.get("n_apreensoes", 0) or 0,
            "valor_total_multas_brl": round(valor, 2),
            "ultima_autuacao": s.get("ultima_autuacao"),
            "nivel_risco": _calcular_nivel_risco(n_aut, n_emb, valor),
        }
    except Exception as e:
        logger.warning(f"fetch_resumo_ibama: falha para {cnpj_basico}: {e}")
        return None
