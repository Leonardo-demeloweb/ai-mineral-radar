"""
MCP Server: Jazidas
====================

Search tools for mining processes (ANM — Agência Nacional de Mineração).

Transport: Streamable HTTP (POST /mcp) — MCP spec v1.26+
Port: 8110

Architecture:
    - 5 custom tools (cross-index, nested, semantic) via Streamable HTTP
    - QPT handles ~50% of scenarios natively via OpenSearch MCP Nativo
    - LangGraph connects to both via UnifiedMCPProvider (single protocol)

Indices:
    - anm_v003 (principal): 956K docs, flat fields + nested substancias/poligonos
    - anm_substancia_v001 (catálogo): 862 docs, k-NN embeddings
    - anm_tipo_uso_substancia_v001 (catálogo): ~50 docs, k-NN embeddings
    - rfb_cnpj_v003 (cross-reference): 221M docs (via cnpjBasico)

Tools:
    1. buscar_fornecedores      — 3-index cross (substância → ANM → CNPJ)
    2. buscar_jazidas           — semantic k-NN + geo
    3. detalhes_processo        — cross-index ANM → CNPJ
    4. jazidas_por_poligono     — nested geo_shape
    5. verificar_vigencia       — nested substancias
"""
