"""
setup_indices.py — Criação de todos os índices OpenSearch do MineralRadar
=========================================================================

Cria (ou recria) os índices com mapeamentos explícitos, analyzer PT-BR,
geo_shape, geo_point e knn_vector (embeddings semânticos).

╔══════════════════════════════════════════════════════════════════════╗
║  FASE 1 — Dev local (MG + SP) e cloud Oracle Free    (11 índices)   ║
╠═══════════════════════════╦══════════════════════════╦══════════════╣
║ Índice                     ║ Fonte                    ║ Volume       ║
╠═══════════════════════════╬══════════════════════════╬══════════════╣
║ mr_jazidas_v001            ║ ANM SIGMINE+SCM+SICOP    ║ ~25M / ~6GB  ║
║ mr_substancias_v001        ║ ANM substâncias          ║ 862 docs     ║
║ mr_tipo_uso_v001           ║ ANM tipo de uso          ║ ~26 docs     ║
║ mr_empresas_v001           ║ RFB CNPJ filtrado        ║ ~350K/~400MB ║
║ mr_cnae_v001               ║ RFB CNAE                 ║ 2.394 docs   ║
║ mr_municipios_v001         ║ IBGE municípios          ║ 5.570/~950MB ║
║ mr_cfem_v001               ║ ANM CFEM histórico       ║ ~3M / ~300MB ║
║ mr_terras_indigenas_v001   ║ FUNAI TIs                ║ ~800 polígon ║
║ mr_ucs_v001                ║ IBAMA CNUC               ║ ~2.300 pólig ║
║ mr_biomas_v001             ║ IBGE biomas              ║ 6 docs       ║
║ mr_provincias_v001         ║ SGB/CPRM (derivado)      ║ 8 docs       ║
╠═══════════════════════════╬══════════════════════════╬══════════════╣
║  FASE 2 — Brasil completo                             (11 índices)  ║
╠═══════════════════════════╬══════════════════════════╬══════════════╣
║ mr_cvm_listadas_v001       ║ CVM cadastro + DFP       ║ ~200–2K docs ║
║ mr_cprm_v001               ║ CPRM+USGS MRDS+CMMI      ║ ~80K/~350MB  ║
║ mr_geoquimica_v001         ║ CPRM Geoquímica (rocha+  ║ ~65K / ~80MB ║
║                            ║ mineral/minério)         ║              ║
║ mr_ral_v001                ║ ANM RAL produção         ║ ~500K docs   ║
║ mr_autuacoes_v001          ║ IBAMA autuações/embargos ║ ~200K docs   ║
║ mr_mercado_v001            ║ ComexStat+Metals+AMB     ║ ~500K/~150MB ║
║ mr_sicar_v001              ║ INCRA CAR/SICAR          ║ ~6.8M / ~8GB  ║
║ mr_sigef_v001              ║ INCRA SIGEF certificados ║ ~7M  / ~9GB  ║
║ mr_monitoring_v001         ║ DOU + SEI eventos        ║ crescente    ║
║ mr_portos_v001             ║ MTransp+ANTAQ+curadoria  ║ ~50–500 docs ║
║ mr_ferrovias_v001          ║ ANTT Declaração de Rede  ║ ~2–20K linhas║
╚═══════════════════════════╩══════════════════════════╩══════════════╝

Uso:
    python -m scripts.setup_indices                     # cria Fase 1 se não existir
    python -m scripts.setup_indices --fase 1            # só Fase 1
    python -m scripts.setup_indices --fase 2            # só Fase 2
    python -m scripts.setup_indices --fase 0            # todos (1+2)
    python -m scripts.setup_indices --index mr_jazidas_v001
    python -m scripts.setup_indices --recreate --fase 1
    python -m scripts.setup_indices --list
"""
from __future__ import annotations

import sys
import logging
from pathlib import Path

import click
from opensearchpy import OpenSearch, RequestError

sys.path.insert(0, str(Path(__file__).parent.parent))
from mcp_servers.common.config import mcp_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("setup_indices")

EMBEDDING_DIM = 1536  # text-embedding-3-small


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _settings(shards: int = 1, replicas: int = 0, knn: bool = False) -> dict:
    s: dict = {
        "number_of_shards": shards,
        "number_of_replicas": replicas,
        "analysis": {
            "normalizer": {
                "lower_ascii": {
                    "type": "custom",
                    "filter": ["lowercase", "asciifolding"],
                }
            },
            "analyzer": {
                "pt_br": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding", "pt_br_stemmer"],
                }
            },
            "filter": {
                "pt_br_stemmer": {"type": "stemmer", "language": "brazilian"}
            },
        },
    }
    if knn:
        s["index.knn"] = True
    return s


def _knn(dim: int = EMBEDDING_DIM) -> dict:
    # nmslib foi removido no OpenSearch 3.x — usar faiss (HNSW) como padrão
    return {
        "type": "knn_vector",
        "dimension": dim,
        "method": {
            "name": "hnsw",
            "space_type": "cosinesimil",
            "engine": "faiss",
            "parameters": {"ef_construction": 128, "m": 16},
        },
    }


def _text_kw(analyzer: str = "pt_br") -> dict:
    """Texto pesquisável com sub-campo keyword para sort/agg."""
    return {
        "type": "text",
        "analyzer": analyzer,
        "fields": {
            "keyword": {
                "type": "keyword",
                "normalizer": "lower_ascii",
                "ignore_above": 256,
            }
        },
    }


def _date() -> dict:
    return {
        "type": "date",
        "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis",
    }


# ═══════════════════════════════════════════════════════════════════════════
# ── FASE 1 ──────────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════

# 1. mr_jazidas_v001
# Processos minerários ANM (SIGMINE shapes + SCM tabular + CFEM enriquecido).
# Campo restrito: geo_point (centróide) + geo_shape (polígono completo).
# Sobreposições geo pré-computadas no PostGIS antes da indexação.

MR_JAZIDAS = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "properties": {
            "numero_processo":          {"type": "keyword"},
            "ativo":                    {"type": "boolean"},
            "fase":                     {"type": "keyword", "normalizer": "lower_ascii"},
            "situacao":                 {"type": "keyword", "normalizer": "lower_ascii"},
            "substancias":              {"type": "keyword"},
            "substancias_desc":         _text_kw(),
            "categorias_estrategicas":  {"type": "keyword"},
            "prioridade_estrategica":   {"type": "keyword"},
            "uf":                       {"type": "keyword"},
            "municipio":                _text_kw(),
            "regiao":                   {"type": "keyword"},
            "area_ha":                  {"type": "double"},
            "location":                 {"type": "geo_point"},
            "geom":                     {"type": "geo_shape"},
            "titular": {
                "properties": {
                    "nome":           _text_kw(),
                    "cnpj_basico":    {"type": "keyword"},
                    "razao_social":   _text_kw(),
                    "situacao_rfb":   {"type": "keyword"},
                    "cnae_principal": {"type": "keyword"},
                }
            },
            "dt_requerimento":  _date(),
            "dt_validade":      _date(),
            "cfem": {
                "properties": {
                    "total_historico":    {"type": "double"},
                    "ultimo_ano":         {"type": "double"},
                    "anos_producao":      {"type": "integer"},
                    "ultima_arrecadacao": _date(),
                }
            },
            # Sobreposições pré-computadas no PostGIS (ids das restrições que intersectam)
            "restricoes_geo":       {"type": "keyword"},
            "n_restricoes_ti":      {"type": "integer"},
            "n_restricoes_uc":      {"type": "integer"},
            # Correlação CPRM — ocorrências minerais dentro de 10 km
            "n_ocorrencias_cprm":   {"type": "integer"},
            "cprm_substancias":     {"type": "keyword"},
            "cprm_ids_proximos":    {"type": "keyword"},
            # Busca semântica é feita via mr_substancias_v001 (k-NN sobre 862 vetores)
            # → filtro terms em substancias[]. Não há k-NN direto neste índice.
            "indexed_at":   _date(),
        }
    },
}

# 2. mr_substancias_v001
# Catálogo de 862 substâncias minerais com embedding k-NN.
# Usado pelo SubstanciaResolver para resolução semântica ("areia lavada" → IDs).

MR_SUBSTANCIAS = {
    "settings": _settings(shards=1, replicas=0, knn=True),
    "mappings": {
        "properties": {
            "id":               {"type": "integer"},
            "id_anm":           {"type": "integer"},   # IDSubstancia (Cadastro Mineiro)
            "codigo":           {"type": "keyword"},
            "nome":             _text_kw(),
            "nome_normalizado": {"type": "keyword", "normalizer": "lower_ascii"},
            "grupo":            {"type": "keyword"},
            "categoria":        {"type": "keyword"},   # metálica, não-metálica, energética
            "categoria_estrategica": {"type": "keyword"},  # terra_rara, litio, ...
            "estrategica":      {"type": "boolean"},   # crítico/estratégico nacional
            "ativo":            {"type": "boolean"},
            "tipo_uso":         {"type": "keyword"},
            "tipo_uso_id":      {"type": "integer"},
            "fonte":            {"type": "keyword"},
            "embedding":        _knn(),
        }
    },
}

# 3. mr_tipo_uso_v001
# Tipos de uso de substância (~26 registros).
# knn=True para resolução semântica de termos de uso ("para pavimentação" → Construção civil).

MR_TIPO_USO = {
    "settings": _settings(shards=1, replicas=0, knn=True),
    "mappings": {
        "properties": {
            "id":          {"type": "integer"},
            "descricao":   _text_kw(),
            "sigla":       {"type": "keyword"},
            "grupo":       {"type": "keyword"},
            "categoria":   {"type": "keyword"},
            "estrategica": {"type": "boolean"},
            "embedding":   _knn(),
        }
    },
}

# 4. mr_empresas_v001
# Empresas do CNPJ/RFB filtradas por 4 critérios minerários (~350K de 221M).
# Sócios desnormalizados em arrays flat para compatibilidade total com ML nativo
# (Anomaly Detection, k-NN, ML inference). Queries de sócio via terms/match em
# socios_cpf_cnpj[] ou socios_nomes[].  Detalhes completos de sócios ficam no
# índice mr_socios_v001 (Fase 2) se queries complexas forem necessárias.

MR_EMPRESAS = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "cnpj_basico":         {"type": "keyword"},
            "cnpj_completo":       {"type": "keyword"},
            "razao_social":        _text_kw(),
            "nome_fantasia":       _text_kw(),
            "situacao":            {"type": "keyword"},
            "dt_situacao":         _date(),
            "dt_abertura":         _date(),
            "cnae_principal":      {"type": "keyword"},
            "cnae_desc":           _text_kw(),
            "cnaes_secundarios":   {"type": "keyword"},
            "porte":               {"type": "keyword"},
            "natureza_juridica":   {"type": "keyword"},
            "criterio_inclusao":   {"type": "keyword"},  # anm_titular | cnae_mineracao | cfem_top
            "logradouro":          _text_kw(),
            "numero":              {"type": "keyword"},
            "complemento":         _text_kw(),
            "bairro":              _text_kw(),
            "municipio":           _text_kw(),
            "uf":                  {"type": "keyword"},
            "cep":                 {"type": "keyword"},
            "location":            {"type": "geo_point"},
            "capital_social":      {"type": "double"},
            "telefone":            {"type": "keyword"},
            "telefone2":           {"type": "keyword"},
            "email":               {"type": "keyword", "normalizer": "lower_ascii"},
            # ── Sócios desnormalizados (flat arrays) ────────────────────────
            # Arrays paralelos: socios_cpf_cnpj[i] ↔ socios_nomes[i]
            "socios_cpf_cnpj":     {"type": "keyword"},          # array de CPF/CNPJ mascarados
            "socios_nomes":        _text_kw(),                   # array de nomes p/ full-text
            "socios_qualificacoes":{"type": "keyword"},          # array de códigos qualificação
            "socios_count":        {"type": "short"},            # total de sócios
            # ── Vínculos ANM ────────────────────────────────────────────────
            "processos_anm_count": {"type": "integer"},
            "fases_anm":           {"type": "keyword"},
            # ── Risco ambiental IBAMA (preenchido por bot_autuacoes) ───────
            "n_autuacoes":         {"type": "integer"},
            "n_embargos":          {"type": "integer"},
            "n_apreensoes":        {"type": "integer"},
            "valor_total_multa":   {"type": "double"},
            "ultima_autuacao":     _date(),
            "tem_risco_ibama":     {"type": "boolean"},
            "indexed_at":          _date(),
        }
    },
}

# 4b. mr_cvm_listadas_v001
# Companhias abertas CVM (cad_cia_aberta.csv) filtradas por setor mineral ou
# cross-ref com mr_jazidas_v001 / mr_empresas_v001. DFP opcional em `financeiro`.
# Bot: mineral-radar-etl/bots/bot_cvm.py · MCP: buscar_empresa_cvm

MR_CVM_LISTADAS = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "cnpj_cia":             {"type": "keyword"},
            "cnpj_basico":          {"type": "keyword"},
            "cd_cvm":               {"type": "keyword"},
            "denom_social":         _text_kw(),
            "denom_comerc":         _text_kw(),
            "setor_ativ":           {"type": "keyword"},
            "tp_merc":              {"type": "keyword"},
            "categ_reg":            {"type": "keyword"},
            "sit":                  {"type": "keyword"},
            "sit_emissor":          {"type": "keyword"},
            "controle_acionario":   {"type": "keyword"},
            "dt_reg":               _date(),
            "dt_const":             _date(),
            "dt_cancel":            _date(),
            "dt_ini_sit":           _date(),
            "motivo_cancel":        {"type": "keyword"},
            "uf":                   {"type": "keyword"},
            "mun":                  {"type": "keyword"},
            "pais":                 {"type": "keyword"},
            "email":                {"type": "keyword", "normalizer": "lower_ascii"},
            "email_resp":           {"type": "keyword", "normalizer": "lower_ascii"},
            "resp":                 _text_kw(),
            "cnpj_auditor":         {"type": "keyword"},
            "auditor":              {"type": "keyword"},
            "criterio_inclusao":    {"type": "keyword"},
            "indexado_em":          _date(),
            "financeiro": {
                "properties": {
                    "ativo_total":          {"type": "double"},
                    "receita_bruta":        {"type": "double"},
                    "resultado_bruto":      {"type": "double"},
                    "lucro_liquido":        {"type": "double"},
                    "dt_fim_exerc":         _date(),
                    "consolidado":          {"type": "boolean"},
                    "ano_dfp":              {"type": "integer"},
                    "total_acoes_raw":      {"type": "long"},
                    "acoes_tesouraria_raw": {"type": "long"},
                    "atualizado_em":        _date(),
                }
            },
        }
    },
}

# 5. mr_cnae_v001
# 2.394 códigos CNAE com embedding k-NN para resolução semântica de atividades.

MR_CNAE = {
    "settings": _settings(shards=1, replicas=0, knn=True),
    "mappings": {
        "properties": {
            "codigo":    {"type": "keyword"},
            "descricao": _text_kw(),
            "secao":     {"type": "keyword"},
            "divisao":   {"type": "keyword"},
            "grupo":     {"type": "keyword"},
            "classe":    {"type": "keyword"},
            "subclasse": {"type": "keyword"},
            "embedding": _knn(),
        }
    },
}

# 6. mr_municipios_v001
# 5.570 municípios com geo_shape (polígono) + geo_point (centróide).
# Usado pelo MCP Geo para resolução de nome → coords e coords → município.

MR_MUNICIPIOS = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "properties": {
            "codigo_ibge":      {"type": "keyword"},
            "nome":             _text_kw(),
            "nome_normalizado": {"type": "keyword", "normalizer": "lower_ascii"},
            "uf":               {"type": "keyword"},
            "uf_nome":          {"type": "keyword"},
            "regiao":           {"type": "keyword"},
            "mesorregiao":      _text_kw(),
            "microrregiao":     _text_kw(),
            "area_km2":         {"type": "double"},
            "populacao":        {"type": "long"},
            "centroide":        {"type": "geo_point"},
            "poligono":         {"type": "geo_shape"},
            "biomas":           {"type": "keyword"},
        }
    },
}

# 6b. mr_portos_v001
# Portos organizados (públicos), TUP/ETC, terminais privados, pontos de apoio:
# geo_shape (polígono) + geo_point (centróide + acesso rodoviário para roteamento).
# Fonte: CKAN poligonais MTransp + merge portos_brasil.csv + curadoria ANTAQ/TUP.
# Ver docs/SPEC_PORTOS_OPENSEARCH.md. Bot: import_portos / bot_portos (a criar).

MR_PORTOS = {
    "settings": _settings(shards=1, replicas=0, knn=True),
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "codigo": {"type": "keyword"},
            "codigo_antaq": {"type": "keyword"},
            "nome": _text_kw(),
            "nome_normalizado": {"type": "keyword", "normalizer": "lower_ascii"},
            "tipo": {
                "type": "keyword",
                # PORTO_ORGANIZADO | TUP | ETC | PONTO_APOIO | TERMINAL_INTERMODAL | BASE_LOGISTICA
            },
            "esfera": {"type": "keyword"},  # FEDERAL | PRIVADO | ESTADUAL | MUNICIPAL
            "uf": {"type": "keyword"},
            "municipio": {"type": "keyword"},
            "id_ibge_municipio": {"type": "keyword"},
            "autoridade_portuaria": _text_kw(),
            "endereco": _text_kw(),
            "cargas_principais": {"type": "keyword"},
            "centroide": {"type": "geo_point"},
            "acesso_rodoviario": {"type": "geo_point"},
            "poligono": {"type": "geo_shape"},
            "area_km2": {"type": "double"},
            "aliases": _text_kw(),
            "vinculo_porto_codigo": {"type": "keyword"},
            "operador": _text_kw(),
            "fonte": {"type": "keyword"},
            "data_referencia": _date(),
            "validacao_pendente": {"type": "boolean"},
            "ativo": {"type": "boolean"},
            "embedding_nome": _knn(),
            "indexed_at": _date(),
        },
    },
}

# 6c. mr_ferrovias_v001
# Trechos da malha ferroviária federal (shapefile ANTT Declaração de Rede ou equivalente).
# Um documento por feição linear (LineString / MultiLineString) com geo_shape + centróide.
# Ingestão: scripts/ingest_ferrovias.py

MR_FERROVIAS = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "ferrovia_id": {"type": "keyword"},
            "codigo_sigla": {"type": "keyword", "normalizer": "lower_ascii"},
            "nome": _text_kw(),
            "nome_normalizado": {"type": "keyword", "normalizer": "lower_ascii"},
            "operadora": _text_kw(),
            "extensao_km": {"type": "double"},
            "uf": {"type": "keyword"},
            "tipo_malha": {"type": "keyword"},
            "geom": {"type": "geo_shape"},
            "centroide": {"type": "geo_point"},
            "fonte": {"type": "keyword"},
            "ano_referencia": {"type": "integer"},
            "shapefile_layer": {"type": "keyword"},
            "shapefile_fid": {"type": "long"},
            "indexed_at": _date(),
        },
    },
}

# 7. mr_cfem_v001
# ~3M registros de arrecadação CFEM histórica (2002-presente).
# Fonte: ANM CFEM_Arrecadacao.csv (~221 MB). Bot: bot_cfem.py.
# Permite séries temporais por processo/empresa/substância/UF.

MR_CFEM = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "properties": {
            # Chave composta: processo + ano + mes + cnpj
            "numero_processo":   {"type": "keyword"},
            "cnpj_basico":       {"type": "keyword"},
            "ano":               {"type": "integer"},
            "mes":               {"type": "integer"},
            "competencia":       _date(),             # primeiro dia do mês
            # Valores
            "valor_arrecadado":  {"type": "double"},
            "quantidade":        {"type": "double"},
            "unidade_medida":    {"type": "keyword"},
            # Referências
            "substancia":        {"type": "keyword"},
            "substancia_desc":   _text_kw(),
            "municipio":         _text_kw(),
            "uf":                {"type": "keyword"},
            "fase_processo":     {"type": "keyword"},
            "indexed_at":        _date(),
        }
    },
}

# 8. mr_terras_indigenas_v001
# ~750 Terras Indígenas homologadas/declaradas (FUNAI).
# Bot: bot_funai.py. Usado para pré-computar sobreposições no PostGIS.
# Indexado no OpenSearch para queries espaciais on-demand pelo MCP Geo.

MR_TERRAS_INDIGENAS = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "properties": {
            "id_ti":               {"type": "keyword"},
            "nome":                _text_kw(),
            "nome_normalizado":    {"type": "keyword", "normalizer": "lower_ascii"},
            "etnia":               {"type": "keyword"},
            "fase_funai": {
                "type": "keyword",
                # Declarada < Delimitada < Homologada < Regularizada
            },
            "municipios":          _text_kw(),
            "uf":                  {"type": "keyword"},
            "area_ha":             {"type": "double"},
            "populacao_estimada":  {"type": "integer"},
            "dt_homologacao":      _date(),
            "dt_atualizacao":      _date(),
            "centroide":           {"type": "geo_point"},
            "poligono":            {"type": "geo_shape"},
            "fonte":               {"type": "keyword"},  # "FUNAI"
        }
    },
}

# 9. mr_ucs_v001
# ~2.300 Unidades de Conservação federais + estaduais (IBAMA/CNUC).
# Bot: bot_ibama.py (a criar). Sobreposições pré-computadas no PostGIS.

MR_UCS = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "properties": {
            "cod_cnuc":       {"type": "keyword"},
            "nome":           _text_kw(),
            "nome_normalizado": {"type": "keyword", "normalizer": "lower_ascii"},
            "categoria": {
                "type": "keyword",
                # "Proteção Integral" | "Uso Sustentável"
            },
            "grupo":          {"type": "keyword"},  # APA, Parque Nacional, RPPN, etc.
            "esfera":         {"type": "keyword"},  # Federal | Estadual | Municipal
            "orgao_gestor":   {"type": "keyword"},  # ICMBio | SEMA-XX | ...
            "municipios":     _text_kw(),
            "uf":             {"type": "keyword"},
            "area_ha":        {"type": "double"},
            "dt_criacao":     _date(),
            "dt_atualizacao": _date(),
            "centroide":      {"type": "geo_point"},
            "poligono":       {"type": "geo_shape"},
            "fonte":          {"type": "keyword"},  # "IBAMA/CNUC"
        }
    },
}


# 10. mr_biomas_v001
# 6 biomas brasileiros (Amazônia, Caatinga, Cerrado, Mata Atlântica, Pampa, Pantanal).
# Fonte: IBGE GeoFTP (shapefile Biomas_250mil). Bot: bot_biomas.py.
# Usado para pré-computar campo `bioma` em mr_jazidas_v001 e queries geo_shape on-demand.

MR_BIOMAS = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "properties": {
            "slug":             {"type": "keyword"},   # amazonia, cerrado, caatinga, etc.
            "nome":             _text_kw(),            # "Amazônia", "Cerrado", etc.
            "nome_normalizado": {"type": "keyword", "normalizer": "lower_ascii"},
            "codigo":           {"type": "keyword"},   # CD_BIOMA do shapefile IBGE
            "area_km2":         {"type": "double"},
            "centroide":        {"type": "geo_point"},
            "poligono":         {"type": "geo_shape"},
            "fonte":            {"type": "keyword"},   # "IBGE"
            "indexed_at":       _date(),
        }
    },
}


# 11. mr_provincias_v001
# 8 províncias geológicas minerais do Brasil (São Francisco, Borborema, Mantiqueira,
# Províncias Amazônicas, Tocantins, Paraná, Bacias Amazônicas, Parnaíba).
# Polígonos derivados via convex hull + buffer das ocorrências CPRM por província.
# Bot: bot_provincias.py. Usado para contexto geológico de jazidas e ocorrências CPRM.

MR_PROVINCIAS = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "properties": {
            "slug":              {"type": "keyword"},   # sao_francisco, borborema, etc.
            "nome":              _text_kw(),            # nome completo da província
            "nome_normalizado":  {"type": "keyword", "normalizer": "lower_ascii"},
            "n_ocorrencias":     {"type": "integer"},   # doc_count CPRM usado para derivar o polígono
            "area_km2":          {"type": "double"},
            "centroide":         {"type": "geo_point"},
            "poligono":          {"type": "geo_shape"}, # convex hull + buffer dos pontos CPRM
            "minerais_principais": {"type": "keyword"}, # lista de substâncias mais frequentes
            "ufs":               {"type": "keyword"},   # UFs cobertas
            "fonte":             {"type": "keyword"},   # "SGB/CPRM (derivado)"
            "indexed_at":        _date(),
        }
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# ── FASE 2 ──────────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════

# 10. mr_cprm_v001
# ~50K ocorrências minerais do GeoPortal SGB/CPRM (WFS).
# Substância, tipo de depósito, teor estimado, localização.

MR_CPRM = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "properties": {
            # ── Identificação ──────────────────────────────────────────────
            "id_ocorrencia":        {"type": "keyword"},
            "id_afloramento":       {"type": "keyword"},
            "nome":                 _text_kw(),           # toponimia
            # ── Substâncias ────────────────────────────────────────────────
            "substancia_principal": {"type": "keyword"},
            "substancias":          {"type": "keyword"},  # lista
            "classes_utilitarias":  {"type": "keyword"},  # Material construção, etc.
            # ── Classificação econômica e geológica ────────────────────────
            # importancia: Depósito | Indício | Ocorrência | Indeterminado
            "importancia":          {"type": "keyword"},
            # status_economico: Mina | Garimpo | Não explotado | Indeterminado
            "status_economico":     {"type": "keyword"},
            "situacao_mina":        {"type": "keyword"},   # localização e status da mina
            "situacao_garimpo":     {"type": "keyword"},
            # ── Geologia da área ───────────────────────────────────────────
            "rochas_hospedeiras":   _text_kw(),  # rochas que hospedam o minério
            "rochas_encaixantes":   _text_kw(),  # rochas ao redor
            "morfologia":           {"type": "keyword"},
            "texturas":             _text_kw(),
            "tipos_alteracao":      _text_kw(),
            # ── Contexto geográfico e administrativo ───────────────────────
            "provincia":            {"type": "keyword"},
            "municipio":            _text_kw(),
            "uf":                   {"type": "keyword"},
            "sureg":                {"type": "keyword"},   # superintendência regional CPRM
            "projeto":              _text_kw(),
            "folha":                {"type": "keyword"},
            # ── Geo ────────────────────────────────────────────────────────
            "location":             {"type": "geo_point"},
            "area_ha":              {"type": "double"},
            # ── Metadados ──────────────────────────────────────────────────
            "descricao":            _text_kw(),
            "metodo_geoposicionamento": {"type": "keyword"},
            "dt_referencia":        _date(),
            "fonte":                {"type": "keyword"},  # "SGB/CPRM"
            "indexed_at":           _date(),
        }
    },
}

# 11. mr_ral_v001
# Produção mineral anual por empresa (ANM RAL / Anuário Mineral).
# Join principal: cnpj_basico → mr_empresas, numero_processo → mr_jazidas.

MR_RAL = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "properties": {
            "cnpj_basico":       {"type": "keyword"},
            "numero_processo":   {"type": "keyword"},
            "ano_base":          {"type": "integer"},
            "substancia":        {"type": "keyword"},
            "substancia_desc":   _text_kw(),
            "quantidade_produzida": {"type": "double"},
            "unidade_medida":    {"type": "keyword"},
            "valor_producao":    {"type": "double"},
            "municipio":         _text_kw(),
            "uf":                {"type": "keyword"},
            "indexed_at":        _date(),
        }
    },
}

# 12. mr_geoquimica_v001
# Análises geoquímicas do SGB/CPRM (GeoBank): rocha e mineral/minério.
# Um doc por amostra, com campo nested ``analises`` para cada analito medido.
# Coleções OGC API: analises-rocha (61K) + analises-mineral-minerio (4K).

MR_GEOQUIMICA = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "dynamic": "strict",
        "properties": {
            # ── Identificação ────────────────────────────────────────────
            "id_amostra":               {"type": "keyword"},   # numero_de_campo
            "numero_laboratorio":       {"type": "keyword"},   # numero_de_laboratorio
            "classe":                   {"type": "keyword"},   # Rocha | Mineral/Minério
            # ── Projeto / contexto ───────────────────────────────────────
            "projeto":                  _text_kw(),            # projeto_amostragem
            "projeto_publicacao":       _text_kw(),
            "centro_de_custo":          {"type": "keyword"},
            "laboratorio":              _text_kw(),
            # ── Método analítico ─────────────────────────────────────────
            "abertura":                 {"type": "keyword"},   # digestão, fusão, etc.
            "leitura":                  {"type": "keyword"},   # ICP-MS, AA, EO, etc.
            # ── Geologia ─────────────────────────────────────────────────
            "classificacao_petrografica": _text_kw(),
            "unidade_litoestratigrafica": _text_kw(),
            # ── Análises (nested) ────────────────────────────────────────
            # Um subdoc por elemento/analito medido nesta amostra.
            "analises": {
                "type": "nested",
                "properties": {
                    "analito":      {"type": "keyword"},       # Au, Cu, Ni, Nb, etc.
                    "valor":        {"type": "double"},        # 0.0 se < LD
                    "unidade":      {"type": "keyword"},       # ppm | ppb | % | g/t
                    "qualificador": {"type": "keyword"},       # N=não detectado | < | > | None
                }
            },
            # Lista plana de símbolos de analitos (para faceting rápido e pré-filtro).
            "analitos":                 {"type": "keyword"},
            # ── Geo ──────────────────────────────────────────────────────
            "location":                 {"type": "geo_point"},
            # ── Datas / flags ─────────────────────────────────────────────
            "data_de_analise":          _date(),
            "duplicata":                {"type": "boolean"},
            # ── Texto livre ───────────────────────────────────────────────
            "observacao":               {"type": "text", "analyzer": "pt_br"},
            # ── Metadados ─────────────────────────────────────────────────
            "fonte":                    {"type": "keyword"},   # "SGB/CPRM"
            "indexed_at":               _date(),
        }
    },
}


# 13. mr_autuacoes_v001
# Autuações e embargos IBAMA relacionados a empresas do domínio mineral.
# Join: cnpj_basico → mr_empresas.

MR_autuacoes = {
    "settings": _settings(shards=1, replicas=0),
    "mappings": {
        "properties": {
            # ── Identificação ─────────────────────────────────────────────
            "id":                 {"type": "keyword"},          # tipo:SEQ
            "tipo":               {"type": "keyword"},          # Autuacao | Embargo | Apreensao
            "numero_auto":        {"type": "keyword"},
            "serie":              {"type": "keyword"},
            "numero_processo":    {"type": "keyword"},
            "fonte":              {"type": "keyword"},          # IBAMA-SIFISC
            # ── Status ────────────────────────────────────────────────────
            "status":             {"type": "keyword"},          # Lavrado | Cancelado | etc
            "cancelado":          {"type": "boolean"},
            # ── Autuado ───────────────────────────────────────────────────
            "cnpj_basico":        {"type": "keyword"},          # 8 dígitos para join
            "cnpj_autuado":       {"type": "keyword"},          # 14 dígitos completos
            "cpf_autuado":        {"type": "keyword"},          # se PF
            "nome_autuado":       _text_kw(),
            "tp_pessoa":          {"type": "keyword"},          # PF | PJ
            # ── Infração / descrição ─────────────────────────────────────
            "infracao":           _text_kw(),                   # descrição textual
            "fundamentacao":      _text_kw(),                   # base legal
            "gravidade":          {"type": "keyword"},
            "tipo_infracao":      {"type": "keyword"},
            "enquadramento":      _text_kw(),
            # ── Valores ──────────────────────────────────────────────────
            "valor_multa":        {"type": "double"},   # bruto (pode estar em moeda antiga pré-1994)
            "valor_multa_real":   {"type": "double"},   # confiável (null se suspeito)
            "valor_multa_suspeito": {"type": "boolean"},
            "tipo_multa":         {"type": "keyword"},
            # ── Geo ──────────────────────────────────────────────────────
            "municipio":          _text_kw(),
            "uf":                 {"type": "keyword"},
            "cod_municipio":      {"type": "keyword"},
            "location":           {"type": "geo_point"},
            "poligono":           {"type": "geo_shape"},        # área embargada/autuada
            "area_ha":            {"type": "double"},
            # ── Datas ────────────────────────────────────────────────────
            "dt_autuacao":        _date(),
            "dt_julgamento":      _date(),
            "dt_atualizacao":     _date(),
            # ── Bioma / contexto ─────────────────────────────────────────
            "biomas":             {"type": "keyword"},
            "unidade_conservacao": _text_kw(),
            # ── Match de domínio mineral ─────────────────────────────────
            "match_origem":       {"type": "keyword"},          # empresas_mineral | keyword_match | geo_jazida
            "match_keywords":     {"type": "keyword"},
            # ── Metadados ────────────────────────────────────────────────
            "indexed_at":         _date(),
        }
    },
}

# 13. mr_mercado_v001
# Exportações/importações de minerais estratégicos (ComexStat MDIC).
# Filtrado por NCMs de minerais.

MR_MERCADO = {
    # _id: {ncm}_{fluxo}_{uf}_{ano}  (ex: "26011100_export_MG_2024")
    # Um doc por (NCM × fluxo × UF × ano), agregado sobre países e meses.
    # Capítulos: 25-27 (minerais), 28 (químicos inorg.), 71 (metais preciosos), 72-81 (metais base).
    "settings": _settings(shards=2, replicas=0),
    "mappings": {
        "dynamic": "strict",
        "properties": {
            # chaves de dimensionamento
            "ncm":              {"type": "keyword"},
            "fluxo":            {"type": "keyword"},   # "export" | "import"
            "uf":               {"type": "keyword"},   # SG_UF_NCM
            "ano":              {"type": "integer"},
            # descrição NCM
            "ncm_desc":         _text_kw(),
            "ncm_capitulo":     {"type": "keyword"},   # "26"
            "ncm_capitulo_desc":_text_kw(),
            "ncm_secao":        {"type": "keyword"},   # "V" — secção SH
            # métricas agregadas (soma do ano)
            "vl_fob_usd":       {"type": "double"},
            "kg_liquido":       {"type": "double"},
            "qt_estat":         {"type": "double"},
            "vl_frete_usd":     {"type": "double"},    # import only
            "vl_seguro_usd":    {"type": "double"},    # import only
            "vl_cif_usd":       {"type": "double"},    # import only
            "n_meses":          {"type": "integer"},   # meses com dados no ano
            "n_operacoes":      {"type": "long"},      # linhas brutas agregadas
            # países destino/origem (top 10)
            "top_paises":       {"type": "keyword"},   # nomes dos países
            "top_paises_cod":   {"type": "keyword"},   # códigos CO_PAIS
            "top_paises_vl_fob":{"type": "double"},    # valores correspondentes
            "indexed_at":       _date(),
        }
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# mr_sicar_v001 — INCRA CAR / SICAR  (Fase 2)
# ~6.8M imóveis rurais + polígonos  →  índice próprio (decisão: separado do
# mr_restricoes_geo para não inflar o índice de restrições ambientais).
# ─────────────────────────────────────────────────────────────────────────────
MR_SICAR: dict = {
    "settings": _settings(shards=5, replicas=1),
    "mappings": {
        "dynamic": "strict",
        "properties": {
            # ── Identificação ───────────────────────────────────────────────
            "cod_car":              {"type": "keyword"},
            "cpf_cnpj_proprietario":{"type": "keyword"},
            "nome_proprietario":    _text_kw(),
            "uf":                   {"type": "keyword"},
            "municipio":            _text_kw(),
            "cod_municipio_ibge":   {"type": "keyword"},
            "tipo_imovel":          {"type": "keyword"},  # IRU, ASS, PCT, QUI
            "status_car":           {"type": "keyword"},  # AT, PE, CA, SU
            # ── Área ────────────────────────────────────────────────────────
            "area_ha":              {"type": "double"},
            "area_modulos_fiscais":  {"type": "float"},
            # ── Geometria ───────────────────────────────────────────────────
            "poligono":             {"type": "geo_shape"},
            "centroide":            {"type": "geo_point"},
            # ── Datas ───────────────────────────────────────────────────────
            "dt_inscricao":         _date(),
            "dt_retificacao":       _date(),
            "dt_cancelamento":      _date(),
            # ── Restrições calculadas (via PostGIS) ─────────────────────────
            "sobreposicao_ti":      {"type": "boolean"},
            "sobreposicao_uc":      {"type": "boolean"},
            "sobreposicao_area_anm":{"type": "boolean"},
            # ── ETL ─────────────────────────────────────────────────────────
            "indexed_at":           _date(),
        }
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# mr_sigef_v001 — INCRA SIGEF / Certificação de Imóveis Rurais  (Fase 2)
# ~7M parcelas certificadas  →  índice próprio separado do SICAR.
# Fonte: WFS GML2 em acervofundiario.incra.gov.br, layer certificada_sigef_particular_{uf}
# ─────────────────────────────────────────────────────────────────────────────
MR_SIGEF: dict = {
    "settings": _settings(shards=5, replicas=1),
    "mappings": {
        "dynamic": "strict",
        "properties": {
            # ── Identificação ────────────────────────────────────────────────
            "parcela_codigo":       {"type": "keyword"},   # UUID INCRA (usado como _id)
            "codigo_imovel":        {"type": "keyword"},   # código INCRA (13 dígitos)
            "nome_area":            _text_kw(),
            "uf":                   {"type": "keyword"},
            "codigo_municipio":     {"type": "keyword"},   # código IBGE
            # ── Status / situação ────────────────────────────────────────────
            "status":               {"type": "keyword"},   # CERTIFICADA | AGUARDANDO_ANALISE
            "situacao_informada":   {"type": "keyword"},   # REGISTRADA | PENDENTE
            # ── Registro cartorial ───────────────────────────────────────────
            "registro_matricula":   {"type": "keyword"},
            "art":                  {"type": "keyword"},   # número ART do RT
            # ── Área ─────────────────────────────────────────────────────────
            "area_ha":              {"type": "double"},    # calculado da geometria
            # ── Geometria ────────────────────────────────────────────────────
            "poligono":             {"type": "geo_shape"},
            "centroide":            {"type": "geo_point"},
            # ── Datas ────────────────────────────────────────────────────────
            "dt_submissao":         _date(),
            "dt_aprovacao":         _date(),
            "dt_registro":          _date(),
            # ── Cruzamentos calculados ───────────────────────────────────────
            "sobreposicao_area_anm":{"type": "boolean"},
            "sobreposicao_ti":      {"type": "boolean"},
            "sobreposicao_uc":      {"type": "boolean"},
            # ── ETL ──────────────────────────────────────────────────────────
            "indexed_at":           _date(),
        }
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# mr_monitoring_v001 — Eventos de Monitoramento  (Fase 2)
# DOU publicações, SEI tramitações, alertas de prazo, mudanças de status ANM.
# Crescimento contínuo; k-NN para busca semântica em conteúdo de despachos.
# ─────────────────────────────────────────────────────────────────────────────
MR_MONITORING: dict = {
    "settings": _settings(shards=2, replicas=1),
    "mappings": {
        "dynamic": "strict",
        "properties": {
            # ── Referência ao processo ANM ────────────────────────────────
            "numero_processo":  {"type": "keyword"},
            "nup":              {"type": "keyword"},
            # ── Evento ────────────────────────────────────────────────────
            "tipo_evento":      {"type": "keyword"},  # DOU_PUBLICACAO | SEI_TRAMITE | STATUS_CHANGE | PRAZO_ALERT
            "subtipo":          {"type": "keyword"},
            "titulo":           _text_kw(),
            "conteudo":         {
                "type": "text",
                "analyzer": "pt_br",
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            "resumo":           {"type": "text", "analyzer": "pt_br"},
            # Busca textual via analyzer pt_br no campo "conteudo".
            # ── Fonte ─────────────────────────────────────────────────────
            "fonte":            {"type": "keyword"},  # DOU | SEI | ANM_WEB | INTERNO
            "secao_dou":        {"type": "keyword"},  # 1 | 2 | 3 (somente para DOU)
            "numero_dou":       {"type": "keyword"},
            "pagina_dou":       {"type": "integer"},
            "url":              {"type": "keyword"},
            # ── Destinatário / interessado ───────────────────────────────
            "cnpj_titular":     {"type": "keyword"},
            "razao_social":     _text_kw(),
            # ── Status do evento ─────────────────────────────────────────
            "lido":             {"type": "boolean"},
            "relevancia":       {"type": "keyword"},  # ALTA | MEDIA | BAIXA
            "acao_necessaria":  {"type": "boolean"},
            # ── Datas ────────────────────────────────────────────────────
            "dt_evento":        _date(),
            "dt_prazo":         _date(),
            "indexed_at":       _date(),
        }
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# REGISTRO CENTRAL
# fase: 1 = Fase 1 (dev local + Oracle Free), 2 = Fase 2 (Brasil completo)
# ═══════════════════════════════════════════════════════════════════════════

ALL_INDICES: dict[str, dict] = {
    # ── Fase 1 ──────────────────────────────────────────────────────────
    "mr_jazidas_v001":          {"fase": 1, "body": MR_JAZIDAS,            "fonte": "ANM SIGMINE + SCM"},
    "mr_substancias_v001":      {"fase": 1, "body": MR_SUBSTANCIAS,        "fonte": "ANM substâncias"},
    "mr_tipo_uso_v001":         {"fase": 1, "body": MR_TIPO_USO,           "fonte": "ANM tipo de uso"},
    "mr_empresas_v001":         {"fase": 1, "body": MR_EMPRESAS,           "fonte": "RFB CNPJ filtrado"},
    "mr_cnae_v001":             {"fase": 1, "body": MR_CNAE,               "fonte": "RFB CNAE"},
    "mr_municipios_v001":       {"fase": 1, "body": MR_MUNICIPIOS,         "fonte": "IBGE malha municipal"},
    "mr_cfem_v001":             {"fase": 1, "body": MR_CFEM,               "fonte": "ANM CFEM histórico"},
    "mr_terras_indigenas_v001": {"fase": 1, "body": MR_TERRAS_INDIGENAS,   "fonte": "FUNAI TIs"},
    "mr_ucs_v001":              {"fase": 1, "body": MR_UCS,                "fonte": "IBAMA CNUC"},
    "mr_biomas_v001":           {"fase": 1, "body": MR_BIOMAS,             "fonte": "IBGE biomas"},
    "mr_provincias_v001":       {"fase": 1, "body": MR_PROVINCIAS,         "fonte": "SGB/CPRM (derivado)"},
    # ── Fase 2 ──────────────────────────────────────────────────────────
    "mr_cvm_listadas_v001":     {"fase": 2, "body": MR_CVM_LISTADAS,       "fonte": "CVM Companhias Abertas + DFP"},
    "mr_cprm_v001":             {"fase": 2, "body": MR_CPRM,               "fonte": "CPRM GeoBank"},
    "mr_geoquimica_v001":       {"fase": 2, "body": MR_GEOQUIMICA,         "fonte": "CPRM Geoquímica"},
    "mr_ral_v001":              {"fase": 2, "body": MR_RAL,                "fonte": "ANM RAL produção"},
    "mr_autuacoes_v001":        {"fase": 2, "body": MR_autuacoes,          "fonte": "IBAMA autuações"},
    "mr_mercado_v001":          {"fase": 2, "body": MR_MERCADO,            "fonte": "ComexStat MDIC"},
    "mr_sicar_v001":            {"fase": 2, "body": MR_SICAR,              "fonte": "INCRA CAR/SICAR"},
    "mr_sigef_v001":            {"fase": 2, "body": MR_SIGEF,              "fonte": "INCRA SIGEF certificados"},
    "mr_monitoring_v001":       {"fase": 2, "body": MR_MONITORING,         "fonte": "DOU + SEI + ANM eventos"},
    "mr_portos_v001":           {"fase": 2, "body": MR_PORTOS,             "fonte": "MTransp poligonais + ANTAQ + curadoria"},
    "mr_ferrovias_v001":        {"fase": 2, "body": MR_FERROVIAS,         "fonte": "ANTT Declaração de Rede (SHP)"},
}


# ═══════════════════════════════════════════════════════════════════════════
# CLIENT
# ═══════════════════════════════════════════════════════════════════════════

def get_client() -> OpenSearch:
    endpoint = mcp_settings.opensearch_endpoint or "http://localhost:9200"
    kwargs: dict = {
        "hosts": [endpoint],
        "use_ssl": mcp_settings.opensearch_use_ssl,
        "verify_certs": mcp_settings.opensearch_verify_certs,
        "timeout": 30,
    }
    if mcp_settings.opensearch_user and mcp_settings.opensearch_password:
        kwargs["http_auth"] = (mcp_settings.opensearch_user, mcp_settings.opensearch_password)
    client = OpenSearch(**kwargs)
    info = client.info()
    log.info("OpenSearch: cluster=%s  versão=%s", info["cluster_name"], info["version"]["number"])
    return client


# ═══════════════════════════════════════════════════════════════════════════
# OPERAÇÕES
# ═══════════════════════════════════════════════════════════════════════════

def create_index(client: OpenSearch, name: str, body: dict, recreate: bool) -> bool:
    exists = client.indices.exists(index=name)
    if exists and not recreate:
        log.info("  ⏭  %-32s já existe", name)
        return True
    if exists and recreate:
        client.indices.delete(index=name)
        log.info("  🗑  %-32s deletado", name)
    client.indices.create(index=name, body=body)
    n = len(body["mappings"]["properties"])
    log.info("  ✔  %-32s criado  (%d campos)", name, n)
    return True


def list_indices(client: OpenSearch) -> None:
    rows = client.cat.indices(
        index="mr_*", h="index,docs.count,store.size,health", s="index", format="json"
    )
    existing = {r["index"] for r in rows} if rows else set()
    log.info("\n%-32s %-6s %-10s %-12s  %-7s  %s", "ÍNDICE", "FASE", "DOCS", "TAMANHO", "STATUS", "FONTE")
    log.info("-" * 90)
    for name, meta in ALL_INDICES.items():
        if name in existing:
            r = next(r for r in rows if r["index"] == name)
            status = f"[{r.get('health','?')}]"
            docs = r.get("docs.count", "0")
            size = r.get("store.size", "-")
        else:
            status = "[ausente]"
            docs = "-"
            size = "-"
        log.info("%-32s F%-5d %-10s %-12s  %-7s  %s", name, meta["fase"], docs, size, status, meta["fonte"])


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

@click.command()
@click.option("--recreate", is_flag=True, default=False,
              help="Apaga e recria os índices selecionados (DESTRUTIVO).")
@click.option("--fase", type=click.Choice(["0", "1", "2"]), default="1",
              help="Fase a criar: 1=Fase1, 2=Fase2, 0=todas. Default: 1.")
@click.option("--index", "only_index", default=None, metavar="NOME",
              help="Cria apenas um índice específico.")
@click.option("--list", "do_list", is_flag=True, default=False,
              help="Lista todos os índices mr_* (existentes e ausentes).")
def main(recreate: bool, fase: str, only_index: str | None, do_list: bool) -> None:
    """Cria os índices OpenSearch do MineralRadar com mapeamentos explícitos."""
    client = get_client()

    if do_list:
        list_indices(client)
        return

    # Seleciona quais índices criar
    if only_index:
        if only_index not in ALL_INDICES:
            log.error("Índice desconhecido: %s\nDisponíveis: %s", only_index, list(ALL_INDICES))
            sys.exit(1)
        targets = {only_index: ALL_INDICES[only_index]["body"]}
    else:
        fase_int = int(fase)
        targets = {
            name: meta["body"]
            for name, meta in ALL_INDICES.items()
            if fase_int == 0 or meta["fase"] == fase_int
        }

    if recreate:
        log.warning("⚠  --recreate: dados dos índices abaixo serão APAGADOS!")
        click.confirm("Confirmar?", abort=True)

    log.info("\nCriando %d índice(s) [fase=%s]...\n", len(targets), fase)
    ok = err = 0
    for name, body in targets.items():
        try:
            create_index(client, name, body, recreate)
            ok += 1
        except RequestError as e:
            reason = e.info.get("error", {}).get("reason", str(e))
            log.error("  ✘  %-32s ERRO: %s", name, reason)
            err += 1

    log.info("\n%d criado(s) / %d já existia(m) / %d erro(s).", ok, err, 0)
    if err:
        sys.exit(1)


if __name__ == "__main__":
    main()
