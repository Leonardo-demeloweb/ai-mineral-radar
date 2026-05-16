"""
SICAR (CAR) Query Module
=========================

Queries para ``mr_sicar_v001`` — Cadastro Ambiental Rural (SFB/MMA).

Contexto de uso:
  O CAR é o registro ambiental obrigatório para imóveis rurais no Brasil
  (Lei 12.651/2012, art. 29). Fornece:
    - Polígono oficial do imóvel rural (AREA_IMOVEL)
    - Status: AT (ativo), PE (pendente), SU (suspenso), CA (cancelado)
    - Tipo: IRU (imóvel rural), ASS (assentamento), PCT, QUI (quilombola)
    - CPF/CNPJ do proprietário (quando disponível publicamente)
    - Área cadastrada em hectares

  Para mineração, o cruzamento CAR × processo ANM responde:
    1. Existem imóveis rurais na área do processo? (quem são os donos?)
    2. O titular do processo possui propriedade rural cadastrada?
    3. Há imóveis de comunidades tradicionais (PCT/QUI) no entorno?
    4. Qual a situação cadastral do imóvel? (ativo vs. pendente/cancelado)

Estratégia de consulta:
  Como ``mr_sicar_v001`` armazena polígonos (``geo_shape``), a busca
  geo usa ``geo_shape::intersects`` (não ``geo_distance``).
  Dado que os polígonos têm centróides, também oferecemos busca
  por ``geo_distance`` no centróide para raio aproximado.

  A busca por CNPJ/CPF cruzada com jazidas permite responder:
  "O titular ANM possui imóvel CAR na área do processo?"

Índice:
  mr_sicar_v001  (~6.8M docs / ~8 GB — 1 doc por imóvel CAR)
"""

from __future__ import annotations

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.jazidas.queries.sicar")

INDEX_SICAR = "mr_sicar_v001"

# Status CAR legíveis
STATUS_LABEL = {
    "AT": "Ativo",
    "PE": "Pendente",
    "SU": "Suspenso",
    "CA": "Cancelado",
}

TIPO_LABEL = {
    "IRU": "Imóvel Rural",
    "ASS": "Assentamento",
    "PCT": "Povos e Comunidades Tradicionais",
    "QUI": "Quilombola",
}

# Tipos com restrição especial (comunidades tradicionais)
TIPOS_SENSIVEIS = {"PCT", "QUI", "ASS"}


# ─────────────────────────────────────────────────────────────────────────────
# Query builders
# ─────────────────────────────────────────────────────────────────────────────

def build_imoveis_proximos_query(
    lat: float,
    lon: float,
    raio_km: float,
    status: str | None,
    tipo: str | None,
    size: int,
) -> dict[str, Any]:
    """
    Busca imóveis CAR cujo centróide está dentro do raio dado.
    Filtros opcionais: status (AT/PE/SU/CA) e tipo (IRU/ASS/PCT/QUI).

    Nota: usa centróide para ``geo_distance`` — não é sobreposição exata
    de polígono. Para sobreposição exata, usar PostGIS.
    """
    filters: list[dict] = [
        {
            "geo_distance": {
                "distance": f"{raio_km}km",
                "centroide": {"lat": lat, "lon": lon},
            }
        }
    ]

    if status:
        filters.append({"term": {"status_car": status.upper()}})
    if tipo:
        filters.append({"term": {"tipo_imovel": tipo.upper()}})

    return {
        "size": size,
        "_source": [
            "cod_car", "uf", "municipio", "cod_municipio_ibge",
            "status_car", "tipo_imovel",
            "area_ha", "area_modulos_fiscais",
            "cpf_cnpj_proprietario", "nome_proprietario",
            "centroide",
            "dt_inscricao", "dt_retificacao", "dt_cancelamento",
            "sobreposicao_ti", "sobreposicao_uc", "sobreposicao_area_anm",
        ],
        "query": {"bool": {"filter": filters}},
        "sort": [
            {
                "_geo_distance": {
                    "centroide": {"lat": lat, "lon": lon},
                    "order":     "asc",
                    "unit":      "km",
                }
            }
        ],
        "aggs": {
            "por_status": {
                "terms": {"field": "status_car", "size": 5}
            },
            "por_tipo": {
                "terms": {"field": "tipo_imovel", "size": 5}
            },
            "area_total": {
                "sum": {"field": "area_ha"}
            },
        },
    }


def build_imoveis_por_cpf_cnpj_query(
    cpf_cnpj: str,
    uf: str | None = None,
    size: int = 20,
) -> dict[str, Any]:
    """
    Busca imóveis CAR de um proprietário pelo CPF ou CNPJ.
    """
    # Normaliza: remove pontuação
    doc_limpo = "".join(c for c in cpf_cnpj if c.isdigit())

    filters: list[dict] = [
        {"term": {"cpf_cnpj_proprietario": doc_limpo}}
    ]
    if uf:
        filters.append({"term": {"uf": uf.upper()}})

    return {
        "size": size,
        "_source": [
            "cod_car", "uf", "municipio",
            "status_car", "tipo_imovel",
            "area_ha", "centroide",
            "dt_inscricao",
        ],
        "query": {"bool": {"filter": filters}},
        "sort": [{"area_ha": "desc"}],
        "aggs": {
            "area_total": {"sum": {"field": "area_ha"}},
            "por_uf":     {"terms": {"field": "uf", "size": 10}},
            "por_status": {"terms": {"field": "status_car", "size": 5}},
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────

def _dist(hit: dict) -> float | None:
    """Extrai distância do sort score (km)."""
    sort = hit.get("sort", [])
    if sort:
        try:
            return round(float(sort[0]), 2)
        except (TypeError, ValueError):
            pass
    return None


def format_imovel(hit: dict) -> dict:
    s   = hit["_source"]
    cod = s.get("cod_car", "")

    # Extrai UF do código CAR se não disponível (formato: UF-CODIBGE-ID)
    uf = s.get("uf") or (cod[:2] if len(cod) >= 2 else None)

    status  = s.get("status_car", "")
    tipo    = s.get("tipo_imovel", "")
    area_ha = s.get("area_ha")
    cpf_cnpj = s.get("cpf_cnpj_proprietario")

    # Mascara CPF/CNPJ para privacidade (exibe apenas últimos dígitos)
    if cpf_cnpj:
        if len(cpf_cnpj) == 11:   # CPF
            cpf_cnpj_display = f"***.***.{cpf_cnpj[6:9]}-**"
        elif len(cpf_cnpj) == 14: # CNPJ
            cpf_cnpj_display = f"{cpf_cnpj[:2]}.***.***/****-{cpf_cnpj[-2:]}"
        else:
            cpf_cnpj_display = cpf_cnpj[-4:].rjust(len(cpf_cnpj), "*")
    else:
        cpf_cnpj_display = None

    return {
        "cod_car":          cod,
        "uf":               uf,
        "municipio":        s.get("municipio"),
        "status":           STATUS_LABEL.get(status, status),
        "status_codigo":    status,
        "tipo":             TIPO_LABEL.get(tipo, tipo),
        "tipo_codigo":      tipo,
        "area_ha":          round(area_ha, 2) if area_ha else None,
        "proprietario":     s.get("nome_proprietario"),
        "cpf_cnpj":         cpf_cnpj_display,
        "dt_inscricao":     s.get("dt_inscricao"),
        "sobreposicao_ti":  s.get("sobreposicao_ti", False),
        "sobreposicao_uc":  s.get("sobreposicao_uc", False),
        "sobreposicao_anm": s.get("sobreposicao_area_anm", False),
        "distancia_km":     _dist(hit),
        "sensivel":         tipo in TIPOS_SENSIVEIS,
    }


def format_resumo_fundiario(
    imoveis: list[dict],
    aggs: dict,
    lat: float,
    lon: float,
    raio_km: float,
) -> dict:
    """
    Sintetiza a situação fundiária da área em linguagem interpretável.
    """
    total = sum(b["doc_count"] for b in aggs.get("por_status", {}).get("buckets", []))
    area_total = aggs.get("area_total", {}).get("value", 0) or 0

    ativos = next(
        (b["doc_count"] for b in aggs.get("por_status", {}).get("buckets", [])
         if b["key"] == "AT"), 0
    )
    pendentes = next(
        (b["doc_count"] for b in aggs.get("por_status", {}).get("buckets", [])
         if b["key"] == "PE"), 0
    )

    por_tipo = {
        b["key"]: b["doc_count"]
        for b in aggs.get("por_tipo", {}).get("buckets", [])
    }

    tipos_sensiveis = sum(
        v for k, v in por_tipo.items() if k in TIPOS_SENSIVEIS
    )

    # Nível de atenção fundiária
    if tipos_sensiveis > 0:
        nivel = "ALTO"
        justificativa = f"{tipos_sensiveis} imóvel(is) de comunidades tradicionais (PCT/QUI/ASS)"
    elif total > 10:
        nivel = "MÉDIO"
        justificativa = f"{total} imóveis CAR na área ({ativos} ativos)"
    elif total > 0:
        nivel = "BAIXO"
        justificativa = f"{total} imóvel(is) CAR ativo(s) registrado(s)"
    else:
        nivel = "SEM_DADOS"
        justificativa = "Nenhum imóvel CAR encontrado no raio buscado"

    return {
        "area_pesquisada": {
            "lat": lat, "lon": lon, "raio_km": raio_km,
        },
        "nivel_atencao_fundiaria": nivel,
        "justificativa":           justificativa,
        "totais": {
            "total_imoveis":          total,
            "imoveis_ativos":         ativos,
            "imoveis_pendentes":      pendentes,
            "area_total_ha":          round(area_total, 1),
            "tipos_sensiveis":        tipos_sensiveis,
        },
        "distribuicao_tipo": {
            TIPO_LABEL.get(k, k): v for k, v in por_tipo.items()
        },
        "distribuicao_status": {
            STATUS_LABEL.get(b["key"], b["key"]): b["doc_count"]
            for b in aggs.get("por_status", {}).get("buckets", [])
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrators
# ─────────────────────────────────────────────────────────────────────────────

async def executar_imoveis_car_proximos(
    os_service: OpenSearchService,
    lat: float,
    lon: float,
    raio_km: float = 5.0,
    status: str | None = "AT",
    tipo: str | None = None,
    max_registros: int = 15,
) -> dict[str, Any]:
    """
    Busca imóveis CAR no entorno de uma coordenada.

    Retorna lista de imóveis com área, proprietário, status CAR,
    flags de sobreposição com TI/UC, e resumo fundiário da área.
    """
    query = build_imoveis_proximos_query(lat, lon, raio_km, status, tipo, max_registros)

    try:
        result = await os_service.search(INDEX_SICAR, query)
    except Exception as e:
        logger.error(f"sicar_proximos: query falhou: {e}")
        return {"erro": str(e)}

    hits  = result.get("hits", {}).get("hits", [])
    total = result.get("hits", {}).get("total", {}).get("value", 0)
    aggs  = result.get("aggregations", {})

    imoveis = [format_imovel(h) for h in hits]
    resumo  = format_resumo_fundiario(imoveis, aggs, lat, lon, raio_km)

    logger.info(
        f"sicar_proximos: lat={lat} lon={lon} raio={raio_km}km "
        f"→ {total} imóveis (mostrando {len(imoveis)}) "
        f"nivel={resumo['nivel_atencao_fundiaria']}"
    )

    return {
        "consulta": {
            "lat": lat, "lon": lon,
            "raio_km": raio_km,
            "status_filtro": status,
            "tipo_filtro": tipo,
        },
        "resumo_fundiario": resumo,
        "total_encontrados": total,
        "imoveis": imoveis,
    }


async def executar_imoveis_por_cpf_cnpj(
    os_service: OpenSearchService,
    cpf_cnpj: str,
    uf: str | None = None,
    max_registros: int = 20,
) -> dict[str, Any]:
    """
    Busca todos os imóveis CAR registrados em nome de um CPF ou CNPJ.

    Útil para: "A empresa titular do processo ANM possui imóveis rurais cadastrados?"
    """
    query = build_imoveis_por_cpf_cnpj_query(cpf_cnpj, uf, max_registros)

    try:
        result = await os_service.search(INDEX_SICAR, query)
    except Exception as e:
        logger.error(f"sicar_cpf_cnpj: query falhou: {e}")
        return {"erro": str(e)}

    hits  = result.get("hits", {}).get("hits", [])
    total = result.get("hits", {}).get("total", {}).get("value", 0)
    aggs  = result.get("aggregations", {})

    area_total  = aggs.get("area_total", {}).get("value", 0) or 0
    por_uf      = [
        {"uf": b["key"], "n_imoveis": b["doc_count"]}
        for b in aggs.get("por_uf", {}).get("buckets", [])
    ]
    por_status  = {
        STATUS_LABEL.get(b["key"], b["key"]): b["doc_count"]
        for b in aggs.get("por_status", {}).get("buckets", [])
    }

    # Formata sem distância (sort por área)
    def _fmt(hit):
        s = hit["_source"]
        return {
            "cod_car":     s.get("cod_car"),
            "uf":          s.get("uf"),
            "municipio":   s.get("municipio"),
            "status":      STATUS_LABEL.get(s.get("status_car", ""), s.get("status_car")),
            "tipo":        TIPO_LABEL.get(s.get("tipo_imovel", ""), s.get("tipo_imovel")),
            "area_ha":     round(s["area_ha"], 2) if s.get("area_ha") else None,
            "dt_inscricao": s.get("dt_inscricao"),
        }

    imoveis = [_fmt(h) for h in hits]

    logger.info(
        f"sicar_cpf_cnpj: doc={cpf_cnpj[:6]}*** uf={uf} "
        f"→ {total} imóveis area_total={area_total:.0f} ha"
    )

    return {
        "consulta": {
            "cpf_cnpj_parcial": cpf_cnpj[:6] + "***",
            "uf_filtro": uf,
        },
        "resumo": {
            "total_imoveis":   total,
            "area_total_ha":   round(area_total, 1),
            "distribuicao_uf": por_uf,
            "distribuicao_status": por_status,
        },
        "imoveis": imoveis,
    }
