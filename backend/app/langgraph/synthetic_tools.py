"""
Synthetic LangChain Tools (orquestradores cross-MCP)
======================================================

Tools que NÃO são expostas por nenhum MCP server isoladamente, mas que
combinam várias MCP tools em uma única operação atômica do ponto de vista
do LLM. Cada synthetic tool:

  • Vive aqui em ``app/langgraph`` (não no diretório de nenhum MCP server)
  • É registrada como ``StructuredTool`` ao lado das tools MCP em
    ``convert_mcp_tools_to_langchain``
  • Internamente faz chamadas a múltiplos MCP servers via
    ``UnifiedMCPProvider.call_prefixed_tool``
  • Retorna um payload JSON consolidado para o LLM

Por que existir:
  • Atomicidade: o LLM não pode "esquecer" de chamar a 2ª tool — só
    existe um caminho.
  • Paralelismo: ``asyncio.gather`` sobre as tools MCP subjacentes.
  • Eliminação de prompt engineering frágil: a regra que estava no system
    prompt ("primeiro calcular_isocrona, depois empresas_por_poligono…")
    vira código.

Tools registradas hoje:
  • ``geo__buscar_dentro_de_isocrona`` — calcula isócrona + busca jazidas/
    empresas/fornecedores dentro do polígono em uma única chamada.

Importante para SSE:
  ``chat.py:_handle_tool_end`` reconhece o nome ``buscar_dentro_de_isocrona``
  e extrai o ``feature`` da isócrona + os pontos das buscas internas para
  emitir os eventos ``isochrone_data`` e ``map_data`` que o frontend
  consome — exatamente como faria se as tools tivessem sido chamadas
  separadamente.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_core.tools import StructuredTool, ToolException
from pydantic import ConfigDict, Field, create_model

from mcp_servers.common.unified_mcp_provider import UnifiedMCPProvider

logger = logging.getLogger("langgraph.synthetic_tools")


# Nome exposto ao LLM — prefixado ``geo__`` por consistência visual com
# as outras tools de orquestração geográfica (calcular_rota,
# comparar_rotas, calcular_isocrona).
BUSCA_EM_ISOCRONA_TOOL_NAME = "geo__buscar_dentro_de_isocrona"


# Bare names das synthetic tools — chat.py usa pra detectar e tratar SSE.
SYNTHETIC_TOOL_BARE_NAMES: frozenset[str] = frozenset({
    "buscar_dentro_de_isocrona",
})


_DESCRIPTION_BUSCA_EM_ISOCRONA = """
Tool ATÔMICA para buscar jazidas/fornecedores/empresas DENTRO de uma
isócrona em uma única chamada. PREFIRA esta tool sobre a sequência manual
"calcular_isocrona + *_por_poligono" — atomicidade no backend garante que
TODOS os passos rodam.

O que a tool faz internamente:
  Passo 1: geo__calcular_isocrona(latitude, longitude, criterio, valor, modo)
            — gera o polígono GeoJSON da área alcançável.
  Passo 2: em PARALELO, conforme o que você passar:
            • substancia → jazidas__fornecedores_por_poligono(substancia,
                geometry=…)   — jazidas + dados CNPJ (telefone/email/sócios)
            • termo_busca / codigos_cnae → empresas__empresas_por_poligono(
                termo_busca/codigos_cnae, geometry=…)   — empresas CNPJ
  Retorno: dict consolidado com a isócrona + um bloco por tipo de busca.

PARA QUÊ USAR:
  • "Empresas/fornecedores DE [substância/produto] DENTRO da isócrona de
    X minutos da obra"
  • "[Substância] e [produto] em até X km de caminhão"
  • "Tudo o que tem dentro do polígono violeta" (combina substancia +
    termo_busca para buscar minerais E empresas industriais juntos)

ESCOLHA DOS FILTROS DE BUSCA:
  • substancia (string) — substâncias minerais brutas: areia, brita,
    cascalho, calcário, granito, basalto, etc. Aciona busca de jazidas/
    fornecedores ANM com cross-walk para CNPJ.
  • termo_busca (string) — produtos industrializados / serviços:
    cimento, concreto, pré-moldados, transportadoras, ferro/aço,
    madeira, etc. Aciona k-NN sobre CNAE.
  • codigos_cnae (string CSV) — alternativa a termo_busca quando você
    já tem os códigos CNAE.
  • Pode passar substancia E (termo_busca | codigos_cnae) JUNTOS para
    consulta híbrida (jazidas + empresas, ambos dentro do polígono).

ARGS DE GEOMETRIA:
  Forneça UMA das duas formas:
  • latitude + longitude (preferido — coordenadas exatas da obra/centro).
  • endereco (texto livre — geocodificado internamente).
  Se ambos forem dados, lat/lon TÊM precedência (mais auditável).

PARÂMETROS DA ISÓCRONA:
  • criterio: "tempo" (default — minutos) ou "distancia" (km).
  • valor: 60 (default — interpretado conforme critério).
  • modo: "truck" (caminhão pesado, default) ou "car".

EXEMPLO — "fornecedores de pré-moldados na isócrona de 60 min da obra":
  buscar_dentro_de_isocrona(
      latitude=-10.91, longitude=-37.08,
      criterio="tempo", valor=60, modo="truck",
      termo_busca="pré-moldados",
  )

EXEMPLO — "areia e cimento dentro de 90 min da obra":
  buscar_dentro_de_isocrona(
      latitude=-10.91, longitude=-37.08,
      valor=90,
      substancia="areia",
      termo_busca="cimento",
  )

NÃO chame esta tool sem nenhum filtro — ela retornará só a isócrona sem
listar entidades.
"""


def _coerce_lat_lon(lat: Any, lon: Any) -> tuple[float | None, float | None]:
    """Aceita lat/lon como str/int/float e converte para float."""
    def _f(v: Any) -> float | None:
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return _f(lat), _f(lon)


def _safe_json_loads(payload: Any) -> dict[str, Any]:
    """
    Converte resultado de provider.call_prefixed_tool em dict.
    Tolera string JSON, dict puro ou erro/None.
    """
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        return {"sucesso": False, "mensagem": f"Resposta MCP inesperada: {type(payload).__name__}"}
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        return {
            "sucesso": False,
            "mensagem": f"Resposta MCP não é JSON: {e}",
            "raw_preview": payload[:200],
        }


async def _resolve_endereco_to_coords(
    provider: UnifiedMCPProvider, endereco: str,
) -> tuple[float | None, float | None, str | None]:
    """
    Geocodifica `endereco` via geo__geocodificar e retorna (lat, lon, label_resolvido).
    Se falhar, retorna (None, None, None).
    """
    raw_trim = (endereco or "").strip()
    if len(raw_trim) >= 4:
        try:
            raw_bp = await provider.call_prefixed_tool(
                "geo__buscar_porto",
                {"termo": raw_trim, "limite": 3},
            )
            bp = _safe_json_loads(raw_bp)
            portos = (bp or {}).get("portos") or []
            if (bp or {}).get("sucesso") and portos:
                p0 = portos[0]
                c = p0.get("centro") or {}
                if c.get("lat") is not None and c.get("lon") is not None:
                    label = (
                        f"{p0.get('nome', '')} — {p0.get('municipio', '')}/"
                        f"{p0.get('uf', '')}"
                    ).strip(" —/")
                    return float(c["lat"]), float(c["lon"]), label or raw_trim
        except Exception as exc:
            logger.debug("buscar_porto pré-geocode ignorado: %s", exc)

    try:
        raw = await provider.call_prefixed_tool(
            "geo__geocodificar", {"endereco": endereco, "limite": 1},
        )
    except Exception as exc:
        logger.warning("geocodificar falhou para %r: %s", endereco, exc)
        return None, None, None

    data = _safe_json_loads(raw)
    if not data.get("sucesso"):
        return None, None, None
    resultados = data.get("resultados") or []
    if not resultados:
        return None, None, None
    r = resultados[0]
    lat = r.get("latitude") or r.get("lat")
    lon = r.get("longitude") or r.get("lon")
    label = r.get("endereco_resolvido") or r.get("address") or endereco
    try:
        return float(lat), float(lon), label
    except (TypeError, ValueError):
        return None, None, None


async def _execute_buscar_dentro_de_isocrona(
    provider: UnifiedMCPProvider,
    *,
    latitude: Any = None,
    longitude: Any = None,
    endereco: str | None = None,
    criterio: str = "tempo",
    valor: float = 60.0,
    modo: str = "truck",
    substancia: str | None = None,
    termo_busca: str | None = None,
    codigos_cnae: str | None = None,
    uf: str | None = None,
    apenas_ativos: bool = False,
    incluir_contatos: bool = True,
    incluir_socios: bool = False,
    incluir_geometria: bool = False,
    incluir_mei: bool = True,
    pagina: int = 1,
    por_pagina: int = 20,
) -> dict[str, Any]:
    """Implementação efetiva do tool sintético — separada para facilitar testes."""

    # ── 1. Resolver coordenadas ───────────────────────────────────────────
    lat_f, lon_f = _coerce_lat_lon(latitude, longitude)
    endereco_resolvido: str | None = None
    if (lat_f is None or lon_f is None) and endereco:
        lat_f, lon_f, endereco_resolvido = await _resolve_endereco_to_coords(
            provider, endereco,
        )

    if lat_f is None or lon_f is None:
        return {
            "sucesso": False,
            "mensagem": (
                "Forneça latitude+longitude (preferido) OU `endereco` para "
                "definir o centro da isócrona."
            ),
        }

    # ── 2. Calcular isócrona ──────────────────────────────────────────────
    iso_args = {
        "latitude": lat_f,
        "longitude": lon_f,
        "criterio": criterio,
        "valor": float(valor),
        "modo": modo,
    }
    logger.info("buscar_dentro_de_isocrona: step1 calcular_isocrona %s", iso_args)
    try:
        iso_raw = await provider.call_prefixed_tool(
            "geo__calcular_isocrona", iso_args,
        )
    except Exception as exc:
        logger.exception("buscar_dentro_de_isocrona: calcular_isocrona falhou")
        return {
            "sucesso": False,
            "mensagem": f"Falha ao calcular isócrona: {exc}",
        }

    iso_data = _safe_json_loads(iso_raw)
    if not iso_data.get("sucesso"):
        return {
            "sucesso": False,
            "mensagem": iso_data.get(
                "mensagem", "Não foi possível gerar a isócrona."
            ),
            "isocrona_raw": iso_data,
        }

    feature = iso_data.get("feature")
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        return {
            "sucesso": False,
            "mensagem": (
                "Azure Maps retornou sucesso mas sem GeoJSON Feature válido — "
                "tente reduzir o valor ou trocar o modo."
            ),
        }

    polygon = feature.get("geometry")
    if not isinstance(polygon, dict) or polygon.get("type") not in (
        "Polygon", "MultiPolygon",
    ):
        return {
            "sucesso": False,
            "mensagem": "Geometria da isócrona inválida.",
        }

    # Registra no session_state — assim qualquer chamada subsequente do LLM
    # a buscar_jazidas/buscar_empresas/etc. já cai no auto-redirect/fill.
    try:
        from app.langgraph.session_state import set_active_isochrone_polygon
        set_active_isochrone_polygon(polygon)
    except Exception:
        logger.debug("session_state indisponível (fora de turno?), seguindo")

    # ── 3. Disparar buscas em paralelo ────────────────────────────────────
    tasks: list[asyncio.Task] = []
    labels: list[str] = []

    has_substance = bool(substancia and str(substancia).strip())
    has_company_filter = bool(
        (termo_busca and str(termo_busca).strip())
        or (codigos_cnae and str(codigos_cnae).strip())
    )

    if has_substance:
        # Raw ANM mining processes within the polygon
        jazidas_args: dict[str, Any] = {
            "geometry": polygon,
            "substancia": substancia,
            "apenas_ativos": apenas_ativos,
            "localizacao_dentro_poligono": True,
            "pagina": pagina,
            "por_pagina": min(por_pagina, 50),
        }
        tasks.append(asyncio.create_task(
            provider.call_prefixed_tool(
                "jazidas__jazidas_por_poligono", jazidas_args,
            )
        ))
        labels.append("jazidas")

        # CNPJ-linked suppliers (may return empty if supplier data not indexed)
        forn_args: dict[str, Any] = {
            "substancia": substancia,
            "geometry": polygon,
            "apenas_ativos": apenas_ativos,
            "incluir_contatos": incluir_contatos,
            "incluir_socios": incluir_socios,
            "incluir_geometria": incluir_geometria,
            "pagina": pagina,
            "por_pagina": por_pagina,
        }
        if uf:
            forn_args["uf"] = uf
        tasks.append(asyncio.create_task(
            provider.call_prefixed_tool(
                "jazidas__fornecedores_por_poligono", forn_args,
            )
        ))
        labels.append("fornecedores")

    if has_company_filter:
        emp_args: dict[str, Any] = {
            "geometry": polygon,
            "apenas_ativas": apenas_ativos,
            "incluir_contatos": incluir_contatos,
            "incluir_geometria": incluir_geometria,
            "incluir_mei": incluir_mei,
            "pagina": pagina,
            "por_pagina": por_pagina,
        }
        if termo_busca:
            emp_args["termo_busca"] = termo_busca
        if codigos_cnae:
            emp_args["codigos_cnae"] = codigos_cnae
        if uf:
            emp_args["uf"] = uf
        tasks.append(asyncio.create_task(
            provider.call_prefixed_tool(
                "empresas__empresas_por_poligono", emp_args,
            )
        ))
        labels.append("empresas")

    isocrona_summary = {
        "feature": feature,
        "centro": iso_data.get("centro"),
        "criterio": iso_data.get("criterio", criterio),
        "valor": iso_data.get("valor", valor),
        "modo": iso_data.get("modo", modo),
        "endereco_consultado": endereco,
        "endereco_resolvido": endereco_resolvido,
    }

    if not tasks:
        return {
            "sucesso": True,
            "isocrona": isocrona_summary,
            "mensagem": (
                "Isócrona calculada. Para listar entidades dentro dela, "
                "passe `substancia` (jazidas) e/ou `termo_busca`/"
                "`codigos_cnae` (empresas)."
            ),
            "fornecedores": None,
            "empresas": None,
        }

    logger.info(
        "buscar_dentro_de_isocrona: step2 paralelo, labels=%s",
        labels,
    )
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    consolidated: dict[str, Any] = {
        "sucesso": True,
        "isocrona": isocrona_summary,
    }
    for label, raw in zip(labels, raw_results):
        if isinstance(raw, Exception):
            logger.warning(
                "buscar_dentro_de_isocrona: %s falhou: %s", label, raw,
            )
            consolidated[label] = {
                "sucesso": False,
                "mensagem": f"Erro na busca de {label}: {raw}",
            }
        else:
            consolidated[label] = _safe_json_loads(raw)

    # Resumo executivo para a LLM (números rápidos)
    consolidated["resumo"] = {
        "jazidas_total": (
            consolidated.get("jazidas", {}).get("total")
            or consolidated.get("jazidas", {}).get("meta", {}).get("total", 0)
        ),
        "fornecedores_total": (
            consolidated.get("fornecedores", {}).get("total")
            or consolidated.get("fornecedores", {}).get("meta", {}).get("total", 0)
        ),
        "empresas_total": (
            consolidated.get("empresas", {}).get("total")
            or consolidated.get("empresas", {}).get("meta", {}).get("total", 0)
        ),
    }
    return consolidated


def _build_synthetic_tool_schema() -> type:
    """Pydantic model para validação de args do StructuredTool."""
    fields: dict[str, Any] = {
        "latitude":         (float | None, Field(default=None, description="Latitude do centro da isócrona (preferido).")),
        "longitude":        (float | None, Field(default=None, description="Longitude do centro da isócrona (preferido).")),
        "endereco":         (str | None,   Field(default=None, description="Endereço alternativo a lat/lon (geocodificado internamente).")),
        "criterio":         (str | None,   Field(default="tempo", description="'tempo' (minutos) ou 'distancia' (km).")),
        "valor":            (float | None, Field(default=60.0, description="Valor do critério: 60 = 60 min ou 60 km.")),
        "modo":             (str | None,   Field(default="truck", description="'truck' (default) ou 'car'.")),
        "substancia":       (str | None,   Field(default=None, description="Substância mineral bruta (areia, brita, …) — aciona busca de jazidas/fornecedores.")),
        "termo_busca":      (str | None,   Field(default=None, description="Termo semântico para empresas (cimento, pré-moldados, …) — aciona k-NN CNAE.")),
        "codigos_cnae":     (str | None,   Field(default=None, description="Códigos CNAE CSV — alternativa a termo_busca.")),
        "uf":               (str | None,   Field(default=None, description="Filtrar por UF (ex.: 'SP').")),
        "apenas_ativos":    (bool | None,  Field(default=False, description="Incluir ativos e inativos (default). Passe True para filtrar apenas ativos.")),
        "incluir_contatos": (bool | None,  Field(default=True, description="Incluir telefone/email.")),
        "incluir_socios":   (bool | None,  Field(default=False, description="Incluir sócios (apenas fornecedores).")),
        "incluir_geometria": (bool | None, Field(default=False, description="Incluir polígonos das jazidas/municípios.")),
        "incluir_mei":      (bool | None,  Field(default=True, description="Incluir MEIs (apenas empresas).")),
        "pagina":           (int | None,   Field(default=1, description="Página de resultados.")),
        "por_pagina":       (int | None,   Field(default=20, description="Resultados por página (max 50).")),
    }
    return create_model(
        "BuscarDentroIsocronaArgs",
        __config__=ConfigDict(coerce_numbers_to_str=False),
        **fields,
    )


def build_synthetic_tools(
    provider: UnifiedMCPProvider,
) -> list[StructuredTool]:
    """
    Constrói a lista de StructuredTools sintéticos a serem agregados
    em ``convert_mcp_tools_to_langchain``.
    """
    args_schema = _build_synthetic_tool_schema()

    async def _invoke(**kwargs: Any) -> str:
        logger.info(
            ">>> Synthetic Tool invoke START: %s(%s)",
            BUSCA_EM_ISOCRONA_TOOL_NAME, kwargs,
        )
        try:
            result = await _execute_buscar_dentro_de_isocrona(
                provider, **kwargs,
            )
        except Exception as e:
            logger.exception(
                "!!! Synthetic Tool invoke FAILED: %s",
                BUSCA_EM_ISOCRONA_TOOL_NAME,
            )
            raise ToolException(
                f"Erro ao executar {BUSCA_EM_ISOCRONA_TOOL_NAME}: {e}"
            ) from e

        result_json = json.dumps(result, ensure_ascii=False)
        logger.info(
            "<<< Synthetic Tool invoke OK: %s → %d chars",
            BUSCA_EM_ISOCRONA_TOOL_NAME, len(result_json),
        )
        return result_json

    tool = StructuredTool(
        name=BUSCA_EM_ISOCRONA_TOOL_NAME,
        description=_DESCRIPTION_BUSCA_EM_ISOCRONA,
        coroutine=_invoke,
        args_schema=args_schema,
        handle_tool_error=True,
    )
    return [tool]
