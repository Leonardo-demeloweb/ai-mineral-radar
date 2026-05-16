"""
Empresas Query Modules
=======================

Query builders and orchestrators for the Empresas MCP Server.

Modules:
    - cnae.py       — CnaeResolver (semantic k-NN + BM25 match on rfb_cnae_v001)
    - empresas.py   — Queries on rfb_cnpj_v003 (flat + nested CNAE sec.)
    - detalhes.py   — Cross-index: rfb_cnpj_v003 + rfb_cnae_v001 + anm_v003
    - socios.py     — Nested socios query + inner_hits on rfb_cnpj_v003
"""
