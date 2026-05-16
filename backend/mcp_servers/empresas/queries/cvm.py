"""
CVM Query Module
================

Consultas ao índice mr_cvm_listadas_v001 (Companhias Abertas CVM).

Ferramentas expostas:
    buscar_empresa_cvm — busca por nome ou CNPJ, retorna ficha cadastral CVM
"""

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.empresas.queries.cvm")

INDEX_CVM = "mr_cvm_listadas_v001"

_TP_MERC_LABEL = {
    "BOLSA":                 "Bolsa (B3)",
    "BALCÃO ORGANIZADO":     "Balcão Organizado",
    "BALCÃO NÃO ORGANIZADO": "Balcão Não Organizado",
}

_CATEG_LABEL = {
    "A": "Categoria A (obrigações completas)",
    "B": "Categoria B (obrigações reduzidas)",
}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_cnpj(digits: str) -> str:
    """Formata 14 dígitos como XX.XXX.XXX/YYYY-ZZ."""
    d = digits.replace(".", "").replace("/", "").replace("-", "")
    if len(d) != 14:
        return digits
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"


def _fmt_brl(value: float | int | None) -> str | None:
    """Formata valor em BRL para exibição legível (ex: R$ 476,09 bilhões)."""
    if value is None:
        return None
    abs_val = abs(value)
    sign = "-" if value < 0 else ""
    if abs_val >= 1e12:
        return f"{sign}R$ {abs_val/1e12:.2f} trilhões"
    if abs_val >= 1e9:
        return f"{sign}R$ {abs_val/1e9:.2f} bilhões"
    if abs_val >= 1e6:
        return f"{sign}R$ {abs_val/1e6:.2f} milhões"
    return f"{sign}R$ {abs_val:,.0f}"


def _format_financeiro(fin: dict[str, Any] | None) -> dict[str, Any] | None:
    if not fin:
        return None
    result: dict[str, Any] = {}
    for campo in ("ativo_total", "receita_bruta", "resultado_bruto", "lucro_liquido"):
        raw = fin.get(campo)
        if raw is not None:
            result[campo] = raw
            result[f"{campo}_fmt"] = _fmt_brl(raw)
    for campo in ("dt_fim_exerc", "ano_dfp", "consolidado"):
        if fin.get(campo) is not None:
            result[campo] = fin[campo]
    return result if result else None


def _format_doc(src: dict[str, Any]) -> dict[str, Any]:
    cnpj_raw = (src.get("cnpj_cia") or "").strip()
    tp_merc_raw = (src.get("tp_merc") or "").strip().upper()
    categ_raw   = (src.get("categ_reg") or "").strip().upper()

    doc: dict[str, Any] = {
        "cnpj":          cnpj_raw or None,
        "cnpj_basico":   (src.get("cnpj_basico") or "").strip() or None,
        "cd_cvm":        (src.get("cd_cvm") or "").strip() or None,
        "razao_social":  (src.get("denom_social") or "").strip() or None,
        "nome_comercial": (src.get("denom_comerc") or "").strip() or None,
        "setor":         (src.get("setor_ativ") or "").strip() or None,
        "situacao":      (src.get("sit") or "").strip() or None,
        "situacao_emissor": (src.get("sit_emissor") or "").strip() or None,
        "mercado":       _TP_MERC_LABEL.get(tp_merc_raw, tp_merc_raw) or None,
        "categoria_registro": _CATEG_LABEL.get(categ_raw, categ_raw) or None,
        "controle_acionario": (src.get("controle_acionario") or "").strip() or None,
        "dt_registro":   src.get("dt_reg"),
        "dt_constituicao": src.get("dt_const"),
        "dt_cancelamento": src.get("dt_cancel"),
        "motivo_cancelamento": (src.get("motivo_cancel") or "").strip() or None,
        "uf":            (src.get("uf") or "").strip() or None,
        "municipio":     (src.get("mun") or "").strip() or None,
        "email":         (src.get("email") or "").strip() or None,
        "auditor":       (src.get("auditor") or "").strip() or None,
        "criterio_inclusao": src.get("criterio_inclusao") or [],
        "listada_b3":    tp_merc_raw == "BOLSA",
        "financeiro":    _format_financeiro(src.get("financeiro")),
    }
    return {k: v for k, v in doc.items() if v is not None and v != [] and v != ""}


# ─────────────────────────────────────────────────────────────────────────────
# Public query function
# ─────────────────────────────────────────────────────────────────────────────

async def executar_buscar_empresa_cvm(
    os_service: OpenSearchService,
    nome: str | None = None,
    cnpj: str | None = None,
    apenas_ativas: bool = True,
    apenas_listadas_bolsa: bool = False,
    max_resultados: int = 10,
) -> dict[str, Any]:
    """
    Busca uma empresa no índice CVM (mr_cvm_listadas_v001).

    Prioridade: CNPJ > nome (match_phrase + multi_match)
    """
    if not nome and not cnpj:
        return {"erro": "Informe pelo menos 'nome' ou 'cnpj'."}

    filters: list[dict] = []
    must: list[dict] = []

    if apenas_ativas:
        filters.append({"term": {"sit": "ATIVO"}})
    if apenas_listadas_bolsa:
        filters.append({"term": {"tp_merc": "BOLSA"}})

    if cnpj:
        # Normalise: aceita com ou sem pontuação
        digits = "".join(c for c in cnpj if c.isdigit())
        basico = digits[:8]
        if len(digits) == 14:
            must.append({"term": {"cnpj_cia": _fmt_cnpj(digits)}})
        elif basico:
            must.append({"term": {"cnpj_basico": basico}})
    elif nome:
        must.append({
            "multi_match": {
                "query": nome,
                "fields": ["denom_social^2", "denom_comerc"],
                "type": "best_fields",
                "fuzziness": "AUTO",
            }
        })

    query: dict = {"bool": {"must": must, "filter": filters}}

    try:
        resp = await os_service.search(
            index=INDEX_CVM,
            body={
                "size": max_resultados,
                "query": query,
                "sort": [{"_score": "desc"}],
                "_source": True,
            },
        )
    except Exception as e:
        logger.error(f"buscar_empresa_cvm: erro OpenSearch: {e}")
        return {"erro": str(e)}

    hits = resp.get("hits", {}).get("hits", [])
    total = resp.get("hits", {}).get("total", {})
    total_val = total.get("value", 0) if isinstance(total, dict) else int(total)

    if not hits:
        filtros_aplicados = []
        if apenas_ativas:
            filtros_aplicados.append("apenas ativas")
        if apenas_listadas_bolsa:
            filtros_aplicados.append("apenas listadas em bolsa")
        msg = f"Nenhuma empresa CVM encontrada"
        if filtros_aplicados:
            msg += f" com filtros: {', '.join(filtros_aplicados)}"
        return {
            "total": 0,
            "empresas": [],
            "mensagem": msg,
        }

    empresas = [_format_doc(h["_source"]) for h in hits]

    return {
        "total": total_val,
        "exibindo": len(empresas),
        "empresas": empresas,
        "fonte": "CVM — Companhias Abertas (dados.cvm.gov.br)",
    }


async def executar_buscar_cvm_por_cnpj_basico(
    os_service: OpenSearchService,
    cnpj_basico: str,
) -> dict[str, Any] | None:
    """
    Busca rápida de dados CVM para um cnpj_basico (8 dígitos).
    Retorna o primeiro resultado ou None se não encontrado.
    Usado como enriquecimento em detalhes_empresa.
    """
    basico = cnpj_basico[:8] if len(cnpj_basico) >= 8 else cnpj_basico
    try:
        resp = await os_service.search(
            index=INDEX_CVM,
            body={
                "size": 1,
                "query": {"term": {"cnpj_basico": basico}},
                "_source": True,
            },
        )
    except Exception as e:
        logger.warning(f"buscar_cvm_por_cnpj_basico: {e}")
        return None

    hits = resp.get("hits", {}).get("hits", [])
    if not hits:
        return None
    return _format_doc(hits[0]["_source"])
