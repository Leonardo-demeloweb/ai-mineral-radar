# 🏔️ MCP Server Jazidas — Planejamento Detalhado

## Visão Geral

| Item | Valor |
|------|-------|
| **Porta** | `8010` |
| **Índice principal** | `anm_v002` (956.288 docs, 14.23 GB) |
| **Índices auxiliares** | `anm_substancia_v001` (862 docs), `anm_tipo_uso_substancia_v001` (~50 docs) |
| **Índice cross-reference** | `cnpj_v001` (221M docs) — via `cnpjBasico` |
| **Total de tools custom** | **5** (reduzido de 10 após reavaliação com QPT) |
| **Classificação** | 3 compostas (cross-index) + 2 especializadas (nested/geo_shape) |
| **Cenários cobertos por QPT** | ~50% (queries ad-hoc, aggregations, listagens simples) |

> **Atualização (maio/2026):** o servidor Jazidas em código também inclui ferramentas sobre dados CPRM em OpenSearch, por exemplo **`ocorrencias_minerais_proximas`** (`mr_cprm_v001`) e **`geoquimica_proxima`** (**`mr_geoquimica_v001`** — amostras geoquímicas com nested `analises`). O índice principal de processos na stack MineralRadar vigente é tipicamente **`mr_jazidas_v001`** (não apenas `anm_v002`).

---

## 1. Racional: Por que 5 tools e não 10?

Com o `anm_v002` **flat** e o **QPT (QueryPlanningTool)** ativo, 5 das 10 tools originais são dispensáveis — o QPT gera DSL válido para elas nativamente.

### 1.1 Análise de cada tool original

| # | Tool Original | QPT resolve? | Veredicto |
|---|---------------|:------------:|-----------|
| 1 | `buscar_fornecedores` | ❌ Cruza 3 índices | 🔴 **MANTER** — razão de existir do MCP |
| 2 | `buscar_jazidas` | ⚠️ Match direto sim, semântico (k-NN) não | 🟡 **MANTER** — k-NN agrega valor real |
| 3 | ~~`buscar_por_tipo_uso`~~ | ✅ `match dsTipoUsoSubstancias "brita"` | 🟢 **REMOVIDA** — QPT cobre |
| 4 | `detalhes_processo` | ⚠️ Busca sim, enriquecer com CNPJ não | 🔴 **MANTER** — cross-index |
| 5 | `jazidas_por_poligono` | ❌ Nested `geo_shape` | 🔴 **MANTER** — QPT não gera nested |
| 6 | ~~`estatisticas_regiao`~~ | ✅ PPLTool faz aggregations | 🟢 **REMOVIDA** — PPLTool cobre |
| 7 | `verificar_vigencia` | ❌ Nested `substancias` | 🔴 **MANTER** — QPT não gera nested |
| 8 | ~~`buscar_processos_titular`~~ | ✅ `match nmTitulares` / `term cnpjTitulares` | 🟢 **REMOVIDA** — QPT cobre |
| 9 | ~~`listar_substancias`~~ | ✅ `match nmSubstancia` no catálogo | 🟢 **REMOVIDA** — QPT cobre |
| 10 | ~~`listar_tipos_uso`~~ | ✅ `match dsTipoUsoSubstancia` no catálogo | 🟢 **REMOVIDA** — QPT cobre |

### 1.2 O que o QPT cobre nativamente (sem código custom)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CENÁRIOS COBERTOS POR QPT + SearchIndexTool (zero código custom)           │
│                                                                             │
│  Pergunta do usuário                    │  QPT gera DSL                     │
│  ───────────────────────────────────────┼─────────────────────────────────  │
│  "Processos de areia em SP"             │  match nmSubstancias + term UF    │
│  "Processos da Cia Melhoramentos"       │  match nmTitulares                │
│  "Processos com CNPJ 60730348"          │  term cnpjTitulares               │
│  "Processos de brita perto de Itaboraí" │  match dsTipoUsoSubstancias       │
│                                         │  + geo_distance localizacao       │
│  "Quantos processos por fase em MG?"    │  PPLTool: aggregation by fase     │
│  "Distribuição de substâncias por UF"   │  PPLTool: aggregation by UF       │
│  "Quais substâncias têm nome 'granito'?"│  match nmSubstancia no catálogo   │
│  "Processos ativos protocolo > 2020"    │  term btAtivo + range dtProtocolo │
│                                         │                                   │
│  Estimativa: ~50% dos cenários do dia-a-dia                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Ganho:** De 10 tools custom em ~6 dias → **5 tools custom em ~4.5 dias**. O QPT absorveu 50% do trabalho custom — esse é o retorno real da reestruturação do `anm_v002`.

---

## 2. Inventário das 5 Custom Tools

### 2.1 Tools Compostas (Cross-Index)

#### 🔴 Tool 1: `buscar_fornecedores`

> **A tool mais importante** — atende a pergunta principal do produto.

| Campo | Valor |
|-------|-------|
| **Descrição** | Busca fornecedores de minério por substância + localização, retornando dados de contato e sócios |
| **Fluxo** | 3 passos cross-index (substância → processos ANM → empresa CNPJ) |
| **QPT nativo?** | ❌ Impossível — cruza 3 índices |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `substancia` | `string` | ✅ | Termo de busca (ex: "areia lavada", "brita") |
| `latitude` | `float` | ✅ | Latitude do ponto central |
| `longitude` | `float` | ✅ | Longitude do ponto central |
| `raio_km` | `float` | ✅ | Raio de busca em km (default: 50) |
| `uf` | `string` | ❌ | Filtro por UF (ex: "SP") |
| `fase` | `string` | ❌ | Fase do processo (ex: "Concessão de Lavra") |
| `apenas_ativos` | `bool` | ❌ | Apenas processos ativos (default: true) |
| `incluir_contatos` | `bool` | ❌ | Incluir telefone/email da empresa (default: true) |
| `incluir_socios` | `bool` | ❌ | Incluir nomes dos sócios (default: false) |
| `pagina` | `int` | ❌ | Página (default: 1) |
| `por_pagina` | `int` | ❌ | Resultados por página (default: 10, max: 50) |

**Fluxo interno (3 passos):**

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PASSO 1: Resolver substância (semântico)                                │
│  Índice: anm_substancia_v001 (k-NN ou match)                            │
│  Input: "areia lavada"                                                   │
│  Output: [200207, 200200, 200201, 200202]                                │
│  Tempo: ~5ms                                                             │
├──────────────────────────────────────────────────────────────────────────┤
│  PASSO 1b: Resolver tipo de uso (se necessário)                          │
│  Índice: anm_tipo_uso_substancia_v001 (k-NN ou match)                   │
│  Input: "brita" → idTipoUsoSubstancia = [3]                             │
│  Tempo: ~5ms (condicional — só se substância não encontrada no passo 1)  │
├──────────────────────────────────────────────────────────────────────────┤
│  PASSO 2: Buscar processos ANM                                           │
│  Índice: anm_v002 (campos flat: idSubstancias, localizacao, siglasUF)   │
│  Filtros: terms + geo_distance + btAtivo                                 │
│  Output: processos com dsProcesso, fase, cnpjTitulares, localizacao     │
│  Tempo: ~30ms                                                            │
├──────────────────────────────────────────────────────────────────────────┤
│  PASSO 3: Enriquecer com dados empresa (cross-index)                     │
│  Índice: cnpj_v001 (terms por cnpjBasico)                                │
│  Output: razaoSocial, telefone, email, endereço, sócios                  │
│  Tempo: ~25ms                                                            │
├──────────────────────────────────────────────────────────────────────────┤
│  MERGE: Combina processos + empresas                                     │
│  Salva resultado completo no Redis (TTL 1h)                              │
│  Retorna página solicitada ao LLM                                        │
│  Envia todos os pontos geo ao frontend (mapa)                            │
└──────────────────────────────────────────────────────────────────────────┘

Tempo total: ~60ms | Ciclos ReAct: 2 (com tool composta)
```

**Resposta:**

```json
{
  "sucesso": true,
  "meta": {
    "total": 76,
    "pagina": 1,
    "por_pagina": 10,
    "total_paginas": 8
  },
  "dados": [
    {
      "processo": "820.603/2018",
      "fase": "Autorização de Pesquisa",
      "area_ha": 552.75,
      "substancias": ["AREIA"],
      "municipio": "Caieiras/SP",
      "localizacao": {"lat": -23.37, "lon": -46.76},
      "distancia_km": 18.2,
      "titular": {
        "nome": "COMPANHIA MELHORAMENTOS DE SAO PAULO",
        "cnpj_basico": "60730348"
      },
      "contato": {
        "telefone": "(11) 38740400",
        "email": "FISCAL@MELHORAMENTOS.COM.BR",
        "endereco": "RODOVIA PRES TANCREDO A NEVES, SN - CEP 07700001"
      },
      "socios": [
        "ANTONIO JOAQUIM DE OLIVEIRA",
        "CAROLINA ALVIM GUEDES ALCOFORADO"
      ]
    }
  ],
  "cache_id": "search:abc123",
  "mapa_pontos": [
    {"lat": -23.37, "lon": -46.76, "processo": "820.603/2018"},
    {"lat": -23.36, "lon": -46.82, "processo": "820.350/2014"}
  ]
}
```

---

#### 🟡 Tool 2: `buscar_jazidas`

| Campo | Valor |
|-------|-------|
| **Descrição** | Busca processos minerários por substância + filtros + geo (sem enriquecer com CNPJ) |
| **Fluxo** | 2 passos (substância semântica → anm_v002) |
| **QPT nativo?** | ⚠️ Parcial — passo semântico (k-NN) não é QPT; passo 2 flat SERIA QPT |
| **Valor do custom** | Transforma termos vagos ("material para pavimentação") em IDs precisos via k-NN |

**Por que manter se QPT faz match direto?**

| Cenário | QPT (match direto) | Custom (k-NN semântico) |
|---------|:------------------:|:----------------------:|
| "areia lavada" | ✅ Encontra | ✅ Encontra |
| "material para pavimentação" | ❌ 0 resultados | ✅ Resolve → brita, cascalho |
| "minério para construção civil" | ❌ 0 resultados | ✅ Resolve → areia, brita, argila |
| "pedra ornamental" | ⚠️ Parcial | ✅ Resolve → granito, mármore, gnaisse |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `termo_busca` | `string` | ✅ | Substância ou tipo de uso (ex: "areia", "brita") |
| `latitude` | `float` | ❌ | Latitude do centro da busca |
| `longitude` | `float` | ❌ | Longitude do centro da busca |
| `raio_km` | `float` | ❌ | Raio em km (default: 50) |
| `uf` | `string` | ❌ | Filtrar por UF |
| `municipio` | `string` | ❌ | Filtrar por nome do município |
| `fase` | `string` | ❌ | Fase do processo |
| `apenas_ativos` | `bool` | ❌ | Default: true |
| `pagina` | `int` | ❌ | Default: 1 |
| `por_pagina` | `int` | ❌ | Default: 10 |

**Fluxo interno:**

```
Passo 1: anm_substancia_v001 (k-NN semântico) → IDs
         OU anm_tipo_uso_substancia_v001 se for tipo de uso
Passo 2: anm_v002 (flat query: terms idSubstancias + geo_distance + filtros)
```

**Índices usados:** `anm_substancia_v001`, `anm_tipo_uso_substancia_v001`, `anm_v002`

---

#### 🔴 Tool 3: `detalhes_processo`

| Campo | Valor |
|-------|-------|
| **Descrição** | Obtém dados completos de um processo pelo dsProcesso, incluindo dados da empresa titular |
| **Fluxo** | 2 passos (anm_v002 → cnpj_v001 para enriquecer) |
| **QPT nativo?** | ❌ Cross-index + formatação de negócio |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `ds_processo` | `string` | ✅ | Código do processo (ex: "832.145/2018") |
| `incluir_empresa` | `bool` | ❌ | Incluir dados CNPJ do titular (default: true) |
| `incluir_eventos` | `bool` | ❌ | Incluir histórico de eventos (default: false) |
| `incluir_titulos` | `bool` | ❌ | Incluir títulos/documentos (default: false) |

**Resposta:**

```json
{
  "sucesso": true,
  "dados": {
    "dsProcesso": "832.145/2018",
    "nrNUP": "48403.832145/2018-12",
    "ativo": true,
    "fase": "Concessão de Lavra",
    "area_ha": 120.50,
    "localizacao": {"lat": -23.39, "lon": -46.60},
    "substancias": [
      {"nome": "AREIA", "tipo_uso": "BRITA", "vigente": true}
    ],
    "municipios": ["Guarulhos/SP"],
    "titular": {
      "nome": "EMPRESA X LTDA",
      "cnpj_basico": "12345678",
      "telefone": "(11) 99999-0000",
      "email": "contato@empresax.com.br",
      "socios": ["JOAO SILVA", "MARIA SANTOS"]
    },
    "pessoas": [
      {"nome": "EMPRESA X LTDA", "relacao": "Titular", "vigente": true},
      {"nome": "ENG. CARLOS SOUZA", "relacao": "Resp. Técnico", "vigente": true}
    ],
    "eventos": [],
    "titulos": []
  }
}
```

---

### 2.2 Tools Especializadas (Queries que QPT não gera)

#### 🔴 Tool 4: `jazidas_por_poligono`

| Campo | Valor |
|-------|-------|
| **Descrição** | Busca processos cujos shapes intersectam um polígono GeoJSON |
| **Fluxo** | 1 passo — nested query geo_shape no anm_v002 |
| **QPT nativo?** | ❌ Nested geo_shape query — QPT não gera nested |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `geometry` | `GeoJSON` | ✅ | Polígono no formato GeoJSON |
| `substancia` | `string` | ❌ | Filtrar por substância |
| `fase` | `string` | ❌ | Filtrar por fase |
| `apenas_ativos` | `bool` | ❌ | Default: true |
| `pagina` | `int` | ❌ | Default: 1 |
| `por_pagina` | `int` | ❌ | Default: 10 |

**Query interna:**

```json
{
  "query": {
    "bool": {
      "must": [
        {
          "nested": {
            "path": "shapes",
            "query": {
              "geo_shape": {
                "shapes.poligono": {
                  "shape": { "type": "Polygon", "coordinates": ["..."] },
                  "relation": "intersects"
                }
              }
            }
          }
        }
      ],
      "filter": [
        { "term": { "btAtivo": "s" } }
      ]
    }
  }
}
```

**Caso de uso**: "Quais processos minerários existem dentro dos limites do município de Guarulhos?"

---

#### 🔴 Tool 5: `verificar_vigencia_substancia`

| Campo | Valor |
|-------|-------|
| **Descrição** | Verifica se uma substância ainda está ativa em um processo (dtFimVigencia IS NULL) |
| **Fluxo** | 1 passo — nested query no campo substancias do anm_v002 |
| **QPT nativo?** | ❌ Nested query em substancias — QPT não gera nested |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `ds_processo` | `string` | ✅ | Código do processo |
| `id_substancia` | `int` | ❌ | ID da substância (se omitido, retorna todas) |

**Caso de uso**: "O processo 832.145/2018 ainda está extraindo areia ou já esgotou?"

---

## 3. Mapa de Cobertura: QPT Nativo vs Custom Tools

```
┌───────────────────────────────────────────────────────────────────────────┐
│                  MAPA DE COBERTURA QPT vs CUSTOM                          │
│                                                                           │
│  ┌───────────────────────────┐ ┌───────────────────────────────────────┐ │
│  │      QPT NATIVO ✅          │ │       CUSTOM TOOLS 🔧 (5)            │ │
│  │  (SearchIndexTool,          │ │                                       │ │
│  │   QueryPlanningTool,        │ │                                       │ │
│  │   PPLTool)                  │ │                                       │ │
│  │                             │ │                                       │ │
│  │  • Processos por UF         │ │  • buscar_fornecedores               │ │
│  │  • Processos por fase       │ │    (3-index cross: substância →      │ │
│  │  • Processos por substância │ │     processos → empresa/contatos)    │ │
│  │    (match direto por nome)  │ │                                       │ │
│  │  • Processos por titular    │ │  • buscar_jazidas                    │ │
│  │    (nome ou CNPJ)           │ │    (resolução semântica k-NN →       │ │
│  │  • Processos por raio       │ │     processos geo)                   │ │
│  │    (geo_distance)           │ │                                       │ │
│  │  • Processos por tipo uso   │ │  • detalhes_processo                 │ │
│  │    (match direto)           │ │    (cross-index → cnpj_v001)         │ │
│  │  • Contagem/aggregations    │ │                                       │ │
│  │  • Range por data/área      │ │  • jazidas_por_poligono              │ │
│  │  • Filtros combinados       │ │    (nested geo_shape)                │ │
│  │  • Listagem de catálogos    │ │                                       │ │
│  │    (substâncias, tipos uso) │ │  • verificar_vigencia_substancia     │ │
│  │  • Estatísticas por região  │ │    (nested substancias)              │ │
│  │                             │ │                                       │ │
│  │  ~50% dos cenários          │ │  ~50% dos cenários                   │ │
│  │  (ad-hoc, exploração,       │ │  (core negócio, cross-index,         │ │
│  │   debug, aggregations)      │ │   semântico, nested, cache)          │ │
│  └───────────────────────────┘ └───────────────────────────────────────┘ │
│                                                                           │
│  PRINCÍPIO: QPT para queries flat ad-hoc ─── Custom para orquestração    │
└───────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Quando o agente usa QPT vs Custom?

```
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│  Pergunta envolve...              → Caminho                    │
│  ─────────────────────────────────────────────────────         │
│                                                                │
│  CUSTOM TOOLS:                                                 │
│  Substância + contatos/sócios     → buscar_fornecedores        │
│  Substância (termo vago/indireto) → buscar_jazidas (k-NN)      │
│  Processo + dados da empresa      → detalhes_processo          │
│  Polígono/área geográfica         → jazidas_por_poligono       │
│  Vigência de substância           → verificar_vigencia         │
│                                                                │
│  QPT NATIVO:                                                   │
│  "Processos de areia em SP"       → QPT: match + term          │
│  "Processos do CNPJ 60730348"     → QPT: term cnpjTitulares    │
│  "Processos da Cia Melhoramentos" → QPT: match nmTitulares     │
│  "Processos de brita em Guarulhos"→ QPT: match + match         │
│  "Quantos processos por fase?"    → PPLTool: aggregation       │
│  "Distribuição por UF?"           → PPLTool: aggregation       │
│  "Processos ativos desde 2020"    → QPT: term + range          │
│  "Mapping do anm_v002"            → IndexMappingTool           │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 4. Índices Utilizados e Campos Relevantes

### 4.1 `anm_v002` — Índice Principal

**Campos flat (QPT-compatíveis — queries diretas):**

| Campo | Tipo | Uso nas Tools |
|-------|------|---------------|
| `dsProcesso` | keyword | detalhes_processo, verificar_vigencia |
| `btAtivo` | keyword | Filtro em TODAS as tools |
| `localizacao` | geo_point | geo_distance em buscar_jazidas/fornecedores |
| `idSubstancias` | integer[] | terms filter após resolução semântica |
| `nmSubstancias` | text | QPT: match direto por nome |
| `idTipoUsoSubstancias` | integer[] | terms filter por tipo de uso |
| `dsTipoUsoSubstancias` | text | QPT: match direto por tipo de uso |
| `siglasUF` | keyword[] | term filter por UF |
| `nomesMunicipios` | text | match por município |
| `nmTitulares` | text | QPT: match por nome do titular |
| `cnpjTitulares` | keyword[] | QPT: terms por CNPJ do titular |
| `faseProcesso.dsFaseProcesso` | text | match por fase |
| `qtAreaHa` | double | range filter por área |
| `dtProtocolo` | date | range filter por data |

**Campos nested (precisam de custom tool — QPT não gera):**

| Campo Nested | Uso nas Tools |
|--------------|---------------|
| `shapes.poligono` (geo_shape) | jazidas_por_poligono |
| `substancias.dtFimVigencia` | verificar_vigencia_substancia |
| `pessoas` (tipoRelacao, cnpjBasico) | detalhes_processo (extração completa) |
| `eventos` | detalhes_processo (histórico) |
| `titulos` | detalhes_processo (documentos legais) |

### 4.2 `anm_substancia_v001` — Catálogo Semântico

| Campo | Tipo | Uso |
|-------|------|-----|
| `idSubstancia` | integer | ID de ligação com anm_v002.idSubstancias |
| `nmSubstancia` | text | QPT: match textual direto |
| `embedding` | knn_vector | Custom: busca semântica k-NN |

### 4.3 `anm_tipo_uso_substancia_v001` — Catálogo Semântico

| Campo | Tipo | Uso |
|-------|------|-----|
| `idTipoUsoSubstancia` | integer | ID de ligação com anm_v002.idTipoUsoSubstancias |
| `dsTipoUsoSubstancia` | text | QPT: match textual direto |
| `embedding` | knn_vector | Custom: busca semântica k-NN |

### 4.4 `cnpj_v001` — Cross-Reference (MCP Empresas)

| Campo | Tipo | Uso |
|-------|------|-----|
| `empresa.cnpjBasico` | keyword | terms lookup via cnpjTitulares |
| `empresa.razaoSocial` | text | Exibição |
| `ddd1`, `telefone1` | keyword | Contato |
| `correioEletronico` | keyword | Email |
| `socios.nomeSocioRazaoSocial` | text (nested) | Nomes dos sócios |
| `localizacao` | geo_point | Endereço empresa |

---

## 5. Cache Redis — Estratégia por Tool

| Tool | Cache Key Pattern | TTL | O que é cacheado |
|------|-------------------|-----|-------------------|
| `buscar_fornecedores` | `search:fornec:{hash_params}` | 1h | Resultado completo (todos os processos + empresas) |
| `buscar_jazidas` | `search:jazidas:{hash_params}` | 1h | Lista de processos |
| `detalhes_processo` | `proc:{dsProcesso}` | 24h | Dados completos do processo |
| `*` (embeddings) | `emb:{hash_texto}` | 7d | Vetor de embedding gerado |

**Paginação via Redis:**

```
┌─────────────────────────────────────────────────────────────────────┐
│  1ª requisição: buscar_fornecedores("areia", -23.39, -46.60, 30)   │
│                                                                     │
│  Tool executa 3 passos → 76 resultados                             │
│  Redis SET search:fornec:abc123 = [76 docs completos]              │
│  Retorna ao LLM: página 1 (10 de 76)                              │
│                                                                     │
│  2ª requisição: "mostre os próximos"                                │
│  Tool lê Redis GET search:fornec:abc123                            │
│  Retorna ao LLM: página 2 (10 de 76)                              │
│  OpenSearch: 0 chamadas (tudo do cache)                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Estrutura de Diretórios

```
mcp_servers/
├── common/                          # ✅ JÁ IMPLEMENTADO
│   ├── __init__.py
│   ├── config.py                    # MCPSettings (env vars)
│   ├── opensearch_client.py         # OpenSearchService (async)
│   ├── redis_cache.py               # RedisCache (graceful degradation)
│   ├── embeddings.py                # EmbeddingService (Azure OpenAI + cache)
│   └── schemas.py                   # GeoPoint, PaginationParams, ToolResponse
│
├── jazidas/                         # 🆕 A IMPLEMENTAR
│   ├── __init__.py
│   ├── server.py                    # MCP Server (entrada SSE/HTTP, registro tools)
│   ├── tools.py                     # 5 tools com decoradores @mcp.tool()
│   ├── queries/                     # Módulos de queries OpenSearch
│   │   ├── __init__.py
│   │   ├── substancia.py            # Resolução semântica de substâncias (k-NN)
│   │   ├── processos.py             # Queries no anm_v002 (flat + nested)
│   │   └── enriquecimento.py        # Cross-index com cnpj_v001
│   ├── schemas.py                   # Schemas específicos do Jazidas
│   └── cache.py                     # Helpers de cache específicos (patterns)
```

> **Nota:** O módulo `queries/tipo_uso.py` foi removido — resolução de tipo de uso é integrada no `substancia.py` (fallback se substância não encontrada). O módulo `queries/agregacoes.py` foi removido — PPLTool cobre aggregations nativamente.

---

## 7. Fluxo por Tool — Diagrama Completo

### 7.1 `buscar_fornecedores` (Tool Principal)

```
 Usuário                  LangGraph              MCP Jazidas (buscar_fornecedores)
    │                        │                          │
    │  "Fornecedores de      │                          │
    │   areia lavada perto   │                          │
    │   do Rodoanel Norte"   │                          │
    │───────────────────────▶│                          │
    │                        │                          │
    │                        │  ① THOUGHT + ACTION      │
    │                        │  buscar_fornecedores(    │
    │                        │    substancia="areia     │
    │                        │    lavada",              │
    │                        │    lat=-23.39,           │
    │                        │    lon=-46.60,           │
    │                        │    raio_km=30,           │
    │                        │    incluir_contatos=true,│
    │                        │    incluir_socios=true   │
    │                        │  )                       │
    │                        │─────────────────────────▶│
    │                        │                          │
    │                        │                          │ Passo 1: anm_substancia_v001
    │                        │                          │   "areia lavada" → [200207,...]
    │                        │                          │
    │                        │                          │ Passo 2: anm_v002
    │                        │                          │   terms + geo_distance(30km)
    │                        │                          │   → 76 processos + cnpjBasicos
    │                        │                          │
    │                        │                          │ Passo 3: cnpj_v001
    │                        │                          │   terms cnpjBasico → contatos
    │                        │                          │
    │                        │                          │ Redis: salva 76 resultados
    │                        │                          │ Retorna: página 1 (10 de 76)
    │                        │                          │
    │                        │  ③ OBSERVE (10 resultados│
    │                        │     + meta paginação)    │
    │                        │◀─────────────────────────│
    │                        │                          │
    │                        │  ② THOUGHT: "Tenho 76    │
    │                        │  resultados, mostro 10"  │
    │                        │  ACTION: FINISH          │
    │                        │                          │
    │  "Encontrei 76         │                          │
    │   fornecedores de      │                          │
    │   areia. Os 10 mais    │                          │
    │   próximos:            │                          │
    │   1. CIA MELHORAMENTOS │                          │
    │      Tel: (11)3874...  │                          │
    │   ..."                 │                          │
    │◀───────────────────────│                          │
```

---

## 8. Schemas Pydantic (jazidas/schemas.py)

```python
from pydantic import BaseModel, Field
from mcp_servers.common.schemas import GeoPoint, PaginationParams, SearchResultMeta


class SubstanciaMatch(BaseModel):
    """Resultado de resolução semântica de substância."""
    id: int = Field(description="idSubstancia no catálogo ANM")
    nome: str = Field(description="Nome da substância")
    score: float = Field(description="Score de relevância")


class ContatoEmpresa(BaseModel):
    """Dados de contato de uma empresa (cnpj_v001)."""
    telefone: str | None = None
    email: str | None = None
    endereco: str | None = None


class TitularProcesso(BaseModel):
    """Titular de um processo ANM."""
    nome: str
    cnpj_basico: str | None = None
    contato: ContatoEmpresa | None = None
    socios: list[str] | None = None


class ProcessoResumido(BaseModel):
    """Processo ANM em formato resumido (para listagens)."""
    ds_processo: str
    fase: str | None = None
    area_ha: float | None = None
    ativo: bool = True
    substancias: list[str] = []
    tipos_uso: list[str] = []
    municipios: list[str] = []
    uf: list[str] = []
    localizacao: GeoPoint | None = None
    distancia_km: float | None = None
    titular: TitularProcesso | None = None


class FornecedorResult(BaseModel):
    """Resultado combinado: processo + empresa + contato."""
    processo: ProcessoResumido
    contato: ContatoEmpresa | None = None
    socios: list[str] | None = None


class ProcessoDetalhado(BaseModel):
    """Processo ANM com todos os campos expandidos."""
    ds_processo: str
    nr_nup: str | None = None
    ativo: bool = True
    fase: str | None = None
    tipo_requerimento: str | None = None
    area_ha: float | None = None
    dt_protocolo: str | None = None
    localizacao: GeoPoint | None = None
    substancias: list[dict] = []
    municipios: list[dict] = []
    pessoas: list[dict] = []
    eventos: list[dict] = []
    titulos: list[dict] = []
    titular_empresa: dict | None = None


class VigenciaSubstancia(BaseModel):
    """Status de vigência de uma substância em um processo."""
    id_substancia: int
    nome: str
    tipo_uso: str | None = None
    dt_inicio: str | None = None
    dt_fim: str | None = None
    vigente: bool
    motivo_encerramento: str | None = None
```

> **Removidos:** `TipoUsoMatch` e `EstatisticaRegiao` — cobertos por QPT/PPLTool nativamente.

---

## 9. Prioridade de Implementação

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    ORDEM DE IMPLEMENTAÇÃO                                │
│                                                                         │
│  FASE 1 — Fundação (Dia 1, ~0.5d) ──────────────────────────────────── │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  ① server.py        → MCP Server bootstrap (SSE endpoint)      │   │
│  │  ② schemas.py       → Modelos Pydantic                         │   │
│  │  ③ queries/substancia.py → Resolução semântica (k-NN + match)  │   │
│  │  ④ cache.py         → Helpers de cache (paginação, patterns)    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  FASE 2 — Tool Principal + Cross-Index (Dia 2-3, ~2d) ─────────────── │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  ⑤ queries/processos.py     → Queries flat no anm_v002         │   │
│  │  ⑥ queries/enriquecimento.py → Lookup cnpj_v001                │   │
│  │  ⑦ buscar_fornecedores       → Tool composta (3 passos)        │   │
│  │  ⑧ buscar_jazidas            → Tool semântica (2 passos)       │   │
│  │  ⑨ detalhes_processo         → Cross-index + nested extraction │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  FASE 3 — Queries Especializadas + Validação (Dia 4, ~1.5d) ────────  │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  ⑩ jazidas_por_poligono    → Nested geo_shape                  │   │
│  │  ⑪ verificar_vigencia      → Nested substancias                │   │
│  │  ⑫ Testes end-to-end       → Todas as 5 tools                  │   │
│  │  ⑬ MCP Inspector           → Validação interativa              │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ESTIMATIVA TOTAL: ~4.5 dias                                            │
│  (vs 6 dias com 10 tools — economia de 25%)                            │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 10. Decisões Técnicas

### 10.1 Por que buscar_fornecedores é uma tool composta (não 3 tools)?

| Critério | 3 Tools Separadas | 1 Tool Composta |
|----------|-------------------|-----------------|
| Ciclos ReAct | 4 | **2** |
| Tokens LLM | ~12.260 | **~9.250** |
| Latência | ~5-7s | **~2-4s** |
| Custo/busca | ~$0.06 | **~$0.04** |
| Controle de erro | LLM decide retry | **Tool trata internamente** |
| Paginação | Complexa (3 caches) | **Simples (1 cache)** |

### 10.2 Por que 5 tools custom e não 0 (tudo QPT)?

| Limitação do QPT | Tool custom que resolve |
|-------------------|------------------------|
| Não cruza índices (single-index only) | `buscar_fornecedores`, `detalhes_processo` |
| Não gera nested queries | `jazidas_por_poligono`, `verificar_vigencia` |
| Não faz k-NN semântico | `buscar_jazidas` (resolve termos vagos → IDs) |
| Não pagina resultados via cache | `buscar_fornecedores`, `buscar_jazidas` |

### 10.3 Índice compatível: anm_v002 (confirmado)

A reindexação foi concluída com sucesso:

| Métrica | Valor |
|---------|-------|
| Documentos | 956.288 (100% de anm_v001) |
| Erros | 0 |
| Tamanho | 14.23 GB (-15.5% vs v001) |
| Campos flat no root | 22 (QPT-compatíveis) |
| Campos nested | 9 (para queries especializadas) |
| detalhesCNPJ removidos | 2.260.647 blocos |
| Tempo de reindex | 47.4 min |

---

## 11. Exemplo de `server.py` (Bootstrap)

```python
"""
MCP Server: Jazidas
====================
Expõe tools de busca de processos minerários (ANM) via protocolo MCP.

Porta: 8010
Índice principal: anm_v002
Tools: 5 custom (buscar_fornecedores, buscar_jazidas, detalhes_processo,
                  jazidas_por_poligono, verificar_vigencia_substancia)
QPT cobre: queries flat ad-hoc, aggregations, listagens de catálogo
"""

import logging
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route

from mcp_servers.common.config import mcp_settings
from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.common.redis_cache import RedisCache
from mcp_servers.common.embeddings import EmbeddingService

logger = logging.getLogger("mcp.jazidas")

# ==================== Services ====================
os_service = OpenSearchService()
redis_cache = RedisCache()
embedding_service = EmbeddingService()

# ==================== MCP Server ====================
mcp = Server("jazidas")

# Tools are registered via decorators in tools.py
from mcp_servers.jazidas.tools import register_tools
register_tools(mcp, os_service, redis_cache, embedding_service)

# ==================== SSE Transport ====================
sse = SseServerTransport("/messages/")

async def handle_sse(request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await mcp.run(streams[0], streams[1], mcp.create_initialization_options())

# ==================== Lifecycle ====================
async def startup():
    await os_service.connect()
    await redis_cache.connect()
    logger.info(f"MCP Jazidas server starting on port {mcp_settings.mcp_jazidas_port}")
    logger.info("Tools: 5 custom | QPT covers: flat queries, aggregations, catalog lookups")

async def shutdown():
    await os_service.disconnect()
    await redis_cache.disconnect()
    logger.info("MCP Jazidas server stopped")

# ==================== ASGI App ====================
app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse),
        Route("/messages/", endpoint=sse.handle_post_message, methods=["POST"]),
    ],
    on_startup=[startup],
    on_shutdown=[shutdown],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=mcp_settings.mcp_jazidas_port)
```

---

*Documento atualizado em 12/02/2026 — reduzido de 10 para 5 custom tools após reavaliação com QPT ativo no anm_v002 (956.288 docs).*
