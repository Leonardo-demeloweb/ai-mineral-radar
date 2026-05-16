"""
Chat Endpoints
==============

POST /api/v1/chat          — Non-streaming (full JSON response)
POST /api/v1/chat/stream   — SSE streaming (token-by-token via Server-Sent Events)

Both endpoints share the same pre-processing (history load, memory injection)
and post-processing (buffer persist, summarization trigger) logic, factored
into private helpers.

SSE Event types emitted by /stream:
    event: meta       — session_id, route, route_reasoning (sent first)
    event: token      — incremental text chunk from LLM
    event: tool_start — tool call initiated (name, arguments)
    event: tool_end   — tool call completed (name, abbreviated result)
    event: map_data       — geo points from tool result (type, pontos[])
    event: geometry_data  — GeoJSON polygons (jazidas + municipios FeatureCollections)
    event: route_data     — route polyline from calcular_rota (1×) or
                            comparar_rotas (1 SSE per route, N total)
    event: isochrone_data — isochrone GeoJSON feature from calcular_isocrona
    event: pin_data       — address/coordinate pin from plotar_endereco
    event: done           — final metadata (full response, tool_calls_count)
    event: error      — agent execution error

DELETE /api/v1/chat/{session_id}
    Clears conversation history for a session (user starts fresh).
"""

import asyncio
import json
import re
import uuid
from typing import Annotated, Any, AsyncGenerator

from app.langgraph.graph import MAX_TOOL_CALLS_DEFAULT
from app.langgraph.route_planner import analyze_route_request
from app.langgraph.session_state import (
    init_session_state,
    set_active_isochrone_polygon,
    set_route_context,
)

# LangGraph default recursion_limit é 25, mas o grafo deste projeto consome
# 3 super-passos por chamada de tool (tool_executor → post_tool → agent),
# mais 2 do warmup (router → agent). Mínimo ~2 + 3*MAX_TOOL_CALLS_DEFAULT;
# folga extra para ToolNode com chamadas paralelas.
GRAPH_RECURSION_LIMIT = 2 + 3 * MAX_TOOL_CALLS_DEFAULT + 15

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.core.logging import get_logger
from app.db.mongodb import get_database
from app.db.redis import RedisClient, get_redis_client
from app.memory.conversation_buffer import (
    RedisConversationBuffer,
    strip_assistant_tool_calls,
)
from app.memory.long_term import LongTermMemory

logger = get_logger(__name__)

router = APIRouter()


# ── Request / Response schemas ─────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="User message to the MineralRadar AI agent.",
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "Session ID for conversation continuity. "
            "Auto-generated (UUID4) on the first turn if omitted. "
            "Return this value in subsequent requests to maintain context."
        ),
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "User identifier for long-term memory. "
            "Defaults to 'dev-user' in development."
        ),
    )
    projeto_id: str | None = Field(
        default=None,
        description="Optional projeto context — filters tool results to this projeto.",
    )
    analise_id: str | None = Field(
        default=None,
        description="Optional análise context.",
    )


class ChatResponse(BaseModel):
    response: str = Field(description="Agent's final text response.")
    session_id: str = Field(description="Session ID — use in next turn to keep context.")
    route: str = Field(description="Intent route classified by the Router Agent.")
    route_reasoning: str = Field(description="One-sentence reasoning for the route.")
    tool_calls_count: int = Field(description="Number of MCP tool calls made this turn.")


class ClearSessionResponse(BaseModel):
    cleared: bool
    session_id: str


# ── Shared helpers ────────────────────────────────────────────────────────────


async def _fetch_projeto_context(
    projeto_id: str | None,
    db: AsyncIOMotorDatabase,
) -> str | None:
    """
    Fetch projeto from MongoDB and return a context string to embed in the system prompt.

    Returns a plain string (not a SystemMessage) so it can be safely passed via
    AgentState and prepended to SYSTEM_PROMPT inside agent_node — avoiding the
    position-0 SystemMessage replacement that would otherwise discard it.
    """
    if not projeto_id:
        return None
    from bson import ObjectId
    try:
        oid = ObjectId(projeto_id)
    except Exception:
        return None

    try:
        doc = await db["projetos"].find_one({"_id": oid})
    except Exception as e:
        logger.warning("Failed to fetch projeto context for %s: %s", projeto_id, e)
        return None

    if not doc:
        return None

    nome = doc.get("nome", "projeto sem nome")
    loc = doc.get("localizacao")  # {"lat": float, "lon": float}
    raio = doc.get("raio_busca_km", 50)
    municipio = doc.get("municipio") or ""
    uf = doc.get("uf") or ""

    if loc and loc.get("lat") is not None and loc.get("lon") is not None:
        loc_str = f"lat={loc['lat']}, lon={loc['lon']} ({municipio}/{uf})"
    elif municipio or uf:
        loc_str = f"{municipio}/{uf} (sem coordenadas cadastradas — use geo__buscar_municipio para resolver)"
    else:
        loc_str = "localização não cadastrada"

    return (
        f"## Contexto do projeto ativo\n"
        f"Nome: {nome}\n"
        f"Localização: {loc_str}\n"
        f"Raio padrão de busca: {raio} km\n\n"
        f"INSTRUÇÃO: Use esta localização e este raio como ponto de referência padrão "
        f"em todas as buscas geoespaciais, a menos que o usuário especifique "
        f"explicitamente uma localização ou raio diferente."
    )


async def _fetch_analise_context(
    analise_id: str | None,
    db: AsyncIOMotorDatabase,
) -> str | None:
    """
    Fetch análise from MongoDB and return a context string for the system prompt.

    When the análise has no termo_busca yet (just created via dialog), the context
    instructs the agent to infer categoria and termo_busca from the first search.
    """
    if not analise_id:
        return None
    from bson import ObjectId
    try:
        oid = ObjectId(analise_id)
    except Exception:
        return None

    try:
        doc = await db["analises"].find_one({"_id": oid})
    except Exception as e:
        logger.warning("Failed to fetch análise context for %s: %s", analise_id, e)
        return None

    if not doc:
        return None

    titulo = doc.get("titulo", "análise sem título")
    termo = doc.get("termo_busca", "")
    categoria = doc.get("categoria", "hibrido")
    status = doc.get("status", "rascunho")

    if not termo:
        return (
            f"## Contexto da análise ativa\n"
            f"ID: {analise_id}\n"
            f"Título: {titulo}\n"
            f"Status: {status} (nova — aguardando primeira busca)\n\n"
            f"INSTRUÇÃO IMPORTANTE: Esta análise foi recém-criada e ainda não tem "
            f"termo de busca definido. Quando o usuário solicitar a primeira busca, "
            f"os resultados serão automaticamente vinculados a esta análise."
        )

    return (
        f"## Contexto da análise ativa\n"
        f"ID: {analise_id}\n"
        f"Título: {titulo}\n"
        f"Categoria: {categoria}\n"
        f"Termo de busca: {termo}\n"
        f"Status: {status}\n\n"
        f"Os resultados de busca serão vinculados automaticamente a esta análise."
    )


def _resolve_graph(request: Request):
    """Get the compiled LangGraph from app.state or raise 503."""
    graph = getattr(request.app.state, "agent_graph", None)
    if graph is None:
        logger.error("agent_graph not found on app.state — MCP servers offline?")
        raise HTTPException(
            status_code=503,
            detail=(
                "Agente indisponível. Verifique se os MCP servers estão rodando "
                "(jazidas :8110, empresas :8111, geo :8112)."
            ),
        )
    return graph


async def _prepare_messages(
    body: ChatRequest,
    buffer: RedisConversationBuffer,
    long_term: LongTermMemory,
    session_id: str,
    user_id: str,
) -> tuple[list, list, HumanMessage, str | None]:
    """
    Load history + long-term memory and build the full message list.

    Returns (all_messages, history, new_human, context_block).
    """
    history = await buffer.load(session_id)
    context_block = await long_term.build_context_block(user_id)

    new_human = HumanMessage(content=body.message)
    all_messages: list = [*history, new_human]

    if context_block and not any(
        isinstance(m, SystemMessage) and "Fatos conhecidos" in str(m.content)
        for m in all_messages
    ):
        context_msg = SystemMessage(
            content=f"## Memória de longo prazo do usuário\n{context_block}"
        )
        all_messages = [context_msg, *all_messages]

    # Evita 400 OpenAI/Azure: assistente com tool_calls sem ToolMessage seguintes
    # (buffer Redis não guarda o par completo).
    all_messages = strip_assistant_tool_calls(all_messages)

    return all_messages, history, new_human, context_block


def _build_initial_state(
    all_messages: list,
    session_id: str,
    body: ChatRequest,
    projeto_context_str: str | None = None,
    analise_context_str: str | None = None,
) -> dict[str, Any]:
    """Build the initial state dict for graph.ainvoke / graph.astream_events."""
    return {
        "messages": all_messages,
        "conversation_id": session_id,
        "projeto_id": body.projeto_id,
        "analise_id": body.analise_id,
        "projeto_context_str": projeto_context_str,
        "analise_context_str": analise_context_str,
        "route": "",
        "route_reasoning": "",
        "route_execution_hint": "",
        "tool_calls_count": 0,
        "max_tool_calls": MAX_TOOL_CALLS_DEFAULT,
    }


async def _persist_turn(
    buffer: RedisConversationBuffer,
    long_term: LongTermMemory,
    session_id: str,
    user_id: str,
    history: list,
    new_human: HumanMessage,
    final_ai_msg: AIMessage | None,
    route: str,
    projeto_id: str | None,
) -> None:
    """Save new messages to Redis and trigger summarization if needed."""
    messages_to_save: list = [new_human]
    if final_ai_msg:
        if getattr(final_ai_msg, "tool_calls", None):
            final_ai_msg = AIMessage(content=final_ai_msg.content or "")
        messages_to_save.append(final_ai_msg)

    would_overflow = (
        len(history) + len(messages_to_save) > RedisConversationBuffer.MAX_MESSAGES
    )
    if would_overflow:
        full_snapshot = [*history, *messages_to_save]
        logger.info(
            "Buffer overflow imminent — snapshotting %d messages to long-term memory",
            len(full_snapshot),
            extra={"session_id": session_id},
        )
        _pre_trim_task = asyncio.create_task(
            _summarize_in_background(
                long_term, session_id, user_id, full_snapshot,
                projeto_id, [route], force_update=True,
            )
        )

    await buffer.append(session_id, messages_to_save)

    if not would_overflow:
        current_history = await buffer.load(session_id)
        if len(current_history) >= 6:
            _summary_task = asyncio.create_task(
                _summarize_in_background(
                    long_term, session_id, user_id, current_history,
                    projeto_id, [route],
                )
            )


async def _summarize_in_background(
    long_term: LongTermMemory,
    session_id: str,
    user_id: str,
    messages: list,
    projeto_id: str | None,
    route_history: list[str],
    force_update: bool = False,
) -> None:
    """Run summarization in background — never blocks the response."""
    try:
        await long_term.summarize_and_save(
            session_id, user_id, messages, projeto_id, route_history,
            force_update=force_update,
        )
    except Exception as e:
        logger.warning(f"Background summarization failed: {e}")


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=ChatResponse,
    summary="Send a message to the AI agent (non-streaming)",
    responses={
        503: {"description": "Agent unavailable — MCP servers not running"},
        500: {"description": "Agent execution failed"},
    },
)
async def chat(
    body: ChatRequest,
    request: Request,
    redis: Annotated[RedisClient, Depends(get_redis_client)],
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
):
    """
    Non-streaming chat endpoint. Waits for the full agent execution
    and returns the complete response as JSON.
    """
    session_id = body.session_id or str(uuid.uuid4())
    user_id = body.user_id or "dev-user"
    graph = _resolve_graph(request)

    buffer = RedisConversationBuffer(redis)
    long_term = LongTermMemory(db)

    all_messages, history, new_human, context_block = await _prepare_messages(
        body, buffer, long_term, session_id, user_id,
    )

    projeto_context_str = await _fetch_projeto_context(body.projeto_id, db)
    analise_context_str = await _fetch_analise_context(body.analise_id, db)

    logger.info(
        "Chat request received",
        extra={
            "session_id": session_id,
            "user_id": user_id,
            "history_length": len(history),
            "has_long_term_context": context_block is not None,
            "projeto_id": body.projeto_id,
            "analise_id": body.analise_id,
            "has_projeto_context": projeto_context_str is not None,
            "has_analise_context": analise_context_str is not None,
        },
    )

    initial_state = _build_initial_state(all_messages, session_id, body, projeto_context_str, analise_context_str)

    try:
        result = await graph.ainvoke(
            initial_state,
            config={"recursion_limit": GRAPH_RECURSION_LIMIT},
        )
    except Exception as e:
        logger.exception("Agent execution failed", extra={"session_id": session_id})
        raise HTTPException(
            status_code=500,
            detail=f"Erro na execução do agente: {str(e)}",
        )

    final_messages = result.get("messages", [])
    ai_response = ""
    final_ai_msg: AIMessage | None = None

    for msg in reversed(final_messages):
        if isinstance(msg, AIMessage):
            text = _normalize_ai_text_content(msg.content).strip()
            if text:
                ai_response = text
                final_ai_msg = msg
                break

    if not ai_response:
        ai_response = "Não foi possível gerar uma resposta. Tente reformular a pergunta."

    route = result.get("route", "general")

    await _persist_turn(
        buffer, long_term, session_id, user_id,
        history, new_human, final_ai_msg, route, body.projeto_id,
    )

    logger.info(
        "Chat turn complete",
        extra={
            "session_id": session_id,
            "route": route,
            "tool_calls": result.get("tool_calls_count", 0),
        },
    )

    return ChatResponse(
        response=ai_response,
        session_id=session_id,
        route=route,
        route_reasoning=result.get("route_reasoning", ""),
        tool_calls_count=result.get("tool_calls_count", 0),
    )


# ── SSE Streaming Endpoint ────────────────────────────────────────────────────


@router.post(
    "/stream",
    summary="Send a message to the AI agent (SSE streaming)",
    responses={
        503: {"description": "Agent unavailable — MCP servers not running"},
    },
)
async def chat_stream(
    body: ChatRequest,
    request: Request,
    redis: Annotated[RedisClient, Depends(get_redis_client)],
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
):
    """
    Streaming chat endpoint using Server-Sent Events.

    Emits typed SSE events as the LangGraph agent processes:

    - **meta**:       session metadata (sent first)
    - **token**:      incremental LLM text chunk
    - **tool_start**: tool call initiated (name + args)
    - **tool_end**:   tool call completed (name + abbreviated result)
    - **done**:       final response + metadata
    - **error**:      agent execution error
    """
    session_id = body.session_id or str(uuid.uuid4())
    user_id = body.user_id or "dev-user"
    graph = _resolve_graph(request)

    buffer = RedisConversationBuffer(redis)
    long_term = LongTermMemory(db)

    all_messages, history, new_human, _ = await _prepare_messages(
        body, buffer, long_term, session_id, user_id,
    )

    projeto_context_str = await _fetch_projeto_context(body.projeto_id, db)
    analise_context_str = await _fetch_analise_context(body.analise_id, db)

    initial_state = _build_initial_state(all_messages, session_id, body, projeto_context_str, analise_context_str)

    async def event_generator() -> AsyncGenerator[dict[str, str], None]:
        """Yield SSE events from the LangGraph astream_events API."""
        ctx = _StreamContext(session_id=session_id, analise_id=body.analise_id)

        # Compartilha estado efêmero do turno (ex.: isócrona ativa) com a ponte
        # MCP (app/langgraph/tools.py) via ContextVar — viabiliza o redirect
        # automático buscar_jazidas → jazidas_por_poligono quando o usuário
        # acabou de calcular uma isócrona no mesmo turno.
        session_turn_state, _session_token = init_session_state()
        route_plan = analyze_route_request(body.message)
        set_route_context(
            route_plan=route_plan.to_dict(),
            user_cited_processos=route_plan.processos_citados,
            user_message=body.message,
        )

        yield _sse("meta", {"session_id": session_id})

        async def _run_graph(state: dict[str, Any]) -> AsyncGenerator[dict[str, str], None]:
            """Stream a single graph invocation through the SSE handlers."""
            async for event in graph.astream_events(
                state,
                version="v2",
                config={"recursion_limit": GRAPH_RECURSION_LIMIT},
            ):
                sse_event = _process_stream_event(event, ctx)
                if sse_event is not None:
                    yield sse_event
                # Drain any side-channel events appended by handlers (e.g. map_data)
                while ctx.pending_sse:
                    yield ctx.pending_sse.pop(0)

        try:
            async for sse_event in _run_graph(initial_state):
                yield sse_event
        except Exception as e:
            logger.exception("Streaming agent error", extra={"session_id": session_id})
            yield _sse("error", {"message": f"Erro na execução do agente: {str(e)}"})
            return

        # ── Retry automático: rota fantasma (0 tool calls) OU comparação faltante ──
        # Três sinais, em ordem de severidade:
        #   GATILHO 0 (HALLUCINATION SEVERA): usuário pede rota E texto tem
        #   "X km" / "Yh" E ZERO tool calls de rota. LLM puxou número do
        #   histórico ou inventou. Disparo imediato, gap=0.
        #   GATILHO 1 (TEXTO ENUMERATIVO): texto menciona N rotas mas só
        #   houve M < N chamadas. gap ≥ 2 (Distância pode ser incidental).
        #   GATILHO 2 (PEDIDO USUÁRIO): mensagem do usuário pede N rotas
        #   (lista ou "principais"). gap ≥ 1 explícito, ≥ 2 genérico.
        # Limite de UM retry para não loopar.
        mentioned = _count_routes_mentioned_in_text(ctx.accumulated_text)
        expected_user, expected_confianca = _expected_routes_from_user_message(body.message)
        called = ctx.calcular_rota_count

        retry_source: str | None = None
        retry_target: int = 0
        if not ctx.retry_in_progress:
            # GATILHO 0 — rota fantasma. Severo: 0 chamadas mas resposta com km.
            # User asks ROUTE em qualquer forma (singular OU comparação) E o
            # texto tem distância/duração ⇒ LLM puxou números do histórico
            # sem chamar tool.
            user_wants_route = (
                _user_asks_for_route(body.message)
                or expected_user >= 1
            )
            if (
                called == 0
                and user_wants_route
                and _response_mentions_route_metric(ctx.accumulated_text)
            ):
                retry_source = "rota_fantasma"
                # Mínimo 1 rota a calcular; se for comparação, usa expected_user.
                retry_target = max(1, expected_user)
            # GATILHO 1 — texto enumerativo desbalanceado.
            elif called >= 1 and mentioned >= called + 2:
                retry_source = "texto"
                retry_target = mentioned
            # GATILHO 2 — pedido explícito do usuário desbalanceado.
            elif called >= 1:
                user_gap_threshold = 1 if expected_confianca == "explicita" else 2
                if expected_user >= called + user_gap_threshold:
                    retry_source = f"pedido_usuario_{expected_confianca}"
                    retry_target = expected_user

        if retry_source is not None:
            missing = retry_target - called
            logger.warning(
                "Route hallucination detected (source=%s): expected ~%d routes "
                "but only %d calcular_rota calls happened (missing=%d, "
                "called_destinos=%s, user_msg=%r, mentioned_in_text=%d). "
                "Triggering forced retry.",
                retry_source, retry_target, called, missing,
                ctx.calcular_rota_destinations, body.message[:120], mentioned,
            )
            yield _sse("meta", {
                "session_id": session_id,
                "auto_retry": "missing_routes",
                "fonte": retry_source,
                "rotas_esperadas": retry_target,
                "rotas_chamadas": called,
                "rotas_faltantes": missing,
            })

            ctx.retry_in_progress = True
            nudge_text = _build_route_retry_message(
                expected_count=retry_target,
                called=ctx.calcular_rota_destinations,
                source=retry_source,
            )

            # Constrói novo estado: histórico atual + a resposta do LLM já
            # produzida (vira parte do contexto que ele vai reler) + a nudge.
            retry_messages = list(initial_state["messages"])
            if ctx.final_ai_msg is None:
                ctx.final_ai_msg = AIMessage(content=ctx.accumulated_text)
            retry_messages.append(ctx.final_ai_msg)
            retry_messages.append(HumanMessage(content=nudge_text))

            # Importante: zeramos accumulated_text porque o agente vai gerar
            # uma resposta nova (curta, confirmatória). Tool counter NÃO zera
            # — o limite total do turno engloba retry.
            ctx.accumulated_text = ""
            ctx.final_ai_msg = None
            retry_state = dict(initial_state)
            retry_state["messages"] = retry_messages

            try:
                async for sse_event in _run_graph(retry_state):
                    yield sse_event
            except Exception as e:
                logger.exception("Streaming agent retry error", extra={"session_id": session_id})
                yield _sse("error", {"message": f"Erro no retry de rotas: {str(e)}"})
                return

        # ── GATILHO 4 — isócrona faltante ───────────────────────────────
        # Dispara um retry quando: (a) usuário pediu busca dentro de
        # isócrona, (b) NENHUMA tool de isócrona foi chamada
        # (`buscar_dentro_de_isocrona` nem `calcular_isocrona`), e (c) ainda
        # não fizemos retry neste turno. Análogo ao Gatilho 0 das rotas.
        if (
            not ctx.retry_in_progress
            and _user_asks_for_isocrona_search(body.message)
            and ctx.buscar_em_isocrona_count == 0
            and ctx.calcular_isocrona_count == 0
        ):
            logger.warning(
                "Isocrona missing detected: user asked isócrona-based search "
                "but no isochrone tool was called (user_msg=%r). "
                "Triggering forced retry with buscar_dentro_de_isocrona nudge.",
                body.message[:160],
            )
            yield _sse("meta", {
                "session_id": session_id,
                "auto_retry": "missing_isocrona",
            })

            ctx.retry_in_progress = True
            iso_nudge = _build_isocrona_retry_message(body.message)
            iso_retry_messages = list(initial_state["messages"])
            if ctx.final_ai_msg is None:
                ctx.final_ai_msg = AIMessage(content=ctx.accumulated_text or "")
            iso_retry_messages.append(ctx.final_ai_msg)
            iso_retry_messages.append(HumanMessage(content=iso_nudge))

            ctx.accumulated_text = ""
            ctx.final_ai_msg = None
            iso_retry_state = dict(initial_state)
            iso_retry_state["messages"] = iso_retry_messages

            try:
                async for sse_event in _run_graph(iso_retry_state):
                    yield sse_event
            except Exception as e:
                logger.exception(
                    "Streaming agent isocrona-retry error",
                    extra={"session_id": session_id},
                )
                yield _sse("error", {
                    "message": f"Erro no retry de isócrona: {str(e)}",
                })
                return

        if not ctx.accumulated_text:
            ctx.accumulated_text = "Não foi possível gerar uma resposta. Tente reformular a pergunta."

        if ctx.final_ai_msg is None:
            ctx.final_ai_msg = AIMessage(content=ctx.accumulated_text)

        yield _sse("done", {
            "session_id": session_id,
            "response": ctx.accumulated_text,
            "route": ctx.route,
            "route_reasoning": ctx.route_reasoning,
            "tool_calls_count": ctx.tool_calls_count,
        })

        await _persist_turn(
            buffer, long_term, session_id, user_id,
            history, new_human, ctx.final_ai_msg, ctx.route, body.projeto_id,
        )

        logger.info(
            "Streaming turn complete",
            extra={
                "session_id": session_id,
                "route": ctx.route,
                "tool_calls": ctx.tool_calls_count,
                "response_length": len(ctx.accumulated_text),
            },
        )

    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
    )


def _truncate_obj(obj: Any, max_len: int) -> str:
    """JSON-serialize an object and truncate to max_len chars."""
    try:
        raw = json.dumps(obj, ensure_ascii=False, default=str)
    except TypeError:
        raw = str(obj)
    return raw[:max_len] + ("…" if len(raw) > max_len else "")


# ── SSE stream helpers ────────────────────────────────────────────────────────


class _StreamContext:
    """Mutable accumulator for SSE streaming state."""

    __slots__ = (
        "session_id", "analise_id", "accumulated_text", "route",
        "route_reasoning", "tool_calls_count", "final_ai_msg",
        "pending_sse",
        # Auto-enrichment de pins: quando jazidas__detalhes_processo roda,
        # guardamos os campos relevantes (substância, área, fase, …) keyed
        # por (lat, lon) arredondados. Se o LLM em seguida chamar
        # geo__plotar_endereco para essas coordenadas SEM passar `detalhes`,
        # o backend injeta automaticamente — assim o popup do pin no mapa
        # nunca fica vazio mesmo se o LLM "esquecer" o parâmetro.
        "processo_enrichment_cache",
        # Detecção de comparação de rotas com `calcular_rota` faltante:
        # contamos quantas chamadas reais à tool aconteceram E os destinos
        # invocados (resolvidos/textuais). Se o texto final do LLM mencionar
        # MAIS rotas (Distância:/Tempo:) do que efetivamente foram calculadas,
        # disparamos um RETRY automático — a polilinha só é desenhada para
        # rotas que vêm de uma chamada real da tool, não do texto.
        "calcular_rota_count",
        "calcular_rota_destinations",
        "retry_in_progress",
        # Detecção de "isócrona faltante": se o usuário pediu busca DENTRO
        # de uma isócrona mas o LLM não chamou nem buscar_dentro_de_isocrona
        # nem calcular_isocrona, disparamos retry com nudge explícito
        # (Gatilho 4, análogo aos Gatilhos 0/1/2 das rotas).
        "buscar_em_isocrona_count",
        "calcular_isocrona_count",
    )

    def __init__(self, session_id: str, analise_id: str | None = None) -> None:
        self.session_id = session_id
        self.analise_id = analise_id
        self.accumulated_text = ""
        self.route = ""
        self.route_reasoning = ""
        self.tool_calls_count = 0
        self.final_ai_msg: AIMessage | None = None
        self.pending_sse: list[dict[str, str]] = []
        self.processo_enrichment_cache: dict[
            tuple[float, float], dict[str, Any]
        ] = {}
        self.calcular_rota_count: int = 0
        self.calcular_rota_destinations: list[str] = []
        self.retry_in_progress: bool = False
        self.buscar_em_isocrona_count: int = 0
        self.calcular_isocrona_count: int = 0


def _sse(event_type: str, payload: dict[str, Any]) -> dict[str, str]:
    """Build a single SSE frame dict for EventSourceResponse."""
    return {
        "event": event_type,
        "data": json.dumps(payload, ensure_ascii=False),
    }


def _normalize_ai_text_content(content: Any) -> str:
    """Flatten AIMessage.content for streaming / chain_end (str or content blocks)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif hasattr(block, "type") and getattr(block, "type", None) == "text":
                parts.append(str(getattr(block, "text", "") or ""))
        return "".join(parts)
    return str(content)


def _process_stream_event(
    event: dict[str, Any], ctx: _StreamContext,
) -> dict[str, str] | None:
    """
    Map a single LangGraph astream_events item to an SSE frame.

    Returns None for events we don't need to forward.
    """
    kind = event.get("event", "")
    handler = _STREAM_HANDLERS.get(kind)
    if handler is None:
        return None
    return handler(event, ctx)


def _handle_chain_end(event: dict[str, Any], ctx: _StreamContext) -> dict[str, str] | None:
    name = event.get("name", "")
    data = event.get("data", {})
    output = data.get("output", {})

    if name == "router":
        ctx.route = output.get("route", "")
        ctx.route_reasoning = output.get("route_reasoning", "")
        return _sse("meta", {
            "session_id": ctx.session_id,
            "route": ctx.route,
            "route_reasoning": ctx.route_reasoning,
        })

    if name == "agent":
        for msg in reversed(output.get("messages", [])):
            if isinstance(msg, AIMessage):
                flat = _normalize_ai_text_content(msg.content).strip()
                ctx.final_ai_msg = msg
                # Streaming pode perder tokens (buffers/proxy). O AIMessage final
                # do nó agente costuma ter o texto completo — sincroniza para o
                # payload ``done`` não ficar cortado no meio.
                if flat:
                    cur = ctx.accumulated_text.strip()
                    if not cur:
                        ctx.accumulated_text = flat
                    elif len(flat) > len(cur):
                        ctx.accumulated_text = flat
                break

    if name == "post_tool" and ctx.analise_id:
        ctx.pending_sse.append(_sse("analise_updated", {
            "analise_id": ctx.analise_id,
        }))

    return None


def _handle_chat_model_stream(event: dict[str, Any], ctx: _StreamContext) -> dict[str, str] | None:
    node = event.get("metadata", {}).get("langgraph_node", "")
    if node != "agent":
        return None
    chunk = event.get("data", {}).get("chunk")
    if chunk and hasattr(chunk, "content"):
        piece = _normalize_ai_text_content(chunk.content)
        if piece:
            ctx.accumulated_text += piece
            return _sse("token", {"text": piece})
    return None


def _clean_tool_name(raw: str) -> str:
    """Strip MCP server prefix (e.g. 'jazidas__buscar_jazidas' → 'buscar_jazidas')."""
    return raw.split("__", 1)[-1] if "__" in raw else raw


def _handle_tool_start(event: dict[str, Any], ctx: _StreamContext) -> dict[str, str] | None:
    ctx.tool_calls_count += 1

    raw_name = event.get("name", "")
    clean_name = _clean_tool_name(raw_name)
    tool_input = event.get("data", {}).get("input", {}) or {}

    # Tracking específico de calcular_rota para detecção de hallucination.
    # Capturamos o destino (texto OU coordenadas) — se o LLM listar 5 rotas
    # no texto final mas só chamar a tool 2 vezes, o retry sabe que precisa
    # forçar mais 3 chamadas.
    if clean_name == "calcular_rota":
        ctx.calcular_rota_count += 1
        destino_label = (
            tool_input.get("destino_endereco")
            or tool_input.get("destino")
            or (
                f"{tool_input.get('destino_lat')},{tool_input.get('destino_lon')}"
                if tool_input.get("destino_lat") is not None
                and tool_input.get("destino_lon") is not None
                else "(destino sem rótulo)"
            )
        )
        ctx.calcular_rota_destinations.append(str(destino_label))

    # Tracking para Gatilho 4 (isócrona faltante): qualquer caminho que
    # produz/usa polígono de isócrona conta como "atendido". Se o usuário
    # pediu isócrona e nenhuma destas foi chamada, disparamos retry.
    if clean_name == "buscar_dentro_de_isocrona":
        ctx.buscar_em_isocrona_count += 1
    if clean_name == "calcular_isocrona":
        ctx.calcular_isocrona_count += 1

    return _sse("tool_start", {
        "name": clean_name,
        "call_id": event.get("run_id", ""),
        "arguments": _truncate_obj(tool_input, 300),
    })


def _parse_tool_content(output: Any) -> dict | None:
    """
    Parse a LangChain ToolMessage output into a Python dict.

    Handles formats produced by the MCP adapter:
    1. output.content is a JSON string
    2. output.content is a list of MCP blocks: [{"type": "text", "text": "..."}]
       Blocks can be dicts OR TextContent objects with .type/.text attributes.
    3. output.content is already a dict
    """
    raw = output.content if hasattr(output, "content") else output

    logger.info("_parse_tool_content: input type=%s", type(raw).__name__)

    if isinstance(raw, list):
        text_found = False
        for block in raw:
            if isinstance(block, dict) and block.get("type") == "text":
                raw = block.get("text", "")
                text_found = True
                break
            if hasattr(block, "type") and getattr(block, "type", None) == "text":
                raw = getattr(block, "text", "")
                text_found = True
                break
        if not text_found:
            logger.info("_parse_tool_content: FAIL no text block in list of %d items (types: %s)",
                        len(raw) if isinstance(raw, list) else 0,
                        [type(b).__name__ for b in (raw if isinstance(raw, list) else [])[:3]])
            return None

    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        logger.info("_parse_tool_content: FAIL JSON decode, raw type=%s, preview=%.300s",
                    type(raw).__name__, str(raw)[:300])
        return None

    if not isinstance(data, dict):
        logger.info("_parse_tool_content: FAIL parsed type=%s (expected dict)", type(data).__name__)
        return None

    logger.info("_parse_tool_content: OK keys=%s", list(data.keys())[:10])
    return data


def _filter_and_enrich_pontos(
    pontos: list, dados: list, point_type: str
) -> list:
    """
    Validate and pass through pontos that already come pre-filtered and
    pre-enriched from the tool (built directly from page_items).

    Since mcp_servers now build mapa.pontos from page_items (not from all
    200 hits), the pontos already represent exactly the current page and
    carry all enriched fields. This function just validates lat/lon presence.
    """
    valid = [p for p in pontos if p.get("lat") and p.get("lon")]
    logger.info(
        "filter_pontos[%s]: %d/%d valid (lat+lon present)",
        point_type, len(valid), len(pontos),
    )
    return valid


def _normalize_dados_list(raw: Any) -> list:
    """
    ``dados`` pode ser lista (página de jazidas/empresas) ou dict com chaves
    típicas de payload aninhado (ex.: afloramentos, ocorrências).
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in (
            "afloramentos",
            "ocorrencias",
            "resultados",
            "dados",
            "areas",
            "fornecedores",
        ):
            v = raw.get(key)
            if isinstance(v, list):
                return v
    return []


def _infer_point_type(pontos: list, dados: list) -> str:
    """
    Infer whether points are empresa or jazida from actual field presence.

    empresa pontos have ``cnpj_basico``; jazida/fornecedor pontos have ``processo``.
    Pontos CPRM de afloramento usam ``tipo`` == ``afloramento`` (sem processo ANM).
    """
    sample = pontos[0] if pontos else {}
    if sample.get("cnpj_basico"):
        return "empresa"
    if sample.get("tipo") in ("afloramento", "geoquimica", "ocorrencia_mineral"):
        return "jazida"
    if sample.get("processo"):
        return "jazida"
    if sample.get("id") and not sample.get("cnpj_basico"):
        return "jazida"
    # Fall back to dados shape
    if dados:
        dsample = dados[0]
        if dsample.get("cnpj_basico"):
            return "empresa"
        if dsample.get("ds_processo") or dsample.get("processo"):
            return "jazida"
    return "empresa"


def _extract_mapa_pontos(output: Any, tool_name: str) -> tuple[str, list] | None:
    """
    Extract mapa.pontos from a tool result, filtered to the current page
    and enriched with full dado fields.

    Returns (tipo, pontos) or None.
    """
    data = _parse_tool_content(output)
    if data is None:
        logger.info("map_extract[%s]: FAIL _parse_tool_content returned None", tool_name)
        return None

    mapa = data.get("mapa")
    if not isinstance(mapa, dict):
        wrapped = data.get("dados")
        if isinstance(wrapped, dict):
            inner = wrapped.get("mapa")
            if isinstance(inner, dict):
                mapa = inner
    if not isinstance(mapa, dict):
        logger.info("map_extract[%s]: FAIL no mapa dict (keys=%s)", tool_name, list(data.keys())[:10])
        return None

    pontos = mapa.get("pontos")
    if not isinstance(pontos, list) or not pontos:
        logger.info("map_extract[%s]: FAIL no pontos in mapa (type=%s)", tool_name, type(pontos).__name__)
        return None

    dados = _normalize_dados_list(data.get("dados"))
    dados_resultados = data.get("resultados", [])
    if not dados and dados_resultados:
        logger.info("map_extract[%s]: 'dados' empty, using 'resultados' (%d items)", tool_name, len(dados_resultados))
        dados = _normalize_dados_list(dados_resultados)

    logger.info("map_extract[%s]: raw_pontos=%d, dados=%d", tool_name, len(pontos), len(dados))
    point_type = _infer_point_type(pontos, dados)

    pontos = _filter_and_enrich_pontos(pontos, dados, point_type)
    logger.info("map_extract[%s]: type=%s filtered=%d", tool_name, point_type, len(pontos))

    if not pontos:
        logger.info("map_extract[%s]: FAIL filtered pontos is empty", tool_name)
        return None

    return point_type, pontos


def _build_route_payload_from_data(data: dict) -> dict | None:
    """
    Converte um dict de rota (do MCP geo, formato calcular_rota) para o
    payload do evento route_data que o frontend consome.

    Compartilhado entre _extract_route_data (1 rota) e _extract_routes_data
    (N rotas via comparar_rotas).
    """
    polyline = data.get("polyline")
    if not isinstance(polyline, list) or not polyline:
        return None
    label = data.get("resumo") or data.get("label") or "Rota"
    return {
        "id": str(uuid.uuid4()),
        "label": label,
        "origin": data.get("origem", {}),
        "destination": data.get("destino", {}),
        "points": polyline,
        "distance_km": data.get("distancia_km", 0),
        "duration_min": data.get("duracao_min", 0),
        "traffic_delay_min": data.get("atraso_trafego_min", 0),
        "travel_mode": data.get("modo", "truck"),
        "gap_origin_km": data.get("gap_origem_km", 0),
        "gap_destination_km": data.get("gap_destino_km", 0),
        "partial_access": bool(data.get("acesso_apenas_parcial", False)),
        "snap_origin": data.get("snap_origem"),
        "snap_destination": data.get("snap_destino"),
    }


def _extract_routes_data(output: Any, tool_name: str) -> list[dict] | None:
    """
    Extract N route polylines from a comparar_rotas tool result.

    Output shape (do MCP geo.comparar_rotas):
        {
          "sucesso": true,
          "rotas": [
            {"sucesso": true, "label": "Aratu", "polyline": [...], …},
            {"sucesso": true, "label": "Salvador", "polyline": [...], …},
            …
          ],
        }

    Retorna lista de payloads no formato do evento route_data, um por rota
    bem-sucedida. Rotas que falharam (sucesso=false ou sem polyline) são
    silenciosamente puladas — o resumo textual continua disponível ao LLM.
    """
    if tool_name != "comparar_rotas":
        return None
    data = _parse_tool_content(output)
    if data is None:
        return None
    rotas = data.get("rotas")
    if not isinstance(rotas, list):
        return None
    payloads: list[dict] = []
    for rota in rotas:
        if not isinstance(rota, dict) or not rota.get("sucesso"):
            continue
        p = _build_route_payload_from_data(rota)
        if p is not None:
            # Sobrepõe label se a rota tiver um label customizado (ex.: "Aratu")
            if rota.get("label"):
                p["label"] = rota["label"]
            payloads.append(p)
    return payloads if payloads else None


def _extract_route_data(output: Any, tool_name: str) -> dict | None:
    """
    Extract route polyline from a calcular_rota tool result.

    Expected tool output shape:
        {
          "distancia_km": float,
          "duracao_min": float,
          "atraso_trafego_min": float,
          "modo": "truck" | "car",
          "resumo": str,
          "polyline": [{"lat": float, "lon": float}, ...],
          "origem": {"lat": float, "lon": float},
          "destino": {"lat": float, "lon": float},
          # Optional gap fields injected by mcp_servers.geo.tools.calcular_rota
          "gap_origem_km": float,
          "gap_destino_km": float,
          "acesso_apenas_parcial": bool,
          "snap_origem":  {"lat": float, "lon": float} | None,
          "snap_destino": {"lat": float, "lon": float} | None,
        }

    Returns a RouteLine-compatible dict or None if not a route result.
    """
    if tool_name != "calcular_rota":
        return None
    data = _parse_tool_content(output)
    if data is None:
        return None
    payload = _build_route_payload_from_data(data)
    if payload is None:
        logger.info("route_extract[%s]: no polyline", tool_name)
    return payload


def _extract_isochrone_data(output: Any, tool_name: str) -> dict | None:
    """
    Extract GeoJSON feature from a calcular_isocrona tool result.

    Expected tool output shape:
        {
          "centro": {"lat": float, "lon": float},
          "criterio": "tempo" | "distancia",
          "valor": float,
          "modo": "truck" | "car",
          "feature": { GeoJSON Feature (Polygon) },
        }

    Returns the GeoJSON Feature dict or None.
    """
    if tool_name != "calcular_isocrona":
        return None
    data = _parse_tool_content(output)
    if data is None:
        return None
    feature = data.get("feature")
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        logger.info("isochrone_extract[%s]: no valid feature", tool_name)
        return None
    return feature


def _extract_pin_data(output: Any, tool_name: str) -> dict | None:
    """
    Extract address pin from a plotar_endereco tool result.

    Expected tool output shape:
        {
          "sucesso": true,
          "pin": {
            "lat": float,
            "lon": float,
            "label": str,
            "endereco_consultado": str | None,
            "endereco_resolvido": str | None,
            "fonte": "coordenadas" | "geocodificado",
            "detalhes": dict[str, Any] | None,  # opcional — enriquecimento
          },
          "mensagem": str,
        }

    Returns an AddressPin-compatible dict (with id) or None.
    """
    if tool_name != "plotar_endereco":
        return None
    data = _parse_tool_content(output)
    if data is None:
        return None
    pin = data.get("pin")
    if not isinstance(pin, dict):
        logger.info("pin_extract[%s]: no pin field", tool_name)
        return None
    lat = pin.get("lat")
    lon = pin.get("lon")
    if lat is None or lon is None:
        logger.info("pin_extract[%s]: pin missing lat/lon", tool_name)
        return None
    payload: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "lat": float(lat),
        "lon": float(lon),
        "label": pin.get("label") or f"{lat:.5f}, {lon:.5f}",
        "endereco_consultado": pin.get("endereco_consultado"),
        "endereco_resolvido": pin.get("endereco_resolvido"),
        "fonte": pin.get("fonte"),
    }
    detalhes = pin.get("detalhes")
    if isinstance(detalhes, dict) and detalhes:
        payload["detalhes"] = detalhes
    return payload


# ── Detecção de hallucination em comparação de rotas ─────────────────────
#
# Cenário 1 (texto enumerativo): Usuário pede "compare rotas para A, B, C, D, E".
# LLM lista 5 entradas com "Distância: X km" no texto, mas só chamou
# geo__calcular_rota M < 5 vezes. Pegamos via _count_routes_mentioned_in_text.
#
# Cenário 2 (texto vago): Usuário pede "calcule as principais rotas até o porto".
# LLM faz 2 chamadas e responde "as rotas estão visíveis no mapa" — sem
# enumerar. _count_routes_mentioned_in_text não detecta. Precisamos olhar a
# INTENÇÃO da mensagem do usuário (palavras "principais"/"todas"/"compare" +
# lista explícita de destinos OU contagem numérica).
#
# A combinação dos dois sinais cobre ambos os casos.

# ── Cenário 1: contar rotas no texto final ──────────────────────────────
_ROUTE_TEXT_DIST_RE = re.compile(r"\bdist[âa]ncia\s*:?\s*~?\s*\d", re.IGNORECASE)
_ROUTE_TEXT_TIME_RE = re.compile(r"\btempo\s*(?:estimado|de viagem)?\s*:?\s*~?\s*\d", re.IGNORECASE)
_ROUTE_TABLE_ROW_RE = re.compile(r"^\s*\|[^\n|]*\|[^\n|]*\|", re.MULTILINE)
_ROUTE_TABLE_SEP_RE = re.compile(r"^\s*\|\s*-{2,}", re.MULTILINE)


def _count_routes_mentioned_in_text(text: str) -> int:
    """
    Heurística para contar quantas rotas o texto final do LLM lista.

    Toma o MAIOR de três sinais (cada um tende a marcar 1× por rota):
      • ocorrências de "Distância: X km"
      • ocorrências de "Tempo: Y h"
      • linhas de tabela markdown (excluindo cabeçalho/separador)

    Retorna 0 se não detectarmos padrão de comparação estruturada.
    """
    if not text:
        return 0
    dist_count = len(_ROUTE_TEXT_DIST_RE.findall(text))
    time_count = len(_ROUTE_TEXT_TIME_RE.findall(text))
    table_rows = len(_ROUTE_TABLE_ROW_RE.findall(text))
    table_seps = len(_ROUTE_TABLE_SEP_RE.findall(text))
    table_data_rows = max(0, table_rows - 1 - table_seps)  # tira header + ----
    return max(dist_count, time_count, table_data_rows)


# ── Cenário 3: usuário pede uma ÚNICA rota (basta "rota X até Y") ────────
# Sinal mais fraco que o de comparação — só serve para detectar hallucination
# severa quando o LLM responde com "X km" sem ter chamado tool nenhuma.
_SINGLE_ROUTE_INTENT_RE = re.compile(
    r"\b("
    r"rota\s+(?:da|de|do|entre|a\s+partir)|"
    r"trajeto\s+(?:de|da|do|entre)|"
    r"dist[âa]ncia\s+(?:da|de|do|entre|at[éae]\s|do\s)|"
    r"quantos?\s+km|"
    r"quanto\s+tempo\s+(?:de|leva|para)|"
    r"caminho\s+(?:de|da|do)|"
    r"como\s+chegar|"
    r"calcule?\s+(?:a\s+)?rota|"
    r"plot(?:e|ar)\s+(?:a\s+)?rota"
    r")\b",
    re.IGNORECASE,
)

# Resposta com número de quilometragem ou duração de viagem — sinal de que
# o LLM produziu UMA rota (mesmo que vaga). Distinto de _ROUTE_TEXT_DIST_RE
# que exige a forma "Distância:" — aqui aceitamos qualquer "NNN km" ou "Xh".
_RESPONSE_KM_RE = re.compile(r"\b\d{1,5}(?:[\.,]\d+)?\s*(?:km|quil[ôo]metros?)\b", re.IGNORECASE)
_RESPONSE_HOURS_RE = re.compile(r"\b\d{1,3}\s*h\d{0,2}|\b\d{1,3}\s*horas?\s*(?:e\s*\d+\s*min)?", re.IGNORECASE)


def _user_asks_for_route(user_msg: str) -> bool:
    """
    Versão fraca de _expected_routes_from_user_message — detecta se o
    usuário menciona QUALQUER rota (incluindo única). Usada apenas pelo
    gatilho de "0 tool calls" para flagrar hallucination.
    """
    if not user_msg:
        return False
    return bool(_SINGLE_ROUTE_INTENT_RE.search(user_msg))


# ── Detector de intenção "dentro de isócrona" (Gatilho 4) ────────────────
# Casa frases típicas:
#   "isócrona de 60 minutos", "isocrona de 1 hora",
#   "dentro de 60 min de caminhão", "em até 90 minutos",
#   "alcance de 1h30", "região alcançável",
#   "dentro do polígono", "no polígono violeta",
#   "área de até 50 km", "raio acessível em 60 min" (ambíguo, mas inclui).
# A ideia é ser GENEROSO — falso positivo aqui só dispara um retry; o pior
# caso é que o LLM já tinha respondido bem e o retry produz a mesma
# resposta. Falso negativo é mais grave (LLM não chama isócrona, usuário
# vê dados fora da área).
_ISOCRONA_INTENT_RE = re.compile(
    r"(?:"
    r"\bis[oó]crona\b|"
    r"\b(?:dentro\s+(?:de|da|do)|em\s+at[ée]?|alcance(?:\s+de)?|"
    r"alcan[çc][aá]vel|abrang[eê]ncia|raio\s+(?:de\s+)?at[ée]?)\s+"
    r"\d+\s*(?:min(?:utos?)?|h(?:oras?)?|hr|m(?:in)?\b|km|quil[oó]met"
    r"r(?:os?|os)?)|"
    r"\b(?:pol[ií]gono|\u00e1rea\s+alcan[çc][aá]vel|regi[ãa]o\s+"
    r"alcan[çc][aá]vel)\b|"
    r"\b\d+\s*min(?:utos?)?\s+de\s+(?:caminh[ãa]o|carro|via\s+terrestre)|"
    r"\b\d+\s*h(?:oras?)?\s+de\s+(?:caminh[ãa]o|carro)|"
    r"\bem\s+at[ée]\s+\d+\s*(?:min|h|km)"
    r")",
    re.IGNORECASE,
)


def _user_asks_for_isocrona_search(user_msg: str) -> bool:
    """True se a mensagem do usuário menciona busca dentro de isócrona."""
    if not user_msg:
        return False
    return bool(_ISOCRONA_INTENT_RE.search(user_msg))


def _build_isocrona_retry_message(user_msg: str) -> str:
    """
    Nudge para forçar o LLM a usar geo__buscar_dentro_de_isocrona quando
    ele esqueceu de chamar qualquer ferramenta de isócrona.
    """
    return (
        "Sua resposta anterior não chamou nenhuma ferramenta de isócrona, "
        "mas o usuário pediu busca DENTRO de uma área alcançável. "
        "Repita a operação chamando OBRIGATORIAMENTE "
        "`geo__buscar_dentro_de_isocrona` em uma única chamada — essa tool "
        "calcula a isócrona E lista as entidades dentro do polígono em "
        "paralelo. Use as coordenadas da OBRA (vêm do contexto) como "
        "latitude/longitude. Passe `criterio` e `valor` conforme o que o "
        "usuário pediu (tempo em minutos OU distância em km). Para o filtro "
        "de busca: `substancia` para jazidas/minerais brutos, `termo_busca` "
        "para empresas/produtos industrializados, ambos para consulta híbrida. "
        f"Pergunta original do usuário: \"{user_msg.strip()[:280]}\""
    )


def _response_mentions_route_metric(text: str) -> bool:
    """True se a resposta tem 'X km' OU 'Yh' — indício de rota numérica."""
    if not text:
        return False
    return bool(_RESPONSE_KM_RE.search(text) or _RESPONSE_HOURS_RE.search(text))


# ── Cenário 2: detectar intenção de comparação na mensagem do usuário ────
# Palavras que sinalizam pedido de comparação/listagem múltipla de rotas.
# Não basta "rota" sozinho — exige modificador "principais"/"todas"/"compar*".
_COMPARISON_INTENT_RE = re.compile(
    r"\b("
    r"compar(?:e|ar|aç(?:ão|oes?)|ativo|ando)|"
    r"principa(?:is|l)\s+(?:rotas?|destinos?|portos?|trajetos?|cidades?)|"
    r"todas?\s+as\s+rotas?|"
    r"todos\s+os\s+(?:portos?|destinos?|trajetos?)|"
    r"melhor(?:es)?\s+(?:rotas?|trajetos?)|"
    r"ranking\s+(?:de\s+)?(?:rotas?|destinos?|portos?)|"
    r"calcul(?:e|ar|o)\s+(?:as\s+|os\s+)?(?:rotas?|trajetos?|distâncias?)|"
    r"trajeto[s]?\s+(?:para|at[éaé])\s+(?:os\s+|as\s+)?\w+\s*,"
    r")\b",
    re.IGNORECASE,
)

# Lista explícita do tipo "A, B, C[, D][ e E]". Exigimos ≥2 vírgulas
# (i.e. 3 itens com vírgulas + opcionalmente um item final após "e/ou").
# Cada item: nome próprio começando com maiúscula, podendo conter
# preposições internas ("Porto de Aratu").
_NAME_TOKEN = (
    r"[A-ZÁÂÃÀÉÊÍÓÔÕÚÇ][\wÁÂÃÀÉÊÍÓÔÕÚÇáâãàéêíóôõúç\.\-/]+"
    r"(?:\s+(?:de|do|da|dos|das)\s+"
    r"[A-ZÁÂÃÀÉÊÍÓÔÕÚÇ][\wÁÂÃÀÉÊÍÓÔÕÚÇáâãàéêíóôõúç\.\-/]+)*"
)
_DESTINATION_LIST_RE = re.compile(
    rf"(?:{_NAME_TOKEN}\s*,\s*){{2,}}"      # "A, B, " (≥2 prefixos)
    rf"{_NAME_TOKEN}"                        # último item antes de "e"
    rf"(?:\s+(?:e|ou)\s+{_NAME_TOKEN})?",   # opcional "e Item" final
)

# Contagem numérica explícita: "as 5 principais rotas" / "10 portos mais próximos"
_NUMERIC_HINT_RE = re.compile(
    r"\b(\d{1,2})\s+(?:principa(?:is|l)|primeir[oa]s?|melhores|"
    r"portos?|destinos?|cidades?|rotas?|trajetos?|fornecedores?)",
    re.IGNORECASE,
)


def _count_destinations_in_user_list(text: str) -> int:
    """
    Conta itens em uma lista explícita do tipo "A, B, C[, D][ e E]".

    Retorna 0 se não houver lista detectável (≥3 itens).
    """
    if not text:
        return 0
    match = _DESTINATION_LIST_RE.search(text)
    if not match:
        return 0
    chunk = match.group(0)
    # Splita por vírgula OU " e Item " OU " ou Item " (lookahead exige
    # capital pra não cortar "de/do/da" internos a "Porto de Aratu").
    items = re.split(
        r",|\s+e\s+(?=[A-ZÁÂÃÀÉÊÍÓÔÕÚÇ])|\s+ou\s+(?=[A-ZÁÂÃÀÉÊÍÓÔÕÚÇ])",
        chunk,
    )
    items = [it.strip() for it in items if it.strip() and len(it.strip()) > 1]
    return len(items) if len(items) >= 3 else 0


def _expected_routes_from_user_message(user_msg: str) -> tuple[int, str]:
    """
    Estima quantas chamadas a calcular_rota o usuário espera neste turno.

    Retorna ``(count, confianca)`` onde:
      • count: número estimado de rotas pedidas (0 = sem pedido)
      • confianca: ``"explicita"`` quando o sinal é lista nomeada ou número,
        ``"generica"`` quando só detectamos intenção sem detalhe (ex.:
        "principais rotas"). O ``chat.py`` usa essa flag pra calibrar o
        gatilho do retry — sinal explícito autoriza retry com gap=1, sinal
        genérico exige gap≥2 pra evitar falso positivo.

    Sinais, em ordem de precedência:
      1. Lista explícita "A, B, C, D" → confianca = "explicita"
      2. Contagem numérica "5 principais rotas" → confianca = "explicita"
      3. Intenção sem detalhe ("principais"/"compare"/"todas") → confianca
         = "generica", count = 3 (mínimo razoável)
    """
    if not user_msg:
        return 0, "nenhuma"

    list_count = _count_destinations_in_user_list(user_msg)
    if list_count >= 2:
        return list_count, "explicita"

    has_intent = bool(_COMPARISON_INTENT_RE.search(user_msg))
    if not has_intent:
        return 0, "nenhuma"

    numeric_match = _NUMERIC_HINT_RE.search(user_msg)
    if numeric_match:
        try:
            n = int(numeric_match.group(1))
            if 2 <= n <= 30:  # range razoável
                return n, "explicita"
        except ValueError:
            pass

    return 3, "generica"


def _build_route_retry_message(
    expected_count: int,
    called: list[str],
    source: str,
) -> str:
    """
    Mensagem human-style que injeta no histórico para forçar o agente a
    refazer a comparação completa chamando geo__calcular_rota para cada
    destino que ficou faltando.

    Args:
        expected_count: total de rotas que esperávamos (texto OU intenção).
        called: lista dos destinos JÁ invocados neste turno.
        source: "texto" ou "pedido_usuario" — só pra log/explicação.
    """
    called_str = ", ".join(called) if called else "nenhum"
    missing = max(0, expected_count - len(called))
    if source == "rota_fantasma":
        diagnosis = (
            "ALERTA: você produziu uma resposta com distância em km e/ou "
            "tempo de viagem MAS não chamou nenhuma tool de rota neste turno "
            "(geo__calcular_rota nem geo__comparar_rotas). Os números que "
            "você escreveu vieram do histórico da conversa ou foram "
            "inventados — não são uma rota REAL recém-calculada e não há "
            "polilinha no mapa. ISSO ESTÁ ERRADO."
        )
    elif source.startswith("pedido_usuario"):
        diagnosis = (
            f"O usuário pediu pelo menos {expected_count} rotas neste turno, "
            f"mas você só chamou geo__calcular_rota {len(called)} vez(es) "
            f"(destinos chamados: {called_str}). Faltam {missing} chamada(s) "
            "reais — o usuário não consegue ver no mapa as rotas que você "
            "não calculou via tool."
        )
    else:
        diagnosis = (
            f"No turno anterior você listou {expected_count} rotas no texto "
            f"MAS só chamou geo__calcular_rota {len(called)} vez(es) "
            f"(destinos chamados: {called_str}). Faltam {missing} chamada(s) "
            "reais."
        )
    if source == "rota_fantasma":
        action = (
            "AÇÃO OBRIGATÓRIA AGORA:\n"
            "1. Releia a pergunta do usuário e identifique a ORIGEM e os "
            "DESTINOS (porto/cidade/endereço/jazida).\n"
            "2. Se a origem for uma jazida pelo número de processo ANM, "
            "chame jazidas__detalhes_processo PRIMEIRO para pegar "
            "processo.localizacao.lat/lon — NÃO use coordenadas da obra.\n"
            "3. Para 1 destino, chame geo__calcular_rota; para 2+ destinos, "
            "chame geo__comparar_rotas (1 chamada batch). NÃO repita os "
            "números do turno anterior — eles podem estar errados (origem "
            "errada, dados desatualizados, ou inventados pelo histórico).\n"
            "4. Após a tool retornar, produza UMA frase curta com a nova "
            "distância e tempo, citando origem.endereco_resolvido e "
            "destino.endereco_resolvido para auditoria."
        )
    else:
        action = (
            "AÇÃO OBRIGATÓRIA AGORA:\n"
            "1. Releia a mensagem original do usuário e identifique TODOS os "
            "destinos pedidos (porto/cidade/jazida/empresa).\n"
            "2. Para cada destino que NÃO está na lista de já chamados, faça "
            "uma chamada paralela a geo__calcular_rota com a MESMA origem do "
            "turno anterior (use destino_endereco para portos/cidades). "
            "Para 2+ destinos faltantes, prefira geo__comparar_rotas.\n"
            "3. Quando todas terminarem, NÃO repita a tabela inteira — só "
            "produza uma frase curta confirmando que as rotas faltantes foram "
            "desenhadas no mapa."
        )
    return (
        "[NUDGE INTERNO DO BACKEND — não responda em texto, só execute "
        "ferramentas]\n\n"
        f"{diagnosis}\n\n"
        "REGRA TÉCNICA: o frontend só desenha no mapa rotas que vêm de "
        "uma chamada efetiva da tool — distâncias/tempos que você cita no "
        "texto sem ter chamado a tool NÃO viram polilinha. O usuário lê "
        "os números, mas não vê a rota no mapa.\n\n"
        f"{action}"
    )


_GEO_STRIP_KEYS = {"geometrias_jazidas", "geometrias_municipios"}

# Heavy payloads from geo tools that the LLM should never see — they explode
# the context window without adding any value. Already delivered to the
# frontend via dedicated SSE events (route_data, isochrone_data).
# snap_origem / snap_destino are rendering hints (used pelo MapContainer para
# desenhar a linha tracejada) — o LLM só precisa do gap_*_km já em texto.
_ROUTE_STRIP_KEYS     = {"polyline", "snap_origem", "snap_destino"}  # calcular_rota
_ISOCHRONE_STRIP_KEYS = {"feature"}                                  # calcular_isocrona


def _strip_keys_from_tool_output(output: Any, keys: set[str], log_label: str) -> None:
    """
    Generic in-place stripper for ToolMessage content.

    Removes any key in ``keys`` from the JSON payload contained in the output,
    handling all three content shapes produced by the MCP adapter:
      - output.content as a JSON string
      - output.content as a list of MCP blocks (dicts or TextContent objects)
      - output.content already a dict
    """
    if not keys:
        return
    try:
        raw = output.content if hasattr(output, "content") else None
        if raw is None:
            return

        if isinstance(raw, str):
            data = json.loads(raw)
            if isinstance(data, dict) and keys & data.keys():
                before = len(raw)
                for k in keys:
                    data.pop(k, None)
                output.content = json.dumps(data, ensure_ascii=False)
                logger.info(
                    "Stripped %s from tool output: %d → %d chars",
                    log_label, before, len(output.content),
                )

        elif isinstance(raw, list):
            for block in raw:
                text: str | None = None
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                elif hasattr(block, "type") and getattr(block, "type") == "text":
                    text = getattr(block, "text", "")

                if text:
                    try:
                        data = json.loads(text)
                        if isinstance(data, dict) and keys & data.keys():
                            before = len(text)
                            for k in keys:
                                data.pop(k, None)
                            new_text = json.dumps(data, ensure_ascii=False)
                            if isinstance(block, dict):
                                block["text"] = new_text
                            else:
                                block.text = new_text  # type: ignore[attr-defined]
                            logger.info(
                                "Stripped %s from tool output (list block): %d → %d chars",
                                log_label, before, len(new_text),
                            )
                    except (json.JSONDecodeError, TypeError):
                        pass
    except Exception as exc:
        logger.warning("strip_keys_from_tool_output[%s] failed: %s", log_label, exc)


def _strip_geometry_from_tool_output(output: Any) -> None:
    """Remove heavy GeoJSON FeatureCollections (jazidas / municipios)."""
    _strip_keys_from_tool_output(output, _GEO_STRIP_KEYS, "geometry")


def _strip_nested_route_polylines(output: Any) -> None:
    """
    Stripper específico para comparar_rotas: as polilinhas estão aninhadas
    em ``rotas[*].polyline`` (lista de N rotas), não no topo do payload.
    Para uma comparação de 5 portos, isso pode ser 1.5–3 MB de pontos —
    devastador para o contexto do LLM.
    """
    try:
        raw = output.content if hasattr(output, "content") else None
        if raw is None:
            return

        def _strip_dict(d: dict) -> bool:
            """In-place strip de rotas[*].polyline / snap_*. Retorna True se mexeu."""
            if not isinstance(d, dict):
                return False
            rotas = d.get("rotas")
            if not isinstance(rotas, list):
                return False
            modified = False
            for r in rotas:
                if not isinstance(r, dict):
                    continue
                for k in _ROUTE_STRIP_KEYS:
                    if k in r:
                        r.pop(k, None)
                        modified = True
            return modified

        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return
            if _strip_dict(data):
                before = len(raw)
                output.content = json.dumps(data, ensure_ascii=False)
                logger.info(
                    "Stripped batch_route_polylines from tool output: %d → %d chars",
                    before, len(output.content),
                )

        elif isinstance(raw, list):
            for block in raw:
                text: str | None = None
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                elif hasattr(block, "type") and getattr(block, "type") == "text":
                    text = getattr(block, "text", "")
                if not text:
                    continue
                try:
                    data = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    continue
                if _strip_dict(data):
                    before = len(text)
                    new_text = json.dumps(data, ensure_ascii=False)
                    if isinstance(block, dict):
                        block["text"] = new_text
                    else:
                        block.text = new_text  # type: ignore[attr-defined]
                    logger.info(
                        "Stripped batch_route_polylines from tool output "
                        "(list block): %d → %d chars",
                        before, len(new_text),
                    )
    except Exception as exc:
        logger.warning("strip_nested_route_polylines failed: %s", exc)


def _strip_route_payload_from_tool_output(output: Any, tool_name: str) -> None:
    """
    Remove the ``polyline`` (calcular_rota) / ``feature`` (calcular_isocrona)
    / ``rotas[*].polyline`` (comparar_rotas) fields from the tool output
    sent to the LLM.

    Must be called AFTER route_data / isochrone_data have been emitted to the
    SSE stream — the frontend already has the full geometry. Stripping here
    prevents the LLM from receiving 300-600 KB (calcular_rota) or several MB
    (comparar_rotas com 5 destinos) de pontos, que tanto confundem o parsing
    dos campos resumo (distancia_km, duracao_min) quanto queimam contexto.
    """
    if tool_name == "calcular_rota":
        _strip_keys_from_tool_output(output, _ROUTE_STRIP_KEYS, "route_polyline")
    elif tool_name == "calcular_isocrona":
        _strip_keys_from_tool_output(output, _ISOCHRONE_STRIP_KEYS, "isochrone_feature")
    elif tool_name == "comparar_rotas":
        _strip_nested_route_polylines(output)
    elif tool_name == "obter_geometria_ferrovia":
        # Geometria não vai ao LLM; mapa busca GET /geo/ferrovia/geometria (refs no SSE).
        _strip_keys_from_tool_output(output, {"feature"}, "ferrovia_feature_geojson")
    elif tool_name == "obter_poligono_porto":
        _strip_keys_from_tool_output(output, {"feature"}, "porto_feature_geojson")


def _extract_context_geometry_for_map(
    output: Any, tool_name: str,
) -> dict[str, Any] | None:
    """
    Referências leves (id/código) para o mapa via SSE ``context_geometry``.

    O frontend busca GeoJSON sob demanda em GET /api/v1/geo/ferrovia/geometria
    ou GET /api/v1/geo/porto/poligono — sem geometria no LLM nem no SSE.
    """
    if tool_name not in ("obter_geometria_ferrovia", "obter_poligono_porto"):
        return None
    data = _parse_tool_content(output)
    if not isinstance(data, dict) or not data.get("sucesso"):
        return None
    if tool_name == "obter_poligono_porto" and not data.get("encontrado"):
        return None

    refs: list[dict[str, Any]] = []
    if tool_name == "obter_geometria_ferrovia":
        fid = data.get("ferrovia_id")
        if not fid:
            return None
        ref: dict[str, Any] = {"kind": "ferrovia", "ferrovia_id": str(fid)}
        lat, lon = data.get("latitude"), data.get("longitude")
        if lat is not None and lon is not None:
            ref["latitude"] = float(lat)
            ref["longitude"] = float(lon)
        refs.append(ref)
    else:
        if not data.get("codigo") and not data.get("nome"):
            return None
        ref = {"kind": "porto"}
        if data.get("codigo"):
            ref["codigo"] = str(data["codigo"])
        if data.get("nome"):
            ref["nome"] = str(data["nome"])
        if data.get("uf"):
            ref["uf"] = str(data["uf"])
        refs.append(ref)

    return {"refs": refs} if refs else None


def _extract_geometry_data(output: Any, tool_name: str) -> dict | None:
    """
    Extract geometrias_jazidas and geometrias_municipios from a tool result.

    Both FeatureCollections are stored at the top level of the tool response
    (not nested inside `mapa`, which is a list of points).

    Returns a dict with the two FeatureCollections, or None if no geometry found.
    """
    data = _parse_tool_content(output)
    if data is None:
        return None

    geo_jazidas = data.get("geometrias_jazidas")
    geo_municipios = data.get("geometrias_municipios")

    if not geo_jazidas and not geo_municipios:
        return None

    result: dict[str, Any] = {}
    if isinstance(geo_jazidas, dict) and geo_jazidas.get("features"):
        result["geometrias_jazidas"] = geo_jazidas
        logger.info(
            "geometry_extract[%s]: jazidas=%d features",
            tool_name, len(geo_jazidas["features"]),
        )
    if isinstance(geo_municipios, dict) and geo_municipios.get("features"):
        result["geometrias_municipios"] = geo_municipios
        logger.info(
            "geometry_extract[%s]: municipios=%d features",
            tool_name, len(geo_municipios["features"]),
        )

    return result if result else None


## ── Auto-enriquecimento de pins ─────────────────────────────────────────────
#
# Mesmo com a Regra 9.a do system prompt instruindo o LLM a passar `detalhes`
# em geo__plotar_endereco quando ele já chamou jazidas__detalhes_processo,
# observou-se que o GPT às vezes "esquece" o parâmetro e o popup do pin volta
# pelado (só lat/lon). Estas funções implementam uma rede de segurança no
# backend: caching por coordenada + injeção automática quando o pin chega
# sem detalhes. O LLM continua autorizado a sobrescrever via `detalhes` ─
# quando ele passa, respeitamos.

# Mapa Fase ANM crua → status legível (mesmo mapeamento usado no
# frontend em ChatShell.mapFase).
_FASE_DISPLAY: dict[str, str] = {
    "concessão de lavra": "Ativa",
    "lavra garimpeira": "Ativa",
    "requerimento de lavra": "Em análise",
    "requerimento de pesquisa": "Em análise",
    "autorização de pesquisa": "Em análise",
    "disponibilidade": "Disponível",
    "licenciamento": "Licenciada",
}


def _normalize_fase(fase_raw: Any) -> str:
    if not fase_raw:
        return ""
    f = str(fase_raw).lower()
    for key, display in _FASE_DISPLAY.items():
        if key in f:
            return display
    return str(fase_raw)


def _cache_processo_enrichment(output: Any, tool_name: str, ctx: _StreamContext) -> None:
    """
    Quando jazidas__detalhes_processo termina, extrai os campos exibíveis
    no popup (processo, substância, área, fase, município, titulares) e
    guarda em ctx.processo_enrichment_cache, keyed por (lat, lon)
    arredondados a 4 casas (≈ 11 m de tolerância).
    """
    if tool_name != "detalhes_processo":
        return
    data = _parse_tool_content(output)
    if not isinstance(data, dict):
        return
    processo = data.get("processo")
    if not isinstance(processo, dict):
        return
    loc = processo.get("localizacao")
    if not isinstance(loc, dict):
        return
    lat = loc.get("lat")
    lon = loc.get("lon")
    if lat is None or lon is None:
        return

    enrichment: dict[str, Any] = {}

    ds_processo = processo.get("ds_processo")
    if ds_processo:
        enrichment["processo"] = str(ds_processo)

    substancias = processo.get("substancias_nomes") or []
    if isinstance(substancias, list) and substancias:
        # OpenSearch devolve em CAIXA-ALTA ("MÁRMORE"); deixa em title-case
        # para casar com o resto da plataforma.
        enrichment["substancia"] = ", ".join(
            str(s).title() for s in substancias if s
        )

    area_ha = processo.get("area_ha")
    if isinstance(area_ha, (int, float)) and area_ha > 0:
        enrichment["area_ha"] = area_ha

    fase = _normalize_fase(processo.get("fase"))
    if fase:
        enrichment["fase"] = fase

    municipios = processo.get("municipios_nomes") or []
    ufs = processo.get("uf") or []
    if isinstance(municipios, list) and municipios:
        muni = str(municipios[0]).strip()
        uf = (
            str(ufs[0]).strip()
            if isinstance(ufs, list) and ufs
            else ""
        )
        if muni and uf:
            enrichment["municipio"] = f"{muni}/{uf}"
        elif muni:
            enrichment["municipio"] = muni

    titulares = processo.get("titulares_nomes") or []
    if isinstance(titulares, list) and titulares:
        clean = [str(t).strip() for t in titulares if t]
        if clean:
            enrichment["titulares"] = clean

    empresa = data.get("empresa")
    if isinstance(empresa, dict):
        cc = empresa.get("cnpj_completo")
        if cc:
            enrichment["cnpj"] = str(cc)
        contato = empresa.get("contato")
        if isinstance(contato, dict):
            tel = contato.get("telefone") or contato.get("telefone2")
            if tel:
                enrichment["telefone"] = str(tel)
            em = contato.get("email")
            if em:
                enrichment["email"] = str(em)
    titular = processo.get("titular")
    if not enrichment.get("cnpj") and isinstance(titular, dict):
        bas = titular.get("cnpj_basico")
        if bas:
            enrichment["cnpj"] = str(bas).strip()

    if not enrichment:
        return

    key = (round(float(lat), 4), round(float(lon), 4))
    ctx.processo_enrichment_cache[key] = enrichment
    logger.info(
        "processo_cache: stored enrichment for %s at (%.4f, %.4f) keys=%s",
        ds_processo, key[0], key[1], list(enrichment.keys()),
    )


def _maybe_enrich_pin(pin_result: dict[str, Any], ctx: _StreamContext) -> None:
    """
    Se o pin chegou sem `detalhes` (LLM esqueceu de passar), tenta achar
    enrichment cacheado para a coordenada e injeta. Se o pin já tem
    `detalhes`, NÃO sobrescreve — respeita o que o LLM enviou.
    """
    if pin_result.get("detalhes"):
        return
    if not ctx.processo_enrichment_cache:
        return
    try:
        key = (round(float(pin_result["lat"]), 4), round(float(pin_result["lon"]), 4))
    except (KeyError, TypeError, ValueError):
        return
    enrichment = ctx.processo_enrichment_cache.get(key)
    if not enrichment:
        return
    pin_result["detalhes"] = dict(enrichment)
    logger.info(
        "processo_cache: AUTO-ENRICHED pin at (%.4f, %.4f) with keys=%s "
        "(LLM didn't pass `detalhes`)",
        key[0], key[1], list(enrichment.keys()),
    )


def _handle_synthetic_busca_em_isocrona(
    output: Any, tool_name: str, ctx: _StreamContext,
) -> None:
    """
    Handler dedicado para a synthetic tool ``buscar_dentro_de_isocrona``.

    O tool retorna um dict consolidado com 4 sub-blocos:
        - ``isocrona``    : {feature: GeoJSON Feature, centro, criterio, …}
        - ``jazidas``     : payload de jazidas__jazidas_por_poligono (opt)
        - ``fornecedores``: payload de jazidas__fornecedores_por_poligono (opt)
        - ``empresas``    : payload de empresas__empresas_por_poligono (opt)

    Esta função extrai cada um e empilha os SSE events apropriados em
    ctx.pending_sse — exatamente como aconteceria se as 3 tools tivessem
    sido chamadas separadamente.

    Também registra a isócrona ativa no session_state para que QUALQUER
    chamada subsequente do LLM (ex.: 2ª página, refinamento) já caia nos
    guardrails de auto-fill / redirect.
    """
    data = _parse_tool_content(output)
    if data is None:
        logger.info("synthetic[%s]: FAIL _parse_tool_content returned None", tool_name)
        return

    # ── isocrona ──────────────────────────────────────────────────────
    iso_block = data.get("isocrona") or {}
    feature = iso_block.get("feature") if isinstance(iso_block, dict) else None
    if isinstance(feature, dict) and feature.get("type") == "Feature":
        ctx.pending_sse.append(_sse("isochrone_data", {"feature": feature}))
        iso_geom = feature.get("geometry")
        if isinstance(iso_geom, dict):
            set_active_isochrone_polygon(iso_geom)
            logger.info(
                "synthetic[%s]: EMITTED isochrone_data + session polygon set (%s)",
                tool_name, iso_geom.get("type"),
            )
    else:
        logger.info("synthetic[%s]: no isochrone feature in result", tool_name)

    # ── busca interna: extrair mapa.pontos + dados de cada bloco ─────
    # Reutilizamos _extract_mapa_pontos passando um pseudo-output cujo
    # `.content` é o JSON do sub-bloco. Isso garante que toda a lógica de
    # filtragem/enrichment/inferência de tipo seja idêntica ao caminho
    # padrão (1 tool por vez).
    class _PseudoOutput:
        def __init__(self, content: str):
            self.content = content

    for label, source_tool in (
        ("jazidas", "jazidas_por_poligono"),
        ("fornecedores", "buscar_fornecedores"),
        ("empresas", "buscar_empresas"),
    ):
        block = data.get(label)
        if not isinstance(block, dict) or not block.get("sucesso"):
            continue
        try:
            pseudo = _PseudoOutput(json.dumps(block, ensure_ascii=False))
            mapa_result = _extract_mapa_pontos(pseudo, source_tool)
            if mapa_result is not None:
                point_type, pontos = mapa_result
                logger.info(
                    "synthetic[%s.%s]: EMITTING map_data tipo=%s pontos=%d",
                    tool_name, label, point_type, len(pontos),
                )
                ctx.pending_sse.append(_sse("map_data", {
                    "tool": f"{tool_name}/{source_tool}",
                    "tipo": point_type,
                    "pontos": pontos,
                }))
        except Exception as exc:
            logger.exception(
                "synthetic[%s.%s]: map extraction failed: %s", tool_name, label, exc,
            )


def _strip_synthetic_busca_em_isocrona_payload(output: Any) -> None:
    """
    Remove do payload visto pela LLM os campos pesados:
        - isocrona.feature (já foi para o frontend via isochrone_data)
        - polylines/shapes inexistem aqui mas mantemos higiene defensiva
    Mantém os DADOS textuais (resultados, totais) que a LLM precisa pra
    redigir a resposta.
    """
    data = _parse_tool_content(output)
    if not isinstance(data, dict):
        return
    iso = data.get("isocrona")
    if isinstance(iso, dict) and "feature" in iso:
        iso.pop("feature", None)
    # Reescreve o payload original de volta no output (em ferramentas
    # LangChain o output costuma ter .content como string JSON).
    raw = json.dumps(data, ensure_ascii=False)
    if hasattr(output, "content"):
        try:
            output.content = raw  # type: ignore[attr-defined]
        except Exception:
            pass


def _handle_tool_end(event: dict[str, Any], ctx: _StreamContext) -> dict[str, str] | None:
    output = event.get("data", {}).get("output", "")
    tool_name = _clean_tool_name(event.get("name", ""))
    preview = str(output.content)[:500] if hasattr(output, "content") else str(output)[:500]

    raw_content = output.content if hasattr(output, "content") else output
    logger.info(
        "tool_end[%s]: output_type=%s, content_type=%s, content_len=%s",
        tool_name,
        type(output).__name__,
        type(raw_content).__name__,
        len(raw_content) if isinstance(raw_content, (str, list)) else "n/a",
    )

    # ── Synthetic tool: ``buscar_dentro_de_isocrona`` ──────────────────
    # Atomicidade: 1 tool call do LLM = isocrona + N buscas internas.
    # Tratamos antes dos extractors padrão e fazemos EARLY RETURN —
    # eles esperam o shape original de cada MCP tool individual e não
    # consigem desempacotar nosso wrapper consolidado.
    if tool_name == "buscar_dentro_de_isocrona":
        try:
            _handle_synthetic_busca_em_isocrona(output, tool_name, ctx)
        except Exception as exc:
            logger.exception(
                "tool_end[%s]: synthetic handler failed: %s", tool_name, exc,
            )
        try:
            _strip_synthetic_busca_em_isocrona_payload(output)
        except Exception as exc:
            logger.warning(
                "tool_end[%s]: synthetic strip failed: %s", tool_name, exc,
            )
        return _sse("tool_end", {
            "name": tool_name,
            "call_id": event.get("run_id", ""),
            "result_preview": preview,
        })

    # Cache enrichment de detalhes_processo para auto-completar o popup
    # de qualquer plotar_endereco subsequente que aponte às mesmas coords.
    try:
        _cache_processo_enrichment(output, tool_name, ctx)
    except Exception as exc:
        logger.warning("tool_end[%s]: processo enrichment cache failed: %s", tool_name, exc)

    try:
        mapa_result = _extract_mapa_pontos(output, tool_name)
        if mapa_result is not None:
            point_type, pontos = mapa_result
            logger.info(
                "tool_end[%s]: EMITTING map_data tipo=%s pontos=%d",
                tool_name, point_type, len(pontos),
            )
            ctx.pending_sse.append(_sse("map_data", {
                "tool": tool_name,
                "tipo": point_type,
                "pontos": pontos,
            }))
        else:
            logger.info("tool_end[%s]: no map_data (extract returned None)", tool_name)
    except Exception as exc:
        logger.exception("tool_end[%s]: map extraction failed: %s", tool_name, exc)

    try:
        route_result = _extract_route_data(output, tool_name)
        if route_result is not None:
            logger.info("tool_end[%s]: EMITTING route_data dist=%.1f km", tool_name, route_result.get("distance_km", 0))
            ctx.pending_sse.append(_sse("route_data", route_result))
            # Auto-pin: origem e destino da rota simples
            for endpoint_key, default_label in (("origin", "Origem"), ("destination", "Destino")):
                ep = route_result.get(endpoint_key) or {}
                ep_lat = ep.get("lat")
                ep_lon = ep.get("lon")
                if ep_lat is not None and ep_lon is not None:
                    ep_label = (
                        ep.get("endereco_resolvido")
                        or ep.get("endereco_consultado")
                        or default_label
                    )
                    pin_payload: dict = {
                        "id": str(uuid.uuid4()),
                        "lat": float(ep_lat),
                        "lon": float(ep_lon),
                        "label": ep_label,
                    }
                    if ep.get("detalhes"):
                        pin_payload["detalhes"] = ep["detalhes"]
                    ctx.pending_sse.append(_sse("pin_data", pin_payload))
    except Exception as exc:
        logger.exception("tool_end[%s]: route extraction failed: %s", tool_name, exc)

    # comparar_rotas (batch) → 1 chamada da tool, mas N polilinhas no mapa.
    # Emitimos 1 evento route_data por rota bem-sucedida E contabilizamos
    # cada polilinha em ctx.calcular_rota_count para que o trigger de retry
    # entenda "1 batch com 5 rotas" como cobertura completa de 5 destinos.
    # Também emitimos pin_data automáticos para origem (1×) e cada destino,
    # sem depender do agente chamar geo__plotar_endereco manualmente.
    try:
        routes_results = _extract_routes_data(output, tool_name)
        if routes_results is not None:
            logger.info(
                "tool_end[%s]: EMITTING %d route_data events (batch)",
                tool_name, len(routes_results),
            )
            origin_pinned = False
            seen_dest_keys: set[str] = set()
            for route_payload in routes_results:
                ctx.pending_sse.append(_sse("route_data", route_payload))
                # Espelha o tracking de calcular_rota — cada rota desenhada
                # conta como 1 destino atendido para o detector de hallucination.
                ctx.calcular_rota_count += 1
                dest_info = route_payload.get("destination") or {}
                dest_label = (
                    route_payload.get("label")
                    or dest_info.get("endereco_resolvido")
                    or dest_info.get("endereco_consultado")
                    or "(destino sem rótulo)"
                )
                ctx.calcular_rota_destinations.append(str(dest_label))

                # Auto-pin: origem (uma vez para todo o batch)
                if not origin_pinned:
                    orig_info = route_payload.get("origin") or {}
                    orig_lat = orig_info.get("lat")
                    orig_lon = orig_info.get("lon")
                    if orig_lat is not None and orig_lon is not None:
                        orig_label = (
                            orig_info.get("endereco_resolvido")
                            or orig_info.get("endereco_consultado")
                            or "Origem"
                        )
                        orig_pin: dict = {
                            "id": str(uuid.uuid4()),
                            "lat": float(orig_lat),
                            "lon": float(orig_lon),
                            "label": orig_label,
                        }
                        if orig_info.get("detalhes"):
                            orig_pin["detalhes"] = orig_info["detalhes"]
                        ctx.pending_sse.append(_sse("pin_data", orig_pin))
                        origin_pinned = True

                # Auto-pin: destino de cada rota
                dest_lat = dest_info.get("lat")
                dest_lon = dest_info.get("lon")
                if dest_lat is not None and dest_lon is not None:
                    dest_key = f"{float(dest_lat):.3f},{float(dest_lon):.3f}"
                    if dest_key not in seen_dest_keys:
                        dest_pin: dict = {
                            "id": str(uuid.uuid4()),
                            "lat": float(dest_lat),
                            "lon": float(dest_lon),
                            "label": str(dest_label),
                        }
                        if dest_info.get("detalhes"):
                            dest_pin["detalhes"] = dest_info["detalhes"]
                        ctx.pending_sse.append(_sse("pin_data", dest_pin))
                        seen_dest_keys.add(dest_key)
    except Exception as exc:
        logger.exception("tool_end[%s]: routes (batch) extraction failed: %s", tool_name, exc)

    try:
        iso_result = _extract_isochrone_data(output, tool_name)
        if iso_result is not None:
            logger.info("tool_end[%s]: EMITTING isochrone_data", tool_name)
            ctx.pending_sse.append(_sse("isochrone_data", {"feature": iso_result}))
            # Registra a geometria da isócrona no session state — assim a
            # ponte MCP (app/langgraph/tools.py) pode redirecionar chamadas
            # subsequentes de buscar_jazidas / buscar_empresas com raio para
            # suas variantes *_por_poligono, garantindo que entidades fora
            # do polígono não apareçam nos resultados (bug que motivou D).
            iso_geom = iso_result.get("geometry")
            if isinstance(iso_geom, dict):
                set_active_isochrone_polygon(iso_geom)
                logger.info(
                    "tool_end[%s]: session isochrone polygon set (%s)",
                    tool_name, iso_geom.get("type"),
                )
    except Exception as exc:
        logger.exception("tool_end[%s]: isochrone extraction failed: %s", tool_name, exc)

    try:
        pin_result = _extract_pin_data(output, tool_name)
        if pin_result is not None:
            # Auto-injeta detalhes do processo se LLM esqueceu de passar.
            _maybe_enrich_pin(pin_result, ctx)
            logger.info(
                "tool_end[%s]: EMITTING pin_data lat=%.5f lon=%.5f label='%s' "
                "detalhes_keys=%s",
                tool_name, pin_result["lat"], pin_result["lon"],
                pin_result.get("label"),
                list((pin_result.get("detalhes") or {}).keys()),
            )
            ctx.pending_sse.append(_sse("pin_data", pin_result))
    except Exception as exc:
        logger.exception("tool_end[%s]: pin extraction failed: %s", tool_name, exc)

    try:
        ctx_geom = _extract_context_geometry_for_map(output, tool_name)
        if ctx_geom is not None:
            nrefs = len(ctx_geom.get("refs") or [])
            logger.info(
                "tool_end[%s]: EMITTING context_geometry (%d ref(s), on-demand)",
                tool_name, nrefs,
            )
            ctx.pending_sse.append(_sse("context_geometry", ctx_geom))
    except Exception as exc:
        logger.exception(
            "tool_end[%s]: context_geometry extraction failed: %s",
            tool_name, exc,
        )

    # Geometry is now served on-demand via GET /api/v1/geo/jazida/{id}/poligono.
    # We still strip geometry fields from the tool response to protect the LLM
    # context window (safety net for old cache entries or direct tool calls).
    try:
        _strip_geometry_from_tool_output(output)
    except Exception as exc:
        logger.warning("tool_end[%s]: geometry strip failed: %s", tool_name, exc)

    # Strip route polyline / isochrone feature from the LLM-bound payload.
    # Heavy points already went to the frontend via route_data / isochrone_data
    # SSE events; the LLM only needs distancia_km, duracao_min, resumo, etc.
    try:
        _strip_route_payload_from_tool_output(output, tool_name)
    except Exception as exc:
        logger.warning("tool_end[%s]: route payload strip failed: %s", tool_name, exc)

    return _sse("tool_end", {
        "name": tool_name,
        "call_id": event.get("run_id", ""),
        "result_preview": preview,
    })


_STREAM_HANDLERS: dict[str, Any] = {
    "on_chain_end": _handle_chain_end,
    "on_chat_model_stream": _handle_chat_model_stream,
    "on_tool_start": _handle_tool_start,
    "on_tool_end": _handle_tool_end,
}


@router.get(
    "/memory/{user_id}",
    summary="Get long-term memory for a user (debug)",
)
async def get_user_memory(
    user_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
):
    """
    Returns the user's long-term memory (facts, fornecedores, recent sessions).
    Useful during development to inspect what the system remembers cross-session.
    """
    long_term = LongTermMemory(db)
    memory = await long_term.load_user_memory(user_id)
    sessions = await long_term.get_recent_sessions(user_id, limit=10)
    context = await long_term.build_context_block(user_id)

    return {
        "user_id": user_id,
        "memory": memory,
        "recent_sessions": sessions,
        "context_block": context,
    }


@router.post(
    "/memory/summarize",
    summary="Force summarize a session (debug)",
)
async def force_summarize(
    session_id: str,
    redis: Annotated[RedisClient, Depends(get_redis_client)],
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
    user_id: str = "dev-user",
):
    """
    Synchronously summarize a session. Useful for testing long-term memory.
    """
    buffer = RedisConversationBuffer(redis)
    messages = await buffer.load(session_id)

    if not messages:
        return {"error": "No messages found in Redis for this session", "session_id": session_id}

    long_term = LongTermMemory(db)
    try:
        doc = await long_term.summarize_and_save(
            session_id, user_id, messages,
        )
        return {"status": "ok", "session_id": session_id, "summary": doc}
    except Exception as e:
        return {"status": "error", "session_id": session_id, "detail": str(e)}


@router.get(
    "/analytics/{user_id}/tools",
    summary="Tool usage analytics for a user",
)
async def get_tool_analytics(
    user_id: str,
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
    session_id: str | None = None,
    limit: int = 20,
):
    """
    Returns which MCP tools were called most often across sessions.

    Aggregates `tool_usage` from the chat_sessions MongoDB collection.
    Filter by session_id to scope to a single conversation.

    Example response:
        [
          {"name": "search_jazidas", "total_calls": 14, "session_count": 3},
          {"name": "search_empresas", "total_calls": 6, "session_count": 2},
        ]
    """
    long_term = LongTermMemory(db)
    stats = await long_term.get_tool_usage_stats(
        user_id=user_id,
        session_id=session_id,
        limit=limit,
    )
    return {
        "user_id": user_id,
        "session_id": session_id,
        "tool_usage": stats,
        "total_unique_tools": len(stats),
    }


@router.delete(
    "/{session_id}",
    response_model=ClearSessionResponse,
    summary="Clear conversation history for a session",
)
async def clear_session(
    session_id: str,
    redis: Annotated[RedisClient, Depends(get_redis_client)],
    db: Annotated[AsyncIOMotorDatabase, Depends(get_database)],
    user_id: str = "dev-user",
):
    """
    Summarize and persist session to MongoDB, then clear Redis.
    Use this when the user explicitly starts a new conversation.
    """
    buffer = RedisConversationBuffer(redis)
    messages = await buffer.load(session_id)

    if messages:
        long_term = LongTermMemory(db)
        await long_term.summarize_and_save(session_id, user_id, messages)

    await buffer.clear(session_id)
    return ClearSessionResponse(cleared=True, session_id=session_id)


@router.get(
    "/{session_id}/history",
    summary="Get raw conversation history for a session (debug)",
)
async def get_history(
    session_id: str,
    redis: Annotated[RedisClient, Depends(get_redis_client)],
):
    """
    Returns the stored messages for a session as JSON.
    Useful during development to inspect what the agent remembers.
    """
    buffer = RedisConversationBuffer(redis)
    messages = await buffer.load(session_id)
    length = await buffer.length(session_id)
    return {
        "session_id": session_id,
        "message_count": length,
        "messages": [
            {"type": msg.type, "content": str(msg.content)[:500]}
            for msg in messages
        ],
    }
