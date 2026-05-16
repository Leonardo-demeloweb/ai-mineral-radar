# Estudo: 9 Cenários Reais — Perguntas da Gerente de Suprimentos

> **Data:** 26/02/2026
> **Contexto:** Validação end-to-end da capacidade do MineralRadar 2.0 para resolver perguntas reais de uma gerente de suprimentos de construtora. Cada cenário mapeia a pergunta original para o fluxo exato de tools (3 MCPs custom + Geo Azure Maps + QPT nativo) que o agente LangGraph executaria.
>
> **Arquitetura disponível:**
> - 15 tools custom (5 Jazidas + 3 Empresas + 7 Geo)
> - 6 índices OpenSearch (anm_v002, cnpj_v002, anm_substancia_v001, rfb_cnae_v001, cnpj_v001, ibge_municipio_v001)
> - 2 resolvedores semânticos k-NN (SubstanciaResolver, CnaeResolver)
> - Azure Maps APIs (Route, Range, Search)
> - LangGraph ReAct agent com orquestração multi-MCP

---

## Sumário dos Cenários

| # | Pergunta da Gerente | Complexidade | MCPs Envolvidos |
|---|---------------------|:------------:|-----------------|
| 1 | Empresas de pré-moldados em Montes Claros com CNPJ e contato | Simples | Empresas + Geo |
| 2 | Fornecedores de brita na região de Governador Valadares | Média | Jazidas + Empresas + Geo |
| 3 | Pedreiras em Belo Horizonte | Média | Jazidas + Empresas + Geo |
| 4 | Gráficas em Magé/RJ | Simples | Empresas + Geo |
| 5 | Contatos de empresas de areia em Taubaté/SP | Média | Jazidas + Empresas + Geo |
| 6 | Áreas licenciadas ANM para brita em Teófilo Otoni fora de operação | **Alta** | Jazidas |
| 7 | Pedreira licenciada ANM em Teófilo Otoni que não está funcionando | **Alta** | Jazidas |
| 8 | Maiores empresas de pré-moldados na região do Rio de Janeiro | Média-Alta | Empresas + Geo |
| 9 | Empresas de pavimentação próximo a Itaguaí | Simples | Empresas + Geo |

---

## Cenário 1: "Quais as empresas de pré-moldados existem na cidade de Montes Claros? Com CNPJ e dados para contato"

### Veredicto: ✅ Resolve com MCP Empresas

### Análise

"Pré-moldados" é uma atividade industrial (fabricação de estruturas de concreto) — domínio 100% CNAE/CNPJ. A gerente pede explicitamente CNPJ e contato, que é exatamente o que o `buscar_empresas` retorna com `incluir_contatos=true`.

### Fluxo do Agente

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuária: "Quais as empresas de pré-moldados existem na cidade de         │
│           Montes Claros? Com CNPJ e dados para contato"                   │
│                                                                           │
│  CICLO 1 — Resolver localização                                          │
│  MCP Geo → buscar_municipio(nome="Montes Claros", uf="MG")              │
│  Retorna: { lat: -16.7353, lon: -43.8617, id_ibge: "3143302" }          │
│                                                                           │
│  CICLO 2 — Buscar empresas por CNAE semântico                            │
│  MCP Empresas → buscar_empresas(                                         │
│      termo_busca="pré-moldados",                                         │
│      latitude=-16.7353,                                                   │
│      longitude=-43.8617,                                                  │
│      raio_km=30,                                                          │
│      incluir_contatos=true,                                               │
│  )                                                                        │
│  Internamente:                                                            │
│    CnaeResolver.resolver("pré-moldados")                                 │
│      → k-NN embedding em rfb_cnae_v001 (2.394 CNAEs)                    │
│      → Matches prováveis:                                                 │
│        • 2330-3/01 — Fabricação de estruturas pré-moldadas de concreto   │
│        • 2330-3/02 — Fabricação de artefatos de cimento p/ construção    │
│        • 2330-3/99 — Fabricação de outros artefatos de concreto/cimento  │
│        • 2342-7/02 — Fabricação de artefatos de cerâmica e barro cozido  │
│      → cnpj_v002: terms CNAE + geo_distance(30km de Montes Claros)      │
│        + situacaoCadastral.codigo = "02" (ativas)                        │
│                                                                           │
│  Resultado: lista de empresas com:                                        │
│    • Razão social, nome fantasia                                          │
│    • CNPJ completo                                                        │
│    • Endereço                                                             │
│    • Telefone(s), e-mail                                                  │
│    • CNAE principal e secundários                                         │
│    • Distância da busca                                                   │
│    • Coordenadas para exibir no mapa                                      │
└────────────────────────────────────────────────────────────────────────────┘
```

### Passo crítico: k-NN semântico

| Busca | Match BM25 ("pré-moldados") | k-NN Semântico ("pré-moldados") |
|-------|:---------------------------:|:-------------------------------:|
| Resultado | ⚠️ Match parcial ("pré-moldadas" existe no CNAE 2330-3/01) | ✅ Encontra "pré-moldadas", "concreto", "cimento", "artefatos" |

O k-NN amplia o resultado além do match literal, capturando empresas que fabricam artefatos de concreto e cimento mas não têm "pré-moldado" no CNAE principal.

### Enriquecimento com Azure Maps (opcional)

Após o resultado, o agente poderia usar `calcular_rota` para calcular a distância rodoviária de cada empresa até um ponto de referência (ex: obra da gerente), agregando valor logístico à resposta.

---

## Cenário 2: "Quais empresas podem me fornecer brita na região de Governador Valadares?"

### Veredicto: ✅ Resolve com MCP Jazidas + MCP Empresas (complementares)

### Análise

"Brita" é um material mineral que pode ser encontrado por **duas vias**:
1. **Processos minerários** (quem extrai pedra/brita) — domínio ANM (Jazidas)
2. **Empresas** (quem comercializa/britamento) — domínio CNAE (Empresas)

A palavra "fornecer" sugere que a gerente quer quem pode **vender** brita — o ideal é cruzar ambas as fontes.

### Fluxo do Agente

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuária: "Quais empresas podem me fornecer brita na região               │
│           de Governador Valadares?"                                       │
│                                                                           │
│  CICLO 1 — Resolver localização                                          │
│  MCP Geo → buscar_municipio(nome="Governador Valadares", uf="MG")       │
│  Retorna: { lat: -18.8509, lon: -41.9490 }                              │
│                                                                           │
│  CICLO 2 — ROTA A: Processos ANM de brita (quem extrai)                 │
│  MCP Jazidas → buscar_fornecedores(                                      │
│      substancia="brita",                                                  │
│      latitude=-18.8509,                                                   │
│      longitude=-41.9490,                                                  │
│      raio_km=100,                                                         │
│      incluir_contatos=true,                                               │
│  )                                                                        │
│  Internamente:                                                            │
│    SubstanciaResolver.resolver("brita")                                   │
│      → k-NN em anm_substancia_v001 (862 substâncias)                    │
│      → Matches: GRANITO, GNAISSE, BASALTO, DIABÁSIO, BRITA              │
│    anm_v002: geo_distance 100km + terms idSubstancias                    │
│    cnpj_v001: enriquece titulares com dados da empresa                   │
│                                                                           │
│  CICLO 3 — ROTA B: Empresas com CNAE de britamento (quem comercializa)  │
│  MCP Empresas → buscar_empresas(                                         │
│      termo_busca="brita britamento",                                     │
│      latitude=-18.8509,                                                   │
│      longitude=-41.9490,                                                  │
│      raio_km=50,                                                          │
│      incluir_contatos=true,                                               │
│  )                                                                        │
│  Internamente:                                                            │
│    CnaeResolver.resolver("brita britamento")                              │
│      → k-NN: 2391-5/01 — Britamento de pedras                           │
│      →       0810-0/02 — Extração de granito e beneficiamento            │
│      →       0810-0/99 — Extração de outros minerais não-metálicos       │
│    cnpj_v002: terms CNAE + geo_distance                                  │
│                                                                           │
│  CICLO 4 — O agente consolida ambas as fontes                            │
│  Cruza resultados por CNPJ (cnpj_basico) para eliminar duplicatas,       │
│  identifica empresas que aparecem em ambos os domínios                    │
│  (titular ANM + empresa ativa = fornecedor completo), ordena por          │
│  distância e apresenta com contatos.                                      │
└────────────────────────────────────────────────────────────────────────────┘
```

### Por que as duas rotas são complementares

| Dimensão | Rota A (Jazidas) | Rota B (Empresas) |
|----------|:----------------:|:-----------------:|
| **O que encontra** | Processos ANM de brita **com localização da jazida** | Empresas de britamento **com sede/filial** |
| **Dados únicos** | Fase, área (ha), polígono da concessão, substâncias | Capital social, sócios, filiais, CNAE secundários |
| **Geo** | Localização da **jazida** (onde a pedra é extraída) | Localização da **empresa** (onde a sede fica) |
| **Contato** | Via CNPJ do titular (cross-index) | Direto do cnpj_v002 |

### Enriquecimento com Azure Maps

```
CICLO 5 (opcional) — Rotas rodoviárias
Para cada fornecedor encontrado, o agente pode chamar:
  MCP Geo → calcular_rota(origem=obra, destino=fornecedor, modo="truck")
Resultado: distância real por caminhão e tempo estimado de frete
```

---

## Cenário 3: "Quais pedreiras existem na cidade de Belo Horizonte?"

### Veredicto: ✅ Resolve com MCP Jazidas + MCP Empresas (complementares)

### Análise

Cenário muito similar ao cenário 2 do estudo anterior (pedreiras em Betim). "Pedreira" é um termo que abrange tanto processos minerários (ANM) quanto empresas (CNAE). A diferença aqui é que BH é uma capital com alta densidade urbana — pode haver restrições de lavra dentro do perímetro municipal.

### Fluxo do Agente

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuária: "Quais pedreiras existem na cidade de Belo Horizonte?"          │
│                                                                           │
│  CICLO 1 — Resolver localização + polígono                               │
│  MCP Geo → buscar_municipio(nome="Belo Horizonte", uf="MG")             │
│  Retorna: { lat: -19.9167, lon: -43.9345, id_ibge: "3106200" }          │
│                                                                           │
│  CICLO 2 — Jazidas de pedra dentro do município                          │
│  O agente tem duas opções:                                                │
│                                                                           │
│  Opção A (por raio — simples):                                            │
│  MCP Jazidas → buscar_fornecedores(                                      │
│      substancia="pedra pedreira granito",                                │
│      latitude=-19.9167, longitude=-43.9345,                              │
│      raio_km=20,     # BH tem ~330 km², raio ~10km cobre o município     │
│      incluir_contatos=true,                                               │
│  )                                                                        │
│                                                                           │
│  Opção B (por polígono — precisa):                                        │
│  MCP Geo → obter_poligono(nome="Belo Horizonte", uf="MG")               │
│  MCP Jazidas → jazidas_por_poligono(                                     │
│      geometry=<polígono de BH>,                                           │
│      substancia="pedra pedreira granito",                                │
│  )                                                                        │
│  → Retorna APENAS jazidas cujas concessões intersectam BH                │
│                                                                           │
│  CICLO 3 — Empresas de pedreira em BH                                    │
│  MCP Empresas → buscar_empresas(                                         │
│      termo_busca="pedreira",                                             │
│      latitude=-19.9167, longitude=-43.9345,                              │
│      raio_km=20,                                                          │
│  )                                                                        │
│    CnaeResolver("pedreira")                                               │
│      → 0810-0/02 — Extração de granito e beneficiamento                  │
│      → 2391-5/01 — Britamento de pedras                                  │
│      → 2391-5/02 — Aparelhamento de pedras para construção               │
│      → 0810-0/99 — Extração de outros minerais não-metálicos             │
│                                                                           │
│  Agente consolida: jazidas + empresas, cruza por CNPJ                    │
└────────────────────────────────────────────────────────────────────────────┘
```

### Diferencial: `jazidas_por_poligono` (Opção B)

A Opção B usa o polígono real do município — isto garante que o resultado não inclui jazidas de Nova Lima ou Sabará que ficariam dentro de um raio de 20km mas fora de BH. É a opção mais precisa quando a gerente diz "na cidade de".

---

## Cenário 4: "Quais são as gráficas em Magé, RJ?"

### Veredicto: ✅ Resolve com MCP Empresas

### Análise

Cenário análogo ao cenário 1 do estudo anterior (gráficas em Contagem). "Gráfica" é atividade econômica pura (impressão/artes gráficas) — sem relação com mineração. O MCP Empresas resolve sozinho via k-NN semântico.

### Fluxo do Agente

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuária: "Quais são as gráficas em Magé, RJ?"                           │
│                                                                           │
│  CICLO 1 — Resolver localização                                          │
│  MCP Geo → buscar_municipio(nome="Magé", uf="RJ")                       │
│  Retorna: { lat: -22.6528, lon: -43.1700 }                              │
│                                                                           │
│  CICLO 2 — Buscar empresas gráficas                                      │
│  MCP Empresas → buscar_empresas(                                         │
│      termo_busca="gráfica",                                              │
│      latitude=-22.6528,                                                   │
│      longitude=-43.1700,                                                  │
│      raio_km=20,                                                          │
│      incluir_contatos=true,                                               │
│  )                                                                        │
│  Internamente:                                                            │
│    CnaeResolver.resolver("gráfica")                                      │
│      → k-NN em rfb_cnae_v001 (2.394 CNAEs)                              │
│      → Matches prováveis:                                                 │
│        • 1811-3/01 — Impressão de jornais, livros, revistas              │
│        • 1811-3/02 — Impressão de material de segurança                  │
│        • 1812-1/00 — Impressão de material para outros usos              │
│        • 1813-0/01 — Impressão de material para uso industrial           │
│        • 1813-0/99 — Impressão de material para outros usos              │
│        • 1821-1/00 — Serviços de pré-impressão                           │
│        • 1822-9/01 — Serviços de encadernação e plastificação            │
│      → cnpj_v002: terms CNAE + geo_distance(20km de Magé)               │
│        + situacaoCadastral.codigo = "02" (ativas)                        │
│                                                                           │
│  Resultado: lista de gráficas com:                                        │
│    • Razão social, nome fantasia                                          │
│    • CNPJ completo                                                        │
│    • Endereço (logradouro, bairro, município, UF, CEP)                   │
│    • Telefone(s), e-mail                                                  │
│    • CNAE principal e secundários                                         │
│    • Distância em km do centro de Magé                                   │
│    • Coordenadas para mapa                                                │
└────────────────────────────────────────────────────────────────────────────┘
```

### Passo crítico: k-NN semântico

| Busca | Match BM25 ("gráfica") | k-NN Semântico ("gráfica") |
|-------|:----------------------:|:--------------------------:|
| Resultado | ⚠️ Sem match direto — nenhum CNAE tem "gráfica" no nome | ✅ Encontra "impressão", "pré-impressão", "acabamentos gráficos" |

O BM25 falharia completamente aqui porque nenhum código CNAE contém a palavra "gráfica" — todos usam "impressão". O k-NN semântico compreende que "gráfica" ≈ "impressão/artes gráficas" e resolve corretamente.

### Particularidade geográfica: Magé

Magé é um município da Baixada Fluminense com ~230 mil hab. O raio de 20km é suficiente para cobrir o município (~388 km²) sem invadir excessivamente municípios vizinhos (Duque de Caxias, Petrópolis, Guapimirim). Se a gerente quisesse mais opções, o agente poderia ampliar o raio ou sugerir cidades próximas.

---

## Cenário 5: "Me passar contatos de empresas de areia em Taubaté/SP"

### Veredicto: ✅ Resolve com MCP Jazidas + MCP Empresas (complementares)

### Análise

"Areia" é substância mineral extraída de jazidas (leito de rio, cavas, depósitos aluvionares) — domínio forte da ANM. Ao mesmo tempo, existem empresas que **comercializam** areia sem necessariamente extrair (areais, depósitos, transportadoras de agregados). A gerente pede explicitamente **contatos**, o que exige enriquecimento com dados CNPJ. O cenário é análogo ao cenário 2 (brita em Gov. Valadares) — duas rotas complementares.

### Fluxo do Agente

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuária: "Me passar contatos de empresas de areia em Taubaté/SP"        │
│                                                                           │
│  CICLO 1 — Resolver localização                                          │
│  MCP Geo → buscar_municipio(nome="Taubaté", uf="SP")                    │
│  Retorna: { lat: -23.0205, lon: -45.5558, id_ibge: "3554102" }          │
│                                                                           │
│  CICLO 2 — ROTA A: Processos ANM de areia (quem extrai)                 │
│  MCP Jazidas → buscar_fornecedores(                                      │
│      substancia="areia",                                                  │
│      latitude=-23.0205,                                                   │
│      longitude=-45.5558,                                                  │
│      raio_km=50,                                                          │
│      incluir_contatos=true,                                               │
│  )                                                                        │
│  Internamente:                                                            │
│    SubstanciaResolver.resolver("areia")                                   │
│      → k-NN em anm_substancia_v001 (862 substâncias)                    │
│      → Matches: AREIA, AREIA INDUSTRIAL, AREIA QUARTZOSA,               │
│        AREIA PARA CONSTRUÇÃO CIVIL, CASCALHO, SAIBRO                    │
│    anm_v002: geo_distance 50km + terms idSubstancias                     │
│      + apenas processos ativos                                            │
│    cnpj_v001: enriquece titulares com CNPJ, telefone, e-mail            │
│                                                                           │
│  CICLO 3 — ROTA B: Empresas com CNAE de comércio de areia               │
│  MCP Empresas → buscar_empresas(                                         │
│      termo_busca="areia extração comércio agregados",                    │
│      latitude=-23.0205,                                                   │
│      longitude=-45.5558,                                                  │
│      raio_km=30,                                                          │
│      incluir_contatos=true,                                               │
│  )                                                                        │
│  Internamente:                                                            │
│    CnaeResolver.resolver("areia extração comércio agregados")            │
│      → k-NN:                                                              │
│        • 0810-0/06 — Extração de areia, cascalho ou pedregulho          │
│        • 4689-3/02 — Comércio atacadista de materiais de construção      │
│        • 4744-0/02 — Comércio varejista de areia, pedras e agregados    │
│        • 0810-0/99 — Extração de outros minerais não-metálicos          │
│      → cnpj_v002: terms CNAE + geo_distance(30km de Taubaté)            │
│        + situacaoCadastral.codigo = "02" (ativas)                        │
│                                                                           │
│  CICLO 4 — Consolidação                                                  │
│  O agente cruza ambas as fontes por CNPJ (cnpj_basico):                 │
│    • Empresas que aparecem em AMBOS = fornecedores completos             │
│      (extraem E comercializam)                                            │
│    • Empresas exclusivas Rota A = extratores (podem vender direto)       │
│    • Empresas exclusivas Rota B = revendedores (compram e revendem)      │
│  Ordena por distância e apresenta com contatos completos.                │
└────────────────────────────────────────────────────────────────────────────┘
```

### Particularidade regional: Vale do Paraíba

Taubaté está no Vale do Paraíba, região com intensa atividade de extração de areia ao longo do Rio Paraíba do Sul. O raio de 50km da Rota A captura não só Taubaté mas também as cidades vizinhas com forte atividade minerária: Tremembé, Pindamonhangaba, Caçapava, São José dos Campos. É uma região particularmente rica em areais.

### Dados retornados por fornecedor

| Campo | Rota A (Jazidas → CNPJ) | Rota B (Empresas) |
|-------|:-----------------------:|:-----------------:|
| **CNPJ** | ✅ Via cnpj_v001 (titular do processo) | ✅ Direto do cnpj_v002 |
| **Telefone** | ✅ incluir_contatos=true | ✅ incluir_contatos=true |
| **E-mail** | ✅ incluir_contatos=true | ✅ incluir_contatos=true |
| **Endereço** | ✅ Via cnpj_v001 | ✅ Direto do cnpj_v002 |
| **Localização da jazida** | ✅ Coordenadas do processo ANM | ❌ Só tem sede da empresa |
| **Fase do processo** | ✅ (Concessão de Lavra, Licenciamento, etc.) | ❌ N/A |
| **Substâncias autorizadas** | ✅ Lista de substâncias minerais | ❌ N/A |

---

## Cenário 6: "Quais áreas licenciadas na ANM para fornecimento de brita em Teófilo Otoni que não estão em operação hoje?"

### Veredicto: ✅ Resolve com MCP Jazidas (buscar_jazidas com filtros de fase)

### Análise

Este é o cenário **mais sofisticado** — a gerente quer uma busca negativa: áreas que **têm** licença ANM mas **não estão** produzindo. Isto exige:
1. Encontrar processos de brita na região de Teófilo Otoni
2. Filtrar por fases que indicam **licenciamento válido** mas **sem operação ativa**

O conceito de "não está em operação" mapeia para fases ANM específicas:
- **Requerimento de Lavra** — pediu mas não obteve concessão
- **Concessão de Lavra** com status inativo — obteve mas parou
- **Licenciamento** — licença obtida mas pode não estar produzindo
- **Disponibilidade** — área disponível para novo titular

O agente precisa buscar **todas** as áreas e depois filtrar/destacar as que não estão em operação efetiva.

### Fluxo do Agente

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuária: "Quais áreas licenciadas na ANM para fornecimento de brita     │
│           em Teófilo Otoni que não estão em operação hoje?"              │
│                                                                           │
│  CICLO 1 — Resolver localização                                          │
│  MCP Geo → buscar_municipio(nome="Teófilo Otoni", uf="MG")             │
│  Retorna: { lat: -17.8575, lon: -41.5056, id_ibge: "3168606" }         │
│                                                                           │
│  CICLO 2 — Buscar TODAS as áreas de brita na região                     │
│  MCP Jazidas → buscar_jazidas(                                           │
│      termo_busca="brita",                                                │
│      latitude=-17.8575,                                                   │
│      longitude=-41.5056,                                                  │
│      raio_km=80,                                                          │
│      apenas_ativos=false,   ← CRUCIAL: inclui processos inativos        │
│      por_pagina=50,                                                       │
│  )                                                                        │
│  Internamente:                                                            │
│    SubstanciaResolver.resolver("brita")                                   │
│      → k-NN: GRANITO, GNAISSE, BASALTO, DIABÁSIO, BRITA                │
│    anm_v002: geo_distance 80km + terms idSubstancias                     │
│      + SEM filtro de ativo (apenas_ativos=false)                         │
│                                                                           │
│  Resultado: lista completa de processos, incluindo:                      │
│    • Processos com fase "Concessão de Lavra" mas ativo=false            │
│    • Processos em "Disponibilidade" (sem titular operando)              │
│    • Processos em "Requerimento de Lavra" (pedido sem concessão)        │
│    • Processos em "Licenciamento" (licença mas sem produção efetiva)    │
│                                                                           │
│  CICLO 3 — Buscar por polígono (alternativa mais precisa)               │
│  MCP Jazidas → jazidas_por_poligono(                                     │
│      nome_municipio="Teófilo Otoni",                                     │
│      uf_municipio="MG",                                                  │
│      substancia="brita",                                                 │
│      apenas_ativos=false,                                                │
│  )                                                                        │
│  → Filtra APENAS processos dentro do polígono municipal                  │
│  → Mais preciso que raio: exclui processos de cidades vizinhas          │
│                                                                           │
│  CICLO 4 — Agente filtra e classifica                                    │
│  O LLM analisa os resultados e separa em categorias:                     │
│                                                                           │
│  FORA DE OPERAÇÃO (resposta principal):                                  │
│    • Processos com ativo=false (qualquer fase)                           │
│    • Processos em fase "Disponibilidade"                                 │
│    • Processos em fase "Requerimento de Lavra" (não iniciaram)          │
│                                                                           │
│  EM OPERAÇÃO (contexto complementar):                                    │
│    • Processos com ativo=true E fase="Concessão de Lavra"               │
│                                                                           │
│  CICLO 5 (opcional) — Verificar vigência das substâncias                │
│  Para processos de interesse, o agente pode chamar:                      │
│  MCP Jazidas → verificar_vigencia_substancia(                            │
│      ds_processo="XXX.YYY/AAAA"                                         │
│  )                                                                        │
│  → Confirma se a substância brita ainda está vigente no título           │
│  → Identifica motivo do encerramento (esgotamento, desistência, etc.)   │
└────────────────────────────────────────────────────────────────────────────┘
```

### Campos-chave para classificação

| Campo ANM | O que indica | Relevância |
|-----------|:------------:|:----------:|
| `ativo` | Se o processo está ativo no sistema ANM | Processos com `ativo=false` estão parados |
| `fase` | Estágio do processo minerário | "Disponibilidade" = sem titular; "Concessão de Lavra" + inativo = parou |
| `dtFimVigencia` (substância) | Se a substância expirou | NULL = vigente; data passada = encerrada |
| `idTipoUso` | Finalidade (construção civil, industrial) | Filtra brita para construção |

### Diferencial: `verificar_vigencia_substancia`

A tool `verificar_vigencia_substancia` é o trunfo deste cenário. Ela faz nested query no campo `substancias` do processo e retorna:
- Se a substância **brita/granito** ainda está vigente no título
- Data de início e fim da vigência
- Motivo do encerramento (se houver)

Isso permite distinguir entre "a área tem licença mas a empresa parou voluntariamente" vs "a licença expirou/foi cancelada".

### Valor de negócio

Este cenário tem altíssimo valor para suprimentos: identificar áreas licenciadas mas inoperantes pode revelar **oportunidades** — uma construtora poderia negociar diretamente com o titular para reativar a produção, ou solicitar à ANM a disponibilidade da área.

---

## Cenário 7: "Tem alguma pedreira licenciada na ANM em Teófilo Otoni que não está funcionando?"

### Veredicto: ✅ Resolve com MCP Jazidas (mesma arquitetura do Cenário 6, escopo diferente)

### Análise

Este cenário é uma **variação do cenário 6** — a diferença é sutil mas importante:
- **Cenário 6:** "áreas licenciadas para brita" → foco na **substância** (brita)
- **Cenário 7:** "pedreira licenciada" → foco no **tipo de empreendimento** (pedreira = extração de rochas em geral)

"Pedreira" abrange mais substâncias que "brita" — inclui granito, gnaisse, basalto, quartzito, ardósia e qualquer rocha para britamento. O fluxo é essencialmente o mesmo, com o resolver semântico capturando um espectro mais amplo.

### Fluxo do Agente

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuária: "Tem alguma pedreira licenciada na ANM em Teófilo Otoni        │
│           que não está funcionando?"                                      │
│                                                                           │
│  CICLO 1 — Resolver localização                                          │
│  MCP Geo → buscar_municipio(nome="Teófilo Otoni", uf="MG")             │
│  Retorna: { lat: -17.8575, lon: -41.5056, id_ibge: "3168606" }         │
│                                                                           │
│  CICLO 2 — Buscar processos de pedreira (espectro amplo)                │
│  MCP Jazidas → buscar_jazidas(                                           │
│      termo_busca="pedreira granito gnaisse basalto rocha",              │
│      latitude=-17.8575,                                                   │
│      longitude=-41.5056,                                                  │
│      raio_km=50,                                                          │
│      apenas_ativos=false,   ← Inclui processos inativos                 │
│      por_pagina=50,                                                       │
│  )                                                                        │
│  Internamente:                                                            │
│    SubstanciaResolver.resolver("pedreira granito gnaisse basalto rocha") │
│      → k-NN em anm_substancia_v001:                                      │
│        GRANITO, GNAISSE, BASALTO, DIABÁSIO, QUARTZITO,                  │
│        ARDÓSIA, PEDRA BRITADA, BRITA, ROCHA ORNAMENTAL                  │
│      → Espectro mais amplo que cenário 6 ("brita" apenas)               │
│    anm_v002: geo_distance 50km + terms idSubstancias                     │
│      + SEM filtro de ativo                                                │
│                                                                           │
│  CICLO 3 — Alternativa por polígono municipal                            │
│  MCP Jazidas → jazidas_por_poligono(                                     │
│      nome_municipio="Teófilo Otoni",                                     │
│      uf_municipio="MG",                                                  │
│      substancia="pedreira granito gnaisse",                              │
│      apenas_ativos=false,                                                │
│  )                                                                        │
│                                                                           │
│  CICLO 4 — Classificação pelo agente                                    │
│  O LLM separa os resultados:                                             │
│                                                                           │
│  "NÃO ESTÁ FUNCIONANDO" (resposta principal):                            │
│    • ativo=false → processo paralisado                                   │
│    • fase="Disponibilidade" → sem titular operando                      │
│    • fase="Requerimento de Pesquisa" → fase exploratória, não produz    │
│                                                                           │
│  "ESTÁ FUNCIONANDO" (contexto):                                          │
│    • ativo=true + fase="Concessão de Lavra" ou "Licenciamento"          │
│                                                                           │
│  CICLO 5 — Detalhes dos processos inativos de interesse                 │
│  Para cada processo inativo relevante:                                    │
│  MCP Jazidas → detalhes_processo(                                        │
│      ds_processo="XXX.YYY/AAAA",                                        │
│      incluir_empresa=true,                                                │
│  )                                                                        │
│  → Retorna dados do titular (CNPJ, contato, sócios)                     │
│  → Permite que a gerente entre em contato para negociar                  │
│                                                                           │
│  CICLO 6 (opcional) — Vigência                                           │
│  MCP Jazidas → verificar_vigencia_substancia(ds_processo="...")          │
│  → Confirma se o título ainda é válido ou já expirou                    │
└────────────────────────────────────────────────────────────────────────────┘
```

### Diferença entre Cenários 6 e 7: Escopo semântico

| Aspecto | Cenário 6 ("brita") | Cenário 7 ("pedreira") |
|---------|:-------------------:|:----------------------:|
| **Termo de busca** | `"brita"` | `"pedreira granito gnaisse basalto rocha"` |
| **Substâncias resolvidas** | GRANITO, GNAISSE, BASALTO, DIABÁSIO, BRITA (5) | GRANITO, GNAISSE, BASALTO, DIABÁSIO, QUARTZITO, ARDÓSIA, BRITA, ROCHA ORNAMENTAL (8+) |
| **Foco** | Material específico (brita para construção) | Tipo de empreendimento (qualquer extração de rocha) |
| **Resultado esperado** | Menos processos, mais focado | Mais processos, espectro mais amplo |

### Valor combinado dos cenários 6 + 7

Se a gerente faz ambas as perguntas, o agente pode:
1. Cenário 6: listar áreas de brita inoperantes
2. Cenário 7: ampliar para pedreiras em geral (pode encontrar uma pedreira de granito inativa que também poderia fornecer brita)
3. Cruzar: identificar pedreiras ativas que fornecem brita E pedreiras inativas com potencial

---

## Cenário 8: "Qual as maiores empresas de pré-moldados na região do Rio de Janeiro?"

### Veredicto: ✅ Resolve com MCP Empresas + MCP Geo (com limitação para "maiores")

### Análise

Este cenário combina dois desafios:
1. **Busca por atividade** — "pré-moldados" = CNAE semântico (igual ao cenário 1)
2. **Critério de tamanho** — "maiores" exige um proxy de porte empresarial

O MineralRadar possui dados cadastrais da RFB no `cnpj_v002` que incluem **porte da empresa** (ME, EPP, Demais) e **capital social**. Estes são os proxies disponíveis para "maior". O agente pode ordenar/filtrar por porte ou capital social.

"Região do Rio de Janeiro" é ambíguo — pode significar:
- Cidade do RJ apenas
- Região Metropolitana do RJ (21 municípios)
- Estado do RJ inteiro

O agente deve interpretar como **Região Metropolitana** (raio ~60-80km a partir do centro do RJ) ou usar a isócrona para área alcançável.

### Fluxo do Agente

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuária: "Qual as maiores empresas de pré-moldados na região do         │
│           Rio de Janeiro?"                                                │
│                                                                           │
│  CICLO 1 — Resolver localização (centro do RJ)                          │
│  MCP Geo → buscar_municipio(nome="Rio de Janeiro", uf="RJ")            │
│  Retorna: { lat: -22.9068, lon: -43.1729, id_ibge: "3304557" }         │
│                                                                           │
│  CICLO 2 — Buscar empresas de pré-moldados em raio amplo                │
│  MCP Empresas → buscar_empresas(                                         │
│      termo_busca="pré-moldados concreto estruturas",                     │
│      latitude=-22.9068,                                                   │
│      longitude=-43.1729,                                                  │
│      raio_km=80,        ← Raio amplo para cobrir Reg. Metropolitana     │
│      incluir_contatos=true,                                               │
│      por_pagina=50,     ← Traz mais resultados para ranquear            │
│  )                                                                        │
│  Internamente:                                                            │
│    CnaeResolver.resolver("pré-moldados concreto estruturas")             │
│      → k-NN em rfb_cnae_v001:                                            │
│        • 2330-3/01 — Fabricação de estruturas pré-moldadas de concreto   │
│        • 2330-3/02 — Fabricação de artefatos de cimento p/ construção    │
│        • 2330-3/99 — Fabricação de outros artefatos de concreto/cimento  │
│        • 2340-4/01 — Fabricação de pré-fabricados de concreto armado     │
│      → cnpj_v002: terms CNAE + geo_distance(80km)                       │
│        + situacaoCadastral.codigo = "02" (ativas)                        │
│                                                                           │
│  CICLO 3 — Agente ranqueia por "porte/tamanho"                          │
│  O LLM ordena os resultados usando:                                      │
│    1. Porte da empresa (Demais > EPP > ME)                               │
│    2. Capital social (maior → menor)                                     │
│    3. Número de filiais (mais filiais → mais presença)                   │
│  Apresenta os top N como "maiores".                                      │
│                                                                           │
│  CICLO 4 (opcional) — Detalhes das maiores                              │
│  Para o top 3-5, o agente pode enriquecer:                               │
│  MCP Empresas → detalhes_empresa(                                        │
│      cnpj_basico="XXXXXXXX",                                            │
│      incluir_socios=true,                                                 │
│      incluir_cnaes_detalhados=true,                                      │
│  )                                                                        │
│  → Ficha completa: sócios, capital social, data fundação,               │
│    todos os CNAEs, endereço completo                                     │
│                                                                           │
│  CICLO 5 (opcional) — Isócrona logística                                │
│  MCP Geo → calcular_isocrona(                                            │
│      latitude=-22.9068,                                                   │
│      longitude=-43.1729,                                                  │
│      criterio="tempo", valor=120, modo="truck"                           │
│  )                                                                        │
│  → Polígono de área alcançável em 2h de caminhão                        │
│  → O agente pode filtrar apenas empresas dentro desta isócrona          │
└────────────────────────────────────────────────────────────────────────────┘
```

### Limitação e transparência: conceito de "maior"

| Critério | Disponível? | Precisão |
|----------|:-----------:|:--------:|
| **Porte RFB** (ME/EPP/Demais) | ✅ No cnpj_v002 | Média — "Demais" inclui de médias a gigantes |
| **Capital social** | ✅ No cnpj_v002 | Média — nem sempre reflete porte real |
| **Faturamento** | ❌ Não disponível | N/A — dado privado |
| **Nº de funcionários** | ❌ Não disponível | N/A — dado RAIS não indexado |
| **Nº de filiais** | ✅ Inferível (múltiplos registros mesmo cnpj_basico) | Baixa — proxy indireto |

O agente deve ser **transparente** ao explicar que "maiores" é inferido por porte cadastral e capital social, não por faturamento ou produção — e que o ranking é uma aproximação.

### Enriquecimento com `detalhes_empresa`

A tool `detalhes_empresa` cruza 3 índices (cnpj_v002 + rfb_cnae_v001 + anm_v002) e retorna a ficha completa, incluindo:
- Dados do sócio administrador (para contato direto)
- Hierarquia CNAE completa (seção → divisão → grupo → classe → subclasse)
- Processos ANM vinculados ao CNPJ (se houver — identifica se a empresa também extrai matéria-prima)

---

## Cenário 9: "Quais empresas prestam o serviço de pavimentação próximo ao município de Itaguaí?"

### Veredicto: ✅ Resolve com MCP Empresas + MCP Geo

### Análise

"Pavimentação" é um serviço de construção civil — domínio 100% CNAE/CNPJ, sem relação direta com mineração (embora pavimentação use brita/areia como insumo). O k-NN semântico é fundamental aqui porque "pavimentação" pode mapear para vários CNAEs:
- Construção de rodovias/ferrovias
- Obras de urbanização
- Terraplenagem
- Obras de infraestrutura

A expressão "próximo ao município" sugere que a gerente aceita empresas de cidades vizinhas — o raio deve ser moderado (30-50km).

### Fluxo do Agente

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Usuária: "Quais empresas prestam o serviço de pavimentação              │
│           próximo ao município de Itaguaí?"                              │
│                                                                           │
│  CICLO 1 — Resolver localização                                          │
│  MCP Geo → buscar_municipio(nome="Itaguaí", uf="RJ")                   │
│  Retorna: { lat: -22.8725, lon: -43.7628, id_ibge: "3302007" }         │
│                                                                           │
│  CICLO 2 — Buscar empresas de pavimentação                              │
│  MCP Empresas → buscar_empresas(                                         │
│      termo_busca="pavimentação",                                         │
│      latitude=-22.8725,                                                   │
│      longitude=-43.7628,                                                  │
│      raio_km=50,        ← "próximo" = raio moderado                     │
│      incluir_contatos=true,                                               │
│  )                                                                        │
│  Internamente:                                                            │
│    CnaeResolver.resolver("pavimentação")                                 │
│      → k-NN em rfb_cnae_v001 (2.394 CNAEs)                              │
│      → Matches prováveis:                                                 │
│        • 4211-1/01 — Construção de rodovias e ferrovias                  │
│        • 4211-1/02 — Pintura para sinalização em pistas rodoviárias     │
│        • 4213-8/00 — Obras de urbanização — ruas, praças e calçadas     │
│        • 4222-7/01 — Construção de redes de abastecimento de água/      │
│                       esgoto (inclui pavimentação associada)             │
│        • 4291-0/00 — Obras portuárias, marítimas e fluviais             │
│        • 4299-5/01 — Construção de instalações esportivas e recreativas │
│        • 4312-6/00 — Perfurações e sondagens (terraplenagem)            │
│        • 4313-4/00 — Obras de terraplenagem                              │
│      → cnpj_v002: terms CNAE + geo_distance(50km de Itaguaí)            │
│        + situacaoCadastral.codigo = "02" (ativas)                        │
│                                                                           │
│  Resultado: lista de empresas com:                                        │
│    • Razão social, nome fantasia                                          │
│    • CNPJ completo                                                        │
│    • Endereço                                                             │
│    • Telefone(s), e-mail                                                  │
│    • CNAE principal e secundários                                         │
│    • Distância em km do centro de Itaguaí                                │
│    • Coordenadas para mapa                                                │
└────────────────────────────────────────────────────────────────────────────┘
```

### Passo crítico: k-NN semântico

| Busca | Match BM25 ("pavimentação") | k-NN Semântico ("pavimentação") |
|-------|:---------------------------:|:-------------------------------:|
| Resultado | ⚠️ Match muito limitado — poucos CNAEs contêm "pavimentação" literal | ✅ Encontra "construção de rodovias", "urbanização", "terraplenagem", "infraestrutura viária" |

O k-NN resolve a distância semântica entre "pavimentação" e os CNAEs que descrevem a atividade com palavras diferentes. Sem o k-NN, a busca perderia a maioria das construtoras relevantes.

### Particularidade geográfica: Itaguaí

Itaguaí está na Região Metropolitana do RJ, na Costa Verde — próximo ao Porto de Itaguaí (antigo Sepetiba), uma região com alta demanda por obras de infraestrutura e pavimentação. O raio de 50km captura:
- O próprio Itaguaí
- Seropédica, Mangaratiba (vizinhos diretos)
- Nova Iguaçu, Queimados (Baixada Fluminense)
- Parte da zona oeste do Rio de Janeiro (Campo Grande, Santa Cruz)

### Enriquecimento com Azure Maps (opcional)

```
CICLO 3 (opcional) — Rotas e isócronas
O agente pode agregar valor logístico de duas formas:

Opção A — Rotas individuais:
  Para cada empresa encontrada:
  MCP Geo → calcular_rota(
      origem_lat=-22.8725, origem_lon=-43.7628,   # Itaguaí
      destino_lat=<empresa.lat>, destino_lon=<empresa.lon>,
      modo="truck"
  )
  → Distância rodoviária real + tempo estimado

Opção B — Isócrona:
  MCP Geo → calcular_isocrona(
      latitude=-22.8725, longitude=-43.7628,
      criterio="tempo", valor=90, modo="truck"   # 1h30 de caminhão
  )
  → Polígono de área alcançável → filtrar empresas dentro dele
```

### Valor para a gerente

A resposta final apresentaria: empresas de pavimentação ordenadas por distância de Itaguaí, com contatos diretos (telefone/e-mail), mapa de localização, e opcionalmente a distância rodoviária real via Azure Maps — tudo que a gerente precisa para iniciar cotações imediatas.

---

## Resumo: Cobertura Completa das 9 Perguntas

| # | Pergunta | Resolve? | Tools utilizadas | Ciclos |
|---|----------|:--------:|------------------|:------:|
| 1 | Pré-moldados em Montes Claros | ✅ | `buscar_municipio` → `buscar_empresas` | 2 |
| 2 | Brita em Gov. Valadares | ✅ | `buscar_municipio` → `buscar_fornecedores` → `buscar_empresas` | 4 |
| 3 | Pedreiras em BH | ✅ | `buscar_municipio` → `buscar_fornecedores` / `jazidas_por_poligono` → `buscar_empresas` | 3 |
| 4 | Gráficas em Magé | ✅ | `buscar_municipio` → `buscar_empresas` | 2 |
| 5 | Areia em Taubaté | ✅ | `buscar_municipio` → `buscar_fornecedores` → `buscar_empresas` | 4 |
| 6 | Áreas de brita inoperantes (ANM) em T. Otoni | ✅ | `buscar_municipio` → `buscar_jazidas(apenas_ativos=false)` → `verificar_vigencia` | 5 |
| 7 | Pedreira inativa (ANM) em T. Otoni | ✅ | `buscar_municipio` → `buscar_jazidas(apenas_ativos=false)` → `detalhes_processo` → `verificar_vigencia` | 6 |
| 8 | Maiores pré-moldados na região do RJ | ✅* | `buscar_municipio` → `buscar_empresas` → `detalhes_empresa` | 4 |
| 9 | Pavimentação próximo a Itaguaí | ✅ | `buscar_municipio` → `buscar_empresas` | 2 |

> \* Cenário 8 tem **limitação**: "maiores" é inferido por porte cadastral e capital social — não por faturamento real.

### Destaques da Arquitetura

1. **k-NN Semântico é obrigatório em 100% dos cenários** — sem ele, termos como "gráfica", "pavimentação", "pré-moldados" não encontrariam os CNAEs corretos.

2. **Cenários 6 e 7 demonstram o poder do MCP Jazidas** — busca negativa (o que **não** está funcionando) é um diferencial do MineralRadar que nenhuma busca Google resolve.

3. **`jazidas_por_poligono` agrega precisão** — sempre que a gerente diz "na cidade de X", o polígono municipal é mais preciso que um raio circular.

4. **`verificar_vigencia_substancia` resolve dúvidas de compliance** — confirma se um título minerário ainda é válido antes de a gerente investir tempo em negociação.

5. **Cross-index é transparente** — o agente cruza até 3 índices (anm_v002 → cnpj_v001 → cnpj_v002) sem que a gerente precise saber a complexidade por trás.

6. **Azure Maps adiciona valor logístico** — rotas de caminhão e isócronas transformam dados cadastrais em decisões de compra (distância real, tempo de entrega).
