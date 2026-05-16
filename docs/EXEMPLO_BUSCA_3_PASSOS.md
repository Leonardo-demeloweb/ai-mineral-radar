# Exemplo Prático: Busca em 3 Passos (Cross-Index)

## Pergunta do Usuário

> **"Preciso encontrar fornecedores de areia lavada perto da obra Rodoanel Norte, num raio de 30km (liste CNPJ, dados de contato, como telefone e email, e traga o nome dos sócios)"**

## Visão Geral do Fluxo

```
┌────────────────────────┐     ┌────────────────────────┐     ┌────────────────────────┐
│      PASSO 1           │     │      PASSO 2           │     │      PASSO 3           │
│                        │     │                        │     │                        │
│  anm_substancia_v001   │────▶│      anm_v001          │────▶│     cnpj_v001          │
│                        │     │                        │     │                        │
│  "areia lavada"        │     │  idSubstancia +        │     │  cnpjBasico            │
│  → idSubstancia        │     │  geo_distance(30km)    │     │  → telefone, email,    │
│                        │     │  → processos + CNPJ    │     │    sócios              │
└────────────────────────┘     └────────────────────────┘     └────────────────────────┘
        ~5ms                          ~30ms                          ~25ms
```

**Ligação entre índices (equivalente a JOIN em SQL):**

| De → Para | Campo de ligação |
|-----------|-----------------|
| `anm_substancia_v001` → `anm_v001` | `idSubstancia` |
| `anm_v001` → `cnpj_v001` | `cnpjBasico` (extraído do titular) |

---

## PASSO 1: Identificar a substância

**Objetivo**: Converter o termo "areia lavada" em IDs de substância que a ANM reconhece.

**Índice**: `anm_substancia_v001` (862 substâncias cadastradas, com embedding para busca semântica)

### Query DSL

```json
{
  "size": 5,
  "query": {
    "match": {
      "nmSubstancia": "areia lavada"
    }
  },
  "_source": ["idSubstancia", "nmSubstancia"]
}
```

### Resultado Real (executado no cluster de produção)

```
Encontrados: 17 substâncias

  ID: 200207 → AREIA LAVADA        (score: 4.17)  ← match exato
  ID: 200200 → AREIA               (score: 2.07)
  ID: 105327 → AREIA MONAZÍTICA    (score: 1.59)
  ID: 200201 → AREIA ALUVIONAR     (score: 1.59)
  ID: 200202 → AREIA COMUM         (score: 1.59)
```

**Saída do Passo 1**: `idSubstancias = [200207, 200200, 200201, 200202]`

> 💡 **Nota**: Incluímos variações de areia para ampliar os resultados. O agente decide automaticamente se deve buscar apenas o match exato ou incluir variações, baseado no contexto.

---

## PASSO 2: Buscar processos ANM por substância + localização

**Objetivo**: Encontrar processos minerários que extraem areia, dentro do raio de 30km da obra, e extrair os CNPJs dos titulares.

**Índice**: `anm_v001` (956.288 processos)

**Coordenadas do Rodoanel Norte (SP)**: lat = -23.39, lon = -46.60

### Query DSL (estrutura atual com nested)

```json
{
  "size": 30,
  "query": {
    "bool": {
      "must": [
        {
          "nested": {
            "path": "substancias",
            "query": {
              "terms": {
                "substancias.Substancia.idSubstancia": [200207, 200200, 200201, 200202]
              }
            }
          }
        },
        {
          "nested": {
            "path": "poligonos",
            "query": {
              "geo_distance": {
                "distance": "30km",
                "poligonos.localizacao": {
                  "lat": -23.39,
                  "lon": -46.60
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
  },
  "_source": [
    "dsProcesso",
    "faseProcesso.dsFaseProcesso",
    "qtAreaHa",
    "poligonos.localizacao",
    "pessoas.pessoa.nmPessoa",
    "pessoas.detalhesCNPJ.empresa.cnpjBasico",
    "pessoas.detalhesCNPJ.empresa.razaoSocial",
    "municipios.nome",
    "municipios.siglaUF"
  ]
}
```

### Query DSL (como seria no anm_v002 flat — sem nested)

```json
{
  "size": 30,
  "query": {
    "bool": {
      "must": [
        { "terms": { "idSubstancias": [200207, 200200, 200201, 200202] } },
        {
          "geo_distance": {
            "distance": "30km",
            "localizacao": { "lat": -23.39, "lon": -46.60 }
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

> ⚠️ **Observação**: A query flat tem 12 linhas. A query nested tem 31 linhas. Ambas retornam o mesmo resultado.

### Resultado Real (executado no cluster de produção)

```
Total processos encontrados: 76

--- Processo 1: 820.603/2018 ---
  Fase: Autorização de Pesquisa
  Área: 552.75 ha
  Localização: -23.37, -46.76
  Município: Caieiras/SP
  Titular: COMPANHIA MELHORAMENTOS DE SAO PAULO (CNPJ base: 60730348)

--- Processo 2: 820.350/2014 ---
  Fase: Autorização de Pesquisa
  Área: 347.12 ha
  Localização: -23.36, -46.82
  Município: Caieiras/SP, Cajamar/SP
  Titular: COMPANHIA MELHORAMENTOS DE SAO PAULO (CNPJ base: 60730348)

--- Processo 3: 820.575/2017 ---
  Fase: Autorização de Pesquisa
  Área: 62.96 ha
  Localização: -23.35, -46.36
  Município: Arujá/SP, Guarulhos/SP
  Titular: (pessoa física — sem CNPJ)

--- Processo 4: 820.469/2016 ---
  Fase: Autorização de Pesquisa
  Área: 41.34 ha
  Localização: -23.39, -46.38
  Município: Guarulhos/SP
  Titular: EMPRESA DE MINERACAO FLORESTA NEGRA LTDA (CNPJ base: 43493899)

--- Processo 5: 820.542/1995 ---
  Fase: Requerimento de Pesquisa
  Área: 50.0 ha
  Localização: -23.49, -46.53
  Município: Guarulhos/SP, São Paulo/SP
  Titular: VITERBO MACHADO LUZ MINERACAO LTDA (CNPJ base: 01587695)

(... +71 processos ...)
```

**Saída do Passo 2**: 
- 76 processos com processo ANM, fase, área, localização, município
- CNPJs dos titulares extraídos: `["60730348", "43493899", "01587695", ...]`

---

## PASSO 3: Buscar dados de contato e sócios

**Objetivo**: Com os CNPJs coletados no Passo 2, buscar no índice da Receita Federal os dados de contato (telefone, email) e nomes dos sócios.

**Índice**: `cnpj_v001` (221 milhões de estabelecimentos)

### Query DSL

```json
{
  "size": 100,
  "query": {
    "terms": {
      "empresa.cnpjBasico": ["60730348", "43493899", "01587695"]
    }
  },
  "_source": [
    "empresa.cnpjBasico",
    "empresa.razaoSocial",
    "ddd1", "telefone1",
    "ddd2", "telefone2",
    "correioEletronico",
    "tipoLogradouro", "logradouro", "numero", "bairro", "cep",
    "municipio.nome", "municipio.siglaUF",
    "situacaoCadastral.descSituacao",
    "socios.nomeSocioRazaoSocial",
    "socios.qualificacaoSocio.descQualificacao"
  ]
}
```

### Resultado Real (executado no cluster de produção)

```
Estabelecimentos encontrados: 11
(uma empresa pode ter vários estabelecimentos/filiais)

═══ EMPRESA 1: COMPANHIA MELHORAMENTOS DE SAO PAULO ═══
  CNPJ Básico: 60730348
  Endereço: RODOVIA PRES TANCREDO A NEVES, SN - CEP 07700001
  📞 Telefone: (11) 38740400
  📧 Email: FISCAL@MELHORAMENTOS.COM.BR
  👥 Sócios (14):
    • ANTONIO JOAQUIM DE OLIVEIRA
    • CAROLINA ALVIM GUEDES ALCOFORADO
    • CLAUDINEI RODRIGUES DA CUNHA
    • HELIO LIMA MAGALHAES
    • INGO PLOGER
    (... +9 sócios)

═══ EMPRESA 2: EMPRESA DE MINERACAO FLORESTA NEGRA LTDA ═══
  CNPJ Básico: 43493899
  Endereço: ESTRADA DA PARTEIRA, 3000, BONSUCESSO - CEP 07178130
  📞 Telefone: (11) 24361210
  📧 Email: FLORESTANEGRALTDA@HOTMAIL.COM
  👥 Sócios (2):
    • KAREN KEHRLE
    • UDO KEHRLE
```

**Saída do Passo 3**: Dados de contato completos de cada empresa (telefone, email, endereço, sócios).

---

## RESULTADO FINAL COMBINADO

O agente (MCP Server) combina os 3 passos e entrega ao usuário:

| # | Processo ANM | Empresa | CNPJ | Município | Fase | Telefone | Email | Sócios |
|---|-------------|---------|------|-----------|------|----------|-------|--------|
| 1 | 820.603/2018 | CIA MELHORAMENTOS DE SP | 60730348 | Caieiras/SP | Aut. Pesquisa | (11) 38740400 | FISCAL@MELHORAMENTOS.COM.BR | Antonio J. Oliveira, Carolina Alcoforado, +12 |
| 2 | 820.469/2016 | MINERAÇÃO FLORESTA NEGRA | 43493899 | Guarulhos/SP | Aut. Pesquisa | (11) 24361210 | FLORESTANEGRALTDA@HOTMAIL.COM | Karen Kehrle, Udo Kehrle |
| 3 | 820.542/1995 | VITERBO MACHADO LUZ | 01587695 | Guarulhos/SP | Req. Pesquisa | (buscar) | (buscar) | (buscar) |
| ... | +73 processos | ... | ... | ... | ... | ... | ... | ... |

**Total: 76 processos encontrados em 30km do Rodoanel Norte**

---

## Equivalência com SQL (para referência)

O que estamos fazendo é equivalente a este SQL:

```sql
-- Passo 1: Identificar substâncias
WITH substancias AS (
  SELECT idSubstancia 
  FROM anm_substancia 
  WHERE nmSubstancia ILIKE '%areia%'
),

-- Passo 2: Buscar processos ANM no raio
processos AS (
  SELECT p.dsProcesso, p.fase, p.area, p.lat, p.lon, p.cnpjBasico
  FROM anm_processo p
  JOIN anm_processo_substancia ps ON p.id = ps.processo_id
  WHERE ps.idSubstancia IN (SELECT idSubstancia FROM substancias)
    AND ST_DWithin(p.geom, ST_MakePoint(-46.60, -23.39)::geography, 30000)
    AND p.ativo = true
),

-- Passo 3: Enriquecer com dados CNPJ
resultado AS (
  SELECT 
    pr.*,
    e.telefone, e.email, e.endereco,
    s.nomeSocio
  FROM processos pr
  LEFT JOIN estabelecimento_cnpj e ON pr.cnpjBasico = e.cnpjBasico
  LEFT JOIN socios s ON e.cnpjBasico = s.cnpjBasico
)
SELECT * FROM resultado;
```

**A diferença**: no SQL é 1 query com JOINs. No OpenSearch são 3 queries separadas, combinadas na camada de aplicação (MCP Server). O resultado final é idêntico.

---

## Tempo de Execução

| Passo | Índice | Tempo | Docs pesquisados |
|-------|--------|-------|-----------------|
| 1 | `anm_substancia_v001` | ~5ms | 862 |
| 2 | `anm_v001` | ~30ms | 956.288 |
| 3 | `cnpj_v001` | ~25ms | 221.000.000 |
| **Total** | | **~60ms** | |

---

## O que o MCP Server faz automaticamente

O usuário **não precisa saber** que existem 3 passos. Ele pergunta em linguagem natural e o agente:

1. Identifica que precisa buscar substância → chama Passo 1
2. Com os IDs, busca processos na região → chama Passo 2
3. Extrai CNPJs dos titulares → chama Passo 3
4. Combina tudo → entrega a resposta completa

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  👤 Usuário: "Fornecedores de areia lavada perto do Rodoanel    │
│              Norte, raio 30km, com CNPJ, contato e sócios"       │
│                                                                  │
│                          │                                       │
│                          ▼                                       │
│                    ┌───────────┐                                 │
│                    │ LangGraph │                                 │
│                    │  (Agente) │                                 │
│                    └─────┬─────┘                                 │
│                          │                                       │
│              ┌───────────┼───────────┐                          │
│              ▼           ▼           ▼                           │
│        ┌──────────┐ ┌──────────┐ ┌──────────┐                  │
│        │ Passo 1  │ │ Passo 2  │ │ Passo 3  │                  │
│        │substância│─▶│processos │─▶│ contatos │                  │
│        │ (5ms)    │ │ (30ms)   │ │ (25ms)   │                  │
│        └──────────┘ └──────────┘ └──────────┘                  │
│              │           │           │                           │
│              └───────────┼───────────┘                          │
│                          ▼                                       │
│                    ┌───────────┐                                 │
│                    │   MERGE   │                                 │
│                    │  combina  │                                 │
│                    │ resultados│                                 │
│                    └─────┬─────┘                                 │
│                          ▼                                       │
│  🤖 Agente: "Encontrei 76 processos de areia no raio de 30km.  │
│             Aqui estão os fornecedores com contatos:             │
│                                                                  │
│             1. CIA MELHORAMENTOS DE SP                            │
│                Tel: (11) 3874-0400                                │
│                Email: fiscal@melhoramentos.com.br                │
│                Sócios: Antonio Oliveira, Carolina Alcoforado...  │
│                                                                  │
│             2. MINERAÇÃO FLORESTA NEGRA                          │
│                Tel: (11) 2436-1210                                │
│                Email: florestanegraltda@hotmail.com               │
│                Sócios: Karen Kehrle, Udo Kehrle                  │
│                                                                  │
│             (...)"                                               │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Análise de Ciclos do Agente ReAct (com ~50 resultados)

### O que é um Ciclo ReAct?

Um agente **ReAct** (Reasoning + Acting) funciona em ciclos repetitivos de 3 etapas:

```
┌──────────────────────────────────────────────────────────┐
│                 1 CICLO ReAct                             │
│                                                          │
│  ① THOUGHT   → LLM raciocina sobre o que fazer           │
│  ② ACTION    → LLM chama uma tool                        │
│  ③ OBSERVE   → LLM recebe o resultado da tool            │
│                                                          │
│  O agente repete ciclos até ter informação suficiente     │
│  para gerar a resposta final (FINISH).                   │
└──────────────────────────────────────────────────────────┘
```

### Cenário: Tools Granulares (3 tools separadas)

Se cada passo for uma tool independente:

```
╔═══════════════════════════════════════════════════════════════════════╗
║  CICLO 1                                                             ║
║  ① Thought: "Usuário quer areia lavada. Preciso identificar o ID     ║
║              da substância."                                          ║
║  ② Action:  resolver_substancia("areia lavada")                      ║
║  ③ Observe: [200207=AREIA LAVADA, 200200=AREIA, ...]                ║
║                                                                      ║
║  Tokens consumidos:                                                   ║
║    Input:  ~500 (system prompt + histórico + tools disponíveis)       ║
║    Output: ~30  (chamada da tool)                                     ║
║    Observação: ~50 (IDs das substâncias)                              ║
╠═══════════════════════════════════════════════════════════════════════╣
║  CICLO 2                                                             ║
║  ① Thought: "Tenho os IDs. Agora buscar processos no raio de 30km." ║
║  ② Action:  buscar_jazida(ids=[200207,200200], lat=-23.39,           ║
║              lon=-46.60, raio_km=30)                                  ║
║  ③ Observe: 50 processos com dsProcesso, fase, cnpjBasico            ║
║                                                                      ║
║  Tokens consumidos:                                                   ║
║    Input:  ~700 (prompt + histórico + observação anterior)            ║
║    Output: ~40  (chamada da tool)                                     ║
║    Observação: ~2.500 (50 processos × ~50 tokens cada)               ║
╠═══════════════════════════════════════════════════════════════════════╣
║  CICLO 3                                                             ║
║  ① Thought: "Tenho 50 processos com 15 CNPJs únicos. Preciso buscar ║
║              contatos e sócios."                                      ║
║  ② Action:  buscar_empresas(cnpjs=["60730348","43493899",...])       ║
║  ③ Observe: 15 empresas com telefone, email, sócios                  ║
║                                                                      ║
║  Tokens consumidos:                                                   ║
║    Input:  ~3.400 (prompt + histórico + 2 observações anteriores)     ║
║    Output: ~40   (chamada da tool)                                    ║
║    Observação: ~1.500 (15 empresas × ~100 tokens cada)               ║
╠═══════════════════════════════════════════════════════════════════════╣
║  CICLO 4 (FINAL)                                                     ║
║  ① Thought: "Tenho tudo. Vou montar a resposta final."               ║
║  ② Action:  FINISH (resposta ao usuário)                              ║
║  ③ Observe: — (não há, é a resposta final)                           ║
║                                                                      ║
║  Tokens consumidos:                                                   ║
║    Input:  ~5.000 (prompt + histórico + 3 observações)                ║
║    Output: ~2.000 (resposta formatada com 50 resultados)              ║
╚═══════════════════════════════════════════════════════════════════════╝

TOTAL: 4 ciclos | 4 chamadas ao LLM | ~12.260 tokens
```

### Cenário: Tool Composta (1 tool que faz os 3 passos internamente)

Se a tool `buscar_fornecedores` fizer os 3 passos internamente (RECOMENDADO):

```
╔═══════════════════════════════════════════════════════════════════════╗
║  CICLO 1                                                             ║
║  ① Thought: "Usuário quer fornecedores de areia lavada perto do      ║
║              Rodoanel Norte com contatos e sócios."                   ║
║  ② Action:  buscar_fornecedores(                                     ║
║               substancia="areia lavada",                              ║
║               latitude=-23.39, longitude=-46.60,                      ║
║               raio_km=30,                                             ║
║               incluir_contatos=true,                                  ║
║               incluir_socios=true                                     ║
║             )                                                         ║
║  ③ Observe: 50 resultados já combinados (processo + empresa +         ║
║             telefone + email + sócios)                                ║
║                                                                      ║
║  Tokens consumidos:                                                   ║
║    Input:  ~500  (system prompt + tools disponíveis)                  ║
║    Output: ~50   (chamada da tool com parâmetros)                     ║
║    Observação: ~3.000 (50 resultados combinados × ~60 tokens cada)   ║
║                                                                      ║
║  ⏱️ Internamente a tool executa os 3 passos (~60ms total):            ║
║    Passo 1: anm_substancia_v001 → IDs                                ║
║    Passo 2: anm_v001 → 50 processos + CNPJs                         ║
║    Passo 3: cnpj_v001 → contatos e sócios                           ║
║    Merge: combina tudo                                                ║
╠═══════════════════════════════════════════════════════════════════════╣
║  CICLO 2 (FINAL)                                                     ║
║  ① Thought: "Tenho tudo. Vou formatar a resposta."                   ║
║  ② Action:  FINISH (resposta ao usuário)                              ║
║                                                                      ║
║  Tokens consumidos:                                                   ║
║    Input:  ~3.700 (prompt + observação)                               ║
║    Output: ~2.000 (resposta formatada com 50 resultados)              ║
╚═══════════════════════════════════════════════════════════════════════╝

TOTAL: 2 ciclos | 2 chamadas ao LLM | ~9.250 tokens
```

### Comparativo Direto

| Métrica | Tools Granulares (3 tools) | Tool Composta (1 tool) |
|---------|---------------------------|----------------------|
| **Ciclos ReAct** | **4** | **2** |
| **Chamadas ao LLM** | 4 | 2 |
| **Chamadas ao OpenSearch** | 3 | 3 (dentro da tool) |
| **Tokens totais** | ~12.260 | ~9.250 |
| **Custo estimado (GPT-4o)** | ~$0.06 | ~$0.04 |
| **Latência LLM** | ~4-6s (4 chamadas) | ~2-3s (2 chamadas) |
| **Latência OpenSearch** | ~60ms | ~60ms |
| **Latência total** | ~5-7s | ~2-4s |

### Ponto-chave: 50 resultados NÃO geram 50 ciclos

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  ⚠️ IMPORTANTE: O número de RESULTADOS não muda o número        │
│     de CICLOS do agente.                                         │
│                                                                  │
│  5 resultados   → 2 ciclos (com tool composta)                  │
│  50 resultados  → 2 ciclos (com tool composta)                  │
│  500 resultados → 2 ciclos (com tool composta)                  │
│                                                                  │
│  O que muda com mais resultados:                                 │
│  • Tamanho da observação (mais tokens no contexto)              │
│  • Tamanho da resposta final (mais tokens de output)            │
│  • Custo por busca (proporcional aos tokens)                    │
│                                                                  │
│  O agente NÃO itera resultado por resultado.                    │
│  A tool retorna TODOS os resultados de uma vez.                 │
│  O LLM formata TODOS de uma vez.                                │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Impacto de Volume nos Tokens (não nos ciclos)

| Resultados | Ciclos | Tokens Observação | Tokens Resposta | Tokens Totais | Custo (GPT-4o) |
|-----------|--------|------------------|-----------------|---------------|----------------|
| 5 | 2 | ~300 | ~500 | ~2.000 | ~$0.01 |
| 10 | 2 | ~600 | ~900 | ~3.000 | ~$0.015 |
| 30 | 2 | ~1.800 | ~2.500 | ~6.500 | ~$0.03 |
| **50** | **2** | **~3.000** | **~4.000** | **~9.250** | **~$0.04** |
| 100 | 2 | ~6.000 | ~7.000 | ~15.500 | ~$0.07 |

### Otimização para Grandes Volumes: Paginação

Para resultados acima de 30, o agente pode paginar:

```
╔═══════════════════════════════════════════════════════════════════╗
║  CICLO 1                                                         ║
║  Action: buscar_fornecedores(substancia="areia lavada", ...)     ║
║  Observe: "50 resultados encontrados. Mostrando 1-10 de 50."    ║
║           (+ 10 resultados detalhados)                           ║
║                                                                  ║
║  Tokens da observação: ~800 (em vez de ~3.000)                  ║
╠═══════════════════════════════════════════════════════════════════╣
║  CICLO 2 (FINAL)                                                 ║
║  Thought: "Vou apresentar os 10 primeiros e informar que há     ║
║            mais 40 disponíveis."                                 ║
║  Action: FINISH                                                  ║
║  "Encontrei 50 fornecedores de areia. Aqui estão os 10 mais     ║
║   próximos: (...) Deseja ver os próximos?"                       ║
╚═══════════════════════════════════════════════════════════════════╝

Se o usuário pedir mais → +1 ciclo para a próxima página.
Custo por página: ~$0.02
```

### Resumo para Decisão

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  Para a busca "areia lavada, 30km, 50 resultados":               │
│                                                                  │
│  • Ciclos ReAct: 2 (com tool composta)                          │
│  • Chamadas ao LLM: 2                                            │
│  • Chamadas ao OpenSearch: 3 (internas à tool)                  │
│  • Tokens totais: ~9.250                                         │
│  • Custo por busca: ~$0.04                                       │
│  • Tempo total: ~2-4 segundos                                    │
│                                                                  │
│  Com paginação (10 por vez): ~$0.02 por página                  │
│                                                                  │
│  100 buscas/dia × 22 dias = $88/mês (sem paginação)             │
│  100 buscas/dia × 22 dias = $44/mês (com paginação)             │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

*Documento gerado em 10/02/2026 com dados reais do cluster OpenSearch de produção.*
