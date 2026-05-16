"""
Per-turn session state shared across the LangGraph stack
=========================================================

Permite que ``app/api/routes/chat.py`` (que coordena o stream SSE) compartilhe
contexto efêmero com ``app/langgraph/tools.py`` (a ponte que invoca os MCP
tools) DENTRO do mesmo turno de execução do agente.

Hoje guardamos:

    - ``isochrone_polygon``: o último ``feature.geometry`` (Polygon /
      MultiPolygon) emitido por ``geo__calcular_isocrona`` no turno corrente.
      Isto permite ao bridge redirecionar automaticamente chamadas de
      ``buscar_jazidas`` / ``buscar_empresas`` por raio para suas variantes
      ``*_por_poligono`` quando o usuário acabou de calcular uma isócrona —
      eliminando a inconsistência geométrica de "círculo de raio R cobre
      regiões fora da isócrona e exclui regiões dentro".

Implementação:

    - Um ``ContextVar`` que é setado no início de ``event_generator`` e
      resetado no fim. ContextVar é async-task-local, então cada requisição
      tem seu próprio dicionário.

    - O dicionário em si é mutável; tanto o produtor (chat.py) quanto os
      consumidores (tools.py) podem atualizá-lo durante o turno.

    - Se ninguém setar o ContextVar (ex.: testes), as funções helper
      retornam None / no-op silenciosamente.
"""

from __future__ import annotations

import contextvars
from typing import Any

# Token-default = None: significa "fora de um turno gerenciado". Tools devem
# tratar isso como "não há informação extra disponível" e seguir o caminho
# original sem injeção.
_session_state: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("supplyradar_session_state", default=None)
)


def init_session_state() -> tuple[dict[str, Any], contextvars.Token]:
    """
    Inicializa um dicionário fresco para o turno corrente e o registra no
    ContextVar. Retorna o dict (para o caller poder ler/escrever) junto com
    o Token de reset.

    Uso típico em chat.py::

        state, token = init_session_state()
        try:
            async for event in graph.astream_events(...):
                ...
                # quando uma isócrona é calculada:
                state["isochrone_polygon"] = feature_geometry
        finally:
            reset_session_state(token)
    """
    state: dict[str, Any] = {}
    token = _session_state.set(state)
    return state, token


def reset_session_state(token: contextvars.Token) -> None:
    """Restaura o ContextVar ao estado anterior ao ``init_session_state``."""
    _session_state.reset(token)


def get_session_state() -> dict[str, Any] | None:
    """Retorna o dict do turno atual, ou None fora de um turno gerenciado."""
    return _session_state.get()


def get_active_isochrone_polygon() -> dict[str, Any] | None:
    """
    Retorna o GeoJSON ``geometry`` (Polygon/MultiPolygon) da última isócrona
    calculada NESTE turno, ou None.

    Usado pelo bridge MCP em ``tools.py`` para decidir se deve redirecionar
    uma busca por raio para a variante ``*_por_poligono``.
    """
    state = _session_state.get()
    if not state:
        return None
    poly = state.get("isochrone_polygon")
    if not isinstance(poly, dict):
        return None
    if poly.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    return poly


def set_active_isochrone_polygon(geometry: dict[str, Any] | None) -> None:
    """
    Atualiza a isócrona ativa do turno. Chamar com ``None`` limpa o estado
    (raramente necessário — o ContextVar reseta no fim do turno).
    """
    state = _session_state.get()
    if state is None:
        return  # fora de um turno gerenciado — silenciosamente ignorado
    if geometry is None:
        state.pop("isochrone_polygon", None)
        return
    if not isinstance(geometry, dict) or geometry.get("type") not in (
        "Polygon", "MultiPolygon",
    ):
        return
    state["isochrone_polygon"] = geometry


def set_route_context(
    *,
    route_plan: dict[str, Any] | None = None,
    user_cited_processos: list[str] | None = None,
    user_message: str | None = None,
) -> None:
    """Plano de rota e processos citados na mensagem do utilizador (turno actual)."""
    state = _session_state.get()
    if state is None:
        return
    if route_plan is not None:
        state["route_plan"] = route_plan
    if user_cited_processos is not None:
        state["user_cited_processos"] = list(user_cited_processos)
    if user_message is not None:
        state["route_user_message"] = user_message.strip()


def get_route_plan() -> dict[str, Any] | None:
    state = _session_state.get()
    if not state:
        return None
    plan = state.get("route_plan")
    return plan if isinstance(plan, dict) else None


def get_route_user_message() -> str:
    """Texto integral da pergunta do utilizador neste turno (contexto de geocode)."""
    state = _session_state.get()
    if not state:
        return ""
    msg = state.get("route_user_message")
    return str(msg).strip() if msg else ""


def get_user_cited_processos() -> list[str]:
    state = _session_state.get()
    if not state:
        return []
    cited = state.get("user_cited_processos")
    if isinstance(cited, list):
        return [str(p) for p in cited if p]
    return []
