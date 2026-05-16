# MineralRadar

Plataforma de **inteligência mineral estratégica** para o Brasil: processos ANM, geoquímica CPRM, empresas (RFB), malha ferroviária, portos, rotas logísticas e agente conversacional com mapa interativo.

[![OpenSearch](https://img.shields.io/badge/OpenSearch-3.x-005EB8)](https://opensearch.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.1xx-009688)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB)](https://react.dev/)

## O que faz

- **Chat com IA** — LangGraph orquestra tools MCP (jazidas, empresas, geo) e responde em linguagem natural com streaming SSE.
- **Mapa MapLibre** — jazidas, empresas, polígonos ANM/CPRM, rotas (Azure Maps), isócronas, portos e ferrovias (GeoJSON sob demanda).
- **Busca híbrida** — OpenSearch: full-text, `geo_shape` / `geo_distance`, embeddings onde aplicável.
- **ETL** — bots Python para ANM, CFEM, CPRM, SIGEF, SICAR, portos, ferrovias e enriquecimentos.

## Arquitetura (resumo)

```
┌──────────────┐     SSE      ┌──────────────┐    JSON-RPC    ┌─────────────────┐
│   React SPA  │ ◄──────────► │  FastAPI     │ ◄────────────► │ MCP Servers     │
│  Vite :5173  │              │  :8100       │                │ jazidas/geo/    │
└──────────────┘              └──────┬───────┘                │ empresas        │
                                     │                         └────────┬────────┘
                                     │                                  │
                    ┌────────────────┼────────────────┐                │
                    ▼                ▼                ▼                ▼
              OpenSearch         MongoDB            Redis         (tools → OS)
              :9200              :27119             :6479
```

## Estrutura do repositório

```
mineral-radar/
├── backend/                 # API FastAPI + LangGraph + MCP (Streamable HTTP)
│   ├── app/                 # rotas REST, auth Azure AD, chat SSE
│   ├── mcp_servers/         # jazidas | empresas | geo
│   ├── scripts/             # setup índices, ingest portos/ferrovias, validação OS
│   └── docker-compose.local.yml
├── frontend/                # React + Vite + MapLibre + MSAL
├── mineral-radar-etl/       # bots de ingestão → OpenSearch
├── docs/                    # especificações e cenários de uso
└── env.example              # template de variáveis (copiar para .env)
```

## Pré-requisitos

- **Docker** (OpenSearch exige `vm.max_map_count >= 262144` no host)
- **Python 3.11+** e **Node.js 20+**
- Conta **Azure OpenAI** (chat + embeddings) e, opcionalmente, **Azure Maps** (rotas/isócronas)
- **Azure AD** opcional em dev (bypass com tenant vazio — ver `env.example`)

## Setup local

### 1. Variáveis de ambiente

```bash
cp env.example .env
cp frontend/.env.example frontend/.env
# Edite .env com Azure OpenAI, etc. Nunca commite .env com segredos.
```

### 2. Infraestrutura (OpenSearch + MongoDB + Redis)

```bash
cd backend
docker compose -f docker-compose.local.yml up -d
# UI opcional: docker compose -f docker-compose.local.yml --profile dev-tools up -d
```

| Serviço    | Porta local |
|-----------|-------------|
| OpenSearch | 9200       |
| MongoDB    | 27119      |
| Redis      | 6479       |

Criar índices (primeira vez):

```bash
cd backend
source .venv/bin/activate   # ou python -m venv .venv && pip install -r requirements.txt
PYTHONPATH=. python -m scripts.setup_indices
```

Validar cluster:

```bash
PYTHONPATH=. python scripts/validate_opensearch_cluster.py
```

### 3. Backend API

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8100
```

MCP servers (em terminais separados, se não estiverem embutidos no fluxo unificado):

```bash
# Portas padrão: jazidas 8110, empresas 8111, geo 8112
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
# http://localhost:5173
```

### 5. ETL (dados)

Ver [`mineral-radar-etl/README.md`](mineral-radar-etl/README.md). Exemplo:

```bash
cd mineral-radar-etl
pip install -r requirements.txt
# Bots por fonte: bot_anm_direto, bot_cfem, bot_cprm, bot_ferrovias (via backend/scripts), etc.
```

Ingestão de malha ferroviária (shapefile ANTT):

```bash
cd backend
PYTHONPATH=. python -m scripts.ingest_ferrovias --zip-path /caminho/malha-ferroviaria-federal-shp.zip
```

## Documentação

| Documento | Conteúdo |
|-----------|----------|
| [`docs/SPEC_MINERALRADAR.md`](docs/SPEC_MINERALRADAR.md) | Visão do produto, personas, escopo |
| [`docs/SPEC_ETL_MINERALRADAR.md`](docs/SPEC_ETL_MINERALRADAR.md) | Pipeline ETL e fontes |
| [`docs/BASES_DADOS_MINERALRADAR.md`](docs/BASES_DADOS_MINERALRADAR.md) | Bases e schemas |
| [`docs/INDICES_OPENSEARCH_ATUAL.md`](docs/INDICES_OPENSEARCH_ATUAL.md) | Índices `mr_*` |
| [`docs/CENARIOS_PERGUNTAS_MINERALRADAR.md`](docs/CENARIOS_PERGUNTAS_MINERALRADAR.md) | Cenários de chat / mapa |
| [`docs/WHITEPAPER_MINERALRADAR.md`](docs/WHITEPAPER_MINERALRADAR.md) | Whitepaper técnico |

## Stack

| Camada | Tecnologia |
|--------|------------|
| API | FastAPI, Pydantic |
| Agente | LangGraph, Azure OpenAI |
| Tools | MCP (Python SDK), servidores dedicados |
| Busca | OpenSearch 3.x (texto, geo, k-NN) |
| Cache / sessão | Redis |
| App data | MongoDB |
| Frontend | React 18, Vite, TypeScript, Tailwind, Radix |
| Mapas | MapLibre GL JS, Azure Maps (rotas) |
| ETL | GeoPandas, Polars, bots Click |

## Segurança

- Autenticação via **Microsoft Entra ID (Azure AD)** em produção.
- Arquivos `.env`, chaves e certificados estão no `.gitignore` — use apenas `env.example` como referência.

## Clonar (repositório privado)

O repositório no GitHub é **privado**. Use SSH ou HTTPS com token pessoal (PAT):

```bash
# SSH (recomendado)
git clone git@github.com:Leonardo-demeloweb/ai-mineral-radar.git

# HTTPS
git clone https://github.com/Leonardo-demeloweb/ai-mineral-radar.git
# GitHub pedirá usuário + PAT (não use a senha da conta)
```

Configure o remote em um clone existente:

```bash
git remote add origin https://github.com/Leonardo-demeloweb/ai-mineral-radar.git
# ou, se origin já existir:
git remote set-url origin https://github.com/Leonardo-demeloweb/ai-mineral-radar.git
```

## Licença

Código proprietário — uso restrito aos mantenedores do projeto. Consulte os mantenedores antes de redistribuir.

---

**Repositório:** [github.com/Leonardo-demeloweb/ai-mineral-radar](https://github.com/Leonardo-demeloweb/ai-mineral-radar)
