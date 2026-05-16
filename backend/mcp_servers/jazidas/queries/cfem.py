"""
CFEM Query Module — mr_cfem_v001
==================================

Dois padrões de consulta ao índice de royalties minerais (CFEM):

  1. historico_cfem_processo
     Retorna a série histórica mensal de arrecadação CFEM de um único processo
     ANM.  Usado pela tool ``consultar_cfem_processo``.

  2. ranking_cfem
     Agrega arrecadação por processo / CNPJ / município / substância e retorna
     os N maiores pagadores.  Usado pela tool ``ranking_cfem``.

Índice: mr_cfem_v001
Campos-chave:
    numero_processo  keyword  "910262/2007"
    cnpj_basico      keyword  primeiros 8 dígitos
    ano              integer  2002-presente
    mes              integer  1-12
    competencia      date     "2024-03-01"
    valor_arrecadado double   R$ (pode ser 0 para declarações zeradas)
    quantidade       double   toneladas / m³ / unidade
    unidade_medida   keyword  "t", "m3", "un", …
    substancia       keyword  "AREIA", "FERRO", …
    municipio        text     município da extração
    uf               keyword  "MG", "PA", …
"""

from __future__ import annotations

import unicodedata
import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.jazidas.cfem")

INDEX_CFEM = "mr_cfem_v001"
INDEX_JAZIDAS_ANM = "mr_jazidas_v001"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_processo(v: str) -> str:
    """Remove pontos/espaços; mantém barra e dígitos."""
    return v.strip().replace(".", "").replace(" ", "") if v else ""


def _normalize_kw(v: str) -> str:
    """Lowercase + remove acentos para comparação keyword."""
    nfkd = unicodedata.normalize("NFKD", v.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _coerce_opt_int(val: Any) -> int | None:
    """Converte valores vindos do MCP/JSON (string, float) para int ou None."""
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None
    return None


def parse_cnpj_basico_list(csv: str | None, *, max_items: int = 30) -> list[str]:
    """
    Extrai lista de CNPJ básico (8 dígitos) a partir de CSV ou tokens soltos.

    Aceita por item:
      - ``33592510`` (já básico)
      - ``33.592.510/0001-54`` (completo — usa os 8 primeiros dígitos)
    """
    if not csv or not str(csv).strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in str(csv).replace(";", ",").split(","):
        raw = part.strip()
        if not raw:
            continue
        digits = "".join(c for c in raw if c.isdigit())
        if len(digits) < 8:
            continue
        basico = digits[:8]
        if basico not in seen:
            seen.add(basico)
            out.append(basico)
        if len(out) >= max_items:
            break
    return out


def _fragmentos_titular_marca(csv: str | None) -> list[str]:
    """Quebra CSV/; de marcas ou trechos de razão social para expansão ANM."""
    if not csv or not str(csv).strip():
        return []
    s = str(csv).replace("|", ",").replace(";", ",")
    parts = [p.strip() for p in s.split(",")]
    return [p for p in parts if len(p) >= 3][:20]


async def _cnpj_basicos_via_titular_anm(
    os_service: OpenSearchService,
    fragmentos_csv: str,
    *,
    max_por_fragmento: int = 45,
) -> list[str]:
    """
    Lista CNPJ básico de titulares em ``mr_jazidas_v001`` que casam com cada
    fragmento (nome/razão). Os arquivos CFEM usam o declarante da declaração,
    que muitas vezes **não** é o mesmo ``cnpj_basico`` da matriz devolvida pela
    busca de empresas — esta expansão aproxima os raizes realmente presentes
    nos processos ANM.
    """
    seen: set[str] = set()
    out: list[str] = []
    for frag in _fragmentos_titular_marca(fragmentos_csv):
        body: dict[str, Any] = {
            "size": 0,
            "query": {
                "bool": {
                    "should": [
                        {"match": {"titular.nome": {"query": frag, "operator": "and"}}},
                        {"match": {"titular.razao_social": {"query": frag, "operator": "and"}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "aggs": {
                "cnps": {
                    "terms": {"field": "titular.cnpj_basico", "size": max_por_fragmento},
                },
            },
        }
        try:
            raw = await os_service.search(INDEX_JAZIDAS_ANM, body)
        except Exception as exc:
            logger.warning("cfem.expand_anm frag=%r falhou: %s", frag, exc)
            continue
        for b in (raw.get("aggregations") or {}).get("cnps", {}).get("buckets", []) or []:
            k = b.get("key")
            if not k:
                continue
            digits = "".join(c for c in str(k) if c.isdigit())
            if len(digits) < 8:
                continue
            ks8 = digits[:8]
            if ks8 not in seen:
                seen.add(ks8)
                out.append(ks8)
    logger.info(
        "cfem.expand_anm %d fragmentos → %d cnpj_basico",
        len(_fragmentos_titular_marca(fragmentos_csv)),
        len(out),
    )
    return out


def _build_date_filter(ano_inicio: int | None, ano_fim: int | None) -> list[dict]:
    """Gera cláusula de filtro por range de anos."""
    if not ano_inicio and not ano_fim:
        return []
    rng: dict[str, Any] = {}
    if ano_inicio:
        rng["gte"] = ano_inicio
    if ano_fim:
        rng["lte"] = ano_fim
    return [{"range": {"ano": rng}}]


def _filter_substancia_keyword(substancia: str) -> dict[str, Any]:
    """
    Filtro compatível com ``substancia`` (keyword) e ``substancia_desc`` (text).

    Evita ``match``+``fuzziness`` direto em ``substancia`` (keyword) — gera 400
    em várias versões do OpenSearch.
    """
    raw = (substancia or "").strip()
    if not raw:
        return {"match_all": {}}
    sub_norm = _normalize_kw(raw).upper()
    should: list[dict[str, Any]] = [
        {"term": {"substancia": sub_norm}},
        {"term": {"substancia": raw.upper()}},
        # Substring no código ANM (ex.: "MINÉRIO DE BAUXITA")
        {"wildcard": {"substancia": f"*{sub_norm}*"}},
        {
            "match": {
                "substancia_desc": {
                    "query": raw,
                    "operator": "and",
                    "fuzziness": "AUTO",
                }
            }
        },
    ]
    return {"bool": {"should": should, "minimum_should_match": 1}}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Histórico de um processo
# ─────────────────────────────────────────────────────────────────────────────

async def historico_cfem_processo(
    os_service: OpenSearchService,
    numero_processo: str,
    ano_inicio: int | None = None,
    ano_fim: int | None = None,
) -> dict[str, Any]:
    """
    Retorna a série histórica mensal de CFEM de um processo ANM.

    Estratégia:
      - Filtra por numero_processo (aceita "910262/2007", "910.262/2007" e
        variações sem o ano)
      - Ordena por ano/mês crescente
      - Agrega totais e sumariza substâncias envolvidas
    """
    processo_norm = _normalize_processo(numero_processo)
    if not processo_norm:
        return {"encontrado": False, "mensagem": "Número de processo inválido."}

    # numero_processo no índice é armazenado só com a parte numérica (ex: "910262").
    # Se o usuário informar "910262/2007", extrai só a parte antes da barra.
    num_part = processo_norm.split("/")[0]
    processo_filter: dict[str, Any] = {"term": {"numero_processo": num_part}}

    ano_inicio = _coerce_opt_int(ano_inicio)
    ano_fim = _coerce_opt_int(ano_fim)
    filters = [processo_filter] + _build_date_filter(ano_inicio, ano_fim)

    query: dict[str, Any] = {
        "size": 600,   # 50 anos × 12 meses
        "query": {
            "bool": {"filter": filters}
        },
        "sort": [
            {"ano": {"order": "asc"}},
            {"mes": {"order": "asc"}},
        ],
        "_source": [
            "numero_processo", "ano", "mes", "competencia",
            "valor_arrecadado", "quantidade", "unidade_medida",
            "substancia", "municipio", "uf",
        ],
        # Agrega totais e substâncias em uma única request
        "aggs": {
            "total_arrecadado":  {"sum": {"field": "valor_arrecadado"}},
            "total_quantidade":  {"sum": {"field": "quantidade"}},
            "substancias":       {"terms": {"field": "substancia", "size": 20}},
            "municipios":        {"terms": {"field": "municipio.keyword", "size": 10}},
            "anos":              {"terms": {"field": "ano", "size": 30, "order": {"_key": "asc"}}},
        },
    }

    logger.info(
        "cfem.historico_processo processo=%s ano_inicio=%s ano_fim=%s",
        processo_norm,
        ano_inicio,
        ano_fim,
    )

    raw    = await os_service.search(INDEX_CFEM, query)
    hits   = raw.get("hits", {}).get("hits", [])
    total  = (raw.get("hits", {}).get("total") or {})
    total  = total.get("value", 0) if isinstance(total, dict) else int(total)
    aggs   = raw.get("aggregations", {})

    if not hits:
        return {
            "encontrado": False,
            "mensagem": (
                f"Nenhum registro CFEM encontrado para o processo '{numero_processo}'. "
                "O processo pode nunca ter declarado arrecadação ou estar fora do período."
            ),
        }

    # Série mensal
    serie: list[dict] = []
    for hit in hits:
        src = hit.get("_source") or {}
        serie.append({
            "ano":               src.get("ano"),
            "mes":               src.get("mes"),
            "competencia":       src.get("competencia"),
            "valor_arrecadado":  round(float(src.get("valor_arrecadado") or 0), 2),
            "quantidade":        src.get("quantidade"),
            "unidade_medida":    src.get("unidade_medida"),
            "substancia":        src.get("substancia"),
            "municipio":         src.get("municipio"),
            "uf":                src.get("uf"),
        })

    # Sumariza substâncias e municípios das aggs
    subs_agg   = aggs.get("substancias", {}).get("buckets", [])
    munis_agg  = aggs.get("municipios", {}).get("buckets", [])
    anos_agg   = aggs.get("anos", {}).get("buckets", [])

    substancias = [b["key"] for b in subs_agg]
    municipios  = [b["key"] for b in munis_agg]
    anos_lista  = [b["key"] for b in anos_agg]

    total_arrecadado = round(float((aggs.get("total_arrecadado") or {}).get("value") or 0), 2)

    resumo = {
        "numero_processo":    numero_processo,  # preserva o formato informado pelo usuário
        "total_arrecadado":   total_arrecadado,
        "total_registros":    total,
        "ano_primeiro":       anos_lista[0]  if anos_lista else None,
        "ano_ultimo":         anos_lista[-1] if anos_lista else None,
        "anos_com_producao":  len(anos_lista),
        "substancias":        substancias,
        "municipios":         municipios,
        "uf":                 serie[0].get("uf") if serie else None,
    }

    logger.info(
        "cfem.historico_processo.ok processo=%s total=%s registros=%s",
        processo_norm,
        total_arrecadado,
        total,
    )

    return {
        "encontrado": True,
        "resumo":     resumo,
        "serie":      serie,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Ranking de maiores arrecadadores
# ─────────────────────────────────────────────────────────────────────────────

_AGRUPAMENTOS_VALIDOS = {"processo", "cnpj", "municipio", "substancia", "uf"}

_CAMPO_AGG: dict[str, str] = {
    "processo":   "numero_processo",
    "cnpj":       "cnpj_basico",
    "municipio":  "municipio.keyword",
    "substancia": "substancia",
    "uf":         "uf",
}

async def ranking_cfem(
    os_service: OpenSearchService,
    uf: str | None = None,
    substancia: str | None = None,
    ano_inicio: int | None = None,
    ano_fim: int | None = None,
    agrupar_por: str = "processo",
    top_n: int = 10,
    cnpj_basico: str | None = None,
    titular_anm_fragmentos: str | None = None,
) -> dict[str, Any]:
    """
    Retorna os maiores pagadores de CFEM agrupados pelo critério escolhido.

    Filtros opcionais:
      - uf:        limita a uma UF (ex: "MG", "PA")
      - substancia: limita a uma substância (ex: "FERRO", "AREIA")
      - ano_inicio / ano_fim: janela temporal
      - cnpj_basico: um ou mais CNPJs básicos (8 dígitos) ou CNPJ completo,
        separados por vírgula — restringe a declarações desses contribuintes.
      - titular_anm_fragmentos: marcas ou trechos de nome/razão (CSV) usados
        para buscar titulares em ``mr_jazidas_v001`` e **unir** os
        ``cnpj_basico`` encontrados ao filtro CFEM. Use junto com ``cnpj_basico``
        vindo da RFB (ex.: ``"Vale,Anglo American,Companhia Siderúrgica Nacional"``)
        para apanhar filiais/raízes que pagam CFEM mas não são a matriz da busca
        ``empresas``.
    Agrupamentos:
      - "processo"  → maiores processos pagadores
      - "cnpj"      → maiores empresas (CNPJ básico)
      - "municipio" → municípios com maior arrecadação
      - "substancia"→ substâncias com maior royalty total
      - "uf"        → UFs com maior arrecadação
    """
    agrupar_por = agrupar_por.lower().strip()
    if agrupar_por not in _AGRUPAMENTOS_VALIDOS:
        agrupar_por = "processo"

    ano_inicio = _coerce_opt_int(ano_inicio)
    ano_fim = _coerce_opt_int(ano_fim)
    tn = _coerce_opt_int(top_n)
    top_n = max(1, min(tn if tn is not None else 10, 50))

    cnpj_semente = parse_cnpj_basico_list(cnpj_basico)
    cnpj_anm: list[str] = []
    if titular_anm_fragmentos and str(titular_anm_fragmentos).strip():
        cnpj_anm = await _cnpj_basicos_via_titular_anm(
            os_service, titular_anm_fragmentos,
        )

    set_sem = set(cnpj_semente)
    cnpj_somente_anm = [c for c in cnpj_anm if c not in set_sem]

    merged: list[str] = []
    seen_m: set[str] = set()
    for c in cnpj_semente + cnpj_anm:
        if c not in seen_m:
            seen_m.add(c)
            merged.append(c)
    merged = merged[:80]

    if merged and len(merged) > top_n:
        # Garante espaço no terms agg para todos os CNPJs pedidos na comparação
        top_n = min(50, max(top_n, len(merged)))

    filters: list[dict] = []

    if uf:
        filters.append({"term": {"uf": uf.upper().strip()}})

    if substancia:
        filters.append(_filter_substancia_keyword(substancia))

    if merged:
        filters.append({"terms": {"cnpj_basico": merged}})

    filters += _build_date_filter(ano_inicio, ano_fim)

    campo_agg = _CAMPO_AGG[agrupar_por]

    # Sub-agg: top substâncias dentro de cada bucket (só para agrupamento por processo/cnpj)
    sub_agg_inner: dict[str, Any] = {
        "top_subs": {"terms": {"field": "substancia", "size": 5}},
    }
    if agrupar_por in ("processo", "cnpj"):
        sub_agg_inner["top_uf"] = {"terms": {"field": "uf", "size": 3}}
        sub_agg_inner["top_municipio"] = {"terms": {"field": "municipio.keyword", "size": 3}}

    query: dict[str, Any] = {
        "size": 0,
        "query": {
            "bool": {
                "filter": filters if filters else [{"match_all": {}}]
            }
        },
        "aggs": {
            "ranking": {
                "terms": {
                    "field":  campo_agg,
                    "size":   top_n,
                    "order":  {"total_valor": "desc"},
                },
                "aggs": {
                    "total_valor": {"sum":  {"field": "valor_arrecadado"}},
                    "total_qtde":  {"sum":  {"field": "quantidade"}},
                    "n_registros": {"value_count": {"field": "valor_arrecadado"}},
                    **sub_agg_inner,
                },
            },
            "total_geral": {"sum": {"field": "valor_arrecadado"}},
        },
    }

    logger.info(
        "cfem.ranking agrupar_por=%s uf=%s substancia=%s ano_inicio=%s ano_fim=%s "
        "top_n=%s cnpj_semente=%s cnpj_anm_extra=%d",
        agrupar_por,
        uf,
        substancia,
        ano_inicio,
        ano_fim,
        top_n,
        cnpj_semente or None,
        max(0, len(merged) - len(cnpj_semente)),
    )

    raw  = await os_service.search(INDEX_CFEM, query)
    aggs = raw.get("aggregations", {})

    buckets     = (aggs.get("ranking") or {}).get("buckets", [])
    total_geral = round(float((aggs.get("total_geral") or {}).get("value") or 0), 2)

    ranking: list[dict] = []
    for i, bucket in enumerate(buckets, start=1):
        chave = bucket.get("key") or bucket.get("key_as_string", "")
        total_v = round(float((bucket.get("total_valor") or {}).get("value") or 0), 2)
        total_q = (bucket.get("total_qtde") or {}).get("value")
        n_reg   = (bucket.get("n_registros") or {}).get("value", 0)

        subs = [b["key"] for b in (bucket.get("top_subs") or {}).get("buckets", [])]
        ufs  = [b["key"] for b in (bucket.get("top_uf") or {}).get("buckets", [])]
        muns = [b["key"] for b in (bucket.get("top_municipio") or {}).get("buckets", [])]

        entry: dict[str, Any] = {
            "posicao":          i,
            agrupar_por:        chave,
            "total_arrecadado": total_v,
            "n_declaracoes":    n_reg,
        }
        if total_q is not None:
            entry["total_quantidade"] = round(float(total_q), 3)
        if subs:
            entry["substancias"] = subs
        if ufs:
            entry["ufs"] = ufs
        if muns:
            entry["municipios"] = muns

        # Percentual do total geral
        if total_geral > 0:
            entry["pct_total"] = round(total_v / total_geral * 100, 2)

        ranking.append(entry)

    logger.info(
        "cfem.ranking.ok agrupar_por=%s n_buckets=%s total_geral=%s",
        agrupar_por,
        len(ranking),
        total_geral,
    )

    return {
        "agrupar_por":   agrupar_por,
        "filtros": {
            "uf":          uf,
            "substancia":  substancia,
            "ano_inicio":  ano_inicio,
            "ano_fim":     ano_fim,
            "cnpj_basico": merged or None,
            "cnpj_basico_semente": cnpj_semente or None,
            "cnpj_basico_expandido_anm": cnpj_somente_anm or None,
            "titular_anm_fragmentos": _fragmentos_titular_marca(titular_anm_fragmentos) or None,
        },
        "total_geral_arrecadado": total_geral,
        "ranking":       ranking,
    }
