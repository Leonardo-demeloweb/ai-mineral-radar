"""
Socios Query Module
====================

Busca reversa ``buscar_por_socio`` no índice **mr_empresas_v001** (arrays planos
``socios_nomes``, ``socios_cpf_cnpj``, ``socios_qualificacoes``).
"""

from __future__ import annotations

import logging
from typing import Any

from mcp_servers.common.formatters import extract_municipio_nome, format_cnpj
from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.empresas.queries.socios")

INDEX_CNPJ = "mr_empresas_v001"
MAX_RESULTS = 300

SOURCE_FIELDS = [
    "cnpj_basico",
    "cnpj_completo",
    "razao_social",
    "nome_fantasia",
    "situacao",
    "uf",
    "municipio",
    "cnae_principal",
    "cnae_desc",
    "socios_nomes",
    "socios_cpf_cnpj",
    "socios_qualificacoes",
]


def _cnpj_triple_flat(source: dict) -> tuple[str, str, str]:
    cc = "".join(c for c in str(source.get("cnpj_completo") or "") if c.isdigit())
    if len(cc) == 14:
        return cc[:8], cc[8:12], cc[12:14]
    return str(source.get("cnpj_basico") or "").zfill(8), "0001", "00"


def build_busca_socio_query(
    nome_socio: str | None = None,
    cpf_cnpj_socio: str | None = None,
    uf: str | None = None,
    apenas_ativas: bool = False,
) -> dict[str, Any]:
    """
    Query em ``mr_empresas_v001``: ``match`` em ``socios_nomes`` e/ou ``term`` em
    ``socios_cpf_cnpj`` (OR entre os critérios quando ambos informados).
    """
    should_clauses: list[dict[str, Any]] = []
    filter_clauses: list[dict[str, Any]] = []

    if nome_socio and nome_socio.strip():
        should_clauses.append({"match": {"socios_nomes": nome_socio.strip()}})

    if cpf_cnpj_socio and cpf_cnpj_socio.strip():
        should_clauses.append({"term": {"socios_cpf_cnpj": cpf_cnpj_socio.strip()}})

    if not should_clauses:
        raise ValueError("Informe nome_socio e/ou cpf_cnpj_socio")

    must_clauses: list[dict[str, Any]] = [
        {
            "bool": {
                "should": should_clauses,
                "minimum_should_match": 1,
            }
        }
    ]

    if apenas_ativas:
        filter_clauses.append({"term": {"situacao": "Ativa"}})

    if uf:
        filter_clauses.append({"term": {"uf": uf.strip().upper()}})

    return {
        "size": MAX_RESULTS,
        "query": {
            "bool": {
                "must": must_clauses,
                "filter": filter_clauses,
            }
        },
        "_source": SOURCE_FIELDS,
        "sort": [{"_score": {"order": "desc"}}],
    }


def _match_socio_index(
    source: dict[str, Any],
    nome_socio: str | None,
    cpf_cnpj_socio: str | None,
) -> int:
    """Índice do sócio mais provável nos arrays paralelos."""
    nomes = source.get("socios_nomes") or []
    cpfs = source.get("socios_cpf_cnpj") or []
    if not isinstance(nomes, list):
        nomes = []
    if not isinstance(cpfs, list):
        cpfs = []

    if cpf_cnpj_socio and str(cpf_cnpj_socio).strip():
        target = str(cpf_cnpj_socio).strip()
        for i, c in enumerate(cpfs):
            if c and target == str(c).strip():
                return i
        for i, c in enumerate(cpfs):
            if c and target in str(c):
                return i

    if nome_socio and str(nome_socio).strip():
        q = str(nome_socio).strip().lower()
        for i, n in enumerate(nomes):
            if n and q in str(n).lower():
                return i

    return 0


def format_busca_socio_results(
    hits: list[dict],
    nome_socio: str | None = None,
    cpf_cnpj_socio: str | None = None,
) -> list[dict[str, Any]]:
    return [
        _format_hit_socio(hit, nome_socio=nome_socio, cpf_cnpj_socio=cpf_cnpj_socio)
        for hit in hits
    ]


def _format_hit_socio(
    hit: dict,
    *,
    nome_socio: str | None,
    cpf_cnpj_socio: str | None,
) -> dict[str, Any]:
    source = hit.get("_source", {})
    b, o, d = _cnpj_triple_flat(source)

    idx = _match_socio_index(source, nome_socio, cpf_cnpj_socio)
    nomes = source.get("socios_nomes") or []
    quals = source.get("socios_qualificacoes") or []
    if not isinstance(nomes, list):
        nomes = []
    if not isinstance(quals, list):
        quals = []

    qualif = quals[idx] if idx < len(quals) else None
    if qualif is not None:
        qualif = str(qualif)

    codigo = source.get("cnae_principal")
    descricao = source.get("cnae_desc")
    cnae_text = f"{codigo} - {descricao}" if codigo and descricao else codigo or descricao

    municipio = extract_municipio_nome(source.get("municipio"))

    return {
        "cnpj_basico": b,
        "cnpj_completo": format_cnpj(b, o, d),
        "razao_social": source.get("razao_social") or "",
        "situacao": source.get("situacao"),
        "uf": source.get("uf"),
        "municipio": municipio,
        "cnae_principal": cnae_text,
        "qualificacao_socio": qualif,
        "data_entrada": None,
    }


async def executar_busca_por_socio(
    os_service: OpenSearchService,
    nome_socio: str | None = None,
    cpf_cnpj_socio: str | None = None,
    uf: str | None = None,
    apenas_ativas: bool = False,
) -> dict[str, Any]:
    query = build_busca_socio_query(
        nome_socio=nome_socio,
        cpf_cnpj_socio=cpf_cnpj_socio,
        uf=uf,
        apenas_ativas=apenas_ativas,
    )

    logger.info(
        "Querying %s for sócios: nome=%r cpf_cnpj=%r uf=%s ativas=%s",
        INDEX_CNPJ,
        nome_socio,
        cpf_cnpj_socio,
        uf,
        apenas_ativas,
    )

    result = await os_service.search_with_meta(INDEX_CNPJ, query)
    total = result.get("total", 0)
    hits = result.get("hits", [])

    if not hits:
        return {"total": 0, "resultados": []}

    resultados = format_busca_socio_results(
        hits, nome_socio=nome_socio, cpf_cnpj_socio=cpf_cnpj_socio
    )
    logger.info("buscar_por_socio: Found %s matches (%s fetched)", total, len(resultados))

    return {
        "total": total,
        "resultados": resultados,
    }
