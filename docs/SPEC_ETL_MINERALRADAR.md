# Especificação de ETL — MineralRadar

**Documento criado em:** 05 de maio de 2026  
**Contexto:** Especificação da arquitetura de ingestão de dados do MineralRadar. O ETL é construído do zero com PostgreSQL + PostGIS como staging e Apache Airflow/Prefect como orquestrador.

---

## 1. Decisão arquitetural central: SQL como camada intermediária?

### 1.1 Padrão de mercado para ETL geoespacial

```
Fontes públicas (Shapefiles, CSVs)
    ↓ Bots de ingestão (Python)
Banco relacional + Geo  ←── staging + joins + transformações
    ↓ Indexador
OpenSearch  ←── busca, IA, geo queries
```

### 1.2 Por que o MineralRadar precisa de um banco SQL intermediário

O MineralRadar consumirá **10+ fontes** com estruturas completamente diferentes:

| Fonte | Formato de origem | Canal de obtenção | Necessidade de transformação |
|---|---|---|---|
| ANM — processos (tabular) | Arquivos de texto relacionais (múltiplas tabelas) | `https://dadosabertos.anm.gov.br/SCM/microdados/microdados-scm.zip` (~313MB, diário) ou CSVs individuais por tipo de título | Alta — join entre processos, substâncias, municípios, titulares, eventos |
| ANM — shapes (SIGMINE) | ZIP com Shapefiles ESRI (ativos + inativos) | `https://dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/{UF}.zip` ou `BRASIL.zip` (~123MB). Inativos: `PROCESSOS_INATIVOS.zip` (~150MB). Atualizado diariamente. | Alta — converter para GeoJSON, reprojetar para WGS84, separar por estado |
| ANM — CFEM | CSV por período | `https://dadosabertos.anm.gov.br/CFEM/CFEM_Arrecadacao.csv` (~221MB completo) ou fatias por período (2002-2026). Atualizado diariamente. | Média — agregar por processo, calcular série histórica |
| ANM — RAL (produção) | CSV por empresa/ano-base | `dados.gov.br` → "Anuário Mineral / RAL" (AMB) | Média — join com processo ANM via CNPJ/NUP |
| CPRM — Ocorrências Minerais | GeoJSON via OGC API Features | SGB `geoservicos.sgb.gov.br/ogcapi` (coleção `recursos-minerais`; legado WFS GeoPortal) | Baixa — já estruturado |
| CPRM — Geoquímica (rocha + mineral/minério) | GeoJSON via OGC API Features | `geoservicos.sgb.gov.br/ogcapi/collections/geologia/geoquimica/` (`analises-rocha`, `analises-mineral-minerio`) | Baixa — **ingestão direta** OpenSearch com `bot_geoquimica.py` (sem tabela `raw_*` obrigatória no Postgres) |
| FUNAI — Terras Indígenas | Shapefile/GeoJSON mensal | Download oficial FUNAI `gov.br/funai` | Média — converter + calcular sobreposições com processos ANM no PostGIS |
| IBAMA — CNUC (UCs) | Shapefile mensal | `dados.mma.gov.br` | Média — calcular sobreposições com processos ANM no PostGIS |
| IBAMA — Autuações | CSV | `dados.gov.br` → "Autuações IBAMA" | Baixa — join com CNPJ do titular |
| RFB — CNPJ | CSV bulk mensal (221M registros, ~40GB) | Download bulk Receita Federal (dados.rfb.gov.br) | Alta — 221M registros, join multi-tabela, geocoding |
| ComexStat — MDIC | CSV mensal por NCM | `api-comexstat.mdic.gov.br` ou download bulk | Média — filtrar NCMs minerais, agregar por substância |

> **Nota sobre fontes:** O ArcGIS REST da ANM (`geo.anm.gov.br`) é a camada de visualização/mapa — **não é fonte de ETL**. Para 25M+ documentos seria inviável (paginação de ~2.000 registros/request, sem completude de campos internos). Toda a ingestão de dados ANM deve usar os arquivos bulk distribuídos via `dados.gov.br` e portal de dados abertos da ANM. O ArcGIS REST pode ser utilizado no **frontend** para renderizar tiles WMS/camadas de mapa.

**Conclusão:** com 10+ fontes heterogêneas, tentar fazer tudo diretamente em memória (Python → OpenSearch) resultaria em código frágil, difícil de debugar e impossível de auditar. O SQL intermediário não é opcional neste caso — é a arquitetura correta.

---

## 2. Qual banco SQL usar?

### 2.1 Comparativo de opções

| Banco | Prós | Contras | Custo |
|---|---|---|---|
| **SQL Server** (Microsoft) | Amplamente conhecido | Pago (licença), fraco em geo nativo, Windows-centric | Alto |
| **PostgreSQL + PostGIS** | Open source, **melhor suporte geo do mercado** (PostGIS), JSONB nativo, extensível, padrão da indústria para dados geoespaciais | Curva de aprendizado se a equipe não conhece | Gratuito |
| **MySQL / MariaDB** | Simples, conhecido | Geo fraco, sem PostGIS, menos recursos analíticos | Gratuito |
| **DuckDB** | Ultra-rápido para transformações analíticas, sem servidor, Python nativo | Não é um banco transacional, sem geo espacial avançado | Gratuito |
| **SQLite** | Simples, zero infra | Sem concorrência real, sem geo avançado, escala limitada | Gratuito |

### 2.2 Recomendação: PostgreSQL + PostGIS

**PostgreSQL + PostGIS** é a escolha natural para este projeto pelos seguintes motivos:

1. **PostGIS é a referência mundial em geo no SQL** — ST_Intersects, ST_Within, ST_Distance, ST_Area — exatamente o que precisamos para calcular sobreposições entre processos ANM, TIs, UCs e biomas **antes** de enviar ao OpenSearch
2. **Open source, sem licença** — importante para um produto novo sem receita ainda
3. **JSONB nativo** — armazena os shapes brutos e dados semi-estruturados sem conversão
4. **Incremental fácil** — com `ON CONFLICT DO UPDATE` (upsert) e colunas de controle (`hash`, `updated_at`, `indexed_at`), gerenciar atualizações diárias é trivial
5. **Auditoria e reprocessamento** — se o índice OpenSearch precisar ser recriado, basta re-rodar o bot de indexação a partir do PostgreSQL sem refazer a ingestão
6. **dbt compatível** — se a equipe quiser usar dbt para as transformações SQL (opcional, mas recomendado)

---

## 3. Arquitetura proposta para o ETL do MineralRadar

```
┌─────────────────────────────────────────────────────────────────────┐
│                     FONTES DE DADOS (downloads bulk)                 │
│                                                                       │
│  ANM CSV/SHP    CPRM WFS    FUNAI SHP   IBAMA SHP   RFB CSV  MDIC   │
│  dados.gov.br   sgb.gov.br  funai.gov   mma.gov.br  bulk     comex  │
└─────────┬───────────┬──────────┬────────┬────────┬────────┬──────────┘
          │           │          │        │        │        │
          ▼           ▼          ▼        ▼        ▼        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   CAMADA DE INGESTÃO (Python Bots)                   │
│                                                                       │
│  bot_anm.py  bot_cprm.py  bot_geoquimica.py  bot_funai.py  bot_rfb.py  bot_mdic.py  │
│                                                                       │
│  Responsabilidades por bot:                                           │
│  • Download / requisição da fonte                                     │
│  • Parse do formato original (CSV, Shapefile, GeoJSON, WFS)          │
│  • Validação básica (schema, tipos, valores nulos)                    │
│  • Upsert no PostgreSQL (sem transformações de negócio aqui)          │
└─────────────────────┬───────────────────────────────────────────────┘
                      │  upsert raw
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│              PostgreSQL + PostGIS  (Staging + Transformação)         │
│                                                                       │
│  Schema: raw_*        (dados brutos sem transformação)               │
│  ├── raw_anm_processos          (tabelas ANM relacionais)            │
│  ├── raw_anm_shapes             (geometrias SIGMINE)                  │
│  ├── raw_anm_cfem               (compensação financeira)             │
│  ├── raw_anm_ral                (produção declarada)                 │
│  ├── raw_cprm_ocorrencias       (ocorrências minerais CPRM)          │
│  ├── raw_funai_ti               (terras indígenas)                   │
│  ├── raw_ibama_uc               (unidades de conservação)            │
│  ├── raw_ibama_autucoes         (autuações e embargos)               │
│  ├── raw_rfb_cnpj               (empresas Receita Federal)           │
│  └── raw_mdic_comex             (exportações/importações)            │
│                                                                       │
│  Schema: staging_*    (dados transformados, prontos para indexar)    │
│  ├── staging_processos          (join ANM + CFEM + RAL + RFB)        │
│  ├── staging_restricoes_geo     (sobreposições TI + UC + bioma)      │
│  ├── staging_ocorrencias        (CPRM enriquecido)                   │
│  └── staging_mercado            (ComexStat por substância mineral)   │
│                                                                       │
│  Views/Materialized Views para o indexador:                          │
│  └── vw_processos_completo      (view que o bot OpenSearch consome)  │
└─────────────────────┬───────────────────────────────────────────────┘
                      │  SELECT da view onde indexed_at IS NULL
                      │  ou hash mudou
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│              CAMADA DE INDEXAÇÃO (Python Bot — OpenSearch)           │
│                                                                       │
│  bot_indexador.py                                                     │
│                                                                       │
│  • Lê da view vw_processos_completo (WHERE needs_reindex = true)     │
│  • Gera embeddings (Azure OpenAI) para substâncias estratégicas      │
│  • Bulk index no OpenSearch                                           │
│  • Atualiza indexed_at e hash no PostgreSQL após sucesso             │
│  • Log de erros, retry automático                                     │
└─────────────────────┬───────────────────────────────────────────────┘
                      │  bulk index
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    OpenSearch (MineralRadar)                          │
│                                                                       │
│  anm_processos_v001     (processos ANM — ativos e inativos)          │
│  cprm_ocorrencias_v001  (ocorrências minerais)                       │
│  restricoes_geo_v001    (TIs, UCs, biomas — para sobreposição)       │
│  rfb_cnpj_v001          (empresas — dados públicos RFB)            │
│  ibge_municipio_v001    (municípios — dados públicos IBGE)          │
│  anm_substancia_v001    (substâncias + embeddings — igual)           │
│  mercado_mineral_v001   (ComexStat filtrado por NCMs minerais)       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Por que o PostgreSQL como staging agrega valor específico

### 4.1 Cálculo de sobreposições geográficas (o caso mais forte)

Calcular se um processo ANM sobrepõe uma Terra Indígena ou UC **no PostgreSQL com PostGIS** é a abordagem correta — não no OpenSearch e não em Python.

```sql
-- Exemplo: calcular sobreposição de todos os processos com TIs (PostGIS)
INSERT INTO staging_restricoes_geo (id_processo, tipo_restricao, id_restricao, area_sobreposta_ha)
SELECT
    p.id_processo,
    'terra_indigena'          AS tipo_restricao,
    ti.id_ti                  AS id_restricao,
    ST_Area(
        ST_Intersection(p.geom, ti.geom)::geography
    ) / 10000.0               AS area_sobreposta_ha   -- m² → ha
FROM raw_anm_shapes p
JOIN raw_funai_ti ti
  ON ST_Intersects(p.geom, ti.geom)
WHERE p.updated_at > NOW() - INTERVAL '1 day'  -- só processos novos/atualizados
ON CONFLICT (id_processo, tipo_restricao, id_restricao)
DO UPDATE SET area_sobreposta_ha = EXCLUDED.area_sobreposta_ha,
              updated_at = NOW();
```

Esse cálculo vai para o OpenSearch como campo pré-computado no documento do processo — sem custo de query em tempo real.

### 4.2 Controle de hash para atualização incremental

```sql
-- Controle de reindexação: só reindexar o que mudou
ALTER TABLE raw_anm_processos ADD COLUMN hash TEXT;
ALTER TABLE raw_anm_processos ADD COLUMN indexed_at TIMESTAMPTZ;

-- O indexador só pega o que precisa ser atualizado
SELECT * FROM vw_processos_completo
WHERE hash != last_indexed_hash
   OR indexed_at IS NULL;
```

### 4.3 Join entre fontes incompatíveis em formato

```sql
-- CFEM (histórico por empresa) linkado ao processo via CNPJ do titular
SELECT
    p.ds_processo,
    SUM(c.valor_cfem)           AS cfem_total_historico,
    MAX(c.dt_arrecadacao)       AS ultima_arrecadacao,
    COUNT(DISTINCT c.ano)       AS anos_producao
FROM raw_anm_processos p
JOIN raw_rfb_cnpj emp ON emp.cnpj_basico = p.cnpj_titular
JOIN raw_anm_cfem c   ON c.cnpj_empresa  = emp.cnpj_basico
GROUP BY p.ds_processo;
```

Esse join entre ANM + CFEM + RFB — impossível de fazer de forma confiável em Python puro sem staging.

---

## 5. Estratégia de carga incremental

| Frequência | O que atualizar | Como |
|---|---|---|
| **Diária** | ANM — Cadastro Mineiro (tabular) | Download do arquivo atualizado em `dados.gov.br` — o arquivo é gerado diariamente com o snapshot completo ou delta |
| **Diária** | ANM — CFEM novo | CSV em `dados.gov.br` → "CFEM ANM" |
| **Semanal** | RFB — CNPJ (situação cadastral, sócios) | Download bulk mensal, upsert semanal de ativos |
| **Mensal** | FUNAI — Terras Indígenas | Download mensal oficial |
| **Mensal** | IBAMA — CNUC (UCs) | Shapefile mensal em `dados.mma.gov.br` |
| **Mensal** | CPRM — Ocorrências Minerais | WFS com filtro de atualização |
| **Mensal** | ANM — RAL (produção anual declarada) | CSV anual, carga única por ano-base |
| **Sob demanda** | ComexStat — MDIC | Download histórico + incremental mensal |

---

## 6. Stack tecnológico sugerido para o ETL

| Componente | Tecnologia | Justificativa |
|---|---|---|
| Orquestrador de jobs | **Apache Airflow** ou **Prefect** | DAGs visuais, retry automático, alertas, monitoramento |
| Linguagem dos bots | **Python 3.12** | Consistente com o backend do MineralRadar |
| Staging / transformação | **PostgreSQL 16 + PostGIS 3.4** | Open source, melhor geo do mercado |
| Processamento de Shapefiles | **GeoPandas + Fiona** | Bibliotecas padrão para Shapefile → GeoJSON em Python |
| Processamento de CSV grande | **Polars** (ou Pandas) | Polars é 10–100x mais rápido que Pandas para arquivos grandes |
| Conexão OpenSearch | **opensearch-py** | Biblioteca open source Apache 2.0 |
| Embeddings | **Azure OpenAI** | Padrão de mercado para embeddings 1536-dim |
| Containerização | **Docker Compose** | ETL, PostgreSQL, Airflow isolados |
| Repositório | Novo repo separado | `mineral-radar-etl` |

---

## 7. Decisões de arquitetura

| Aspecto | Decisão do MineralRadar |
|---|---|
| Banco intermediário | PostgreSQL + PostGIS (open source, referência mundial em geo) |
| Fontes de dados | 10+ fontes heterogêneas (ANM, shapes, CFEM, RAL, CPRM, FUNAI, IBAMA, RFB, ComexStat) |
| Sobreposições geo | Calculadas no PostgreSQL (PostGIS) antes de indexar — campo pré-computado no OpenSearch |
| Inativos no escopo | Explicitamente parte do escopo — reativação de minas estratégicas, análise histórica |
| Controle de reindexação | Hash diferencial — só reindexar documentos que mudaram |
| Endpoint fonte ANM | `dadosabertos.anm.gov.br` (HTTP, requer User-Agent) — confirmado 05/05/2026 |
| Orquestração | Airflow ou Prefect (DAGs formais com retry, alertas, monitoramento) |
| Embeddings | Substâncias + CNAEs + Ocorrências CPRM |

---

## 8. Próximos passos para o ETL

1. ✅ **Endereço do download bulk SIGMINE confirmado** — o novo portal é `https://dadosabertos.anm.gov.br`. Protocolo: HTTP puro (não FTP), requer `User-Agent` de browser. Shapefiles: `https://dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/` — ZIPs por UF atualizados diariamente às ~00h. Arquivo Brasil completo: `BRASIL.zip` (~123MB). Inativos separados: `PROCESSOS_INATIVOS.zip` (~150MB). Cadastro Mineiro: `https://dadosabertos.anm.gov.br/SCM/microdados/microdados-scm.zip` (~313MB).
2. **Criar repositório** `mineral-radar-etl` com estrutura de pastas por bot e schema PostgreSQL
3. **Modelar o schema PostgreSQL** — definir tabelas `raw_*` e `staging_*`, chaves primárias, índices geográficos
4. **Prototipar bot_anm.py** — download de `{UF}.zip` de `dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/` → unzip + GeoPandas → PostgreSQL. Começar por `AC.zip` (~168KB) como estado menor para teste.
5. **Prototipar cálculo de sobreposições** — PostGIS: processos ANM × Terras Indígenas (FUNAI)
6. **Modelar o mapping do OpenSearch** — schema original do MineralRadar com campos: cfem, ral, restricoes_geo, categoria_mineral_estrategica, ativo
7. **Definir orquestrador** — Airflow vs Prefect (decidir com a equipe)

---

## 9. Estratégia de pré-filtro RFB (CNPJ)

### 9.1 Problema

O bulk da Receita Federal traz **~221M estabelecimentos / ~68 GB**. Indexar tudo no OpenSearch para o domínio mineral seria desperdício — o MineralRadar usa empresas apenas como contexto para processos minerários, due diligence e CFEM.

### 9.2 Estratégia: 4 critérios + fallback

```python
# compute_cnpj_filter.py — executado entre bot_anm e bot_rfb

# Critério 1 — Titulares de processos ANM
cnpjs_anm = {p["cnpj_titular_basico"] for p in raw_anm_processos
             if p["cnpj_titular_basico"]}

# Critério 2 — Indústrias Extrativas (Seção B do CNAE: 05xx-09xx)
cnpjs_mineracao = set(
    raw_rfb_estabelecimentos
    .query("cnae_principal_2dig in ['05','06','07','08','09']")
    ["cnpj_basico"]
    .unique()
)

# Critério 3 — Maiores arrecadadores CFEM (todo o histórico)
cnpjs_cfem = {c["cnpj_empresa_basico"] for c in raw_anm_cfem
              if c["cnpj_empresa_basico"]}

# Critério 4 — Sócios PJ recursivos (1 nível) das empresas dos critérios 1-3
cnpjs_base = cnpjs_anm | cnpjs_mineracao | cnpjs_cfem  # ~300K
socios_pj = (
    raw_rfb_socios
    .query("cnpj_basico in @cnpjs_base and tipo_socio == 'PJ'")
    ["cnpj_basico_socio_pj"]
    .unique()
)
cnpjs_indexar = cnpjs_base | set(socios_pj)  # ~350K total

# Persiste no PostgreSQL para o bot_rfb consumir
upsert_to_table("staging_cnpjs_relevantes", cnpjs_indexar)
```

### 9.3 Filtragem durante o parse do bulk

```python
# bot_rfb.py — lê 40GB de CSV em chunks, mas só persiste o relevante

cnpjs_filtro = set(load_from_pg("staging_cnpjs_relevantes"))

for chunk in pd.read_csv("Estabelecimentos.zip", chunksize=100_000):
    chunk["cnpj_basico"] = chunk["cnpj"].str[:8]
    relevantes = chunk[chunk["cnpj_basico"].isin(cnpjs_filtro)]
    upsert_postgres(relevantes, "raw_rfb_estabelecimentos")
```

### 9.4 Ordem obrigatória dos bots

```
[bot_anm]  →  [bot_cfem]  →  [compute_cnpj_filter]  →  [bot_rfb]  →  [bot_indexador]
```

Implementar como DAG no Airflow/Prefect com dependência explícita — sem essa ordem, o filtro fica vazio e o `bot_rfb` indexaria tudo (ou nada).

### 9.5 Fallback on-demand para CNPJs novos

Entre os refreshes mensais da RFB, novos titulares podem aparecer no ANM. Solução: lookup sob demanda no `bot_indexador.py` durante a indexação ANM:

```python
# bot_indexador.py — durante enriquecimento de cada processo

def enriquecer_titular(processo):
    cnpj = processo["cnpj_titular"]
    cache_key = f"cnpj:{cnpj}"

    empresa = redis.get(cache_key)
    if empresa:
        return empresa

    empresa = opensearch.get(index="rfb_cnpj_v001", id=cnpj, ignore=[404])
    if empresa:
        redis.setex(cache_key, 30 * 86400, empresa)
        return empresa

    # Não está no índice — lookup externo
    empresa = await brasilapi_lookup(cnpj)
    if empresa:
        opensearch.index(index="rfb_cnpj_v001", id=cnpj, body=empresa)
        redis.setex(cache_key, 30 * 86400, empresa)
    return empresa
```

### 9.6 Métricas de cobertura

```sql
-- Job periódico em etl_run_log
INSERT INTO etl_coverage_metrics (run_at, metric, value)
SELECT
    NOW(),
    'pct_titulares_anm_com_rfb',
    COUNT(DISTINCT r.cnpj_basico) * 100.0 /
        NULLIF(COUNT(DISTINCT p.cnpj_titular_basico), 0)
FROM raw_anm_processos p
LEFT JOIN raw_rfb_estabelecimentos r ON r.cnpj_basico = p.cnpj_titular_basico;

-- Alerta se cobertura cair abaixo de 95%
```

### 9.7 Fora de escopo: holdings estrangeiras

Controladoras offshore de junior miners listadas em TSX/ASX **não estão na RFB**. Tratamento separado:
- **B3-listadas** → API CVM (`dados.cvm.gov.br`) — gratuito
- **Estrangeiras** → OpenCorporates (freemium) e Tavily web search

Esses lookups vivem fora do índice `rfb_cnpj_v001`, em MCPs específicos da Fase 3.

### 9.8 Resumo de impacto

| Métrica | Sem filtro | Com filtro | Redução |
|---|---|---|---|
| Documentos | 221M | ~350K | 630x |
| Tamanho OpenSearch | ~68 GB | ~400 MB | 175x |
| Tempo de indexação | ~12h | ~10 min | 70x |
| RAM heap necessária | ~16 GB | ~1 GB | 16x |
| Custo de cluster | ~US$ 600/mês | US$ 0 (Free Tier) | ∞ |

---

## 10. Observabilidade do ETL

| Aspecto | Implementação |
|---|---|
| **Logging** | JSON estruturado por bot — `{"bot": "bot_anm", "run_id": "...", "docs_processados": 600321, "erros": 0, "duracao_s": 1840}` |
| **Métricas** | Tabela `etl_run_log` no PostgreSQL — histórico de cada execução com volume, erros, duração |
| **Alertas** | Airflow/Prefect notifica por e-mail se: (a) download falha, (b) volume de docs cai >10% vs. run anterior, (c) erros de embedding > 1% |
| **Idempotência** | Todo bot é reexecutável sem duplicação — upsert por chave primária + hash diferencial |
| **Reprocessamento** | Se o cluster OpenSearch for recriado, o `bot_indexador.py` pode re-indexar a partir do PostgreSQL sem re-baixar as fontes |

---

*Documento de especificação técnica. Sujeito a revisão após prototipagem.*
