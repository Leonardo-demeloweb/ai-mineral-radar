"""
Detalhes Empresa Query Module
================================

Cross-index enrichment for ``detalhes_empresa`` (Tool 2):

    Passo 1 → Fetch ALL establishments from rfb_cnpj_v003 (by empresa.cnpjBasico)
    Passo 2 → Enrich CNAE principal + secundários (rfb_cnae_v001 hierarchy)
    Passo 2b → Count ANM processes (anm_v003 aggregation by fase)
    Merge   → Combine into EmpresaDetalhada-compatible dict

Indices:
    - rfb_cnpj_v003  (~69M establishments) — base data + nested socios/cnaeFiscalSec.
    - rfb_cnae_v001 (2.394 docs)      — CNAE hierarchy enrichment
    - anm_v003 (956K docs)            — mining processes cross-reference

Performance:
    Step 1:  ~20ms (term match, few results per cnpjBasico)
    Step 2:  ~10ms (msearch on rfb_cnae_v001, few CNAE codes)
    Step 2b: ~15ms (term + aggregation on anm_v003, conditional)
    Total:   ~45-65ms
"""

import logging
from typing import Any

from mcp_servers.common.formatters import (
    build_contato,
    extract_municipio_nome,
    format_cnpj,
    only_digits,
)
from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.empresas.queries.detalhes")

# ==================== Constants ====================

INDEX_CNPJ = "mr_empresas_v001"
INDEX_CNAE = "mr_cnae_v001"
INDEX_ANM = "mr_jazidas_v001"

# Max establishments to fetch for a single cnpjBasico
MAX_ESTABELECIMENTOS = 100

_PORTE_MAP: dict[str, str] = {
    "00": "Não informado",
    "01": "Microempresa",
    "03": "Empresa de Pequeno Porte",
    "05": "Demais (Médio/Grande)",
}

# Max processes to list in the summary
MAX_PROCESSOS_LISTA = 10

# Source fields — mr_empresas_v001 (ETL plano, matriz por ``cnpj_basico``)
DETAIL_SOURCE_FIELDS = [
    "cnpj_basico",
    "cnpj_completo",
    "razao_social",
    "nome_fantasia",
    "capital_social",           # mapeado no índice; ETL preenche quando disponível
    "porte",
    "natureza_juridica",
    "criterio_inclusao",
    "situacao",
    "dt_situacao",
    "dt_abertura",
    "cnae_principal",
    "cnae_desc",
    "cnaes_secundarios",
    "location",
    "uf",
    "municipio",
    "logradouro",               # tipo_logradouro + logradouro já unificados pelo ETL
    "numero",
    "complemento",
    "bairro",
    "cep",
    "capital_social",
    "telefone",                 # ddd1+telefone1 unificados pelo ETL
    "telefone2",
    "email",
    "processos_anm_count",
    "fases_anm",
    "socios_nomes",
    "socios_cpf_cnpj",
    "socios_qualificacoes",
    "socios_count",
    "indexed_at",
]

# CNAE hierarchy fields from rfb_cnae_v001
CNAE_HIERARCHY_FIELDS = [
    "codigo",
    "nomeSubclasse",
    "nomeClasse",
    "classe",
    "nomeGrupo",
    "grupo",
    "nomeDivisao",
    "divisao",
    "nomeSecao",
    "secao",
    "notasExplicativas",
]


def _is_flat_mr_empresas_source(source: dict) -> bool:
    if not isinstance(source.get("cnpj_basico"), str) or not source.get("cnpj_basico"):
        return False
    emp = source.get("empresa")
    if isinstance(emp, dict) and emp.get("cnpjBasico"):
        return False
    return True


def _cnpj_triple_flat(source: dict) -> tuple[str, str, str]:
    cc = "".join(c for c in str(source.get("cnpj_completo") or "") if c.isdigit())
    if len(cc) == 14:
        return cc[:8], cc[8:12], cc[12:14]
    return str(source.get("cnpj_basico") or "").zfill(8), "0001", "00"


def _build_endereco_detalhado_flat(source: dict) -> dict[str, Any]:
    return {
        "tipo_logradouro": None,   # ETL une tipo+logradouro em um único campo
        "logradouro": (source.get("logradouro") or "").strip() or None,
        "numero": (source.get("numero") or "").strip() or None,
        "complemento": (source.get("complemento") or "").strip() or None,
        "bairro": (source.get("bairro") or "").strip() or None,
        "cep": source.get("cep") or None,
        "municipio": extract_municipio_nome(source.get("municipio")),
        "uf": source.get("uf") or None,
    }


def _build_contato_flat(source: dict) -> dict[str, Any]:
    logra = (source.get("logradouro") or "").strip()
    num = (source.get("numero") or "").strip()
    bairro = (source.get("bairro") or "").strip()
    mun = extract_municipio_nome(source.get("municipio")) or ""
    uf = (source.get("uf") or "").strip()
    cep = (source.get("cep") or "").strip()

    street = ", ".join(p for p in [logra, num, bairro] if p)
    parts = [p for p in [street, mun, uf] if p]
    endereco = ", ".join(parts)
    if cep:
        endereco = f"{endereco} - CEP {cep}" if endereco else f"CEP {cep}"

    return {
        "telefone": source.get("telefone") or None,
        "telefone2": source.get("telefone2") or None,
        "email": source.get("email") or None,
        "endereco": endereco or None,
    }


def _format_empresa_detalhada_flat(
    source: dict,
    *,
    cnae_map: dict[str, dict] | None,
    processos_anm: dict | None,
    incluir_socios: bool,
) -> dict[str, Any]:
    b, o, d = _cnpj_triple_flat(source)
    cnae_principal = _build_cnae_hierarquia(
        str(source.get("cnae_principal") or ""),
        str(source.get("cnae_desc") or ""),
        cnae_map,
    )

    cnaes_sec: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    raw_sec = source.get("cnaes_secundarios") or []
    if isinstance(raw_sec, list):
        for item in raw_sec:
            cod = (
                item.strip()
                if isinstance(item, str)
                else str((item or {}).get("codigo") or "").strip()
            )
            if not cod or cod in seen_codes:
                continue
            seen_codes.add(cod)
            desc = "" if isinstance(item, str) else str((item or {}).get("descricao") or "")
            cnaes_sec.append(_build_cnae_hierarquia(cod, desc, cnae_map))

    socios: list[dict[str, Any]] = []
    if incluir_socios:
        nomes = source.get("socios_nomes") or []
        cpfs = source.get("socios_cpf_cnpj") or []
        quals = source.get("socios_qualificacoes") or []
        if isinstance(nomes, list):
            for i, nome in enumerate(nomes):
                if not nome:
                    continue
                socios.append({
                    "nome": nome,
                    "cpf_cnpj": cpfs[i] if i < len(cpfs) else None,
                    "qualificacao": quals[i] if i < len(quals) else None,
                    "data_entrada": None,
                })

    loc = source.get("location") or source.get("localizacao")
    if not isinstance(loc, dict):
        loc = None

    raw_cap = source.get("capital_social")
    capital_social = None
    if raw_cap is not None:
        try:
            v = float(raw_cap)
            capital_social = round(v, 2) if v > 0 else None
        except (TypeError, ValueError):
            pass

    return {
        "cnpj_basico": b,
        "cnpj_completo": format_cnpj(b, o, d),
        "razao_social": source.get("razao_social") or "",
        "nome_fantasia": source.get("nome_fantasia") or None,
        "capital_social": capital_social,
        "porte": source.get("porte") or None,
        "natureza_juridica": _extract_natureza_juridica(source.get("natureza_juridica")),
        "criterio_inclusao": source.get("criterio_inclusao") or None,
        "situacao": source.get("situacao"),
        "dt_situacao": source.get("dt_situacao") or None,
        "data_inicio_atividade": source.get("dt_abertura"),
        "processos_anm_count": source.get("processos_anm_count"),
        "fases_anm": source.get("fases_anm") or [],
        "endereco": _build_endereco_detalhado_flat(source),
        "contato": _build_contato_flat(source),
        "localizacao": loc,
        "cnae_principal": cnae_principal,
        "cnaes_secundarios": cnaes_sec,
        "socios": socios,
        "processos_anm": processos_anm,
        "estabelecimentos": {
            "total": 1,
            "matriz": format_cnpj(b, o, d),
            "filiais": [],
        },
    }


# ==================== Query Builders ====================


def build_empresa_query(cnpj_basico: str) -> dict[str, Any]:
    """
    Busca o documento em ``mr_empresas_v001`` pelo ``cnpj_basico`` (keyword).

    O ETL grava um documento por CNPJ básico (estabelecimento matriz).
    """
    c = cnpj_basico.strip()
    return {
        "size": MAX_ESTABELECIMENTOS,
        "query": {"term": {"cnpj_basico": c}},
        "_source": DETAIL_SOURCE_FIELDS,
    }


def build_cnae_hierarchy_queries(codigos: list[str]) -> list[dict]:
    """
    Build msearch body for CNAE hierarchy lookup on rfb_cnae_v001.

    Each code gets a separate search header + body pair.

    Args:
        codigos: Unique CNAE codes (e.g., ["0810-0/99", "1710-9/00"])

    Returns:
        List of dicts ready for ``os_service.msearch()``
    """
    body: list[dict] = []
    for codigo in codigos:
        # msearch header
        body.append({"index": INDEX_CNAE})
        # msearch body
        body.append({
            "size": 1,
            "query": {"term": {"codigo": codigo.strip()}},
            "_source": CNAE_HIERARCHY_FIELDS,
        })
    return body


def build_anm_processos_query(cnpj_basico: str) -> dict[str, Any]:
    """
    Build anm_v003 aggregation query to count processes by fase.

    Uses flat ``cnpjTitulares`` field (keyword[]) at root level.
    Aggregates by ``faseProcesso.dsFaseProcesso.keyword``.
    """
    return {
        "size": MAX_PROCESSOS_LISTA,
        "query": {
            "term": {"cnpjTitulares": cnpj_basico.strip().lower()},
        },
        "_source": ["dsProcesso", "faseProcesso.dsFaseProcesso", "btAtivo"],
        "aggs": {
            "por_fase": {
                "terms": {
                    "field": "faseProcesso.dsFaseProcesso.keyword",
                    "size": 20,
                }
            }
        },
    }


# ==================== Result Formatters ====================


def format_empresa_detalhada(
    hits: list[dict],
    cnae_map: dict[str, dict] | None = None,
    processos_anm: dict | None = None,
    incluir_socios: bool = True,
) -> dict[str, Any]:
    """
    Formata hits de ``mr_empresas_v001`` em dict compatível com ``EmpresaDetalhada``.

    O índice ETL tem um hit por ``cnpj_basico`` (matriz).
    """
    if not hits:
        return {}

    matriz = hits[0].get("_source", {})
    if _is_flat_mr_empresas_source(matriz):
        return _format_empresa_detalhada_flat(
            matriz,
            cnae_map=cnae_map,
            processos_anm=processos_anm,
            incluir_socios=incluir_socios,
        )

    empresa_data = matriz.get("empresa", {})

    cnpj_basico = empresa_data.get("cnpjBasico", "")
    cnpj_ordem = matriz.get("cnpjOrdem", "")
    cnpj_dv = matriz.get("cnpjDv", "")

    cnae_principal_raw = matriz.get("cnaeFiscalPrincipal") or {}
    cnae_principal = _build_cnae_hierarquia(
        cnae_principal_raw.get("codigo", ""),
        cnae_principal_raw.get("descricao", ""),
        cnae_map,
    )

    # CNAE secundários (merged from all establishments, deduplicated)
    cnaes_sec = _collect_cnaes_secundarios(hits, cnae_map)

    # Socios (merged from all establishments, deduplicated by nome)
    socios = _collect_socios(hits) if incluir_socios else []

    # Estabelecimentos summary
    estabelecimentos = _build_estabelecimentos_summary(hits)

    # Natureza jurídica
    nat_jur = empresa_data.get("naturezaJuridica") or {}
    nat_jur_desc = _extract_natureza_juridica(nat_jur)

    result: dict[str, Any] = {
        "cnpj_basico": cnpj_basico,
        "cnpj_completo": format_cnpj(cnpj_basico, cnpj_ordem, cnpj_dv),
        "razao_social": empresa_data.get("razaoSocial", ""),
        "nome_fantasia": matriz.get("nomeFantasia") or None,
        "capital_social": empresa_data.get("capitalSocial"),
        "porte": _PORTE_MAP.get(empresa_data.get("porteEmpresa", ""), empresa_data.get("porteEmpresa")),
        "natureza_juridica": nat_jur_desc,
        "situacao": (matriz.get("situacaoCadastral") or {}).get("descricao"),
        "data_inicio_atividade": matriz.get("dataInicioAtividade"),
        "endereco": _build_endereco_detalhado(matriz),
        "contato": build_contato(matriz),
        "localizacao": matriz.get("localizacao"),
        "cnae_principal": cnae_principal,
        "cnaes_secundarios": cnaes_sec,
        "socios": socios,
        "processos_anm": processos_anm,
        "estabelecimentos": estabelecimentos,
    }

    return result


# ==================== CNAE Enrichment ====================


def _build_cnae_hierarquia(
    codigo: str,
    descricao: str,
    cnae_map: dict[str, dict] | None,
) -> dict[str, Any]:
    """
    Build a CnaeHierarquia-compatible dict.

    If cnae_map has the code, enrich with full hierarchy.
    Otherwise, return just codigo + descricao.
    """
    result: dict[str, Any] = {
        "codigo": codigo,
        "descricao": descricao,
    }

    if cnae_map and codigo in cnae_map:
        h = cnae_map[codigo]
        result["secao"] = h.get("secao")
        result["nome_secao"] = h.get("nomeSecao")
        result["divisao"] = h.get("divisao")
        result["nome_divisao"] = h.get("nomeDivisao")
        result["grupo"] = h.get("grupo")
        result["nome_grupo"] = h.get("nomeGrupo")
        result["classe"] = h.get("classe")
        result["nome_classe"] = h.get("nomeClasse")
        result["notas_explicativas"] = h.get("notasExplicativas")

    return result


def _collect_cnaes_secundarios(
    hits: list[dict],
    cnae_map: dict[str, dict] | None,
) -> list[dict[str, Any]]:
    """
    Collect unique CNAE secundários from all establishments.

    Deduplicates by CNAE codigo across all establishments.
    """
    seen: set[str] = set()
    result: list[dict[str, Any]] = []

    for hit in hits:
        source = hit.get("_source", {})
        sec_list = source.get("cnaeFiscalSecundaria") or []
        if isinstance(sec_list, dict):
            sec_list = [sec_list]

        for sec in sec_list:
            codigo = sec.get("codigo", "")
            if not codigo or codigo in seen:
                continue
            seen.add(codigo)
            result.append(
                _build_cnae_hierarquia(
                    codigo,
                    sec.get("descricao", ""),
                    cnae_map,
                )
            )

    return result


# ==================== Socios ====================


def _collect_socios(hits: list[dict]) -> list[dict[str, Any]]:
    """
    Collect unique socios from all establishments.

    Deduplicates by (nome, cpf_cnpj) across all establishments.
    Socios may appear in multiple establishments with the same data.
    """
    seen: set[str] = set()
    result: list[dict[str, Any]] = []

    for hit in hits:
        source = hit.get("_source", {})
        socios_list = source.get("socios") or []
        if isinstance(socios_list, dict):
            socios_list = [socios_list]

        for socio in socios_list:
            nome = socio.get("nomeSocioRazaoSocial", "")
            if not nome:
                continue

            cpf_cnpj = socio.get("cpfCnpjSocio", "")
            dedup_key = f"{nome.lower().strip()}:{cpf_cnpj}"

            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            qualif = socio.get("qualificacaoSocio") or {}
            qualif_desc = (
                qualif.get("descQualificacao")
                or qualif.get("dsQualificacaoSocio")
                or qualif.get("descricao")
            )

            result.append({
                "nome": nome,
                "cpf_cnpj": cpf_cnpj or None,
                "qualificacao": qualif_desc,
                "data_entrada": socio.get("dataEntradaSociedade"),
            })

    return result


# ==================== Estabelecimentos Summary ====================


def _build_estabelecimentos_summary(
    hits: list[dict],
) -> dict[str, Any]:
    """
    Build a summary of all establishments (matriz + filiais).

    Identifies the matriz (cnpjOrdem=0001) and lists filials.
    """
    matriz_cnpj: str | None = None
    filiais: list[str] = []

    for hit in hits:
        source = hit.get("_source", {})
        empresa_data = source.get("empresa", {})
        basico = empresa_data.get("cnpjBasico", "")
        ordem = source.get("cnpjOrdem", "")
        dv = source.get("cnpjDv", "")

        cnpj_full = format_cnpj(basico, ordem, dv)

        if ordem == "0001":
            matriz_cnpj = cnpj_full
        elif cnpj_full:
            filiais.append(cnpj_full)

    return {
        "total": len(hits),
        "matriz": matriz_cnpj,
        "filiais": filiais,
    }


# ==================== ANM Process Summary ====================


def format_processos_anm(result: dict) -> dict[str, Any]:
    """
    Format anm_v003 aggregation result into ProcessoAnmResumo-compatible dict.

    Args:
        result: Raw OpenSearch response from anm_v003 aggregation query

    Returns:
        {
            "total": int,
            "por_fase": {"Concessão de Lavra": 2, ...},
            "processos": ["832.145/2018", ...]
        }
    """
    hits_data = result.get("hits", {})
    total_obj = hits_data.get("total", {})
    total = total_obj.get("value", 0) if isinstance(total_obj, dict) else int(total_obj)

    # Aggregation buckets
    aggs = result.get("aggregations", {})
    por_fase_buckets = aggs.get("por_fase", {}).get("buckets", [])
    por_fase = {b["key"]: b["doc_count"] for b in por_fase_buckets}

    # Process codes from hits
    processos = []
    for hit in hits_data.get("hits", []):
        ds = hit.get("_source", {}).get("dsProcesso")
        if ds:
            processos.append(ds)

    return {
        "total": total,
        "por_fase": por_fase,
        "processos": processos[:MAX_PROCESSOS_LISTA],
    }


# ==================== ANM Fallback ====================


async def buscar_titular_no_anm(
    os_service: OpenSearchService,
    cnpj_basico: str,
) -> dict[str, Any] | None:
    """
    Fallback: fetch titular profile from mr_jazidas_v001 when the CNPJ is not
    in mr_empresas_v001 (e.g., old companies, cancelled registrations, ETL gaps).

    Queries by titular.cnpj_basico (keyword) and returns a simplified profile
    built from the most recent process where the company appears as titular.
    """
    cn = only_digits(str(cnpj_basico))[:8]
    if len(cn) < 8:
        return None
    cnpj_basico = cn

    query = {
        "size": 1,
        "_source": [
            "titular", "numero_processo", "fase",
            "substancias_desc", "municipio", "uf",
        ],
        "query": {
            "term": {"titular.cnpj_basico": cnpj_basico}
        },
        "sort": [{"dt_requerimento": {"order": "desc"}}],
    }

    try:
        result = await os_service.search(INDEX_ANM, query)
    except Exception as e:
        logger.warning(f"buscar_titular_no_anm: query failed: {e}")
        return None

    hits = result.get("hits", {}).get("hits", [])
    if not hits:
        return None

    source = hits[0].get("_source", {})
    titular = source.get("titular") or {}

    return {
        "cnpj_basico": cnpj_basico,
        "razao_social": titular.get("nome") or titular.get("razao_social"),
        "nome_fantasia": None,
        "situacao": titular.get("situacao_rfb"),
        "cnae_principal": titular.get("cnae_principal"),
        "socios": [],
        "fonte": "anm_jazidas",
        "exemplo_processo_anm": source.get("numero_processo"),
        "aviso": (
            "Dados obtidos do índice ANM (titular do processo). "
            "Cadastro completo da Receita Federal não disponível para este CNPJ."
        ),
    }


# ==================== Full Orchestrator ====================


async def executar_detalhes_empresa(
    os_service: OpenSearchService,
    cnpj_basico: str,
    incluir_socios: bool = True,
    incluir_processos_anm: bool = False,
    incluir_cnaes_detalhados: bool = True,
    incluir_cvm: bool = True,
) -> dict[str, Any] | None:
    """
    Execute the complete detalhes_empresa cross-index flow.

    Steps:
        1. Fetch all establishments from rfb_cnpj_v003 (by empresa.cnpjBasico)
        2. Enrich CNAE principal + secundários (rfb_cnae_v001 hierarchy)
        2b. Count ANM processes (anm_v003, conditional)
        3. Merge into EmpresaDetalhada-compatible dict

    Args:
        os_service: OpenSearch async client
        cnpj_basico: CNPJ básico (8 digits, already sanitized)
        incluir_socios: Include socios list (default: True)
        incluir_processos_anm: Include ANM process summary (default: False)
        incluir_cnaes_detalhados: Include full CNAE hierarchy (default: True)

    Returns:
        EmpresaDetalhada-compatible dict or None if empresa not found
    """
    # ── Step 1: Fetch empresa from rfb_cnpj_v003 ──
    query = build_empresa_query(cnpj_basico)

    logger.info(
        f"Querying {INDEX_CNPJ} for cnpj_basico='{cnpj_basico}' "
        f"(socios={'on' if incluir_socios else 'off'}, "
        f"anm={'on' if incluir_processos_anm else 'off'}, "
        f"cnaes={'on' if incluir_cnaes_detalhados else 'off'})"
    )

    result = await os_service.search(INDEX_CNPJ, query)
    hits = result.get("hits", {}).get("hits", [])

    if not hits:
        logger.info(f"Empresa cnpjBasico='{cnpj_basico}' not found")
        return None

    logger.info(
        f"Found {len(hits)} establishments for cnpjBasico='{cnpj_basico}'"
    )

    # ── Step 2: Enrich CNAE hierarchy (conditional) ──
    cnae_map: dict[str, dict] | None = None
    if incluir_cnaes_detalhados:
        cnae_map = await _fetch_cnae_hierarchy(os_service, hits)

    # ── Step 2b: ANM process summary (conditional) ──
    processos_anm: dict | None = None
    if incluir_processos_anm:
        processos_anm = await _fetch_processos_anm(os_service, cnpj_basico)

    # ── Step 3: Merge and format ──
    empresa = format_empresa_detalhada(
        hits=hits,
        cnae_map=cnae_map,
        processos_anm=processos_anm,
        incluir_socios=incluir_socios,
    )

    # ── Step 4: CVM enrichment (non-blocking) ──
    if incluir_cvm:
        try:
            from mcp_servers.empresas.queries.cvm import executar_buscar_cvm_por_cnpj_basico

            cvm_data = await executar_buscar_cvm_por_cnpj_basico(os_service, cnpj_basico)
            if cvm_data:
                empresa["cvm"] = cvm_data
        except Exception as e:
            logger.warning(f"CVM enrichment skip: {e}")

    logger.info(
        f"detalhes_empresa OK: {empresa.get('razao_social', 'N/A')} — "
        f"{empresa.get('estabelecimentos', {}).get('total', 0)} estabs, "
        f"{len(empresa.get('socios', []))} sócios, "
        f"{len(empresa.get('cnaes_secundarios', []))} CNAEs sec."
        f"{' [CVM]' if empresa.get('cvm') else ''}"
    )

    return empresa


# ==================== Cross-Index Helpers ====================


async def _fetch_cnae_hierarchy(
    os_service: OpenSearchService,
    hits: list[dict],
) -> dict[str, dict]:
    """
    Fetch CNAE hierarchy for all unique codes found in the establishments.

    Uses msearch for batch efficiency.

    Returns:
        Dict mapping CNAE code → hierarchy dict from rfb_cnae_v001
    """
    codigos_list = _extract_cnae_codes(hits)
    if not codigos_list:
        return {}

    logger.debug(f"Fetching CNAE hierarchy for {len(codigos_list)} codes")

    # Build msearch body
    msearch_body = build_cnae_hierarchy_queries(codigos_list)

    try:
        result = await os_service.msearch(msearch_body)
        responses = result.get("responses", [])
        cnae_map = _map_cnae_responses(codigos_list, responses)

        logger.info(
            f"CNAE hierarchy: {len(cnae_map)}/{len(codigos_list)} codes enriched"
        )
        return cnae_map

    except Exception as e:
        logger.warning(f"Failed to fetch CNAE hierarchy: {e}")
        return {}


def _extract_cnae_codes(hits: list[dict]) -> list[str]:
    """Extrai códigos CNAE únicos (principal + secundários) dos hits."""
    codigos: set[str] = set()
    for hit in hits:
        source = hit.get("_source", {})
        if _is_flat_mr_empresas_source(source):
            p = source.get("cnae_principal")
            if p:
                codigos.add(str(p).strip())
            for item in source.get("cnaes_secundarios") or []:
                if isinstance(item, str) and item.strip():
                    codigos.add(item.strip())
                elif isinstance(item, dict):
                    c = (item.get("codigo") or "").strip()
                    if c:
                        codigos.add(c)
            continue

        principal = (source.get("cnaeFiscalPrincipal") or {}).get("codigo")
        if principal:
            codigos.add(principal)
        sec_list = source.get("cnaeFiscalSecundaria") or []
        if isinstance(sec_list, dict):
            sec_list = [sec_list]
        for sec in sec_list:
            cod = sec.get("codigo")
            if cod:
                codigos.add(cod)
    return sorted(codigos)


def _map_cnae_responses(codigos_list: list[str], responses: list[dict]) -> dict[str, dict]:
    """Map msearch responses into a codigo -> source dict."""
    cnae_map: dict[str, dict] = {}
    for idx, response in enumerate(responses):
        if idx >= len(codigos_list):
            break
        hits_data = response.get("hits", {}).get("hits", [])
        if not hits_data:
            continue
        cnae_map[codigos_list[idx]] = hits_data[0].get("_source", {})
    return cnae_map


async def _fetch_processos_anm(
    os_service: OpenSearchService,
    cnpj_basico: str,
) -> dict[str, Any] | None:
    """
    Fetch ANM process summary for a cnpjBasico from anm_v003.

    Uses term query on flat cnpjTitulares + aggregation by fase.

    Returns:
        ProcessoAnmResumo-compatible dict or None on failure
    """
    query = build_anm_processos_query(cnpj_basico)

    logger.debug(f"Querying {INDEX_ANM} for cnpjTitulares='{cnpj_basico}'")

    try:
        result = await os_service.search(INDEX_ANM, query)
        summary = format_processos_anm(result)

        if summary["total"] > 0:
            logger.info(
                f"ANM processes for {cnpj_basico}: "
                f"{summary['total']} total, "
                f"{len(summary['por_fase'])} fases"
            )
        else:
            logger.debug(f"No ANM processes found for {cnpj_basico}")

        return summary

    except Exception as e:
        logger.warning(f"Failed to fetch ANM processes for '{cnpj_basico}': {e}")
        return None


# ==================== Formatting Helpers ====================


def _extract_natureza_juridica(nat_jur: Any) -> str | None:
    """Extract natureza jurídica description from dict/string/None values."""
    if not nat_jur:
        return None
    if isinstance(nat_jur, dict):
        return nat_jur.get("descricao")
    return str(nat_jur)




def _build_endereco_detalhado(source: dict) -> dict[str, Any]:
    """Build detailed address dict with separate fields."""
    municipio_nome = extract_municipio_nome(source.get("municipio"))

    return {
        "tipo_logradouro": source.get("tipoLogradouro") or None,
        "logradouro": source.get("logradouro") or None,
        "numero": source.get("numero") or None,
        "complemento": source.get("complemento") or None,
        "bairro": source.get("bairro") or None,
        "cep": source.get("cep") or None,
        "municipio": municipio_nome,
        "uf": source.get("uf") or None,
    }


