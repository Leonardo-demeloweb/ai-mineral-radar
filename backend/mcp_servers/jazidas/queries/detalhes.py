"""
Detalhes Processo Query Module
================================

Fetches complete details for a single ANM process by ``numero_processo``
and optionally enriches with CNPJ company data from mr_empresas_v001.

Flow:
    Passo 1 → Fetch process from mr_jazidas_v001 (by numero_processo exact match)
    Passo 2 → Fetch company data from mr_empresas_v001 (optional, by cnpj_basico)

Indices:
    - mr_jazidas_v001 (snake_case schema from bot_anm_direto)
    - mr_empresas_v001 (company enrichment — contatos, sócios)

Field mapping (mr_jazidas_v001):
    numero_processo, ativo, area_ha, dt_requerimento, dt_validade, fase,
    substancias_desc, uf, municipio, location, titular {cnpj_basico, ...},
    cfem, prioridade_estrategica, categorias_estrategicas, restricoes_geo,
    cprm_substancias, n_ocorrencias_cprm

Performance:
    Step 1: ~5ms (exact keyword match, single result)
    Step 2: ~10ms (single mr_empresas_v001 lookup)
    Total:  ~15ms (with CNPJ enrichment)
"""

import logging
import re
from typing import Any

from mcp_servers.common.formatters import format_cnpj_desde_basico_e_ordem, only_digits
from mcp_servers.common.opensearch_client import OpenSearchService

logger = logging.getLogger("mcp.jazidas.queries.detalhes")

# ==================== Constants ====================

INDEX_ANM = "mr_jazidas_v001"
INDEX_CNPJ = "mr_empresas_v001"

# Base fields (always fetched — mr_jazidas_v001 schema)
BASE_SOURCE_FIELDS = [
    "numero_processo",
    "ativo",
    "area_ha",
    "dt_requerimento",
    "dt_validade",
    "fase",
    "situacao",
    "substancias_desc",
    "substancias",
    "uf",
    "municipio",
    "regiao",
    "location",
    "geom",
    "titular",
    "cfem",
    "prioridade_estrategica",
    "categorias_estrategicas",
    "n_restricoes_ti",
    "n_restricoes_uc",
    "restricoes_geo",
    "cprm_substancias",
    "n_ocorrencias_cprm",
]

# Optional nested arrays (only when requested)
EVENTOS_FIELDS = ["eventos"]
TITULOS_FIELDS = ["titulos"]

# CNPJ fields for company enrichment — mr_empresas_v001 flat schema
CNPJ_SOURCE_FIELDS = [
    "cnpj_basico",
    "cnpj_completo",
    "razao_social",
    "nome_fantasia",
    "capital_social",
    "porte",
    "situacao",
    "telefone",
    "telefone2",
    "email",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "municipio",
    "uf",
    "socios_nomes",
    "socios_cpf_cnpj",
    "socios_qualificacoes",
]


# ==================== Query Builder ====================

# Formato ANM: 832.145/2018 ou 832145/2018
_NUMERO_PROCESSO_RE = re.compile(
    r"^\s*\d{1,3}\.?\d{1,6}\s*/\s*\d{4}\s*$",
)


def looks_like_numero_processo(texto: str) -> bool:
    """True se o texto parece código ANM (NNN.NNN/AAAA), não nome de mina/local."""
    return bool(_NUMERO_PROCESSO_RE.match((texto or "").strip()))


def _texto_busca_processo(texto: str) -> str:
    """Remove prefixos comuns ('Mina do Salobo' → 'Salobo')."""
    t = (texto or "").strip()
    lower = t.lower()
    for prefix in (
        "mina do ",
        "mina de ",
        "mina ",
        "jazida do ",
        "jazida de ",
        "jazida ",
        "projeto ",
        "complexo ",
    ):
        if lower.startswith(prefix):
            return t[len(prefix) :].strip()
    return t


def build_processo_por_texto_query(texto: str, *, size: int = 5) -> dict[str, Any]:
    """Busca textual em titular, município e substâncias (ex.: 'Salobo')."""
    termo = _texto_busca_processo(texto)
    if not termo or len(termo) < 2:
        termo = texto.strip()
    return {
        "size": size,
        "query": {
            "bool": {
                "should": [
                    {"match": {"titular.nome": {"query": termo, "operator": "and"}}},
                    {"match": {"titular.razao_social": {"query": termo, "operator": "and"}}},
                    {"match": {"municipio": {"query": termo, "fuzziness": "AUTO"}}},
                    {"match": {"substancias_desc": {"query": termo, "fuzziness": "AUTO"}}},
                ],
                "minimum_should_match": 1,
            }
        },
        "_source": BASE_SOURCE_FIELDS,
    }


def _normalize_numero_processo(numero: str) -> str:
    """
    Normalize a process number to match the stored format.

    ANM shapefiles store processes without formatting dots:
        "832.145/2018" → "832145/2018"
        "832145/2018"  → "832145/2018" (unchanged)
    """
    parts = numero.strip().split("/")
    if len(parts) == 2:
        return f"{parts[0].replace('.', '')}/{parts[1]}"
    return numero.strip().replace(".", "")


def build_processo_query(
    ds_processo: str,
    incluir_eventos: bool = False,
    incluir_titulos: bool = False,
) -> dict[str, Any]:
    """
    Build the mr_jazidas_v001 query to fetch a single process by numero_processo.

    Uses exact term match on numero_processo (keyword field).

    Args:
        ds_processo: Process code (e.g., "832.145/2018" or "832145/2018")
        incluir_eventos: Kept for API compatibility — not available in current schema
        incluir_titulos: Kept for API compatibility — not available in current schema
    """
    numero = _normalize_numero_processo(ds_processo)
    return {
        "size": 1,
        "query": {
            "term": {
                "numero_processo": numero,
            }
        },
        "_source": BASE_SOURCE_FIELDS,
    }


def build_cnpj_query(cnpj_basico: str) -> dict[str, Any]:
    """Build a mr_empresas_v001 lookup query (flat schema, one doc per cnpj_basico)."""
    return {
        "size": 1,
        "query": {
            "term": {"cnpj_basico": cnpj_basico},
        },
        "_source": CNPJ_SOURCE_FIELDS,
    }


# ==================== Result Formatter ====================


def format_processo_detalhado(source: dict) -> dict[str, Any]:
    """
    Format a single mr_jazidas_v001 source into a detailed process dict.

    Maps snake_case fields from bot_anm_direto schema.
    Nested arrays (substancias, municipios, pessoas, shapes, etc.) are
    not present in the current schema and return empty lists.
    """
    titular = source.get("titular") or {}
    municipio = source.get("municipio")
    uf = source.get("uf")
    cfem = source.get("cfem") or {}

    raw_digits = only_digits(str(titular.get("cnpj_basico") or ""))
    if len(raw_digits) >= 8:
        cnpj_basico_norm = raw_digits[:8]
    else:
        cnpj_basico_norm = raw_digits or ""

    cnpj_fmt: str | None = None
    if len(cnpj_basico_norm) == 8 and cnpj_basico_norm != "00000000":
        cnpj_fmt = format_cnpj_desde_basico_e_ordem(cnpj_basico_norm, "0001")

    return {
        "ds_processo": source.get("numero_processo", ""),
        "nr_nup": None,
        "ativo": bool(source.get("ativo", False)),
        "area_ha": source.get("area_ha"),
        "dt_protocolo": source.get("dt_requerimento"),
        "dt_prioridade": None,
        "fase": source.get("fase"),
        "situacao": source.get("situacao"),
        "tipo_requerimento": None,
        "unidade_regional": None,
        "unidade_protocolizadora": None,
        "localizacao": source.get("location"),
        "regiao": source.get("regiao"),
        # Substâncias
        "substancias_nomes": _ensure_list(source.get("substancias_desc")),
        "tipos_uso_nomes": [],
        "uf": [uf] if uf else [],
        "municipios_nomes": [municipio] if municipio else [],
        "titulares_nomes": [titular.get("nome") or titular.get("razao_social")] if titular else [],
        # Titular enriquecido
        "titular": {
            "nome": titular.get("nome"),
            "razao_social": titular.get("razao_social"),
            "cnpj_basico": cnpj_basico_norm or titular.get("cnpj_basico"),
            # CNPJ formatado (DVs corretos) assumindo estabelecimento 0001 — SIGMINE costuma trazer só a raiz.
            "cnpj_completo_formatado": cnpj_fmt,
            "situacao_rfb": titular.get("situacao_rfb"),
            "cnae_principal": titular.get("cnae_principal"),
        } if titular else None,
        # CFEM
        "cfem": {
            "total_historico": cfem.get("total_historico"),
            "ultimo_ano": cfem.get("ultimo_ano"),
            "anos_producao": cfem.get("anos_producao"),
        } if cfem else None,
        # Geo
        "geom": source.get("geom"),
        # Estratégico
        "prioridade_estrategica": source.get("prioridade_estrategica"),
        "categorias_estrategicas": source.get("categorias_estrategicas"),
        # Restrições
        "n_restricoes_ti": source.get("n_restricoes_ti"),
        "n_restricoes_uc": source.get("n_restricoes_uc"),
        "restricoes_geo": source.get("restricoes_geo"),
        # CPRM
        "cprm_substancias": source.get("cprm_substancias"),
        "n_ocorrencias_cprm": source.get("n_ocorrencias_cprm"),
        # Vigência
        "dt_validade": source.get("dt_validade"),
        # Nested arrays — not present in current schema
        "substancias": [],
        "municipios": [],
        "pessoas": [],
        "shapes": [],
        "eventos": [],
        "titulos": [],
        "associacoes": [],
    }


def format_empresa_detalhada(source: dict) -> dict[str, Any]:
    """Format mr_empresas_v001 flat source into a company enrichment dict."""
    cnpj_basico = str(source.get("cnpj_basico") or "").zfill(8)
    cnpj_completo = source.get("cnpj_completo") or ""

    raw_cap = source.get("capital_social")
    capital_social = None
    if raw_cap is not None:
        try:
            v = float(raw_cap)
            capital_social = round(v, 2) if v > 0 else None
        except (TypeError, ValueError):
            pass

    socios: list[dict] = []
    nomes = source.get("socios_nomes") or []
    cpfs  = source.get("socios_cpf_cnpj") or []
    quals = source.get("socios_qualificacoes") or []
    if isinstance(nomes, list):
        for i, nome in enumerate(nomes):
            if not nome:
                continue
            socios.append({
                "nome": nome,
                "cpf_cnpj": cpfs[i] if i < len(cpfs) else None,
                "qualificacao": quals[i] if i < len(quals) else None,
            })

    return {
        "razao_social": source.get("razao_social") or "",
        "cnpj_basico": cnpj_basico,
        "cnpj_completo": cnpj_completo or format_cnpj_desde_basico_e_ordem(cnpj_basico, "0001"),
        "nome_fantasia": source.get("nome_fantasia") or None,
        "capital_social": capital_social,
        "porte": source.get("porte") or None,
        "situacao_rfb": source.get("situacao") or None,
        "contato": {
            "telefone": source.get("telefone") or None,
            "telefone2": source.get("telefone2") or None,
            "email": source.get("email") or None,
            "endereco": _build_endereco_flat(source),
        },
        "socios": socios,
    }


# ==================== Orchestrator ====================


async def executar_detalhes_processo(
    os_service: OpenSearchService,
    ds_processo: str,
    incluir_empresa: bool = True,
    incluir_eventos: bool = False,
    incluir_titulos: bool = False,
) -> dict[str, Any]:
    """
    Execute the detail lookup flow for a single process.

    Returns:
        {
            "encontrado": bool,
            "processo": {...},
            "empresa": {...} | None,
        }
    """
    # ── Step 1: Fetch process from anm_v003 ──
    query = build_processo_query(
        ds_processo=ds_processo,
        incluir_eventos=incluir_eventos,
        incluir_titulos=incluir_titulos,
    )

    logger.info(f"Querying {INDEX_ANM} for numero_processo='{ds_processo}'")

    result = await os_service.search(INDEX_ANM, query)
    hits = result.get("hits", {}).get("hits", [])

    resolucao: str | None = "numero_processo"
    if not hits and not looks_like_numero_processo(ds_processo):
        logger.info(
            "Process '%s' not found by number — trying text search",
            ds_processo,
        )
        text_q = build_processo_por_texto_query(ds_processo)
        result = await os_service.search(INDEX_ANM, text_q)
        hits = result.get("hits", {}).get("hits", [])
        if hits:
            resolucao = "busca_por_nome"

    if not hits:
        logger.info(f"Process '{ds_processo}' not found")
        return {
            "encontrado": False,
            "processo": None,
            "empresa": None,
            "parece_numero_processo": looks_like_numero_processo(ds_processo),
        }

    source = hits[0].get("_source", {})
    processo = format_processo_detalhado(source)
    logger.info(
        "Process '%s' found — fase: %s (resolucao=%s)",
        ds_processo,
        processo.get("fase"),
        resolucao,
    )

    # ── Step 2: Enrich with CNPJ company data (optional) ──
    empresa = None
    if incluir_empresa:
        empresa = await _fetch_empresa(os_service, source)

    out: dict[str, Any] = {
        "encontrado": True,
        "processo": processo,
        "empresa": empresa,
    }
    if resolucao:
        out["resolucao"] = resolucao
    return out


async def _fetch_empresa(
    os_service: OpenSearchService,
    anm_source: dict,
) -> dict[str, Any] | None:
    """
    Fetch company data from rfb_cnpj_v003 using the titular's cnpjBasico.

    Strategy:
        1. Extract cnpjBasico from pessoas[] where tipoRelacao = Titular
        2. Fallback: use flat cnpjTitulares[] field
        3. Query rfb_cnpj_v003 with the first available CNPJ
    """
    cnpj = _extract_titular_cnpj(anm_source)
    if not cnpj:
        logger.debug("No cnpjBasico found for empresa enrichment")
        return None

    try:
        query = build_cnpj_query(cnpj)
        result = await os_service.search(INDEX_CNPJ, query)
        hits = result.get("hits", {}).get("hits", [])

        if hits:
            empresa = format_empresa_detalhada(hits[0].get("_source", {}))
            logger.info(f"Company enrichment: {empresa.get('razao_social', 'N/A')}")
            return empresa

        logger.debug(f"No CNPJ record found for '{cnpj}'")
        return None

    except Exception as e:
        logger.warning(f"Failed to fetch empresa for CNPJ '{cnpj}': {e}")
        return None


# ==================== Internal Formatters ====================


def _format_substancias(substancias: list) -> list[dict]:
    """Format nested substancias array with vigência details."""
    result = []
    for sub in substancias:
        item: dict[str, Any] = {}
        # Substancia object
        substancia = sub.get("substancia") or {}
        if substancia:
            item["id_substancia"] = substancia.get("idSubstancia")
            item["nome"] = substancia.get("nmSubstancia", "")
        # Tipo de uso
        uso = sub.get("tipoUsoSubstancia") or {}
        if uso:
            item["tipo_uso"] = uso.get("dsTipoUsoSubstancia", "")
        # Vigência
        item["dt_inicio"] = sub.get("dtInicioVigencia")
        item["dt_fim"] = sub.get("dtFimVigencia")
        item["vigente"] = sub.get("dtFimVigencia") is None
        # Motivo de encerramento
        motivo = sub.get("motivoEncerramentoSubstancia") or {}
        if motivo:
            item["motivo_encerramento"] = motivo.get(
                "dsMotivoEncerramentoSubstancia"
            )
        result.append(item)
    return result


def _format_municipios(municipios: list) -> list[dict]:
    """Format nested municipios array."""
    result = []
    for mun in municipios:
        result.append({
            "id_ibge": mun.get("idMunicipio"),
            "nome": mun.get("nome", ""),
            "sigla_uf": mun.get("siglaUF", ""),
            "nome_uf": mun.get("nomeUF", ""),
            "regiao": mun.get("nomeRegiao"),
            "mesorregiao": mun.get("nomeMesorregiao"),
            "microrregiao": mun.get("nomeMicrorregiao"),
            "capital_uf": mun.get("capitalUF"),
            "amazonia_legal": mun.get("amazoniaLegal"),
            "localizacao": mun.get("localizacao"),
        })
    return result


def _format_pessoas(pessoas: list) -> list[dict]:
    """Format nested pessoas array."""
    result = []
    for p in pessoas:
        pessoa = p.get("pessoa") or {}
        relacao = p.get("tipoRelacao") or {}
        resp_tecnica = p.get("tipoResponsabilidadeTecnica") or {}
        repr_legal = p.get("tipoRepresentacaoLegal") or {}
        result.append({
            "nome": pessoa.get("nmPessoa", ""),
            "cpf_cnpj": pessoa.get("nrCpfCnpj"),
            "tipo_pessoa": pessoa.get("tpPessoa"),
            "cnpj_basico": p.get("cnpjBasico"),
            "relacao": relacao.get("dsTipoRelacao"),
            "responsabilidade_tecnica": resp_tecnica.get(
                "dsTipoResponsabilidadeTecnica"
            ),
            "representacao_legal": repr_legal.get("dsTipoRepresentacaoLegal"),
            "dt_inicio": p.get("dtInicioVigencia"),
            "dt_fim": p.get("dtFimVigencia"),
        })
    return result


def _format_shapes(shapes: list) -> list[dict]:
    """Format nested shapes array (summary — polygons omitted for readability)."""
    result = []
    for s in shapes:
        sub = s.get("substancia") or {}
        uso = s.get("tipoUsoSubstancia") or {}
        fase = s.get("faseProcesso") or {}
        result.append({
            "id": s.get("id"),
            "titular": s.get("titular"),
            "area_ha": s.get("areaHa"),
            "ativa": s.get("ativa"),
            "substancia": sub.get("nmSubstancia"),
            "tipo_uso": uso.get("dsTipoUsoSubstancia"),
            "fase": fase.get("dsFaseProcesso"),
            "tem_poligono": s.get("poligono") is not None,
            "localizacao": s.get("localizacao"),
        })
    return result


def _format_eventos(eventos: list) -> list[dict]:
    """Format nested eventos array."""
    result = []
    for e in eventos:
        evento = e.get("evento") or {}
        result.append({
            "descricao": evento.get("dsEvento", ""),
            "data": e.get("dtEvento"),
            "observacao": e.get("obEvento"),
            "publicacao_dou": e.get("dsPublicacaoDOU"),
        })
    return result


def _format_titulos(titulos: list) -> list[dict]:
    """Format nested titulos array."""
    result = []
    for t in titulos:
        doc_legal = t.get("documentoLegal") or {}
        tipo_doc = t.get("tipoDocumentoLegal") or {}
        situacao = t.get("situacaoDocumentoLegal") or {}
        result.append({
            "numero": t.get("nrTitulo"),
            "documento": doc_legal.get("dsDocumentoLegal"),
            "tipo": tipo_doc.get("dsTipoDocumentoLegal"),
            "situacao": situacao.get("dsSituacaoDocumentoLegal"),
            "dt_publicacao": t.get("dtPublicacao"),
            "dt_vencimento": t.get("dtVencimento"),
        })
    return result


def _format_associacoes(associacoes: list) -> list[dict]:
    """Format nested associacoes array."""
    result = []
    for a in associacoes:
        tipo = a.get("tipoAssociacao") or {}
        result.append({
            "processo_associado": a.get("dsProcessoAssociado"),
            "tipo": tipo.get("dsTipoAssociacao"),
        })
    return result


def _format_socios_detalhado(socios: list) -> list[dict]:
    """Format socios array with qualificação."""
    result = []
    for s in socios:
        nome = s.get("nomeSocioRazaoSocial")
        if not nome:
            continue
        qualif = s.get("qualificacaoSocio") or {}
        result.append({
            "nome": nome,
            "qualificacao": qualif.get("dsQualificacaoSocio"),
        })
    return result


# ==================== Helpers ====================


def _extract_titular_cnpj(source: dict) -> str | None:
    """
    Extract the titular's cnpj_basico from mr_jazidas_v001 source.

    The ``titular`` object is embedded directly in the document by bot_anm_direto.
    """
    titular = source.get("titular") or {}
    raw = titular.get("cnpj_basico")
    if raw is None or raw == "":
        return None
    d = only_digits(str(raw))
    if len(d) >= 8:
        return d[:8]
    return d or None


def _format_obj(obj: Any, desc_field: str) -> str | None:
    """Extract description from a simple {id, ds} object."""
    if not obj or not isinstance(obj, dict):
        return None
    return obj.get(desc_field)


def _build_telefone(source: dict) -> str | None:
    """Build phone string: '(11) 25428111' — legacy nested schema."""
    ddd1 = source.get("ddd1", "")
    tel1 = source.get("telefone1", "")
    if ddd1 and tel1:
        return f"({ddd1}) {tel1}"
    return tel1 or None


def _build_endereco_flat(source: dict) -> str | None:
    """Build display address from mr_empresas_v001 flat fields."""
    parts = []
    logra = str(source.get("logradouro") or "").strip()
    if logra:
        parts.append(logra)
    for campo in ["numero", "complemento", "bairro"]:
        val = str(source.get(campo) or "").strip()
        if val:
            parts.append(val)
    mun_raw = source.get("municipio")
    mun = ""
    if isinstance(mun_raw, dict):
        mun = mun_raw.get("nome") or ""
    elif isinstance(mun_raw, str):
        mun = mun_raw
    uf = str(source.get("uf") or "").strip()
    for val in [mun, uf]:
        if val:
            parts.append(val)
    cep = str(source.get("cep") or "").strip()
    if cep:
        parts.append(f"CEP {cep}")
    return ", ".join(parts) or None


def _build_endereco(source: dict) -> str | None:
    """Build display address: 'AVENIDA PAULISTA, 671, BELA VISTA, CIDADE, UF, CEP 01000-000'."""
    parts = []
    tipo = str(source.get("tipoLogradouro") or "").strip()
    logradouro = str(source.get("logradouro") or "").strip()
    if tipo and logradouro:
        parts.append(f"{tipo} {logradouro}")
    elif logradouro:
        parts.append(logradouro)
    elif tipo:
        parts.append(tipo)
    for campo in ["numero", "complemento", "bairro"]:
        val = source.get(campo)
        if val and str(val).strip():
            parts.append(str(val).strip())
    if not parts:
        return None
    municipio_raw = source.get("municipio")
    municipio = ""
    if isinstance(municipio_raw, dict):
        municipio = municipio_raw.get("nome", "")
    elif isinstance(municipio_raw, str):
        municipio = municipio_raw
    uf = source.get("uf", "")
    cep = source.get("cep", "")
    for val in [municipio, uf]:
        if val and str(val).strip():
            parts.append(str(val).strip())
    if cep:
        parts.append(f"CEP {cep}")
    return ", ".join(parts)


def _ensure_list(val: Any) -> list:
    """Ensure a value is a list."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def _num_base_processo_cfem(s: str) -> str:
    """Parte numérica antes da barra, sem pontos — alinha chave CFEM e mr_jazidas."""
    t = str(s).strip().replace(".", "")
    if not t:
        return ""
    return t.split("/")[0]


def build_multi_processo_geo_query(keys: list[str]) -> dict[str, Any] | None:
    """
    Uma busca OR em mr_jazidas_v001 para localização dos processos do ranking CFEM.

    Chaves vindas do agg CFEM costumam ser só o prefixo numérico (ex.: ``830564``);
    no índice ANM o campo ``numero_processo`` costuma ser ``830564/1980`` → ``prefix``.
    """
    should: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in keys:
        s = str(raw).strip()
        if not s:
            continue
        if "/" in s:
            n = _normalize_numero_processo(s)
            if not n or n in seen:
                continue
            seen.add(n)
            should.append({"term": {"numero_processo": n}})
        else:
            base = _num_base_processo_cfem(s)
            if not base or base in seen:
                continue
            seen.add(base)
            should.append({"prefix": {"numero_processo": base}})
    if not should:
        return None
    return {
        "size": min(100, max(len(should) + 10, 20)),
        "query": {"bool": {"should": should, "minimum_should_match": 1}},
        "_source": [
            "numero_processo",
            "location",
            "municipio",
            "uf",
            "substancias_desc",
            "fase",
            "area_ha",
            "titular",
        ],
    }


async def mapa_pontos_para_ranking_cfem(
    os_service: OpenSearchService,
    ranking_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Monta ``mapa.pontos`` na ordem do ranking, com lat/lon do índice ANM.

    Ignora linhas sem documento ANM ou sem ``location``.
    """
    keys: list[str] = []
    for r in ranking_rows:
        if not isinstance(r, dict):
            continue
        pk = r.get("processo")
        if pk is None or (isinstance(pk, str) and not pk.strip()):
            continue
        keys.append(str(pk).strip())

    query = build_multi_processo_geo_query(keys)
    if query is None:
        return {"pontos": [], "total_pontos": 0}

    result = await os_service.search(INDEX_ANM, query)
    hits = result.get("hits", {}).get("hits", [])

    by_base: dict[str, dict[str, Any]] = {}
    for h in hits:
        src = h.get("_source") or {}
        np = str(src.get("numero_processo") or "")
        base = _num_base_processo_cfem(np)
        if not base or base in by_base:
            continue
        loc = src.get("location")
        if not isinstance(loc, dict):
            continue
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat is None or lon is None:
            continue
        titular = src.get("titular") if isinstance(src.get("titular"), dict) else {}
        tit_nome = titular.get("nome") or titular.get("razao_social")
        titulares = [tit_nome] if tit_nome else []
        uf_val = src.get("uf")
        uf_list = [str(uf_val).strip()] if uf_val and str(uf_val).strip() else []
        mun_val = src.get("municipio")
        mun_list = [str(mun_val)] if mun_val and str(mun_val).strip() else []
        by_base[base] = {
            "lat": float(lat),
            "lon": float(lon),
            "tipo": "jazida",
            "processo": np,
            "substancias": _ensure_list(src.get("substancias_desc")),
            "municipios": mun_list,
            "uf": uf_list,
            "titulares": titulares,
            "fase": src.get("fase"),
            "area_ha": src.get("area_ha"),
            "tipo_requerimento": None,
        }

    pontos: list[dict[str, Any]] = []
    seen_proc: set[str] = set()
    for r in ranking_rows:
        if not isinstance(r, dict):
            continue
        pk = r.get("processo")
        if pk is None:
            continue
        b = _num_base_processo_cfem(str(pk).strip())
        if not b:
            continue
        ponto = by_base.get(b)
        if not ponto:
            continue
        proc = str(ponto["processo"])
        if proc in seen_proc:
            continue
        seen_proc.add(proc)
        row_cfem = float(r.get("total_arrecadado") or 0)
        pontos.append({
            **ponto,
            "total_cfem_periodo": row_cfem,
        })

    return {"pontos": pontos, "total_pontos": len(pontos)}
