# 🌍 MCP Server Geo — Planejamento Detalhado

## Visão Geral

| Item | Valor |
|------|-------|
| **Porta** | `8012` |
| **Índice principal** | `ibge_municipio_v001` (5.631 docs, 931 MB — polígonos + geo_points de todos os municípios BR) |
| **Serviço externo** | Azure Maps REST APIs (Route Directions, Route Range, Search, Reverse Geocode) |
| **Total de tools custom** | **7** (4 OpenSearch + 3 Azure Maps) |
| **Classificação** | 4 geoespaciais (OpenSearch) + 2 routing (Azure Maps) + 1 geocoding (Azure Maps) |
| **Cenários cobertos por QPT** | ~20% (apenas lookups simples por nome/UF — QPT não faz geo_shape, route, geocode) |

---

## 1. Racional: Por que um MCP Geo dedicado?

### 1.1 Justificativa

Os MCPs Jazidas e Empresas já fazem buscas geoespaciais (`geo_distance`, `geo_shape intersects`), mas **sempre subordinadas** a uma busca de domínio (substância, CNAE, sócio). O MCP Geo cobre cenários **puramente geoespaciais** que nenhum dos outros resolve:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CENÁRIO                                      │ MCP que resolve             │
│  ─────────────────────────────────────────────┼──────────────────────────── │
│  "Jazidas de areia perto de Guarulhos"        │ Jazidas (geo + substância)  │
│  "Empresas de transporte em MG"               │ Empresas (geo + CNAE)       │
│  "Qual município fica nesta coordenada?"      │ 🔴 NENHUM → MCP Geo        │
│  "Mostre o polígono de Campinas"              │ 🔴 NENHUM → MCP Geo        │
│  "Quais municípios ficam em 100km de SP?"     │ 🔴 NENHUM → MCP Geo        │
│  "Rota de caminhão da obra até a jazida"      │ 🔴 NENHUM → MCP Geo        │
│  "Onde fica Rua XV de Novembro, Curitiba?"    │ 🔴 NENHUM → MCP Geo        │
│  "Área alcançável em 2h de caminhão"          │ 🔴 NENHUM → MCP Geo        │
│  "Coordenadas de Ribeirão Preto"              │ 🔴 NENHUM → MCP Geo        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 O que o QPT cobre nativamente

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CENÁRIOS COBERTOS POR QPT + SearchIndexTool (zero código custom)           │
│                                                                             │
│  Pergunta do usuário                      │  QPT gera DSL                   │
│  ─────────────────────────────────────────┼───────────────────────────────  │
│  "Município com código IBGE 3509502"      │  term idMunicipio               │
│  "Municípios de SP"                       │  term siglaUF                   │
│  "Capitais estaduais"                     │  term capitalUF: true           │
│  "Municípios da Amazônia Legal"           │  term amazoniaLegal: true       │
│  "Mesorregião de Campinas"                │  match nomeMesorregiao          │
│                                                                             │
│  Estimativa: ~20% dos cenários geo do dia-a-dia                             │
│  Limitação: QPT NÃO gera geo_shape, geo_distance, external API calls       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.3 Por que não colocar no Jazidas ou Empresas?

| Critério | Embutir no Jazidas/Empresas | MCP Geo dedicado |
|----------|:---------------------------:|:----------------:|
| Single Responsibility | ❌ Mistura domínio + infra geo | ✅ Separação clara |
| Reutilização | ❌ Duplica entre Jazidas/Empresas | ✅ Ambos importam |
| Azure Maps credentials | ❌ Espalha config em 2+ servers | ✅ Centralizado |
| Escalabilidade | ❌ Jazidas/Empresas ficam pesados | ✅ Escala independente |
| LLM routing | ❌ Confunde o agente (tools demais por server) | ✅ Domínio coerente |

---

## 2. Inventário das 7 Custom Tools

### 2.1 Tools OpenSearch (ibge_municipio_v001)

#### 🟢 Tool 1: `buscar_municipio`

| Campo | Valor |
|-------|-------|
| **Descrição** | Busca municípios por nome, código IBGE, ou UF |
| **Fluxo** | 1 passo — query direta em `ibge_municipio_v001` |
| **QPT nativo?** | ⚠️ Parcial — QPT faz match simples, mas não retorna polígono otimizado |
| **Valor do custom** | Retorna dados estruturados com centro, UF, região + opcionalmente polígono |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `nome` | `string` | ❌* | Nome do município (fuzzy match) |
| `codigo_ibge` | `string` | ❌* | Código IBGE 7 dígitos (exact) |
| `uf` | `string` | ❌ | Filtro por UF (ex: "SP") |
| `incluir_poligono` | `bool` | ❌ | Retornar GeoJSON do polígono (default: false) |
| `limite` | `int` | ❌ | Máximo de resultados (default: 10, max: 50) |

> *Pelo menos `nome` ou `codigo_ibge` obrigatório.

**Resposta:**

```json
{
  "sucesso": true,
  "total": 1,
  "municipios": [
    {
      "id_ibge": "3509502",
      "nome": "Campinas",
      "uf": "SP",
      "nome_uf": "São Paulo",
      "regiao": "Sudeste",
      "mesorregiao": "Campinas",
      "microrregiao": "Campinas",
      "capital": false,
      "amazonia_legal": false,
      "centro": { "lat": -22.9064, "lon": -47.0616 },
      "centro_economico": { "lat": -22.9035, "lon": -47.0573 },
      "poligono": null
    }
  ]
}
```

---

#### 🔴 Tool 2: `municipio_por_coordenada`

> **Tool mais importante do MCP Geo** — resolve o caso "onde estou?" via `geo_shape contains`.

| Campo | Valor |
|-------|-------|
| **Descrição** | Identifica em qual município está um ponto geográfico |
| **Fluxo** | 1 passo — `geo_shape` query com `relation: contains` |
| **QPT nativo?** | ❌ QPT não gera geo_shape queries |
| **Use case** | Clique no mapa, resolução de coordenada, contexto de jazida/empresa |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `latitude` | `float` | ✅ | Latitude do ponto |
| `longitude` | `float` | ✅ | Longitude do ponto |
| `incluir_poligono` | `bool` | ❌ | Retornar polígono completo (default: false) |

**Query OpenSearch:**

```json
{
  "query": {
    "geo_shape": {
      "poligono": {
        "shape": {
          "type": "point",
          "coordinates": [-47.06, -22.90]
        },
        "relation": "contains"
      }
    }
  },
  "_source": [
    "idMunicipio", "nome", "siglaUF", "nomeUF",
    "nomeRegiao", "nomeMesorregiao", "nomeMicrorregiao",
    "localizacao", "localizacaoEconomica",
    "amazoniaLegal", "capitalUF"
  ]
}
```

**Resposta:**

```json
{
  "sucesso": true,
  "encontrado": true,
  "municipio": {
    "id_ibge": "3509502",
    "nome": "Campinas",
    "uf": "SP",
    "nome_uf": "São Paulo",
    "regiao": "Sudeste",
    "mesorregiao": "Campinas",
    "microrregiao": "Campinas",
    "capital": false,
    "amazonia_legal": false,
    "centro": { "lat": -22.9064, "lon": -47.0616 }
  }
}
```

**Performance:** ~5-10ms (geo_shape contains em índice de 5.6K docs)

---

#### 🟡 Tool 3: `obter_poligono`

| Campo | Valor |
|-------|-------|
| **Descrição** | Retorna o polígono GeoJSON completo de um município |
| **Fluxo** | 1 passo — term query por `idMunicipio` |
| **QPT nativo?** | ⚠️ QPT poderia buscar, mas não formata como GeoJSON Feature |
| **Use case** | Overlay no mapa, cálculo de intersecção, contexto visual |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `codigo_ibge` | `string` | ❌* | Código IBGE 7 dígitos |
| `nome` | `string` | ❌* | Nome do município |
| `uf` | `string` | ❌ | UF (obrigatório se buscar por nome, para desambiguar) |

> *Pelo menos `codigo_ibge` ou `nome` obrigatório.

**Resposta:**

```json
{
  "sucesso": true,
  "municipio": "Campinas",
  "uf": "SP",
  "id_ibge": "3509502",
  "feature": {
    "type": "Feature",
    "geometry": {
      "type": "Polygon",
      "coordinates": [[[...], [...]]]
    },
    "properties": {
      "camada": "municipio",
      "nome": "Campinas",
      "uf": "SP",
      "id_ibge": "3509502",
      "centro": { "lat": -22.9064, "lon": -47.0616 }
    }
  }
}
```

---

#### 🟡 Tool 4: `municipios_em_raio`

| Campo | Valor |
|-------|-------|
| **Descrição** | Lista municípios cujo centro geográfico está dentro de um raio |
| **Fluxo** | 1 passo — `geo_distance` em `localizacao` |
| **QPT nativo?** | ⚠️ QPT gera geo_distance mas sem formatação + sorting por distância |
| **Use case** | "Quais municípios ficam perto da obra?", contexto regional |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `latitude` | `float` | ✅ | Centro da busca |
| `longitude` | `float` | ✅ | Centro da busca |
| `raio_km` | `float` | ✅ | Raio em km (default: 50, max: 500) |
| `uf` | `string` | ❌ | Filtro opcional por UF |
| `incluir_poligonos` | `bool` | ❌ | Retornar polígonos (default: false — pesado!) |
| `limite` | `int` | ❌ | Máximo de resultados (default: 20, max: 100) |

**Query OpenSearch:**

```json
{
  "size": 20,
  "query": {
    "bool": {
      "filter": [
        {
          "geo_distance": {
            "distance": "50km",
            "localizacao": { "lat": -22.90, "lon": -47.06 }
          }
        }
      ]
    }
  },
  "_source": ["idMunicipio", "nome", "siglaUF", "nomeUF", "localizacao", "capitalUF"],
  "sort": [
    {
      "_geo_distance": {
        "localizacao": { "lat": -22.90, "lon": -47.06 },
        "order": "asc",
        "unit": "km"
      }
    }
  ]
}
```

**Resposta:**

```json
{
  "sucesso": true,
  "centro": { "lat": -22.90, "lon": -47.06 },
  "raio_km": 50,
  "total": 15,
  "municipios": [
    {
      "id_ibge": "3509502",
      "nome": "Campinas",
      "uf": "SP",
      "distancia_km": 0.0,
      "centro": { "lat": -22.9064, "lon": -47.0616 },
      "capital": false
    },
    {
      "id_ibge": "3556701",
      "nome": "Valinhos",
      "uf": "SP",
      "distancia_km": 12.3,
      "centro": { "lat": -22.9722, "lon": -46.9960 },
      "capital": false
    }
  ]
}
```

---

### 2.2 Tools Azure Maps (Serviço Externo)

#### 🔴 Tool 5: `calcular_rota`

> **Tool de maior valor de negócio** — rota real de caminhão entre obra e jazida/fornecedor.

| Campo | Valor |
|-------|-------|
| **Descrição** | Calcula rota real entre dois pontos usando Azure Maps Route Directions |
| **Fluxo** | 1 passo — HTTP call para Azure Maps REST API |
| **QPT nativo?** | ❌ Impossível — serviço externo |
| **Use case** | "Qual a rota da obra até a jazida?", logística de transporte mineral |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `origem_lat` | `float` | ✅ | Latitude da origem |
| `origem_lon` | `float` | ✅ | Longitude da origem |
| `destino_lat` | `float` | ✅ | Latitude do destino |
| `destino_lon` | `float` | ✅ | Longitude do destino |
| `modo` | `string` | ❌ | `"truck"` (default) ou `"car"` |
| `peso_veiculo_kg` | `int` | ❌ | Peso do veículo em kg (default: 40000 — carreta de minério) |
| `evitar_pedagios` | `bool` | ❌ | Evitar pedágios (default: false) |

**API Azure Maps:**

```
POST https://atlas.microsoft.com/route/directions/json
  ?api-version=1.0
  &subscription-key={key}
  &travelMode=truck
  &vehicleWeight=40000
  &vehicleHeight=4.5
  &vehicleWidth=2.6
  &vehicleLength=18.75
  &vehicleAxleWeight=12000
  &routeRepresentation=polyline
  &query={origemLat},{origemLon}:{destLat},{destLon}
```

**Resposta:**

```json
{
  "sucesso": true,
  "rota": {
    "distancia_km": 47.3,
    "duracao_min": 58,
    "atraso_trafego_min": 7,
    "modo": "truck",
    "resumo": "47.3 km • ~58 min via BR-381",
    "polyline": [
      { "lat": -23.55, "lon": -46.63 },
      { "lat": -23.54, "lon": -46.65 },
      "..."
    ],
    "origem": { "lat": -23.55, "lon": -46.63 },
    "destino": { "lat": -23.37, "lon": -46.76 }
  }
}
```

**Performance:** ~200-500ms (round-trip Azure Maps)

---

#### 🟡 Tool 6: `calcular_isocrona`

| Campo | Valor |
|-------|-------|
| **Descrição** | Retorna polígono de área alcançável em tempo/distância (Route Range) |
| **Fluxo** | 1 passo — HTTP call para Azure Maps Route Range API |
| **QPT nativo?** | ❌ Impossível — serviço externo |
| **Use case** | "Quais jazidas estão a 2h de caminhão?", análise de cobertura logística |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `latitude` | `float` | ✅ | Centro da isócrona |
| `longitude` | `float` | ✅ | Centro da isócrona |
| `tempo_minutos` | `int` | ❌* | Tempo máximo de viagem |
| `distancia_km` | `float` | ❌* | Distância máxima |
| `modo` | `string` | ❌ | `"truck"` (default) ou `"car"` |
| `peso_veiculo_kg` | `int` | ❌ | Peso do veículo (default: 40000) |

> *Pelo menos `tempo_minutos` ou `distancia_km` obrigatório.

**API Azure Maps:**

```
POST https://atlas.microsoft.com/route/range/json
  ?api-version=1.0
  &subscription-key={key}
  &travelMode=truck
  &timeBudgetInSec=7200
  &query={lat},{lon}
```

**Resposta:**

```json
{
  "sucesso": true,
  "centro": { "lat": -23.55, "lon": -46.63 },
  "criterio": "tempo",
  "valor": 120,
  "unidade": "minutos",
  "modo": "truck",
  "feature": {
    "type": "Feature",
    "geometry": {
      "type": "Polygon",
      "coordinates": [[[...], [...]]]
    },
    "properties": {
      "camada": "isocrona",
      "tempo_min": 120,
      "modo": "truck",
      "centro": { "lat": -23.55, "lon": -46.63 }
    }
  }
}
```

---

#### 🟢 Tool 7: `geocodificar`

| Campo | Valor |
|-------|-------|
| **Descrição** | Converte endereço/nome de local em coordenadas (e vice-versa) |
| **Fluxo** | 1 passo — HTTP call para Azure Maps Search ou Search Reverse |
| **QPT nativo?** | ❌ Impossível — serviço externo |
| **Use case** | "Onde fica a Rua XV em Curitiba?", clique no mapa → endereço |

**Parâmetros:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|:-----------:|-----------|
| `endereco` | `string` | ❌* | Endereço ou nome do local (forward geocoding) |
| `latitude` | `float` | ❌* | Latitude para reverse geocoding |
| `longitude` | `float` | ❌* | Longitude para reverse geocoding |
| `limite` | `int` | ❌ | Máximo de resultados (default: 5) |

> *`endereco` para forward geocoding OU `latitude`+`longitude` para reverse.

**API Azure Maps (Forward):**

```
GET https://atlas.microsoft.com/search/fuzzy/json
  ?api-version=1.0
  &subscription-key={key}
  &query=Rua XV de Novembro, Curitiba
  &countrySet=BR
  &language=pt-BR
  &limit=5
```

**API Azure Maps (Reverse):**

```
GET https://atlas.microsoft.com/search/address/reverse/json
  ?api-version=1.0
  &subscription-key={key}
  &query={lat},{lon}
```

**Resposta (forward):**

```json
{
  "sucesso": true,
  "tipo": "forward",
  "total": 3,
  "resultados": [
    {
      "endereco": "Rua XV de Novembro, Centro, Curitiba, PR",
      "coordenadas": { "lat": -25.4284, "lon": -49.2733 },
      "tipo": "Street",
      "confianca": "High",
      "municipio": "Curitiba",
      "uf": "PR"
    }
  ]
}
```

**Resposta (reverse):**

```json
{
  "sucesso": true,
  "tipo": "reverse",
  "endereco": "Avenida Paulista, 1578, Bela Vista, São Paulo, SP",
  "coordenadas": { "lat": -23.5629, "lon": -46.6544 },
  "municipio": "São Paulo",
  "uf": "SP",
  "cep": "01310-200"
}
```

---

## 3. Mapa de Cobertura: QPT Nativo vs Custom Tools

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    COBERTURA DE CENÁRIOS GEO                                 │
│                                                                             │
│  ┌──────────────────────────────────────────────────┐                       │
│  │  QPT NATIVO (~20%)                               │                       │
│  │                                                  │                       │
│  │  ✅ Buscar município por código IBGE             │                       │
│  │  ✅ Listar municípios por UF                     │                       │
│  │  ✅ Filtrar capitais / Amazônia Legal             │                       │
│  │  ✅ Buscar por mesorregião/microrregião          │                       │
│  └──────────────────────────────────────────────────┘                       │
│                                                                             │
│  ┌──────────────────────────────────────────────────┐                       │
│  │  TOOLS CUSTOM (~80%)                             │                       │
│  │                                                  │                       │
│  │  🔴 Município por coordenada (geo_shape)         │  ← Tool 2            │
│  │  🔴 Rota de caminhão (Azure Maps)                │  ← Tool 5            │
│  │  🟡 Buscar município (fuzzy + polígono)          │  ← Tool 1            │
│  │  🟡 Polígono de município (GeoJSON Feature)      │  ← Tool 3            │
│  │  🟡 Municípios em raio (geo_distance + sort)     │  ← Tool 4            │
│  │  🟡 Isócrona (Azure Maps Route Range)            │  ← Tool 6            │
│  │  🟢 Geocodificar (Azure Maps Search)             │  ← Tool 7            │
│  └──────────────────────────────────────────────────┘                       │
│                                                                             │
│  🔴 = QPT impossível   🟡 = QPT parcial   🟢 = QPT cobre parcialmente     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Índice Utilizado e Campos Relevantes

### 4.1 `ibge_municipio_v001` — 5.631 docs, 931 MB

| Campo | Tipo | Uso nas Tools |
|-------|------|---------------|
| `idMunicipio` | keyword | Tool 1, 3: busca por código IBGE |
| `idMunicipio6` | keyword | Compatibilidade IBGE 6 dígitos |
| `idMunicipioANM` | keyword | Ligação com anm_v002.municipios |
| `idMunicipioRFB` | keyword | Ligação com cnpj_v002.municipio |
| `idMunicipioBCB` | keyword | Referência BCB |
| `idMunicipioTSE` | keyword | Referência TSE |
| `nome` | text (pt_brazilian) | Tool 1, 3: busca por nome (fuzzy) |
| `nome.keyword` | keyword (lower_ascii) | Tool 1, 3: match exato (normalizado) |
| `siglaUF` | keyword (lower_ascii) | Tool 1, 4: filtro por UF |
| `nomeUF` | text (pt_brazilian) | Exibição |
| `nomeRegiao` | text | Contexto regional |
| `nomeMesorregiao` | text | Contexto regional |
| `nomeMicrorregiao` | text | Contexto regional |
| `idMesorregiao` | integer | Referência IBGE |
| `idMicrorregiao` | integer | Referência IBGE |
| `capitalUF` | boolean | Filtro de capitais |
| `amazoniaLegal` | boolean | Filtro Amazônia Legal |
| `localizacao` | **geo_point** | Tool 2, 4: centro geográfico |
| `localizacaoEconomica` | **geo_point** | Contexto econômico |
| `poligono` | **geo_shape** | Tool 2: geo_shape contains / Tool 3: GeoJSON export |

### 4.2 Azure Maps REST APIs

| API | Endpoint | Uso nas Tools |
|-----|----------|---------------|
| Route Directions | `POST /route/directions/json` | Tool 5: rota ponto-a-ponto (truck/car) |
| Route Range | `POST /route/range/json` | Tool 6: isócrona (polígono de alcance) |
| Search Fuzzy | `GET /search/fuzzy/json` | Tool 7: forward geocoding |
| Search Reverse | `GET /search/address/reverse/json` | Tool 7: reverse geocoding |

**Autenticação:** Azure Maps subscription key (mesmo tenant Azure do cluster OpenSearch).

---

## 5. Cache Redis — Estratégia por Tool

| Tool | Cache Key Pattern | TTL | O que é cacheado |
|------|-------------------|-----|-------------------|
| `buscar_municipio` | `geo:mun:{hash_params}` | 24h | Lista de municípios |
| `municipio_por_coordenada` | `geo:coord:{lat6}:{lon6}` | 24h | Município identificado |
| `obter_poligono` | `geo:poly:{id_ibge}` | 7d | GeoJSON Feature completo |
| `municipios_em_raio` | `geo:raio:{hash_params}` | 1h | Lista de municípios |
| `calcular_rota` | `geo:rota:{hash_params}` | 1h | Rota + polyline |
| `calcular_isocrona` | `geo:iso:{hash_params}` | 1h | Polígono isócrona |
| `geocodificar` | `geo:gc:{hash_query}` | 24h | Resultados geocoding |

**Lógica de cache:**

```
┌─────────────────────────────────────────────────────────────────────┐
│  Tools OpenSearch (1-4):                                             │
│  - Cache agressivo (24h-7d) — dados IBGE mudam ~1x/ano              │
│  - Polígonos cacheados 7d (931 MB no índice, ~2-200 KB cada)        │
│  - Coordenada → município cacheado por lat/lon arredondado (6 dec)  │
│                                                                     │
│  Tools Azure Maps (5-7):                                             │
│  - Cache moderado (1h) — rotas mudam com tráfego                    │
│  - Geocoding cacheado 24h — endereços não mudam frequentemente      │
│  - Isócronas cacheadas 1h — dependem de condições de tráfego        │
│                                                                     │
│  Motivação: Azure Maps cobra por request → cache reduz custo real   │
│  Estimativa de economia: ~60-70% de requests Azure Maps com cache   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Estrutura de Diretórios

```
mcp_servers/
├── common/                          # ✅ JÁ IMPLEMENTADO (shared)
│   ├── config.py                    # MCPSettings (+ azure_maps_key, cache_geo_ttl)
│   ├── formatters.py                # format_cnpj, extract_municipio_nome, etc.
│   ├── opensearch_client.py         # OpenSearchService (async)
│   ├── redis_cache.py               # RedisCache (graceful degradation)
│   ├── embeddings.py                # EmbeddingService (Azure OpenAI)
│   └── schemas.py                   # GeoPoint, PaginationParams, ToolResponse
│
├── jazidas/                         # ✅ JÁ IMPLEMENTADO
│   └── queries/
│       └── municipio.py             # 🔄 REUTILIZAR (_municipio_to_geojson_feature)
│
├── empresas/                        # ✅ JÁ IMPLEMENTADO
│
└── geo/                             # 🆕 A IMPLEMENTAR
    ├── __init__.py
    ├── server.py                    # MCP Server bootstrap (Streamable HTTP :8012/mcp)
    ├── tools.py                     # 7 tools com decoradores @mcp.tool()
    ├── queries/
    │   ├── __init__.py
    │   ├── municipios.py            # Queries ibge_municipio_v001 (Tools 1-4)
    │   └── formatters.py            # Município → dict/GeoJSON formatters
    ├── services/
    │   ├── __init__.py
    │   ├── azure_maps.py            # Client Azure Maps REST (Route, Search)
    │   └── route.py                 # Orquestrador de rotas (Tool 5-6)
    ├── schemas.py                   # Schemas: MunicipioResult, RotaResult, etc.
    └── cache.py                     # Helpers de cache (patterns, TTLs)
```

**Reutilização de módulos existentes:**

| Módulo | O que reutiliza | Como |
|--------|-----------------|------|
| `common/opensearch_client.py` | Conexão OpenSearch, search, msearch | Direto (shared) |
| `common/redis_cache.py` | Cache Redis | Direto (shared) |
| `common/config.py` | MCPSettings (+ novos campos Azure Maps) | Direto (shared) |
| `common/formatters.py` | `extract_municipio_nome` | Import direto |
| `jazidas/queries/municipio.py` | `_municipio_to_geojson_feature`, `MUNICIPIO_SOURCE_FIELDS` | Import direto |

---

## 7. Schemas Pydantic (geo/schemas.py)

```python
from pydantic import BaseModel, Field


class MunicipioResult(BaseModel):
    """Resultado de busca de município."""
    id_ibge: str = Field(description="Código IBGE 7 dígitos")
    nome: str = Field(description="Nome do município")
    uf: str = Field(description="Sigla UF")
    nome_uf: str | None = Field(default=None, description="Nome do estado")
    regiao: str | None = Field(default=None, description="Região")
    mesorregiao: str | None = Field(default=None, description="Mesorregião IBGE")
    microrregiao: str | None = Field(default=None, description="Microrregião IBGE")
    capital: bool = Field(default=False, description="É capital estadual")
    amazonia_legal: bool = Field(default=False, description="Na Amazônia Legal")
    centro: dict | None = Field(default=None, description="Centro geográfico {lat, lon}")
    centro_economico: dict | None = Field(default=None, description="Centro econômico {lat, lon}")
    distancia_km: float | None = Field(default=None, description="Distância do centro de busca")
    poligono: dict | None = Field(default=None, description="GeoJSON Feature (se solicitado)")


class RotaResult(BaseModel):
    """Resultado de cálculo de rota Azure Maps."""
    distancia_km: float = Field(description="Distância total em km")
    duracao_min: float = Field(description="Duração estimada em minutos")
    atraso_trafego_min: float = Field(default=0, description="Atraso por tráfego")
    modo: str = Field(description="Modo de viagem (truck/car)")
    resumo: str = Field(description="Resumo legível: '47.3 km • ~58 min'")
    polyline: list[dict] = Field(description="Pontos da polyline [{lat, lon}, ...]")
    origem: dict = Field(description="{lat, lon}")
    destino: dict = Field(description="{lat, lon}")


class IsocronaResult(BaseModel):
    """Resultado de isócrona Azure Maps."""
    centro: dict = Field(description="{lat, lon}")
    criterio: str = Field(description="'tempo' ou 'distancia'")
    valor: float = Field(description="Valor do critério (minutos ou km)")
    modo: str = Field(description="Modo de viagem")
    feature: dict = Field(description="GeoJSON Feature com polígono da isócrona")


class GeocodingResult(BaseModel):
    """Resultado de geocoding."""
    endereco: str = Field(description="Endereço completo")
    coordenadas: dict = Field(description="{lat, lon}")
    tipo: str = Field(description="Tipo (Street, Address, POI)")
    confianca: str | None = Field(default=None, description="Nível de confiança")
    municipio: str | None = Field(default=None, description="Município")
    uf: str | None = Field(default=None, description="UF")
    cep: str | None = Field(default=None, description="CEP")
```

---

## 8. Comparação: 3 MCPs do MineralRadar

| Dimensão | MCP Jazidas | MCP Empresas | MCP Geo |
|----------|-------------|--------------|---------|
| **Port** | 8010 | 8011 | 8012 |
| **Índice principal** | `anm_v002` (956K, 14 GB) | `cnpj_v002` (~69M) | `ibge_municipio_v001` (5.6K, 931 MB) |
| **Volume** | Médio | Massivo | Pequeno (mas pesado — geo_shapes) |
| **Serviço externo** | — | — | **Azure Maps** (Route, Search) |
| **Catálogo semântico** | `anm_substancia_v001` | `rfb_cnae_v001` | — |
| **Resolver k-NN** | SubstanciaResolver | CnaeResolver | — (sem busca semântica) |
| **Campos nested** | 9 | 2 | 0 |
| **Tools custom** | 5 | 3 | **7** |
| **Cross-index** | 3 | 2 | 0 |
| **Geoespaciais** | 1 (nested geo_shape) | 0 | **4** (geo_shape, geo_distance) |
| **Externas** | 0 | 0 | **3** (Azure Maps) |
| **Cache agressivo** | 1h (search) | 1h (search) / 24h (detalhe) | **7d polígono** / 1h rotas |
| **Reutilização** | — | common/, municipio.py | common/, municipio.py formatters |
| **Estimativa** | ~4.5 dias | ~2.5 dias | **~3 dias** |

---

## 9. Prioridade de Implementação

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    ORDEM DE IMPLEMENTAÇÃO                                │
│                                                                         │
│  FASE 1 — Fundação + OpenSearch Tools (Dia 1, ~5h) ────────────────── │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  ① server.py            → MCP Server bootstrap (Streamable HTTP) │   │
│  │  ② schemas.py           → Modelos Pydantic (4 schemas)           │   │
│  │  ③ queries/municipios.py → Queries ibge_municipio_v001           │   │
│  │  ④ queries/formatters.py → Município → dict/GeoJSON              │   │
│  │  ⑤ cache.py             → Helpers de cache geo (patterns, TTLs)  │   │
│  │  ⑥ config.py            → + azure_maps_key, cache_geo_ttl        │   │
│  │  ⑦ Tools 1-4            → buscar_municipio,                      │   │
│  │                            municipio_por_coordenada,              │   │
│  │                            obter_poligono,                        │   │
│  │                            municipios_em_raio                     │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  FASE 2 — Azure Maps Client + Routing Tools (Dia 2, ~5h) ──────────── │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  ⑧ services/azure_maps.py → Client HTTP para Azure Maps REST    │   │
│  │  ⑨ services/route.py      → Orquestrador de rotas               │   │
│  │  ⑩ Tool 5: calcular_rota  → Route Directions (truck/car)        │   │
│  │  ⑪ Tool 6: calcular_isocrona → Route Range (polígono alcance)   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  FASE 3 — Geocoding + Validação (Dia 3, ~4h) ──────────────────────── │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  ⑫ Tool 7: geocodificar   → Search Fuzzy + Reverse              │   │
│  │  ⑬ Testes end-to-end      → Todas as 7 tools                    │   │
│  │  ⑭ MCP Inspector          → Validação interativa                │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ESTIMATIVA TOTAL: ~3 dias (~14h efetivas)                              │
│  (7 tools, mas nenhuma cross-index — complexidade menor)               │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 10. Decisões Técnicas

### 10.1 Por que Azure Maps e não OSRM/Nominatim?

| Critério | Azure Maps | OSRM + Nominatim |
|----------|-----------|------------------|
| **Truck routing** | ✅ Nativo (peso, dimensões, carga perigosa) | ❌ Não suporta |
| **Infra** | ✅ Managed (REST API) | ❌ Self-hosted (50GB+ RAM para América do Sul) |
| **Auth** | ✅ Subscription key, mesmo tenant Azure | N/A |
| **Custo** | ~$8/mês estimado (ver seção 10.5) | $0 (mas custo de infra) |
| **Geocoding Brasil** | ✅ Bom (dados TomTom) | ⚠️ Variável (dados OSM) |
| **SLA** | 99.9% (Azure) | Depende da infra |

**Decisão:** Azure Maps para routing + geocoding. Se no futuro o custo escalar, OSRM pode ser adicionado como fallback para rotas simples (car), mantendo Azure Maps para truck routing (que não tem alternativa gratuita).

### 10.2 Por que não chamar Azure Maps diretamente do frontend?

| Critério | Frontend direto | Via MCP Geo (backend) |
|----------|:--------------:|:---------------------:|
| **Subscription key** | Exposta no client | ✅ Protegida no backend |
| **Cache** | ❌ Sem cache server-side | ✅ Redis (~60-70% saving) |
| **Rate limiting** | ❌ Difícil no client | ✅ Controlado no backend |
| **LLM acesso** | ❌ Agente não consegue usar | ✅ Agente chama como tool |
| **Logs/métricas** | ❌ Sem visibilidade | ✅ Structured logging |

O **frontend** continua fazendo chamadas Azure Maps diretas para UX interativa (drag route, real-time geocode while typing), mas o **agente** usa sempre o MCP Geo — que cacheia e protege a key.

### 10.3 Por que separar `obter_poligono` de `buscar_municipio`?

| Critério | Tool unificada | 2 Tools separadas |
|----------|:--------------:|:-----------------:|
| **Payload** | Polígono sempre incluído (~2-200KB) | ✅ Polígono só quando pedido |
| **LLM token usage** | 🔴 Desperdiça tokens com GeoJSON | ✅ Retorna só metadados por default |
| **Cache** | Cache único (pesado) | ✅ Polígono cacheado 7d separado |
| **UX do agente** | "Buscar município" devolve polígono desnecessário | ✅ Agente pede polígono só quando precisa |

`buscar_municipio` retorna metadados leves. Quando o frontend precisa desenhar o polígono no mapa, o agente chama `obter_poligono` separadamente — ou o frontend busca direto via API se já tem o `id_ibge`.

### 10.4 Reutilização do módulo `jazidas/queries/municipio.py`

O MCP Jazidas já implementou:
- `_municipio_to_geojson_feature()` — converte hit em GeoJSON Feature
- `build_municipios_msearch()` — busca precisa por nome+UF
- `fetch_municipios_precise()` — orquestrador com msearch
- `MUNICIPIO_SOURCE_FIELDS` — lista de campos do índice

O MCP Geo **importa diretamente** esses utilitários para evitar duplicação. As Tools 1-4 adicionam lógica nova (geo_shape contains, geo_distance sort, fuzzy search) mas reutilizam os formatters.

### 10.5 Estimativa de Custo Azure Maps

| Serviço | Preço (S1 tier) | Volume mensal estimado | Custo/mês |
|---------|----------------|----------------------|-----------|
| Route Directions | $0.50 / 1K requests | ~5K rotas | ~$2.50 |
| Route Range | $0.50 / 1K requests | ~1K isócronas | ~$0.50 |
| Search (geocoding) | $0.50 / 1K requests | ~10K buscas | ~$5.00 |
| **Total estimado** | | | **~$8.00/mês** |
| **Com cache Redis (~65% saving)** | | | **~$3.00/mês** |

*Comparativo Google Maps: Route Directions ($5/1K) + Geocoding ($5/1K) = ~$80/mês para mesmo volume.*

---

## 11. Configuração Necessária (config.py)

Novos campos a adicionar no `MCPSettings`:

```python
# ==================== Azure Maps ====================
azure_maps_subscription_key: str = Field(default="")
azure_maps_base_url: str = Field(default="https://atlas.microsoft.com")

# ==================== Truck Defaults (Azure Maps Route) ====================
truck_weight_kg: int = Field(default=40000)       # 40 toneladas (carreta minério)
truck_height_m: float = Field(default=4.5)
truck_width_m: float = Field(default=2.6)
truck_length_m: float = Field(default=18.75)       # Carreta
truck_axle_weight_kg: int = Field(default=12000)

# ==================== Cache TTL Geo ====================
cache_geo_municipio_ttl: int = Field(default=86400)  # 24h
cache_geo_poligono_ttl: int = Field(default=604800)   # 7d
cache_geo_rota_ttl: int = Field(default=3600)          # 1h
cache_geo_geocode_ttl: int = Field(default=86400)      # 24h
```

---

## 12. Fluxo por Tool — Diagrama Completo

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MCP GEO — FLUXO COMPLETO                        │
│                                                                         │
│  Tool 1: buscar_municipio                                               │
│  ─────────────────────────                                              │
│  Input: nome="Campinas", uf="SP"                                        │
│  → Redis GET geo:mun:{hash} → miss                                     │
│  → OpenSearch: bool filter nome.keyword + siglaUF                       │
│  → Redis SET + retorno                                                  │
│  Tempo: ~10ms (OS) + ~5ms (Redis)                                       │
│                                                                         │
│  Tool 2: municipio_por_coordenada                                       │
│  ────────────────────────────────                                       │
│  Input: lat=-22.90, lon=-47.06                                          │
│  → Redis GET geo:coord:-22.906:-47.061 → miss                          │
│  → OpenSearch: geo_shape.contains(point)                                │
│  → Redis SET + retorno                                                  │
│  Tempo: ~10ms (OS) + ~5ms (Redis)                                       │
│                                                                         │
│  Tool 3: obter_poligono                                                 │
│  ──────────────────────                                                 │
│  Input: codigo_ibge="3509502"                                           │
│  → Redis GET geo:poly:3509502 → miss                                   │
│  → OpenSearch: term idMunicipio (includes poligono)                     │
│  → _municipio_to_geojson_feature() (reutiliza jazidas)                  │
│  → Redis SET (TTL 7d) + retorno                                        │
│  Tempo: ~15ms (OS, polígono ~50KB) + ~5ms (Redis)                       │
│                                                                         │
│  Tool 4: municipios_em_raio                                             │
│  ──────────────────────────                                             │
│  Input: lat=-22.90, lon=-47.06, raio_km=50                              │
│  → Redis GET geo:raio:{hash} → miss                                    │
│  → OpenSearch: geo_distance + sort _geo_distance                        │
│  → Format + Redis SET + retorno                                         │
│  Tempo: ~20ms (OS) + ~5ms (Redis)                                       │
│                                                                         │
│  Tool 5: calcular_rota                                                  │
│  ─────────────────────                                                  │
│  Input: origem(-23.55,-46.63), destino(-23.37,-46.76), modo="truck"     │
│  → Redis GET geo:rota:{hash} → miss                                    │
│  → Azure Maps POST /route/directions (truck params)                     │
│  → Parse response → Redis SET (TTL 1h)                                  │
│  Tempo: ~300ms (Azure Maps) + ~5ms (Redis)                              │
│                                                                         │
│  Tool 6: calcular_isocrona                                              │
│  ──────────────────────────                                             │
│  Input: lat=-23.55, lon=-46.63, tempo_minutos=120                       │
│  → Redis GET geo:iso:{hash} → miss                                     │
│  → Azure Maps POST /route/range (truck params)                          │
│  → Parse boundary → GeoJSON Feature → Redis SET (TTL 1h)               │
│  Tempo: ~400ms (Azure Maps) + ~5ms (Redis)                              │
│                                                                         │
│  Tool 7: geocodificar                                                   │
│  ────────────────────                                                   │
│  Input: endereco="Rua XV de Novembro, Curitiba"                         │
│  → Redis GET geo:gc:{hash} → miss                                      │
│  → Azure Maps GET /search/fuzzy (countrySet=BR, language=pt-BR)         │
│  → Parse results → Redis SET (TTL 24h)                                  │
│  Tempo: ~200ms (Azure Maps) + ~5ms (Redis)                              │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 13. Mapa Completo: Tool → MCP Server → Conexão (Geo)

| Tool | Tipo | MCP Server | Conexão | Índice/Serviço | Cache |
|------|------|------------|---------|----------------|-------|
| `ListIndexTool` | Nativa | OpenSearch MCP | Streamable HTTP | todos | ❌ |
| `QueryPlanningTool` | Nativa | OpenSearch MCP | Streamable HTTP | todos | ❌ |
| `PPLTool` | Nativa | OpenSearch MCP | Streamable HTTP | todos | ❌ |
| `SearchIndexTool` | Nativa | OpenSearch MCP | Streamable HTTP | todos | ❌ |
| `buscar_municipio` | **Custom** | **MCP Geo (:8012)** | **Streamable HTTP** | `ibge_municipio_v001` | ✅ 24h |
| `municipio_por_coordenada` | **Custom** | **MCP Geo (:8012)** | **Streamable HTTP** | `ibge_municipio_v001` | ✅ 24h |
| `obter_poligono` | **Custom** | **MCP Geo (:8012)** | **Streamable HTTP** | `ibge_municipio_v001` | ✅ 7d |
| `municipios_em_raio` | **Custom** | **MCP Geo (:8012)** | **Streamable HTTP** | `ibge_municipio_v001` | ✅ 1h |
| `calcular_rota` | **Custom** | **MCP Geo (:8012)** | **Streamable HTTP** | Azure Maps Route | ✅ 1h |
| `calcular_isocrona` | **Custom** | **MCP Geo (:8012)** | **Streamable HTTP** | Azure Maps Route Range | ✅ 1h |
| `geocodificar` | **Custom** | **MCP Geo (:8012)** | **Streamable HTTP** | Azure Maps Search | ✅ 24h |

> **Resumo**: 7 tools nativas (via MCP OpenSearch nativo) + 7 tools customizadas (via MCP Geo Python) = **14 tools** disponíveis para o agente no domínio Geo.

---

## 14. Visão Global: Todos os MCPs do MineralRadar

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SUPPLYRADAR 2.0 — MAPA COMPLETO DE TOOLS                  │
│                                                                             │
│  MCP OpenSearch Nativo (ML Commons)                                         │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  ListIndexTool • QueryPlanningTool • PPLTool • SearchIndexTool        │  │
│  │  (7 tools nativas — zero código custom, funciona em todos os índices) │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  MCP Jazidas (:8010)          MCP Empresas (:8011)      MCP Geo (:8012)    │
│  ┌────────────────────┐      ┌────────────────────┐     ┌─────────────────┐│
│  │  buscar_fornecedores│      │  buscar_empresas   │     │ buscar_municipio││
│  │  buscar_jazidas     │      │  detalhes_empresa  │     │ mun_por_coordena││
│  │  detalhes_processo  │      │  buscar_por_socio  │     │ obter_poligono  ││
│  │  jazidas_por_poligono│     │                    │     │ mun_em_raio     ││
│  │  verificar_vigencia │      │                    │     │ calcular_rota   ││
│  │                     │      │                    │     │ calcular_isocron││
│  │  (5 tools custom)  │      │  (3 tools custom)  │     │ geocodificar    ││
│  └─────────┬──────────┘      └─────────┬──────────┘     │ (7 tools custom)││
│            │                           │                └────────┬────────┘│
│            │                           │                         │         │
│  ┌─────────┴───────────────────────────┴─────────────────────────┘         │
│  │                                                                         │
│  │  OpenSearch Cluster                        Azure Maps REST              │
│  │  ┌──────────────────────────────────┐     ┌──────────────────┐          │
│  │  │  anm_v002        (956K docs)     │     │  Route Directions│          │
│  │  │  cnpj_v002       (~69M docs)     │     │  Route Range     │          │
│  │  │  ibge_municipio  (5.6K docs)     │     │  Search Fuzzy    │          │
│  │  │  rfb_cnae_v001   (2.4K docs)     │     │  Search Reverse  │          │
│  │  │  anm_substancia  (862 docs)      │     └──────────────────┘          │
│  │  └──────────────────────────────────┘                                   │
│  │                                                                         │
│  │  Redis Cache                                                            │
│  │  ┌──────────────────────────────────┐                                   │
│  │  │  search:*, detalhe:*, socio:*    │  ← Jazidas + Empresas            │
│  │  │  geo:mun:*, geo:poly:*, geo:rota:* │  ← Geo                        │
│  │  │  emb:*, sub:*                    │  ← Embeddings + Substâncias      │
│  │  └──────────────────────────────────┘                                   │
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                             │
│  TOTAL: 7 nativas + 15 custom = 22 tools disponíveis para o LangGraph     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

*Documento criado em 24/02/2026 — MCP Server Geo (port 8012): 7 tools custom (4 OpenSearch + 3 Azure Maps).*
