"""
Disponibilidades Query Module
==============================

Busca de áreas em disponibilidade (fase = 'disponibilidade' ou
'apto para disponibilidade') em mr_jazidas_v001.

Fases:
  - 'disponibilidade'         → processo extinto, área disponível para requerimento
  - 'apto para disponibilidade' → processo em fase final antes da disponibilidade

Used by:
    - Tool: areas_em_disponibilidade
"""

import logging
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.jazidas.queries.disponibilidades")

INDEX_ANM = "mr_jazidas_v001"

FASES_DISPONIBILIDADE = ["disponibilidade", "apto para disponibilidade"]

SOURCE_FIELDS = [
    "numero_processo",
    "fase",
    "substancias_desc",
    "uf",
    "municipio",
    "codigo_ibge",
    "area_ha",
    "dt_requerimento",
    "categorias_estrategicas",
    "prioridade_estrategica",
    "n_restricoes_ti",
    "n_restricoes_uc",
    "bioma",
    "location",
]


def build_disponibilidades_query(
    substancias_desc: list[str] | None = None,
    uf: str | None = None,
    municipio: str | None = None,
    codigo_ibge: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    raio_km: float = 100.0,
    apenas_sem_restricoes: bool = False,
    incluir_apto: bool = True,
    categorias_estrategicas: list[str] | None = None,
    area_min_ha: float | None = None,
    area_max_ha: float | None = None,
    limite: int = 30,
) -> dict[str, Any]:
    """
    Constrói a query OpenSearch para busca de áreas em disponibilidade.

    Args:
        substancias_desc: Lista de nomes de substâncias (já resolvidos pelo SubstanciaResolver)
        uf: Filtro por UF (ex: "MG")
        municipio: Filtro por município
        codigo_ibge: Código IBGE do município (7 dígitos)
        latitude/longitude: Ponto central para busca geográfica
        raio_km: Raio em km para busca geográfica (default 100 km)
        apenas_sem_restricoes: Se True, exclui áreas com n_restricoes_ti > 0 ou n_restricoes_uc > 0
        incluir_apto: Se True, inclui fase 'apto para disponibilidade' além de 'disponibilidade'
        categorias_estrategicas: Filtro por categoria mineral estratégica
        area_min_ha/area_max_ha: Filtro por tamanho da área
        limite: Máximo de resultados
    """
    fases = FASES_DISPONIBILIDADE if incluir_apto else ["disponibilidade"]

    filters: list[dict] = [
        {"terms": {"fase": fases}},
    ]

    if substancias_desc:
        # Mesmo padrão que jazidas.py: terms em subcampo keyword (normalizado lower_ascii)
        filters.append({"terms": {"substancias_desc.keyword": substancias_desc}})

    if uf:
        filters.append({"term": {"uf": uf.upper()}})

    if municipio:
        filters.append({"match": {"municipio": {"query": municipio, "fuzziness": "AUTO"}}})

    if codigo_ibge:
        filters.append({"term": {"codigo_ibge": codigo_ibge.zfill(7)}})

    if categorias_estrategicas:
        filters.append({"terms": {"categorias_estrategicas": categorias_estrategicas}})

    if area_min_ha is not None or area_max_ha is not None:
        range_clause: dict[str, Any] = {}
        if area_min_ha is not None:
            range_clause["gte"] = area_min_ha
        if area_max_ha is not None:
            range_clause["lte"] = area_max_ha
        filters.append({"range": {"area_ha": range_clause}})

    if apenas_sem_restricoes:
        filters.append({"term": {"n_restricoes_ti": 0}})
        filters.append({"term": {"n_restricoes_uc": 0}})

    if latitude is not None and longitude is not None:
        filters.append({
            "geo_distance": {
                "distance": f"{raio_km}km",
                "location": {"lat": latitude, "lon": longitude},
            }
        })

    query: dict[str, Any] = {
        "size": min(limite, 200),
        "query": {"bool": {"filter": filters}},
        "_source": SOURCE_FIELDS,
        "sort": [
            # Primeiro: maior prioridade estratégica
            {"prioridade_estrategica": {"order": "desc", "missing": "_last"}},
            # Depois: maior área
            {"area_ha": {"order": "desc"}},
        ],
    }

    # Se busca geográfica, adiciona ordenação por distância como critério secundário
    if latitude is not None and longitude is not None:
        query["sort"] = [
            {"prioridade_estrategica": {"order": "desc", "missing": "_last"}},
            {
                "_geo_distance": {
                    "location": {"lat": latitude, "lon": longitude},
                    "order": "asc",
                    "unit": "km",
                }
            },
        ]

    return query


def _format_disponibilidade(hit: dict) -> dict[str, Any]:
    """Formata um hit do OpenSearch para resposta."""
    src = hit.get("_source", {})
    sort_vals = hit.get("sort", [])

    result: dict[str, Any] = {
        "numero_processo":        src.get("numero_processo"),
        "fase":                   src.get("fase"),
        "substancias_desc":       src.get("substancias_desc", []),
        "uf":                     src.get("uf"),
        "municipio":              src.get("municipio"),
        "area_ha":                src.get("area_ha"),
        "dt_requerimento":        src.get("dt_requerimento"),
        "categorias_estrategicas": src.get("categorias_estrategicas", []),
        "prioridade_estrategica": src.get("prioridade_estrategica"),
        "n_restricoes_ti":        src.get("n_restricoes_ti", 0),
        "n_restricoes_uc":        src.get("n_restricoes_uc", 0),
        "bioma":                  src.get("bioma"),
    }

    loc = src.get("location") or {}
    if loc.get("lat") is not None:
        result["coordenada"] = {"lat": loc["lat"], "lon": loc["lon"]}

    # Distância (presente quando busca geográfica com sort _geo_distance)
    if len(sort_vals) >= 2 and isinstance(sort_vals[-1], (int, float)):
        result["distancia_km"] = round(float(sort_vals[-1]), 1)

    return result


async def executar_areas_em_disponibilidade(
    os_service: OpenSearchService,
    substancias_desc: list[str] | None = None,
    uf: str | None = None,
    municipio: str | None = None,
    codigo_ibge: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    raio_km: float = 100.0,
    apenas_sem_restricoes: bool = False,
    incluir_apto: bool = True,
    categorias_estrategicas: list[str] | None = None,
    area_min_ha: float | None = None,
    area_max_ha: float | None = None,
    limite: int = 30,
) -> dict[str, Any]:
    """
    Busca áreas ANM em disponibilidade para novo requerimento.

    Returns:
        Dict com ``total_encontrado``, ``total_disponibilidade``, ``total_apto``,
        ``areas`` (lista de áreas formatadas).
    """
    query = build_disponibilidades_query(
        substancias_desc=substancias_desc,
        uf=uf,
        municipio=municipio,
        codigo_ibge=codigo_ibge,
        latitude=latitude,
        longitude=longitude,
        raio_km=raio_km,
        apenas_sem_restricoes=apenas_sem_restricoes,
        incluir_apto=incluir_apto,
        categorias_estrategicas=categorias_estrategicas,
        area_min_ha=area_min_ha,
        area_max_ha=area_max_ha,
        limite=limite,
    )

    result = await os_service.search_with_meta(INDEX_ANM, query)
    hits = result.get("hits", [])
    total = result.get("total", 0)

    areas = [_format_disponibilidade(h) for h in hits]

    # Contadores por fase
    total_disp = sum(1 for a in areas if a.get("fase") == "disponibilidade")
    total_apto = sum(1 for a in areas if a.get("fase") == "apto para disponibilidade")

    logger.info(
        f"areas_em_disponibilidade: {len(areas)} retornados / {total} total "
        f"(substancias={substancias_desc}, uf={uf}, raio={raio_km}km)"
    )

    return {
        "total_encontrado":      total,
        "retornados":            len(areas),
        "total_disponibilidade": total_disp,
        "total_apto":            total_apto,
        "areas":                 areas,
    }
