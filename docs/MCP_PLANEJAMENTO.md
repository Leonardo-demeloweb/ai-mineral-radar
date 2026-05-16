# 🔌 MCP Servers - Planejamento de Implementação

## 📋 Índice

1. [O que é MCP?](#1-o-que-é-mcp)
2. [Por que usar MCP no MineralRadar?](#2-por-que-usar-mcp-no-supplyradar)
3. [Como funciona efetivamente?](#3-como-funciona-efetivamente)
4. [Ganhos esperados](#4-ganhos-esperados)
5. [Arquitetura proposta](#5-arquitetura-proposta)
6. [Abordagem Híbrida: Tools Nativas + Customizadas](#6-abordagem-híbrida-tools-nativas--customizadas)
7. [MCPs planejados](#7-mcps-planejados)
8. [Integração com LangGraph](#8-integração-com-langgraph)
9. [Plano de implementação](#9-plano-de-implementação)

---

## 1. O que é MCP?

### 1.1 Definição

**MCP (Model Context Protocol)** é um protocolo aberto criado pela Anthropic que padroniza como aplicações fornecem **contexto e ferramentas** para modelos de linguagem (LLMs). 

Pense no MCP como uma **"USB universal para IA"**: assim como USB permite que qualquer dispositivo conecte em qualquer computador, o MCP permite que qualquer fonte de dados ou ferramenta seja consumida por qualquer agente de IA.

### 1.2 Analogia Prática

```
┌─────────────────────────────────────────────────────────────────────┐
│                         SEM MCP (Antes)                             │
│                                                                     │
│   LLM/Agent ──┬── Código específico para OpenSearch                │
│               ├── Código específico para MongoDB                    │
│               ├── Código específico para API externa X              │
│               └── Código específico para cada nova integração...    │
│                                                                     │
│   Problema: Cada integração é única, não padronizada, difícil      │
│   de manter e não reutilizável entre projetos.                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         COM MCP (Depois)                            │
│                                                                     │
│   LLM/Agent ── MCP Protocol ──┬── MCP Server: Jazidas              │
│                               ├── MCP Server: Empresas             │
│                               ├── MCP Server: Geo                  │
│                               └── Qualquer MCP Server novo...      │
│                                                                     │
│   Solução: Interface padronizada! Qualquer ferramenta pode ser     │
│   plugada sem alterar o código do agente.                          │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.3 Componentes do MCP

| Componente      | Descrição                                                   |
| --------------- | ----------------------------------------------------------- |
| **MCP Host**    | O "cliente" - aplicação que precisa de contexto (ex: LangGraph, Claude Desktop) |
| **MCP Server**  | O "servidor" - serviço que expõe Tools, Resources e Prompts |
| **Tools**       | Ações executáveis (ex: `buscar_jazidas`, `calcular_rota`)   |
| **Resources**   | Dados somente-leitura (ex: lista de UFs, tabela CNAE)       |
| **Prompts**     | Templates de prompts reutilizáveis                          |

### 1.4 Transporte

O MCP suporta três modos de comunicação:

| Modo                  | Uso                                    | Transporte               | Status |
| --------------------- | -------------------------------------- | ------------------------ | ------ |
| **stdio**             | Servidores locais, subprocessos        | stdin/stdout (JSON-RPC)  | Estável |
| **SSE/HTTP**          | Servidores remotos (legado)            | HTTP GET (SSE) + POST    | Depreciando |
| **Streamable HTTP** ✅ | Servidores remotos, microsserviços    | HTTP POST (JSON ou SSE)  | **Recomendado** |

Para o MineralRadar, usaremos **Streamable HTTP** — o transport recomendado pela spec MCP (v1.26+). Vantagens sobre SSE:

- **1 endpoint** (`POST /mcp`) vs 2 rotas (SSE: `GET /sse` + `POST /messages/`)
- **Stateless** — cada POST é independente, melhor para containers e load balancers
- **Modo JSON puro** — para tools request-response, retorna JSON direto sem overhead SSE
- **Resumability** — client retoma com `Last-Event-Id` se conexão cair
- **Proxy-friendly** — funciona melhor atrás de Azure App Gateway, Nginx, etc.

> **Nota**: O OpenSearch 3.x com MCP nativo habilitado (`plugins.ml_commons.mcp_server_enabled`) também expõe endpoint MCP compatível, permitindo conexão via MCP Client padrão.

---

## 2. Por que usar MCP no MineralRadar?

### 2.1 Problema Atual (Sistema Blazor)

```
┌─────────────────────────────────────────────────────────────────────┐
│   Blazor Server (Monolito)                                         │
│   ┌───────────────────────────────────────────────────────────┐     │
│   │  Plugin C# Jazidas ────────────────────────────────────┐  │     │
│   │  Plugin C# Empresas ───────────────────────────────────┤  │     │
│   │  Plugin C# Geo ────────────────────────────────────────┤  │     │
│   │                                                        │  │     │
│   │  ❌ Não reutilizáveis fora do Blazor                   │  │     │
│   │  ❌ Sem API REST documentada                           │  │     │
│   │  ❌ Impossível integrar com chatbots, mobile, etc.     │  │     │
│   └────────────────────────────────────────────────────────┘  │     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Solução com MCP (MineralRadar 2.0)

```
┌─────────────────────────────────────────────────────────────────────┐
│   Ecossistema MineralRadar 2.0 — Protocolo MCP Unificado            │
│                                                                     │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│   │ Web App      │  │ Mobile App   │  │ Chatbot Teams│             │
│   │ (React)      │  │ (futuro)     │  │ (futuro)     │             │
│   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘             │
│          │                 │                 │                      │
│          └────────────────┬┴─────────────────┘                      │
│                           │                                         │
│                    ┌──────┴──────┐                                  │
│                    │ LangGraph   │ (Orquestrador IA)                │
│                    │ MCP Client  │ (Streamable HTTP)                │
│                    └──────┬──────┘                                  │
│                           │ MCP Protocol (unificado)                │
│       ┌───────────────────┼───────────────────┐                     │
│       │                   │                   │                     │
│  ┌────┴─────┐       ┌────┴────┐        ┌────┴────┐                 │
│  │OpenSearch │       │MCP Server│       │MCP Server│                │
│  │MCP Nativo │       │ Jazidas  │       │ Empresas │                │
│  │(ML Commons)│      │ (Python) │       │ (Python) │                │
│  │QPT, PPL,  │       └────┬────┘        └────┬────┘                 │
│  │Search...  │            │                   │                     │
│  └────┬─────┘             └───────────┬───────┘                     │
│       │                               │ opensearch-py (data)        │
│       └───────────────────────────────┘                             │
│                    ┌──────┴──────┐                                  │
│                    │ OpenSearch  │                                  │
│                    │ Cluster    │                                  │
│                    │ + Redis    │                                  │
│                    └─────────────┘                                  │
│                                                                     │
│   ✅ Cada MCP é independente e reutilizável                        │
│   ✅ OpenSearch é tratado como mais um MCP Server no pool          │
│   ✅ Tool discovery automático (list_tools()) em todos os servers  │
│   ✅ Bidirecional: Dashboard → Nossas tools Python                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Como funciona efetivamente?

### 3.1 Ciclo de Vida de uma Chamada MCP

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        CICLO DE VIDA MCP                                   │
│                                                                            │
│  1. DESCOBERTA (initialize + list_tools)                                   │
│  ┌─────────────────┐         ┌─────────────────┐                          │
│  │ LangGraph       │ ─POST──→│ MCP Server      │                          │
│  │ MCP Client      │  /mcp   │ Jazidas (:8010) │                          │
│  │ "Quais tools    │ ←JSON── │ "Tenho:         │                          │
│  │  você expõe?"   │         │  - buscar_fornecedores                     │
│  │                 │         │  - buscar_jazidas                          │
│  │                 │         │  - detalhes_processo                       │
│  │                 │         │  - jazidas_por_poligono                    │
│  │                 │         │  - verificar_vigencia"                     │
│  └─────────────────┘         └─────────────────┘                          │
│                                                                            │
│  2. EXECUÇÃO (tools/call)                                                  │
│  ┌─────────────────┐         ┌─────────────────┐         ┌───────────┐    │
│  │ LangGraph       │ ─POST──→│ MCP Server      │ ──────→ │ OpenSearch│    │
│  │ MCP Client      │  /mcp   │ Jazidas         │ search  │ (HTTP REST│    │
│  │ "call_tool:     │         │                 │ ←─────  │  via lib) │    │
│  │  buscar_jazidas │ ←JSON── │ Executa query   │         └───────────┘    │
│  │  substancia:areia│        │ retorna dados   │                          │
│  │  raio: 50km"    │         │ formatados      │                          │
│  └─────────────────┘         └─────────────────┘                          │
│                                                                            │
│  3. RESULTADO → LLM                                                        │
│  ┌─────────────────┐         ┌─────────────────┐                          │
│  │ LangGraph       │ ──────→ │ LLM (GPT-4/     │                          │
│  │ "Aqui estão     │         │      Claude)    │                          │
│  │  os dados da    │         │ "Encontrei 23   │                          │
│  │  ferramenta"    │ ←─────  │  jazidas de     │                          │
│  │                 │         │  areia próximas │                          │
│  └─────────────────┘         │  à sua obra..." │                          │
│                              └─────────────────┘                          │
└────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Estrutura de uma Tool MCP

Cada Tool é definida com:

```json
{
  "name": "buscar_jazidas",
  "description": "Busca jazidas minerais por substância e localização geográfica",
  "inputSchema": {
    "type": "object",
    "properties": {
      "substancia": {
        "type": "string",
        "description": "Tipo de substância mineral (ex: areia, brita, granito)"
      },
      "latitude": {
        "type": "number",
        "description": "Latitude do ponto central de busca"
      },
      "longitude": {
        "type": "number",
        "description": "Longitude do ponto central de busca"
      },
      "raio_km": {
        "type": "number",
        "description": "Raio de busca em quilômetros",
        "default": 50
      },
      "limite": {
        "type": "integer",
        "description": "Número máximo de resultados",
        "default": 20
      }
    },
    "required": ["substancia"]
  }
}
```

### 3.3 O que o LLM "vê"?

Quando o LangGraph conecta ao MCP Server, o LLM recebe essa informação no contexto:

```
Você tem acesso às seguintes ferramentas:

1. buscar_jazidas - Busca jazidas minerais por substância e localização geográfica
   Parâmetros:
   - substancia (obrigatório): Tipo de substância mineral
   - latitude: Latitude do ponto central
   - longitude: Longitude do ponto central
   - raio_km: Raio de busca (padrão: 50km)
   - limite: Máximo de resultados (padrão: 20)

2. detalhes_jazida - Obtém informações detalhadas de uma jazida específica
   Parâmetros:
   - id (obrigatório): ID único da jazida

3. ...
```

O LLM então decide **quando e como** usar essas ferramentas baseado na pergunta do usuário.

### 3.4 Exemplo de Conversa Real

```
┌─────────────────────────────────────────────────────────────────────┐
│ Usuário: "Preciso encontrar fornecedores de areia lavada perto da  │
│          obra Rodoanel Norte, num raio de 30km"                    │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ LLM (raciocínio interno):                                          │
│                                                                     │
│ 1. O usuário quer encontrar fornecedores de areia lavada           │
│ 2. Preciso da localização da obra "Rodoanel Norte"                 │
│ 3. Vou usar a tool "buscar_obra" para obter coordenadas            │
│ 4. Depois vou usar "buscar_jazidas" com raio=30km                  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ LLM → Tool Call 1:                                                 │
│ {                                                                   │
│   "tool": "buscar_obra",                                           │
│   "arguments": { "nome": "Rodoanel Norte" }                        │
│ }                                                                   │
│                                                                     │
│ → Resposta: { "lat": -23.45, "lon": -46.78, "id": "obra123" }      │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ LLM → Tool Call 2:                                                 │
│ {                                                                   │
│   "tool": "buscar_jazidas",                                        │
│   "arguments": {                                                   │
│     "substancia": "areia lavada",                                  │
│     "latitude": -23.45,                                            │
│     "longitude": -46.78,                                           │
│     "raio_km": 30                                                  │
│   }                                                                 │
│ }                                                                   │
│                                                                     │
│ → Resposta: [                                                       │
│   { "nome": "Areial São José", "dist": 12km, "status": "ativo" },  │
│   { "nome": "Extração Minas", "dist": 18km, "status": "ativo" },   │
│   ...                                                               │
│ ]                                                                   │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Resposta para o usuário:                                           │
│                                                                     │
│ "Encontrei 5 fornecedores de areia lavada num raio de 30km da      │
│  obra Rodoanel Norte:                                              │
│                                                                     │
│  1. **Areial São José** - 12km - Status: Ativo                     │
│     Produção: 5.000 m³/mês                                         │
│                                                                     │
│  2. **Extração Minas** - 18km - Status: Ativo                      │
│     Produção: 3.200 m³/mês                                         │
│  ..."                                                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Ganhos Esperados

### 4.1 Tabela Comparativa

| Aspecto                | Sem MCP (Atual)                          | Com MCP (Proposto)                     |
| ---------------------- | ---------------------------------------- | -------------------------------------- |
| **Reutilização**       | Zero - código embarcado no Blazor        | 100% - MCP é padrão de mercado         |
| **Integrações**        | Apenas web Blazor                        | Web, mobile, chatbot, API B2B          |
| **Manutenção**         | Alterar plugin = rebuild do monolito     | Alterar MCP = deploy isolado           |
| **Testabilidade**      | Difícil - acoplado ao Blazor             | Fácil - cada MCP é testável isolado    |
| **Escalabilidade**     | Escala tudo junto (caro)                 | Escala individualmente por serviço     |
| **Time to Market**     | Lento - dependências entre equipes       | Rápido - equipes trabalham em paralelo |
| **Documentação**       | Manual, desatualizada                    | Auto-gerada pelo MCP SDK               |
| **Compatibilidade IA** | Requer adaptação para cada LLM           | Universal - qualquer LLM com tool call |

### 4.2 Benefícios Específicos para o MineralRadar

```
┌─────────────────────────────────────────────────────────────────────┐
│                    GANHOS IMEDIATOS (S5-S6)                         │
│                                                                     │
│  📦 Modularidade                                                    │
│     └── MCP Jazidas pode ser atualizado sem afetar MCP Empresas    │
│                                                                     │
│  🔌 Plug-and-Play                                                   │
│     └── Adicionar nova fonte de dados = criar novo MCP Server      │
│                                                                     │
│  🧪 Testabilidade                                                   │
│     └── Cada MCP Server é testável isoladamente com curl/Postman   │
│                                                                     │
│  📖 Documentação                                                    │
│     └── Tools auto-documentadas (Swagger-like para MCPs)           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    GANHOS FUTUROS (S7+)                             │
│                                                                     │
│  🤖 Integração LangGraph                                            │
│     └── Agente decide automaticamente qual MCP usar                │
│                                                                     │
│  🔄 Multi-step Reasoning                                            │
│     └── LLM encadeia chamadas: Obra → Jazidas → Rotas → Relatório  │
│                                                                     │
│  📱 Multi-canal                                                     │
│     └── Mesmo MCP serve: Web, Mobile, Teams Bot, API B2B           │
│                                                                     │
│  🌐 Ecossistema                                                     │
│     └── Compartilhar MCPs entre projetos AG (outros sistemas)      │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.3 Métricas de Sucesso

| Métrica                          | Meta                                 |
| -------------------------------- | ------------------------------------ |
| Tempo para adicionar nova tool   | < 2 horas (vs dias no sistema atual) |
| Cobertura de testes              | > 90% por MCP Server                 |
| Latência média de tool call      | < 200ms (p95)                        |
| Disponibilidade                  | 99.5% por MCP Server                 |
| Reuso em outros projetos         | 3+ projetos usando mesmos MCPs       |

---

## 5. Arquitetura Proposta

> 📌 **Nota**: A arquitetura adota a **Abordagem Híbrida** descrita na [Seção 6](#6-abordagem-híbrida-tools-nativas--customizadas),
> combinando tools nativas do OpenSearch ML Commons (exploração) com tools customizadas em Python (negócio).

### 5.1 Visão Geral

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            ARQUITETURA MCP                                  │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                        CAMADA DE APRESENTAÇÃO                       │    │
│  │  ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐     │    │
│  │  │  React    │   │  Mobile   │   │  Teams    │   │  API B2B  │     │    │
│  │  │  Web App  │   │  (futuro) │   │   Bot     │   │  Partners │     │    │
│  │  └─────┬─────┘   └─────┬─────┘   └─────┬─────┘   └─────┬─────┘     │    │
│  └────────┼───────────────┼───────────────┼───────────────┼───────────┘    │
│           │               │               │               │                 │
│           └───────────────┴───────┬───────┴───────────────┘                 │
│                                   │                                         │
│  ┌────────────────────────────────┼────────────────────────────────────┐    │
│  │                         CAMADA DE API                               │    │
│  │                                │                                    │    │
│  │  ┌─────────────────────────────┴─────────────────────────────────┐  │    │
│  │  │                    FastAPI Gateway                            │  │    │
│  │  │  /api/v1/obras      → CRUD direto (MongoDB)                   │  │    │
│  │  │  /api/v1/estudos    → CRUD direto (MongoDB)                   │  │    │
│  │  │  /api/v1/chat       → LangGraph (orquestra MCPs)              │  │    │
│  │  │  /api/v1/search/*   → Proxy para MCPs (uso direto)            │  │    │
│  │  └───────────────────────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                   │                                         │
│  ┌────────────────────────────────┼────────────────────────────────────┐    │
│  │                      CAMADA DE ORQUESTRAÇÃO                         │    │
│  │                                │                                    │    │
│  │  ┌─────────────────────────────┴─────────────────────────────────┐  │    │
│  │  │                    LangGraph Orchestrator                     │  │    │
│  │  │                                                               │  │    │
│  │  │  • Router Agent: Decide qual fluxo usar                       │  │    │
│  │  │  • State Manager: Mantém contexto da conversa                 │  │    │
│  │  │  • Tool Executor: Chama MCPs via protocolo                    │  │    │
│  │  │  • Memory: Redis (curto prazo) + MongoDB (longo prazo)        │  │    │
│  │  └───────────────────────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                   │ MCP Protocol (Streamable HTTP)          │
│           ┌───────────────────────┼───────────────────────┐                 │
│           │                       │                       │                 │
│  ┌────────┼───────────────────────┼───────────────────────┼────────────┐    │
│  │        ▼                       ▼                       ▼            │    │
│  │  CAMADA DE SERVIÇOS (MCP Servers)                                   │    │
│  │                                                                     │    │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │    │
│  │  │  MCP: Jazidas   │  │  MCP: Empresas  │  │  MCP: Geo       │     │    │
│  │  │  (port 8010)    │  │  (port 8011)    │  │  (port 8012)    │     │    │
│  │  │                 │  │                 │  │                 │     │    │
│  │  │  Tools:         │  │  Tools:         │  │  Tools:         │     │    │
│  │  │  • buscar       │  │  • buscar_cnpj  │  │  • buscar_muni  │     │    │
│  │  │  • detalhes     │  │  • filtrar_cnae │  │  • calcular_rota│     │    │
│  │  │  • listar_subs  │  │  • detalhes     │  │  • geocodificar │     │    │
│  │  │  • geo_intersect│  │  • socios       │  │  • reverse_geo  │     │    │
│  │  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘     │    │
│  └───────────┼────────────────────┼────────────────────┼───────────────┘    │
│              │                    │                    │                    │
│              └────────────────────┼────────────────────┘                    │
│                                   │                                         │
│  ┌────────────────────────────────┼────────────────────────────────────┐    │
│  │                        CAMADA DE DADOS                              │    │
│  │                                │                                    │    │
│  │  ┌──────────────────┐  ┌──────┴───────┐  ┌──────────────────┐      │    │
│  │  │    OpenSearch    │  │   MongoDB    │  │     Redis        │      │    │
│  │  │   (MCP Nativo)   │  │              │  │                  │      │    │
│  │  │  • anm_v002      │  │  • obras     │  │  • sessions      │      │    │
│  │  │    (25M docs)    │  │  • estudos   │  │  • memory        │      │    │
│  │  │  • cnpj_v001     │  │  • users     │  │  • cache         │      │    │
│  │  │    (221M docs)   │  │  • uploads   │  │  • rate_limit    │      │    │
│  │  │  • ibge_municipio│  │              │  │                  │      │    │
│  │  │    (5.6K+geoshape│  │              │  │                  │      │    │
│  │  │  • rfb_cnae_v001 │  │              │  │                  │      │    │
│  │  │    (embedding!)  │  │              │  │                  │      │    │
│  │  │  • anm_substancia│  │              │  │                  │      │    │
│  │  │    (embedding!)  │  │              │  │                  │      │    │
│  │  └──────────────────┘  └──────────────┘  └──────────────────┘      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Estrutura de Diretórios

> Veja a estrutura completa e atualizada na [Seção 9.5](#95-estrutura-de-diretórios-atualizada-abordagem-híbrida),
> que inclui os módulos adicionais da abordagem híbrida (cache Redis, embeddings).

```
MineralRadar_v2/
├── backend/
│   ├── app/                          # FastAPI Gateway (existente)
│   │   ├── api/routes/               # CRUD + proxy search
│   │   └── langgraph/
│   │       └── tool_provider.py      # 🆕 UnifiedMCPProvider (conecta a TODOS os MCP Servers)
│   │
│   └── mcp_servers/                  # 🆕 MCP Servers customizados
│       ├── common/                   # Código compartilhado
│       │   ├── opensearch_client.py  # Cliente OpenSearch (HTTP REST para data queries)
│       │   ├── redis_cache.py        # Cache Redis
│       │   └── embeddings.py         # Azure OpenAI embeddings
│       │
│       ├── jazidas/                  # MCP Jazidas (port 8010) — Streamable HTTP
│       ├── empresas/                 # MCP Empresas (port 8011) — Streamable HTTP
│       └── geo/                      # MCP Geo (port 8012) — Streamable HTTP

# OpenSearch MCP Nativo (ML Commons) — endpoint do cluster, sem código nosso
# https://<os-endpoint>/_plugins/_ml/mcp/sse
# Tools: QPT, PPL, SearchIndex, ListIndex, Mapping, VectorDB, etc.
```

### 5.3 Tecnologias e Bibliotecas

| Componente           | Tecnologia                     | Justificativa                              |
| -------------------- | ------------------------------ | ------------------------------------------ |
| MCP Framework        | `mcp` (Python SDK oficial)     | SDK oficial da Anthropic, padrão de mercado |
| HTTP Server          | `starlette` (via MCP SDK)      | Async, leve, compatível com ASGI           |
| OpenSearch Client    | `opensearch-py`                | Já utilizado no projeto                    |
| Validação            | `pydantic`                     | Já utilizado, consistência                 |
| Logging              | `structlog`                    | Já utilizado, logs estruturados            |
| Testes               | `pytest` + `pytest-asyncio`    | Padrão do projeto                          |

---

## 6. Abordagem Híbrida: Tools Nativas (via MCP OpenSearch) + Customizadas

> 📌 **Decisão Arquitetural**: O cluster OpenSearch (3.x) com `mcp_server_enabled: true` expõe **21+ tools nativas** via protocolo MCP padrão.
> Em vez de criar wrappers HTTP REST, conectamos ao OpenSearch como mais um MCP Server no pool — protocolo unificado.
> Tools de negócio complexas (cross-index, semântica, cache) permanecem nos nossos MCP Servers Python.

### 6.1 Inventário de Tools Nativas (MCP OpenSearch Nativo)

O cluster OpenSearch em produção (`search-supplyradar-prod-*.aos.sa-east-1.on.aws`) com MCP nativo habilitado (`plugins.ml_commons.mcp_server_enabled: true`) expõe as seguintes tools via protocolo MCP padrão:

> **Acesso**: `MCP Client` → `POST https://<os-endpoint>/_plugins/_ml/mcp/sse` → `list_tools()` / `call_tool()`

#### 🔍 Tools de Busca e Dados

| # | Tool | Tipo | Descrição | Relevância |
|---|------|------|-----------|------------|
| 1 | **SearchIndexTool** | Busca DSL | Executa query DSL em um índice específico | ⭐⭐⭐ Alta |
| 2 | **QueryPlanningTool** | NL→DSL | Gera OpenSearch DSL a partir de linguagem natural | ⭐⭐⭐ Alta |
| 3 | **VectorDBTool** | k-NN | Executa busca densa (embedding) em índice k-NN | ⭐⭐ Média |
| 4 | **NeuralSparseSearchTool** | Sparse | Busca neural sparse (ELSER-like) | ⭐ Baixa |
| 5 | **RAGTool** | RAG | Retrieval augmented generation genérica | ⭐⭐ Média |
| 6 | **PPLTool** | PPL | Executa Piped Processing Language (queries analíticas) | ⭐⭐ Média |

#### 📋 Tools de Exploração e Mapeamento

| # | Tool | Tipo | Descrição | Relevância |
|---|------|------|-----------|------------|
| 7 | **ListIndexTool** | Catálogo | Lista todos os índices do cluster com metadados | ⭐⭐⭐ Alta |
| 8 | **IndexMappingTool** | Mapeamento | Retorna mappings e settings dos índices | ⭐⭐⭐ Alta |
| 9 | **IndexInsightTool** | Insights | Distribuição de dados, estatísticas, descrição de campos | ⭐⭐ Média |
| 10 | **DataDistributionTool** | Análise | Analisa distribuição entre ranges de tempo | ⭐ Baixa |

#### 🔔 Tools de Monitoramento e Alertas

| # | Tool | Tipo | Descrição | Relevância |
|---|------|------|-----------|------------|
| 11 | **SearchMonitorsTool** | Alertas | Busca monitors configurados | ⚪ N/A |
| 12 | **SearchAlertsTool** | Alertas | Busca alertas disparados | ⚪ N/A |
| 13 | **CreateAlertTool** | Alertas | Cria monitor com triggers | ⚪ N/A |
| 14 | **SearchAnomalyDetectorsTool** | Anomalia | Busca detectors configurados | ⚪ N/A |
| 15 | **SearchAnomalyResultsTool** | Anomalia | Busca resultados de anomalia | ⚪ N/A |
| 16 | **CreateAnomalyDetectorTool** | Anomalia | Cria detector de anomalias | ⚪ N/A |

#### 🧰 Tools de Infraestrutura e Integração

| # | Tool | Tipo | Descrição | Relevância |
|---|------|------|-----------|------------|
| 17 | **ConnectorTool** | Conector | Invoca serviços externos via connectors | ⭐ Baixa |
| 18 | **AgentTool** | Agente | Executa agentes internos do OpenSearch | ⭐ Baixa |
| 19 | **MLModelTool** | Modelo | Executa modelos ML registrados | ⭐ Baixa |
| 20 | **McpSseTool** | MCP | Integração com MCP Servers externos via SSE | ⭐⭐⭐ Alta |
| 21 | **McpStreamableHttpTool** | MCP | Integração MCP via Streamable HTTP | ⭐⭐⭐ Alta |

#### 📝 Tools Auxiliares

| # | Tool | Tipo | Descrição | Relevância |
|---|------|------|-----------|------------|
| 22 | **VisualizationTool** | Dashboard | Busca visualizações salvas | ⚪ N/A |
| 23 | **LogPatternTool** | Logs | Detecta padrões de log | ⚪ N/A |
| 24 | **LogPatternAnalysisTool** | Logs | Análise avançada de padrões | ⚪ N/A |
| 25 | **WriteToScratchPadTool** | Memória | Salva notas no scratchpad | ⭐ Baixa |
| 26 | **ReadFromScratchPadTool** | Memória | Lê notas do scratchpad | ⭐ Baixa |
| 27 | **WebSearchTool** | Web | Busca na web via search engine | ⚪ N/A |

### 6.2 Três Abordagens Possíveis

Antes de definir a estratégia, analisamos três abordagens:

#### ❌ Abordagem A: 100% Tools Nativas (via MCP OpenSearch)

```
LangGraph → MCP Client → OpenSearch MCP Nativo → QPT → SearchIndexTool

Prós: Zero código no backend, protocolo MCP unificado
Contras:
  ❌ QPT não faz busca semântica k-NN cross-index (substância → processo)
  ❌ Não faz busca híbrida em 2 passos (auxiliar → principal)
  ❌ Não integra com Redis para cache
  ❌ Respostas em JSON bruto, sem formatação de negócio
  ❌ Sem validação de parâmetros específicos do domínio
  ❌ Sem controle de rate limiting por tool
```

#### ❌ Abordagem B: 100% Tools Customizadas

```
LangGraph → MCP Servers Python → OpenSearch Client

Prós: Controle total sobre lógica
Contras:
  ❌ Reinventa funcionalidades genéricas já prontas
  ❌ Mais código para manter
  ❌ Não aproveita investimento em tools nativas do cluster
```

#### ✅ Abordagem C: Híbrida com Protocolo MCP Unificado (ADOTADA)

```
LangGraph → MCP Client → OpenSearch MCP Nativo (exploração, QPT, PPL)
                       → MCP Server Jazidas Python (negócio)
                       → MCP Server Empresas Python (negócio)

✅ Protocolo UNIFICADO: tudo via MCP (Streamable HTTP)
✅ Tools nativas via MCP nativo do OpenSearch (zero wrappers HTTP REST)
✅ Tools customizadas para lógica de negócio (busca híbrida, formatação)
✅ Tool discovery automático (list_tools() em todos os servers)
✅ Bidirecional: OpenSearch Dashboard também chama nossas tools
✅ Melhor custo-benefício: menos código + máximo controle onde importa
```

### 6.3 Decisão: Abordagem Híbrida com MCP Unificado

```
┌─────────────────────────────────────────────────────────────────────────────┐
│              ABORDAGEM HÍBRIDA — PROTOCOLO MCP UNIFICADO                    │
│                                                                             │
│   LangGraph (Orquestrador)                                                  │
│         │                                                                   │
│         │  MCP Client (Streamable HTTP)                                     │
│         │  Protocolo ÚNICO para todas as conexões                           │
│         │                                                                   │
│         ├────────────────────┬──────────────────────┐                       │
│         │                    │                      │                       │
│         ▼                    ▼                      ▼                       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │  OpenSearch   │    │  MCP Server  │    │  MCP Server  │                  │
│  │  MCP Nativo   │    │  Jazidas     │    │  Empresas    │                  │
│  │  (ML Commons) │    │  (Python)    │    │  (Python)    │                  │
│  │               │    │              │    │              │                  │
│  │  ┌──────────┐ │    │  ┌─────────┐ │    │  ┌─────────┐ │                  │
│  │  │ListIndex │ │    │  │buscar_  │ │    │  │buscar   │ │                  │
│  │  │Mapping   │ │    │  │fornec.  │ │    │  │detalhes │ │                  │
│  │  │Insight   │ │    │  │buscar_  │ │    │  │cnae     │ │                  │
│  │  │PPL       │ │    │  │jazidas  │ │    │  │socios   │ │                  │
│  │  │QueryPlan │ │    │  │detalhes │ │    │  │         │ │                  │
│  │  │SearchIdx │ │    │  │poligono │ │    │  │         │ │                  │
│  │  │VectorDB  │ │    │  │vigencia │ │    │  │         │ │                  │
│  │  └──────────┘ │    │  └─────────┘ │    │  └─────────┘ │                  │
│  │               │    │              │    │              │                  │
│  │  Conexão:     │    │  Conexão:    │    │  Conexão:    │                  │
│  │  MCP Client   │    │  MCP Client  │    │  MCP Client  │                  │
│  │  (Streamable  │    │  (Streamable │    │  (Streamable │                  │
│  │   HTTP)       │    │   HTTP)      │    │   HTTP)      │                  │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘                  │
│         │                    │                   │                          │
│         │                    │  ┌────────────────┘                          │
│         │                    │  │  (internamente: opensearch-py HTTP REST)  │
│         │                    │  │  (queries de dados, NÃO tool invocation)  │
│         └────────────────────┼──┘                                          │
│                              │                                              │
│                       ┌──────┴──────┐                                       │
│                       │  OpenSearch  │                                       │
│                       │  Cluster    │                                       │
│                       │  + Redis    │                                       │
│                       └──────┬──────┘                                       │
│                              │                                              │
│  ┌───────────────────────────┴────────────────────────────────────────────┐ │
│  │  BIDIRECIONAL (ativo):                                                │ │
│  │  OpenSearch Dashboard → McpStreamableHttpTool → MCP Jazidas (:8010)   │ │
│  │  O agente nativo do Dashboard chama nossas tools Python!              │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

> **Diferença chave vs arquitetura anterior**: Antes, as tools nativas do OpenSearch eram acessadas via HTTP REST wrappers (`httpx`). Agora, **todas as conexões** passam por MCP Client padrão — o OpenSearch é tratado como mais um MCP Server no pool. Isso elimina a classe `OpenSearchNativeTools` e unifica o protocolo.

### 6.4 Classificação Detalhada: Nativa vs Customizada

Para cada cenário operacional do MineralRadar, definimos qual tipo de tool utilizar:

#### 📊 Operações de Exploração e Debug → Tools NATIVAS

| Cenário | Tool Nativa | Justificativa |
|---------|-------------|---------------|
| "Quais índices existem no cluster?" | `ListIndexTool` | Operação genérica, sem lógica de negócio |
| "Mostre o mapeamento do anm_v001" | `IndexMappingTool` | Leitura de estrutura, nenhuma transformação |
| "Quantos documentos têm no cnpj_v001?" | `IndexInsightTool` | Estatísticas puras do índice |
| "Qual a distribuição de UFs nos dados?" | `PPLTool` | Queries analíticas ad-hoc |
| "Gere uma query DSL para buscar processos ativos" | `QueryPlanningTool` | Queries simples sem nested complexo |

> **Nota**: Estas tools são especialmente úteis para o **agente interno** (dashboard de admin),
> operações de suporte técnico, e durante desenvolvimento/debugging.

#### 🎯 Operações de Negócio → Tools CUSTOMIZADAS (Python)

| Cenário | Tool Customizada | Por que não nativa? |
|---------|------------------|---------------------|
| "Busque jazidas de areia perto de Guarulhos" | `buscar_jazidas` | Requer busca em 2 passos: semântica em `anm_substancia_v001` → nested+geo em `anm_v002` |
| "Detalhes do processo 832.145/2018" | `detalhes_processo` | Requer formatação específica, extração de campos nested, cache em Redis |
| "Empresas de transporte de minérios em SP" | `buscar_empresas` | Requer busca semântica CNAE em `rfb_cnae_v001` → geo+filtro em `cnpj_v001` |
| "Em qual município está esta coordenada?" | `municipio_por_coordenada` | Requer `geo_shape contains` com formatação específica |
| "Calcule a rota da obra até a jazida" | `calcular_rota` | Integração com OSRM externo, fora do OpenSearch |
| "Relatório de fornecedores da obra X" | `relatorio_obra` | Orquestra múltiplas queries + formatação complexa |

#### 🔑 Por que Tools Customizadas para o Core?

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  5 MOTIVOS para tools customizadas no core do negócio:                      │
│                                                                             │
│  1️⃣ BUSCA EM 2 PASSOS (cross-index)                                         │
│     QPT (via MCP nativo) NÃO sabe que precisa buscar em                    │
│     anm_substancia_v001 primeiro, obter IDs, e depois filtrar em           │
│     anm_v002. Isso é LÓGICA DE NEGÓCIO específica do MineralRadar.          │
│                                                                             │
│  2️⃣ NESTED QUERIES COMPLEXAS                                                │
│     anm_v002 ainda tem nested para substancias e poligonos.                │
│     QPT gera DSL genérico que não otimiza nested queries.                  │
│     Nossas tools montam queries cirúrgicas com inner_hits.                 │
│                                                                             │
│  3️⃣ CACHE E PERFORMANCE                                                     │
│     Tools nativas não têm cache em Redis. Nossas tools cachearão:           │
│     - Embeddings gerados (evita re-gerar para mesmos termos)                │
│     - Resultados frequentes (mesmo raio, mesma substância)                  │
│     - Mapeamentos substância→ID (tabela auxiliar)                           │
│                                                                             │
│  4️⃣ FORMATAÇÃO DE RESPOSTA                                                  │
│     SearchIndexTool retorna JSON bruto do OpenSearch. Nossas tools          │
│     formatam para o domínio: nomes amigáveis, distâncias calculadas,        │
│     campos relevantes extraídos de nested, geração de resumos.              │
│                                                                             │
│  5️⃣ INTEGRAÇÕES EXTERNAS                                                    │
│     Cálculo de rotas (OSRM), geocoding (Nominatim), geração de             │
│     embeddings (Azure OpenAI) - nada disso vive dentro do OpenSearch.       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.5 Fluxo de Decisão do Agente

Quando o LangGraph recebe uma requisição, o Router Agent decide qual caminho seguir:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ÁRVORE DE DECISÃO DO AGENTE                             │
│                                                                             │
│  Requisição do Usuário                                                      │
│         │                                                                   │
│         ▼                                                                   │
│  ┌─────────────────────┐                                                   │
│  │ É sobre estrutura/  │──SIM──→ Tools Nativas (via MCP OpenSearch Nativo) │
│  │ exploração de dados?│         • ListIndexTool, IndexMappingTool          │
│  │ (debug, análise)    │         • PPLTool, QueryPlanningTool               │
│  └─────────┬───────────┘         • SearchIndexTool, VectorDBTool            │
│            NÃO                   (auto-descobertas via list_tools())        │
│            │                                                                │
│            ▼                                                                │
│  ┌─────────────────────┐                                                   │
│  │ É sobre busca de    │──SIM──→ Tools Customizadas: MCP Jazidas           │
│  │ jazidas/processos   │         • buscar_jazidas (2 passos + geo)          │
│  │ minerários?         │         • detalhes_processo                        │
│  └─────────┬───────────┘         • buscar_por_substancia                    │
│            NÃO                   • listar_substancias                       │
│            │                                                                │
│            ▼                                                                │
│  ┌─────────────────────┐                                                   │
│  │ É sobre busca de    │──SIM──→ Tools Customizadas: MCP Empresas          │
│  │ empresas/CNPJ?      │         • buscar_empresas (CNAE semântico + geo)   │
│  │                     │         • detalhes_empresa                         │
│  └─────────┬───────────┘         • buscar_por_cnae                          │
│            NÃO                   • buscar_cnaes                             │
│            │                                                                │
│            ▼                                                                │
│  ┌─────────────────────┐                                                   │
│  │ É sobre localização,│──SIM──→ Tools Customizadas: MCP Geo               │
│  │ rotas, municípios?  │         • municipio_por_coordenada                 │
│  │                     │         • calcular_rota (OSRM)                     │
│  └─────────┬───────────┘         • geocodificar (Nominatim)                 │
│            NÃO                                                              │
│            │                                                                │
│            ▼                                                                │
│  ┌─────────────────────┐                                                   │
│  │ É sobre obras/      │──SIM──→ API REST direta (FastAPI, sem MCP)        │
│  │ estudos (CRUD)?     │         • /api/v1/obras                            │
│  └─────────┬───────────┘         • /api/v1/estudos                          │
│            NÃO                                                              │
│            │                                                                │
│            ▼                                                                │
│    Resposta direta do LLM (sem tool call)                                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.6 Integração Bidirecional: 3 Fluxos MCP Ativos

Com o MCP nativo habilitado no OpenSearch (`plugins.ml_commons.mcp_server_enabled: true`) e os MCP Servers Python rodando, temos **3 fluxos MCP ativos simultâneos**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 3 FLUXOS MCP ATIVOS (protocolo unificado)                    │
│                                                                             │
│  FLUXO 1: LangGraph → OpenSearch MCP Nativo                                │
│  ─────────────────────────────────────────                                  │
│  LangGraph conecta via MCP Client ao endpoint nativo do OpenSearch.         │
│  Tools auto-descobertas: QPT, PPL, SearchIndex, ListIndex, Mapping, etc.   │
│  Endpoint: https://<os-endpoint>/_plugins/_ml/mcp/sse                      │
│  Transport: Streamable HTTP (ou SSE como fallback)                          │
│                                                                             │
│  ┌────────────┐   MCP Client    ┌──────────────────┐                       │
│  │  LangGraph  │ ─────────────→ │  OpenSearch MCP   │                       │
│  │             │ ←───────────── │  (ML Commons)     │                       │
│  └────────────┘   list_tools()  │  QPT, PPL, Search │                       │
│                   call_tool()   └──────────────────┘                       │
│                                                                             │
│  FLUXO 2: LangGraph → MCP Servers Python                                   │
│  ────────────────────────────────────────                                   │
│  LangGraph conecta via MCP Client aos nossos servers custom.               │
│  Tools: buscar_fornecedores, buscar_jazidas, detalhes_processo, etc.       │
│  Endpoint: http://localhost:8010/mcp (Jazidas), :8011 (Empresas)           │
│  Transport: Streamable HTTP                                                 │
│                                                                             │
│  ┌────────────┐   MCP Client    ┌──────────────────┐                       │
│  │  LangGraph  │ ─────────────→ │  MCP Jazidas      │                       │
│  │             │ ←───────────── │  (Python :8010)   │                       │
│  └────────────┘   call_tool()   └──────────────────┘                       │
│                                                                             │
│  FLUXO 3: OpenSearch Dashboard → MCP Servers Python (ATIVO) ✅             │
│  ──────────────────────────────────────────────────────                     │
│  O agente nativo do OpenSearch Dashboard usa McpStreamableHttpTool          │
│  para chamar NOSSOS MCP Servers Python como tools dele.                    │
│  Mesmo backend serve LangGraph E Dashboard — experiência unificada!        │
│                                                                             │
│  Cenário: Admin no Dashboard pergunta:                                     │
│  "Quais jazidas de areia estão perto da obra Rodoanel Norte?"              │
│                                                                             │
│  ┌──────────────┐ McpStreamable  ┌─────────────────┐                       │
│  │  OpenSearch   │ HttpTool       │ MCP Server      │                       │
│  │  Dashboard    │ ────────────→  │ Jazidas (Python) │                       │
│  │  Agent        │ ←────────────  │ :8010/mcp       │                       │
│  └──────────────┘ Resultado      └─────────────────┘                       │
│                                                                             │
│  CONEXÃO INTERNA (NÃO é MCP):                                              │
│  ─────────────────────────────                                              │
│  MCP Jazidas → OpenSearch: opensearch-py (HTTP REST)                        │
│  Queries de dados (search, msearch, count) — operação de dados,            │
│  NÃO tool invocation. Permanece como HTTP REST.                            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.7 Mapa Completo: Tool → MCP Server → Conexão

| Tool | Tipo | MCP Server | Conexão LangGraph → Server | Índice(s) OpenSearch | Cache |
|------|------|------------|---------------------------|---------------------|-------|
| `ListIndexTool` | Nativa | OpenSearch MCP Nativo | **MCP Streamable HTTP** | todos | ❌ |
| `IndexMappingTool` | Nativa | OpenSearch MCP Nativo | **MCP Streamable HTTP** | todos | ❌ |
| `IndexInsightTool` | Nativa | OpenSearch MCP Nativo | **MCP Streamable HTTP** | todos | ❌ |
| `PPLTool` | Nativa | OpenSearch MCP Nativo | **MCP Streamable HTTP** | todos | ❌ |
| `QueryPlanningTool` | Nativa | OpenSearch MCP Nativo | **MCP Streamable HTTP** | todos | ❌ |
| `SearchIndexTool` | Nativa | OpenSearch MCP Nativo | **MCP Streamable HTTP** | todos | ❌ |
| `VectorDBTool` | Nativa | OpenSearch MCP Nativo | **MCP Streamable HTTP** | todos (k-NN) | ❌ |
| `buscar_fornecedores` | **Custom** | **MCP Jazidas (:8010)** | **MCP Streamable HTTP** | `anm_v002` + `cnpj_v001` | ✅ |
| `buscar_jazidas` | **Custom** | **MCP Jazidas (:8010)** | **MCP Streamable HTTP** | `anm_substancia_v001` → `anm_v002` | ✅ |
| `detalhes_processo` | **Custom** | **MCP Jazidas (:8010)** | **MCP Streamable HTTP** | `anm_v002` | ✅ |
| `jazidas_por_poligono` | **Custom** | **MCP Jazidas (:8010)** | **MCP Streamable HTTP** | `anm_v002` | ✅ |
| `verificar_vigencia_substancia` | **Custom** | **MCP Jazidas (:8010)** | **MCP Streamable HTTP** | `anm_v002` | ✅ |

> **Resumo**: 7 tools nativas (via MCP OpenSearch nativo) + 5 tools customizadas (via MCP Jazidas Python) = **12 tools** disponíveis para o agente no domínio Jazidas.
> 
> **Protocolo unificado**: Todas acessadas via MCP Client — nenhum wrapper HTTP REST necessário. O LangGraph faz `list_tools()` em cada MCP Server e descobre tools automaticamente.

---

### 6.8 Análise Detalhada: Por que NÃO podemos usar 100% Nativo (Hoje)

> 📌 **Objetivo desta seção**: Documentar com evidências técnicas concretas por que a abordagem
> 100% nativa não é viável com a estrutura atual dos índices, e propor as alterações necessárias
> no `anm_v002` para maximizar o uso de ferramentas nativas do OpenSearch.

#### 6.8.1 Inventário de Campos Nested (Dados Reais do Cluster)

##### `anm_v001` — **12 campos nested** (CRÍTICO)

| # | Campo Nested | Nível | Média/doc | Total | Impacto |
|---|-------------|-------|-----------|-------|---------|
| 1 | `substancias` | 1 | 1.20 | 1.145.529 | 🔴 Alto — campo mais buscado |
| 2 | `municipios` | 1 | 1.17 | 1.115.251 | 🔴 Alto — filtro frequente |
| 3 | `poligonos` | 1 | **1.00** | 958.682 | 🔴 Alto — geo_point + geo_shape |
| 4 | `pessoas` | 1 | 2.36 | 2.260.416 | 🟡 Médio — titular/responsável |
| 5 | `eventos` | 1 | N/A | N/A | ⚪ Baixo — raramente buscado |
| 6 | `titulos` | 1 | N/A | N/A | ⚪ Baixo — raramente buscado |
| 7 | `associacoes` | 1 | N/A | N/A | ⚪ Baixo |
| 8 | `documentacao` | 1 | N/A | N/A | ⚪ Baixo |
| 9 | `propriedadeSolo` | 1 | N/A | N/A | ⚪ Baixo |
| 10 | `pessoas.socios` | **2** | N/A | N/A | 🔴 Anti-pattern |
| 11 | `pessoas.detalhesCNPJ.socios` | **3** | N/A | N/A | 🔴 Anti-pattern grave |
| 12 | `pessoas.detalhesCNPJ.cnaeFiscalSecundaria` | **3** | N/A | N/A | 🔴 Anti-pattern grave |

##### `cnpj_v001` — **2 campos nested** (Moderado)

| # | Campo Nested | Nível | Impacto |
|---|-------------|-------|---------|
| 1 | `cnaeFiscalSecundaria` | 1 | 🟡 Médio — filtro por CNAE secundário |
| 2 | `socios` | 1 | 🟡 Médio — busca por sócio |

##### Demais índices — **0 campos nested** ✅

`ibge_municipio_v001`, `rfb_cnae_v001`, `anm_substancia_v001`, `anm_tipo-uso-substancia_v001`: todos flat, sem nested. **Já compatíveis com tools nativas.**

#### 6.8.2 Por que Nested Quebra as Tools Nativas

As tools nativas `QueryPlanningTool` e `SearchIndexTool` do ML Commons foram projetadas para queries padrão. Quando o índice tem nested, ocorrem **3 problemas concretos**:

##### Problema 1: QueryPlanningTool NÃO gera nested queries

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  TESTE REAL: Pergunta ao QueryPlanningTool                                   │
│                                                                             │
│  Input: "Busque processos com substância areia em São Paulo"                │
│                                                                             │
│  ❌ O que o QueryPlanningTool GERA (DSL incorreto):                         │
│  {                                                                          │
│    "query": {                                                               │
│      "bool": {                                                              │
│        "must": [                                                            │
│          {"match": {"substancias.Substancia.nmSubstancia": "areia"}},       │
│          {"match": {"municipios.siglaUF": "SP"}}                            │
│        ]                                                                    │
│      }                                                                      │
│    }                                                                        │
│  }                                                                          │
│  ⚠️ Resultado: 0 hits! Campo nested não pode ser buscado diretamente.       │
│                                                                             │
│  ✅ O que DEVERIA gerar (DSL correto para nested):                          │
│  {                                                                          │
│    "query": {                                                               │
│      "bool": {                                                              │
│        "must": [                                                            │
│          {                                                                  │
│            "nested": {                   ← obrigatório!                     │
│              "path": "substancias",      ← path do nested                  │
│              "query": {                                                     │
│                "match": {                                                   │
│                  "substancias.Substancia.nmSubstancia": "areia"             │
│                }                                                            │
│              }                                                              │
│            }                                                                │
│          },                                                                 │
│          {                                                                  │
│            "nested": {                   ← obrigatório de novo!             │
│              "path": "municipios",                                          │
│              "query": {                                                     │
│                "term": {"municipios.siglaUF": "SP"}                         │
│              }                                                              │
│            }                                                                │
│          }                                                                  │
│        ]                                                                    │
│      }                                                                      │
│    }                                                                        │
│  }                                                                          │
│                                                                             │
│  Diferença: 13 linhas → 31 linhas. O LLM precisa SABER que é nested.       │
└─────────────────────────────────────────────────────────────────────────────┘
```

##### Problema 2: Nested de 3 níveis é inviável via NL→DSL

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CASO EXTREMO: Buscar processo pelo sócio do titular                        │
│                                                                             │
│  Pergunta: "Processos onde um dos sócios do titular é João Silva"           │
│                                                                             │
│  ❌ QueryPlanningTool NÃO CONSEGUE gerar isso:                              │
│  {                                                                          │
│    "query": {                                                               │
│      "nested": {                              ← nível 1: pessoas           │
│        "path": "pessoas",                                                   │
│        "query": {                                                           │
│          "nested": {                          ← nível 2: detalhesCNPJ      │
│            "path": "pessoas.detalhesCNPJ",                                  │
│            "query": {                                                       │
│              "nested": {                      ← nível 3: socios            │
│                "path": "pessoas.detalhesCNPJ.socios",                       │
│                "query": {                                                   │
│                  "match": {                                                 │
│                    "pessoas.detalhesCNPJ.socios.nome": "João Silva"         │
│                  }                                                          │
│                }                                                            │
│              }                                                              │
│            }                                                                │
│          }                                                                  │
│        }                                                                    │
│      }                                                                      │
│    }                                                                        │
│  }                                                                          │
│                                                                             │
│  25 linhas de DSL para uma busca simples!                                   │
│  Nenhuma tool nativa consegue gerar isso de forma confiável.                │
└─────────────────────────────────────────────────────────────────────────────┘
```

##### Problema 3: Geo queries em nested adicionam complexidade

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  BUSCA GEO: "Jazidas num raio de 50km de São Paulo"                         │
│                                                                             │
│  ❌ SEM nested (como SearchIndexTool tentaria):                              │
│  {                                                                          │
│    "query": {                                                               │
│      "geo_distance": {                                                      │
│        "distance": "50km",                                                  │
│        "localizacao": {"lat": -23.55, "lon": -46.63}                        │
│      }                                                                      │
│    }                                                                        │
│  }                                                                          │
│  ⚠️ ERRO: campo "localizacao" não existe no root. Está em                   │
│  "poligonos.localizacao" que é NESTED!                                      │
│                                                                             │
│  ✅ COM nested (query correta):                                              │
│  {                                                                          │
│    "query": {                                                               │
│      "nested": {                                                            │
│        "path": "poligonos",                                                 │
│        "query": {                                                           │
│          "geo_distance": {                                                  │
│            "distance": "50km",                                              │
│            "poligonos.localizacao": {"lat": -23.55, "lon": -46.63}          │
│          }                                                                  │
│        }                                                                    │
│      }                                                                      │
│    }                                                                        │
│  }                                                                          │
│                                                                             │
│  Tools nativas NÃO sabem que geo_point está dentro de nested.               │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 6.8.3 Dados-chave que Viabilizam a Desnormalização

A análise de cardinalidade dos campos nested revela que **a maioria tem relação 1:1**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CARDINALIDADE REAL (Cluster Produção)                      │
│                                                                             │
│  Total docs anm_v001: 956.288 (com poligonos)                              │
│                                                                             │
│  Campo              │ Total Nested │ Média/doc │ Pode Flatten? │            │
│  ────────────────────┼──────────────┼───────────┼───────────────│            │
│  poligonos          │    958.682   │   1.00    │ ✅ SIM (1:1)  │            │
│  substancias        │  1.145.529   │   1.20    │ ✅ SIM*       │            │
│  municipios         │  1.115.251   │   1.17    │ ✅ SIM*       │            │
│  pessoas            │  2.260.416   │   2.36    │ ⚠️ PARCIAL   │            │
│                                                                             │
│  * Para substancias e municipios com média ~1.2, a maioria é 1:1.           │
│    Os casos com 2+ podem usar campos array simples (não nested).            │
│                                                                             │
│  Conclusão: 3 dos 4 campos mais importantes têm cardinalidade ~1:1          │
│  e podem ser promovidos a root-level SEM PERDA de informação.               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 6.8.4 Dados Redundantes: Já Existem em Índices Separados

```
┌─────────────────────────────────────────────────────────────────────────────┐
│            REDUNDÂNCIA IDENTIFICADA                                          │
│                                                                             │
│  Dado dentro de anm_v001 (nested)      │ Já existe em índice separado       │
│  ──────────────────────────────────────┼────────────────────────────────────│
│  substancias.Substancia.idSubstancia   │ anm_substancia_v001 (862 docs)    │
│  substancias.Substancia.nmSubstancia   │ anm_substancia_v001 (+ embedding) │
│  substancias.tipoUsoSubstancia         │ anm_tipo-uso-substancia_v001      │
│  pessoas.detalhesCNPJ.*                │ cnpj_v001 (221M docs)             │
│  pessoas.detalhesCNPJ.socios.*         │ cnpj_v001.socios (nested)         │
│  pessoas.detalhesCNPJ.cnaeFiscalSec.*  │ cnpj_v001.cnaeFiscalSecundaria    │
│  municipios.*                          │ ibge_municipio_v001 (5.6K docs)   │
│                                                                             │
│  ⚠️ TODOS os 3 níveis de nesting são dados COPIADOS de outros índices!     │
│                                                                             │
│  Não há necessidade de manter cópias nested quando temos:                   │
│  • anm_substancia_v001 → com embedding para busca semântica                │
│  • cnpj_v001 → dados completos do CNPJ                                     │
│  • ibge_municipio_v001 → com geo_shape do município                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 6.8.5 Proposta: Estrutura `anm_v002` (Flat, Otimizada para Tools Nativas)

##### Princípios da Reestruturação

| Princípio | Ação |
|-----------|------|
| **Eliminar nested** | Promover campos 1:1 para root-level |
| **Eliminar redundância** | Remover dados que existem em índices separados |
| **Manter IDs de ligação** | Guardar apenas `idSubstancia`, `cnpjBasico`, `idMunicipio` |
| **Flatten geo** | `localizacao` (geo_point) e `geom` (geo_shape) no root |
| **Arrays simples** | Quando cardinalidade > 1, usar array (não nested) |

##### Mapeamento Proposto: `anm_v002`

```json
{
  "anm_v002": {
    "mappings": {
      "properties": {
        
        "dsProcesso":        { "type": "keyword" },
        "nrProcesso":        { "type": "integer" },
        "nrAnoProcesso":     { "type": "integer" },
        "nrNUP":             { "type": "keyword" },
        "btAtivo":           { "type": "keyword" },
        "qtAreaHa":          { "type": "double" },
        "dtProtocolo":       { "type": "date" },
        "dtPrioridade":      { "type": "date" },
        
        "dsFaseProcesso":    { "type": "text", "fields": {"keyword": {"type": "keyword"}} },
        "idFaseProcesso":    { "type": "integer" },
        "dsTipoRequerimento":{ "type": "text", "fields": {"keyword": {"type": "keyword"}} },
        
        "localizacao":       { "type": "geo_point" },
        "geom":              { "type": "geo_shape" },
        
        "idSubstancias":     { "type": "integer" },
        "nmSubstancias":     { "type": "text", "analyzer": "pt_brazilian",
                               "fields": {"keyword": {"type": "keyword"}} },
        
        "idMunicipio":       { "type": "keyword" },
        "nomeMunicipio":     { "type": "text", "analyzer": "pt_brazilian" },
        "siglaUF":           { "type": "keyword" },
        
        "nmTitularPrincipal":{ "type": "text", "analyzer": "pt_brazilian",
                               "fields": {"keyword": {"type": "keyword"}} },
        "cnpjBasicoTitular": { "type": "keyword" },
        
        "nmPessoas":         { "type": "text", "analyzer": "pt_brazilian" },
        "cnpjBasicos":       { "type": "keyword" },
        
        "dsUnidadeRegional": { "type": "text", "fields": {"keyword": {"type": "keyword"}} }
      }
    }
  }
}
```

##### Comparação: anm_v001 (nested) vs anm_v002 (flat)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                anm_v001 (ATUAL)              anm_v002 (PROPOSTO)             │
│  ──────────────────────────────────────  ────────────────────────────────── │
│  substancias (nested)                    idSubstancias: [45, 67]            │
│    └─ Substancia.idSubstancia            nmSubstancias: ["Areia", "Argila"] │
│    └─ Substancia.nmSubstancia            (array simples, não nested)        │
│    └─ tipoUsoSubstancia (nested)         → tipo-uso via índice separado     │
│    └─ vigência início/fim                → não essencial para busca         │
│                                                                             │
│  poligonos (nested)                      localizacao: {lat, lon}            │
│    └─ localizacao (geo_point)            geom: {type, coordinates}          │
│    └─ geom (geo_shape) ← VAZIO!         (root-level, cardinalidade 1:1)    │
│    └─ poligonos (object) ← confuso      → geo_shape corrigido no root      │
│                                                                             │
│  municipios (nested)                     idMunicipio: "3550308"             │
│    └─ idMunicipio                        nomeMunicipio: "São Paulo"         │
│    └─ nome                               siglaUF: "SP"                      │
│    └─ siglaUF                            → dados completos via ibge_muni... │
│    └─ 15+ campos IBGE copiados                                             │
│                                                                             │
│  pessoas (nested)                        nmTitularPrincipal: "Empresa X"    │
│    └─ pessoa.nmPessoa                    cnpjBasicoTitular: "56649577"      │
│    └─ detalhesCNPJ (object)              nmPessoas: ["Pessoa1", "Pessoa2"]  │
│       └─ empresa.* (40+ campos)          cnpjBasicos: ["123", "456"]        │
│       └─ socios (nested, nível 3!)       → dados completos via cnpj_v001   │
│       └─ cnaeFiscalSec (nested, nível 3!)                                   │
│                                                                             │
│  12 campos nested                        0 campos nested ✅                 │
│  5.6 GB                                  ~2 GB estimados ✅                 │
│  Nested queries obrigatórias             Queries flat simples ✅            │
│  Tools nativas: ❌ Incompatível          Tools nativas: ✅ Compatível       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

##### Como a Busca Muda com anm_v002

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  QUERY: "Jazidas de areia em São Paulo num raio de 50km"                     │
│                                                                             │
│  ────── COM anm_v001 (nested) ──────                                        │
│  {                                                                          │
│    "query": {                                                               │
│      "bool": {                                                              │
│        "must": [                                                            │
│          {"nested": {"path": "substancias", "query":                        │
│            {"match": {"substancias.Substancia.nmSubstancia": "areia"}}       │
│          }},                                                                │
│          {"nested": {"path": "municipios", "query":                         │
│            {"term": {"municipios.siglaUF": "SP"}}                           │
│          }},                                                                │
│          {"nested": {"path": "poligonos", "query":                          │
│            {"geo_distance": {"distance": "50km",                            │
│              "poligonos.localizacao": {"lat": -23.55, "lon": -46.63}}}      │
│          }}                                                                 │
│        ],                                                                   │
│        "filter": [{"term": {"btAtivo": "s"}}]                               │
│      }                                                                      │
│    }                                                                        │
│  }                                                                          │
│  → 22 linhas | ❌ QueryPlanningTool NÃO gera                                │
│                                                                             │
│  ────── COM anm_v002 (flat) ──────                                          │
│  {                                                                          │
│    "query": {                                                               │
│      "bool": {                                                              │
│        "must": [                                                            │
│          {"match": {"nmSubstancias": "areia"}},                             │
│          {"term": {"siglaUF": "SP"}},                                       │
│          {"geo_distance": {"distance": "50km",                              │
│            "localizacao": {"lat": -23.55, "lon": -46.63}}}                  │
│        ],                                                                   │
│        "filter": [{"term": {"btAtivo": "s"}}]                               │
│      }                                                                      │
│    }                                                                        │
│  }                                                                          │
│  → 12 linhas | ✅ QueryPlanningTool CONSEGUE gerar                          │
│  → 45% menos linhas de DSL                                                  │
│  → Qualquer SearchIndexTool executa sem problemas                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 6.8.6 Impacto da Reestruturação nas Tools

| Tool Nativa | Sem Nested (anm_v002) | Com Nested (anm_v001) |
|-------------|----------------------|----------------------|
| **QueryPlanningTool** | ✅ Gera DSL correto | ❌ Não gera nested queries |
| **SearchIndexTool** | ✅ Executa qualquer query | ⚠️ Precisa de nested path correto |
| **VectorDBTool** | ✅ (se adicionar embedding) | ❌ Sem embedding |
| **PPLTool** | ✅ Queries analíticas simples | ❌ PPL não suporta nested |
| **IndexInsightTool** | ✅ Estatísticas corretas | ⚠️ Nested distorce contagens |
| **RAGTool** | ✅ Pode indexar docs flat | ❌ Nested fragmenta contexto |

#### 6.8.7 Plano de Migração: anm_v001 → anm_v002

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PLANO DE MIGRAÇÃO                                         │
│                                                                             │
│  FASE 1: Criar índice anm_v002 com mapeamento flat                          │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  • Criar mapping conforme proposta acima                            │    │
│  │  • Incluir analyzers pt_brazilian existentes                        │    │
│  │  • Configurar réplicas e shards adequados                           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  FASE 2: Script de re-indexação (ETL)                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Para cada doc em anm_v001:                                         │    │
│  │  • localizacao ← poligonos[0].localizacao                           │    │
│  │  • geom ← poligonos[0].poligonos (ou geom se preenchido)           │    │
│  │  • idSubstancias ← [s.Substancia.idSubstancia for s in substancias]│    │
│  │  • nmSubstancias ← [s.Substancia.nmSubstancia for s in substancias]│    │
│  │  • idMunicipio ← municipios[0].idMunicipio                          │    │
│  │  • nomeMunicipio ← municipios[0].nome                               │    │
│  │  • siglaUF ← municipios[0].siglaUF                                  │    │
│  │  • nmTitularPrincipal ← pessoas[0].pessoa.nmPessoa                  │    │
│  │  • cnpjBasicoTitular ← pessoas[0].detalhesCNPJ.empresa.cnpjBasico  │    │
│  │  • nmPessoas ← [p.pessoa.nmPessoa for p in pessoas]                 │    │
│  │  • cnpjBasicos ← [p.detalhesCNPJ.empresa.cnpjBasico for p ...]     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  FASE 3: Validação                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  • Comparar contagem de docs: anm_v001 vs anm_v002                  │    │
│  │  • Testar mesmas queries em ambos                                   │    │
│  │  • Validar geo_point e geo_shape                                    │    │
│  │  • Testar tools nativas contra anm_v002                             │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  FASE 4: Transição                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  • Manter anm_v001 em paralelo (leitura)                            │    │
│  │  • Migrar MCPs para apontar para anm_v002                           │    │
│  │  • Após validação completa, desativar anm_v001                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 6.8.8 Proposta Análoga para `cnpj_v002`

O `cnpj_v001` tem apenas 2 campos nested e pode ser simplificado:

| Campo Nested (v001) | Proposta (v002) | Justificativa |
|---------------------|-----------------|---------------|
| `cnaeFiscalSecundaria` | `codigosCnaeSecundarios` (array keyword) | Array simples de códigos basta |
| `socios` | `nomesSocios` (array text) + `cpfCnpjSocios` (array keyword) | Busca full-text flat |

> **Nota**: A reestruturação do `cnpj_v001` é menos urgente pois tem apenas 2 campos nested
> de nível 1 e o impacto nas tools nativas é menor.

#### 6.8.9 Resumo Executivo: Recomendação para a Equipe

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  📋 RECOMENDAÇÃO: REPUBLICAR anm_v002 SEM NESTED                            │
│                                                                             │
│  MOTIVOS:                                                                   │
│                                                                             │
│  1. ✅ Maximiza uso de tools nativas OpenSearch (QueryPlanningTool,          │
│        SearchIndexTool, PPLTool, RAGTool, VectorDBTool)                     │
│                                                                             │
│  2. ✅ Dados nested JÁ existem em índices separados:                        │
│        • substancias → anm_substancia_v001 (com embedding!)                │
│        • tipo-uso → anm_tipo-uso-substancia_v001 (com embedding!)          │
│        • detalhes CNPJ → cnpj_v001 (221M docs)                             │
│        • municípios → ibge_municipio_v001 (com geo_shape!)                 │
│                                                                             │
│  3. ✅ Cardinalidade ~1:1 (poligonos=1.00, substancias=1.20,               │
│        municipios=1.17) permite flatten sem perda                           │
│                                                                             │
│  4. ✅ Elimina anti-pattern de 3 níveis de nesting                          │
│        (pessoas.detalhesCNPJ.socios)                                       │
│                                                                             │
│  5. ✅ Reduz tamanho do índice (~5.6GB → ~2GB estimado)                     │
│                                                                             │
│  6. ✅ Queries 45% mais simples (12 linhas vs 22 linhas)                    │
│                                                                             │
│  7. ✅ Performance: queries flat são 2-5x mais rápidas que nested           │
│                                                                             │
│  AÇÃO NECESSÁRIA DA EQUIPE ETL:                                             │
│  • Criar script de re-indexação anm_v001 → anm_v002                        │
│  • Flatten: promover geo_point e geo_shape ao root                         │
│  • Flatten: manter apenas IDs de substância (array simples)                │
│  • Flatten: manter apenas idMunicipio, nome, UF (campos root)             │
│  • Flatten: manter titular principal + array de nomes                      │
│  • Remover: detalhesCNPJ completo (já existe em cnpj_v001)                 │
│  • Remover: socios copiados (já existe em cnpj_v001)                       │
│  • Remover: cnaeFiscalSecundaria copiada                                   │
│                                                                             │
│  RESULTADO ESPERADO:                                                        │
│  • 0 campos nested (vs 12 atuais)                                          │
│  • 100% compatível com tools nativas OpenSearch                            │
│  • Busca híbrida 2-passos continua funcionando (melhor ainda)              │
│  • Possibilidade futura de adicionar embedding ao próprio índice           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 6.9 Como o QueryPlanningTool Funciona (Explicação para Stakeholders)

> 📌 **Objetivo desta seção**: Explicar de forma clara como funciona o fluxo interno das tools
> nativas do OpenSearch ML Commons, com exemplos reais executados no cluster de produção,
> para que stakeholders entendam por que a estrutura do índice impacta diretamente o funcionamento
> do agente de IA.

#### 6.9.1 Fluxo Interno do QueryPlanningTool

O `QueryPlanningTool` é uma **tool nativa do OpenSearch ML Commons** que converte linguagem natural em DSL (Domain Specific Language) de busca. Internamente, funciona assim:

```
 Usuário                  LangGraph              QueryPlanningTool           OpenSearch
    │                        │                          │                       │
    │  "Jazidas de areia     │                          │                       │
    │   perto de Campinas"   │                          │                       │
    │───────────────────────▶│                          │                       │
    │                        │                          │                       │
    │                        │  Envia ao LLM:           │                       │
    │                        │  - Mapping do índice     │                       │
    │                        │  - Pergunta do usuário   │                       │
    │                        │─────────────────────────▶│                       │
    │                        │                          │                       │
    │                        │                          │  LLM gera DSL JSON   │
    │                        │                          │  baseado no mapping   │
    │                        │                          │                       │
    │                        │  Recebe DSL              │                       │
    │                        │◀─────────────────────────│                       │
    │                        │                          │                       │
    │                        │  SearchIndexTool         │                       │
    │                        │  executa a DSL           │                       │
    │                        │─────────────────────────────────────────────────▶│
    │                        │                          │                       │
    │                        │  Resultados              │                       │
    │                        │◀─────────────────────────────────────────────────│
    │  Resposta formatada    │                          │                       │
    │◀───────────────────────│                          │                       │
```

**O ponto crítico**: o LLM interno precisa **gerar DSL válido** a partir do mapping. Se o mapping tem campos nested, o LLM precisa saber que deve usar `{"nested": {"path": "..."}}` — e **LLMs genéricos não são treinados para isso**.

#### 6.9.2 Prova Real: Falha Silenciosa com Nested (Executada no Cluster)

Executamos no cluster de produção a **mesma busca** de duas formas — com e sem o wrapper `nested`:

##### ❌ Query SEM nested (é o que o QueryPlanningTool gera):
```json
{
  "query": {
    "bool": {
      "must": [
        {"match": {"substancias.Substancia.nmSubstancia": "areia"}},
        {"term": {"municipios.siglaUF": "SP"}}
      ],
      "filter": [{"term": {"btAtivo": "s"}}]
    }
  }
}
```
**→ Resultado: 0 hits** ❌ (falha silenciosa — nenhum erro, só não encontra nada)

##### ✅ Query COM nested (a única correta para `anm_v001`):
```json
{
  "query": {
    "bool": {
      "must": [
        {
          "nested": {
            "path": "substancias",
            "query": {"match": {"substancias.Substancia.nmSubstancia": "areia"}}
          }
        },
        {
          "nested": {
            "path": "municipios",
            "query": {"term": {"municipios.siglaUF": "SP"}}
          }
        }
      ],
      "filter": [{"term": {"btAtivo": "s"}}]
    }
  }
}
```
**→ Resultado: 8.007 hits** ✅

> ⚠️ **Falha silenciosa**: O OpenSearch **não retorna erro**. Retorna `200 OK` com 0 resultados.
> O agente assume que não existem dados e responde ao usuário: *"Não encontrei jazidas de areia em SP"*.
> **Mas existem 8.007!**

#### 6.9.3 Complexidade DSL: Nested vs Flat (Comparação Real)

| Pergunta | DSL Flat (tokens) | DSL Nested (tokens) | Diferença |
|----------|-------------------|---------------------|-----------|
| "Areia em SP" | ~38 tokens | ~69 tokens | **+82%** |
| "Areia em SP, raio 50km" | ~60 tokens | ~104 tokens | **+75%** |
| "Processos do sócio SILVA" | ~26 tokens | ~50 tokens | **+88%** |
| **Mapping do índice** | ~200 tokens | ~243 tokens | **+22%** |

> O mapping é enviado ao LLM em **toda chamada** ao QueryPlanningTool. Um mapping mais complexo
> (com nested) não só custa mais tokens como **confunde o LLM** na geração.

#### 6.9.4 O Argumento do Custo LLM (Desmistificação)

A preocupação levantada: *"O custo com LLM aumentará usando tools nativas"*.

**Análise factual**:

| Abordagem | Chamadas LLM | Tokens/busca | Custo/mês* | Confiabilidade |
|-----------|-------------|-------------|-----------|----------------|
| **A: Flat + Tool Nativa** | 2 | ~300 | **$3.30** | 🟢 Alta |
| **B: Nested + Tool Nativa** | 2-3** | ~400 | $4.40 | 🔴 Baixa (falha silenciosa) |
| **C: Nested + Tool Custom** | 2 | ~260 | $2.86 | 🟡 Média (manutenção) |

\* *100 buscas/dia, 22 dias/mês, GPT-4o pricing ($0.005/1K input)*
\*\* *Re-tentativa quando query nested falha*

**Conclusões**:
1. **A diferença de custo entre as 3 abordagens é de ~$1.50/mês** — irrelevante
2. **Em TODAS as abordagens o LLM é chamado** — o custo é inerente à busca inteligente
3. **O custo real do nested não é em tokens — é em falhas silenciosas** que geram buscas vazias
4. Com tools custom (opção C), o "custo de LLM" é ligeiramente menor, mas **o custo de desenvolvimento e manutenção é de 2-5 dias por tool**

#### 6.9.5 Fluxo Completo: 3 Cenários Comparados

##### Cenário A: Índice Flat + Tools Nativas (RECOMENDADO)

```
Usuário: "Quero jazidas de areia perto de Campinas"
    │
    ▼
LangGraph decide: usar SearchIndexTool no anm_v002
    │
    ▼
LLM gera DSL (simples, flat):
{
  "query": {
    "bool": {
      "must": [
        {"match": {"nmSubstancias": "areia"}},
        {"geo_distance": {
          "distance": "50km",
          "localizacao": {"lat": -22.9, "lon": -47.06}
        }}
      ],
      "filter": [{"term": {"btAtivo": "s"}}]
    }
  }
}
    │
    ▼
SearchIndexTool executa → 234 resultados ✅
    │
    ▼
LLM formata resposta → Usuário recebe lista de jazidas
```

- **Chamadas LLM**: 2
- **Risco de falha**: Baixo
- **Código custom necessário**: 0

##### Cenário B: Índice Nested + Tools Nativas (PROBLEMÁTICO)

```
Usuário: "Quero jazidas de areia perto de Campinas"
    │
    ▼
LangGraph decide: usar SearchIndexTool no anm_v001
    │
    ▼
LLM gera DSL (INCORRETO — esquece o nested wrapper):
{
  "query": {
    "bool": {
      "must": [
        {"match": {"substancias.Substancia.nmSubstancia": "areia"}},
        {"geo_distance": {
          "distance": "50km",
          "poligonos.localizacao": {"lat": -22.9, "lon": -47.06}
        }}
      ]
    }
  }
}
    │
    ▼
SearchIndexTool executa → 0 resultados ❌ (FALHA SILENCIOSA)
    │
    ▼
LLM responde: "Não encontrei jazidas de areia perto de Campinas"
→ RESPOSTA ERRADA (existem 234!)
```

- **Chamadas LLM**: 2 (mas resultado errado)
- **Risco de falha**: ALTO
- **Confiança do usuário**: Destruída

##### Cenário C: Índice Nested + Tools Custom (FUNCIONAL, MAS CARO EM DEV)

```
Usuário: "Quero jazidas de areia perto de Campinas"
    │
    ▼
LangGraph decide: usar buscar_jazida (tool custom)
    │
    ▼
LLM extrai parâmetros:
buscar_jazida(substancia="areia", lat=-22.9, lon=-47.06, raio_km=50)
    │
    ▼
Tool Python monta DSL com nested internamente:
{
  "query": {
    "bool": {
      "must": [
        {"nested": {"path": "substancias", "query": ...}},
        {"nested": {"path": "poligonos", "query": {"geo_distance": ...}}}
      ]
    }
  }
}
    │
    ▼
Executa no OpenSearch → 234 resultados ✅
    │
    ▼
LLM formata resposta → Usuário recebe lista de jazidas
```

- **Chamadas LLM**: 2
- **Risco de falha**: Médio (depende da tool estar correta)
- **Código custom necessário**: ~200 linhas Python por cenário de busca
- **Manutenção**: Qualquer mudança no mapping = alterar código

#### 6.9.6 E Se Quiserem Manter Nested? (Opções Alternativas)

Se a equipe decidir manter a estrutura nested no `anm_v001`, as opções são:

| Opção | Esforço | Custo LLM Extra | Confiabilidade |
|-------|---------|----------------|----------------|
| **Few-shot prompting** (exemplos no prompt) | 1-2 dias | +$4-8/mês* | 🟡 Nível 1 OK, nível 2-3 falha |
| **Fine-tuning** do modelo | 1-2 semanas | $50-200 treino + $0/mês | 🟡 Funciona mas quebra em mudanças |
| **100% Tools Custom** | 2-5 dias/tool | $0 extra | 🟢 Alta (mas manutenção alta) |
| **Middleware de tradução** (query rewriter) | 3-5 dias | +$2-3/mês | 🟡 Adiciona latência |

\* *+500-1000 tokens de exemplos × 100 chamadas/dia × 22 dias*

**Few-shot prompting (detalhe)**:
Adicionaríamos exemplos no prompt do QueryPlanningTool:

```
"Quando o campo é type: nested, SEMPRE use a sintaxe:
{\"nested\": {\"path\": \"CAMPO\", \"query\": {...}}}

Exemplo 1: buscar substância areia
✅ Correto: {\"nested\": {\"path\": \"substancias\", ...}}
❌ Errado: {\"match\": {\"substancias.Substancia.nmSubstancia\": \"areia\"}}

Exemplo 2: buscar por localização
✅ Correto: {\"nested\": {\"path\": \"poligonos\", ...}}
❌ Errado: {\"geo_distance\": {\"poligonos.localizacao\": ...}}"
```

**Problemas do few-shot**:
- Funciona para nível 1 (`substancias`, `municipios`, `poligonos`)
- **Falha** para nível 2 (`pessoas.detalhesCNPJ.socios`) — muito difícil ensinar via prompt
- Cada exemplo adiciona ~200 tokens = custo cumulativo
- O prompt fica **frágil** — qualquer mudança no mapping exige refazer exemplos

#### 6.9.7 Recomendação Final para Decisão

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  DECISÃO PRAGMÁTICA (se equipe insiste em manter nested):           │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  CURTO PRAZO (S5-S6): Implementar com Tools Custom          │   │
│  │  • Funciona com anm_v001 como está hoje                     │   │
│  │  • Custo: 2-5 dias/tool, 0 custo extra de LLM              │   │
│  │  • Resultado confiável imediatamente                         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  MÉDIO PRAZO (S7+): Migrar para anm_v002 flat               │   │
│  │  • Substituir tools custom por tools nativas                 │   │
│  │  • Reduzir código de manutenção                             │   │
│  │  • Ganhar performance (queries flat 2-5x mais rápidas)      │   │
│  │  • Habilitar PPLTool e RAGTool (incompatíveis com nested)   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  IDEAL (se equipe aceitar a reestruturação):                        │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  JÁ (S5): Publicar anm_v002 flat                            │   │
│  │  • 1-2 dias de script ETL                                   │   │
│  │  • 0 código custom para buscas                              │   │
│  │  • Tools nativas funcionam out-of-the-box                   │   │
│  │  • Performance melhor, índice menor                         │   │
│  │  • Dados nested JÁ existem em índices separados             │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. MCPs Planejados (Baseado nos Índices Reais)

> ⚠️ **Importante**: Esta seção foi atualizada com base na análise dos índices reais do cluster OpenSearch em produção.
> Consulte `INDICES_OPENSEARCH_ATUAL.md` para detalhes completos dos mapeamentos.

### 7.1 MCP Server: Jazidas (S5)

**Objetivo**: Expor capacidades de busca de processos minerários do índice `anm_v002` (25M+ documentos).

#### Estrutura Real do Índice

O índice `anm_v002` possui estrutura **flat** (otimizada para QPT) com campos nested apenas onde necessário:
- Campos top-level: `dsProcesso`, `btAtivo`, `qtAreaHa`, `nmUF`, `nmMunicipio`, `idSubstancia`, `nmSubstancia`, etc.
- `substancias` (nested) - objetos completos: `substancia`, `tipoUsoSubstancia`, `motivoEncerramentoSubstancia`, vigência
- `poligonos` (nested) - **contém `geo_shape` e `geo_point`**
- `localizacao` (geo_point) - localização flat para buscas simples
- `pessoas` (nested) - titulares com `detalhesCNPJ` completo

#### Estratégia de Busca Híbrida (2 passos)

```
┌─────────────────────────────────────────────────────────────────┐
│  PASSO 1: Busca Semântica (índice auxiliar)                     │
│                                                                 │
│  Termo: "areia para construção"                                 │
│         ↓                                                       │
│  Query k-NN em `anm_substancia_v001` (tem embedding!)           │
│         ↓                                                       │
│  Resultado: [idSubstancia: 45 (Areia), 67 (Areia Lavada), ...]  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  PASSO 2: Query Estruturada + Geo (índice principal)            │
│                                                                 │
│  Query em `anm_v002`:                                           │
│  - nested filter: substancias.Substancia.idSubstancia IN [...]  │
│  - nested geo_distance: poligonos.localizacao                   │
│  - filter: faseProcesso.dsFaseProcesso = "Concessão de Lavra"   │
└─────────────────────────────────────────────────────────────────┘
```

#### Tools Expostas

| Tool                      | Descrição                                                | Parâmetros Reais                                           |
| ------------------------- | -------------------------------------------------------- | ---------------------------------------------------------- |
| `buscar_jazidas`          | Busca híbrida (semântica + geo + filtros estruturados)   | `termo_busca`, `lat`, `lon`, `raio_km`, `uf?`, `fase?`, `ativo?` |
| `detalhes_processo`       | Obtém dados completos de um processo pelo dsProcesso     | `ds_processo` (ex: "832.145/2018")                         |
| `buscar_por_substancia`   | Busca semântica de substância + filtros                  | `substancia`, `lat?`, `lon?`, `raio_km?`, `uf?`            |
| `jazidas_por_poligono`    | Busca jazidas dentro de um polígono (nested geo_shape)   | `geometry` (GeoJSON)                                       |
| `listar_substancias`      | Lista substâncias com busca semântica                    | `termo?`                                                   |
| `estatisticas_regiao`     | Agregações por UF/município                              | `uf?`, `municipio?`, `substancia?`                         |

#### Exemplo de Implementação Real

```python
# mcp_servers/jazidas/queries.py

async def buscar_jazidas_hibrido(
    termo_busca: str,
    lat: float | None = None,
    lon: float | None = None,
    raio_km: float = 50,
    uf: str | None = None,
    fase: str | None = None,
    limite: int = 20
) -> list[dict]:
    """
    Busca híbrida em 2 passos:
    1. Busca semântica de substância em anm_substancia_v001
    2. Query estruturada + geo em anm_v002
    """
    
    # Passo 1: Buscar IDs de substâncias semanticamente similares
    embedding = await generate_embedding(termo_busca)
    substancia_response = await opensearch.search(
        index="anm_substancia_v001",
        body={
            "size": 5,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": embedding,
                        "k": 5
                    }
                }
            }
        }
    )
    ids_substancias = [hit["_source"]["idSubstancia"] for hit in substancia_response["hits"]["hits"]]
    
    # Passo 2: Query estruturada com nested
    must_clauses = []
    filter_clauses = []
    
    # Filtro por substância (nested)
    if ids_substancias:
        must_clauses.append({
            "nested": {
                "path": "substancias",
                "query": {
                    "terms": {
                        "substancias.Substancia.idSubstancia": ids_substancias
                    }
                }
            }
        })
    
    # Filtro geoespacial (nested em poligonos)
    if lat and lon:
        filter_clauses.append({
            "nested": {
                "path": "poligonos",
                "query": {
                    "geo_distance": {
                        "distance": f"{raio_km}km",
                        "poligonos.localizacao": {
                            "lat": lat,
                            "lon": lon
                        }
                    }
                }
            }
        })
    
    # Filtro por UF (nested em municipios)
    if uf:
        filter_clauses.append({
            "nested": {
                "path": "municipios",
                "query": {
                    "term": {
                        "municipios.siglaUF": uf.upper()
                    }
                }
            }
        })
    
    # Filtro por fase
    if fase:
        filter_clauses.append({
            "match": {
                "faseProcesso.dsFaseProcesso": fase
            }
        })
    
    # Filtro apenas ativos
    filter_clauses.append({
        "term": {"btAtivo": "s"}
    })
    
    response = await opensearch.search(
        index="anm_v002",
        body={
            "size": limite,
            "query": {
                "bool": {
                    "must": must_clauses,
                    "filter": filter_clauses
                }
            },
            "_source": [
                "dsProcesso", "faseProcesso", "qtAreaHa", "btAtivo",
                "substancias", "municipios", "poligonos.localizacao",
                "pessoas.pessoa.nmPessoa"
            ]
        }
    )
    
    return [hit["_source"] for hit in response["hits"]["hits"]]
```

### 7.2 MCP Server: Empresas (S5)

**Objetivo**: Expor capacidades de busca de empresas do índice `cnpj_v001` (221M+ documentos).

#### Estrutura Real do Índice

O índice `cnpj_v001` possui:
- `localizacao` (geo_point) - coordenadas do estabelecimento
- `cnaeFiscalPrincipal` (object) - código e descrição
- `cnaeFiscalSecundaria` (nested) - lista de CNAEs secundários
- `socios` (nested) - dados dos sócios
- `empresa` (object) - cnpjBasico, razaoSocial, capitalSocial

#### Estratégia de Busca Híbrida (2 passos)

```
┌─────────────────────────────────────────────────────────────────┐
│  PASSO 1: Busca Semântica de CNAE                               │
│                                                                 │
│  Termo: "transporte de minérios"                                │
│         ↓                                                       │
│  Query k-NN em `rfb_cnae_v001` (tem embedding!)                 │
│         ↓                                                       │
│  Resultado: ["4930-2/01", "4930-2/02", "0891-6/00", ...]        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  PASSO 2: Query Estruturada + Geo                               │
│                                                                 │
│  Query em `cnpj_v001`:                                          │
│  - terms: cnaeFiscalPrincipal.codigo IN [...]                   │
│  - OR nested: cnaeFiscalSecundaria.codigo IN [...]              │
│  - geo_distance: localizacao                                    │
│  - filter: situacaoCadastral.codigo = "02" (ativa)              │
└─────────────────────────────────────────────────────────────────┘
```

#### Tools Expostas

| Tool                    | Descrição                                           | Parâmetros Reais                                       |
| ----------------------- | --------------------------------------------------- | ------------------------------------------------------ |
| `buscar_empresas`       | Busca híbrida por atividade + localização           | `termo_busca`, `lat`, `lon`, `raio_km`, `uf?`, `ativas_apenas?` |
| `detalhes_empresa`      | Obtém dados completos pelo ID do estabelecimento    | `id` (CNPJ completo ou id interno)                     |
| `buscar_por_cnae`       | Busca por código(s) CNAE específicos                | `codigos_cnae[]`, `lat?`, `lon?`, `raio_km?`, `uf?`    |
| `buscar_por_cnpj_basico`| Busca estabelecimentos de uma empresa (matriz+filiais) | `cnpj_basico` (8 dígitos)                           |
| `listar_socios`         | Lista sócios de uma empresa (query nested)          | `id`                                                   |
| `buscar_cnaes`          | Busca semântica de códigos CNAE                     | `termo`                                                |

#### Exemplo: Busca por Atividade + Geo

```python
async def buscar_empresas_por_atividade(
    termo_busca: str,
    lat: float,
    lon: float,
    raio_km: float = 30,
    uf: str | None = None,
    ativas_apenas: bool = True,
    limite: int = 20
) -> list[dict]:
    """
    Busca empresas por atividade econômica usando busca semântica de CNAE.
    """
    
    # Passo 1: Buscar CNAEs semanticamente similares
    embedding = await generate_embedding(termo_busca)
    cnae_response = await opensearch.search(
        index="rfb_cnae_v001",
        body={
            "size": 10,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": embedding,
                        "k": 10
                    }
                }
            },
            "_source": ["codigo", "nomeClasse"]
        }
    )
    codigos_cnae = [hit["_source"]["codigo"] for hit in cnae_response["hits"]["hits"]]
    
    # Passo 2: Buscar empresas
    must_clauses = [
        {
            "bool": {
                "should": [
                    # CNAE principal
                    {"terms": {"cnaeFiscalPrincipal.codigo": codigos_cnae}},
                    # OU CNAE secundário (nested)
                    {
                        "nested": {
                            "path": "cnaeFiscalSecundaria",
                            "query": {
                                "terms": {"cnaeFiscalSecundaria.codigo": codigos_cnae}
                            }
                        }
                    }
                ],
                "minimum_should_match": 1
            }
        }
    ]
    
    filter_clauses = [
        {
            "geo_distance": {
                "distance": f"{raio_km}km",
                "localizacao": {"lat": lat, "lon": lon}
            }
        }
    ]
    
    if ativas_apenas:
        filter_clauses.append({"term": {"situacaoCadastral.codigo": "02"}})
    
    if uf:
        filter_clauses.append({"term": {"uf": uf.upper()}})
    
    response = await opensearch.search(
        index="cnpj_v001",
        body={
            "size": limite,
            "query": {
                "bool": {
                    "must": must_clauses,
                    "filter": filter_clauses
                }
            },
            "_source": [
                "id", "empresa.razaoSocial", "nomeFantasia",
                "cnaeFiscalPrincipal", "localizacao",
                "uf", "municipio.descricao", "logradouro", "numero",
                "telefone1", "correioEletronico"
            ],
            "sort": [
                {
                    "_geo_distance": {
                        "localizacao": {"lat": lat, "lon": lon},
                        "order": "asc",
                        "unit": "km"
                    }
                }
            ]
        }
    )
    
    return [hit["_source"] for hit in response["hits"]["hits"]]
```

### 7.3 MCP Server: Geo (S6)

**Objetivo**: Expor capacidades geoespaciais usando `ibge_municipio_v001` (que tem `geo_shape`!) e serviços externos.

#### Estrutura Real do Índice

O índice `ibge_municipio_v001` possui:
- `poligono` (geo_shape) - **polígono completo do município!**
- `localizacao` (geo_point) - centro geográfico
- `localizacaoEconomica` (geo_point) - centro econômico
- Múltiplos códigos: `idMunicipio`, `idMunicipioRFB`, `idMunicipioANM`, etc.

#### Tools Expostas

| Tool                      | Descrição                                          | Parâmetros                              |
| ------------------------- | -------------------------------------------------- | --------------------------------------- |
| `buscar_municipio`        | Busca municípios por nome ou código                | `nome?`, `codigo_ibge?`, `uf?`          |
| `municipio_por_coordenada`| Identifica município usando geo_shape contains     | `lat`, `lon`                            |
| `obter_poligono`          | Retorna GeoJSON do polígono do município           | `codigo_ibge`                           |
| `municipios_em_raio`      | Lista municípios em raio de um ponto               | `lat`, `lon`, `raio_km`                 |
| `calcular_rota`           | Calcula rota entre dois pontos (OSRM)              | `origem`, `destino`, `modo?`            |
| `calcular_distancia`      | Distância em linha reta ou por rota                | `pontos[]`, `tipo?`                     |
| `geocodificar`            | Converte endereço em coordenadas (Nominatim)       | `endereco`                              |
| `reverse_geocode`         | Converte coordenadas em endereço                   | `lat`, `lon`                            |

#### Exemplo: Identificar Município por Coordenada

```python
async def identificar_municipio(lat: float, lon: float) -> dict | None:
    """
    Usa geo_shape query para identificar em qual município está o ponto.
    """
    response = await opensearch.search(
        index="ibge_municipio_v001",
        body={
            "query": {
                "geo_shape": {
                    "poligono": {
                        "shape": {
                            "type": "point",
                            "coordinates": [lon, lat]  # GeoJSON é [lon, lat]
                        },
                        "relation": "contains"
                    }
                }
            },
            "_source": [
                "idMunicipio", "nome", "siglaUF", "nomeUF",
                "nomeMesorregiao", "nomeMicrorregiao",
                "localizacao", "amazoniaLegal", "capitalUF"
            ]
        }
    )
    
    if response["hits"]["total"]["value"] > 0:
        return response["hits"]["hits"][0]["_source"]
    return None
```

#### Integrações Externas

| Serviço      | Uso                                    | Tipo         |
| ------------ | -------------------------------------- | ------------ |
| OSRM         | Cálculo de rotas e distâncias          | Self-hosted ou público  |
| Nominatim    | Geocoding (gratuito, prioridade)       | OSM          |
| Google Maps  | Fallback para geocoding complexo       | API externa  |

---

## 8. Integração com LangGraph

### 8.1 Visão da Integração (S7-S8)

```
┌─────────────────────────────────────────────────────────────────────┐
│                 LANGGRAPH + MCP (Protocolo Unificado)                │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    LangGraph Graph                            │  │
│  │                                                               │  │
│  │  START ──→ [Router Agent] ──→ Decide fluxo                   │  │
│  │                   │                                           │  │
│  │         ┌─────────┼─────────┐                                │  │
│  │         ▼         ▼         ▼                                │  │
│  │    [Busca]   [Análise]  [Relatório]                          │  │
│  │         │         │         │                                │  │
│  │         └─────────┼─────────┘                                │  │
│  │                   ▼                                           │  │
│  │            [Tool Executor]                                    │  │
│  │                   │                                           │  │
│  │    ┌──────────────┼──────────────┐                           │  │
│  │    │   UnifiedMCPProvider         │                           │  │
│  │    │   (MCP Client pool)          │                           │  │
│  │    │              │               │                           │  │
│  │    ▼              ▼               ▼                           │  │
│  │ [OpenSearch   [Jazidas        [Empresas                      │  │
│  │  MCP Nativo]   :8010/mcp]      :8011/mcp]                   │  │
│  │  QPT, PPL,    buscar_fornec.  buscar_emp.                   │  │
│  │  Search...    buscar_jazidas  detalhes...                   │  │
│  │    │              │               │                           │  │
│  │    └──────────────┼───────────────┘                           │  │
│  │                   ▼                                           │  │
│  │            [Synthesizer]                                      │  │
│  │                   │                                           │  │
│  │                   ▼                                           │  │
│  │                 [END]                                         │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Diferença: OpenSearch é tratado como mais um MCP Server no pool.   │
│  Todas as conexões usam MCP Client (Streamable HTTP).              │
│  Tool discovery: list_tools() em cada server.                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 8.2 Configuração do MCP Client Unificado no LangGraph

```python
# app/langgraph/tool_provider.py
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from langchain_core.tools import StructuredTool

class UnifiedMCPProvider:
    """
    Provedor MCP unificado: TODOS os servers (incluindo OpenSearch nativo)
    são tratados como MCP Servers e conectados via Streamable HTTP.
    
    Não precisa mais de wrappers HTTP REST para OpenSearch.
    Tool discovery automático via list_tools() em cada server.
    """
    
    def __init__(self, mcp_servers: dict[str, str]):
        """
        Args:
            mcp_servers: nome → URL do endpoint MCP de cada server
                {
                    "opensearch": "https://<os-endpoint>/_plugins/_ml/mcp/sse",
                    "jazidas": "http://localhost:8010/mcp",
                    "empresas": "http://localhost:8011/mcp",
                }
        """
        self.mcp_servers = mcp_servers
        self._sessions: dict[str, ClientSession] = {}
    
    async def connect(self):
        """Conecta a todos os MCP Servers usando Streamable HTTP."""
        for name, url in self.mcp_servers.items():
            async with streamablehttp_client(url) as (read, write, _):
                session = ClientSession(read, write)
                await session.initialize()
                self._sessions[name] = session
    
    async def get_all_tools(self) -> list[StructuredTool]:
        """
        Retorna TODAS as tools de TODOS os MCP Servers.
        
        Resultado inclui:
        - OpenSearch nativas: QPT, PPL, SearchIndex, ListIndex, etc.
        - Jazidas custom: buscar_fornecedores, buscar_jazidas, etc.
        - Empresas custom: buscar_empresas, detalhes_empresa, etc.
        
        Tudo descoberto automaticamente via MCP Protocol.
        """
        tools = []
        for server_name, session in self._sessions.items():
            mcp_tools = await session.list_tools()
            for tool in mcp_tools.tools:
                tools.append(self._convert_mcp(server_name, tool, session))
        return tools
    
    def _convert_mcp(self, server, tool, session) -> StructuredTool:
        async def call(**kwargs):
            result = await session.call_tool(tool.name, kwargs)
            return result.content[0].text
        return StructuredTool(
            name=f"{server}__{tool.name}",
            description=tool.description,
            func=call,
            args_schema=tool.inputSchema
        )
```

> **Simplificação chave**: A classe `OpenSearchNativeTools` (wrapper HTTP REST) foi **eliminada**. O OpenSearch MCP nativo é tratado como qualquer outro MCP Server no pool. Zero código especial para tools nativas — apenas mais uma entrada no dicionário `mcp_servers`.

### 8.3 Configuração do OpenSearch como MCP Server

Com o MCP nativo habilitado no OpenSearch (`plugins.ml_commons.mcp_server_enabled: true`), o cluster expõe automaticamente suas tools via protocolo MCP:

```
Habilitação no opensearch.yml:
───────────────────────────────
plugins.ml_commons.mcp_server_enabled: true

Endpoint MCP exposto:
───────────────────────────────
https://<os-endpoint>/_plugins/_ml/mcp/sse

Tools auto-expostas pelo OpenSearch MCP Server:
───────────────────────────────
 • ListIndexTool       — lista índices com contagem e tamanho
 • IndexMappingTool    — retorna mapeamento de campos de um índice
 • IndexInsightTool    — estatísticas e metadados do índice
 • SearchIndexTool     — executa query DSL completa
 • QueryPlanningTool   — gera queries DSL via IA (QPT)
 • PPLTool             — executa queries PPL
 • VectorDBTool        — busca k-NN em índices vetoriais
```

> **Eliminação do wrapper**: A classe `OpenSearchNativeTools` (que usava `httpx` para fazer chamadas HTTP REST e expor como LangChain Tools) **não é mais necessária**. O `UnifiedMCPProvider` acima descobre essas tools automaticamente via `list_tools()` no MCP Client — exatamente como faz com os MCP Servers Python.

```python
# Exemplo de como a configuração fica no LangGraph:
mcp_servers = {
    # OpenSearch é tratado como mais um MCP Server no pool
    "opensearch": os.getenv("OPENSEARCH_MCP_ENDPOINT"),  # https://.../mcp/sse
    # Nossos MCP Servers Python
    "jazidas": "http://localhost:8010/mcp",
    "empresas": "http://localhost:8011/mcp",
}

provider = UnifiedMCPProvider(mcp_servers)
await provider.connect()       # Conecta a todos via MCP Client
all_tools = await provider.get_all_tools()  # ~12+ tools unificadas

# all_tools conterá:
# - opensearch__ListIndexTool, opensearch__QueryPlanningTool, ...
# - jazidas__buscar_jazidas, jazidas__detalhes_processo, ...
# - empresas__buscar_empresas, ...
```

---

## 9. Plano de Implementação

### 9.1 Timeline Detalhada (Atualizada com Abordagem Híbrida)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    TIMELINE S5-S6 (2 semanas)                       │
│                                                                     │
│  SEMANA 5 (S5)                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ Dia 1-2: Setup e Estrutura Base                             │    │
│  │   • Criar estrutura mcp_servers/                            │    │
│  │   • Implementar classe base MCP Server                      │    │
│  │   • Configurar cliente OpenSearch compartilhado             │    │
│  │   • Configurar testes e CI                                  │    │
│  │                                                             │    │
│  │ Dia 3-4: MCP Server Jazidas                                 │    │
│  │   • Implementar tools (buscar, detalhes, listar)            │    │
│  │   • Implementar queries OpenSearch                          │    │
│  │   • Testes unitários e integração                           │    │
│  │   • Documentação                                            │    │
│  │                                                             │    │
│  │ Dia 5: MCP Server Empresas                                  │    │
│  │   • Implementar tools básicas                               │    │
│  │   • Reutilizar padrões do MCP Jazidas                       │    │
│  │   • Testes                                                  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  SEMANA 6 (S6)                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ Dia 1-2: MCP Server Empresas (continuação)                  │    │
│  │   • Completar tools avançadas                               │    │
│  │   • Integração com índice CNAE                              │    │
│  │   • Testes                                                  │    │
│  │                                                             │    │
│  │ Dia 3-4: MCP Server Geo                                     │    │
│  │   • Implementar tools                                       │    │
│  │   • Integração OSRM/Nominatim                               │    │
│  │   • Testes                                                  │    │
│  │                                                             │    │
│  │ Dia 5: Integração e Documentação                            │    │
│  │   • Testar comunicação entre MCPs                           │    │
│  │   • Documentação final                                      │    │
│  │   • Deploy em ambiente de desenvolvimento                   │    │
│  │   • ✅ MARCO M3: API + MCPs funcionais                      │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### 9.2 Entregáveis por Sprint (Atualizado com Abordagem Híbrida)

#### S5 - Estrutura Base + MCP Jazidas + Início Empresas

| Entregável | Critério de Aceite | Tipo |
|---|---|---|
| Estrutura `mcp_servers/` criada | Classe base, cliente OpenSearch, schemas comuns | Infra |
| Validação tools nativas (OpenSearch MCP) | ListIndexTool, QueryPlanningTool acessíveis via MCP Client | Nativa |
| MCP Server Jazidas funcionando | 5 tools custom expostas via Streamable HTTP | Custom |
| Busca híbrida 2 passos (jazidas) | `anm_substancia_v001` → `anm_v002` com geo | Custom |
| Cache Redis configurado | Embeddings e resultados cacheados | Infra |
| MCP Server Empresas (básico) | 3 tools custom básicas funcionando | Custom |
| Testes | Cobertura > 80% para tools custom | QA |
| Documentação | README com exemplos de uso para cada tool | Doc |

#### S6 - MCP Empresas (completo) + MCP Geo + Integração Nativas

| Entregável | Critério de Aceite | Tipo |
|---|---|---|
| MCP Empresas completo | Todas as 5 tools custom funcionando | Custom |
| Busca híbrida 2 passos (empresas) | `rfb_cnae_v001` → `cnpj_v001` com geo | Custom |
| MCP Geo funcionando | 7 tools custom expostas | Custom |
| Integração OSRM | Rotas calculando em < 500ms | Externo |
| Integração tools nativas via MCP | PPLTool + QueryPlanningTool via UnifiedMCPProvider | Nativa |
| Deploy dev | 3 MCPs custom + OpenSearch MCP nativo em containers | Infra |
| Marco M3 | 12+ tools totais (7 nativas via MCP + 5+ custom) testáveis | ✅ |

### 9.3 Dependências e Riscos

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| MCP SDK instável (versão beta) | Média | Alto | Usar versão fixada, monitorar releases |
| Latência queries OpenSearch (25M+ docs) | Alta | Médio | Cache Redis, otimizar nested queries |
| QueryPlanningTool gera DSL incorreto | Média | Baixo | Usar apenas para queries simples; core via custom tools |
| `poligonos.geom` vazio no anm_v002 | Confirmado | Médio | Usar `localizacao` (geo_point flat) ou `poligonos.localizacao` para raio |
| Complexidade do OSRM | Baixa | Baixo | Serviço público inicialmente, self-host depois |
| OpenSearch MCP nativo instável (v3.0 recente) | Média | Médio | Manter `opensearch-py` como fallback para data queries; MCP apenas para tool invocation |
| Dados ausentes nos índices | Média | Médio | Validar existência de dados antes de implementar tool |

### 9.4 Próximos Passos Imediatos

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CHECKLIST DE IMPLEMENTAÇÃO                                │
│                                                                             │
│  FASE A: Setup Inicial (Dia 1)                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  □ 1. Instalar MCP SDK Python (`pip install mcp`)                   │    │
│  │  □ 2. Instalar opensearch-py, redis, httpx                          │    │
│  │  □ 3. Criar estrutura de diretórios mcp_servers/                    │    │
│  │  □ 4. Implementar classe base (BaseMCPServer)                       │    │
│  │  □ 5. Configurar cliente OpenSearch compartilhado                   │    │
│  │  □ 6. Testar conexão com cluster produção                          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  FASE B: OpenSearch MCP Nativo - Validação (Dia 1-2)                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  □ 7. Validar mcp_server_enabled no cluster OpenSearch              │    │
│  │  □ 8. Conectar via MCP Client (Streamable HTTP) ao endpoint nativo │    │
│  │  □ 9. Listar tools expostas via list_tools() e documentar          │    │
│  │  □ 10. Testar QueryPlanningTool via MCP call_tool()                │    │
│  │  □ 11. Testar SearchIndexTool via MCP call_tool()                  │    │
│  │  □ 12. Validar UnifiedMCPProvider descobre tools nativas + custom  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  FASE C: MCP Server Jazidas - Tools Custom (Dia 2-4)                        │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  □ 13. Implementar tool: buscar_jazidas (2 passos + geo)            │    │
│  │  □ 14. Implementar tool: detalhes_processo                          │    │
│  │  □ 15. Implementar tool: buscar_por_substancia                      │    │
│  │  □ 16. Implementar tool: listar_substancias                         │    │
│  │  □ 17. Implementar tool: estatisticas_regiao                        │    │
│  │  □ 18. Configurar cache Redis para embeddings e resultados          │    │
│  │  □ 19. Testes unitários + integração                                │    │
│  │  □ 20. Testar via MCP Inspector                                     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  FASE D: MCP Server Empresas - Tools Custom (Dia 4-5)                       │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  □ 21. Implementar tool: buscar_empresas (CNAE semântico + geo)     │    │
│  │  □ 22. Implementar tool: detalhes_empresa                           │    │
│  │  □ 23. Implementar tool: buscar_por_cnae                            │    │
│  │  □ 24. Implementar tool: buscar_cnaes                               │    │
│  │  □ 25. Reutilizar padrões de cache e base do MCP Jazidas            │    │
│  │  □ 26. Testes + MCP Inspector                                       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 9.5 Estrutura de Diretórios Atualizada (MCP Unificado)

```
MineralRadar_v2/
├── backend/
│   ├── app/                          # FastAPI Gateway (existente)
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── obras.py          # CRUD Obras (MongoDB) ✅ feito
│   │   │       ├── estudos.py        # CRUD Estudos (MongoDB) ✅ feito
│   │   │       ├── search.py         # 🆕 Proxy para tools custom (uso direto)
│   │   │       └── chat.py           # (S7-S10) Endpoint de chat
│   │   ├── langgraph/
│   │   │   └── tool_provider.py      # 🆕 UnifiedMCPProvider (conecta a TODOS os MCP Servers)
│   │   └── ...
│   │
│   └── mcp_servers/                  # 🆕 MCP Servers Python (tools customizadas)
│       ├── __init__.py
│       ├── common/                   # Código compartilhado
│       │   ├── __init__.py
│       │   ├── opensearch_client.py  # Cliente OpenSearch (HTTP REST para data queries)
│       │   ├── redis_cache.py        # 🆕 Cache Redis para embeddings/resultados
│       │   ├── embeddings.py         # 🆕 Geração de embeddings (Azure OpenAI)
│       │   ├── config.py             # Configurações (env vars)
│       │   └── schemas.py            # Schemas comuns (GeoPoint, etc.)
│       │
│       ├── jazidas/                   # MCP Server: Jazidas (port 8010) — Streamable HTTP
│       │   ├── __init__.py
│       │   ├── server.py             # Bootstrap + Streamable HTTP endpoint (/mcp)
│       │   ├── tools.py              # 5 tools: fornecedores, jazidas, detalhes, poligono, vigencia
│       │   ├── queries/              # Queries OpenSearch (nested + geo + 2 passos)
│       │   │   ├── substancia.py     # SubstanciaResolver (k-NN + match)
│       │   │   └── ...
│       │   ├── cache.py              # Helpers de cache e paginação
│       │   └── schemas.py            # Schemas: JazidaResult, SubstanciaMatch, etc.
│       │
│       ├── empresas/                  # MCP Server: Empresas (port 8011) — Streamable HTTP
│       │   ├── __init__.py
│       │   ├── server.py
│       │   ├── tools.py              # Tools: buscar, detalhes, cnae, cnpj, socios
│       │   ├── queries.py            # Queries OpenSearch (CNAE semântico + geo)
│       │   └── schemas.py            # Schemas: EmpresaResult, CnaeMatch, etc.
│       │
│       └── geo/                       # MCP Server: Geo (port 8012) — Streamable HTTP
│           ├── __init__.py
│           ├── server.py
│           ├── tools.py              # Tools: municipio, rota, geocode, etc.
│           ├── queries.py            # Queries: geo_shape contains, geo_distance
│           ├── osrm.py               # 🆕 Cliente OSRM para rotas
│           ├── nominatim.py          # 🆕 Cliente Nominatim para geocoding
│           └── schemas.py
│
├── docker-compose.yml                 # Atualizado com 3 MCP Servers
└── docs/
    ├── MCP_PLANEJAMENTO.md            # Este documento
    └── INDICES_OPENSEARCH_ATUAL.md    # Análise dos índices reais

# OpenSearch MCP Nativo (zero código nosso):
# Endpoint: https://<os-endpoint>/_plugins/_ml/mcp/sse
# Config: plugins.ml_commons.mcp_server_enabled: true
# Tools: QPT, PPL, SearchIndex, ListIndex, Mapping, VectorDB, etc.
# Acesso: via UnifiedMCPProvider (mesmo MCP Client que nossos servers)
```

### 9.6 Docker Compose Atualizado

```yaml
# Adição ao docker-compose.yml existente
services:
  # ... serviços existentes (mongodb, opensearch, redis) ...

  mcp-jazidas:
    build:
      context: ./backend
      dockerfile: Dockerfile.mcp
    command: python -m mcp_servers.jazidas.server
    ports:
      - "8010:8010"
    env_file: ./backend/.env
    depends_on:
      - redis
    networks:
      - supply-network

  mcp-empresas:
    build:
      context: ./backend
      dockerfile: Dockerfile.mcp
    command: python -m mcp_servers.empresas.server
    ports:
      - "8011:8011"
    env_file: ./backend/.env
    depends_on:
      - redis
    networks:
      - supply-network

  mcp-geo:
    build:
      context: ./backend
      dockerfile: Dockerfile.mcp
    command: python -m mcp_servers.geo.server
    ports:
      - "8012:8012"
    env_file: ./backend/.env
    depends_on:
      - redis
    networks:
      - supply-network
```

---

## 📚 Referências

- [MCP Specification](https://spec.modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [LangGraph + MCP Integration](https://langchain-ai.github.io/langgraph/tutorials/mcp/)
- [OpenSearch Python Client](https://opensearch-project.github.io/opensearch-py/)
- [OpenSearch ML Commons Tools](https://opensearch.org/docs/latest/ml-commons-plugin/tools/)
- [OpenSearch ML Commons Agents](https://opensearch.org/docs/latest/ml-commons-plugin/agents-tools/)