# 🏢 MCP Server Empresas — Planejamento Detalhado

## Visão Geral

| Item | Valor |
|------|-------|
| **Porta** | `8011` |
| **Índice principal** | `cnpj_v002` (~69M estabelecimentos, dynamic:strict, pt_brazilian) |
| **Índice auxiliar** | `rfb_cnae_v001` (2.394 docs, 16.8 MB) — catálogo CNAE com embeddings |
| **Índice referência** | `ibge_municipio_v001` (5.631 docs) — shared com MCP Jazidas |
| **Índice cross-reference** | `anm_v002` (956K docs) — processos minerários vinculados |
| **Total de tools custom** | **3** (reduzido de 6 após reavaliação com QPT) |
| **Classificação** | 2 compostas (cross-index) + 1 especializada (nested) |
| **Cenários cobertos por QPT** | ~50% (queries flat, aggregations, lookups diretos) |

---

## 1. Racional: Por que 3 tools e não 6?

O `cnpj_v002` tem apenas **2 campos nested** (nível 1) — significativamente mais simples que o `anm_v002` (9 nested). Com o **QPT (QueryPlanningTool)** ativo, metade das tools originais é dispensável.

### 1.1 Análise de cada tool original

| # | Tool Original | QPT resolve? | Veredicto |
|---|---------------|:------------:|-----------|
| 1 | `buscar_empresas` | ❌ k-NN cross-index + nested CNAE sec. | 🔴 **MANTER** — razão de existir do MCP |
| 2 | `detalhes_empresa` | ⚠️ Fetch sim, enriquecer cross-index não | 🔴 **MANTER** — cross 3 índices |
| 3 | ~~`buscar_por_cnae`~~ | ⚠️ CNAE principal sim, sec. (nested) não | 🟡 **ABSORVIDA** → merge em buscar_empresas |
| 4 | ~~`buscar_por_cnpj_basico`~~ | ✅ `term empresa.cnpjBasico` | 🟢 **REMOVIDA** — QPT cobre |
| 5 | `buscar_por_socio` | ❌ Nested `socios` | 🔴 **MANTER** — QPT não gera nested |
| 6 | ~~`buscar_cnaes`~~ | ✅ `match nomeClasse` no catálogo | 🟢 **REMOVIDA** — QPT cobre |

### 1.2 O que o QPT cobre nativamente (sem código custom)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CENÁRIOS COBERTOS POR QPT + SearchIndexTool (zero código custom)           │
│                                                                             │
│  Pergunta do usuário                      │  QPT gera DSL                   │
│  ─────────────────────────────────────────┼───────────────────────────────  │
│  "Empresa CNPJ 12345678"                  │  term empresa.cnpjBasico        │
│  "Razão social VALE"                      │  match empresa.razaoSocial      │
│  "Empresas ativas em SP"                  │  term uf + term situacao "02"   │
│  "Empresas abertas após 2020"             │  range dataInicioAtividade      │
│  "Capital social > 1 milhão"              │  range empresa.capitalSocial    │
│  "O que é CNAE 0810-0/99?"               │  match nomeClasse no catálogo   │
│  "Quantas empresas por estado?"           │  PPLTool: aggregation by uf     │
│  "Top 10 CNAEs mais comuns em MG"         │  PPLTool: aggregation by CNAE   │
│  "Filiais do grupo VALE (cnpjBasico)"     │  term empresa.cnpjBasico        │
│  "Empresas perto de Campinas (lat/lon)"   │  geo_distance localizacao       │
│                                                                             │
│  Estimativa: ~50% dos cenários do dia-a-dia                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Ganho:** De 6 tools custom em ~4 dias → **3 tools custom em ~2.5 dias**. O QPT absorveu 50% do trabalho custom.

---

## 2. Inventário das 3 Custom Tools

### 2.1 Tools Compostas (Cross-Index)

#### 🔴 Tool 1: `buscar_empresas`

> **A tool mais importante** — busca semântica de empresas por atividade econômica.

| Campo | Valor |
|-------|-------|
| **Descrição** | Busca empresas por atividade econômica (CNAE) + localização, com contato |
| **Fluxo** | 2 passos cross-index (CNAE semântico → empresas CNPJ) |
| **QPT nativo?** | ❌ Impossível — k-NN cross-index + nested cnaeFiscalSecundaria |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `termo_busca` | `string` | ⚠️ | Busca semântica de atividade (ex: "transporte de minérios") |
| `codigos_cnae` | `list[str]` | ⚠️ | OU códigos CNAE diretos (ex: ["4930201"]) |
| `latitude` | `float` | ✅ | Latitude do ponto central |
| `longitude` | `float` | ✅ | Longitude do ponto central |
| `raio_km` | `float` | ❌ | Raio de busca em km (default: 30) |
| `uf` | `string` | ❌ | Filtro por UF (ex: "SP") |
| `apenas_ativas` | `bool` | ❌ | Apenas empresas ativas (default: true) |
| `incluir_contatos` | `bool` | ❌ | Incluir telefone/email/endereço (default: true) |
| `incluir_geometria` | `bool` | ❌ | Incluir pontos + fronteiras municípios para mapa (default: false) |
| `pagina` | `int` | ❌ | Página (default: 1) |
| `por_pagina` | `int` | ❌ | Resultados por página (default: 10, max: 50) |

> ⚠️ Obrigatório fornecer `termo_busca` OU `codigos_cnae` (não ambos).
> `buscar_por_cnae` original foi **absorvida** aqui — a tool aceita tanto termos semânticos quanto códigos diretos.

**Fluxo interno (2 passos):**

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PASSO 1: Resolver CNAE (semântico) — CONDICIONAL                        │
│  Índice: rfb_cnae_v001 (k-NN com embedding)                             │
│  Input: "transporte de minérios"                                         │
│  Output: ["4930201", "4930202"] (CNAEs relevantes)                       │
│  Tempo: ~15ms (k-NN 1536 dims em 2.394 docs)                            │
│  Obs: Pulado se codigos_cnae fornecidos diretamente                      │
├──────────────────────────────────────────────────────────────────────────┤
│  PASSO 2: Buscar empresas no CNPJ                                        │
│  Índice: cnpj_v002 (~69M docs)                                          │
│  Filtros:                                                                │
│    - cnaeFiscalPrincipal.codigo (flat) + cnaeFiscalSecundaria (NESTED)   │
│    - geo_distance em localizacao                                         │
│    - situacaoCadastral.codigo = "02" (se apenas_ativas)                  │
│    - term uf (se especificado)                                           │
│  Source: razaoSocial, nomeFantasia, contato, endereço, localização       │
│  Output: empresas com dados de contato                                   │
│  Tempo: ~50-200ms (depende de filtros + volume)                          │
├──────────────────────────────────────────────────────────────────────────┤
│  MERGE: Formata resultados + salva no Redis (TTL 1h)                     │
│  Retorna página solicitada ao LLM                                        │
│  Envia pontos geo ao frontend (mapa)                                     │
└──────────────────────────────────────────────────────────────────────────┘

Tempo total: ~65-215ms | Ciclos ReAct: 2 (com tool composta)
```

**Resposta:**

```json
{
  "sucesso": true,
  "meta": {
    "total": 61,
    "pagina": 1,
    "por_pagina": 10,
    "total_paginas": 7
  },
  "dados": [
    {
      "cnpj_basico": "12345678",
      "cnpj_completo": "12.345.678/0001-99",
      "razao_social": "TRADO EQUIPAMENTOS E SERVIÇOS LTDA",
      "nome_fantasia": "TRADO",
      "cnae_principal": {
        "codigo": "4930201",
        "descricao": "Transporte rodoviário de carga, municipal"
      },
      "situacao": "Ativa",
      "uf": "MG",
      "municipio": "Belo Horizonte",
      "localizacao": {"lat": -19.92, "lon": -43.93},
      "distancia_km": 5.3,
      "contato": {
        "telefone": "(31) 33337933",
        "email": "ADMINISTRATIVO@TRADO.COM.BR",
        "endereco": "AV PRESIDENTE VARGAS, 1234 - CENTRO - CEP 30130-000"
      }
    }
  ],
  "resolucao": {
    "metodo": "knn",
    "cnaes_resolvidos": ["4930201", "4930202"],
    "termo": "transporte de minérios"
  },
  "mapa": {
    "pontos": [
      {"lat": -19.92, "lon": -43.93, "tipo": "empresa", "nome": "TRADO"}
    ]
  },
  "cache_id": "empresas:search:abc123"
}
```

---

#### 🔴 Tool 2: `detalhes_empresa`

| Campo | Valor |
|-------|-------|
| **Descrição** | Ficha completa de uma empresa com enriquecimento cross-index |
| **Fluxo** | 3 passos (cnpj_v002 → rfb_cnae_v001 + anm_v002) |
| **QPT nativo?** | ❌ Cross-index com 3 índices + extração nested |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `cnpj_basico` | `string` | ✅ | CNPJ básico 8 dígitos (ex: "60730348") |
| `incluir_socios` | `bool` | ❌ | Incluir sócios com qualificação (default: true) |
| `incluir_processos_anm` | `bool` | ❌ | Incluir contagem/resumo de processos ANM (default: false) |
| `incluir_cnaes_detalhados` | `bool` | ❌ | Incluir hierarquia CNAE completa (default: true) |

**Fluxo interno (3 passos):**

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PASSO 1: Buscar empresa no CNPJ                                        │
│  Índice: cnpj_v002                                                       │
│  Query: term empresa.cnpjBasico (retorna TODOS os estabelecimentos)      │
│  Source: dados completos + socios (nested) + cnaeFiscalSecundaria        │
│  Output: razão social, capital social, endereço, sócios, CNAEs          │
│  Tempo: ~20ms                                                            │
├──────────────────────────────────────────────────────────────────────────┤
│  PASSO 2: Enriquecer CNAE (hierarquia completa)                          │
│  Índice: rfb_cnae_v001                                                   │
│  Query: term codigo (principal + secundários)                            │
│  Output: seção → divisão → grupo → classe → subclasse + notas           │
│  Tempo: ~10ms                                                            │
├──────────────────────────────────────────────────────────────────────────┤
│  PASSO 2b: Contagem processos ANM (CONDICIONAL)                          │
│  Índice: anm_v002                                                        │
│  Query: term cnpjTitulares (campo flat no root)                          │
│  Output: total de processos vinculados + resumo por fase                 │
│  Tempo: ~15ms (condicional — só se incluir_processos_anm=true)           │
├──────────────────────────────────────────────────────────────────────────┤
│  MERGE: Combina empresa + CNAE + ANM                                     │
│  Salva no Redis (TTL 24h)                                                │
│  Retorna dados consolidados ao LLM                                       │
└──────────────────────────────────────────────────────────────────────────┘

Tempo total: ~45-65ms | Cache: 24h
```

**Resposta:**

```json
{
  "sucesso": true,
  "dados": {
    "cnpj_basico": "60730348",
    "razao_social": "COMPANHIA MELHORAMENTOS DE SAO PAULO",
    "nome_fantasia": "MELHORAMENTOS",
    "capital_social": 625000000.00,
    "porte": "Demais",
    "natureza_juridica": "Sociedade Anônima Fechada",
    "situacao": "Ativa",
    "data_inicio_atividade": "1967-07-01",
    "endereco": {
      "logradouro": "RODOVIA PRES TANCREDO A NEVES",
      "numero": "SN",
      "bairro": "AREA RURAL",
      "cep": "07700001",
      "municipio": "Caieiras",
      "uf": "SP"
    },
    "contato": {
      "telefone": "(11) 38740400",
      "email": "FISCAL@MELHORAMENTOS.COM.BR"
    },
    "cnae_principal": {
      "codigo": "0810-0/99",
      "descricao": "Extração de outros minerais não-metálicos",
      "hierarquia": {
        "secao": "B - Indústrias Extrativas",
        "divisao": "08 - Extração de minerais não-metálicos",
        "grupo": "081 - Extração de pedra, areia e argila",
        "classe": "0810 - Extração de minerais não-metálicos"
      }
    },
    "cnaes_secundarios": [
      {"codigo": "1710-9/00", "descricao": "Fabricação de celulose"},
      {"codigo": "1721-4/00", "descricao": "Fabricação de papel"}
    ],
    "socios": [
      {
        "nome": "ANTONIO JOAQUIM DE OLIVEIRA",
        "qualificacao": "Diretor",
        "data_entrada": "2019-01-15"
      },
      {
        "nome": "CAROLINA ALVIM GUEDES ALCOFORADO",
        "qualificacao": "Diretor",
        "data_entrada": "2020-06-01"
      }
    ],
    "processos_anm": {
      "total": 5,
      "por_fase": {
        "Autorização de Pesquisa": 3,
        "Concessão de Lavra": 2
      }
    },
    "estabelecimentos": {
      "total": 4,
      "matriz": "60.730.348/0001-21",
      "filiais": ["60.730.348/0002-02", "60.730.348/0003-93", "60.730.348/0004-74"]
    }
  }
}
```

---

### 2.2 Tool Especializada (Query Nested)

#### 🔴 Tool 3: `buscar_por_socio`

| Campo | Valor |
|-------|-------|
| **Descrição** | Busca reversa: encontra empresas onde uma pessoa é sócia |
| **Fluxo** | 1 passo — nested query no campo `socios` do cnpj_v002 |
| **QPT nativo?** | ❌ Nested query em socios — QPT não gera nested |
| **Use case** | Due diligence, compliance, mapeamento de participações |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `nome_socio` | `string` | ⚠️ | Nome do sócio (busca textual) |
| `cpf_cnpj_socio` | `string` | ⚠️ | CPF ou CNPJ do sócio (busca exata) |
| `uf` | `string` | ❌ | Filtro por UF |
| `apenas_ativas` | `bool` | ❌ | Apenas empresas ativas (default: true) |
| `pagina` | `int` | ❌ | Default: 1 |
| `por_pagina` | `int` | ❌ | Default: 10 |

> ⚠️ Obrigatório fornecer `nome_socio` OU `cpf_cnpj_socio` (pelo menos um).

**Query interna:**

```json
{
  "query": {
    "bool": {
      "must": [
        {
          "nested": {
            "path": "socios",
            "query": {
              "bool": {
                "should": [
                  { "match": { "socios.nomeSocioRazaoSocial": "JOAO SILVA" } },
                  { "term": { "socios.cpfCnpjSocio": "12345678900" } }
                ],
                "minimum_should_match": 1
              }
            },
            "inner_hits": {
              "_source": [
                "socios.nomeSocioRazaoSocial",
                "socios.qualificacaoSocio",
                "socios.dataEntradaSociedade"
              ]
            }
          }
        }
      ],
      "filter": [
        { "term": { "situacaoCadastral.codigo": "02" } }
      ]
    }
  }
}
```

**Caso de uso**: "Em quais empresas João Silva é sócio?" ou "Quais empresas estão ligadas ao CPF 123.456.789-00?"

**Resposta:**

```json
{
  "sucesso": true,
  "meta": {
    "total": 3,
    "pagina": 1,
    "por_pagina": 10,
    "total_paginas": 1
  },
  "socio_buscado": {
    "nome": "JOAO SILVA",
    "cpf_cnpj": null
  },
  "empresas": [
    {
      "cnpj_basico": "12345678",
      "cnpj_completo": "12.345.678/0001-99",
      "razao_social": "MINERAÇÃO SILVA LTDA",
      "situacao": "Ativa",
      "uf": "SP",
      "qualificacao_socio": "Sócio-Administrador",
      "data_entrada": "2015-03-20",
      "cnae_principal": "0810-0/99 - Extração de outros minerais"
    },
    {
      "cnpj_basico": "87654321",
      "cnpj_completo": "87.654.321/0001-00",
      "razao_social": "TRANSPORTES SILVA E FILHOS LTDA",
      "situacao": "Ativa",
      "uf": "MG",
      "qualificacao_socio": "Diretor",
      "data_entrada": "2020-01-10",
      "cnae_principal": "4930-2/01 - Transporte rodoviário de carga"
    }
  ]
}
```

---

## 3. Mapa de Cobertura: QPT Nativo vs Custom Tools

```
┌───────────────────────────────────────────────────────────────────────────┐
│                  MAPA DE COBERTURA QPT vs CUSTOM                          │
│                                                                           │
│  ┌───────────────────────────┐ ┌───────────────────────────────────────┐ │
│  │      QPT NATIVO ✅          │ │       CUSTOM TOOLS 🔧 (3)            │ │
│  │  (SearchIndexTool,          │ │                                       │ │
│  │   QueryPlanningTool,        │ │                                       │ │
│  │   PPLTool)                  │ │                                       │ │
│  │                             │ │                                       │ │
│  │  • Empresas por razão social│ │  • buscar_empresas                   │ │
│  │  • Empresas por CNPJ        │ │    (2-index cross: CNAE semântico →  │ │
│  │  • Empresas por UF          │ │     empresas + nested CNAE sec.)     │ │
│  │  • Empresas por raio        │ │                                       │ │
│  │    (geo_distance)           │ │  • detalhes_empresa                  │ │
│  │  • Filiais de uma empresa   │ │    (cross 3 índices: cnpj + CNAE    │ │
│  │    (term cnpjBasico)        │ │     hierarquia + processos ANM)      │ │
│  │  • Capital social (range)   │ │                                       │ │
│  │  • Data abertura (range)    │ │  • buscar_por_socio                  │ │
│  │  • CNAE por código          │ │    (nested socios + inner_hits)      │ │
│  │  • Contagem/aggregations    │ │                                       │ │
│  │  • Situação cadastral       │ │                                       │ │
│  │  • Listagem catálogo CNAE   │ │                                       │ │
│  │                             │ │                                       │ │
│  │  ~50% dos cenários          │ │  ~50% dos cenários                   │ │
│  │  (flat queries, lookups,    │ │  (core negócio, cross-index,         │ │
│  │   aggregations, ranges)     │ │   semântico, nested, cache)          │ │
│  └───────────────────────────┘ └───────────────────────────────────────┘ │
│                                                                           │
│  PRINCÍPIO: QPT para queries flat ad-hoc ─── Custom para orquestração    │
└───────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Quando o agente usa QPT vs Custom?

```
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│  Pergunta envolve...                → Caminho                  │
│  ─────────────────────────────────────────────────             │
│                                                                │
│  CUSTOM TOOLS:                                                 │
│  Atividade (termo vago/semântico)   → buscar_empresas (k-NN)   │
│  CNAE + inclui secundários          → buscar_empresas (nested) │
│  Ficha completa + sócios + ANM      → detalhes_empresa         │
│  "Empresas do sócio X"              → buscar_por_socio         │
│                                                                │
│  QPT NATIVO:                                                   │
│  "Empresas ativas em SP"            → QPT: term + term         │
│  "Empresa CNPJ 60730348"            → QPT: term cnpjBasico     │
│  "Razão social VALE"                → QPT: match razaoSocial   │
│  "Capital social > 1M em MG"        → QPT: range + term        │
│  "CNAE 0810-0/99 perto de BH"      → QPT: term + geo_distance │
│  "Quantas empresas por estado?"     → PPLTool: aggregation     │
│  "Top CNAEs em mineração"           → PPLTool: aggregation     │
│  "O que é CNAE 4930-2/01?"         → QPT: term no catálogo    │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 4. Índices Utilizados e Campos Relevantes

### 4.1 `cnpj_v002` — Índice Principal (~69M estabelecimentos, dynamic:strict)

**Campos flat (QPT-compatíveis — queries diretas):**

| Campo | Tipo | Uso nas Tools |
|-------|------|---------------|
| `id` | keyword | ID único do estabelecimento |
| `empresa.cnpjBasico` | keyword | Chave de ligação (8 dígitos) |
| `empresa.razaoSocial` | text (pt_brazilian) | Exibição + QPT: match |
| `empresa.capitalSocial` | double | QPT: range filter |
| `empresa.porteEmpresa` | keyword | QPT: term filter |
| `nomeFantasia` | text (pt_brazilian) | QPT: match |
| `uf` | keyword | QPT: term filter |
| `localizacao` | geo_point | geo_distance em buscar_empresas |
| `cnaeFiscalPrincipal.codigo` | keyword | term/terms filter (flat) |
| `cnaeFiscalPrincipal.descricao` | text | QPT: match |
| `situacaoCadastral.codigo` | keyword | "02" = ativa |
| `dataInicioAtividade` | date | QPT: range filter |
| `cnpjOrdem` | keyword | Identificação matriz ("0001") vs filial |
| `cnpjDv` | keyword | Dígito verificador |
| `ddd1`, `telefone1` | keyword | Contato telefônico |
| `ddd2`, `telefone2` | keyword | Segundo telefone |
| `correioEletronico` | keyword | Email |
| `tipoLogradouro`, `logradouro`, `numero`, `bairro`, `cep` | text/keyword | Endereço |
| `municipio.nome` | text (pt_brazilian) | Município (enriquecimento IBGE) |

**Campos nested (precisam de custom tool — QPT não gera):**

| Campo Nested | Nível | Uso nas Tools |
|--------------|-------|---------------|
| `cnaeFiscalSecundaria` | 1 | buscar_empresas (terms por código) |
| `socios` | 1 | buscar_por_socio, detalhes_empresa |

**Campos dos sócios (nested `socios`):**

| Campo | Tipo | Uso |
|-------|------|-----|
| `socios.nomeSocioRazaoSocial` | text | match por nome |
| `socios.cpfCnpjSocio` | keyword | term por CPF/CNPJ |
| `socios.qualificacaoSocio.descQualificacao` | text | Qualificação |
| `socios.dataEntradaSociedade` | date | Data de entrada |

### 4.2 `rfb_cnae_v001` — Catálogo Semântico

| Campo | Tipo | Uso |
|-------|------|-----|
| `codigo` | keyword | ID de ligação com cnpj_v002.cnaeFiscalPrincipal.codigo |
| `nomeClasse` | text | QPT: match textual direto |
| `nomeSubclasse` | text | Descrição detalhada |
| `secao`, `nomeSecao` | keyword/text | Hierarquia: seção |
| `divisao`, `nomeDivisao` | keyword/text | Hierarquia: divisão |
| `grupo`, `nomeGrupo` | keyword/text | Hierarquia: grupo |
| `classe`, `nomeClasse` | keyword/text | Hierarquia: classe |
| `notasExplicativas` | text | Notas adicionais |
| `embedding` | knn_vector (1536) | Custom: busca semântica k-NN |

### 4.3 `anm_v002` — Cross-Reference (MCP Jazidas)

| Campo | Tipo | Uso |
|-------|------|-----|
| `cnpjTitulares` | keyword[] | terms lookup (campo flat no root) |
| `dsProcesso` | keyword | Código do processo |
| `faseProcesso.dsFaseProcesso` | text | Fase (para resumo) |
| `btAtivo` | keyword | Status ativo/inativo |

### 4.4 `ibge_municipio_v001` — Referência Geográfica (shared)

| Campo | Tipo | Uso |
|-------|------|-----|
| `nome` | text | Nome do município |
| `siglaUF` | keyword | Sigla do estado |
| `poligono` | geo_shape | Fronteira do município (mapa) |
| `localizacao` | geo_point | Centro do município |

---

## 5. Cache Redis — Estratégia por Tool

| Tool | Cache Key Pattern | TTL | O que é cacheado |
|------|-------------------|-----|-------------------|
| `buscar_empresas` | `empresas:search:{hash_params}` | 1h | Resultado completo (todas as empresas) |
| `detalhes_empresa` | `empresas:detalhe:{cnpj_basico}` | 24h | Dados completos da empresa |
| `buscar_por_socio` | `empresas:socio:{hash_params}` | 1h | Lista de empresas encontradas |
| `*` (embeddings) | `emb:{hash_texto}` | 7d | Vetor de embedding gerado |

**Paginação via Redis:**

```
┌─────────────────────────────────────────────────────────────────────┐
│  1ª requisição: buscar_empresas("transporte minérios", -19.91, ...)│
│                                                                     │
│  Tool executa 2 passos → 61 resultados                             │
│  Redis SET empresas:search:abc123 = [61 docs completos]            │
│  Retorna ao LLM: página 1 (10 de 61)                              │
│                                                                     │
│  2ª requisição: "mostre os próximos"                                │
│  Tool lê Redis GET empresas:search:abc123                          │
│  Retorna ao LLM: página 2 (10 de 61)                              │
│  OpenSearch: 0 chamadas (tudo do cache)                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Estrutura de Diretórios

```
mcp_servers/
├── common/                          # ✅ JÁ IMPLEMENTADO (shared com Jazidas)
│   ├── config.py                    # MCPSettings (env vars)
│   ├── opensearch_client.py         # OpenSearchService (async)
│   ├── redis_cache.py               # RedisCache (graceful degradation)
│   ├── embeddings.py                # EmbeddingService (Azure OpenAI + cache)
│   └── schemas.py                   # GeoPoint, PaginationParams, ToolResponse
│
├── jazidas/                         # ✅ JÁ IMPLEMENTADO (MCP Jazidas — 5 tools)
│   ├── queries/
│   │   ├── municipio.py             # 🔄 REUTILIZAR (lookup ibge_municipio_v001)
│   │   └── geo.py                   # 🔄 REUTILIZAR (GeoJSON, mapa response)
│   └── ...
│
├── empresas/                        # 🆕 A IMPLEMENTAR
│   ├── __init__.py
│   ├── server.py                    # MCP Server bootstrap (Streamable HTTP :8011/mcp)
│   ├── tools.py                     # 3 tools com decoradores @mcp.tool()
│   ├── queries/
│   │   ├── __init__.py
│   │   ├── cnae.py                  # CnaeResolver (k-NN + match) — análogo a substancia.py
│   │   ├── empresas.py              # Queries cnpj_v002 (flat + nested CNAE sec.)
│   │   ├── detalhes.py              # Cross-index: cnpj + CNAE hierarquia + ANM
│   │   └── socios.py                # Nested socios query + inner_hits
│   ├── schemas.py                   # Schemas: EmpresaResult, CnaeMatch, SocioResult, etc.
│   └── cache.py                     # Helpers de cache (patterns, TTLs)
```

**Reutilização do MCP Jazidas:**

| Módulo | O que reutiliza | Como |
|--------|-----------------|------|
| `common/opensearch_client.py` | Conexão OpenSearch, search, msearch | Direto (shared) |
| `common/redis_cache.py` | Cache Redis, paginação | Direto (shared) |
| `common/embeddings.py` | Azure OpenAI embeddings | Direto (shared) |
| `jazidas/queries/municipio.py` | Lookup ibge_municipio_v001 | Import direto |
| `jazidas/queries/geo.py` | GeoJSON, pontos de mapa | Import direto |

> **Nota:** O `CnaeResolver` segue o mesmo padrão do `SubstanciaResolver` do Jazidas — ambos fazem k-NN + fallback match em índices de catálogo com embedding.

---

## 7. Schemas Pydantic (empresas/schemas.py)

```python
from pydantic import BaseModel, Field
from mcp_servers.common.schemas import GeoPoint, PaginationParams


class CnaeMatch(BaseModel):
    """Resultado de resolução semântica de CNAE."""
    codigo: str = Field(description="Código CNAE (ex: '4930-2/01')")
    nome: str = Field(description="Nome da classe/subclasse")
    score: float = Field(description="Score de relevância")


class ResolucaoCnae(BaseModel):
    """Resultado da resolução semântica de CNAE (CnaeResolver)."""
    metodo: str = Field(description="'knn' ou 'match'")
    codigos: list[str] = Field(description="Códigos CNAE resolvidos")
    matches: list[CnaeMatch] = Field(default_factory=list)
    termo_original: str = Field(description="Termo de busca original")


class ContatoEmpresa(BaseModel):
    """Dados de contato de uma empresa."""
    telefone: str | None = None
    telefone2: str | None = None
    email: str | None = None
    endereco: str | None = None


class EnderecoEmpresa(BaseModel):
    """Endereço completo de uma empresa."""
    tipo_logradouro: str | None = None
    logradouro: str | None = None
    numero: str | None = None
    complemento: str | None = None
    bairro: str | None = None
    cep: str | None = None
    municipio: str | None = None
    uf: str | None = None


class EmpresaResumida(BaseModel):
    """Empresa em formato resumido (para listagens)."""
    cnpj_basico: str
    cnpj_completo: str | None = None
    razao_social: str
    nome_fantasia: str | None = None
    cnae_principal: str | None = Field(default=None, description="Código CNAE principal")
    cnae_descricao: str | None = Field(default=None, description="Descrição CNAE")
    situacao: str | None = None
    uf: str | None = None
    municipio: str | None = None
    localizacao: GeoPoint | None = None
    distancia_km: float | None = None
    contato: ContatoEmpresa | None = None


class SocioEmpresa(BaseModel):
    """Sócio de uma empresa."""
    nome: str = Field(description="Nome completo do sócio")
    cpf_cnpj: str | None = Field(default=None, description="CPF ou CNPJ do sócio")
    qualificacao: str | None = Field(default=None, description="Qualificação no QSA")
    data_entrada: str | None = Field(default=None, description="Data de entrada na sociedade")


class CnaeHierarquia(BaseModel):
    """CNAE com hierarquia completa (enriquecido via rfb_cnae_v001)."""
    codigo: str
    descricao: str
    secao: str | None = None
    divisao: str | None = None
    grupo: str | None = None
    classe: str | None = None
    notas_explicativas: str | None = None


class ProcessoAnmResumo(BaseModel):
    """Resumo de processos ANM vinculados a uma empresa."""
    total: int = Field(description="Total de processos")
    por_fase: dict[str, int] = Field(
        default_factory=dict,
        description="Contagem por fase (ex: {'Concessão de Lavra': 2})"
    )


class EmpresaDetalhada(BaseModel):
    """Empresa com todos os campos expandidos (detalhes_empresa)."""
    cnpj_basico: str
    cnpj_completo: str | None = None
    razao_social: str
    nome_fantasia: str | None = None
    capital_social: float | None = None
    porte: str | None = None
    natureza_juridica: str | None = None
    situacao: str | None = None
    data_inicio_atividade: str | None = None
    endereco: EnderecoEmpresa | None = None
    contato: ContatoEmpresa | None = None
    localizacao: GeoPoint | None = None
    cnae_principal: CnaeHierarquia | None = None
    cnaes_secundarios: list[CnaeHierarquia] = []
    socios: list[SocioEmpresa] = []
    processos_anm: ProcessoAnmResumo | None = None
    estabelecimentos: dict | None = Field(
        default=None,
        description="Total de estabelecimentos (matriz + filiais)"
    )


class SocioResultado(BaseModel):
    """Empresa encontrada via busca por sócio."""
    cnpj_basico: str
    cnpj_completo: str | None = None
    razao_social: str
    situacao: str | None = None
    uf: str | None = None
    cnae_principal: str | None = None
    qualificacao_socio: str | None = None
    data_entrada: str | None = None
```

---

## 8. Comparação: MCP Jazidas vs MCP Empresas

| Dimensão | MCP Jazidas | MCP Empresas |
|----------|-------------|--------------|
| **Port** | 8010 | 8011 |
| **Índice principal** | `anm_v002` (956K, 14 GB) | `cnpj_v002` (~69M, dynamic:strict) |
| **Volume** | Médio | **Massivo** (~72x maior) |
| **Catálogo semântico** | `anm_substancia_v001` (862) | `rfb_cnae_v001` (2.394) |
| **Resolver** | `SubstanciaResolver` (k-NN + match) | `CnaeResolver` (k-NN + match) |
| **Campos nested** | 9 | **2** (muito mais simples) |
| **Tools custom** | 5 | **3** |
| **Compostas (cross-index)** | 3 | 2 |
| **Especializadas (nested)** | 2 | 1 |
| **Reutilização** | — | common/, municipio.py, geo.py |
| **Estimativa** | ~4.5 dias | **~2.5 dias** |

---

## 9. Prioridade de Implementação

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    ORDEM DE IMPLEMENTAÇÃO                                │
│                                                                         │
│  FASE 1 — Fundação (Dia 1, ~4h) ────────────────────────────────────── │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  ① server.py        → MCP Server bootstrap (Streamable HTTP)    │   │
│  │  ② schemas.py       → Modelos Pydantic (8 schemas)              │   │
│  │  ③ queries/cnae.py  → CnaeResolver (k-NN + match)              │   │
│  │  ④ cache.py         → Helpers de cache (patterns, paginação)    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  FASE 2 — Tools Cross-Index (Dia 2-3, ~1.5d) ──────────────────────── │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  ⑤ queries/empresas.py  → Queries cnpj_v002 (flat + nested)    │   │
│  │  ⑥ buscar_empresas      → Tool composta (2 passos + nested)    │   │
│  │  ⑦ queries/detalhes.py  → Cross: cnpj + CNAE + ANM (3 índices)│   │
│  │  ⑧ detalhes_empresa     → Ficha completa enriquecida           │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  FASE 3 — Query Nested + Validação (Dia 4, ~1d) ──────────────────── │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  ⑨ queries/socios.py    → Nested socios + inner_hits           │   │
│  │  ⑩ buscar_por_socio     → Tool nested                          │   │
│  │  ⑪ Testes end-to-end    → Todas as 3 tools                     │   │
│  │  ⑫ MCP Inspector        → Validação interativa                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ESTIMATIVA TOTAL: ~2.5 dias                                            │
│  (vs 4 dias com 6 tools — economia de 37%)                             │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 10. Decisões Técnicas

### 10.1 Por que buscar_por_cnae foi absorvida em buscar_empresas?

| Critério | 2 Tools Separadas | 1 Tool Unificada |
|----------|-------------------|------------------|
| Duplicação de código | Query CNAE em ambas | **Query única com 2 modos** |
| UX do agente | LLM decide qual usar | **Uma tool resolve ambos** |
| Manutenção | 2 queries, 2 caches | **1 query, 1 cache** |
| Parâmetros | Overlap 90% | **Aceita termo_busca OU codigos_cnae** |

### 10.2 Por que detalhes_empresa precisa de cross-index?

| Informação | cnpj_v002 (raw) | detalhes_empresa (enriquecido) |
|------------|-----------------|-------------------------------|
| CNAE | `"0810-0/99"` | `"B → 08 → 081 → 0810: Extração de minerais"` |
| Sócios | Nested JSON | Lista formatada com qualificação |
| Processos ANM | Não disponível | `"5 processos (3 pesquisa, 2 lavra)"` |
| Estabelecimentos | Doc por filial | Contagem consolidada |

### 10.3 Diferença para buscar_fornecedores (MCP Jazidas)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│  buscar_fornecedores (Jazidas)     vs    buscar_empresas (Empresas)     │
│  ────────────────────────────────        ─────────────────────────      │
│                                                                         │
│  Entrada: substância mineral             Entrada: atividade econômica   │
│  Catálogo: anm_substancia_v001           Catálogo: rfb_cnae_v001        │
│  Índice: anm_v002 → cnpj_v002           Índice: cnpj_v002 direto       │
│  Passos: 3 (subst → ANM → CNPJ)        Passos: 2 (CNAE → CNPJ)       │
│  Foco: processos minerários             Foco: empresas (qualquer)       │
│  Geo: coordenada da JAZIDA              Geo: coordenada da EMPRESA      │
│                                                                         │
│  Exemplo: "areia lavada perto da obra"  "transportadora perto de BH"   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

Complementares — não se sobrepõem:
- buscar_fornecedores: "Quem extrai areia perto da obra?" (via ANM)
- buscar_empresas: "Quem transporta minério perto da obra?" (via CNPJ/CNAE)
```

### 10.4 Por que 3 tools custom e não 0 (tudo QPT)?

| Limitação do QPT | Tool custom que resolve |
|-------------------|------------------------|
| Não cruza índices (single-index only) | `buscar_empresas` (rfb_cnae_v001 → cnpj_v002), `detalhes_empresa` (cnpj + CNAE + ANM) |
| Não gera nested queries | `buscar_por_socio` (socios), `buscar_empresas` (cnaeFiscalSecundaria) |
| Não faz k-NN semântico | `buscar_empresas` (resolve termos vagos → CNAEs via embedding) |
| Não pagina resultados via cache | `buscar_empresas`, `buscar_por_socio` |

---

## 11. Cronograma (Semana 4 — 23 a 27/02)

| Dia | Entrega | Estimativa |
|-----|---------|------------|
| **Seg 23** | Fase 1: Fundação (server.py, schemas, CnaeResolver, cache) | ~4h |
| **Ter 24** | Tool 1: `buscar_empresas` (k-NN CNAE → cnpj_v002 + nested + geo) | ~6h |
| **Qua 25** | Tool 2: `detalhes_empresa` (cross 3 índices + sócios + ANM) | ~4h |
| **Qui 26** | Tool 3: `buscar_por_socio` (nested socios + inner_hits) | ~3h |
| **Sex 27** | Testes, ajustes, docs, fechar prestação de contas fevereiro | ~4h |

---

## 12. Mapa Completo: Tool → MCP Server → Conexão (Empresas)

| Tool | Tipo | MCP Server | Conexão | Índice(s) | Cache |
|------|------|------------|---------|-----------|-------|
| `ListIndexTool` | Nativa | OpenSearch MCP | Streamable HTTP | todos | ❌ |
| `QueryPlanningTool` | Nativa | OpenSearch MCP | Streamable HTTP | todos | ❌ |
| `PPLTool` | Nativa | OpenSearch MCP | Streamable HTTP | todos | ❌ |
| `SearchIndexTool` | Nativa | OpenSearch MCP | Streamable HTTP | todos | ❌ |
| `buscar_empresas` | **Custom** | **MCP Empresas (:8011)** | **Streamable HTTP** | `rfb_cnae_v001` → `cnpj_v002` | ✅ |
| `detalhes_empresa` | **Custom** | **MCP Empresas (:8011)** | **Streamable HTTP** | `cnpj_v002` + `rfb_cnae_v001` + `anm_v002` | ✅ |
| `buscar_por_socio` | **Custom** | **MCP Empresas (:8011)** | **Streamable HTTP** | `cnpj_v002` | ✅ |

> **Resumo**: 7 tools nativas (via MCP OpenSearch nativo) + 3 tools customizadas (via MCP Empresas Python) = **10 tools** disponíveis para o agente no domínio Empresas.

---

*Documento criado em 20/02/2026 — Atualizado 23/02/2026: migrado para cnpj_v002 (~69M estabelecimentos, ~65M empresas, dynamic:strict, pt_brazilian).*
