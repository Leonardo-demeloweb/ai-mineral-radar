"""
MCP → LangChain Tool Bridge
=============================

Converts MCP tools (discovered via UnifiedMCPProvider) into
LangChain StructuredTool instances that LangGraph can bind to the LLM.

Flow:
    UnifiedMCPProvider.get_all_tools()
        → list of MCP tool descriptors (name, description, input_schema)
        → convert_mcp_tools_to_langchain()
        → list of langchain_core.tools.StructuredTool
        → bind to ChatOpenAI via .bind_tools()

The bridge preserves:
    - Tool names (prefixed: "jazidas__buscar_fornecedores")
    - Descriptions (for LLM routing decisions)
    - Input schemas (JSON Schema → Pydantic for validation)
    - Async invocation (calls MCP Server via Streamable HTTP)

Backend-side guardrails (rules moved out of the system prompt):
    - Default `por_pagina=20` injected for buscar_* tools when the LLM
      doesn't pass it (replaces Rule 6).
    - geo__geocodificar with an ANM process number (e.g. "870.773/2012")
      is short-circuited to a structured error redirecting the agent to
      jazidas__detalhes_processo (replaces Rule 8.a).
    - Quando uma isócrona ativa existe no session state (capturada em
      chat.py logo após geo__calcular_isocrona), uma chamada de
      jazidas__buscar_jazidas / empresas__buscar_empresas com raio é
      automaticamente reescrita para a variante *_por_poligono usando o
      polígono da isócrona — isto elimina o bug "raio circular cobre
      regiões fora da isócrona / exclui regiões dentro".
"""

import json
import logging
import re
from typing import Any

from langchain_core.tools import StructuredTool, ToolException

from app.langgraph.session_state import (
    get_active_isochrone_polygon,
    get_route_plan,
    get_route_user_message,
    get_user_cited_processos,
)
from mcp_servers.common.unified_mcp_provider import UnifiedMCPProvider

logger = logging.getLogger("langgraph.tools")


# ── Backend guardrails (substituem regras do system prompt) ──────────────────

# Tools onde a paginação `por_pagina` faz sentido. Quando o LLM não passar
# explicitamente, injetamos 20 como default — o tamanho que garante
# 10–15 resultados úteis por chamada (Regra 6 do prompt original).
_PAGINATED_TOOLS: frozenset[str] = frozenset({
    "buscar_fornecedores",
    "buscar_jazidas",
    "buscar_empresas",
    "buscar_por_socio",
    "jazidas_por_poligono",
    "fornecedores_por_poligono",
    "empresas_por_poligono",
})
_DEFAULT_PER_PAGE = 20

# Regex que reconhece "número de processo ANM" como argumento de
# geocodificação (formato NNN.NNN/AAAA, opcionalmente prefixado por
# "processo" / "jazida"). O Azure Maps Search retornaria coordenadas
# aleatórias para isso — abortamos antes de gastar a chamada.
_ANM_PROCESSO_RE = re.compile(
    r"""
    \b
    (?:processo\s+|jazida\s+|protocolo\s+)?   # prefixo opcional
    \d{3}\.\d{3}/\d{4}                        # NNN.NNN/AAAA
    \b
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)

def _looks_like_cnpj_endereco(text: Any) -> bool:
    """True quando o texto é essencialmente só um CNPJ (14 dígitos)."""
    if not isinstance(text, str):
        return False
    s = text.strip()
    if not s:
        return False
    digits = re.sub(r"\D", "", s)
    return len(digits) == 14 and digits.isdigit()


def _is_anm_processo_query(endereco: Any) -> bool:
    """True quando `endereco` parece um número de processo ANM puro."""
    if not isinstance(endereco, str):
        return False
    s = endereco.strip()
    if not s:
        return False
    return bool(_ANM_PROCESSO_RE.fullmatch(s))


def _route_tokens(text: str) -> set[str]:
    """Tokens significativos para comparar rótulos de origem/destino."""
    stop = {
        "mina", "minas", "do", "da", "de", "dos", "das", "porto", "portos",
        "brasil", "canaa", "carajas",
    }
    words = re.findall(r"[a-z0-9]{3,}", text.lower())
    return {w for w in words if w not in stop}


def _route_endereco_overlap(a: str, b: str) -> bool:
    ta, tb = _route_tokens(a), _route_tokens(b)
    if not ta or not tb:
        return False
    return bool(ta & tb)


def _route_endereco_swapped(
    origem_kw: str, destino_kw: str, origem_plan: str, destino_plan: str,
) -> bool:
    """True se o LLM trocou origem/destino em relação ao plano do turno."""
    ok_direct = (
        _route_endereco_overlap(origem_kw, origem_plan)
        and _route_endereco_overlap(destino_kw, destino_plan)
    )
    if ok_direct:
        return False
    return (
        _route_endereco_overlap(origem_kw, destino_plan)
        and _route_endereco_overlap(destino_kw, origem_plan)
    )


# ── Guardrail 3: redirect raio→polígono quando há isócrona ativa ─────────────
#
# Mapeia o nome prefixado da tool de busca por raio para a variante por
# polígono, junto com a função que adapta os kwargs (descarta lat/lon/raio_km,
# preserva filtros relevantes). Disparado APENAS se ``session_state`` tiver
# isochrone_polygon válido, o que só acontece quando o LLM acabou de chamar
# geo__calcular_isocrona NESTE turno.

def _adapt_kwargs_jazidas(kwargs: dict[str, Any], polygon: dict) -> dict[str, Any]:
    """
    buscar_jazidas(latitude, longitude, raio_km, termo_busca, fase,
                   apenas_ativos, pagina, por_pagina, ...)
        → jazidas_por_poligono(geometry, substancia, fase,
                               apenas_ativos, pagina, por_pagina, ...)
    Descarta latitude/longitude/raio_km/uf/municipio (irrelevantes — o
    polígono já delimita a área). ``termo_busca`` mapeia para ``substancia``.
    """
    keep = {"fase", "apenas_ativos", "pagina", "por_pagina"}
    new_kwargs: dict[str, Any] = {
        "geometry": polygon,
        # Isócrona: pins devem cair dentro do polígono (não basta a concessão cruzar).
        "localizacao_dentro_poligono": True,
    }
    # buscar_jazidas usa ``termo_busca``; jazidas_por_poligono espera ``substancia``.
    if kwargs.get("termo_busca") is not None:
        new_kwargs["substancia"] = kwargs["termo_busca"]
    for k, v in kwargs.items():
        if k in keep and v is not None:
            new_kwargs[k] = v
    return new_kwargs


def _adapt_kwargs_fornecedores(kwargs: dict[str, Any], polygon: dict) -> dict[str, Any]:
    """
    buscar_fornecedores(substancia, latitude, longitude, raio_km, uf, fase,
                        apenas_ativos, incluir_contatos, incluir_socios,
                        incluir_geometria, pagina, por_pagina)
        → fornecedores_por_poligono(substancia, geometry, uf, fase,
                                    apenas_ativos, incluir_contatos,
                                    incluir_socios, incluir_geometria,
                                    pagina, por_pagina)
    Descarta latitude/longitude/raio_km. Mantém o cross-walk de 3 índices
    para preservar contatos/sócios.
    """
    keep = {
        "substancia", "uf", "fase", "apenas_ativos",
        "incluir_contatos", "incluir_socios", "incluir_geometria",
        "pagina", "por_pagina",
    }
    new_kwargs: dict[str, Any] = {"geometry": polygon}
    for k, v in kwargs.items():
        if k in keep and v is not None:
            new_kwargs[k] = v
    return new_kwargs


def _adapt_kwargs_empresas(kwargs: dict[str, Any], polygon: dict) -> dict[str, Any]:
    """
    buscar_empresas(termo_busca, codigos_cnae, latitude, longitude, raio_km,
                    uf, apenas_ativas, incluir_contatos, incluir_geometria,
                    incluir_mei, pagina, por_pagina, ordenar_por)
        → empresas_por_poligono(geometry, termo_busca, codigos_cnae, uf,
                                apenas_ativas, incluir_contatos,
                                incluir_geometria, incluir_mei,
                                pagina, por_pagina)
    Descarta latitude/longitude/raio_km/ordenar_por.
    """
    keep = {
        "termo_busca", "codigos_cnae", "uf",
        "apenas_ativas", "incluir_contatos", "incluir_geometria",
        "incluir_mei", "pagina", "por_pagina",
    }
    new_kwargs: dict[str, Any] = {"geometry": polygon}
    for k, v in kwargs.items():
        if k in keep and v is not None:
            new_kwargs[k] = v
    return new_kwargs


# (prefixed_name → (target_prefixed_name, adapter))
_RADIUS_TO_POLYGON_REDIRECTS: dict[
    str,
    tuple[str, Any],
] = {
    "jazidas__buscar_jazidas": (
        "jazidas__jazidas_por_poligono",
        _adapt_kwargs_jazidas,
    ),
    "jazidas__buscar_fornecedores": (
        "jazidas__fornecedores_por_poligono",
        _adapt_kwargs_fornecedores,
    ),
    "empresas__buscar_empresas": (
        "empresas__empresas_por_poligono",
        _adapt_kwargs_empresas,
    ),
}

# Tools de polígono que aceitam ``geometry`` e disparam o auto-fill quando
# o LLM chama diretamente sem passar a geometria. Mantemos como bare names
# para casar independentemente do prefixo do servidor MCP.
_POLYGON_TOOLS_NEED_GEOMETRY: frozenset[str] = frozenset({
    "jazidas_por_poligono",
    "fornecedores_por_poligono",
    "empresas_por_poligono",
})




def convert_mcp_tools_to_langchain(
    provider: UnifiedMCPProvider,
) -> list[StructuredTool]:
    """
    Convert all MCP tools from UnifiedMCPProvider into LangChain tools.

    Each tool becomes a StructuredTool that:
    - Has the prefixed name (e.g., "jazidas__buscar_fornecedores")
    - Preserves the MCP description (guides LLM tool selection)
    - Calls the MCP server via provider.call_prefixed_tool()

    Também registra synthetic tools (orquestradores cross-MCP) definidos em
    ``synthetic_tools.py`` — tools que combinam várias MCPs em UMA única
    chamada atômica do ponto de vista do LLM (ex.: `buscar_dentro_de_isocrona`).

    Args:
        provider: Connected UnifiedMCPProvider instance.

    Returns:
        List of StructuredTool instances ready for LLM binding.
    """
    mcp_tools = provider.get_all_tools()
    langchain_tools: list[StructuredTool] = []

    for tool_desc in mcp_tools:
        prefixed_name = tool_desc["name"]
        description = tool_desc.get("description", "")
        input_schema = tool_desc.get("input_schema", {})

        lc_tool = _build_langchain_tool(
            provider=provider,
            prefixed_name=prefixed_name,
            description=description,
            input_schema=input_schema,
        )
        langchain_tools.append(lc_tool)

    # Synthetic tools (cross-MCP orchestrators) — adicionadas ao final.
    from app.langgraph.synthetic_tools import build_synthetic_tools
    synthetic = build_synthetic_tools(provider)
    langchain_tools.extend(synthetic)

    logger.info(
        f"Converted {len(mcp_tools)} MCP tools + {len(synthetic)} synthetic = "
        f"{len(langchain_tools)} total LangChain tools: "
        f"{[t.name for t in langchain_tools]}"
    )

    return langchain_tools


def _build_langchain_tool(
    provider: UnifiedMCPProvider,
    prefixed_name: str,
    description: str,
    input_schema: dict[str, Any],
) -> StructuredTool:
    """Build a single LangChain StructuredTool from MCP tool descriptor."""

    # Nome simples sem o prefixo do servidor MCP (ex.: "buscar_jazidas").
    bare_name = prefixed_name.split("__", 1)[-1]

    async def _invoke(**kwargs: Any) -> str:
        """Async invocation that calls the MCP server."""

        # ── Guardrail 1: por_pagina default = 20 para tools paginadas ────
        # Substitui a Regra 6 do system prompt — o LLM esquecia de passar
        # e voltava com 5–10 resultados, abaixo do mínimo desejado.
        if bare_name in _PAGINATED_TOOLS and kwargs.get("por_pagina") in (None, 0):
            kwargs["por_pagina"] = _DEFAULT_PER_PAGE
            logger.info(
                "[guardrail] %s: injecting por_pagina=%d (LLM didn't pass)",
                prefixed_name, _DEFAULT_PER_PAGE,
            )

        # ── Guardrail 2: bloquear geocoding de número de processo ANM ────
        # Substitui a Regra 8.a — Azure Maps retorna coordenadas
        # aleatórias para "870.773/2012". Reroteamos para detalhes_processo
        # antes de gastar a chamada.
        if bare_name == "geocodificar" and _is_anm_processo_query(
            kwargs.get("endereco")
        ):
            logger.warning(
                "[guardrail] %s: blocking ANM-processo geocode (endereco=%r)",
                prefixed_name, kwargs.get("endereco"),
            )
            return json.dumps({
                "sucesso": False,
                "erro": "argumento_invalido",
                "mensagem": (
                    f"'{kwargs.get('endereco')}' parece um número de processo "
                    "ANM (formato NNN.NNN/AAAA), não um endereço. O Azure Maps "
                    "Search NÃO geocodifica processos minerários — retornaria "
                    "coordenadas aleatórias e ERRADAS. Use a tool "
                    "jazidas__detalhes_processo(ds_processo='...') para obter "
                    "as coordenadas reais da jazida em processo.localizacao "
                    "(lat/lon)."
                ),
            }, ensure_ascii=False)

        # ── Guardrail 2b: CNPJ em *_endereco de calcular_rota ─────────────
        # Azure Search interpreta "32.810.525/0001-89" como query vaga —
        # origem/destino podem cair milhares de km fora (ex.: Nordeste vs RN).
        if bare_name == "calcular_rota":
            plan = get_route_plan()
            if plan and plan.get("is_route_request"):
                kwargs = dict(kwargs)
                po = plan.get("origem_endereco")
                pd = plan.get("destino_endereco")
                # Plano dinâmico da pergunta tem prioridade sobre lat/lon inventados pelo LLM.
                if plan.get("strategy") == "calcular_rota_enderecos":
                    if po:
                        kwargs["origem_endereco"] = po
                        kwargs.pop("origem_lat", None)
                        kwargs.pop("origem_lon", None)
                    if pd:
                        kwargs["destino_endereco"] = pd
                        kwargs.pop("destino_lat", None)
                        kwargs.pop("destino_lon", None)
                elif plan.get("strategy") == "calcular_rota_processo_destino" and pd:
                    kwargs["destino_endereco"] = pd
                    kwargs.pop("destino_lat", None)
                    kwargs.pop("destino_lon", None)

                if po and pd:
                    ko = str(kwargs.get("origem_endereco") or "")
                    kd = str(kwargs.get("destino_endereco") or "")
                    if ko and kd and _route_endereco_swapped(ko, kd, str(po), str(pd)):
                        logger.warning(
                            "[guardrail] %s: origem/destino invertidos — corrigindo "
                            "(%r, %r) → (%r, %r)",
                            prefixed_name, ko, kd, kd, ko,
                        )
                        kwargs["origem_endereco"], kwargs["destino_endereco"] = kd, ko
                        kwargs.pop("origem_lat", None)
                        kwargs.pop("origem_lon", None)
                        kwargs.pop("destino_lat", None)
                        kwargs.pop("destino_lon", None)

            um = get_route_user_message()
            if um:
                kwargs["contexto_pergunta"] = um

            for key in ("origem_endereco", "destino_endereco"):
                val = kwargs.get(key)
                if val is not None and _looks_like_cnpj_endereco(val):
                    logger.warning(
                        "[guardrail] %s: blocking CNPJ in %s=%r — use "
                        "empresas__detalhes_empresa → empresa.localizacao",
                        prefixed_name, key, val,
                    )
                    return json.dumps({
                        "sucesso": False,
                        "erro": "cnpj_nao_e_endereco",
                        "mensagem": (
                            f"O parâmetro {key!r} parece um CNPJ ({val!r}), não um "
                            "endereço. Passar CNPJ ao geocoder da rota produz "
                            "coordenadas incorretas e a polilinha no mapa fica "
                            "errada. Chame empresas__detalhes_empresa com "
                            "cnpj_basico (8 primeiros dígitos) ou o CNPJ completo "
                            "normalizado, leia empresa.localizacao.lat/lon e "
                            "invoque geo__calcular_rota com origem_lat/origem_lon "
                            "(ou destino_*). Se localizacao for nula, use "
                            "geo__geocodificar com empresa.contato.endereco + UF."
                        ),
                    }, ensure_ascii=False)

        if bare_name == "comparar_rotas":
            um = get_route_user_message()
            if um:
                kwargs = dict(kwargs)
                kwargs["contexto_pergunta"] = um

        # ── Guardrail 2c: detalhes_processo — só processos citados / não inventar ──
        if bare_name == "detalhes_processo":
            ds = str(kwargs.get("ds_processo") or "").strip()
            cited = get_user_cited_processos()
            plan = get_route_plan()
            try:
                from mcp_servers.jazidas.queries.detalhes import (
                    _normalize_numero_processo,
                    looks_like_numero_processo,
                )
            except ImportError:
                looks_like_numero_processo = lambda _: False  # type: ignore[assignment]
                _normalize_numero_processo = lambda x: x  # type: ignore[assignment]

            if ds and looks_like_numero_processo(ds):
                norm = _normalize_numero_processo(ds)
                cited_norm = {_normalize_numero_processo(p) for p in cited}
                if cited_norm and norm not in cited_norm:
                    logger.warning(
                        "[guardrail] %s: processo %r não citado pelo usuário (citados=%s)",
                        prefixed_name, ds, cited,
                    )
                    return json.dumps({
                        "sucesso": False,
                        "erro": "processo_nao_citado",
                        "mensagem": (
                            f"O processo '{ds}' não aparece na pergunta do usuário. "
                            f"Processos permitidos neste turno: {', '.join(cited)}. "
                            "Não invente números ANM. Para rota entre locais por nome, "
                            "use geo__calcular_rota com origem_endereco e destino_endereco "
                            "conforme o plano dinâmico no system prompt."
                        ),
                        "processos_citados": cited,
                    }, ensure_ascii=False)
            elif ds and not looks_like_numero_processo(ds) and plan:
                if plan.get("is_route_request") and plan.get("strategy") in (
                    "calcular_rota_enderecos",
                    "comparar_rotas",
                ):
                    logger.warning(
                        "[guardrail] %s: '%s' parece local, não processo — use calcular_rota",
                        prefixed_name, ds,
                    )
                    payload: dict[str, Any] = {
                        "sucesso": False,
                        "erro": "use_calcular_rota",
                        "mensagem": (
                            f"'{ds}' não é número de processo ANM. Para esta pergunta "
                            "use geo__calcular_rota ou geo__comparar_rotas com os "
                            "endereços do plano dinâmico — não detalhes_processo."
                        ),
                        "plano_rota": plan,
                    }
                    if plan.get("origem_endereco"):
                        payload["origem_endereco_sugerido"] = plan["origem_endereco"]
                    if plan.get("destino_endereco"):
                        payload["destino_endereco_sugerido"] = plan["destino_endereco"]
                    return json.dumps(payload, ensure_ascii=False)

        # ── Guardrail 3: raio→polígono quando há isócrona ativa no turno ──
        # Quando o LLM chama buscar_jazidas/buscar_empresas com raio E o
        # turno corrente já calculou uma isócrona, redirecionamos para a
        # variante *_por_poligono usando a geometria da isócrona. Evita o
        # bug em que o círculo de raio R cobre regiões fora da isócrona e
        # exclui regiões dentro (formas geometricamente diferentes).
        effective_prefixed_name = prefixed_name
        effective_kwargs = kwargs
        if prefixed_name in _RADIUS_TO_POLYGON_REDIRECTS:
            polygon = get_active_isochrone_polygon()
            if polygon is not None:
                target_name, adapter = _RADIUS_TO_POLYGON_REDIRECTS[prefixed_name]
                effective_kwargs = adapter(kwargs, polygon)
                logger.warning(
                    "[guardrail] redirect %s → %s (isócrona ativa, geom=%s); "
                    "kwargs descartados=%s",
                    prefixed_name, target_name, polygon.get("type"),
                    sorted(set(kwargs) - set(effective_kwargs)),
                )
                effective_prefixed_name = target_name

        # ── Guardrail 3b: auto-fill de geometry em chamadas DIRETAS de       ──
        # ── *_por_poligono quando a LLM esqueceu/não tinha o polígono.      ──
        # Cenário: a LLM segue a regra 8.b e chama empresas_por_poligono /
        # jazidas_por_poligono / fornecedores_por_poligono, mas como o
        # `feature` da isócrona é stripado do contexto após o emit do
        # isochrone_data SSE, a LLM não tem o GeoJSON serializado pra
        # passar como `geometry`. Nessa situação preenchemos com a isócrona
        # ativa do session_state. Sem isso o tool retorna "geometry: Field
        # required" e a LLM faz fallback estranho (pede pro usuário, ou
        # tenta outra coisa).
        if bare_name in _POLYGON_TOOLS_NEED_GEOMETRY and not effective_kwargs.get("geometry"):
            polygon = get_active_isochrone_polygon()
            if polygon is not None:
                effective_kwargs = dict(effective_kwargs)
                effective_kwargs["geometry"] = polygon
                if bare_name == "jazidas_por_poligono":
                    effective_kwargs["localizacao_dentro_poligono"] = True
                logger.warning(
                    "[guardrail] auto-fill geometry em %s a partir da isócrona "
                    "ativa (type=%s) — LLM não passou o parâmetro.",
                    effective_prefixed_name, polygon.get("type"),
                )

        logger.info(
            f">>> Tool invoke START: {effective_prefixed_name}"
            f"({effective_kwargs if effective_prefixed_name == prefixed_name else '...redirected'})"
        )
        try:
            result = await provider.call_prefixed_tool(
                effective_prefixed_name, effective_kwargs
            )
            logger.info(
                f"<<< Tool invoke OK: {effective_prefixed_name} → "
                f"{len(result)} chars"
            )
            return result
        except Exception as e:
            logger.error(
                f"!!! Tool invoke FAILED: {effective_prefixed_name} — "
                f"{type(e).__name__}: {e}"
            )
            raise ToolException(
                f"Erro ao executar {effective_prefixed_name}: {str(e)}"
            ) from e

    args_schema = _build_args_schema(input_schema)

    return StructuredTool(
        name=prefixed_name,
        description=description or f"MCP tool: {prefixed_name}",
        coroutine=_invoke,
        args_schema=args_schema,
        handle_tool_error=True,
    )


def _build_args_schema(input_schema: dict[str, Any]) -> type | None:
    """
    Convert JSON Schema (from MCP tool) to a simple Pydantic model for validation.

    Returns None if schema is empty or can't be converted (LangChain
    will accept dict kwargs without validation in that case).
    """
    if not input_schema or not input_schema.get("properties"):
        return None

    try:
        from pydantic import create_model, Field

        fields: dict[str, Any] = {}
        properties = input_schema.get("properties", {})
        required = set(input_schema.get("required", []))

        type_map = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        for name, prop in properties.items():
            json_type = prop.get("type", "string")
            python_type = type_map.get(json_type, str)
            desc = prop.get("description", "")
            default = prop.get("default")

            if name in required:
                fields[name] = (python_type, Field(description=desc))
            else:
                fields[name] = (
                    python_type | None,
                    Field(default=default, description=desc),
                )

        from pydantic import ConfigDict
        model = create_model(
            "MCPToolArgs",
            __config__=ConfigDict(coerce_numbers_to_str=True),
            **fields,
        )
        return model

    except Exception as e:
        logger.warning(
            f"Could not build args schema from JSON Schema: {e}. "
            "Tool will accept unvalidated kwargs."
        )
        return None
