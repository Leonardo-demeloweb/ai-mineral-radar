"""
MCP Server: Empresas
=====================

Search tools for company data (CNPJ — Receita Federal do Brasil).

Architecture:
    - 3 custom tools (cross-index, nested, semantic)
    - QPT handles ~50% of scenarios natively (flat queries, aggregations)
    - Uses Streamable HTTP for MCP communication

Indices:
    - rfb_cnpj_v003 (principal): ~69M estabelecimentos, dynamic:strict, pt_brazilian
    - rfb_cnae_v001 (catálogo): 2.394 docs, k-NN embeddings
    - anm_v003 (cross-reference): 956K docs (processos minerários vinculados)
    - ibge_municipio_v001 (geo): 5.631 docs (fronteiras municípios)

Tools:
    1. buscar_empresas      — 2-index cross (CNAE semântico → CNPJ)
    2. detalhes_empresa     — cross 3 índices (CNPJ + CNAE hierarquia + ANM)
    3. buscar_por_socio     — nested socios + inner_hits
"""
