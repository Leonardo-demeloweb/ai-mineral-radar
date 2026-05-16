"""
Agent State Definition
=======================

Defines the state schema for the LangGraph agent.

The state flows through all nodes in the graph and accumulates
messages (user, assistant, tool calls, tool results).

LangGraph uses ``Annotated[list, add_messages]`` to automatically
merge new messages into the existing list (instead of replacing).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """
    State schema for the MineralRadar AI agent.

    Attributes:
        messages: Conversation history (auto-accumulated via add_messages).
            Includes HumanMessage, AIMessage (with tool_calls), ToolMessage.
        conversation_id: Unique conversation identifier for memory/cache.
        projeto_id: Optional projeto context (filters tool results to this projeto).
        analise_id: Optional análise context.
        projeto_context_str: Pre-built projeto context block injected into the system
            prompt. Contains name, coordinates and default search radius.
        analise_context_str: Pre-built análise context block. When the análise has
            no termo_busca yet, instructs the agent to infer it from the conversation.
        route: Classified intent route (set by router node).
            One of: mineral, empresa, hybrid, geo, general.
        route_reasoning: Brief explanation of why the route was chosen.
        route_execution_hint: Plano de rota derivado da mensagem (route_planner).
        tool_calls_count: Running count of tool invocations in this turn.
        max_tool_calls: Safety limit to prevent infinite loops.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    conversation_id: str
    projeto_id: str | None
    analise_id: str | None
    projeto_context_str: str | None
    analise_context_str: str | None
    route: str
    route_reasoning: str
    route_execution_hint: str
    tool_calls_count: int
    max_tool_calls: int
