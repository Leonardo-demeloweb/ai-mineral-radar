# mineral-radar-etl

Pipeline de ingestão de dados para o **MineralRadar** — plataforma de inteligência mineral estratégica.

Transforma 10+ fontes públicas heterogêneas (ANM, CPRM, FUNAI, IBAMA, RFB, MDIC) em documentos indexados no OpenSearch, usando **PostgreSQL + PostGIS** como camada de staging e transformação geoespacial.

---

## Arquitetura

```
Fontes públicas (Shapefiles, CSVs, WFS)
    ↓ bots de ingestão (Python)
PostgreSQL 16 + PostGIS 3.4   ← staging + joins + sobreposições geo
    ↓ bot_indexador.py
OpenSearch                    ← busca, filtros geo, embeddings k-NN
```

### Schemas PostgreSQL

| Schema | Conteúdo |
|--------|----------|
| `raw_*` | Dados brutos ingeridos pelos bots, sem transformação de negócio |
| `staging_*` | Dados transformados, enriquecidos e prontos para indexação |
| Views | `vw_processos_completo` — consumida pelo `bot_indexador.py` |

### Tabelas `raw_*`

| Tabela | Fonte | Volume |
|--------|-------|--------|
| `raw_anm_shapes` | SIGMINE (Shapefiles/ZIPs) | ~600K ativos + ~24M inativos |
| `raw_anm_processos` | Cadastro Mineiro SCM (CSV) | ~600K |
| `raw_anm_cfem` | CFEM Arrecadação (CSV) | ~8M linhas |
| `raw_anm_ral` | RAL Produção (CSV anual) | ~200K |
| `raw_cprm_ocorrencias` | GeoBank WFS | ~50K |
| `raw_funai_ti` | Terras Indígenas (Shapefile) | ~750 polígonos |
| `raw_ibama_uc` | CNUC Unidades de Conservação | ~2.3K polígonos |
| `raw_ibama_autucoes` | Autuações (CSV) | ~700K |
| `raw_rfb_estabelecimentos` | CNPJ bulk (pré-filtrado) | ~350K |
| `raw_rfb_socios` | Sócios bulk (pré-filtrado) | ~1M |
| `raw_mdic_comex` | ComexStat NCMs minerais | ~5M linhas |
| `raw_ibge_municipios` | Municípios + polígonos | 5.570 |
| `raw_ibge_biomas` | Biomas brasileiros | 6 |

### Tabelas `staging_*`

| Tabela | Conteúdo |
|--------|----------|
| `staging_processos` | Processos ANM desnormalizados + flag `needs_reindex` |
| `staging_restricoes_geo` | Sobreposições pré-computadas (PostGIS) |
| `staging_ocorrencias` | CPRM enriquecido |
| `staging_mercado` | ComexStat filtrado por NCMs minerais |
| `staging_cnpjs_relevantes` | Pré-filtro RFB (~350K CNPJs) |

---

## Pré-requisitos

- Docker + Docker Compose v2
- Python 3.12+ (somente para desenvolvimento local sem Docker)

---

## Setup rápido

```bash
# 1. Copiar e editar variáveis de ambiente
cp .env.example .env

# 2. Subir PostgreSQL + PostGIS (inicializa o schema automaticamente)
docker compose up -d postgres

# 3. Verificar que o banco subiu e o schema foi criado
docker compose exec postgres psql -U mineralradar -d mineralradar -c "\dt raw_*"

# 4. (Opcional) Abrir pgAdmin em http://localhost:5050
docker compose --profile dev up -d pgadmin
```

---

## Executando os bots

### Via Docker (recomendado)

```bash
# Testar com o Acre — menor arquivo (~168KB)
docker compose --profile run run --rm etl-runner \
    python -m bots.bot_anm --uf AC

# Ingestão CFEM completa
docker compose --profile run run --rm etl-runner \
    python -m bots.bot_cfem

# Indexar no OpenSearch
docker compose --profile run run --rm etl-runner \
    python -m bots.bot_indexador --batch-size 500
```

### Localmente (desenvolvimento)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL="postgresql://mineralradar:mineralradar_secret@localhost:5432/mineralradar"

python -m bots.bot_anm --uf AC
```

---

## Ordem obrigatória dos bots (DAG)

```
[bot_anm]
    ↓
[bot_cfem]
    ↓
[bot_funai]  [bot_ibama]  [bot_cprm]  [bot_geoquimica → OpenSearch direto]
    ↓
[compute_cnpj_filter]   ← calcula staging_cnpjs_relevantes
    ↓
[bot_rfb]
    ↓
[bot_indexador]         ← lê vw_processos_completo WHERE needs_reindex = TRUE
```

> **`bot_geoquimica`** grava em `mr_geoquimica_v001` sem depender do fluxo PostgreSQL → `bot_indexador`; pode rodar em paralelo após `python -m scripts.setup_indices --index mr_geoquimica_v001` no backend.

---

## Fontes de dados

| Bot | URL | Frequência |
|-----|-----|-----------|
| `bot_anm` | `dadosabertos.anm.gov.br/SIGMINE/` | Diária |
| `bot_cfem` | `dadosabertos.anm.gov.br/CFEM/` | Diária |
| `bot_funai` | `gov.br/funai` (WFS) | Mensal |
| `bot_ibama` | `dados.mma.gov.br` (CNUC) | Mensal |
| `bot_cprm` | `geoservicos.sgb.gov.br/ogcapi` (GeoBank `recursos-minerais`) | Mensal |
| `bot_geoquimica` | `geoservicos.sgb.gov.br/ogcapi` (`geologia/geoquimica/*`) → **`mr_geoquimica_v001`** | Mensal |
| `bot_rfb` | `dados.rfb.gov.br` (bulk ~40GB) | Mensal |
| `bot_mdic` | `api-comexstat.mdic.gov.br` | Mensal |

> **Nota:** O ArcGIS REST da ANM (`geo.anm.gov.br`) é camada de visualização de mapa — **não é fonte de ETL**. Toda a ingestão usa os arquivos bulk de `dadosabertos.anm.gov.br`.

---

## Controle de reindexação incremental

Cada tabela `raw_*` tem uma coluna `hash` (xxhash64 do registro). A tabela `staging_processos` tem uma coluna computada `needs_reindex` que é `TRUE` quando `hash != last_indexed_hash` ou `indexed_at IS NULL`. O `bot_indexador.py` só processa os documentos marcados, tornando o refresh diário muito eficiente.

---

## Migrations

As migrations SQL ficam em `db/migrations/` e são executadas automaticamente pelo PostgreSQL na primeira inicialização via `db/init/00_run_migrations.sh`.

Para rodar manualmente:
```bash
docker compose exec postgres bash -c \
    "for f in /migrations/*.sql; do psql -U mineralradar -d mineralradar -f \$f; done"
```

---

## Estrutura do repositório

```
mineral-radar-etl/
├── docker-compose.yml          # PostgreSQL 16 + PostGIS 3.4 + pgAdmin
├── Dockerfile                  # Imagem Python 3.12 + GDAL
├── requirements.txt
├── .env.example
├── db/
│   ├── init/
│   │   └── 00_run_migrations.sh
│   └── migrations/
│       ├── 001_extensions.sql  # PostGIS, uuid-ossp, pg_trgm
│       ├── 002_raw_schema.sql  # 12 tabelas raw_*
│       ├── 003_staging_schema.sql
│       ├── 004_views.sql       # vw_processos_completo + MVs
│       └── 005_indexes.sql     # índices GIST geo + GIN arrays
├── bots/
│   ├── common/
│   │   ├── settings.py         # Pydantic Settings v2
│   │   ├── db.py               # psycopg3 helpers
│   │   ├── logging.py          # structlog JSON
│   │   └── hashing.py          # xxhash para controle incremental
│   ├── bot_anm.py              # SIGMINE Shapefiles
│   ├── bot_cfem.py             # CFEM CSV (Polars)
│   ├── bot_funai.py            # Terras Indígenas + sobreposições PostGIS
│   ├── bot_cprm.py             # CPRM GeoBank → mr_cprm_v001 (OGC API)
│   ├── bot_geoquimica.py       # CPRM Geoquímica → mr_geoquimica_v001 (OGC API)
│   ├── bot_indexador.py        # OpenSearch bulk index (processos ANM)
│   └── ...                     # bot_ibama, bot_rfb, bot_mdic, etc.
└── scripts/
    └── compute_cnpj_filter.py  # Pré-filtro RFB: 221M → ~350K CNPJs
```
