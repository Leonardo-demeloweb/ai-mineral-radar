# MineralRadar — Especificação Técnica de Arquitetura

**Versão:** 1.0  
**Data:** maio de 2026  
**Status:** Referência de implementação

---

## 1. Visão Geral

O **MineralRadar** é uma plataforma de inteligência mineral estratégica construída sobre quatro pilares:

1. **Agente IA conversacional** — orquestra buscas complexas em linguagem natural via LangGraph + MCP
2. **Busca híbrida** — combina full-text, embeddings semânticos e geolocalização (OpenSearch)
3. **Dados atualizados automaticamente** — ETL automatizado de ANM, CFEM, FUNAI e IBGE
4. **Gestão de projetos e análises** — CRUD de projetos minerários com análises de fornecedores vinculadas

---

## 2. Por que Busca Híbrida? (Full-text + Embeddings + Geo)

### O Problema do Full-text Search Isolado

| Busca       | Query do Usuário                        | Resultado Full-text                       | Problema                                             |
| ----------- | --------------------------------------- | ----------------------------------------- | ---------------------------------------------------- |
| Semântica   | "mineral para baterias"                 | ❌ Não encontra "lítio", "grafita", "cobalto" | Não entende sinônimos/contexto                   |
| Geoespacial | "jazidas perto do projeto"              | ❌ Não processa geometrias               | Só texto, sem cruzamento espacial                    |
| Combinada   | "terra rara de alta pureza no Nordeste" | ❌ Parcial                               | Não ranqueia por relevância semântica + distância    |

### A Solução: Busca Híbrida (OpenSearch)

```
┌─────────────────────────────────────────────────────────────────────────┐
│          QUERY: "jazida de nióbio para exportação próxima a Araxá"      │
│          LOCALIZAÇÃO: Projeto em Araxá/MG                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  1️⃣ FULL-TEXT          2️⃣ EMBEDDING           3️⃣ GEO_SHAPE            │
│  ─────────────         ─────────────          ─────────────            │
│  match: "nióbio"       knn: vetor da          geo_distance:            │
│  match: "exportação"   query (1536 dims)      50km do ponto            │
│                        encontra:               geo_shape:               │
│                        - "Nb2O5"              intersecção com          │
│                        - "pirocloro"          área de influência       │
│                        - "columbita"                                   │
│         ↓                    ↓                      ↓                   │
│      score: 0.7          score: 0.85           score: 0.92             │
│                                                                         │
│                    ┌─────────────────────┐                              │
│                    │  SCORE COMBINADO    │                              │
│                    │  (0.7+0.85+0.92)/3  │                              │
│                    │     = 0.82          │                              │
│                    └─────────────────────┘                              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Embeddings por Domínio

| Domínio      | Campos Vetorizados                                        | Benefício                                                               |
| ------------ | --------------------------------------------------------- | ----------------------------------------------------------------------- |
| **Jazidas**  | `substância + uso + fase + titular + município`           | "mineral para baterias" → encontra lítio, grafita, cobalto              |
| **Empresas** | `atividade_principal + cnae_descricao`                    | "beneficiadora de minério de ferro" → encontra CNAEs correlatos         |
| **Municípios** | `nome + mesorregião + características`                  | "Vale do Jequitinhonha" → encontra todos os municípios da região        |

### Resumo de Capacidades

| Capacidade                | Full-text | + Embeddings | + Geo |
| ------------------------- | --------- | ------------ | ----- |
| Busca exata por termo     | ✅        | ✅           | ✅    |
| Sinônimos e contexto      | ❌        | ✅           | ✅    |
| Similaridade semântica    | ❌        | ✅           | ✅    |
| Filtro por raio/distância | ❌        | ❌           | ✅    |
| Cruzamento de polígonos   | ❌        | ❌           | ✅    |
| Ranking multi-fator       | ❌        | Parcial      | ✅    |

---

## 3. Arquitetura do Sistema

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              MINERALRADAR — ARQUITETURA                                  │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                           CAMADA DE APRESENTAÇÃO                                │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                 │   │
│  │  │   Web App       │  │   Mobile App    │  │   Chatbot       │                 │   │
│  │  │   (React + Vite)│  │   (futuro)      │  │   (futuro)      │                 │   │
│  │  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘                 │   │
│  └───────────┼────────────────────┼────────────────────┼────────────────────────── ┘  │
│              └────────────────────┼────────────────────┘                               │
│                               REST / SSE                                               │
│                                   ↓                                                    │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                           CAMADA DE ORQUESTRAÇÃO                                │   │
│  │  ┌────────────────────────────────────────────────────────────────────────┐     │   │
│  │  │                    API Gateway (FastAPI)                               │     │   │
│  │  │  • Autenticação via Azure AD                                           │     │   │
│  │  │  • Rate Limiting · Request Validation · Logging                        │     │   │
│  │  │  • Rotas: /projetos, /analises, /chat, /geo, /health                   │     │   │
│  │  └──────────────────────────────┬─────────────────────────────────────────┘     │   │
│  │                                 │                                               │   │
│  │  ┌──────────────────────────────┴─────────────────────────────────────────┐     │   │
│  │  │                    LangGraph Orchestrator                              │     │   │
│  │  │  • Router Agent (classifica intenção: mineral/empresa/geo/geral)       │     │   │
│  │  │  • State Machine com AgentState                                        │     │   │
│  │  │  • Tool calling via MCP Protocol                                       │     │   │
│  │  │  • Memória de curto prazo (Redis) + longo prazo (MongoDB)              │     │   │
│  │  └──────────────────────────────┬─────────────────────────────────────────┘     │   │
│  └─────────────────────────────────┼───────────────────────────────────────────────┘   │
│                           MCP Protocol                                                  │
│              ┌──────────────────────┼──────────────────────┐                           │
│              ↓                      ↓                      ↓                           │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                      CAMADA DE SERVIÇOS (MCP Servers)                           │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                          │   │
│  │  │ MCP: Jazidas │  │ MCP: Empresas│  │ MCP: Geo     │                          │   │
│  │  │              │  │              │  │              │                          │   │
│  │  │ • busca geo  │  │ • CNPJ lookup│  │ • municípios │                          │   │
│  │  │ • filtros ANM│  │ • CNAE filter│  │ • geocoding  │                          │   │
│  │  │ • polígonos  │  │ • sócios     │  │ • rotas/iso  │                          │   │
│  │  │ • detalhes   │  │ • histórico  │  │ • raio busca │                          │   │
│  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                          │   │
│  └─────────┼─────────────────┼─────────────────┼──────────────────────────────────┘   │
│            └─────────────────┴─────────────────┘                                       │
│                                       ↓                                                │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                           CAMADA DE DADOS                                       │   │
│  │                                                                                 │   │
│  │  ┌──────────────────────────────────────────────────────────────────────────┐   │   │
│  │  │                         OpenSearch Cluster                               │   │   │
│  │  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │   │   │
│  │  │  │   jazidas   │  │  empresas   │  │ municipios  │  │   cnaes     │     │   │   │
│  │  │  │ geo_shape   │  │ geo_point   │  │ geo_shape   │  │ text/knn    │     │   │   │
│  │  │  │ knn_vector  │  │ knn_vector  │  │ hierarchy   │  │             │     │   │   │
│  │  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘     │   │   │
│  │  └──────────────────────────────────────────────────────────────────────────┘   │   │
│  │                                                                                 │   │
│  │  ┌────────────────────────┐  ┌────────────────────────────────────────────┐     │   │
│  │  │        Redis           │  │              MongoDB                       │     │   │
│  │  │  • Conversation buffer │  │  • projetos, analises, fornecedores        │     │   │
│  │  │  • Session cache       │  │  • chat_sessions, user_memory              │     │   │
│  │  │  • Rate limiting       │  │  • Audit logs                              │     │   │
│  │  └────────────────────────┘  └────────────────────────────────────────────┘     │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                        │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                      CAMADA DE ETL (mineral-radar-etl)                          │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │   │
│  │  │  bot_anm.py │  │bot_cfem.py  │  │bot_funai.py │  │bot_indexador│           │   │
│  │  │ Shapefiles  │  │ CFEM/royalt.│  │ TI/UC sobrep│  │ OpenSearch  │           │   │
│  │  │ Processos   │  │ Arrecadação │  │ GeoJSON     │  │ Embeddings  │           │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘           │   │
│  │         └────────────────┴────────────────┴────────────────┘                  │   │
│  │                                   ↓                                            │   │
│  │                      ┌─────────────────────────┐                               │   │
│  │                      │   PostgreSQL + PostGIS   │                               │   │
│  │                      │   (staging / raw data)   │                               │   │
│  │                      └─────────────────────────┘                               │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                        │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Modelo de Dados (MongoDB)

### Collections

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              ESTRUTURA MONGODB                                          │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                         │
│  ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐               │
│  │    USUARIOS     │◄──────│    PROJETOS     │──────►│  CHAT_SESSIONS  │               │
│  │                 │       │                 │       │                 │               │
│  └─────────────────┘       └────────┬────────┘       └─────────────────┘               │
│                                     │                                                   │
│                                     ▼                                                   │
│                            ┌─────────────────┐                                          │
│                            │    ANALISES     │                                          │
│                            │                 │                                          │
│                            └────────┬────────┘                                          │
│                                     │                                                   │
│                      ┌──────────────┼──────────────┐                                    │
│                      ↓             ↓              ↓                                    │
│               ┌───────────┐  ┌───────────┐  ┌───────────┐                              │
│               │FORNECEDORES│  │ ROTAS     │  │USER_MEMORY│                              │
│               │(embedded) │  │(calculadas│  │(long-term)│                              │
│               └───────────┘  └───────────┘  └───────────┘                              │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

### Schemas

```javascript
// ==================== COLLECTION: projetos ====================
{
  "_id": ObjectId("..."),
  "nome": "Mineração Serra Azul — Fase 2",
  "tipo": "mineracao", // mineracao|pesquisa_mineral|licenciamento|lavra|
                       // beneficiamento|infraestrutura|logistica|ambiental|
                       // industrial|outro
  "status": "em_andamento", // planejamento|em_andamento|pausado|concluido|cancelado
  "localizacao": { "lat": -15.8267, "lon": -42.9833 },
  "endereco": "Serra Azul, MG",
  "municipio": "Serra Azul de Minas",
  "uf": "MG",
  "raio_busca_km": 150,
  "total_analises": 3,
  "created_at": ISODate("2026-05-01T10:00:00Z"),
  "updated_at": ISODate("2026-05-06T14:00:00Z")
}

// ==================== COLLECTION: analises ====================
{
  "_id": ObjectId("..."),
  "projeto_id": ObjectId("ref:projetos"),
  "titulo": "Fornecedores de Nióbio — Raio 200km",
  "categoria": "material_mineracao", // material_mineracao|produto_comercial|
                                     // servico|hibrido
  "termo_busca": "nióbio pirocloro",
  "status": "em_analise", // rascunho|em_analise|concluida|arquivada
  "filtros": {
    "ufs": ["MG", "GO"],
    "raio_km": 200,
    "centro_busca": { "lat": -15.8267, "lon": -42.9833 },
    "filtros_cnpj": { "incluir_mei": false }
  },
  "fornecedores": [
    {
      "id": "832.145/2018",
      "tipo_fonte": "anm",      // anm|cnpj|manual
      "nome": "Mineração CBMM",
      "favorito": true,
      "processo_anm": "832.145/2018",
      "substancia": "Nióbio",
      "fase": "Concessão de Lavra",
      "situacao": "Ativo",
      "localizacao": { "lat": -18.7392, "lon": -46.5281 },
      "municipio": "Araxá",
      "uf": "MG",
      "distancia_km": 142.3,
      "topico": "Nióbio Pirocloro",
      "adicionado_em": ISODate("2026-05-06T15:00:00Z")
    }
  ],
  "total_fornecedores": 1,
  "created_at": ISODate("2026-05-06T10:00:00Z"),
  "updated_at": ISODate("2026-05-06T15:00:00Z")
}

// ==================== COLLECTION: chat_sessions ====================
{
  "_id": ObjectId("..."),
  "session_id": "uuid-v4",
  "user_id": "dev-user",
  "projeto_id": ObjectId("ref:projetos"),   // contexto opcional
  "analise_id": ObjectId("ref:analises"),   // contexto opcional
  "summary": "Usuário buscou jazidas de nióbio em MG e selecionou CBMM",
  "key_entities": {
    "jazidas": ["832.145/2018"],
    "empresas": [],
    "substancias": ["nióbio", "pirocloro"],
    "municipios": ["Araxá", "Serra Azul de Minas"]
  },
  "messages": [
    { "role": "user", "content": "...", "timestamp": "..." },
    { "role": "assistant", "content": "...", "timestamp": "..." }
  ],
  "created_at": ISODate("2026-05-06T10:00:00Z"),
  "updated_at": ISODate("2026-05-06T15:30:00Z")
}

// ==================== COLLECTION: user_memory ====================
{
  "_id": ObjectId("..."),
  "user_id": "dev-user",
  "projeto_id": ObjectId("ref:projetos"),
  "summary": "Resumo da sessão gerado por LLM",
  "facts": {
    "substancias_interesse": ["nióbio", "terras raras", "lítio"],
    "regioes_foco": ["MG", "GO", "PA"],
    "projetos_ativos": ["Mineração Serra Azul — Fase 2"],
    "preferencias": "Prefere jazidas com fase Concessão de Lavra"
  },
  "created_at": ISODate("2026-05-06T16:00:00Z")
}
```

### Índices MongoDB

```javascript
db.projetos.createIndex({ status: 1, updated_at: -1 })
db.projetos.createIndex({ municipio: "text", nome: "text" })
db.analises.createIndex({ projeto_id: 1, status: 1 })
db.analises.createIndex({ "fornecedores.id": 1 })
db.chat_sessions.createIndex({ user_id: 1, updated_at: -1 })
db.chat_sessions.createIndex({ projeto_id: 1 })
db.user_memory.createIndex({ user_id: 1, projeto_id: 1 })
```

---

## 5. LangGraph — Estado e Fluxo do Agente

### AgentState

```python
class AgentState(TypedDict):
    # Input
    messages: list[BaseMessage]
    session_id: str
    user_id: str

    # Context injetado
    projeto_id: str | None
    analise_id: str | None
    projeto_context_str: str | None
    analise_context_str: str | None

    # Roteamento
    intent_route: str | None   # mineral | empresa | geo | general

    # Output
    response: str | None
    tool_calls: list[dict]
    error: str | None
```

### Grafo de Estados

```
┌────────────────────────────────────────────────────────────────────────┐
│                         LANGGRAPH FLOW                                 │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   START                                                                │
│     │                                                                  │
│     ▼                                                                  │
│  ┌──────────────────┐                                                  │
│  │  inject_context  │  ← carrega projeto + analise do MongoDB          │
│  └────────┬─────────┘                                                  │
│           │                                                            │
│           ▼                                                            │
│  ┌──────────────────┐                                                  │
│  │   router_node    │  ← classifica intenção com LLM leve              │
│  └────────┬─────────┘                                                  │
│           │                                                            │
│    ┌──────┴──────┬──────────────┬────────────┐                        │
│    ▼             ▼              ▼            ▼                        │
│ mineral      empresa           geo        general                     │
│    │             │              │            │                        │
│    └──────┬───────┘              │            │                        │
│           ▼                      ▼            ▼                        │
│  ┌──────────────────┐   ┌──────────────┐  ┌──────────────┐            │
│  │   agent_node     │   │  agent_node  │  │  agent_node  │            │
│  │ (tools: jazidas  │   │(tools: geo)  │  │ (no tools)   │            │
│  │  + empresas)     │   └──────┬───────┘  └──────┬───────┘            │
│  └────────┬─────────┘          │                 │                    │
│           │                    │                 │                    │
│           ▼                    ▼                 ▼                    │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      post_tool_node                              │  │
│  │  • auto-salva fornecedores na analise (se analise_id presente)   │  │
│  │  • emite SSE: analise_updated                                    │  │
│  └────────────────────────────────┬─────────────────────────────────┘  │
│                                   │                                    │
│                                   ▼                                    │
│                                  END                                   │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

### System Prompt (base)

```
Você é o assistente IA do MineralRadar — plataforma de inteligência mineral
estratégica para projetos de mineração e cadeia de suprimentos no Brasil.
Data de hoje: {today}.

CONTEXTO ATIVO:
{projeto_context}
{analise_context}

MEMÓRIA DO USUÁRIO:
{user_memory}

Você tem acesso a:
- Jazidas ANM: ~500K processos com geometrias, substâncias, titulares, fases
- Empresas CNPJ: prestadoras de serviço e fornecedores industriais
- Geo: municípios, rotas, isócronas, geocoding

REGRAS:
1. Responda sempre em português do Brasil
2. Quando buscar jazidas, priorize fase "Concessão de Lavra" e "Licenciamento"
3. Quando não souber uma coordenada, resolva via ferramenta municipio antes de buscar
4. Nunca invente processos ANM — use apenas dados retornados pelas ferramentas
5. Se o usuário perguntar sobre preços de minerais, baseie-se em dados públicos disponíveis
```

---

## 6. SSE — Eventos de Streaming

O endpoint `POST /api/v1/chat/stream` produz os seguintes eventos SSE:

| Evento           | Payload                                             | Ação no Frontend                              |
| ---------------- | --------------------------------------------------- | --------------------------------------------- |
| `meta`           | `{session_id, route, route_reasoning}`              | Exibe badge de rota no painel de pensamento   |
| `token`          | `{text}`                                            | Concatena texto na mensagem do assistente     |
| `tool_start`     | `{name, call_id}`                                   | Adiciona step "running" no painel             |
| `tool_end`       | `{name, call_id}`                                   | Marca step como "done"                        |
| `map_data`       | `{tool, tipo: "jazida"\|"empresa", pontos: [...]}`   | Plota marcadores no mapa                      |
| `route_data`     | `{polyline, distancia_m, duracao_s}`                | Adiciona polyline de rota                     |
| `isochrone_data` | `{feature: GeoJSONFeature}`                         | Renderiza polígono de isócrona                |
| `pin_data`       | `{lat, lon, label, ...}`                            | Plota pin de endereço                         |
| `analise_updated`| `{analise_id}`                                      | Invalida cache React Query da análise         |
| `done`           | `{session_id, response, route, tool_calls_count}`   | Finaliza mensagem, sincroniza tópicos do mapa |
| `error`          | `{message}`                                         | Exibe erro no chat                            |

---

## 7. Pipeline ETL

### Robôs de Extração

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                              mineral-radar-etl                                       │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │  bot_anm.py — Agência Nacional de Mineração                                  │    │
│  │                                                                              │    │
│  │  Fontes:                                                                     │    │
│  │  • https://app.anm.gov.br/dadosabertos/SIGMINE/PROCESSOS_MINERARIOS/*.zip   │    │
│  │  • Shapefiles por UF (ativos e inativos)                                    │    │
│  │                                                                              │    │
│  │  Schedule: Diário às 02:00 UTC                                              │    │
│  │  Output: raw/anm/{date}/brasil_ativos.parquet + geometrias.geojson           │    │
│  └─────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │  bot_cfem.py — Compensação Financeira pela Exploração Mineral               │    │
│  │                                                                              │    │
│  │  Fontes:                                                                     │    │
│  │  • https://sistemas.anm.gov.br/arrecadacao/extra/relatorios/cfem_*.csv      │    │
│  │  • Arrecadação por substância, município, titular                           │    │
│  │                                                                              │    │
│  │  Schedule: Mensal (dia 5 às 03:00 UTC)                                      │    │
│  │  Output: raw/cfem/{date}/arrecadacao.parquet                                │    │
│  └─────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │  bot_funai.py — Sobreposições Territoriais                                   │    │
│  │                                                                              │    │
│  │  Fontes:                                                                     │    │
│  │  • FUNAI — Terras Indígenas (GeoJSON)                                       │    │
│  │  • ICMBio — Unidades de Conservação (GeoJSON)                               │    │
│  │                                                                              │    │
│  │  Schedule: Semanal (domingo às 04:00 UTC)                                   │    │
│  │  Output: raw/ti/{date}/terras_indigenas.geojson                             │    │
│  │           raw/uc/{date}/unidades_conservacao.geojson                        │    │
│  └─────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────────────┐    │
│  │  bot_indexador.py — Indexação OpenSearch                                     │    │
│  │                                                                              │    │
│  │  1. Lê dados processados do PostgreSQL/PostGIS                               │    │
│  │  2. Gera embeddings (OpenAI text-embedding-3-small, 1536 dims)              │    │
│  │  3. Bulk index no OpenSearch (jazidas, empresas, municipios)                 │    │
│  │  4. Calcula sobreposições TI/UC por jazida                                  │    │
│  │                                                                              │    │
│  │  Schedule: Após bot_anm (trigger)                                           │    │
│  └─────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                      │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### Transformação ANM (exemplo)

```python
@task
def clean_and_validate(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf[gdf.geometry.is_valid]
    gdf["substancia"]      = gdf["SUBS"].str.upper().str.strip()
    gdf["fase"]            = gdf["FASE"].str.upper()
    gdf["numero_processo"] = gdf["PROCESSO"].str.replace(r"[^\d/]", "", regex=True)
    gdf["centroid"]        = gdf.geometry.centroid
    gdf["latitude"]        = gdf.centroid.y
    gdf["longitude"]       = gdf.centroid.x
    gdf["area_ha"]         = gdf.geometry.to_crs(epsg=32723).area / 10000
    return gdf

@task
def generate_embeddings(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    def embed_text(row):
        return (
            f"Processo minerário de {row['substancia']} em fase {row['fase']}. "
            f"Localizado em {row['municipio']}/{row['uf']}. "
            f"Área: {row['area_ha']:.1f} ha. Titular: {row['titular']}."
        )
    texts = gdf.apply(embed_text, axis=1).tolist()
    # batch 2000 por request
    embeddings = batch_embed(texts, model="text-embedding-3-small")
    gdf["embedding"] = embeddings
    return gdf
```

### Mapping OpenSearch — índice `jazidas`

```python
JAZIDAS_MAPPING = {
    "settings": {
        "index": { "knn": True, "knn.algo_param.ef_search": 100 }
    },
    "mappings": {
        "properties": {
            "numero_processo": { "type": "keyword" },
            "substancia":      { "type": "keyword" },
            "substancias":     { "type": "keyword" },
            "fase":            { "type": "keyword" },
            "situacao":        { "type": "keyword" },
            "titular":         { "type": "text", "analyzer": "brazilian" },
            "area_ha":         { "type": "float" },
            "municipio":       { "type": "text", "analyzer": "brazilian" },
            "uf":              { "type": "keyword" },
            "location": { "type": "geo_point" },
            "geometry": { "type": "geo_shape" },
            "embedding": {
                "type": "knn_vector",
                "dimension": 1536,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "lucene",
                    "parameters": { "ef_construction": 256, "m": 16 }
                }
            },
            "sobrepoe_ti": { "type": "boolean" },
            "sobrepoe_uc": { "type": "boolean" },
            "cfem_anual":  { "type": "float" },
            "data_atualizacao": { "type": "date" },
            "ativo": { "type": "boolean" }
        }
    }
}
```

---

## 8. Sistema de Memória

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                         SISTEMA DE MEMÓRIA                                           │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  CURTO PRAZO (Redis — TTL: 2h)                                                       │
│  ─────────────────────────────────────────────────────────────────────────────────   │
│  buffer:{session_id}:messages   → lista das últimas 50 mensagens da sessão           │
│  cache:search:{query_hash}      → resultados cacheados de buscas (TTL: 1h)           │
│  cache:embedding:{text_hash}    → vetores gerados (TTL: 24h)                        │
│                                                                                      │
│  LONGO PRAZO (MongoDB — sem TTL)                                                     │
│  ─────────────────────────────────────────────────────────────────────────────────   │
│  chat_sessions   → histórico completo das sessões com resumo gerado por LLM          │
│  user_memory     → fatos do usuário: substâncias de interesse, regiões, preferências │
│                                                                                      │
│  FLUXO DE SUMARIZAÇÃO (background, após cada turno)                                  │
│  ─────────────────────────────────────────────────────────────────────────────────   │
│  1. Lê mensagens do buffer Redis                                                     │
│  2. LLM gera summary + extrai entidades (jazidas, empresas, substâncias)             │
│  3. Persiste em chat_sessions (MongoDB)                                              │
│  4. Atualiza user_memory com novos fatos do usuário                                  │
│                                                                                      │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Busca Híbrida — Query OpenSearch

```python
async def hybrid_search(
    query_text: str,
    query_embedding: list[float],
    location: dict | None = None,   # {"lat": -15.8, "lon": -43.0}
    radius_km: int | None = None,
    geometry: dict | None = None,   # GeoJSON para intersecção
    filters: dict | None = None,    # {"substancia": ["nióbio"], "uf": ["MG"]}
    limit: int = 20,
) -> list[dict]:

    query = { "size": limit, "query": { "bool": { "must": [], "filter": [], "should": [] } } }

    # 1. Full-text (BM25)
    query["query"]["bool"]["should"].append({
        "multi_match": {
            "query": query_text,
            "fields": ["substancia^3", "titular^2", "municipio", "descricao_completa"],
            "type": "best_fields", "fuzziness": "AUTO"
        }
    })

    # 2. k-NN semântico
    query["query"]["bool"]["should"].append({
        "knn": { "embedding": { "vector": query_embedding, "k": limit * 2 } }
    })

    # 3. Filtro geo_distance (raio)
    if location and radius_km:
        query["query"]["bool"]["filter"].append({
            "geo_distance": { "distance": f"{radius_km}km", "location": location }
        })

    # 4. Filtro geo_shape (polígono / intersecção)
    if geometry:
        query["query"]["bool"]["filter"].append({
            "geo_shape": { "geometry": { "shape": geometry, "relation": "intersects" } }
        })

    # 5. Filtros de atributos
    if filters:
        for field, values in filters.items():
            query["query"]["bool"]["filter"].append(
                { "terms": { field: values } } if isinstance(values, list)
                else { "term": { field: values } }
            )

    response = client.search(index="jazidas", body=query)
    return [{ **hit["_source"], "score": hit["_score"] } for hit in response["hits"]["hits"]]
```

---

## 10. Stack Tecnológica

### Backend

| Componente       | Tecnologia                  | Justificativa                                   |
| ---------------- | --------------------------- | ----------------------------------------------- |
| API              | **FastAPI**                 | Performance async, OpenAPI automático            |
| Agente IA        | **LangGraph**               | Fluxos com estado, tool calling, streaming       |
| Protocolo Tools  | **MCP SDK (Python)**        | Desacoplamento, multi-canal futuro               |
| Autenticação     | **Azure AD (MSAL)**         | SSO corporativo                                  |
| LLM              | **Azure OpenAI (GPT-4o)**   | Qualidade + conformidade de dados                |
| Embeddings       | **text-embedding-3-small**  | 1536 dims, custo-benefício                       |

### Dados

| Componente         | Tecnologia         | Uso                                            |
| ------------------ | ------------------ | ---------------------------------------------- |
| Search + Geo + Vec | **OpenSearch**     | jazidas, empresas, municípios, busca híbrida    |
| Cache / Buffer     | **Redis**          | Conversas, sessões, rate limiting               |
| Aplicação          | **MongoDB**        | projetos, analises, chat_sessions, user_memory  |
| ETL Staging        | **PostgreSQL + PostGIS** | Transformações geo, sobreposições TI/UC   |

### Frontend

| Componente   | Tecnologia                    | Uso                                        |
| ------------ | ----------------------------- | ------------------------------------------ |
| Framework    | **React 18 + Vite**           | SPA, HMR, TypeScript                       |
| Mapas        | **MapLibre GL JS + OSM**      | Renderização, polígonos, clusters          |
| Estado       | **Zustand + TanStack Query**  | Estado global + cache de API               |
| Routing      | **React Router v6**           | /workspace, /projetos, /analises           |
| UI           | **Radix UI + Tailwind CSS**   | Acessibilidade, tema dark/light            |

### ETL

| Componente      | Tecnologia             | Uso                                            |
| --------------- | ---------------------- | ---------------------------------------------- |
| Processamento   | **GeoPandas + Shapely**| Shapefiles ANM, sobreposições                  |
| Transformação   | **Polars**             | CSV/Parquet grande volume                      |
| Containerização | **Docker**             | Isolamento de ambiente por bot                 |
| Orquestração    | **Prefect / Airflow**  | DAGs, retry, monitoramento                     |

---

## 11. Endpoints da API

```
GET  /api/v1/health

# Projetos
GET    /api/v1/projetos              ?search=&page=&page_size=
POST   /api/v1/projetos
GET    /api/v1/projetos/{id}
PUT    /api/v1/projetos/{id}
DELETE /api/v1/projetos/{id}         ?cascade=true

# Análises
GET    /api/v1/analises              ?projeto_id=&page=&page_size=
POST   /api/v1/analises
GET    /api/v1/analises/{id}
PUT    /api/v1/analises/{id}
DELETE /api/v1/analises/{id}
POST   /api/v1/analises/{id}/fornecedores
DELETE /api/v1/analises/{id}/fornecedores/{fid}  ?tipo_fonte=

# Chat
POST   /api/v1/chat/stream           (SSE)

# Geo (utilitários)
GET    /api/v1/geo/municipios        ?q=&uf=
GET    /api/v1/geo/municipio/{id}
POST   /api/v1/geo/geocode
```

---

## 12. Roadmap de Implementação

| Marco | Semana | Entrega                                | Critério de Aceite                              |
| ----- | ------ | -------------------------------------- | ----------------------------------------------- |
| M1    | S1–2   | Infraestrutura: OpenSearch + Redis + MongoDB | Todos os serviços ativos                   |
| M2    | S3–4   | ETL ANM em produção                    | 500K+ jazidas indexadas, busca funcionando      |
| M3    | S5–6   | Backend: API + MCP + LangGraph         | Endpoints testáveis via Swagger                 |
| M4    | S7–8   | Frontend base: workspace + mapa        | Login, mapa, chat SSE, projetos CRUD            |
| M5    | S9–10  | Integração completa                    | Chat + mapa + análises + fornecedores salvos    |
| M6    | S11–12 | ETL CFEM + FUNAI + sobreposições TI/UC | Dados de royalties e sobreposições disponíveis  |
| M7    | S13–16 | Produção                               | Sistema estável, monitorado, usuários ativos    |

---

## 13. Estimativa de Custos (Mensal)

| Componente  | Serviço                              | Custo Estimado   |
| ----------- | ------------------------------------ | ---------------- |
| OpenSearch  | AWS (m5.large.search × 2)            | ~$300            |
| Redis       | AWS ElastiCache (cache.t3.medium)    | ~$50             |
| MongoDB     | Atlas M10 ou DocumentDB              | ~$60             |
| LLM         | Azure OpenAI (chat + embeddings)     | ~$100–500        |
| Compute     | AWS ECS/Fargate (backend + MCPs)     | ~$150            |
| ETL         | EC2 t3.medium (bots periódicos)      | ~$30             |
| **Total**   |                                      | **~$690–1090/mês** |

_Valores variam conforme volume de dados e requisições de LLM._
