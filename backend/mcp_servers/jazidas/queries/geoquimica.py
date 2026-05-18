"""
CPRM Geoquímica — Query Module
================================

Queries para ``mr_geoquimica_v001`` (SGB/CPRM — ~65K análises geoquímicas).

Expõe as tools ``geoquimica_proxima`` e ``geoquimica_detalhes_amostra``:
  - Busca amostras por geo_distance (lat/lon + raio)
  - Detalhe por ``id_amostra`` (código de campo CPRM, ex. ``1182-LK-R-0039B``)
  - Filtro opcional por um ou vários analitos (``Au``, ``Ce, La, Nd``, ``Nb ou Ta``) — **OU**
  - ``term`` com ``case_insensitive`` (o índice guarda ``Au``, ``Ce``, não ``AU``)
  - Filtro opcional por classe (Rocha | Mineral/Minério)
  - Retorna resumo de analitos detectados + lista de amostras com valores
  - Retorna ``mapa.pontos`` para visualização no frontend

Índice:
    mr_geoquimica_v001  (~77K amostras API; _count = raiz; _cat inclui nested analises)

Performance esperada: ~20–50ms (geo_distance + optional term filter)
"""
from __future__ import annotations

import logging
import re
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.jazidas.queries.geoquimica")

INDEX_GEO = "mr_geoquimica_v001"

# Analitos considerados estratégicos / de alto interesse mineralógico
ANALITOS_ESTRATEGICOS = {
    "Au", "Pt", "Pd",           # preciosos
    "Li", "Nb", "Ta", "Co",     # estratégicos
    "Ce", "La", "Nd", "Pr",     # terras raras leves
    "Dy", "Tb", "Y",            # terras raras pesadas
    "U", "Th",                  # radioativos
    "W", "Mo", "Sn",            # metais especiais
}

MAX_AMOSTRAS = 25

# CPRM campo / id de documento: 1182-LK-R-0039B, GEO:4212-PD-R-0010A
_SAMPLE_ID_RE = re.compile(
    r"^(?:GEO:)?\d{3,5}[-/][A-Za-z]{1,6}[-/][A-Za-z]{1,3}[-/][A-Za-z0-9]+$",
    re.IGNORECASE,
)


def parse_analitos_param(analito: str | None) -> list[str]:
    """
    Converte o parâmetro livre ``analito`` numa lista de símbolos químicos.

    Aceita CSV, ``;``, ``|``, e conectores em PT/EN (``ou`` / ``or``).
    Deduplica sem alterar capitalização (o índice usa ``Au``, ``Ce``, … —
    **não** ``AU``/``CE`` em ``term`` sem ``case_insensitive``).
    """
    if not analito or not str(analito).strip():
        return []
    s = str(analito).strip()
    for sep in (" OU ", " Ou ", " ou ", " OR ", " or ", " e ", " E "):
        s = s.replace(sep, ",")
    parts: list[str] = []
    for chunk in re.split(r"[,;/|]", s):
        t = chunk.strip()
        if 1 <= len(t) <= 8:
            parts.append(t)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        k = p.upper()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out[:15]


def _term_analito_ci(field: str, valor: str) -> dict[str, Any]:
    """``term`` em campo keyword com comparação insensível a maiúsculas."""
    return {"term": {field: {"value": valor, "case_insensitive": True}}}


# ─────────────────────────────────────────────────────────────────────────────
# Query builders
# ─────────────────────────────────────────────────────────────────────────────

def build_geoquimica_geo_query(
    lat: float,
    lon: float,
    raio_km: float,
    analito: str | None = None,
    classe: str | None = None,
    valor_min: float | None = None,
    size: int = MAX_AMOSTRAS,
) -> dict[str, Any]:
    """
    Busca amostras geoquímicas dentro de um raio, com filtros opcionais.

    O filtro por ``analito`` usa o campo keyword ``analitos`` com
    ``case_insensitive`` (o GeoBank indexa ``Au``, ``Ce``, …).
    Vários símbolos (ex.: ``"Ce, La, Nd"``) aplicam lógica **OU**.
    O filtro ``valor_min`` usa nested query; com vários símbolos, basta
    **um** analito satisfazer o limiar.
    """
    filters: list[dict] = [
        {
            "geo_distance": {
                "distance": f"{raio_km}km",
                "location": {"lat": lat, "lon": lon},
            }
        }
    ]

    tokens = parse_analitos_param(analito)

    if tokens:
        if len(tokens) == 1:
            filters.append(_term_analito_ci("analitos", tokens[0]))
        else:
            filters.append({
                "bool": {
                    "should": [_term_analito_ci("analitos", t) for t in tokens],
                    "minimum_should_match": 1,
                }
            })

    if classe:
        filters.append({"term": {"classe": classe}})

    if tokens and valor_min is not None:
        if len(tokens) == 1:
            filters.append({
                "nested": {
                    "path": "analises",
                    "query": {
                        "bool": {
                            "filter": [
                                _term_analito_ci("analises.analito", tokens[0]),
                                {"range": {"analises.valor": {"gte": valor_min}}},
                            ]
                        }
                    },
                },
            })
        else:
            nested_should = [
                {
                    "bool": {
                        "filter": [
                            _term_analito_ci("analises.analito", t),
                            {"range": {"analises.valor": {"gte": valor_min}}},
                        ]
                    }
                }
                for t in tokens
            ]
            filters.append({
                "nested": {
                    "path": "analises",
                    "query": {
                        "bool": {
                            "should": nested_should,
                            "minimum_should_match": 1,
                        }
                    },
                },
            })

    return {
        "size": size,
        "query": {"bool": {"filter": filters}},
        "_source": [
            "id_amostra", "classe", "projeto", "projeto_publicacao",
            "laboratorio", "abertura", "leitura",
            "classificacao_petrografica", "unidade_litoestratigrafica",
            "analises", "analitos",
            "location", "data_de_analise", "duplicata", "observacao",
        ],
        "sort": [
            {
                "_geo_distance": {
                    "location": {"lat": lat, "lon": lon},
                    "order":    "asc",
                    "unit":     "km",
                }
            }
        ],
        "aggs": {
            "por_analito":     {"terms": {"field": "analitos",         "size": 40}},
            "por_classe":      {"terms": {"field": "classe"}},
            "por_projeto":     {"terms": {"field": "projeto.keyword",  "size": 10}},
            "por_laboratorio": {"terms": {"field": "laboratorio.keyword", "size": 5}},
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────

def _distancia_km(sort_value: list | None) -> float | None:
    if sort_value and len(sort_value) > 0:
        v = sort_value[0]
        if isinstance(v, (int, float)):
            return round(float(v), 2)
    return None


def format_amostra(
    hit: dict,
    analitos_alvo: list[str] | None = None,
) -> dict:
    """Formata um hit do mr_geoquimica_v001 para output limpo."""
    src = hit.get("_source", {})
    dist = _distancia_km(hit.get("sort"))

    loc = src.get("location") or {}

    alvo_upper = {t.upper() for t in (analitos_alvo or []) if t}

    # Analises — se houver filtro de analito, destaca o valor
    raw_analises: list[dict] = src.get("analises") or []
    analises_out: list[dict] = []
    valor_analito_filtro: float | None = None
    qualif_filtro: str | None = None

    for a in raw_analises:
        simbolo = a.get("analito", "")
        valor   = a.get("valor")
        qual    = a.get("qualificador")
        unidade = a.get("unidade", "")
        is_target = bool(alvo_upper and str(simbolo).upper() in alvo_upper)

        if is_target and valor_analito_filtro is None:
            valor_analito_filtro = valor
            qualif_filtro = qual

        # Inclui todos os analitos (não filtrados) no resultado completo
        analises_out.append({
            "analito":      simbolo,
            "valor":        valor,
            "unidade":      unidade,
            "qualificador": qual,
        })

    out: dict[str, Any] = {
        "id_amostra":                 src.get("id_amostra"),
        "classe":                     src.get("classe"),
        "projeto":                    src.get("projeto"),
        "projeto_publicacao":         src.get("projeto_publicacao"),
        "laboratorio":                src.get("laboratorio"),
        "metodo_abertura":            src.get("abertura"),
        "metodo_leitura":             src.get("leitura"),
        "classificacao_petrografica": src.get("classificacao_petrografica"),
        "unidade_litoestratigrafica": src.get("unidade_litoestratigrafica"),
        "data_de_analise":            src.get("data_de_analise"),
        "analitos_presentes":         src.get("analitos") or [],
        "analises":                   analises_out,
        "lat":                        loc.get("lat"),
        "lon":                        loc.get("lon"),
        "distancia_km":               dist,
    }

    # Destaca o analito filtrado no topo
    if analitos_alvo:
        out["analito_filtrado"] = ", ".join(analitos_alvo)
        out["valor_analito"]    = valor_analito_filtro
        out["qualificador"]     = qualif_filtro

    return out


def format_resumo(
    aggs: dict,
    total: int,
    lat: float,
    lon: float,
    raio_km: float,
    analito: str | None,
    analitos_tokens: list[str] | None = None,
) -> dict:
    """Formata o resumo geoquímico da área consultada."""
    por_analito  = {b["key"]: b["doc_count"]
                    for b in aggs.get("por_analito", {}).get("buckets", [])}
    por_classe   = {b["key"]: b["doc_count"]
                    for b in aggs.get("por_classe", {}).get("buckets", [])}
    por_projeto  = [b["key"] for b in aggs.get("por_projeto", {}).get("buckets", [])]

    estrategicos_na_area = sorted(
        [s for s in por_analito if s in ANALITOS_ESTRATEGICOS],
        key=lambda s: por_analito[s],
        reverse=True,
    )

    return {
        "total_amostras":          total,
        "raio_km":                 raio_km,
        "centro":                  {"lat": lat, "lon": lon},
        "n_analises_rocha":        por_classe.get("Rocha", 0),
        "n_analises_mineral_minerio": por_classe.get("Mineral/Minério", 0),
        "analitos_detectados":     por_analito,
        "n_analitos_distintos":    len(por_analito),
        "analitos_estrategicos_na_area": estrategicos_na_area,
        "projetos_cprm_na_area":   por_projeto,
        "filtro_analito":          (
            ", ".join(analitos_tokens) if analitos_tokens else (analito.strip() if analito else None)
        ),
    }


def build_mapa_pontos(amostras: list[dict], analito_filtro: str | None = None) -> dict:
    """Gera pontos para visualização no mapa frontend."""
    pontos = []
    for a in amostras:
        lat = a.get("lat")
        lon = a.get("lon")
        if lat is None or lon is None:
            continue
        aid = a.get("id_amostra")
        pontos.append({
            "lat":          lat,
            "lon":          lon,
            "tipo":         "geoquimica",
            "id":           aid,
            "label":        aid,
            "classe":       a.get("classe"),
            "projeto":      a.get("projeto"),
            "substancia":   a.get("classificacao_petrografica"),
            "analitos":     a.get("analitos_presentes", []),
            "distancia_km": a.get("distancia_km"),
            "analito_filtrado": analito_filtro if analito_filtro else None,
            "valor_analito":    a.get("valor_analito"),
            "qualificador":     a.get("qualificador"),
        })
    return {"pontos": pontos, "total_pontos": len(pontos)}


# ─────────────────────────────────────────────────────────────────────────────
# Execução
# ─────────────────────────────────────────────────────────────────────────────

async def executar_geoquimica_proxima(
    os_service: OpenSearchService,
    lat: float,
    lon: float,
    raio_km: float = 25.0,
    analito: str | None = None,
    classe: str | None = None,
    valor_min: float | None = None,
    max_amostras: int = MAX_AMOSTRAS,
) -> dict[str, Any]:
    """
    Executa busca geoquímica por proximidade e retorna resultado formatado.

    Args:
        os_service: cliente OpenSearch
        lat:        latitude do centro
        lon:        longitude do centro
        raio_km:    raio de busca em km
        analito:    símbolo do elemento (ex: "Au", "Nb", "Cu") — opcional.
                    Vários símbolos: ``"Ce, La, Nd"`` ou ``"Ce ou La ou Nd"`` (OU).
        classe:     "Rocha" ou "Mineral/Minério" — opcional
        valor_min:  valor mínimo — aplica aos analitos indicados (basta um cumprir
                    se forem vários).
        max_amostras: máximo de amostras a retornar

    Returns:
        Dict com ``resumo``, ``amostras``, ``mapa``
    """
    tokens = parse_analitos_param(analito)

    query = build_geoquimica_geo_query(
        lat=lat,
        lon=lon,
        raio_km=raio_km,
        analito=analito,
        classe=classe,
        valor_min=valor_min,
        size=max_amostras,
    )

    raw = await os_service.search(INDEX_GEO, query)

    hits  = raw.get("hits", {}).get("hits", [])
    total = raw.get("hits", {}).get("total", {}).get("value", 0)
    aggs  = raw.get("aggregations", {})

    label_mapa = ", ".join(tokens) if tokens else None
    amostras = [format_amostra(h, analitos_alvo=tokens or None) for h in hits]
    resumo   = format_resumo(aggs, total, lat, lon, raio_km, analito, analitos_tokens=tokens or None)
    mapa     = build_mapa_pontos(amostras, analito_filtro=label_mapa)

    return {
        "resumo":   resumo,
        "amostras": amostras,
        "mapa":     mapa,
    }


def normalize_id_amostra(raw: str) -> str:
    """Normaliza código de amostra CPRM (remove prefixo GEO: se houver)."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.upper().startswith("GEO:"):
        s = s[4:].strip()
    return s


def looks_like_id_amostra(text: str) -> bool:
    """Heurística para códigos tipo 1182-LK-R-0039B no texto do usuário."""
    s = normalize_id_amostra(text)
    if not s or len(s) < 8:
        return False
    return bool(_SAMPLE_ID_RE.match(s))


def _format_analises_linhas(analises: list[dict]) -> list[str]:
    """Linhas legíveis para o LLM colar no chat (cards com Ce:, La:, etc.)."""
    linhas: list[str] = []
    for a in analises or []:
        sym = a.get("analito") or ""
        val = a.get("valor")
        unit = a.get("unidade") or ""
        qual = a.get("qualificador")
        if val is None and qual in ("N", "n"):
            linhas.append(f"{sym}: não detectado")
            continue
        if val is None:
            continue
        q = f"{qual} " if qual and qual not in ("N", "n") else ""
        linhas.append(f"{sym}: {q}{val} {unit}".strip())
    return linhas


async def executar_geoquimica_detalhes_amostra(
    os_service: OpenSearchService,
    id_amostra: str,
) -> dict[str, Any]:
    """
    Busca uma amostra geoquímica pelo ``id_amostra`` (número de campo CPRM).

    ``_id`` no índice = ``GEO:{colecao}:{ogc_feature_id}``; busca por ``id_amostra``
    (numero_de_campo) via term/wildcard — pode haver várias análises por campo.
    """
    aid = normalize_id_amostra(id_amostra)
    if not aid:
        return {
            "sucesso": False,
            "mensagem": "Informe o código da amostra (ex.: 1182-LK-R-0039B).",
        }

    src: dict[str, Any] | None = None
    doc_id = f"GEO:{aid}"

    try:
        doc = await os_service.get(INDEX_GEO, doc_id)
        if isinstance(doc, dict) and doc.get("found"):
            src = doc.get("_source") or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("geoquimica get %s: %s", doc_id, exc)

    if not src:
        body = {
            "size": 3,
            "query": {
                "bool": {
                    "should": [
                        {"term": {"id_amostra": {"value": aid, "case_insensitive": True}}},
                        {
                            "wildcard": {
                                "id_amostra": {
                                    "value": f"*{aid}*",
                                    "case_insensitive": True,
                                }
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
        }
        try:
            raw = await os_service.search(INDEX_GEO, body)
            hits = raw.get("hits", {}).get("hits", [])
            if hits:
                src = hits[0].get("_source") or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("geoquimica_detalhes_amostra search %s: %s", aid, exc)
            return {"sucesso": False, "mensagem": str(exc)}

    if not src:
        return {
            "sucesso": False,
            "mensagem": (
                f"Amostra '{aid}' não encontrada em {INDEX_GEO}. "
                "Confira o código (ex.: 1182-LK-R-0039B) ou busque por coordenada "
                "com geoquimica_proxima."
            ),
        }

    amostra = format_amostra({"_source": src})
    mapa = build_mapa_pontos([amostra])

    titulo = amostra.get("id_amostra") or aid
    projeto = amostra.get("projeto") or amostra.get("projeto_publicacao") or ""
    detalhes_card = [
        f"Classe: {amostra.get('classe')}" if amostra.get("classe") else "",
        f"Projeto: {projeto}" if projeto else "",
        f"Laboratório: {amostra.get('laboratorio')}" if amostra.get("laboratorio") else "",
        f"Abertura: {amostra.get('metodo_abertura')}" if amostra.get("metodo_abertura") else "",
        f"Leitura: {amostra.get('metodo_leitura')}" if amostra.get("metodo_leitura") else "",
        f"Petrografia: {amostra.get('classificacao_petrografica')}"
        if amostra.get("classificacao_petrografica")
        else "",
        f"Data: {amostra.get('data_de_analise')}" if amostra.get("data_de_analise") else "",
    ]
    detalhes_card = [d for d in detalhes_card if d]
    teores = _format_analises_linhas(amostra.get("analises") or [])
    if amostra.get("lat") is not None and amostra.get("lon") is not None:
        dist = amostra.get("distancia_km")
        dist_s = f" ({dist} km)" if dist is not None else ""
        detalhes_card.append(
            f"Localização: {amostra['lat']:.6f}, {amostra['lon']:.6f}{dist_s}"
        )

    return {
        "sucesso": True,
        "amostra": amostra,
        "titulo_card": f"{titulo}" + (f" ({projeto})" if projeto else ""),
        "detalhes_card": detalhes_card,
        "teores": teores,
        "n_analitos": len(amostra.get("analises") or []),
        "mapa": mapa,
    }
