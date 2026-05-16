# Especificação: Catálogo de Portos Brasileiros — Fase 2 (OpenSearch)

> **Data:** 05/05/2026  
> **Status:** Rascunho — Fase 1 (CSV in-memory) já implementada; Fase 2 planejada  
> **Pré-requisito:** Fase 1 (`backend/data/portos_brasil.csv` + `mcp_servers/geo/services/portos_registry.py` + `geo__buscar_porto` + guardrail no `app/langgraph/tools.py`) já em produção.

---

## 1. Motivação

A Fase 1 entregou o caso central: rotas para os **35 portos organizados públicos** ficam corretas porque as coordenadas saem de um catálogo curado em vez do fuzzy do Azure Maps. Mas o catálogo in-memory atinge um teto rápido:

| Limitação Fase 1 | O que falta |
|---|---|
| Só 35 portos organizados — não tem TUPs nem ETCs | Brasil tem ~220 TUPs + ETCs (Vale, Petrobras, CSN, …) |
| Lookup pontual (1 porto = 1 ponto) | Não dá para responder "qual porto contém esta coordenada?" |
| Sem busca por geometria | Não dá para "fornecedores DENTRO do Porto de Santos" |
| Sem busca semântica | "porto graneleiro do nordeste" → precisaria k-NN sobre cargas |
| Sem versionamento de polígonos | A área legal do porto muda (portarias trimestrais MTransp) — hoje, ignoramos |
| Sem TUPs privados | Caso comum: cliente pede rota até "Ponta da Madeira" (Vale, MA) |

A Fase 2 resolve isso usando o mesmo padrão que já temos para `mr_municipios_v001`: **um índice OpenSearch (`mr_portos_v001`) com `geo_shape` polygon + `geo_point` centroide + campos textuais com analyzer PT-BR + opcional embedding k-NN** sobre o nome/cargas para busca semântica. O mapeamento oficial vive em `backend/scripts/setup_indices.py` (`MR_PORTOS`).

---

## 2. Fontes de Dados

### 2.1 Portos Organizados (35) — `dados.transportes.gov.br`

**Dataset:** "Poligonais dos Portos Públicos"  
**URL base CKAN:** `https://dados.transportes.gov.br/api/3/action/`  
**Package ID:** `85954902-5ec2-4432-a6f9-99f3ce77f8d1`  
**Atualização:** trimestral (última versão jun/2024)

**Formato:** 1 CSV por porto, separador `;`, schema:

```
Anexos;Vértice;Zona (Fuso);Hemisfério;Long (º);Lat (º);Long (UTM);Lat (UTM)
1;SSZ-1;23;Sul;-46,30174178;-23,89886746;367487,4707;7356360,256
1;SSZ-2;23;Sul;-46,30132054;-23,89855336;367530,036;7356395,431
...
```

- Cada linha = 1 vértice do polígono em SIRGAS 2000 (`EPSG:4674` ≈ WGS-84 para nossos fins).
- Coluna `Anexos` separa polígonos múltiplos quando o porto tem áreas descontinuadas (ex.: Santos tem 4 anexos).
- Santos: 12.790 vértices. Os menores: ~50–200 vértices.

**Endpoint de download conhecido (via `resource_show`):**
```
https://dados.transportes.gov.br/dataset/{package_id}/resource/{resource_id}/download/{nome-do-arquivo}.csv
```

### 2.2 Terminais de Uso Privado (TUPs) — fonte mais espinhosa

A ANTAQ tinha um dataset consolidado de TUPs até 2016 (`infraestrutura-federal-de-transportes-instalacoes-portuarias`) que está marcado como **DESCONTINUADO** no portal e os recursos viraram PDFs.

Alternativas:

1. **Sistema Webportos** (ANTAQ + UFSC) — tem dados de TUPs, mas a API pública não é estável; pode requerer screen-scraping cuidadoso.
2. **Geobases ES** — exemplo: `antaq_terminais_uso_privado_es` (camada vetorial). Existe equivalente para outras UFs em IDEs estaduais (RJ, SP, BA), mas a cobertura nacional consolidada não é trivial.
3. **Curadoria manual dos TOP TUPs**:
   - **Ponta da Madeira (Vale, MA)** — minério de ferro
   - **Tubarão (Vale, ES)** — minério de ferro
   - **Açu (Prumo, RJ)** — multipropósito
   - **Pecém (CE)** — granéis
   - **Itaguaí TECON (CSN, RJ)** — minério/contêineres
   - **Madre de Deus (Petrobras, BA)** — derivados
   - **Ilha d'Água/Redonda (Petrobras, RJ)** — derivados
   - **Almirante Maximiano Fonseca (Petrobras, SP)** — derivados
   - **Itapoá (TUP, SC)** — contêineres
   - **Embraport (TUP, SP, Santos)** — contêineres

   Para os 10 mais relevantes ao caso do MineralRadar (movimentação de minério/granéis bruto, alvo do produto), **fazemos curadoria manual**, mesmo padrão dos portos organizados. O campo `tipo` agrega também pontos de apoio e terminais (ver §2.3).

**Recomendação Fase 2:** começar com Portos Organizados (poligonais oficiais, automatizado) + os 10 TOP TUPs (curadoria manual). Se o backlog pedir, expandir TUPs/ETCs depois.

### 2.3 Pontos de apoio, terminais intermodais e bases logísticas

Objetivo: cobrir instalações que **não são porto organizado** mas entram em rota, overlay e análise de proximidade (ex.: base de apoio offshore, terminal ferroviário ligado ao arco portuário, armazém ANTAQ-adjacente).

| Origem típica | Observação |
|---|---|
| Curadoria + geometria pontual | Muitos cadastros públicos consolidados não trazem polígono; usar `geo_point` em `centroide` / `acesso_rodoviario` e `poligono: null` até haver fonte vetorial. |
| Operador / concessionária | Documentos públicos de licitação e relatórios ANTAQ (PDF/HTML) — ingestão semiautomática. |
| `vinculo_porto_codigo` | Quando o registro for ponto de apoio de um porto organizado, preencher com o `codigo` do porto pai (ex.: ancoradouro auxiliar de `VDC`). |

No schema, `tipo` inclui: `PORTO_ORGANIZADO` | `TUP` | `ETC` | `PONTO_APOIO` | `TERMINAL_INTERMODAL` | `BASE_LOGISTICA`.

---

## 3. Mapping OpenSearch — `mr_portos_v001`

Mesmo padrão de `mr_municipios_v001` (já consumido por `municipio_por_coordenada` / `obter_poligono` / `municipios_em_raio`). **Fonte canônica do JSON de criação:** constante `MR_PORTOS` em `backend/scripts/setup_indices.py` (analyzer `pt_br`, `knn_vector` com engine `faiss` alinhada aos demais índices do projeto).

Referência conceitual (pode divergir em detalhes de analyzer/engine — prevalece o `setup_indices.py`):

```json
PUT mr_portos_v001
{
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "codigo":                 { "type": "keyword" },
      "codigo_antaq":           { "type": "keyword" },
      "nome":                   { "type": "text", "analyzer": "pt_br", "fields": { "keyword": { ... } } },
      "nome_normalizado":       { "type": "keyword", "normalizer": "lower_ascii" },
      "tipo":                   { "type": "keyword" },
      "esfera":                 { "type": "keyword" },
      "uf":                     { "type": "keyword" },
      "municipio":              { "type": "keyword" },
      "id_ibge_municipio":      { "type": "keyword" },
      "autoridade_portuaria":   { "type": "text", "analyzer": "pt_br", "fields": { "keyword": { ... } } },
      "endereco":               { "type": "text", "analyzer": "pt_br", "fields": { "keyword": { ... } } },
      "cargas_principais":      { "type": "keyword" },
      "centroide":              { "type": "geo_point" },
      "acesso_rodoviario":      { "type": "geo_point" },
      "poligono":               { "type": "geo_shape" },
      "area_km2":               { "type": "double" },
      "aliases":                { "type": "text", "analyzer": "pt_br", "fields": { "keyword": { ... } } },
      "vinculo_porto_codigo":   { "type": "keyword" },
      "operador":               { "type": "text", "analyzer": "pt_br", "fields": { "keyword": { ... } } },
      "fonte":                  { "type": "keyword" },
      "data_referencia":        { "type": "date" },
      "validacao_pendente":     { "type": "boolean" },
      "ativo":                  { "type": "boolean" },
      "embedding_nome":         { "type": "knn_vector", "dimension": 1536, "method": { ... } },
      "indexed_at":             { "type": "date" }
    }
  }
}
```

### Comentários sobre o schema

- **`acesso_rodoviario`** preserva a virada-chave da Fase 1: o ponto de roteamento NÃO é o centroide (pode cair no mar para portos como Santana/AP) — vem da curadoria manual, mesmo conteúdo do CSV de hoje.
- **`poligono` (`geo_shape`)** habilita queries que a Fase 1 não consegue:
  - `porto_por_coordenada(lat, lon)` — mesmo padrão do `municipio_por_coordenada`.
  - `geo_shape.relation = "within"` para "fornecedores dentro do Porto de Santos".
  - Intersecção com isócrona (já temos a infra em `synthetic_tools.py`).
- **`embedding_nome`** é opcional na primeira versão. Justifica o k-NN apenas se aparecer demanda real do tipo "porto graneleiro do nordeste" — o LLM já consegue resolver isso via `cargas_principais` keyword, então não é prioridade.
- **`id_ibge_municipio`** permite `JOIN` lógico com `mr_municipios_v001` na hora de responder "porto de Santos está em qual mesorregião?".

---

## 4. Pipeline de Ingestão

Script `backend/scripts/ingest_portos.py` (substitui o nome provisório `import_portos_v001.py`):

**Configuração opcional:** `backend/data/portos_ckan_overrides.yaml` — mapeamento `ckan_match` (código interno → `cidade` / `uf` como no título CKAN) e opcionalmente `ckan_package_id`. Mescla com defaults embutidos no script; o YAML prevalece nas chaves repetidas. CLI: `--config-yaml PATH`, `--package-id UUID`.

**Embeddings opcionais:** flag `--embed` gera `embedding_nome` (1536 dims) via Azure OpenAI **síncrono** (`AZURE_OPENAI_*` no `.env`, mesmo padrão do MCP), sem Redis.

### 4.1 Etapas

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PASSO 1: Listar todos os recursos do dataset CKAN                      │
│   GET /api/3/action/package_show?id=85954902-5ec2-4432-a6f9-99f3ce77f8d1│
│   → 35 portos × ~3 trimestres versionados = ~105 recursos               │
│   Filtrar para a versão MAIS RECENTE de cada porto (parsing do nome)    │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│  PASSO 2: Para cada porto, baixar o CSV + montar GeoJSON                │
│   shapely.geometry.Polygon([(lon, lat) for lon, lat in vertices])       │
│   - Se múltiplos `Anexos`, monta MultiPolygon                           │
│   - Calcula centroide via Polygon.centroid                              │
│   - Calcula area_km2 via Polygon.area + projeção UTM da zona            │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│  PASSO 3: Merge com curadoria manual (CSV Fase 1)                       │
│   - Lê backend/data/portos_brasil.csv (Fase 1)                          │
│   - Match por `codigo` (chave estável que cada um leva)                 │
│   - Pega `acesso_rodoviario`, `aliases`, `cargas_principais`            │
│   - Para portos que existem só na curadoria (ex.: TUPs), usa            │
│     o centroide curado como `centroide` E `acesso_rodoviario`           │
│     (poligono fica null — flag para o frontend não tentar overlay)      │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│  PASSO 4: Cross-walk com mr_municipios_v001                               │
│   geo_shape contains (centroide) → idMunicipio                          │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│  PASSO 5: (opcional) Flag `--embed` — Azure OpenAI síncrono no ingest     │
│   Texto: nome | UF | cargas | aliases → vetor em embedding_nome         │
└─────────────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────────────┐
│  PASSO 6: Bulk index em mr_portos_v001 (refresh wait_for)               │
│   Smoke test: count == ~45 (35 organizados + 10 TUPs top)               │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Atualização periódica

- Cron mensal (ou trigger manual): roda `python -m scripts.ingest_portos` de novo.
- O script é **idempotente** — usa `_id = codigo` no bulk para upsert determinístico.
- O changelog (delta da última versão) é logado para auditoria.

### 4.3 Tratamento da Fase 1 → Fase 2

O CSV `backend/data/portos_brasil.csv` **continua existindo** após a Fase 2 — vira a fonte de verdade da curadoria manual (acesso rodoviário, aliases, TUPs top). O script de ingestão lê o CSV e o consolida com o pipeline CKAN antes de indexar.

---

## 5. Tools MCP Novas (Fase 2)

Mantém todas as tools da Fase 1 e adiciona:

### 5.1 `geo__porto_por_coordenada(latitude, longitude)`

```
geo_shape.contains(point=[lon, lat]) sobre mr_portos_v001.poligono
```

Use case: "Esta coordenada está dentro de algum porto?" → resposta sim/não + dados do porto.

### 5.2 `geo__obter_poligono_porto(codigo | nome)`

Mesmo contrato de `obter_poligono` (municípios), retorna `GeoJSON Feature` com `properties` ricos do porto.  
Use case: overlay do porto no mapa do frontend (mesmo player que já desenha município).

### 5.3 `geo__buscar_dentro_de_porto(codigo, substancia=None, termo_busca=None, …)`

**Synthetic tool** (mesmo padrão do `geo__buscar_dentro_de_isocrona`):
- Passo 1: pega o `poligono` do `mr_portos_v001` pelo código.
- Passo 2: chama em paralelo `jazidas__fornecedores_por_poligono` + `empresas__empresas_por_poligono` com aquela geometria.

Use case: "fornecedores de cimento dentro do Porto de Suape".

### 5.4 `geo__buscar_porto` (reescrito)

Mesma assinatura pública da Fase 1, mas o backend trocado para OpenSearch:
- Lookup por código → `term query` (idêntico)
- Lookup por nome → `multi_match` com analyzer `pt_br` sobre `nome` + `aliases`
- Filtro por `uf` / `tipo` → `term query`
- `proximo_a_lat/lon` → `_geo_distance sort` + `geo_distance` filter (raio implícito de 2000 km, longe o suficiente para sempre cobrir Brasil inteiro)
- (Opcional) Busca semântica → `knn` sobre `embedding_nome` + filtro post-rerank

A migração é transparente: o LLM continua chamando `geo__buscar_porto` com os mesmos kwargs.

---

## 6. Migração do Guardrail

O guardrail em `app/langgraph/tools.py` (`_rewrite_kwargs_for_porto`) usa `_try_resolve_porto`, que importa o registry diretamente. Na migração:

1. Mantemos o registry em memória **como cache L1** (preserva latência de µs).
2. Adicionamos um método `PortosRegistry.refresh_from_opensearch(client)` que recarrega o cache quando o índice muda.
3. O guardrail continua chamando `_try_resolve_porto` — sem mudança no `app/langgraph/tools.py`.

A separação preserva a propriedade de "guardrail rápido sem rede" do guardrail atual.

---

## 7. Critérios de Aceite (Fase 2)

| # | Critério | Como validar |
|---|---|---|
| F2-1 | Índice `mr_portos_v001` criado e populado com ≥35 portos organizados | `GET mr_portos_v001/_count` |
| F2-2 | Polígono real de Santos importado (≥4 anexos, ≥10000 vértices) | `GET mr_portos_v001/_doc/PSV?_source=poligono` |
| F2-3 | `municipio_por_coordenada` continua funcionando após o índice | regression test em `tests/test_geo_tools.py` |
| F2-4 | `porto_por_coordenada(-23.92, -46.32)` → `PSV` | novo test |
| F2-5 | `obter_poligono_porto('PSV')` retorna GeoJSON Feature válido | novo test |
| F2-6 | `buscar_dentro_de_porto('PSV', termo_busca='cimento')` retorna empresas | novo test E2E |
| F2-7 | Pelo menos 10 TUPs top com coordenadas curadas | conferência visual no mapa |
| F2-8 | Atualização mensal automatizada (cron) | doc + ID do job |
| F2-9 | Latência da `buscar_porto` < 50ms p95 | benchmark |
| F2-10 | Guardrail Fase 1 continua resolvendo "Porto de Aratu" → ATU | regression test |
| F2-11 | Pelo menos um registro `PONTO_APOIO` ou `TERMINAL_INTERMODAL` com `vinculo_porto_codigo` quando aplicável | revisão de dados |

---

## 8. Estimativa

| Tarefa | Tempo |
|---|---|
| Mapping + script `ingest_portos.py` (poligonais + merge CSV) | 1 dia |
| Curadoria manual TOP 10 TUPs | 0.5 dia |
| Tools MCP novas (3 tools) | 1 dia |
| Synthetic tool `buscar_dentro_de_porto` | 0.5 dia |
| Migração do guardrail (cache L1) | 0.5 dia |
| Testes + benchmark + smoke E2E | 1 dia |
| **Total** | **4.5 dias** |

---

## 9. Pendências / Decisões em aberto

- [ ] **Embedding obrigatório?** — Fase 2 inicial pode pular, ativar só se aparecer demanda de busca semântica. Deixa o índice mais leve.
- [ ] **TUPs até onde?** — TOP 10 cobrem 80% da movimentação nacional. Se cliente pedir, expandir.
- [ ] **Polígonos versionados** — guardar todas as versões trimestrais (`data_referencia`) ou só a mais recente? Recomendação: só a mais recente; auditoria histórica fica no git do CSV.
- [ ] **Frontend** — overlay do polígono do porto no mapa (igual já tem para município) é dependência de um ticket separado no frontend.

---

## 10. Referências

- Dataset oficial: https://dados.transportes.gov.br/dataset/poligonais-dos-portos-publicos
- API CKAN: https://dados.transportes.gov.br/api/3/action/help_show?name=resource_show
- ANTAQ Painel Estatístico: https://www.gov.br/antaq/pt-br/central-de-conteudos/estatisticas
- Pacote `geobr` (Python/R) — possível alternativa para automação: https://github.com/ipeaGIT/geobr
- `mcp_servers/geo/queries/municipios.py` — referência de implementação para as tools de polígono/coordenada/raio.
