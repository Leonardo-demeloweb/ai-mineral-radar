"""
CPRM Ocorrências Query Module
==============================

Queries para ``mr_cprm_v001`` (SGB/CPRM GeoBank — ~36.000 ocorrências minerais).

Expõe a tool ``ocorrencias_minerais_proximas``:
  - Busca por geo_distance em torno de uma coordenada, **ou**
  - Busca em todo um estado (filtro ``uf``, ex.: MT / Mato Grosso)
  - Filtra opcionalmente por substância
  - Retorna resumo mineralógico + lista ordenada por distância
  - Retorna ``mapa.pontos`` para visualização no frontend

Campos indexados (pós-refatoração OGC API):
  substancia_principal, substancias, importancia, status_economico,
  situacao_mina, situacao_garimpo, rochas_hospedeiras, rochas_encaixantes,
  morfologia, provincia, municipio, uf, sureg, projeto, folha

Índice:
    mr_cprm_v001  (~36.000 docs — ocorrências com location geo_point + metadados)

Performance esperada: ~20–40ms (geo_distance + optional term filter)
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.jazidas.queries.cprm")

INDEX_CPRM = "mr_cprm_v001"

MAX_OCORRENCIAS = 30

_UF_NOME_PARA_SIGLA: dict[str, str] = {
    "acre": "AC",
    "alagoas": "AL",
    "amapa": "AP",
    "amazonas": "AM",
    "bahia": "BA",
    "ceara": "CE",
    "distrito federal": "DF",
    "espirito santo": "ES",
    "goias": "GO",
    "maranhao": "MA",
    "mato grosso": "MT",
    "mato grosso do sul": "MS",
    "minas gerais": "MG",
    "para": "PA",
    "paraiba": "PB",
    "parana": "PR",
    "pernambuco": "PE",
    "piaui": "PI",
    "rio de janeiro": "RJ",
    "rio grande do norte": "RN",
    "rio grande do sul": "RS",
    "rondonia": "RO",
    "roraima": "RR",
    "santa catarina": "SC",
    "sao paulo": "SP",
    "sergipe": "SE",
    "tocantins": "TO",
}


def normalizar_uf_cprm(uf: str) -> str | None:
    """
    Converte nome do estado ou sigla para sigla UF (2 letras), ou None.
    """
    if not uf or not str(uf).strip():
        return None
    s = str(uf).strip()
    su = s.upper()
    if len(su) == 2 and su.isalpha():
        return su
    key = "".join(
        c
        for c in unicodedata.normalize("NFKD", s.lower())
        if not unicodedata.combining(c)
    )
    return _UF_NOME_PARA_SIGLA.get(key)


# ─────────────────────────────────────────────────────────────────────────────
# Query builders
# ─────────────────────────────────────────────────────────────────────────────

def _aggs_padrao_ocorrencias() -> dict[str, Any]:
    return {
        "por_substancia": {
            "terms": {"field": "substancia_principal", "size": 15}
        },
        "por_importancia": {
            "terms": {"field": "importancia", "size": 6}
        },
        "por_status_economico": {
            "terms": {"field": "status_economico", "size": 6}
        },
        "por_uf": {
            "terms": {"field": "uf", "size": 10}
        },
    }


def _filtro_substancia_ocorrencia(substancia: str) -> dict[str, Any]:
    subs_normalized = substancia.strip().title()
    return {
        "bool": {
            "should": [
                {"term": {"substancia_principal": subs_normalized}},
                {"term": {"substancias": subs_normalized}},
                {"term": {"substancia_principal": substancia.strip()}},
                {"term": {"substancias": substancia.strip()}},
            ],
            "minimum_should_match": 1,
        }
    }


def build_ocorrencias_geo_query(
    lat: float,
    lon: float,
    raio_km: float,
    substancia: str | None = None,
    size: int = MAX_OCORRENCIAS,
) -> dict[str, Any]:
    """
    Busca ocorrências CPRM dentro de um raio, opcionalmente filtrando por substância.

    Substância é buscada em ``substancia_principal`` (keyword) e
    ``substancias`` (keyword[]) via ``terms`` ou ``term`` filter.
    O filtro é case-insensitive via normalização do valor (title case).
    """
    filters: list[dict] = [
        {
            "geo_distance": {
                "distance": f"{raio_km}km",
                "location": {"lat": lat, "lon": lon},
            }
        }
    ]

    if substancia:
        filters.append(_filtro_substancia_ocorrencia(substancia))

    return {
        "size": size,
        "query": {"bool": {"filter": filters}},
        "_source": [
            "id_ocorrencia", "nome",
            "substancia_principal", "substancias", "classes_utilitarias",
            # campos corretos pós-OGC API
            "importancia", "status_economico",
            "situacao_mina", "situacao_garimpo",
            "rochas_hospedeiras", "rochas_encaixantes",
            "morfologia",
            "provincia", "municipio", "uf", "sureg", "projeto", "folha",
            "location", "descricao", "dt_referencia", "poligono_fonte",
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
        "aggs": _aggs_padrao_ocorrencias(),
    }


def build_ocorrencias_uf_query(
    uf: str,
    substancia: str | None = None,
    size: int = MAX_OCORRENCIAS,
) -> dict[str, Any]:
    """Ocorrências em todo o estado (filtro ``uf``), opcionalmente por substância."""
    filters: list[dict[str, Any]] = [{"term": {"uf": uf.upper().strip()}}]
    if substancia:
        filters.append(_filtro_substancia_ocorrencia(substancia))
    return {
        "size": min(max(size, 1), 100),
        "query": {"bool": {"filter": filters}},
        "_source": [
            "id_ocorrencia", "nome",
            "substancia_principal", "substancias", "classes_utilitarias",
            "importancia", "status_economico",
            "situacao_mina", "situacao_garimpo",
            "rochas_hospedeiras", "rochas_encaixantes",
            "morfologia",
            "provincia", "municipio", "uf", "sureg", "projeto", "folha",
            "location", "descricao", "dt_referencia", "poligono_fonte",
        ],
        "aggs": _aggs_padrao_ocorrencias(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────

def format_ocorrencia(hit: dict) -> dict[str, Any]:
    """Formata um hit do mr_cprm_v001 com distância calculada."""
    s = hit.get("_source", {})
    sort_vals = hit.get("sort", [])
    dist_km = round(sort_vals[0], 2) if sort_vals else None

    return {
        "id":                   s.get("id_ocorrencia"),
        "nome":                 s.get("nome"),
        "substancia_principal": s.get("substancia_principal"),
        "substancias":          s.get("substancias") or [],
        # classificação econômica (campos corretos)
        "importancia":          s.get("importancia"),    # Depósito | Indício | Ocorrência
        "status_economico":     s.get("status_economico"),  # Mina | Garimpo | Não explotado
        "situacao_mina":        s.get("situacao_mina"),
        # geologia
        "rochas_hospedeiras":   s.get("rochas_hospedeiras"),
        "rochas_encaixantes":   s.get("rochas_encaixantes"),
        "morfologia":           s.get("morfologia"),
        # localização
        "provincia":            s.get("provincia"),
        "municipio":            s.get("municipio"),
        "uf":                   s.get("uf"),
        "projeto":              s.get("projeto"),
        "descricao":            (s.get("descricao") or "")[:300] or None,
        "dt_referencia":        s.get("dt_referencia"),
        "distancia_km":         dist_km,
        "location":             s.get("location"),
        "poligono_fonte":       s.get("poligono_fonte"),
    }


def format_resumo_mineralogico(
    aggs: dict,
    total_hits: int,
    raio_km: float,
    *,
    escopo_uf: str | None = None,
) -> dict[str, Any]:
    """
    Constrói o resumo mineralógico da área a partir das agregações.

    Inclui:
    - Distribuição de substâncias encontradas
    - Importância dos recursos (Depósito, Indício, Ocorrência)
    - Status econômico (Mina, Garimpo, Não explotado)
    - Províncias geológicas presentes
    - Minerais estratégicos identificados
    """
    por_subst   = {b["key"]: b["doc_count"]
                   for b in aggs.get("por_substancia", {}).get("buckets", [])}
    por_import  = {b["key"]: b["doc_count"]
                   for b in aggs.get("por_importancia", {}).get("buckets", [])}
    por_status  = {b["key"]: b["doc_count"]
                   for b in aggs.get("por_status_economico", {}).get("buckets", [])}
    por_uf      = {b["key"]: b["doc_count"]
                   for b in aggs.get("por_uf", {}).get("buckets", [])}

    substancias_area = list(por_subst.keys())

    minerios_estrategicos = {
        k for k in por_subst
        if k.lower() in {
            "ouro", "cobre", "ferro", "nióbio", "lítio", "urânio", "cobalto",
            "manganês", "níquel", "cromo", "titânio", "vanádio", "estanho",
            "tungstênio", "molibdênio", "diamante", "platina", "prata",
            "terras raras", "grafita", "fosfato", "potássio",
        }
    }

    # Contagem de depósitos confirmados (importancia = "Depósito")
    n_depositos = por_import.get("Depósito", 0)
    n_minas     = por_status.get("Mina", 0) + por_status.get("Garimpo", 0)

    out: dict[str, Any] = {
        "total_ocorrencias":        total_hits,
        "substancias_encontradas":  substancias_area,
        "minerios_estrategicos":    sorted(minerios_estrategicos),
        "distribuicao_substancias": por_subst,
        # classificação econômica (novos campos OGC API)
        "importancia_recursos":     por_import,   # Depósito | Indício | Ocorrência
        "status_economico":         por_status,   # Mina | Garimpo | Não explotado
        "n_depositos_confirmados":  n_depositos,
        "n_minas_garimpas_ativas":  n_minas,
        "estados":                  por_uf,
    }
    if escopo_uf:
        out["escopo"] = "uf"
        out["uf"] = escopo_uf
    else:
        out["raio_km"] = raio_km
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────


def _mapa_pontos_from_ocorrencias(ocorrencias: list[dict[str, Any]]) -> dict[str, Any]:
    pontos_mapa: list[dict[str, Any]] = []
    for o in ocorrencias:
        loc = o.get("location") or {}
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat is None or lon is None:
            continue
        sp = o.get("substancia_principal")
        mun = o.get("municipio")
        ufv = o.get("uf")
        oid = str(o.get("id") or o.get("nome") or "")
        pontos_mapa.append({
            "lat": float(lat),
            "lon": float(lon),
            "tipo": "ocorrencia_mineral",
            "processo": oid,
            "substancias": [sp] if sp else [],
            "municipios": [str(mun)] if mun else [],
            "uf": [str(ufv)] if ufv else [],
            "label": sp or "Ocorrência",
            "importancia":  o.get("importancia"),
            "status":       o.get("status_economico"),
            "distancia_km": o.get("distancia_km"),
        })
    return {"pontos": pontos_mapa}


async def executar_ocorrencias_por_uf(
    os_service: OpenSearchService,
    uf: str,
    substancia: str | None = None,
    max_ocorrencias: int = 50,
) -> dict[str, Any]:
    """
    Ocorrências CPRM filtradas por UF (sem centroide / raio).

    ``uf`` deve ser sigla (MT) ou nome reconhecível (Mato Grosso).
    """
    uf_sigla = normalizar_uf_cprm(uf)
    if not uf_sigla:
        return {"erro": f"UF inválida ou não reconhecida: {uf!r}"}

    query = build_ocorrencias_uf_query(uf_sigla, substancia, max_ocorrencias)

    try:
        result = await os_service.search(INDEX_CPRM, query)
    except Exception as e:
        logger.error("cprm: falha na busca por uf=%s substancia=%r: %s", uf_sigla, substancia, e)
        return {"erro": str(e)}

    hits  = result.get("hits", {}).get("hits", [])
    total = result.get("hits", {}).get("total", {})
    total_val = total.get("value", 0) if isinstance(total, dict) else int(total)
    aggs  = result.get("aggregations", {})

    resumo      = format_resumo_mineralogico(aggs, total_val, 0.0, escopo_uf=uf_sigla)
    ocorrencias = [format_ocorrencia(h) for h in hits]

    logger.info(
        "cprm: uf=%s substancia=%r → %s ocorrências (hits=%d)",
        uf_sigla, substancia, total_val, len(ocorrencias),
    )

    return {
        "area": {
            "uf": uf_sigla,
            "filtro_substancia": substancia,
            "escopo": "uf",
        },
        "resumo_mineralogico": resumo,
        "ocorrencias": ocorrencias,
        "mapa": _mapa_pontos_from_ocorrencias(ocorrencias),
    }


async def executar_ocorrencias_minerais_proximas(
    os_service: OpenSearchService,
    lat: float,
    lon: float,
    raio_km: float = 10.0,
    substancia: str | None = None,
) -> dict[str, Any]:
    """
    Orquestra a busca de ocorrências CPRM próximas a uma coordenada.

    Retorna:
      - Resumo mineralógico da área (substâncias, importância, status econômico,
        províncias geológicas, minerais estratégicos presentes)
      - Lista de ocorrências ordenadas por distância crescente
      - ``mapa.pontos`` para visualização no frontend

    Args:
        os_service: Client OpenSearch assíncrono
        lat, lon:   Coordenada central
        raio_km:    Raio de busca (default: 10km)
        substancia: Filtro opcional por substância (ex: "Ouro", "Ferro")
    """
    query = build_ocorrencias_geo_query(lat, lon, raio_km, substancia)

    try:
        result = await os_service.search(INDEX_CPRM, query)
    except Exception as e:
        logger.error(
            f"cprm: falha na busca geo lat={lat} lon={lon} raio={raio_km}: {e}"
        )
        return {"erro": str(e)}

    hits  = result.get("hits", {}).get("hits", [])
    total = result.get("hits", {}).get("total", {})
    total_val = total.get("value", 0) if isinstance(total, dict) else int(total)
    aggs  = result.get("aggregations", {})

    resumo      = format_resumo_mineralogico(aggs, total_val, raio_km)
    ocorrencias = [format_ocorrencia(h) for h in hits]

    logger.info(
        f"cprm: lat={lat} lon={lon} raio={raio_km}km substancia={substancia!r} → "
        f"{total_val} ocorrências, estratégicos={resumo['minerios_estrategicos']}, "
        f"depósitos={resumo['n_depositos_confirmados']}"
    )

    return {
        "area": {
            "lat": lat,
            "lon": lon,
            "raio_km": raio_km,
            "filtro_substancia": substancia,
        },
        "resumo_mineralogico": resumo,
        "ocorrencias": ocorrencias,
        "mapa": _mapa_pontos_from_ocorrencias(ocorrencias),
    }
