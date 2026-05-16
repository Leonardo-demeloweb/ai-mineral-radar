# Índices OpenSearch — MineralRadar (estado atual)

> **Atualizado em:** 16 de maio de 2026  
> **Cluster medido:** `mineralradar-local` (OpenSearch **3.6.0**, Docker `backend/docker-compose.local.yml`)  
> **Endpoint típico dev:** `http://localhost:9200` (variáveis `OPENSEARCH_*` no `.env`)  
> **Mapeamentos e criação:** `backend/scripts/setup_indices.py`  
> **Listagem ao vivo:** `cd backend && python -m scripts.setup_indices --list`  
> **Validação geo/jazidas:** `python scripts/validate_opensearch_cluster.py`

---

## Escopo deste documento

Este arquivo descreve o **cenário real do cluster de desenvolvimento local** em 16/05/2026: contagens, tamanhos, saúde, lacunas de ingestão e implicações para os MCPs dos índices **`mr_*_v001`**.

---

## Resumo executivo

| Métrica | Valor (cluster local) |
|--------|------------------------|
| Índices `mr_*` definidos | **22** (`setup_indices.py`) |
| Índices presentes | **20** |
| Índices ausentes | **2** (`mr_autuacoes_v001`, `mr_cvm_listadas_v001`) |
| Índices vazios (0 docs raiz) | **1** (`mr_ral_v001`) |
| Documentos raiz (estimativa) | **~5,84 milhões** |
| Armazenamento primário | **~3,5 GB** |
| Status cluster | **green** (nó único); alguns índices **yellow** (réplica 0 em cluster single-node) |

### Tabela consolidada — todos os índices `mr_*`

Coluna **Docs (cat)** = valor de `_cat/indices` (pode incluir filhos **nested**). Coluna **Docs (raiz)** = `_count` com `match_all` (documentos lógicos de negócio).

| Índice | Fase | Docs (cat) | Docs (raiz) | Tamanho | Health | Fonte / ETL | MCP / uso principal |
|--------|------|------------|-------------|---------|--------|-------------|---------------------|
| `mr_jazidas_v001` | 1 | 906.780 | 906.780 | 1,5 GB | green | ANM SIGMINE + SCM + SICOP | Jazidas: `buscar_jazidas`, detalhes, vigência, disponibilidades |
| `mr_cfem_v001` | 1 | 3.289.871 | 3.289.871 | 691 MB | green | ANM CFEM (`bot_cfem.py`) | Jazidas: CFEM, ranking arrecadação |
| `mr_sigef_v001` | 2 | 1.411.595 | 1.411.595 | 1,0 GB | yellow | INCRA SIGEF (`bot_sigef.py`) | Geo: sobreposição imóveis rurais |
| `mr_geoquimica_v001` | 2 | 305.955* | **20.342** | 13,3 MB | green | CPRM OGC API (`bot_geoquimica.py`) | Jazidas: `geoquimica_proxima`, `geoquimica_detalhes_amostra` |
| `mr_cprm_v001` | 2 | 36.472 | 36.472 | 165,5 MB | green | CPRM GeoBank (`bot_cprm.py`) | Jazidas: ocorrências, enriquecimento `n_ocorrencias_cprm` |
| `mr_mercado_v001` | 2 | 66.771 | 66.771 | 24,9 MB | green | ComexStat / AMB (`bot_mercado.py`) | Jazidas: séries de mercado / NCM |
| `mr_empresas_v001` | 1 | 43.622 | 43.622 | 29,2 MB | green | RFB CNPJ filtrado (`bot_empresas.py`) | Empresas + enriquecimento titular em jazidas |
| `mr_sicar_v001` | 2 | 56.134 | 56.134 | 35,9 MB | yellow | INCRA CAR (`bot_sicar.py`) | Geo: CAR / imóveis rurais (amostra parcial no dev) |
| `mr_municipios_v001` | 1 | 5.572 | 5.572 | 227,1 MB | green | IBGE (`bot_municipios.py`) | Geo: município por nome/coordenada, `geo_shape` |
| `mr_cnae_v001` | 1 | 1.359 | 1.359 | 16,3 MB | green | RFB CNAE (`bot_cnae.py`) | Empresas: resolução semântica de CNAE (k-NN) |
| `mr_substancias_v001` | 1 | 358 | 358 | 6,7 MB | green | ANM substâncias (`bot_anm` / indexador) | Jazidas: `SubstanciaResolver` (k-NN + BM25) |
| `mr_ferrovias_v001` | 2 | 1.865 | 1.865 | 23,5 MB | green | ANTT SHP (`ingest_ferrovias.py`) | Geo: `ferrovias_proximas`, geometria de trecho |
| `mr_terras_indigenas_v001` | 1 | 657 | 657 | 7,6 MB | green | FUNAI (`bot_funai.py` / `bot_terras_indigenas.py`) | Geo + Jazidas: sobreposição TI |
| `mr_ucs_v001` | 1 | 2.073 | 2.073 | 15,4 MB | green | IBAMA CNUC (`bot_ucs.py`) | Geo + Jazidas: sobreposição UC |
| `mr_portos_v001` | 2 | 36 | 36 | 44,4 KB | green | MTransp + ANTAQ + curadoria (`ingest_portos.py`) | Geo: portos, rotas, `comparar_rotas` |
| `mr_tipo_uso_v001` | 1 | 26 | 26 | 9,1 KB | green | ANM tipo de uso | Jazidas: uso de substância (k-NN) |
| `mr_biomas_v001` | 1 | 6 | 6 | 3,5 MB | green | IBGE biomas (`bot_biomas.py`) | Geo: identificar bioma por ponto |
| `mr_provincias_v001` | 1 | 8 | 8 | 31,1 KB | green | Derivado CPRM (`bot_provincias.py`) | Contexto geológico regional |
| `mr_monitoring_v001` | 2 | 38 | 38 | 107 KB | yellow | DOU / eventos (`bot_monitoring.py`) | Alertas / monitoramento (piloto) |
| `mr_ral_v001` | 2 | 0 | 0 | 208 B | green | ANM RAL (`bot_mercado` / futuro `bot_ral`) | Produção anual — **índice criado, sem ingestão** |
| `mr_autuacoes_v001` | 2 | — | — | — | **ausente** | IBAMA SIFISC (`bot_autuacoes.py`) | Empresas: risco ambiental — **índice não criado no cluster** |
| `mr_cvm_listadas_v001` | 2 | — | — | — | **ausente** | CVM cadastro + DFP (`bot_cvm.py`) | Empresas: `buscar_empresa_cvm` — **criar índice + rodar ETL** |

\* `mr_geoquimica_v001`: o `_cat/indices` soma documentos **nested** (`analises[]`); cada amostra tem dezenas de analitos → ~306K segmentos Lucene vs **~20K amostras** reais.

---

## Cobertura geográfica atual (dev local)

O cluster local **não replica o Brasil inteiro** em todos os índices. Destaques medidos em 16/05/2026:

| Índice | Cobertura observada |
|--------|---------------------|
| `mr_jazidas_v001` | ~907K processos (subconjunto ANM; meta produção ~25M ativos+inativos) |
| `mr_geoquimica_v001` | Envelope BR: lat **+4,13°** a **−32,40°**, lon **−69,82°** a **−36,02°**; **~20K amostras** indexadas (OGC prevê ~65K) |
| `mr_geoquimica_v001` | **0 amostras** em raio 50 km de Carajás (PA) e 25 km de Paraíso (MG) — lacuna de dados na subárea indexada, não falha de query |
| `mr_ferrovias_v001` | Geometria presente (1.865 trechos), mas campo `nome`/`codigo_sigla` com **hashes** do ingest — busca textual por "Norte-Sul" retorna **0 hits** |
| `mr_portos_v001` | 36 portos curados (Santos, Paranaguá, Itaqui, etc.) |
| `mr_sicar_v001` | ~56K imóveis (dev parcial; produção prevista ~6,8M) |

---

## Arquitetura: fases e relacionamentos

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  FASE 1 — Núcleo regulatório + referência geo                               │
│                                                                             │
│  mr_substancias_v001 ──k-NN──► mr_jazidas_v001 ◄──enriquec── mr_cfem_v001   │
│  mr_tipo_uso_v001     ──k-NN──►      │                                      │
│  mr_empresas_v001 ◄──cnpj_basico────┤                                       │
│  mr_cnae_v001       ──k-NN──► mr_empresas_v001                              │
│  mr_municipios_v001 ◄──codigo IBGE──┘ (via município/UF)                   │
│  mr_terras_indigenas_v001 / mr_ucs_v001 / mr_biomas_v001 → restrições geo   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  FASE 2 — Geologia, mercado, infraestrutura, monitoramento                  │
│                                                                             │
│  mr_cprm_v001 ──10 km──► mr_jazidas_v001 (n_ocorrencias_cprm, cprm_*)       │
│  mr_geoquimica_v001 (amostras CPRM, nested analises)                        │
│  mr_mercado_v001 / mr_ral_v001 (produção & comércio exterior)               │
│  mr_sicar_v001 / mr_sigef_v001 (restrições fundiárias)                      │
│  mr_portos_v001 / mr_ferrovias_v001 (logística — rotas Azure Maps + overlay) │
│  mr_monitoring_v001 / mr_autuacoes_v001 (risco & alertas — em expansão)     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Chaves de ligação

| De | Para | Campo |
|----|------|---------|
| `mr_jazidas_v001` | `mr_empresas_v001` | `titular.cnpj_basico` = `cnpj_basico` |
| `mr_jazidas_v001` | `mr_substancias_v001` | `substancias` / `substancias_desc` ↔ `nome_normalizado` |
| `mr_jazidas_v001` | `mr_cfem_v001` | `numero_processo` |
| `mr_jazidas_v001` | `mr_cprm_v001` | pré-computado: `cprm_ids_proximos`, `n_ocorrencias_cprm` |
| `mr_empresas_v001` | `mr_cnae_v001` | `cnae_principal` = `codigo` |
| `mr_empresas_v001` | `mr_autuacoes_v001` | `cnpj_basico` (quando indexado) |
| `mr_empresas_v001` | `mr_cvm_listadas_v001` | `cnpj_basico` |
| `mr_jazidas_v001` | `mr_cvm_listadas_v001` | `titular.cnpj_basico` = `cnpj_basico` |
| `mr_cfem_v001` | `mr_jazidas_v001` | `numero_processo` |
| Geo (coords) | `mr_municipios_v001` | `geo_shape` em `poligono` / `centroide` |

---

## Detalhamento por índice

### `mr_jazidas_v001` — Processos minerários ANM

| | |
|--|--|
| **Volume local** | 906.780 docs · 1,5 GB |
| **Schema** | Plano (sem nested profundo): `location` (geo_point), `geom` (geo_shape), `titular`, `substancias[]`, `cfem`, flags `n_restricoes_*`, correlação CPRM |
| **ETL** | `bot_anm_direto.py`, `bot_scm.py`, `bot_sicop.py`, `bot_inativos.py`, `bot_enrich_municipio.py`, `bot_cprm.py --enrich-jazidas` |
| **Busca** | BM25 pt-BR, filtros UF/município/fase/ativo, `geo_distance` em `location`, `geo_shape` em `geom` |
| **k-NN** | Não — substâncias resolvidas via `mr_substancias_v001` + filtro `terms` |

Campos críticos: `numero_processo`, `ativo`, `fase`, `situacao`, `substancias`, `substancias_desc`, `area_ha`, `location`, `geom`, `titular.*`, `cfem.*`, `cprm_*`, `restricoes_geo`.

---

### `mr_cfem_v001` — Arrecadação CFEM

| | |
|--|--|
| **Volume local** | 3.289.871 docs · 691 MB |
| **Granularidade** | Um doc por processo × competência (ano/mês) × declarante |
| **Campos** | `numero_processo`, `cnpj_basico`, `ano`, `mes`, `valor_arrecadado`, `substancia`, `uf` |
| **Uso MCP** | Séries temporais, ranking por município/empresa, cruzamento com jazidas |

---

### `mr_empresas_v001` — CNPJ filtrado (universo mineral)

| | |
|--|--|
| **Volume local** | 43.622 docs · 29,2 MB (filtro: titular ANM, CNAE mineração, top CFEM) |
| **Geo** | `location` (geo_point) |
| **Sócios** | Arrays flat: `socios_cpf_cnpj[]`, `socios_nomes[]` (sem nested) |
| **Risco IBAMA** | Campos `n_autuacoes`, `tem_risco_ibama` — preenchidos quando `mr_autuacoes_v001` existir |

---

### `mr_cvm_listadas_v001` — Companhias abertas CVM

| | |
|--|--|
| **Volume esperado** | ~200–2.000 docs (filtro setor mineral + cross-ref jazidas/empresas) |
| **Fonte** | `dados.cvm.gov.br` — `cad_cia_aberta.csv`; DFP anual opcional |
| **Campos** | `cnpj_cia`, `cnpj_basico`, `cd_cvm`, `denom_social`, `tp_merc`, `sit`, `setor_ativ`, `financeiro.*` |
| **ETL** | `python -m bots.bot_cvm --all` (download → index → enrich-jazidas → enrich-dfp) |
| **Criação índice** | `python -m scripts.setup_indices --index mr_cvm_listadas_v001` |
| **MCP** | `buscar_empresa_cvm`; enriquecimento em `detalhes_empresa` via `cnpj_basico` |
| **Join** | `cnpj_basico` → `mr_empresas_v001` / `mr_jazidas_v001.titular.cnpj_basico` |

Critérios de inclusão no ETL (`bot_cvm.py`): setor mineral na CVM **OU** CNPJ presente em jazidas **OU** em empresas filtradas.

---

### `mr_substancias_v001` + `mr_tipo_uso_v001` — Catálogos com embedding

| Índice | Docs | k-NN |
|--------|------|------|
| `mr_substancias_v001` | 358 | `embedding` dim 1536, HNSW faiss |
| `mr_tipo_uso_v001` | 26 | idem |

Fluxo híbrido (`substancia.py`): uso semântico → k-NN/BM25 nos catálogos → filtro em `mr_jazidas_v001` por `substancias_desc.keyword` ou uso.

---

### `mr_municipios_v001` — Malha municipal IBGE

| | |
|--|--|
| **Volume** | 5.572 municípios · 227 MB (polígonos) |
| **Geo** | `centroide` (geo_point), `poligono` (geo_shape) |
| **MCP Geo** | Resolver nome → coords; ponto dentro de município; contexto regional |

---

### `mr_cprm_v001` — Ocorrências minerais CPRM

| | |
|--|--|
| **Volume** | 36.472 · 165 MB |
| **Geo** | `location` (geo_point) |
| **Campos** | `substancia_principal`, `importancia`, `status_economico`, `provincia`, `rochas_hospedeiras` |
| **Correlação** | `bot_cprm.py --enrich-jazidas` atualiza `mr_jazidas_v001` num raio de 10 km |

---

### `mr_geoquimica_v001` — Amostras geoquímicas CPRM

| | |
|--|--|
| **Docs raiz** | **20.342 amostras** (cat mostra ~306K por nested `analises`) |
| **Fonte** | OGC API `geoservicos.sgb.gov.br` — rocha + mineral/minério |
| **ID documento** | `GEO:{id_amostra}` |
| **Campos** | `id_amostra`, `classe`, `projeto`, `location`, `analitos[]`, `analises` (nested: `analito`, `valor`, `unidade`, `qualificador`) |
| **Tools** | `geoquimica_proxima`, `geoquimica_detalhes_amostra` |
| **Limitação** | Cobertura espacial irregular; muitas regiões minerais sem amostras no subconjunto indexado |

Exemplo de busca por proximidade (campo raiz `location`, não nested):

```json
{
  "query": {
    "bool": {
      "filter": [
        {
          "geo_distance": {
            "distance": "25km",
            "location": { "lat": -20.95, "lon": -46.96 }
          }
        }
      ]
    }
  }
}
```

---

### `mr_portos_v001` + `mr_ferrovias_v001` — Logística

**Portos** (36 docs): `centroide`, `acesso_rodoviario`, `poligono`, `embedding_nome`, tipos `PORTO_ORGANIZADO` | `TUP` | etc. Ver `docs/SPEC_PORTOS_OPENSEARCH.md`.

**Ferrovias** (1.865 trechos): `geom` (geo_shape), `centroide`, `nome`, `codigo_sigla`.

| Problema conhecido | Impacto | Correção |
|--------------------|---------|----------|
| Ingest ANTT sem coluna `NOME`/`SIGLA_EF` | `nome` = hash `antt-2024-linhaesta-o-h...` | Reingest `ingest_ferrovias.py` mapeando atributos reais do shapefile |
| Busca "Norte-Sul" | 0 resultados | Depende da correção acima |
| Rota no mapa | Azure Maps (rodoviário); overlay ferrovia é geometria, não rota ferroviária | Comportamento esperado |

---

### `mr_sigef_v001` + `mr_sicar_v001` — Fundiário

| Índice | Docs local | Observação |
|--------|------------|------------|
| `mr_sigef_v001` | 1.411.595 · 1 GB | Certificações INCRA; yellow = réplica |
| `mr_sicar_v001` | 56.134 · 36 MB | Amostra dev; meta ~6,8M em produção |

---

### `mr_mercado_v001` — Comércio exterior / preços

66.771 docs · 24,9 MB — séries ComexStat, metais (Metals-API), enriquecimento AMB. Tool: evolução de mercado por NCM/substância.

---

### `mr_terras_indigenas_v001` + `mr_ucs_v001` + `mr_biomas_v001`

| Índice | Docs | Geo |
|--------|------|-----|
| TIs FUNAI | 657 | `poligono` + `centroide` |
| UCs CNUC | 2.073 | idem |
| Biomas IBGE | 6 | idem |

Usados em sobreposição com processos (`verificar_restricoes` / enriquecimento PostGIS).

---

### `mr_provincias_v001` — Províncias minerais (derivado)

8 polígonos derivados de hull CPRM por província (`bot_provincias.py`). Contexto geológico em respostas do agente.

---

### `mr_monitoring_v001` — Eventos (piloto)

38 eventos · DOU/SEI/ANM — índice para alertas; volume crescente com `bot_monitoring.py`.

---

### Pendentes no cluster local

| Índice | Situação | Próximo passo |
|--------|----------|---------------|
| `mr_ral_v001` | Criado, **0 docs** | Rodar ingest RAL/AMB |
| `mr_autuacoes_v001` | **Não criado** | `python -m scripts.setup_indices --index mr_autuacoes_v001` + `bot_autuacoes.py` |
| `mr_cvm_listadas_v001` | **Não criado** | `python -m scripts.setup_indices --index mr_cvm_listadas_v001` + `python -m bots.bot_cvm --all` |

---

## Capacidades de busca (MCP)

| Tipo | Índices | Campo | Status |
|------|---------|-------|--------|
| Full-text pt-BR | `mr_jazidas_v001`, `mr_empresas_v001`, `mr_cprm_v001` | vários `_text_kw` | ✅ |
| geo_distance | `mr_jazidas_v001` | `location` | ✅ |
| geo_shape (interseção) | `mr_jazidas_v001`, `mr_municipios_v001`, TIs, UCs, biomas, portos, ferrovias | `geom` / `poligono` | ✅ (ferrovias: geometria ok, nome ruim) |
| k-NN semântico | `mr_substancias_v001`, `mr_tipo_uso_v001`, `mr_cnae_v001`, `mr_portos_v001` | `embedding` / `embedding_nome` | ✅ |
| k-NN no índice principal | `mr_jazidas_v001`, `mr_empresas_v001` | — | ❌ por desenho (2 passos) |
| Nested (analitos) | `mr_geoquimica_v001` | `analises` | ✅ queries nested para filtro por analito |
| Detalhe por ID | `mr_geoquimica_v001` | `GET GEO:{id_amostra}` | ✅ |

---

## Comandos operacionais

```bash
# Listar índices com contagens (a partir de backend/)
cd backend && source ../.env && source .venv/bin/activate
python -m scripts.setup_indices --list

# Criar índice ausente
python -m scripts.setup_indices --index mr_autuacoes_v001
python -m scripts.setup_indices --index mr_cvm_listadas_v001

# ETL CVM (a partir de mineral-radar-etl/)
python -m bots.bot_cvm --all

# Validar cluster + probes Carajás / geoquímica
python scripts/validate_opensearch_cluster.py

# Contagem raiz vs cat (ex.: geoquímica)
curl -s 'http://localhost:9200/mr_geoquimica_v001/_count'
curl -s 'http://localhost:9200/_cat/indices/mr_geoquimica_v001?v'
```

---

## Referências no repositório

| Documento / código | Conteúdo |
|--------------------|----------|
| `docs/BASES_DADOS_MINERALRADAR.md` | Fontes públicas e prioridade de ETL |
| `docs/SPEC_ETL_MINERALRADAR.md` | Pipeline PostGIS → OpenSearch |
| `docs/MCP_SERVER_JAZIDAS.md` | Tools que consomem cada índice |
| `docs/MCP_SERVER_GEO.md` | Portos, ferrovias, municípios |
| `docs/MCP_SERVER_EMPRESAS.md` | CNPJ, CNAE, CVM |
| `mineral-radar-etl/bots/` | Bots de ingestão por fonte |

---

## Histórico deste arquivo

| Data | Alteração |
|------|-----------|
| 2026-05-16 | Documento criado: índices `mr_*`, snapshot `mineralradar-local`, lacunas e notas operacionais |
