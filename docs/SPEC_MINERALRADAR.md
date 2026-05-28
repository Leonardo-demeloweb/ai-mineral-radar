# Especificação do Produto — MineralRadar

**Versão:** 1.1  
**Data:** 05 de maio de 2026  
**Status:** Rascunho para validação  

---

## 1. Visão do Produto

### 1.1 Conceito

O **MineralRadar** é uma plataforma de inteligência mineral, focada em mineração estratégica, terras raras e inteligência de processos minerários no Brasil.

A plataforma combina:

- **Inteligência geoespacial** — análise de processos ANM, sobreposições ambientais, ocorrências geológicas
- **IA conversacional** — agente em linguagem natural que orquestra múltiplas fontes de dados simultaneamente
- **Inteligência de mercado** — cadeia de suprimento global de minerais críticos, preços em tempo real, fluxos de exportação/importação

### 1.2 Problema que resolve

O Brasil é o terceiro maior produtor mundial de minérios e possui reservas estratégicas relevantes de Nióbio (90% do mundo), Terras Raras, Lítio, Grafita e Cobalto. Nenhuma plataforma no mercado trata esses minerais como domínio prioritário. O MineralRadar preenche esse gap respondendo à pergunta que o mercado não resolve:

> *"Qual é o valor real desta jazida de terra rara, quem a controla, quais são os riscos jurídicos e ambientais, e como ela se conecta ao mercado global?"*

### 1.3 Personas principais


| Persona                                | Perfil                                                     | Pergunta central                                                                            |
| -------------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| **Geólogo / Explorador**               | Prospector, consultor mineral, empresa júnior de mineração | *"Onde está o melhor alvo de exploração para NdFeB nessa região?"*                          |
| **Gestor de Processos ANM**            | Advogado minerário, gerente de compliance regulatório      | *"O que vence nos próximos 90 dias? O que está em risco de multa?"*                         |
| **Analista de M&A / Investidor**       | Fundo de investimento, empresa multinacional de mineração  | *"Vale a pena adquirir esta jazida? Qual é o passivo ambiental e quem controla realmente?"* |
| **Gestor de Suprimentos Estratégicos** | Indústria automotiva, defesa, energia renovável            | *"Onde no Brasil posso garantir fornecimento de Neodímio para os próximos 5 anos?"*         |


### 1.4 Posicionamento de mercado

Dois produtos referenciais existentes no mercado são relevantes para entender o espaço do MineralRadar:

- **Plataformas de gestão de processos ANM** (ex: Jazida.com) — boas em gestão legal/burocrática (prazos, alertas, petições), mas sem inteligência de mercado, sem IA conversacional e sem foco em minerais estratégicos
- **Plataformas de inteligência de suprimentos** (setor construção civil) — boas em logística e geolocalização de agregados, mas sem domínio minerário estratégico

O MineralRadar ocupa um espaço que **nenhum desses dois** preenche: inteligência mineral estratégica com IA conversacional, cobrindo do potencial geológico ao mercado global de minerais críticos.

---

## 2. Estratégia de desenvolvimento — Clean Room Design

### 2.1 Princípio fundamental

O MineralRadar é construído do zero como produto independente. A implementação segue o princípio de **Clean Room Design**:

- Toda a lógica de negócio, estrutura de dados, índices, queries, agentes e prompts são originais do MineralRadar
- As **únicas dependências permitidas** são bibliotecas e frameworks de código aberto amplamente estabelecidos no mercado
- Nenhum cluster de dados externo é reaproveitado — o MineralRadar terá seu próprio cluster OpenSearch, ETL e pipeline de dados
- Nenhum código proprietário de terceiros é incorporado ao repositório

### 2.2 O que é open source e pode ser usado livremente


| Biblioteca / Framework    | Licença                 | Uso no MineralRadar                        |
| ------------------------- | ----------------------- | ------------------------------------------ |
| **FastAPI**               | MIT                     | Framework de API REST                      |
| **LangGraph**             | MIT                     | Orquestrador de agentes IA                 |
| **MCP SDK (Python)**      | MIT                     | Protocolo Model Context Protocol           |
| **opensearch-py**         | Apache 2.0              | Cliente OpenSearch                         |
| **GeoPandas**             | BSD                     | Processamento de Shapefiles                |
| **Polars**                | MIT                     | Processamento de CSV/Parquet grande volume |
| **SQLAlchemy + asyncpg**  | MIT                     | ORM para PostgreSQL                        |
| **PostGIS**               | GPL-2.0 (extensão PG)   | Geo queries no staging ETL                 |
| **React + Vite**          | MIT                     | Frontend SPA                               |
| **MapLibre GL JS**        | BSD-3-Clause            | Renderização de mapas                      |
| **Tailwind CSS**          | MIT                     | Estilização                                |
| **Radix UI**              | MIT                     | Componentes acessíveis                     |
| **Zustand**               | MIT                     | Estado global frontend                     |
| **Redis (redis-py)**      | MIT                     | Cache e memória de sessão                  |
| **Motor (MongoDB async)** | Apache 2.0              | Persistência de dados de aplicação         |
| **Airflow / Prefect**     | Apache 2.0 / Apache 2.0 | Orquestração de ETL                        |


### 2.3 O que será construído do zero (lógica de negócio original)


| Componente                                 | Descrição                                                                                      |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| **Mapeamento de índices OpenSearch**       | Schema original para processos minerários, ocorrências CPRM, restrições geo, CFEM              |
| **MCP Server Jazidas Mineral**             | Tools específicas para domínio mineral estratégico — substâncias críticas, CFEM, sobreposições |
| **MCP Server Ambiental**                   | Tools de sobreposição com TIs, UCs, biomas — lógica original                                   |
| **MCP Server CFEM**                        | Tools de análise de compensação financeira como indicador de produção real                     |
| **Classificador de Minerais Estratégicos** | Lógica de classificação das 862 substâncias ANM em categorias críticas                         |
| **Agente LangGraph MineralRadar**          | Grafo de estados, router, ferramentas, system prompt — originais                               |
| **ETL Pipeline**                           | Bots Python para cada fonte (ANM, CPRM, FUNAI, IBAMA), transformações PostGIS, indexador       |
| **Frontend MineralRadar**                  | Componentes de UI, stores, hooks, layout — construídos do zero                                 |
| **Sistema de Memória**                     | Padrões de chave Redis, estrutura de conversas, preferências — originais                       |


### 2.4 Infraestrutura própria


| Recurso              | Descrição                                                                |
| -------------------- | ------------------------------------------------------------------------ |
| Cluster OpenSearch   | Novo, isolado, com índices e mappings específicos do MineralRadar        |
| PostgreSQL + PostGIS | Banco de staging do ETL — novo, sem relação com qualquer sistema externo |
| Redis                | Instância própria para cache e memória conversacional                    |
| MongoDB              | Banco de aplicação próprio — projetos, análises, usuários                |
| Repositório git      | `mineral-radar` — repositório novo, sem histórico externo                |


---

## 3. Arquitetura

### 3.1 Visão geral

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USUÁRIO                                      │
│  Geólogo / Gestor ANM / Analista M&A / Gestor de Suprimentos       │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    FRONTEND (React + MapLibre)                        │
│  Chat IA  │  Mapa Interativo  │  Workspace  │  Projetos / Análises  │
└─────────────────────────────────────────────────────────────────────┘
                              │ REST + SSE
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    BACKEND (FastAPI + LangGraph)                      │
│                                                                       │
│  Router Agent → [Prospecção | Due Diligence | Monitoramento |        │
│                  Mercado | Logística | Ambiental | Histórico]        │
└─────────────────────────────────────────────────────────────────────┘
                              │ MCP Protocol
       ┌──────────────────────┼──────────────────────┐
       ▼                      ▼                       ▼
┌──────────────┐  ┌─────────────────┐  ┌────────────────────────┐
│  Fase 1      │  │  Fase 2         │  │  Fase 3                │
│              │  │                 │  │                        │
│ • Jazidas    │  │ • Ambiental     │  │ • Monitoramento DOU    │
│ • Empresas   │  │ • CFEM          │  │ • Mercado (Preços TR)  │
│ • Geo        │  │ • Geológico     │  │ • Due Diligence deep   │
└──────────────┘  └─────────────────┘  └────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│              OpenSearch MineralRadar (cluster próprio)                │
│                                                                       │
│  anm_processos_v001     cprm_ocorrencias_v001   mr_geoquimica_v001           │
│  rfb_cnpj_v001          ibge_municipio_v001      anm_substancia_v001          │
│  anm_cfem_v001          mercado_mineral_v001     restricoes_geo_v001          │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ETL: fontes → Python Bots → PostgreSQL + PostGIS → OpenSearch       │
│  dadosabertos.anm.gov.br │ sgb.gov.br │ funai.gov.br │ mma.gov.br   │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Stack tecnológico


| Camada                  | Tecnologia                                    | Licença                       |
| ----------------------- | --------------------------------------------- | ----------------------------- |
| API                     | FastAPI + Python 3.12                         | MIT                           |
| Agente IA               | LangGraph                                     | MIT                           |
| Ferramentas             | MCP Servers (protocolo Anthropic)             | MIT                           |
| Busca                   | OpenSearch (k-NN + geo_shape + full-text)     | Apache 2.0                    |
| Memória curto prazo     | Redis Stack                                   | BSD                           |
| Persistência            | MongoDB                                       | SSPL (uso interno)            |
| Frontend                | React + Vite + MapLibre + Tailwind + Radix UI | MIT / BSD                     |
| Auth                    | Azure AD (MSAL)                               | Microsoft — padrão de mercado |
| Geo / Rotas             | Azure Maps                                    | Microsoft — padrão de mercado |
| LLM                     | Azure OpenAI (GPT-4o)                         | Microsoft — padrão de mercado |
| Embeddings              | Azure OpenAI text-embedding-3-small           | Microsoft — padrão de mercado |
| ETL — Staging           | PostgreSQL 16 + PostGIS 3.4                   | GPL-2.0                       |
| ETL — Geo processing    | GeoPandas + Shapely + Fiona                   | BSD / BSD / MIT               |
| ETL — CSV grande volume | Polars                                        | MIT                           |
| ETL — Orquestração      | Apache Airflow ou Prefect                     | Apache 2.0                    |
| ETL — Containerização   | Docker Compose                                | Apache 2.0                    |


---

## 4. Módulos do produto

### Módulo 1 — Prospecção e Mapa de Potencial

> *"Onde está o melhor alvo de exploração para esta substância estratégica?"*

**Funcionalidades:**

- Visualização no mapa de todos os processos ANM da área (ativos e inativos) com filtro por substância
- Sobreposição de ocorrências minerais da CPRM (GeoBank) — contexto geológico
- Classificação de substâncias por categoria estratégica (terra rara, lítio, nióbio, cobalto, grafita, urânio)
- Identificação de áreas livres (sem processo ANM ativo) com potencial geológico
- Visualização de províncias minerais brasileiras (Carajás, Quadrilátero Ferrífero, Borborema, etc.)

**Fontes de dados:**


| Fonte                                  | Status no MineralRadar | Ação                                            |
| -------------------------------------- | ---------------------- | ----------------------------------------------- |
| ANM SIGMINE (ativos + inativos)        | 🟡 ETL necessário      | `bot_anm.py` → PostgreSQL → OpenSearch          |
| Classificador Minerais Estratégicos    | 🟡 Implementar         | Lógica original sobre tabela de substâncias ANM |
| CPRM — Ocorrências Minerais            | 🟡 ETL necessário      | WFS `geoportal.sgb.gov.br`                      |
| CPRM — Mapa Geológico                  | 🔵 API direta          | WMS tiles no frontend                           |
| ANM — Áreas em Disponibilidade (SOPLE) | 🟡 ETL necessário      | `dadosabertos.anm.gov.br/SOPLE/`                |


---

### Módulo 2 — Due Diligence de Processo

> *"Este processo é sólido? Quem realmente controla? Quais são os riscos?"*

**Funcionalidades:**

- Ficha completa do processo: fases, eventos, títulos, histórico, substâncias, área
- Titular real com estrutura societária completa (cross-reference com dados públicos RFB)
- CFEM histórico: o processo tem produção real ou está inativo de fato?
- RAL (Relatório Anual de Lavra): volume declarado de produção
- Histórico de processos inativos do mesmo titular
- Verificação de sobreposições geográficas (TI, UC, bioma, SICAR)

**Fontes de dados:**


| Fonte                       | Status no MineralRadar | Ação                                            |
| --------------------------- | ---------------------- | ----------------------------------------------- |
| ANM SIGMINE (processos)     | 🟡 ETL necessário      | `bot_anm.py`                                    |
| RFB CNPJ (titular + sócios) | 🟡 ETL necessário      | `bot_rfb.py`                                    |
| CFEM histórico              | 🟡 ETL necessário      | `bot_cfem.py` → `dadosabertos.anm.gov.br/CFEM/` |
| ANM SCM — Cadastro Mineiro  | 🟡 ETL necessário      | `dadosabertos.anm.gov.br/SCM/`                  |
| SICOP — Trâmite processual  | 🟡 ETL necessário      | `dadosabertos.anm.gov.br/SICOP/Sicop.csv`       |
| Sobreposições geo (TI + UC) | 🟡 Calcular PostGIS    | Pré-computado antes da indexação                |


---

### Módulo 3 — Restrições e Sobreposições Geográficas

> *"Esta área tem impedimentos legais ou ambientais que inviabilizam a mineração?"*

**Funcionalidades:**

- Sobreposição com Terras Indígenas (FUNAI) — risco constitucional máximo
- Sobreposição com Unidades de Conservação (IBAMA/CNUC) — APA, REBIO, PARNA, etc.
- Sobreposição com biomas (IBGE) — Amazônia, Cerrado, Mata Atlântica
- Sobreposição com imóveis rurais (INCRA/SIGEF) — direitos superficiários
- Cálculo de % de área sobreposta para cada restrição
- Classificação automática de nível de risco (baixo / médio / alto / crítico)

**Fontes de dados:**


| Fonte                        | Status no MineralRadar | Ação                                              |
| ---------------------------- | ---------------------- | ------------------------------------------------- |
| FUNAI — Terras Indígenas     | 🟡 ETL necessário      | Download mensal GeoJSON                           |
| IBAMA — CNUC (UCs)           | 🟡 ETL necessário      | Shapefile mensal                                  |
| IBGE — Biomas                | 🟡 ETL necessário      | Download único estável                            |
| INCRA — SIGEF                | 🟡 ETL necessário      | WFS público                                       |
| Sobreposições pré-computadas | 🟡 Calcular PostGIS    | `ST_Intersects` + `ST_Area(ST_Intersection(...))` |


> **Nota técnica:** As sobreposições são calculadas no PostgreSQL+PostGIS via `ST_Intersects` **antes** da indexação no OpenSearch. O resultado chega ao índice como campo pré-computado (`restricoes_geo`), sem custo de query em tempo real. Ver `SPEC_ETL_MINERALRADAR.md`.

---

### Módulo 4 — Monitoramento Contínuo

> *"O que mudou nos meus processos hoje? O que vence nos próximos 30 dias?"*

**Funcionalidades:**

- Dashboard de alertas: prazos de vencimento de autorização de pesquisa (3 anos + prorrogação)
- Leitura do Diário Oficial da União: novos atos minerários que afetam processos monitorados
- Acompanhamento de movimentações no SEI/ANM
- Notificações por e-mail para obrigações críticas (RAL, DIPEM, renovações)
- Histórico de mudanças de fase e status do processo

**Fontes de dados:**


| Fonte                      | Status no MineralRadar | Ação                             |
| -------------------------- | ---------------------- | -------------------------------- |
| SICOP — Trâmite processual | 🟡 ETL diário          | `dadosabertos.anm.gov.br/SICOP/` |
| ANM SCM — Cadastro Mineiro | 🟡 ETL diário          | `dadosabertos.anm.gov.br/SCM/`   |
| DOU — API IN DOU           | 🔵 API disponível      | `in.gov.br/leituradou` (Fase 2+) |
| SEI — Movimentações ANM    | ⚪ Complexo             | Scraping controlado (Fase 3)     |


---

### Módulo 5 — Inteligência de Mercado (Minerais Estratégicos)

> *"Qual é o valor desta jazida? Onde este mineral é consumido no mundo?"*

**Funcionalidades:**

- Preços em tempo real de Terras Raras (Nd, Pr, Dy, Tb, Ce, La, etc.), Lítio, Nióbio, Cobalto
- Exportações e importações brasileiras por NCM mineral (ComexStat MDIC)
- Contexto de cadeia de suprimento global: China ~90% das TRs, IRA (EUA), CMRD (Europa)
- Produção nacional declarada (RAL/AMB por substância e estado)
- Processos com CFEM alto: indicador de jazidas efetivamente produtivas

**Fontes de dados:**


| Fonte                  | Status no MineralRadar | Ação                             |
| ---------------------- | ---------------------- | -------------------------------- |
| Metals-API (preços TR) | 🔴 Pago                | Integração REST (plano freemium) |
| ComexStat MDIC         | 🔵 API gratuita        | `api-comexstat.mdic.gov.br`      |
| ANM AMB/RAL (produção) | 🟡 ETL necessário      | `dadosabertos.anm.gov.br/`       |
| CFEM histórico         | 🟡 ETL necessário      | `dadosabertos.anm.gov.br/CFEM/`  |


---

### Módulo 6 — Logística Mineral

> *"Como escoar o minério? Custo de frete, porto mais próximo, ferrovia?"*

**Funcionalidades:**

- Isócrona de acesso a partir da jazida (tempo de caminhão/ferro até porto)
- Rota ótima jazida → porto → mercado internacional
- Comparação de N rotas com custo de frete estimado
- Infraestrutura de escoamento: ferrovias (EF-334, EFVM, Carajás), portos, hidrovias

**Fontes de dados:**


| Fonte                       | Status no MineralRadar | Ação              |
| --------------------------- | ---------------------- | ----------------- |
| Azure Maps (isócrona, rota) | 🔵 API disponível      | Integração direta |
| ANTAQ — Portos              | 🟡 ETL necessário      | CSV público       |
| ANTT — Ferrovias/Rodovias   | 🟡 ETL necessário      | WFS público       |


---

## 5. Classificador de Minerais Estratégicos

Este componente é original do MineralRadar e representa o elemento de maior impacto imediato. As 862 substâncias cadastradas na ANM não possuem classificação por família estratégica em nenhum sistema público.

### 5.1 Categorias


| Categoria    | Substâncias principais                                                                           | Relevância estratégica                            |
| ------------ | ------------------------------------------------------------------------------------------------ | ------------------------------------------------- |
| `terra_rara` | La, Ce, Pr, Nd, Sm, Eu, Gd, Tb, Dy, Ho, Er, Tm, Yb, Lu, Sc, Y + monazita, bastnaesita, xenotímio | Ímãs NdFeB (VEs, eólica), eletrônica, defesa      |
| `niobio`     | Nióbio, pirocloro, columbita, tantalita                                                          | Brasil 90% das reservas — superligas, capacitores |
| `litio`      | Lítio, espodumênio, petalita, lepidolita, ambligonita                                            | Baterias de íons de lítio (VEs, armazenamento)    |
| `cobalto`    | Cobalto, smaltita, cobaltita, asbolana                                                           | Baterias NMC, superligas de alta temperatura      |
| `grafita`    | Grafita natural cristalina, grafita amorfa                                                       | Anodos de bateria, lubrificantes, refratários     |
| `uranio`     | Urânio, uraninita, pechblenda, carnotita, torbernita                                             | Ciclo nuclear; reservas em MG e CE                |
| `titanio`    | Titânio, ilmenita, rutilo, anatásio, leucoxênio                                                  | Aeroespacial, pigmentos (TiO₂), implantes         |
| `vanadio`    | Vanádio, vanadinita, carnotita                                                                   | Baterias de fluxo, ligas especiais para aço       |
| `manganes`   | Manganês, pirolusita, manganita, romanechita                                                     | Aço, baterias de Mn-ion                           |
| `fosfato`    | Fosfato, apatita, fosforita, monazita-fosfato                                                    | Fertilizantes — segurança alimentar estratégica   |
| `cobre`      | Cobre, calcopirita, calcocita, bornita, malaquita                                                | Eletrónica, energia renovável, EVs                |
| `ouro`       | Ouro, eletro, teluretos de ouro                                                                  | Eletrônica, reserva de valor                      |
| `ferro`      | Hematita, magnetita, siderita, itabirito                                                         | Principal exportação mineral do Brasil            |
| `agregados`  | Areia, brita, cascalho, calcário, granito, basalto, argila                                       | Construção civil                                  |
| `outro`      | Demais substâncias                                                                               | Categoria residual                                |


### 5.2 Implementação

```python
# mineralradar/classificador/estrategico.py

CATEGORIAS: dict[str, list[str]] = {
    "terra_rara": [
        "lantânio", "lantanio", "cério", "cerio", "praseodímio", "praseodimio",
        "neodímio", "neodimio", "samário", "samario", "európio", "europio",
        "gadolínio", "gadolinio", "térbio", "terbio", "disprósio", "disprosio",
        "hólmio", "holmio", "érbio", "erbio", "túlio", "tulio",
        "itérbio", "iterbio", "lutécio", "lutecio", "escândio", "escandio",
        "ítrio", "itrio", "monazita", "bastnaesita", "xenotímio", "xenotimio",
        "alanita", "terras raras", "terra rara",
    ],
    "niobio":   ["nióbio", "niobio", "pirocloro", "columbita", "tantalita", "tântalo", "tantalo"],
    "litio":    ["lítio", "litio", "espodumênio", "espodumenio", "petalita", "lepidolita", "ambligonita"],
    "cobalto":  ["cobalto", "smaltita", "cobaltita", "asbolana"],
    "grafita":  ["grafita", "grafite"],
    "uranio":   ["urânio", "uranio", "uraninita", "pechblenda", "carnotita", "torbernita"],
    "titanio":  ["titânio", "titanio", "ilmenita", "rutilo", "anatásio", "anatasio", "leucoxênio"],
    "vanadio":  ["vanádio", "vanadio", "vanadinita"],
    "manganes": ["manganês", "manganes", "pirolusita", "manganita", "romanechita"],
    "fosfato":  ["fosfato", "apatita", "fosforita"],
    "cobre":    ["cobre", "calcopirita", "calcocita", "bornita", "malaquita"],
    "ouro":     ["ouro", "eletro"],
    "ferro":    ["ferro", "hematita", "magnetita", "siderita", "itabirito"],
    "agregados": ["areia", "brita", "cascalho", "calcário", "calcario", "granito",
                  "basalto", "argila", "saibro", "calhau", "caulim", "quartzo"],
}

def classificar(nome_substancia: str) -> str:
    nome = nome_substancia.lower().strip()
    for categoria, termos in CATEGORIAS.items():
        if any(t in nome for t in termos):
            return categoria
    return "outro"
```

---

## 6. Índices OpenSearch — Cluster MineralRadar

Todos os índices são novos, com mapeamento original. Não há reaproveitamento de índices externos.

### 6.1 `anm_processos_v001` — Índice principal

Campos originais do MineralRadar (além dos campos brutos do shapefile ANM):

```json
{
  "categoria_mineral_estrategica": "terra_rara",
  "cfem_total_historico": 1240500.00,
  "cfem_ultima_arrecadacao": "2024-12-15",
  "cfem_anos_producao": 8,
  "restricoes_geo": [
    {
      "tipo": "terra_indigena",
      "nome": "Terra Indígena Kayapó",
      "id_restricao": "TI-0001-PA",
      "area_sobreposta_ha": 1245.7,
      "percentual_sobreposicao": 34.2,
      "nivel_risco": "critico"
    }
  ],
  "ativo": true
}
```

### 6.2 Todos os índices previstos


| Índice                  | Fonte                            | Volume estimado | Tamanho | Conteúdo                                                            |
| ----------------------- | -------------------------------- | --------------- | ------- | ------------------------------------------------------------------- |
| `anm_processos_v001`    | ANM SIGMINE + CFEM + SCM + SICOP | ~907K docs (geo) | ~1,5 GB | Polígonos SIGMINE completos (**~267K ativos** + **~664K inativos**, mai/2026); SCM tabular enriquece campos |
| `anm_substancia_v001`   | ANM tabela de substâncias        | 862 docs        | ~5 MB   | 862 substâncias + `categoria_estrategica` + embeddings k-NN         |
| `rfb_cnpj_v001`         | Receita Federal (filtrado)       | **~350K docs**  | ~400 MB | Empresas relevantes ao domínio mineral (ver §6.3)                   |
| `ibge_municipio_v001`   | IBGE — malha municipal           | 5.631 docs      | ~950 MB | Municípios com geo_shape e hierarquia                               |
| `rfb_cnae_v001`         | Receita Federal — tabela CNAE    | 2.394 docs      | ~20 MB  | CNAEs com embeddings k-NN                                           |
| `mr_geoquimica_v001`    | CPRM Geoquímica (OGC API)        | ~65K docs       | ~80–120 MB | Amostras rocha + mineral/minério — nested `analises`, `analitos`, `geo_point` |
| `cprm_ocorrencias_v001` | CPRM GeoBank (OGC API / WFS)     | ~36–50K docs    | ~25–200 MB | Ocorrências minerais (produção MineralRadar: **`mr_cprm_v001`**)            |
| `restricoes_geo_v001`   | FUNAI + IBAMA + IBGE + INCRA     | ~100K polígonos | ~2 GB   | TIs, UCs, biomas, SIGEF — geometrias para sobreposição              |
| `anm_cfem_v001`         | ANM CFEM CSV                     | ~3M docs        | ~300 MB | Arrecadação histórica por processo/empresa/substância               |
| `mercado_mineral_v001`  | ComexStat MDIC                   | ~500K docs      | ~150 MB | Exportações/importações por NCMs de minerais estratégicos           |
| **Total estimado**      |                                  | **~25,5M docs** | **~10 GB** |                                                                  |

### 6.3 Estratégia de pré-filtro RFB (decisão arquitetural)

O bulk RFB tem 221M estabelecimentos / 68 GB. Indexar tudo seria desnecessário para o domínio mineral. Aplicamos um pré-filtro de 4 critérios que reduz o índice em ~630x:

| Critério | Volume estimado |
|---|---|
| Titulares de processos ANM (SIGMINE geo indexado) | ~150K CNPJs |
| CNAE Indústrias Extrativas (Seção B: 05xx-09xx) | ~80K CNPJs |
| Top arrecadadores CFEM (histórico completo) | ~20K CNPJs |
| Sócios PJ recursivos (1 nível) das empresas dos critérios 1-3 | +50K CNPJs |
| **Total deduplicado** | **~350K CNPJs** |

**Fallback on-demand:** CNPJs novos (entre refreshes mensais da RFB) são buscados via [BrasilAPI](https://brasilapi.com.br) com cache de 30 dias no Redis e indexados sob demanda. Holdings estrangeiras são tratadas via CVM (B3-listadas) e OpenCorporates — fora deste índice.

Ver `SPEC_ETL_MINERALRADAR.md` §9 para implementação detalhada.

### 6.4 Provedor de cluster — opções por fase

| Fase | Recomendação | Custo/mês | Observação |
|---|---|---|---|
| **Dev local** | Docker Compose | US$ 0 | Já no boilerplate (`backend/docker-compose.local.yml`) |
| **Fase 1 (MG + PA, ~60K processos)** | Oracle Cloud Free Tier (2 VMs ARM) | US$ 0 | Forever free — 4 OCPU + 24 GB RAM |
| **Fase 2 (Brasil completo)** | AWS `t3.small.search` × 1 + 30 GB EBS | ~US$ 25 | Free tier 12 meses depois cobra |
| **Produção self-hosted** | Hetzner VPS 16 GB RAM | ~EUR 16 | Mais barato que AWS no longo prazo |
| **Produção AWS managed** | AWS `r6g.large.search` × 2 (k-NN) | ~US$ 300 | Necessário para k-NN em escala |

> Sem o pré-filtro RFB, o cluster precisaria de ~80 GB e instâncias `r6g.xlarge` (~US$ 600/mês) — Oracle Free Tier seria inviável.


---

## 7. MCP Servers

Todos os MCP Servers são implementações originais do MineralRadar, construídas sobre o protocolo aberto MCP (Anthropic).

### 7.1 MCP Jazidas Mineral (Fase 1)


| Tool                            | Descrição                                                                       |
| ------------------------------- | ------------------------------------------------------------------------------- |
| `buscar_processos`              | Busca por substância, município, fase, categoria estratégica, polígono, raio    |
| `buscar_processos_inativos`     | Inclui processos históricos encerrados — análise de reativação                  |
| `detalhes_processo`             | Ficha completa: fases, eventos, títulos, titular, substâncias, área, restrições |
| `buscar_por_categoria`          | Todos os processos de uma categoria estratégica em uma área                     |
| `verificar_vigencia_substancia` | Resolve nome de substância → IDs canônicos via k-NN embeddings                  |
| `jazidas_por_poligono`          | Processos que intersectam um polígono GeoJSON desenhado no mapa                 |
| `analisar_cfem_processo`        | Histórico CFEM de um processo: arrecadação real vs. processo inativo            |
| `verificar_restricoes_geo`      | Sobreposições TI/UC/bioma pré-computadas de um processo                         |


### 7.2 MCP Empresas (Fase 1)


| Tool                   | Descrição                                                  |
| ---------------------- | ---------------------------------------------------------- |
| `buscar_empresa`       | Busca por razão social, CNPJ, CNAE, município              |
| `detalhes_empresa`     | Ficha completa: capital, sócios, CNAEs, situação cadastral |
| `processos_do_titular` | Todos os processos ANM de um CNPJ/titular                  |
| `socios_da_empresa`    | Estrutura societária completa                              |


### 7.3 MCP Geo (Fase 1)


| Tool                       | Descrição                                           |
| -------------------------- | --------------------------------------------------- |
| `geocodificar`             | Endereço/município → coordenadas                    |
| `isocrona`                 | Polígono de alcance por tempo de viagem (truck/car) |
| `calcular_rota`            | Rota origem → destino com distância e tempo         |
| `municipio_por_coordenada` | Identifica município a partir de lat/lon            |


### 7.4 MCP Ambiental (Fase 2)


| Tool                              | Descrição                                                           | Fonte        |
| --------------------------------- | ------------------------------------------------------------------- | ------------ |
| `terras_indigenas_na_area`        | TIs por estágio (homologada, declarada, em estudo) em polígono/raio | FUNAI        |
| `unidades_conservacao_na_area`    | UCs por categoria (APA, REBIO, PARNA, etc.) em polígono/raio        | IBAMA CNUC   |
| `bioma_do_ponto`                  | Bioma de uma coordenada                                             | IBGE         |
| `calcular_sobreposicoes_processo` | Todas as sobreposições de um NUP (pré-computado PostGIS)            | Índice local |


### 7.5 MCP CFEM (Fase 2)


| Tool                      | Descrição                                           | Fonte    |
| ------------------------- | --------------------------------------------------- | -------- |
| `historico_cfem_processo` | Arrecadação anual de CFEM via CNPJ do titular       | ANM CFEM |
| `ranking_cfem_substancia` | Maiores arrecadadores por substância e estado       | ANM CFEM |
| `cfem_municipio`          | Total CFEM de um município por substância e período | ANM CFEM |


### 7.6 MCP Monitoramento (Fase 3)


| Tool                       | Descrição                                               | Fonte         |
| -------------------------- | ------------------------------------------------------- | ------------- |
| `prazos_processo`          | Próximos vencimentos (autorização pesquisa, RAL, DIPEM) | ANM SICOP/SCM |
| `publicacoes_dou_processo` | Publicações DOU para um NUP                             | API IN DOU    |


---

## 8. Agente LangGraph

### 8.1 System prompt

```
Você é o MineralRadar, um agente de inteligência especializado em mineração
estratégica e processos minerários no Brasil.

Você combina o conhecimento de um geólogo experiente, um advogado minerário
e um analista de mercado de commodities.

Domínio central:
- Processos minerários ANM: fases, substâncias, titulares, vigências, histórico
- Minerais estratégicos: Terras Raras, Lítio, Nióbio, Cobalto, Grafita, Urânio
- Restrições legais: Terras Indígenas, Unidades de Conservação, faixas de fronteira
- Due diligence mineral: CFEM (indicador de produção real), estrutura societária,
  passivos ambientais, histórico de fases
- Contexto de mercado: cadeias de suprimento globais, preços, geopolítica de
  minerais críticos (IRA/EUA, CMRD/Europa, domínio chinês de TRs)

Você analisa dados de fontes públicas oficiais: ANM, CPRM, IBAMA, FUNAI, IBGE, RFB.
Cite sempre a fonte dos dados na resposta.

Responda em português do Brasil, com precisão técnica minerária.
Preços e cotações de minerais estratégicos são parte do seu domínio —
não os trate como fora de escopo.
```

### 8.2 Rotas do router


| Rota                     | Gatilhos                                                              | MCPs acionados                        |
| ------------------------ | --------------------------------------------------------------------- | ------------------------------------- |
| `prospectar_estrategico` | "terras raras", "lítio", "nióbio", "TR", "minerais críticos"          | Jazidas + CPRM + Ambiental            |
| `due_diligence`          | "analisar processo", "quem controla", "risco", "NUP", "due diligence" | Jazidas + Empresas + CFEM + Ambiental |
| `verificar_restricoes`   | "TI", "UC", "terra indígena", "área protegida", "IBAMA"               | Ambiental + Jazidas                   |
| `analisar_cfem`          | "produção real", "CFEM", "arrecadação", "produz mesmo"                | CFEM + Jazidas + Empresas             |
| `inteligencia_mercado`   | "preço", "cotação", "exportação", "mercado", "supply chain"           | Mercado (Fase 3)                      |
| `monitorar_prazos`       | "vence", "prazo", "renovação", "RAL", "DIPEM", "obrigação"            | Monitoramento (Fase 3)                |
| `busca_historica`        | "inativo", "histórico", "extinto", "cancelado", "antes de"            | Jazidas (inativos=True)               |
| `buscar_jazida_geo`      | substância + localização — fluxo principal                            | Jazidas + Geo                         |
| `detalhes_empresa`       | empresa, CNPJ, titular                                                | Empresas                              |
| `calcular_rota`          | logística, porto, ferrovia, escoamento                                | Geo + Jazidas                         |


---

## 9. Frontend

### 9.1 Estrutura de páginas


| Página          | Rota            | Descrição                                 |
| --------------- | --------------- | ----------------------------------------- |
| Login           | `/login`        | Autenticação Azure AD                     |
| Dashboard       | `/`             | KPIs, mapa inicial, alertas ativos        |
| Workspace       | `/workspace`    | Chat IA + mapa integrado                  |
| Projetos        | `/projetos`     | Lista de projetos de exploração           |
| Projeto detalhe | `/projetos/:id` | Análises e jazidas favoritas do projeto   |
| Análise         | `/analises/:id` | Resultado de análise com mapa e dados     |
| Alertas         | `/alertas`      | Prazos e monitoramento contínuo (Fase 2+) |


### 9.2 Componentes originais a implementar


| Componente                  | Módulo      | Descrição                                                         |
| --------------------------- | ----------- | ----------------------------------------------------------------- |
| `ChatPanel`                 | Global      | Chat streaming com agente MineralRadar                            |
| `MapaMineralRadar`          | Global      | MapLibre com layers configuráveis                                 |
| `LayerControl`              | Global      | Controle de camadas: ANM, CPRM, TIs, UCs, biomas                  |
| `ProcessoCard`              | Módulos 1/2 | Card de processo ANM com badge de categoria estratégica           |
| `RestricoesPainel`          | Módulo 3    | Lista de sobreposições TI/UC/bioma com % de área e nível de risco |
| `CfemChart`                 | Módulo 2    | Gráfico de arrecadação CFEM histórica por processo                |
| `CategoriaEstrategicaBadge` | Global      | Badge colorido: terra_rara, litio, niobio, etc.                   |
| `OcorrenciasCPRMLayer`      | Módulo 1    | Pontos de ocorrências minerais CPRM no mapa                       |
| `AlertasPrazosWidget`       | Módulo 4    | Widget de próximos vencimentos processuais                        |
| `EmpresaCard`               | Módulo 2    | Card de empresa com estrutura societária                          |


### 9.3 Layers do mapa


| Layer                   | Padrão      | Fonte de dados                             |
| ----------------------- | ----------- | ------------------------------------------ |
| Processos ANM ativos    | ✅ Ligado    | OpenSearch `anm_processos_v001` (ativos)   |
| Processos ANM inativos  | ⬜ Desligado | OpenSearch `anm_processos_v001` (inativos) |
| Ocorrências CPRM        | ⬜ Desligado | OpenSearch `cprm_ocorrencias_v001` ou **`mr_cprm_v001`** |
| Pontos geoquímicos CPRM | ⬜ Desligado | OpenSearch **`mr_geoquimica_v001`** (tool `geoquimica_proxima`) |
| Terras Indígenas        | ⬜ Desligado | GeoJSON `restricoes_geo_v001`              |
| Unidades de Conservação | ⬜ Desligado | GeoJSON `restricoes_geo_v001`              |
| Mapa Geológico 1:1M     | ⬜ Desligado | WMS CPRM `geoportal.sgb.gov.br`            |
| Biomas IBGE             | ⬜ Desligado | GeoJSON `restricoes_geo_v001`              |


---

## 10. ETL — Resumo executivo

Ver `SPEC_ETL_MINERALRADAR.md` para arquitetura detalhada. Pontos críticos:

### 10.1 Endpoints ANM confirmados (05/05/2026)

Protocolo: **HTTP puro** (não FTP). Requer `User-Agent` de browser — sem ele retorna 403.


| Dado                    | URL                                                                                   | Tamanho    | Frequência |
| ----------------------- | ------------------------------------------------------------------------------------- | ---------- | ---------- |
| SIGMINE por UF          | `https://dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/{UF}.zip`               | 168KB–22MB | Diária     |
| SIGMINE Brasil completo | `https://dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/BRASIL.zip`             | ~123MB     | Diária     |
| SIGMINE Inativos        | `https://dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/PROCESSOS_INATIVOS.zip` | ~150MB     | Diária     |
| SCM Microdados mestre   | `https://dadosabertos.anm.gov.br/SCM/microdados/microdados-scm.zip`                   | ~313MB     | Diária     |
| CFEM completo           | `https://dadosabertos.anm.gov.br/CFEM/CFEM_Arrecadacao.csv`                           | ~221MB     | Diária     |
| SICOP                   | `https://dadosabertos.anm.gov.br/SICOP/Sicop.csv`                                     | ~211MB     | Diária     |


### 10.2 Arquitetura ETL

```
Fontes públicas (ANM, CPRM, FUNAI, IBAMA, RFB, IBGE, MDIC)
    ↓ Python Bots (download, parse, validação, upsert)
PostgreSQL + PostGIS (staging)
    raw_*   →   staging_*   →   vw_processos_completo
    (sobreposições calculadas aqui via ST_Intersects)
    ↓ bot_indexador.py (hash diff → só reindexar o que mudou)
OpenSearch MineralRadar (cluster próprio)
```

### 10.3 Repositório ETL

Repositório separado: `mineral-radar-etl`

---

## 11. Roadmap de implementação

### Fase 1 — Fundação (0–6 semanas)


| #    | Tarefa                                                                      | Esforço   | Status |
| ---- | --------------------------------------------------------------------------- | --------- | ------ |
| 1.1  | Setup repositório `mineral-radar` + estrutura de pastas + boilerplate       | 1 dia     | ✅ Concluído |
| 1.2  | Setup repositório `mineral-radar-etl` + Docker Compose (PostgreSQL+PostGIS) | 2 dias    | ⬜ Pendente |
| 1.3  | Backend: FastAPI scaffold + autenticação Azure AD                           | 3 dias    | 🔄 Parcial (scaffold feito, auth pendente) |
| 1.4  | Classificador de minerais estratégicos — módulo Python puro                 | 2 dias    | ⬜ Pendente |
| 1.5  | `bot_anm.py` — download SIGMINE por UF, GeoPandas, upsert PostgreSQL        | 1 semana  | ⬜ Pendente |
| 1.6  | `bot_rfb.py` — download bulk RFB, parse CSV, upsert PostgreSQL              | 1 semana  | ⬜ Pendente |
| 1.7  | `bot_indexador.py` — índice `anm_processos_v001` no novo cluster            | 1 semana  | ⬜ Pendente |
| 1.8  | MCP Jazidas Mineral — tools para domínio mineral estratégico                | 1 semana  | 🔄 Parcial (estrutura boilerplate) |
| 1.9  | MCP Empresas — cross-reference ANM ↔ RFB                                    | 1 semana  | 🔄 Parcial (estrutura boilerplate) |
| 1.10 | MCP Geo — geocoding, isocrona, rota (Azure Maps)                            | 3 dias    | 🔄 Parcial (estrutura boilerplate) |
| 1.11 | Agente LangGraph — grafo MineralRadar + router + system prompt              | 1 semana  | 🔄 Parcial (grafo genérico, system prompt pendente) |
| 1.12 | Frontend: renomear entidades (obras→projetos, estudos→analises) + UI mineral | 2 semanas | ⬜ Pendente |


### Fase 2 — Inteligência Mineral (6–16 semanas)


| #   | Tarefa                                                              |
| --- | ------------------------------------------------------------------- |
| 2.1 | `bot_cfem.py` + MCP CFEM                                            |
| 2.2 | `bot_funai.py` + `bot_ibama.py` + cálculo sobreposições PostGIS     |
| 2.3 | MCP Ambiental + layer TIs/UCs no mapa                               |
| 2.4 | `bot_cprm.py` + índice **`mr_cprm_v001`** (ou legado `cprm_ocorrencias_v001`) + layer CPRM no mapa |
| 2.4b | `bot_geoquimica.py` + índice **`mr_geoquimica_v001`** + tool MCP `geoquimica_proxima`            |
| 2.5 | SCM/SICOP — prazos processuais no índice principal                  |
| 2.6 | Frontend: componentes RestricoesPainel, CfemChart, AlertasPrazos    |


### Fase 3 — Diferencial competitivo (4–9 meses)


| #   | Tarefa                                                      |
| --- | ----------------------------------------------------------- |
| 3.1 | Integração Metals-API — preços TR em tempo real             |
| 3.2 | ComexStat MDIC — exportações por NCM mineral                |
| 3.3 | MCP Monitoramento — SICOP + DOU + alertas de prazo          |
| 3.4 | IBAMA autuações e embargos por empresa                      |
| 3.5 | CVM — dados de mineradoras listadas                         |
| 3.6 | USGS MRDS — depósitos minerais globais                      |
| 3.7 | Sensoriamento remoto — Sentinel-2 para mapeamento espectral |


---

## 12. Decisões pendentes


| Decisão                   | Contexto                                                           | Status         | Impacto                                 |
| ------------------------- | ------------------------------------------------------------------ | -------------- | --------------------------------------- |
| **Modelo de negócio**     | SaaS por usuário/empresa vs. API B2B vs. freemium                  | ⬜ Em aberto   | Afeta controle de acesso e precificação |
| **Orquestrador ETL**      | Apache Airflow vs. Prefect                                         | ⬜ Em aberto   | Decidir no início da Fase 1.2           |
| **Provedor OpenSearch**   | Oracle Free Tier (Fase 1) → AWS managed ou Hetzner self-hosted (Fase 2+) | ✅ Decidido por fase (ver §6.4) | Custo vs. controle |
| **Rename de entidades**   | Frontend usa `obras/estudos` — renomear para `projetos/analises`   | ✅ Decidido: `Projeto` + `Analise` (Fase 1.12) | UX — boilerplate a atualizar   |
| **Escopo inicial de UFs** | MG + PA + BA (maior volume de TRs) vs. Brasil completo             | ✅ Decidido: começar por MG + PA (Carajás + QF) | ETL da Fase 1 |
| **Preços de TR**          | Metals-API (freemium) vs. scraping público vs. USGS gratuito       | ⬜ Em aberto   | Módulo 5 — Fase 3                       |


---

## 13. Requisitos não-funcionais

### 13.1 Performance
| Métrica | Target |
| ------- | ------ |
| Latência de query OpenSearch (p95) | < 500ms |
| Primeira resposta do agente (streaming SSE) | < 2s |
| Throughput da API | ≥ 50 req/s por instância |
| Tempo máximo de ingestão ETL (ANM SIGMINE Brasil) | < 4h/ciclo diário |

### 13.2 Observabilidade
- **Logs estruturados** (JSON) em toda a camada backend — request ID, user ID, latência, tool calls
- **Métricas ETL**: documentos ingeridos / atualizados / erros por run, expostos via `/metrics` (Prometheus)
- **Alertas operacionais**: falha no download ANM, queda no volume de documentos (>10% vs. run anterior), erros de embedding
- **Tracing distribuído** (OpenTelemetry) para rastrear fluxo completo: request → agent → MCP → OpenSearch

### 13.3 Segurança e dados
- Dados de fontes públicas oficiais — sem PII sensível além do que está no CNPJ público RFB
- Autenticação via Azure AD (MSAL) com JWT — todos os endpoints protegidos
- Secrets via variáveis de ambiente (`.env`) — nunca hardcoded no repositório
- Rate limiting por usuário na API de chat (evitar abuso de tokens LLM)

---

## 14. Documentos relacionados


| Documento                            | Conteúdo                                                               |
| ------------------------------------ | ---------------------------------------------------------------------- |
| `BASES_DADOS_MINERALRADAR.md`        | Mapeamento completo de fontes de dados com endpoints verificados       |
| `SPEC_ETL_MINERALRADAR.md`           | Arquitetura detalhada do ETL: PostgreSQL+PostGIS → OpenSearch          |
| `COMPARATIVO_SUPPLYRADAR_JAZIDA.md`  | Análise competitiva: MineralRadar vs. Jazida.com                       |
| `INDICES_OPENSEARCH_MINERALRADAR.md` | Mapeamentos completos dos índices OpenSearch (a criar)                 |


---

*Documento de especificação — v1.1. Construído sob princípio de Clean Room Design.*  
*Toda implementação usa exclusivamente bibliotecas open source e dados de fontes públicas oficiais.*