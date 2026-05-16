"""
Monitoring Query Module
========================

Queries para ``mr_monitoring_v001`` — alertas de processos ANM.

Tipos de evento armazenados:
    PRAZO_ALERT     — Processo próximo ao vencimento (dt_validade)
    STATUS_CHANGE   — Mudança de fase ou titular detectada
    DOU_PUBLICACAO  — Publicação ANM no Diário Oficial (via INLABS)

Subtipos de PRAZO_ALERT:
    VENCIMENTO_30D  — vence em até 30 dias (ALTA)
    VENCIMENTO_60D  — vence em 31-60 dias (MEDIA)
    VENCIMENTO_90D  — vence em 61-90 dias (BAIXA)
    VENCIDO_ATIVO   — dt_validade < hoje, processo ainda ativo (ALTA)

Subtipos de STATUS_CHANGE:
    FASE_ALTERADA    — fase do processo mudou (MEDIA)
    TITULAR_ALTERADO — CNPJ do titular mudou (ALTA)

Relevância:
    ALTA  → ação_necessaria = true (vence ≤30d, vencido, titular alterado)
    MEDIA → prazo ≤60d, mudança de fase
    BAIXA → prazo ≤90d

Índice:
    mr_monitoring_v001  (crescente conforme bot roda)
"""

from __future__ import annotations

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.empresas.queries.monitoring")

INDEX_MON = "mr_monitoring_v001"

TIPO_LABEL = {
    "PRAZO_ALERT":    "Alerta de Prazo",
    "STATUS_CHANGE":  "Mudança de Status",
    "DOU_PUBLICACAO": "Publicação DOU",
}

RELEVANCIA_PESO = {"ALTA": 3, "MEDIA": 2, "BAIXA": 1}


# ─────────────────────────────────────────────────────────────────────────────
# Query builders
# ─────────────────────────────────────────────────────────────────────────────

def build_alertas_processo_query(
    numero_processo: str | None,
    cnpj_titular: str | None,
    tipo_evento: str | None,
    apenas_nao_lidos: bool,
    apenas_acao: bool,
    size: int,
) -> dict[str, Any]:
    filters: list[dict] = []

    if numero_processo:
        filters.append({"term": {"numero_processo": numero_processo}})
    if cnpj_titular:
        doc = "".join(c for c in cnpj_titular if c.isdigit())
        if len(doc) >= 8:
            filters.append({"term": {"cnpj_titular": doc[:8]}})
    if tipo_evento:
        filters.append({"term": {"tipo_evento": tipo_evento.upper()}})
    if apenas_nao_lidos:
        filters.append({"term": {"lido": False}})
    if apenas_acao:
        filters.append({"term": {"acao_necessaria": True}})

    query = (
        {"bool": {"filter": filters}}
        if filters
        else {"match_all": {}}
    )

    return {
        "size": size,
        "_source": [
            "tipo_evento", "subtipo", "titulo", "resumo",
            "numero_processo", "nup", "cnpj_titular", "razao_social",
            "fonte", "relevancia", "acao_necessaria", "lido",
            "dt_evento", "dt_prazo", "url",
        ],
        "query": query,
        "sort": [
            # Prioridade: ação_necessaria desc, relevância desc, dt_evento desc
            {"acao_necessaria": "desc"},
            {"dt_evento":       "desc"},
        ],
        "aggs": {
            "por_tipo":      {"terms": {"field": "tipo_evento",    "size": 5}},
            "por_subtipo":   {"terms": {"field": "subtipo",        "size": 10}},
            "por_relevancia":{"terms": {"field": "relevancia",     "size": 5}},
            "acoes":         {"filter": {"term": {"acao_necessaria": True}}},
            "nao_lidos":     {"filter": {"term": {"lido": False}}},
        },
    }


def build_resumo_carteira_query(
    cnpjs: list[str] | None = None,
    uf: str | None = None,
    apenas_acao: bool = True,
) -> dict[str, Any]:
    """
    Resumo de alertas de toda uma carteira de CNPJs ou de uma UF.
    """
    filters: list[dict] = []
    if cnpjs:
        docs = ["".join(c for c in d if c.isdigit())[:8] for d in cnpjs]
        filters.append({"terms": {"cnpj_titular": docs}})
    if uf:
        # Sem campo UF direto no monitoring — busca por razao_social não é viável
        # O join correto seria via mr_jazidas_v001; aqui fazemos busca por texto
        pass
    if apenas_acao:
        filters.append({"term": {"acao_necessaria": True}})

    query = (
        {"bool": {"filter": filters}}
        if filters
        else {"match_all": {}}
    )

    return {
        "size": 20,
        "_source": [
            "tipo_evento", "subtipo", "titulo",
            "numero_processo", "cnpj_titular", "razao_social",
            "relevancia", "acao_necessaria", "dt_evento", "dt_prazo",
        ],
        "query": query,
        "sort": [
            {"acao_necessaria": "desc"},
            {"relevancia":       "desc"},
            {"dt_evento":        "desc"},
        ],
        "aggs": {
            "por_relevancia": {"terms": {"field": "relevancia",  "size": 5}},
            "por_tipo":       {"terms": {"field": "tipo_evento", "size": 5}},
            "acoes_urgentes": {"filter": {"bool": {"filter": [
                {"term": {"acao_necessaria": True}},
                {"term": {"relevancia": "ALTA"}},
            ]}}},
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────

def format_alerta(hit: dict) -> dict:
    s = hit["_source"]
    return {
        "id":              hit["_id"],
        "tipo":            TIPO_LABEL.get(s.get("tipo_evento", ""), s.get("tipo_evento")),
        "subtipo":         s.get("subtipo"),
        "titulo":          s.get("titulo"),
        "resumo":          s.get("resumo"),
        "processo":        s.get("numero_processo"),
        "empresa":         s.get("razao_social"),
        "cnpj_basico":     s.get("cnpj_titular"),
        "fonte":           s.get("fonte"),
        "relevancia":      s.get("relevancia"),
        "acao_necessaria": s.get("acao_necessaria", False),
        "lido":            s.get("lido", False),
        "dt_evento":       s.get("dt_evento"),
        "dt_prazo":        s.get("dt_prazo"),
        "url":             s.get("url"),
    }


def format_resumo_alertas(hits: list[dict], aggs: dict, total: int) -> dict:
    por_tipo = {
        TIPO_LABEL.get(b["key"], b["key"]): b["doc_count"]
        for b in aggs.get("por_tipo", {}).get("buckets", [])
    }
    por_rel  = {
        b["key"]: b["doc_count"]
        for b in aggs.get("por_relevancia", {}).get("buckets", [])
    }

    acoes     = aggs.get("acoes", {}).get("doc_count", 0)
    nao_lidos = aggs.get("nao_lidos", {}).get("doc_count", acoes)
    urgentes  = (aggs.get("acoes_urgentes", {}) or {}).get("doc_count", 0)

    # Nível geral
    alta = por_rel.get("ALTA", 0)
    if alta >= 3 or urgentes >= 2:
        nivel = "CRITICO"
    elif alta >= 1:
        nivel = "ALTO"
    elif por_rel.get("MEDIA", 0) >= 1:
        nivel = "MEDIO"
    elif total > 0:
        nivel = "BAIXO"
    else:
        nivel = "SEM_ALERTAS"

    return {
        "nivel_geral":       nivel,
        "total_alertas":     total,
        "acoes_necessarias": acoes,
        "nao_lidos":         nao_lidos,
        "urgentes_alta":     urgentes,
        "por_tipo":          por_tipo,
        "por_relevancia":    por_rel,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrators
# ─────────────────────────────────────────────────────────────────────────────

async def executar_alertas_processo(
    os_service: OpenSearchService,
    numero_processo: str | None = None,
    cnpj_titular: str | None = None,
    tipo_evento: str | None = None,
    apenas_nao_lidos: bool = False,
    apenas_acao: bool = False,
    max_registros: int = 20,
) -> dict[str, Any]:
    """
    Retorna alertas de monitoramento ativos, filtráveis por processo, empresa
    ou tipo de evento.
    """
    query = build_alertas_processo_query(
        numero_processo=numero_processo,
        cnpj_titular=cnpj_titular,
        tipo_evento=tipo_evento,
        apenas_nao_lidos=apenas_nao_lidos,
        apenas_acao=apenas_acao,
        size=max_registros,
    )

    try:
        result = await os_service.search(INDEX_MON, query)
    except Exception as e:
        logger.error(f"alertas_processo: query falhou: {e}")
        return {"erro": str(e)}

    hits  = result.get("hits", {}).get("hits", [])
    total = result.get("hits", {}).get("total", {}).get("value", 0)
    aggs  = result.get("aggregations", {})

    alertas = [format_alerta(h) for h in hits]
    resumo  = format_resumo_alertas(alertas, aggs, total)

    logger.info(
        f"alertas_processo: processo={numero_processo} cnpj={cnpj_titular} "
        f"→ {total} alertas nivel={resumo['nivel_geral']}"
    )

    return {
        "consulta": {
            "numero_processo": numero_processo,
            "cnpj_titular":    cnpj_titular,
            "tipo_evento":     tipo_evento,
            "apenas_acao":     apenas_acao,
        },
        "resumo":  resumo,
        "alertas": alertas,
    }


async def executar_resumo_carteira(
    os_service: OpenSearchService,
    cnpjs: list[str] | None = None,
    apenas_acao: bool = True,
    max_registros: int = 20,
) -> dict[str, Any]:
    """
    Resumo executivo de alertas para uma carteira de empresas.
    """
    query = build_resumo_carteira_query(
        cnpjs=cnpjs,
        apenas_acao=apenas_acao,
    )

    try:
        result = await os_service.search(INDEX_MON, query)
    except Exception as e:
        logger.error(f"resumo_carteira: query falhou: {e}")
        return {"erro": str(e)}

    hits  = result.get("hits", {}).get("hits", [])
    total = result.get("hits", {}).get("total", {}).get("value", 0)
    aggs  = result.get("aggregations", {})

    alertas = [format_alerta(h) for h in hits]
    resumo  = format_resumo_alertas(alertas, aggs, total)

    return {
        "resumo":  resumo,
        "alertas": alertas,
    }
