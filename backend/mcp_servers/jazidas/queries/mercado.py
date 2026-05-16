"""
Mercado Mineral Query Module
==============================

Queries para ``mr_mercado_v001`` (ComexStat/MDIC — 66.771 docs, 2019-2025).

Expõe dois contextos de consulta:

    1. consultar_mercado_mineral  — tendência anual de exp/imp por substância
       ou NCM direto, com breakdown por UF e top países destino/origem.

    2. principais_destinos_mineral — ranking de países para uma substância/NCM,
       com série temporal opcional.

Resolução substância → NCM:
    O índice armazena NCMs (códigos de 8 dígitos), não substâncias ANM.
    Há um mapa curado (atalho) para substâncias frequentes; para **qualquer**
    outra expressão, os NCMs são **descobertos dinamicamente** no índice
    (agregação sobre ``ncm_desc`` no período e fluxo pedidos). Se a descoberta
    falhar, cai na busca textual clássica em ``ncm_desc``.

Índice:
    mr_mercado_v001  (66.771 docs — granularidade ncm × fluxo × uf × ano)

Performance esperada: ~15-30ms (agg sobre índice pequeno)
"""

from __future__ import annotations

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.jazidas.queries.mercado")

INDEX_MERCADO = "mr_mercado_v001"

# ─────────────────────────────────────────────────────────────────────────────
# Mapa substância ANM → NCMs primários do ComexStat
# Cobertura: ~40 substâncias mais relevantes para a mineração brasileira.
# Lista de NCMs em ordem de relevância (primeiro = mais representativo).
# ─────────────────────────────────────────────────────────────────────────────
SUBSTANCIA_NCM_MAP: dict[str, list[str]] = {
    # Metais ferrosos
    "ferro":          ["26011100", "26011210", "26011290", "72011000"],
    "minério de ferro": ["26011100", "26011210"],
    "pelota de ferro": ["26011290"],

    # Metais não ferrosos
    "cobre":          ["26030010", "26030090", "74011000", "74040000"],
    "alumínio":       ["76011000", "76012000"],
    "bauxita":        ["26060011", "26060019"],
    "níquel":         ["26040000", "75011000", "75021000"],
    "zinco":          ["26080010", "79011111", "79011119"],
    "estanho":        ["26090000", "80011000", "80012000"],
    "chumbo":         ["26070000", "78011000"],
    "manganês":       ["26020090", "72024000"],
    "cromo":          ["26100000", "72021000"],
    "titânio":        ["26140000", "81081000"],
    "vanádio":        ["26159000", "72022100"],
    "cobalto":        [
        "26050000", "81051000", "81052010", "81052021", "81052090",
        "81053000", "81059010", "81059090",
        "28220010", "28220090", "28273997", "28444320",
    ],
    "cobalto refinado": [
        "81052010", "81052021", "81052090", "81059010", "81059090",
        "28220010", "28220090", "28273997", "28444320",
        "81051000", "26050000",
    ],
    "molibdênio":     ["26131000", "81021000"],
    "antimônio":      ["26170090", "81041000"],

    # Metais preciosos
    "ouro":           ["71081210", "71081310", "71081390", "26169000"],
    "prata":          ["71069110", "71069190"],
    "platina":        ["71101100", "71101900"],
    "diamante":       ["71021000", "71022100", "71023100"],

    # Minerais estratégicos/raros
    "nióbio":         ["72029300", "81129900", "26159000"],
    "lítio":          ["25309010", "28259090"],   # espodumênio + carbonato
    "espodumênio":    ["25309010"],
    "grafita":        ["25040010", "25040090"],
    "terras raras":   ["28469000", "28469010"],
    "urânio":         ["26121000", "26122000"],
    "fluorita":       ["25292100", "25292200"],
    "apatita":        ["25101010", "25101020"],

    # Minerais industriais
    "calcário":       ["25210000", "25221000"],
    "caulim":         ["25070010", "25070090"],
    "talco":          ["25261000", "25262000"],
    "cianita":        ["25083000"],
    "vermiculita":    ["25309050"],
    "amianto":        ["25241000", "25249000"],
    "mica":           ["25251000", "25252000"],
    "feldspato":      ["25291000"],
    "gipsita":        ["25201000"],
    "sal":            ["25010010", "25010090"],
    "enxofre":        ["25030010"],
    "potássio":       ["31042000", "28351100"],   # cloreto + silvinita
    "fosfato":        ["25101020", "28092010"],
    "argila":         ["25082000", "25085000"],
    "areia":          ["25051000", "25059010"],
    "granito":        ["25162100", "25162200", "68022100"],
    "mármore":        ["25151100", "25151200", "68021000"],
    "quartzito":      ["25171000"],
    "carvão":         ["27011100", "27011200", "27011900"],
    "petróleo":       ["27090010"],
}

# Normalização de nomes para lookup
_NORMALIZED_MAP: dict[str, list[str]] = {
    k.lower().strip(): v for k, v in SUBSTANCIA_NCM_MAP.items()
}


def resolve_ncms(substancia_ou_ncm: str) -> tuple[list[str], str]:
    """
    Resolve uma substância ou NCM para lista de NCM codes.

    Returns:
        (ncm_codes, tipo) onde tipo é "ncm_direto" | "substancia_mapa" | "texto"
    """
    s = substancia_ou_ncm.strip()

    # NCM direto: 8 dígitos (aceita máscara 2615.90.00 ou 26159000)
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) == 8:
        return [digits], "ncm_direto"
    if len(s) == 2 and s.isdigit():
        return [], f"capitulo_{s}"

    # Lookup no mapa curado (chave exata)
    sn = s.lower()
    ncms = _NORMALIZED_MAP.get(sn)
    if ncms:
        return ncms, "substancia_mapa"

    # Frase composta: usa a chave mais longa presente em `sn` (ex.: "cobalto refinado"
    # antes de só "cobalto"). Ignora chaves muito curtas (<5) para evitar ruído.
    best_k: str | None = None
    for k in _NORMALIZED_MAP.keys():
        if len(k) < 5:
            continue
        if k in sn:
            if best_k is None or len(k) > len(best_k):
                best_k = k
    if best_k:
        return _NORMALIZED_MAP[best_k], "substancia_mapa"

    # Sem match direto → busca textual (tratado na query)
    return [], "texto"


# Palavras muito comuns em perguntas de comércio — removidas para a descoberta dinâmica
_STOP_NCM_DESC: frozenset[str] = frozenset({
    "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas", "por", "para",
    "com", "sem", "sobre", "que", "qual", "quais", "quanto", "o", "a", "os", "as",
    "um", "uma", "uns", "umas", "ao", "aos", "à", "às", "pelo", "pela", "pelos",
    "pelas", "brasil", "brasileira", "brasileiro", "export", "exporta", "exportação",
    "exportacao", "import", "importa", "importação", "importacao", "fob", "usd",
    "volume", "valor", "anos", "ano", "foi", "são", "ser", "mais", "menos",
    "entre", "desde", "ate", "até", "e", "ou",
})


def _frase_para_busca_ncm_desc(frase: str) -> str:
    """
    Reduz a frase do utilizador a termos provavelmente presentes em ``ncm_desc``.

    Remove stopwords PT e pontuação; mantém tokens com 2+ caracteres úteis.
    """
    import re
    import unicodedata

    def norm_token(w: str) -> str:
        return unicodedata.normalize("NFD", w).encode("ascii", "ignore").decode().lower()

    raw = re.split(r"[\s,/;|]+", frase.strip())
    out: list[str] = []
    for tok in raw:
        t = tok.strip(".,;:!?\"'()[]{}").strip()
        if len(t) < 2:
            continue
        if norm_token(t) in _STOP_NCM_DESC:
            continue
        out.append(t)
    return " ".join(out) if out else frase.strip()


def _safe_simple_query_string(q: str) -> str:
    """Evita caracteres que quebram o simple_query_string."""
    return q.replace("\\", " ").replace('"', " ").replace("+", " ")[:220].strip() or "*"


async def _descobrir_ncms_por_descricao(
    os_service: OpenSearchService,
    frase: str,
    fluxo: str,
    ano_inicio: int,
    ano_fim: int,
    uf: str | None,
    *,
    ano_exato: int | None = None,
    limit: int = 48,
) -> list[str]:
    """
    Agrega os NCMs mais relevantes no período cujo ``ncm_desc`` casa com a frase.

    Usa ``simple_query_string`` (OR) sobre o texto limpo — funciona para minerais
    fora do mapa curado.
    """
    q = _safe_simple_query_string(_frase_para_busca_ncm_desc(frase))
    if not q or q == "*":
        q = _safe_simple_query_string(frase[:220])

    filters: list[dict[str, Any]] = [
        {"term": {"fluxo": fluxo}},
        {"range": {"ano": {"gte": ano_inicio, "lte": ano_fim}}},
    ]
    if ano_exato is not None:
        filters.append({"term": {"ano": ano_exato}})
    if uf:
        filters.append({"term": {"uf": uf.upper().strip()}})

    body: dict[str, Any] = {
        "size": 0,
        "query": {
            "bool": {
                "filter": filters,
                "must": [
                    {
                        "simple_query_string": {
                            "query":             q,
                            "fields":            ["ncm_desc"],
                            "default_operator":  "OR",
                        }
                    }
                ],
            }
        },
        "aggs": {
            "ncm_rank": {
                "terms": {"field": "ncm", "size": limit, "order": {"vl": "desc"}},
                "aggs": {"vl": {"sum": {"field": "vl_fob_usd"}}},
            }
        },
    }
    try:
        raw = await os_service.search(INDEX_MERCADO, body)
    except Exception as exc:
        logger.warning("mercado.discover_ncm falhou: %s", exc)
        return []

    buckets = (raw.get("aggregations") or {}).get("ncm_rank", {}).get("buckets", []) or []
    out: list[str] = []
    for b in buckets:
        k = b.get("key")
        if k:
            out.append(str(k))
    if out:
        logger.info(
            "mercado.discover_ncm frase=%r fluxo=%s periodo=%s-%s → %d ncms",
            frase[:80], fluxo, ano_inicio, ano_fim, len(out),
        )
    return out


async def _resolver_ncms_com_dinamico(
    os_service: OpenSearchService,
    substancia_ou_ncm: str,
    fluxo: str,
    ano_inicio: int,
    ano_fim: int,
    uf: str | None,
    *,
    ano_exato: int | None = None,
    max_ncms: int = 48,
) -> tuple[list[str], str | None, str | None, str]:
    """
    Junta mapa curado (se houver) com NCMs descobertos no índice.

    Returns:
        (ncm_codes, capitulo, texto_fallback, tipo_efetivo)
        ``texto_fallback`` só quando não há NCMs para usar ``terms``.
    """
    ncm_codes, tipo = resolve_ncms(substancia_ou_ncm)
    if tipo.startswith("capitulo_"):
        capitulo = tipo.split("_", 1)[1]
        return [], capitulo, None, tipo
    if tipo == "ncm_direto":
        return ncm_codes, None, None, tipo

    discovered = await _descobrir_ncms_por_descricao(
        os_service,
        substancia_ou_ncm,
        fluxo,
        ano_inicio,
        ano_fim,
        uf,
        ano_exato=ano_exato,
        limit=max_ncms,
    )

    merged: list[str] = []
    seen: set[str] = set()
    for c in ncm_codes + discovered:
        if c not in seen:
            seen.add(c)
            merged.append(c)

    if merged:
        eff = tipo if tipo == "substancia_mapa" else "dinamico_ncm"
        if discovered and tipo == "substancia_mapa":
            eff = "mapa_e_dinamico"
        return merged[:max_ncms], None, None, eff

    return [], None, substancia_ou_ncm.strip(), "texto"


# ─────────────────────────────────────────────────────────────────────────────
# Query builders
# ─────────────────────────────────────────────────────────────────────────────

def build_mercado_query(
    ncm_codes: list[str],
    capitulo: str | None,
    texto: str | None,
    fluxo: str,
    uf: str | None,
    ano_inicio: int,
    ano_fim: int,
) -> dict[str, Any]:
    """
    Monta query agregada por ano para tendência temporal.
    Filtros: NCMs ou capítulo, fluxo, UF, período.
    """
    filters: list[dict] = [
        {"term": {"fluxo": fluxo}},
        {"range": {"ano": {"gte": ano_inicio, "lte": ano_fim}}},
    ]

    if ncm_codes:
        filters.append({"terms": {"ncm": ncm_codes}})
    elif capitulo:
        filters.append({"term": {"ncm_capitulo": capitulo}})
    elif texto:
        filters.append({
            "simple_query_string": {
                "query": _safe_simple_query_string(
                    _frase_para_busca_ncm_desc(texto) or texto,
                ),
                "fields":            ["ncm_desc"],
                "default_operator":  "OR",
            }
        })

    if uf:
        filters.append({"term": {"uf": uf.upper()}})

    return {
        "size": 0,
        "query": {"bool": {"filter": filters}},
        "aggs": {
            # Tendência anual
            "por_ano": {
                "terms": {"field": "ano", "size": 20, "order": {"_key": "asc"}},
                "aggs": {
                    "vl_fob":    {"sum": {"field": "vl_fob_usd"}},
                    "kg":        {"sum": {"field": "kg_liquido"}},
                    "n_meses":   {"avg": {"field": "n_meses"}},
                },
            },
            # UFs mais relevantes (todo o período)
            "por_uf": {
                "terms": {"field": "uf", "size": 10},
                "aggs": {"vl_fob": {"sum": {"field": "vl_fob_usd"}}},
            },
            # NCMs na seleção (para saber o que foi encontrado via texto)
            "por_ncm": {
                "terms": {"field": "ncm", "size": 10},
                "aggs": {"vl_fob": {"sum": {"field": "vl_fob_usd"}}},
            },
            # Totais gerais
            "total_vl_fob": {"sum": {"field": "vl_fob_usd"}},
            "total_kg":     {"sum": {"field": "kg_liquido"}},
        },
    }


def build_destinos_query(
    ncm_codes: list[str],
    capitulo: str | None,
    texto: str | None,
    fluxo: str,
    uf: str | None,
    ano: int | None,
) -> dict[str, Any]:
    """
    Monta query para ranking de países com breakdown por NCM.
    Agrega top_paises diretamente dos arrays armazenados.
    """
    filters: list[dict] = [{"term": {"fluxo": fluxo}}]

    if ncm_codes:
        filters.append({"terms": {"ncm": ncm_codes}})
    elif capitulo:
        filters.append({"term": {"ncm_capitulo": capitulo}})
    elif texto:
        filters.append({
            "simple_query_string": {
                "query": _safe_simple_query_string(
                    _frase_para_busca_ncm_desc(texto) or texto,
                ),
                "fields":            ["ncm_desc"],
                "default_operator":  "OR",
            }
        })

    if uf:
        filters.append({"term": {"uf": uf.upper()}})
    if ano:
        filters.append({"term": {"ano": ano}})

    return {
        "size": 20,   # busca docs para extrair top_paises manualmente
        "_source": [
            "ncm", "ncm_desc", "uf", "ano",
            "vl_fob_usd", "top_paises", "top_paises_cod", "top_paises_vl_fob",
        ],
        "query": {"bool": {"filter": filters}},
        "sort": [{"vl_fob_usd": "desc"}],
        "aggs": {
            "total_vl": {"sum": {"field": "vl_fob_usd"}},
            "por_ncm":  {
                "terms": {"field": "ncm", "size": 10},
                "aggs":  {"vl": {"sum": {"field": "vl_fob_usd"}}},
            },
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────

def format_tendencia_anual(aggs: dict) -> list[dict]:
    """Converte agregação por_ano em série temporal limpa."""
    series = []
    for b in aggs.get("por_ano", {}).get("buckets", []):
        vl = b["vl_fob"].get("value", 0) or 0
        kg = b["kg"].get("value", 0) or 0
        series.append({
            "ano":        b["key"],
            "vl_fob_usd": round(vl, 0),
            "kg_liquido": round(kg, 0),
            "vl_fob_bi":  round(vl / 1e9, 3),
        })
    return series


def _aggregate_top_paises(hits: list[dict], top_n: int = 15) -> list[dict]:
    """
    Agrega top países a partir dos arrays top_paises/top_paises_vl_fob dos docs.

    Como cada doc já tem os top-10 países, somamos os valores por nome de país.
    """
    country_totals: dict[str, float] = {}
    for hit in hits:
        s = hit.get("_source", {})
        paises = s.get("top_paises") or []
        valores = s.get("top_paises_vl_fob") or []
        for pais, vl in zip(paises, valores):
            if pais and vl:
                country_totals[pais] = country_totals.get(pais, 0) + vl

    ranked = sorted(country_totals.items(), key=lambda x: -x[1])
    total = sum(country_totals.values()) or 1

    return [
        {
            "pais":       pais,
            "vl_fob_usd": round(vl, 0),
            "vl_fob_bi":  round(vl / 1e9, 3),
            "share_pct":  round(vl / total * 100, 1),
        }
        for pais, vl in ranked[:top_n]
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrators
# ─────────────────────────────────────────────────────────────────────────────

async def executar_consultar_mercado_mineral(
    os_service: OpenSearchService,
    substancia_ou_ncm: str,
    fluxo: str = "export",
    uf: str | None = None,
    ano_inicio: int = 2019,
    ano_fim: int = 2025,
) -> dict[str, Any]:
    """
    Consulta tendência de exportação/importação para uma substância ou NCM.

    Steps:
      1. Resolve NCM (código direto, capítulo, mapa curado e/ou **descoberta dinâmica**
         no índice por ``ncm_desc``); se nada for encontrado, busca textual ampla.
      2. Agrega valores por ano (tendência) + por UF (concentração geográfica)
      3. Retorna série temporal + contexto interpretativo
    """
    ncm_codes, capitulo, texto, tipo_resolucao = await _resolver_ncms_com_dinamico(
        os_service,
        substancia_ou_ncm,
        fluxo,
        ano_inicio,
        ano_fim,
        uf,
        ano_exato=None,
    )

    query = build_mercado_query(
        ncm_codes, capitulo, texto, fluxo, uf, ano_inicio, ano_fim
    )

    try:
        result = await os_service.search(INDEX_MERCADO, query)
    except Exception as e:
        logger.error(f"mercado: falha na query: {e}")
        return {"erro": str(e)}

    aggs = result.get("aggregations", {})

    tendencia    = format_tendencia_anual(aggs)
    total_vl     = aggs.get("total_vl_fob", {}).get("value", 0) or 0
    total_kg     = aggs.get("total_kg", {}).get("value", 0) or 0
    por_uf_raw   = aggs.get("por_uf", {}).get("buckets", [])
    por_ncm_raw  = aggs.get("por_ncm", {}).get("buckets", [])

    por_uf = [
        {"uf": b["key"], "vl_fob_usd": round(b["vl_fob"]["value"] or 0, 0),
         "share_pct": round((b["vl_fob"]["value"] or 0) / (total_vl or 1) * 100, 1)}
        for b in por_uf_raw
    ]
    por_ncm = [
        {"ncm": b["key"], "vl_fob_usd": round(b["vl_fob"]["value"] or 0, 0)}
        for b in por_ncm_raw
    ]

    # Variação entre primeiro e último ano disponíveis
    variacao_pct = None
    if len(tendencia) >= 2:
        v_ini = tendencia[0]["vl_fob_usd"]
        v_fim = tendencia[-1]["vl_fob_usd"]
        if v_ini > 0:
            variacao_pct = round((v_fim - v_ini) / v_ini * 100, 1)

    logger.info(
        f"mercado: substancia={substancia_ou_ncm!r} fluxo={fluxo} uf={uf} "
        f"→ total={total_vl/1e9:.1f}bi NCMs={[b['ncm'] for b in por_ncm[:3]]}"
    )

    return {
        "consulta": {
            "substancia_ou_ncm": substancia_ou_ncm,
            "fluxo":             fluxo,
            "uf_filtro":         uf,
            "periodo":           f"{ano_inicio}-{ano_fim}",
            "ncms_resolvidos":   ncm_codes or [f"busca textual: {texto or substancia_ou_ncm}"],
            "tipo_resolucao":    tipo_resolucao,
        },
        "resumo": {
            "total_vl_fob_usd_periodo": round(total_vl, 0),
            "total_vl_fob_bi_periodo":  round(total_vl / 1e9, 2),
            "total_kg_periodo":         round(total_kg, 0),
            "variacao_pct_periodo":     variacao_pct,
            "ncms_encontrados":         por_ncm,
        },
        "tendencia_anual":    tendencia,
        "concentracao_por_uf": por_uf,
    }


async def executar_principais_destinos_mineral(
    os_service: OpenSearchService,
    substancia_ou_ncm: str,
    fluxo: str = "export",
    uf: str | None = None,
    ano: int | None = None,
) -> dict[str, Any]:
    """
    Retorna ranking de países destino/origem para uma substância ou NCM.

    Extrai os top países dos arrays pré-calculados em cada doc
    e agrega os valores para montar o ranking global.

    Returns:
        Dict com ranking de países + valor total + série por NCM.
    """
    if ano is not None:
        ncm_codes, capitulo, texto, tipo_resolucao = await _resolver_ncms_com_dinamico(
            os_service,
            substancia_ou_ncm,
            fluxo,
            ano,
            ano,
            uf,
            ano_exato=ano,
        )
    else:
        ncm_codes, capitulo, texto, tipo_resolucao = await _resolver_ncms_com_dinamico(
            os_service,
            substancia_ou_ncm,
            fluxo,
            2019,
            2025,
            uf,
            ano_exato=None,
        )

    query = build_destinos_query(ncm_codes, capitulo, texto, fluxo, uf, ano)

    try:
        result = await os_service.search(INDEX_MERCADO, query)
    except Exception as e:
        logger.error(f"mercado_destinos: falha na query: {e}")
        return {"erro": str(e)}

    hits = result.get("hits", {}).get("hits", [])
    aggs = result.get("aggregations", {})

    top_paises = _aggregate_top_paises(hits)
    total_vl   = aggs.get("total_vl", {}).get("value", 0) or 0
    por_ncm    = [
        {"ncm": b["key"], "vl_fob_usd": round(b["vl"]["value"] or 0, 0)}
        for b in aggs.get("por_ncm", {}).get("buckets", [])
    ]

    destino_label = "destinos" if fluxo == "export" else "origens"
    ano_label = str(ano) if ano else "todos os anos disponíveis"

    logger.info(
        f"mercado_destinos: {substancia_ou_ncm!r} {fluxo} {ano_label} "
        f"→ top={[p['pais'] for p in top_paises[:5]]}"
    )

    return {
        "consulta": {
            "substancia_ou_ncm": substancia_ou_ncm,
            "fluxo":             fluxo,
            "uf_filtro":         uf,
            "ano":               ano_label,
            "ncms_resolvidos":   ncm_codes or [f"busca textual: {texto or substancia_ou_ncm}"],
            "tipo_resolucao":    tipo_resolucao,
        },
        "resumo": {
            "total_vl_fob_usd": round(total_vl, 0),
            "total_vl_fob_bi":  round(total_vl / 1e9, 2),
            f"top_{destino_label}": top_paises,
            "ncms_encontrados": por_ncm,
        },
    }
