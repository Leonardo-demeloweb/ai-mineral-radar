# Estudo: Cenários de Busca Híbrida — Análise de Viabilidade

> **Data:** 24/02/2026
> **Contexto:** Validação da capacidade dos MCPs Jazidas + Empresas + tools nativas QPT para resolver 4 cenários reais de uso.

---

## Cenários Analisados

| # | Pergunta do Usuário | Complexidade |
|---|---------------------|:------------:|
| 1 | "procurar gráfica em Contagem/MG" | Simples |
| 2 | "procurar pedreiras em Betim/MG" | Média |
| 3 | "procurar concreteiras em Belo Horizonte/MG" | Média |
| 4 | "estou em uma obra em Sete Lagoas: quais concreteiras que têm minas de areia próxima?" | **Alta** |

---

## Cenário 1: "procurar gráfica em Contagem/MG"

### Veredicto: ✅ Resolve com MCP Empresas (`buscar_empresas`)

### Análise

"Gráfica" é uma atividade econômica (indústria gráfica / impressão) — não tem relação com mineração. Isto significa que o **MCP Jazidas não participa**. O cenário é 100% resolvido pelo MCP Empresas.

### Fluxo do Agente

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuário: "procurar gráfica em Contagem/MG"                                │
│                                                                            │
│  PASSO 1 — Resolver localização                                            │
│  O agente precisa das coordenadas de Contagem/MG.                          │
│  Opção A: QPT nativo → SearchIndexTool em ibge_municipio_v001              │
│           query: { match: { nome: "Contagem" }, term: { siglaUF: "mg" } }  │
│           retorna: { lat: -19.9320, lon: -44.0539 }                        │
│  Opção B: MCP Geo (quando implementado) → buscar_municipio                 │
│                                                                            │
│  PASSO 2 — Resolver CNAE via busca semântica                               │
│  MCP Empresas → buscar_empresas(termo_busca="gráfica", lat, lon, raio_km)  │
│  Internamente:                                                             │
│    CnaeResolver.resolver("gráfica")                                        │
│      → k-NN embedding "gráfica" em rfb_cnae_v001 (2.394 docs)             │
│      → Matches prováveis:                                                  │
│        • 1811-3/01 — Impressão de jornais                                  │
│        • 1811-3/02 — Impressão de livros, revistas e publicações           │
│        • 1812-1/00 — Impressão de material de segurança                    │
│        • 1813-0/01 — Impressão de material para uso publicitário           │
│        • 1813-0/99 — Impressão de material para outros usos                │
│        • 1821-1/00 — Serviços de pré-impressão                             │
│      → Retorna códigos CNAE para terms filter                              │
│                                                                            │
│  PASSO 3 — Buscar no cnpj_v002                                             │
│    Query: terms cnaeFiscalPrincipal.codigo + geo_distance(50km de Contagem)│
│           + situacaoCadastral.codigo = "02" (ativas)                       │
│    Retorna: empresas gráficas próximas com contato, endereço, localização  │
└────────────────────────────────────────────────────────────────────────────┘
```

### Por que funciona

| Componente | Papel | Fonte de dados |
|-----------|-------|----------------|
| **CnaeResolver (k-NN)** | Transforma "gráfica" → CNAEs de impressão/gráfica | `rfb_cnae_v001` (embedding) |
| **geo_distance** | Filtra por proximidade a Contagem | `cnpj_v002.localizacao` |
| **QPT ou MCP Geo** | Resolve "Contagem/MG" → coordenadas | `ibge_municipio_v001` |

**O k-NN é o diferencial:** O termo "gráfica" não aparece literalmente em nenhum nome de CNAE. Um match BM25 direto por "gráfica" falharia. Mas o embedding semântico de "gráfica" está próximo de "impressão", "artes gráficas", "pré-impressão" no espaço vetorial — e o `CnaeResolver` encontra os códigos corretos.

---

## Cenário 2: "procurar pedreiras em Betim/MG"

### Veredicto: ✅ Resolve com MCP Jazidas + MCP Empresas (complementares)

### Análise

"Pedreira" tem **duas dimensões**:

1. **Processo minerário** (jazida de extração de pedra) — domínio ANM
2. **Empresa** (empresa que opera pedreira) — domínio CNPJ/CNAE

O agente (LangGraph) pode usar **ambos os MCPs** para uma resposta completa.

### Fluxo do Agente — Via MCP Jazidas

```
┌────────────────────────────────────────────────────────────────────────────┐
│  ROTA A: Processos minerários (jazidas de pedra)                           │
│                                                                            │
│  MCP Jazidas → buscar_fornecedores(substancia="pedra", lat, lon, raio_km) │
│  Internamente:                                                             │
│    SubstanciaResolver.resolver("pedra")                                    │
│      → k-NN em anm_substancia_v001 (862 substâncias)                      │
│      → Matches prováveis:                                                  │
│        • GRANITO (id: 200100)                                              │
│        • GNAISSE (id: 200101)                                              │
│        • BASALTO (id: 200102)                                              │
│        • DIABÁSIO (id: 200103)                                             │
│        • PEDRA PORTUGUESA (se existir no catálogo)                         │
│      → anm_v002: geo_distance de Betim + idSubstancias terms              │
│      → cnpj_v002: enriquece com dados da empresa titular                  │
│    Resultado: processos de pedreira com titular, contato, localização      │
└────────────────────────────────────────────────────────────────────────────┘
```

### Fluxo do Agente — Via MCP Empresas

```
┌────────────────────────────────────────────────────────────────────────────┐
│  ROTA B: Empresas com CNAE de extração/britamento de pedra                 │
│                                                                            │
│  MCP Empresas → buscar_empresas(termo_busca="pedreira", lat, lon, raio_km)│
│  Internamente:                                                             │
│    CnaeResolver.resolver("pedreira")                                       │
│      → k-NN em rfb_cnae_v001 (2.394 CNAEs)                                │
│      → Matches prováveis:                                                  │
│        • 0810-0/02 — Extração de granito e beneficiamento                  │
│        • 0810-0/99 — Extração e beneficiamento de outros minerais          │
│        • 2391-5/01 — Britamento de pedras (brita)                          │
│        • 2391-5/02 — Aparelhamento de pedras para construção               │
│      → cnpj_v002: geo_distance de Betim + terms CNAE                      │
│    Resultado: empresas pedreiras com contato, endereço, CNAE               │
└────────────────────────────────────────────────────────────────────────────┘
```

### Por que as duas rotas são complementares

| Dimensão | Rota A (Jazidas) | Rota B (Empresas) |
|----------|:----------------:|:-----------------:|
| **O que encontra** | Processos ANM de pedra **com localização da jazida** | Empresas com CNAE de pedreira **com sede/filial** |
| **Dados únicos** | Fase do processo, área em hectares, polígono da concessão | Sócios, capital social, filiais, CNAE secundários |
| **Geo** | Localização da **jazida** (onde a pedra está) | Localização da **empresa** (onde a sede fica) |
| **Titular** | Quem detém o direito minerário | Quem é a empresa legalmente |

O agente inteligente usaria ambas e **cruzaria**: "A empresa X (CNPJ) é titular do processo Y (ANM) e opera pedreira em Betim".

---

## Cenário 3: "procurar concreteiras em Belo Horizonte/MG"

### Veredicto: ✅ Resolve com MCP Empresas (`buscar_empresas`)

### Análise

"Concreteira" é uma empresa que fabrica concreto — atividade industrial do CNAE, sem relação direta com processos minerários (concreteira **compra** areia, não **extrai**). O MCP Empresas resolve sozinho.

### Fluxo do Agente

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuário: "procurar concreteiras em Belo Horizonte/MG"                     │
│                                                                            │
│  PASSO 1 — Resolver coordenadas de BH                                     │
│  QPT → ibge_municipio_v001: { lat: -19.9167, lon: -43.9345 }              │
│                                                                            │
│  PASSO 2 — Resolver CNAE semântico                                         │
│  MCP Empresas → buscar_empresas(termo_busca="concreteira", lat, lon, 50km)│
│  Internamente:                                                             │
│    CnaeResolver.resolver("concreteira")                                    │
│      → k-NN embedding "concreteira" em rfb_cnae_v001                       │
│      → Matches prováveis:                                                  │
│        • 2330-3/01 — Fabricação de estruturas pré-moldadas de concreto     │
│        • 2330-3/02 — Fabricação de artefatos de cimento para construção    │
│        • 2330-3/99 — Fabricação de outros artefatos de concreto/cimento    │
│        • 2320-6/00 — Fabricação de cimento                                 │
│        • 2399-1/01 — Decoração, lapidação e outros trabalhos em cerâmica   │
│                                                                            │
│  PASSO 3 — Buscar cnpj_v002                                               │
│    terms CNAE + geo_distance 50km de BH + ativas                           │
│    Resultado: concreteiras próximas com contato e localização              │
└────────────────────────────────────────────────────────────────────────────┘
```

### Por que funciona

A palavra "concreteira" **não existe** em nenhum nome de CNAE oficial. A CNAE usa "fabricação de artefatos de concreto", "pré-moldados de concreto", etc. O **k-NN semântico** é essencial aqui:

| Busca | Match BM25 ("concreteira") | k-NN Semântico ("concreteira") |
|-------|:--------------------------:|:------------------------------:|
| Resultado | ❌ 0 matches (palavra não existe nos CNAEs) | ✅ Encontra "concreto", "cimento", "pré-moldado" |

Este é exatamente o caso de uso que justifica a existência da tool custom `buscar_empresas` vs deixar o QPT resolver sozinho.

---

## Cenário 4: "estou em uma obra em Sete Lagoas: quais concreteiras que têm minas de areia próxima?"

### Veredicto: ✅ Resolve, mas requer **orquestração multi-MCP** pelo agente

### Análise

Esta é a pergunta mais complexa — combina **3 domínios**:

1. **Geocoding**: resolver "Sete Lagoas" → coordenadas
2. **Mineração**: encontrar minas de areia próximas (processos ANM)
3. **Empresas**: encontrar concreteiras (empresas com CNAE de concreto)
4. **Cruzamento**: quais concreteiras **também** são titulares de minas de areia?

### Fluxo do Agente (Multi-Tool, Multi-MCP)

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuário: "estou em uma obra em Sete Lagoas: quais concreteiras            │
│            que têm minas de areia próxima?"                                │
│                                                                            │
│  O agente (LangGraph) decompõe em sub-tarefas:                             │
│                                                                            │
│  ═══════════════════════════════════════════════════════════════════════    │
│  CICLO 1: Resolver localização                                             │
│  ═══════════════════════════════════════════════════════════════════════    │
│  QPT → ibge_municipio_v001                                                 │
│  "Sete Lagoas/MG" → { lat: -19.4658, lon: -44.2469 }                      │
│                                                                            │
│  ═══════════════════════════════════════════════════════════════════════    │
│  CICLO 2: Buscar fornecedores de areia (processos ANM + empresa)           │
│  ═══════════════════════════════════════════════════════════════════════    │
│  MCP Jazidas → buscar_fornecedores(                                        │
│      substancia="areia",                                                   │
│      latitude=-19.4658,                                                    │
│      longitude=-44.2469,                                                   │
│      raio_km=100,           # Raio maior (minas podem estar afastadas)     │
│      incluir_contatos=true,                                                │
│  )                                                                         │
│  Internamente:                                                             │
│    SubstanciaResolver("areia")                                             │
│      → k-NN: AREIA (200200), AREIA LAVADA (200207), AREIA COMUM (200202)  │
│    anm_v002: geo_distance 100km + terms idSubstancias                      │
│    cnpj_v002: enriquece titulares com dados empresa                        │
│                                                                            │
│  Resultado: 30 processos de areia com titulares e CNPJs                    │
│  Ex: "CONCRETEIRA SETE LAGOAS LTDA" — CNPJ 12.345.678/0001-00             │
│      "MINERAÇÃO XYZ SA" — CNPJ 98.765.432/0001-00                         │
│      ...                                                                   │
│                                                                            │
│  ═══════════════════════════════════════════════════════════════════════    │
│  CICLO 3: Buscar concreteiras na região                                    │
│  ═══════════════════════════════════════════════════════════════════════    │
│  MCP Empresas → buscar_empresas(                                           │
│      termo_busca="concreteira",                                            │
│      latitude=-19.4658,                                                    │
│      longitude=-44.2469,                                                   │
│      raio_km=50,                                                           │
│  )                                                                         │
│  Internamente:                                                             │
│    CnaeResolver("concreteira")                                             │
│      → k-NN: 2330-3/01, 2330-3/02, 2330-3/99, 2320-6/00                   │
│    cnpj_v002: geo_distance 50km + terms CNAE                               │
│                                                                            │
│  Resultado: 15 concreteiras perto de Sete Lagoas                           │
│  Ex: "CONCRETEIRA SETE LAGOAS LTDA" — CNPJ 12.345.678/0001-00             │
│      "SUPERMIX CONCRETO LTDA" — CNPJ 44.556.677/0001-00                    │
│      ...                                                                   │
│                                                                            │
│  ═══════════════════════════════════════════════════════════════════════    │
│  CICLO 4: O LLM cruza os resultados (raciocínio)                          │
│  ═══════════════════════════════════════════════════════════════════════    │
│  O agente compara os CNPJs dos titulares de minas de areia (Ciclo 2)       │
│  com os CNPJs das concreteiras (Ciclo 3):                                  │
│                                                                            │
│  Interseção: "CONCRETEIRA SETE LAGOAS LTDA" (CNPJ 12.345.678)             │
│  → Aparece nos DOIS resultados!                                            │
│  → É concreteira (CNAE 2330-3/01) E titular de mina de areia (ANM)        │
│                                                                            │
│  Resposta ao usuário:                                                      │
│  "Encontrei 1 concreteira que também opera mina de areia próxima:          │
│   - CONCRETEIRA SETE LAGOAS LTDA (12.345.678/0001-00)                      │
│     • Concreteira: CNAE 2330-3/01 - Fab. pré-moldados de concreto          │
│     • Mina de areia: Processo ANM 820.603/2018 — 12.3 km da obra           │
│     • Contato: (31) 3771-XXXX                                              │
│                                                                            │
│   Outras concreteiras próximas (sem mina própria):                         │
│   - SUPERMIX CONCRETO LTDA — 8.5 km                                        │
│   - ..."                                                                   │
└────────────────────────────────────────────────────────────────────────────┘
```

### Por que funciona — a orquestração Agentic

| Etapa | MCP/Tool | O que faz | Sem busca híbrida |
|-------|----------|-----------|:-----------------:|
| 1 | QPT (nativa) | Geocoding "Sete Lagoas" → lat/lon | ❌ Precisaria API externa |
| 2 | MCP Jazidas `buscar_fornecedores` | areia → k-NN substância → ANM → CNPJ | ❌ Impossível em 1 query |
| 3 | MCP Empresas `buscar_empresas` | concreteira → k-NN CNAE → CNPJ | ❌ "concreteira" não existe no CNAE |
| 4 | LLM raciocínio | Cruza CNPJs dos dois resultados | ❌ Sem agente, sem cruzamento |

**O cenário 4 é o caso de uso mais poderoso do sistema** — demonstra que a arquitectura de 3 MCPs + agente LangGraph permite responder perguntas que cruzam domínios sem qualquer código custom para o cruzamento em si.

---

## Resumo de Viabilidade

| # | Cenário | Resolve? | MCPs Envolvidos | Passo Crítico |
|---|---------|:--------:|-----------------|---------------|
| 1 | Gráfica em Contagem | ✅ | Empresas | k-NN "gráfica" → CNAEs de impressão |
| 2 | Pedreiras em Betim | ✅ | Jazidas + Empresas | k-NN "pedra" nos dois domínios (ANM + CNAE) |
| 3 | Concreteiras em BH | ✅ | Empresas | k-NN "concreteira" → CNAEs de concreto/cimento |
| 4 | Concreteiras com minas de areia | ✅ | Jazidas + Empresas + QPT | Orquestração multi-MCP + cruzamento por CNPJ |

### O que torna isso possível

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  PILARES DA BUSCA HÍBRIDA                                                    │
│                                                                             │
│  1. RESOLUÇÃO SEMÂNTICA (k-NN Embedding)                                    │
│     "gráfica" → impressão        (CnaeResolver)                            │
│     "concreteira" → concreto     (CnaeResolver)                            │
│     "pedreira" → granito/basalto (SubstanciaResolver)                      │
│     "areia" → AREIA/AREIA LAVADA (SubstanciaResolver)                      │
│     → Permite linguagem natural sem saber códigos CNAE/ANM                  │
│                                                                             │
│  2. CROSS-INDEX (buscar_fornecedores, detalhes_empresa)                     │
│     anm_substancia_v001 → anm_v002 → cnpj_v002                             │
│     rfb_cnae_v001 → cnpj_v002                                              │
│     → Uma tool cruza 2-3 índices em ~100ms                                  │
│                                                                             │
│  3. ORQUESTRAÇÃO AGENTIC (LangGraph)                                        │
│     O agente decompõe perguntas complexas em sub-tarefas                    │
│     Chama múltiplos MCPs em sequência                                       │
│     Cruza resultados por raciocínio (ex: interseção de CNPJs)              │
│     → Perguntas que cruzam domínios sem código custom                       │
│                                                                             │
│  4. GEO NATIVO                                                              │
│     geo_distance em todos os índices (anm_v002, cnpj_v002)                  │
│     geo_shape contains em ibge_municipio_v001                               │
│     → "perto de X" funciona nativamente                                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Pré-requisito: Geocoding de Municípios

Os 4 cenários precisam resolver nomes de município em coordenadas. Hoje isto é feito pelo **QPT nativo** (SearchIndexTool em `ibge_municipio_v001`). Quando o **MCP Geo** estiver implementado, a tool `buscar_municipio` fará isso de forma mais estruturada e cacheada.

---

*Estudo elaborado em 24/02/2026 — MineralRadar 2.0, arquitectura de busca híbrida com 3 MCPs custom + QPT nativo.*
