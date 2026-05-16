"""
Vigência Query Module
========================

Verifica vigência de processos minerários em ``mr_jazidas_v001``.

Dois perfis de documento coexistem:

- **SIGMINE (bot_anm_direto):** campos planos (`numero_processo`, ``ativo``, ``situacao``,
  ``dt_validade``, listas ``substancias_desc`` / ``substancias`` keyword). Vigência por
  substância granular (nested ANM) não existe aqui — usamos inferência ao nível do processo.

- **Legado (estilo anm_v003):** ``dsProcesso``, ``substancias`` nested com datas de vigência.

Usado pela tool ``verificar_vigencia_substancia``.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.jazidas.queries.detalhes import _normalize_numero_processo
from mcp_servers.jazidas.schemas import VigenciaSubstancia

logger = logging.getLogger("mcp.jazidas.queries.vigencia")

# ==================== Constants ====================

INDEX_ANM = "mr_jazidas_v001"

PROCESSO_SOURCE_FIELDS = [
    # SIGMINE / atual
    "numero_processo",
    "ativo",
    "fase",
    "situacao",
    "substancias_desc",
    "substancias",
    "dt_validade",
    "dt_requerimento",
    # Legado nested
    "dsProcesso",
    "btAtivo",
    "faseProcesso.dsFaseProcesso",
    "nmSubstancias",
]

# Nested inner_hits desativados: em ``mr_jazidas_v001`` ``substancias`` costuma ser keyword[],
# onde ``nested`` no OpenSearch quebraria — filtro por ``id_substancia`` é feito em Python.


# ==================== Query Builders ====================


def build_vigencia_query(
    ds_processo: str,
    id_substancia: int | None = None,
) -> dict[str, Any]:
    """
    Resolve o documento pelo número do processo (formato atual) ou campo legado.

    Args:
        ds_processo: Código (ex: "832145/2018", "832.145/2018")
        id_substancia: Opcional — filtro aplicado após fetch (sem query nested ES)

    Returns:
        Corpo JSON da busca OpenSearch
    """
    _ = id_substancia  # filtrado em _extract_vigencias (compatível sem nested)

    numero = _normalize_numero_processo(ds_processo)
    ds_lower = ds_processo.strip().lower()

    should: list[dict[str, Any]] = [
        {"term": {"numero_processo": numero}},
        {"term": {"dsProcesso": ds_lower}},
    ]

    return {
        "size": 1,
        "query": {
            "bool": {
                "should": should,
                "minimum_should_match": 1,
            },
        },
        "_source": PROCESSO_SOURCE_FIELDS,
    }


# ==================== Formatters ====================


def format_vigencia(sub_data: dict) -> VigenciaSubstancia:
    """
    Converte um item **nested legado** (``substancias[]`` estilo API ANM).

    Vigência pela regra nested:
        dtFimVigencia nulo/absente → vigente
        caso contrário → encerrada

    Returns:
        VigenciaSubstancia Pydantic model
    """
    substancia = sub_data.get("substancia", {})
    tipo_uso = sub_data.get("tipoUsoSubstancia", {})
    motivo = sub_data.get("motivoEncerramentoSubstancia", {})

    dt_inicio = sub_data.get("dtInicioVigencia")
    dt_fim = sub_data.get("dtFimVigencia")

    # Vigente = fim is null/empty
    vigente = dt_fim is None

    return VigenciaSubstancia(
        id_substancia=substancia.get("idSubstancia", 0),
        nome=substancia.get("nmSubstancia", "Desconhecida"),
        tipo_uso=tipo_uso.get("dsTipoUsoSubstancia"),
        dt_inicio=_format_date(dt_inicio),
        dt_fim=_format_date(dt_fim),
        vigente=vigente,
        motivo_encerramento=motivo.get("dsMotivoEncerramentoSubstancia")
        if isinstance(motivo, dict)
        else None,
    )


def build_vigencia_summary(vigencias: list[VigenciaSubstancia]) -> dict[str, Any]:
    """
    Build a summary of vigência states for the process.

    Returns:
        Dict with counts and overall status::

            {
                "total": 5,
                "vigentes": 3,
                "encerradas": 2,
                "status": "parcialmente_vigente"
            }

    Possible status values:
        - ``sem_substancias``:  No substances in the process
        - ``todas_vigentes``:   All substances are still active
        - ``todas_encerradas``: All substances have expired
        - ``parcialmente_vigente``: Mix of active and expired
    """
    total = len(vigencias)
    vigentes = sum(1 for v in vigencias if v.vigente)
    encerradas = total - vigentes

    if total == 0:
        status = "sem_substancias"
    elif encerradas == 0:
        status = "todas_vigentes"
    elif vigentes == 0:
        status = "todas_encerradas"
    else:
        status = "parcialmente_vigente"

    return {
        "total": total,
        "vigentes": vigentes,
        "encerradas": encerradas,
        "status": status,
    }


# ==================== Orchestrator ====================


async def executar_verificacao_vigencia(
    os_service: OpenSearchService,
    ds_processo: str,
    id_substancia: int | None = None,
) -> dict[str, Any]:
    """
    Orchestrate the vigência check for substances in a mining process.

    Args:
        os_service: OpenSearchService instance
        ds_processo: Process code (e.g., "832.145/2018")
        id_substancia: Optional specific substance ID to check

    Returns:
        Dict with::

            {
                "encontrado": True,
                "ds_processo": "832.145/2018",
                "fase": "Concessão de Lavra",
                "ativo": True,
                "substancias": [VigenciaSubstancia, ...],
                "resumo": {"total": 3, "vigentes": 2, ...},
                "filtro_aplicado": {"id_substancia": 123} | None,
            }

    If process not found, returns ``{"encontrado": False}``.
    """
    # ── Step 1: Fetch process with substancias ──
    query = build_vigencia_query(ds_processo, id_substancia)

    logger.info(
        f"Querying {INDEX_ANM} vigência for numero_processo~'{ds_processo}'"
        + (f", id_substancia={id_substancia}" if id_substancia else " (all)")
    )

    result = await os_service.search(INDEX_ANM, query)
    hits = result.get("hits", {}).get("hits", [])

    if not hits:
        logger.info(f"Process '{ds_processo}' not found")
        return {"encontrado": False}

    hit = hits[0]
    source = hit.get("_source", {})

    # ── Step 2: Extract substancias ──
    vigencias = _extract_vigencias(hit, id_substancia)

    # ── Step 3: resultado / observações ──
    processo_info = _extract_processo_info(source, ds_processo)
    has_legacy_nested = _has_legacy_nested_substancias(source)

    observacao_filtracao: str | None = None
    if id_substancia is not None and not has_legacy_nested:
        observacao_filtracao = (
            "O filtro id_substancia refere-se aos IDs da API nestada da ANM; "
            "no índice SIGMINE as substâncias vêm apenas como lista textual — "
            "o filtro foi ignorado e todas foram listadas."
        )

    if not vigencias and id_substancia is not None and has_legacy_nested:
        return {
            **processo_info,
            "substancias": [],
            "resumo": build_vigencia_summary([]),
            "filtro_aplicado": {
                "id_substancia": id_substancia,
                "resultado": "Substância não encontrada neste processo.",
            },
        }

    if not vigencias:
        return {
            **processo_info,
            "substancias": [],
            "resumo": build_vigencia_summary([]),
            "observacao": _build_empty_vigencias_observacao(source),
            **(
                {"observacao_filtro": observacao_filtracao}
                if observacao_filtracao
                else {}
            ),
        }

    # ── Step 4: Build summary ──
    resumo = build_vigencia_summary(vigencias)

    logger.info(
        f"Vigência for '{ds_processo}': "
        f"{resumo['vigentes']} vigentes, {resumo['encerradas']} encerradas "
        f"(total: {resumo['total']})"
    )

    final: dict[str, Any] = {
        **processo_info,
        "substancias": [v.model_dump() for v in vigencias],
        "resumo": resumo,
        "filtro_aplicado": {"id_substancia": id_substancia}
        if id_substancia is not None and has_legacy_nested
        else None,
    }
    if observacao_filtracao:
        final["observacao_filtro"] = observacao_filtracao
    return final


# ==================== Internal Helpers ====================


def _has_legacy_nested_substancias(source: dict) -> bool:
    raw = source.get("substancias") or []
    if not isinstance(raw, list) or not raw:
        return False
    first = raw[0]
    return isinstance(first, dict) and first.get("substancia") is not None


def _extract_vigencias(
    hit: dict,
    id_substancia: int | None,
) -> list[VigenciaSubstancia]:
    """Interpreta `_source`: nested legado (ANM API) × SIGMINE (listas keyword)."""
    source = hit.get("_source", {})

    if _has_legacy_nested_substancias(source):
        substancias_raw = source.get("substancias") or []
        vigencias = [format_vigencia(sub) for sub in substancias_raw if isinstance(sub, dict)]
        if id_substancia is not None:
            vigencias = [v for v in vigencias if v.id_substancia == id_substancia]
        return vigencias

    # SIGMINE: inferência ao nível do processo por substância listada em texto/código.
    vig_proc = _infer_vigencia_processo_sigmine(source)
    nomes = _flat_subst_names(source)
    if not nomes:
        return []

    vigencias_flat: list[VigenciaSubstancia] = []
    for idx, nome in enumerate(nomes):
        vigencias_flat.append(
            VigenciaSubstancia(
                id_substancia=(idx + 1),
                nome=nome,
                tipo_uso=None,
                dt_inicio=_format_iso_date_fragment(source.get("dt_requerimento")),
                dt_fim=_format_iso_date_fragment(source.get("dt_validade")),
                vigente=vig_proc,
                motivo_encerramento=(
                    None
                    if vig_proc
                    else "Inferência SIGMINE: não vigente segundo ativo/situação/dt_validade"
                ),
            )
        )

    return vigencias_flat


def _infer_vigencia_processo_sigmine(source: dict) -> bool:
    situacao = str(source.get("situacao") or "").lower()
    if "inativ" in situacao:
        return False

    ativo_ok = bool(source.get("ativo", False))
    if not ativo_ok:
        return False

    dt_fin = source.get("dt_validade")
    if not dt_fin:
        return True

    try:
        s = str(dt_fin)[:10]
        expiry = datetime.fromisoformat(s).date()
        today = datetime.now(timezone.utc).date()
        if expiry < today:
            return False
    except (ValueError, TypeError):
        pass

    return True


def _flat_subst_names(source: dict) -> list[str]:
    desc = source.get("substancias_desc") or []
    if isinstance(desc, str) and desc.strip():
        desc = [desc.strip()]
    if not isinstance(desc, list):
        desc = []

    out: list[str] = []
    for x in desc:
        sx = str(x).strip()
        if sx:
            out.append(sx.upper())

    if out:
        return out

    # fallback: keywords em substancias
    kw = source.get("substancias") or []
    if isinstance(kw, str):
        kw = [kw]
    for x in kw or []:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())

    return out


def _format_iso_date_fragment(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:10] if len(s) >= 10 else s or None


def _extract_processo_info(source: dict, ds_processo: str) -> dict[str, Any]:
    """Metadados básicos para a resposta (SIGMINE ou legado)."""
    np_doc = source.get("numero_processo")
    if np_doc:
        info = {"encontrado": True, "ds_processo": np_doc}
        fase_flat = source.get("fase")
        info["fase"] = fase_flat if isinstance(fase_flat, str) else None
        info["ativo"] = bool(source.get("ativo", False))
        return info

    subs = source.get("substancias") or []
    if subs and isinstance(subs[0], dict) and subs[0].get("substancia") is not None:
        fase_obj = source.get("faseProcesso", {})
        fase = fase_obj.get("dsFaseProcesso") if isinstance(fase_obj, dict) else None
        return {
            "encontrado": True,
            "ds_processo": source.get("dsProcesso", ds_processo),
            "fase": fase,
            "ativo": str(source.get("btAtivo", "")).lower() == "s",
        }

    # Fallback: documento só com formato flat / sem numero_processo
    fase_flat = source.get("fase")
    return {
        "encontrado": True,
        "ds_processo": np_doc or ds_processo,
        "fase": fase_flat if isinstance(fase_flat, str) else None,
        "ativo": bool(source.get("ativo", False)),
    }


def _build_empty_vigencias_observacao(source: dict) -> str:
    if _has_legacy_nested_substancias(source):
        nm = source.get("nmSubstancias") or []
        if (nm_subs := _coerce_string_list(nm)):
            nomes = ", ".join(nm_subs)
            return (
                f"Substâncias flat ({nomes}) sem objeto nested — "
                "vigência detalhada por substância indisponível."
            )

    nm = source.get("substancias_desc") or source.get("substancias")
    if _coerce_string_list(nm):
        return "Lista de substâncias presente mas inválida para inferência automatizada."

    return "Sem substâncias registradas nos campos esperados (SIGMINE)."


def _coerce_string_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [val] if val.strip() else []
    if isinstance(val, list):
        return [str(x) for x in val if x is not None and str(x).strip()]
    return [str(val)]


def _format_date(value: Any) -> str | None:
    """
    Format a date value to ISO date string.

    Handles:
        - None → None
        - str  → pass through
        - int/float (epoch_millis) → "YYYY-MM-DD"
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(
                value / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return str(value)
    return str(value)
