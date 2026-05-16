"""
LangGraph Orchestrator
=======================

AI agent orchestrator for MineralRadar.

Architecture:
    START → Router (classifies intent) → Agent (route-filtered tools) → Tool Executor → Agent → ... → END

    The agent uses an intent-routed ReAct loop:
    1. Router classifies user intent into a route (mineral/empresa/hybrid/geo/general)
    2. Agent receives only the tools relevant to the route (fewer tokens, better accuracy)
    3. LLM decides to call tools or respond directly
    4. Tool calls go through UnifiedMCPProvider → MCP Servers
    5. Results feed back to LLM for synthesis
    6. Loop until LLM responds without tool calls

Routes:
    - mineral:  Jazidas + Geo tools (ANM processes, mining, extraction)
    - empresa:  Empresas + Geo tools (companies by CNAE, economic activity)
    - hybrid:   All tools (mining + commercial combined)
    - geo:      Geo tools only (locations, routes, distances)
    - general:  No tools (greetings, general questions)

Components:
    - state.py:  AgentState TypedDict (conversation state + route)
    - router.py: Intent classification via structured output
    - tools.py:  Bridge UnifiedMCPProvider → LangChain StructuredTools
    - graph.py:  StateGraph definition (nodes, edges, compilation)

MCP Servers available via UnifiedMCPProvider:
    - OpenSearch nativo: QPT, PPL, SearchIndex, ListIndex
    - Jazidas (:8110):   buscar_fornecedores, buscar_jazidas, detalhes_processo, etc.
    - Empresas (:8111):  buscar_empresas, detalhes_empresa, buscar_por_socio
    - Geo (:8112):       buscar_municipio, municipio_por_coordenada, etc.
"""
