# 📊 Análise dos Índices OpenSearch - Estado Atual

> Documento gerado em: 2026-02-09
> Cluster: `search-supplyradar-prod-5arrhz7f5fcgh2xpj6uevjigoa.aos.sa-east-1.on.aws`

---

## 📋 Resumo dos Índices

| Índice | Documentos | Tamanho | Tipo | Descrição |
|--------|------------|---------|------|-----------|
| `anm_v001` | **25.254.131** | 5.6 GB | Principal | Processos ANM (jazidas minerais) |
| `cnpj_v001` | **220.997.521** | 68.5 GB | Principal | Empresas CNPJ (estabelecimentos) |
| `ibge_municipio_v001` | 5.631 | 931 MB | Referência | Municípios brasileiros com polígonos |
| `rfb_cnae_v001` | 2.394 | 16.8 MB | Referência | Códigos CNAE com embeddings |
| `anm_substancia_v001` | 862 | 5.1 MB | Auxiliar | Substâncias minerais com embeddings |
| `anm_tipo-uso-substancia_v001` | 26 | 162.6 KB | Auxiliar | Tipos de uso de substância |
| `anm_v002` | 0 | - | (vazio) | Provavelmente para migração futura |

**Total: ~246 milhões de documentos, ~75 GB**

---

## 🔍 Detalhamento por Índice

### 1. `anm_v001` - Processos ANM (Jazidas)

**Volume:** 25.2M documentos | 5.6 GB

Este é o índice principal de jazidas minerais. Estrutura muito rica com dados denormalizados.

#### Campos Principais

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `dsProcesso` | keyword | Número do processo ANM (ex: "832.145/2018") |
| `nrProcesso` | integer | Número numérico do processo |
| `nrAnoProcesso` | integer | Ano do processo |
| `nrNUP` | keyword | Número Único de Protocolo |
| `btAtivo` | keyword | Status ativo/inativo |
| `qtAreaHa` | double | Área em hectares |
| `dtProtocolo` | date | Data de protocolo |
| `dtPrioridade` | date | Data de prioridade |

#### Campos Aninhados (nested)

| Campo | Tipo | Campos Internos |
|-------|------|-----------------|
| `faseProcesso` | object | `idFaseProcesso`, `dsFaseProcesso` |
| `tipoRequerimento` | object | `idTipoRequerimento`, `dsTipoRequerimento` |
| `unidadeAdministrativaRegional` | object | ID e descrição da unidade |
| `substancias` | **nested** | `Substancia.nmSubstancia`, `tipoUsoSubstancia`, datas de vigência |
| `municipios` | **nested** | Dados completos IBGE (código, nome, UF, mesorregião, microrregião, `geo_point`) |
| `pessoas` | **nested** | Titulares e responsáveis com `detalhesCNPJ` completo |
| `poligonos` | **nested** | **`geom` (geo_shape)**, `localizacao` (geo_point), área, substância, titular |
| `eventos` | **nested** | Histórico de eventos com datas |
| `titulos` | **nested** | Documentos legais (alvarás, portarias) |
| `associacoes` | **nested** | Processos associados |
| `documentacao` | **nested** | Documentos protocolados |

#### ⚠️ Observações Importantes

1. **Sem campo `embedding`** - Não há busca vetorial direta no índice principal
2. **Estrutura denormalizada** - Dados do CNPJ embutidos em `pessoas.detalhesCNPJ`
3. **Geometria em nested** - O `geo_shape` está dentro de `poligonos.geom`
4. **Múltiplos níveis de aninhamento** - Ex: `pessoas.detalhesCNPJ.socios` (nested dentro de nested)

#### Exemplo de Busca Geoespacial (nested)

```json
{
  "query": {
    "nested": {
      "path": "poligonos",
      "query": {
        "bool": {
          "must": [
            {
              "geo_distance": {
                "distance": "50km",
                "poligonos.localizacao": {
                  "lat": -23.55,
                  "lon": -46.63
                }
              }
            }
          ]
        }
      }
    }
  }
}
```

---

### 2. `cnpj_v001` - Empresas CNPJ

**Volume:** 221M documentos | 68.5 GB

Índice de estabelecimentos da Receita Federal.

#### Campos Principais

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `id` | keyword | Identificador único |
| `cnpjDv` | keyword | Dígito verificador CNPJ |
| `cnpjOrdem` | keyword | Ordem do estabelecimento |
| `nomeFantasia` | text (pt_brazilian) | Nome fantasia |
| `uf` | keyword | Sigla do estado |
| `localizacao` | **geo_point** | Coordenadas do estabelecimento |
| `dataInicioAtividade` | date | Data de abertura |
| `dataSituacaoCadastral` | date | Data da situação |

#### Campos Estruturados

| Campo | Tipo | Campos Internos |
|-------|------|-----------------|
| `empresa` | object | `cnpjBasico`, `razaoSocial`, `capitalSocial`, `naturezaJuridica`, `porteEmpresa` |
| `cnaeFiscalPrincipal` | object | `codigo`, `descricao` |
| `cnaeFiscalSecundaria` | **nested** | Lista de CNAEs secundários |
| `situacaoCadastral` | object | `codigo`, `descricao` |
| `motivoSituacaoCadastral` | object | Motivo da situação |
| `municipio` | object | `codigo`, `descricao` |
| `socios` | **nested** | Dados dos sócios (nome, CPF/CNPJ, qualificação, etc.) |
| `simples` | object | Dados do Simples Nacional |

#### Endereço

| Campo | Tipo |
|-------|------|
| `tipoLogradouro` | text |
| `logradouro` | text |
| `numero` | keyword |
| `complemento` | text |
| `bairro` | text |
| `cep` | keyword |

#### ⚠️ Observações Importantes

1. **Sem campo `embedding`** - Não há busca vetorial direta
2. **geo_point disponível** - Permite buscas por proximidade
3. **Sócios em nested** - Requer queries nested para filtrar por sócio
4. **CNAE secundário em nested** - Permite filtrar por qualquer CNAE

#### Exemplo de Busca por CNAE + Geo

```json
{
  "query": {
    "bool": {
      "must": [
        {
          "term": {
            "cnaeFiscalPrincipal.codigo": "0810-0/99"
          }
        },
        {
          "geo_distance": {
            "distance": "30km",
            "localizacao": {
              "lat": -23.55,
              "lon": -46.63
            }
          }
        }
      ],
      "filter": [
        {
          "term": {
            "situacaoCadastral.codigo": "02"
          }
        }
      ]
    }
  }
}
```

---

### 3. `ibge_municipio_v001` - Municípios

**Volume:** 5.631 documentos | 931 MB

Dados geográficos dos municípios brasileiros.

#### Campos

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `idMunicipio` | keyword | Código IBGE 7 dígitos |
| `idMunicipio6` | keyword | Código IBGE 6 dígitos |
| `idMunicipioANM` | keyword | Código ANM |
| `idMunicipioRFB` | keyword | Código RFB |
| `idMunicipioBCB` | keyword | Código BCB |
| `idMunicipioTSE` | keyword | Código TSE |
| `nome` | text (ptbr_text) | Nome do município |
| `siglaUF` | keyword | Sigla do estado |
| `nomeUF` | text | Nome do estado |
| `nomeRegiao` | text | Nome da região |
| `nomeMesorregiao` | text | Mesorregião IBGE |
| `nomeMicrorregiao` | text | Microrregião IBGE |
| `idMesorregiao` | keyword | Código mesorregião |
| `idMicrorregiao` | keyword | Código microrregião |
| `amazoniaLegal` | boolean | Está na Amazônia Legal? |
| `capitalUF` | boolean | É capital? |
| `localizacao` | **geo_point** | Centro do município |
| `localizacaoEconomica` | **geo_point** | Centro econômico |
| `poligono` | **geo_shape** | ✅ **Polígono do município!** |

#### ⚠️ Observações Importantes

1. **Tem geo_shape!** - Permite queries de interseção com polígonos
2. **Múltiplos códigos** - Compatibilidade com ANM, RFB, BCB, TSE
3. **Dois geo_points** - Centro geográfico e econômico

#### Exemplo: Município por Coordenada

```json
{
  "query": {
    "geo_shape": {
      "poligono": {
        "shape": {
          "type": "point",
          "coordinates": [-46.63, -23.55]
        },
        "relation": "contains"
      }
    }
  }
}
```

---

### 4. `rfb_cnae_v001` - Códigos CNAE

**Volume:** 2.394 documentos | 16.8 MB

Tabela de classificação de atividades econômicas **COM EMBEDDINGS**.

#### Campos

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `codigo` | keyword | Código CNAE (ex: "0810-0/99") |
| `secao` | keyword | Letra da seção |
| `nomeSecao` | text | Nome da seção |
| `divisao` | keyword | Código divisão |
| `nomeDivisao` | text | Nome da divisão |
| `grupo` | keyword | Código grupo |
| `nomeGrupo` | text | Nome do grupo |
| `classe` | keyword | Código classe |
| `nomeClasse` | text | Nome da classe |
| `subclasse` | keyword | Código subclasse |
| `nomeSubclasse` | text | Nome da subclasse |
| `nivel` | keyword | Nível hierárquico |
| `hierarquia` | text | Hierarquia completa |
| `notasExplicativas` | text | Notas explicativas |
| `conteudo` | text | Texto agregado (copy_to) |
| `textoSemantico` | text | Texto para embedding |
| `embedding` | **knn_vector (1536)** | ✅ **Embedding para busca vetorial!** |

#### ✅ Este índice JÁ TEM busca vetorial!

```json
{
  "query": {
    "knn": {
      "embedding": {
        "vector": [0.1, 0.2, ...],
        "k": 10
      }
    }
  }
}
```

---

### 5. `anm_substancia_v001` - Substâncias Minerais

**Volume:** 862 documentos | 5.1 MB

Tabela de substâncias minerais **COM EMBEDDINGS**.

#### Campos

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `idSubstancia` | integer | ID da substância |
| `nmSubstancia` | text (pt_brazilian) | Nome da substância |
| `embedding` | **knn_vector (1536)** | ✅ **Embedding para busca vetorial!** |

#### ✅ Permite busca semântica de substâncias

"Areia para construção" → encontra "Areia", "Areia lavada", "Areia industrial"

---

### 6. `anm_tipo-uso-substancia_v001` - Tipos de Uso

**Volume:** 26 documentos | 162.6 KB

Tabela de tipos de uso de substâncias **COM EMBEDDINGS**.

#### Campos

| Campo | Tipo |
|-------|------|
| `idTipoUsoSubstancia` | integer |
| `dsTipoUsoSubstancia` | text (pt_brazilian) |
| `embedding` | **knn_vector (1536)** |

---

## 🔗 Relacionamentos entre Índices

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         RELACIONAMENTOS                                     │
│                                                                             │
│  ┌──────────────┐                      ┌──────────────┐                     │
│  │  anm_v001    │─────────────────────►│  cnpj_v001   │                     │
│  │  (Jazidas)   │  pessoas.detalhesCNPJ│  (Empresas)  │                     │
│  │              │  .empresa.cnpjBasico │              │                     │
│  │  25M docs    │         =            │  221M docs   │                     │
│  └──────┬───────┘  empresa.cnpjBasico  └──────┬───────┘                     │
│         │                                      │                             │
│         │ municipios.idMunicipio               │ municipio.codigo            │
│         │                                      │                             │
│         └──────────────┬───────────────────────┘                             │
│                        │                                                     │
│                        ▼                                                     │
│               ┌────────────────┐                                             │
│               │ibge_municipio  │                                             │
│               │    _v001       │                                             │
│               │   5.6K docs    │                                             │
│               │  (geo_shape!)  │                                             │
│               └────────────────┘                                             │
│                                                                             │
│  ┌──────────────┐         ┌──────────────┐         ┌──────────────┐         │
│  │anm_substancia│         │anm_tipo-uso  │         │ rfb_cnae_v001│         │
│  │    _v001     │         │_substancia   │         │              │         │
│  │  862 docs    │         │   _v001      │         │  2.4K docs   │         │
│  │ (embedding!) │         │   26 docs    │         │ (embedding!) │         │
│  └──────────────┘         │ (embedding!) │         └──────────────┘         │
│         │                 └──────────────┘                │                 │
│         │                        │                        │                 │
│         └────────────────────────┴────────────────────────┘                 │
│                                  │                                          │
│                    Busca semântica por termo                                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Chaves de Ligação

| De | Para | Campo de ligação |
|----|------|------------------|
| `anm_v001` → `cnpj_v001` | `pessoas.detalhesCNPJ.empresa.cnpjBasico` = `empresa.cnpjBasico` |
| `anm_v001` → `ibge_municipio_v001` | `municipios.idMunicipio` = `idMunicipio` |
| `cnpj_v001` → `ibge_municipio_v001` | `municipio.codigo` = `idMunicipioRFB` |
| `anm_v001` → `anm_substancia_v001` | `substancias.Substancia.idSubstancia` = `idSubstancia` |
| `cnpj_v001` → `rfb_cnae_v001` | `cnaeFiscalPrincipal.codigo` = `codigo` |

---

## 🎯 Capacidades de Busca Disponíveis

### ✅ Já Implementado

| Tipo | Índice | Campo | Status |
|------|--------|-------|--------|
| Full-text (pt-BR) | `anm_v001` | Múltiplos campos | ✅ |
| Full-text (pt-BR) | `cnpj_v001` | Múltiplos campos | ✅ |
| Geo-distance | `anm_v001` | `poligonos.localizacao` (nested) | ✅ |
| Geo-distance | `cnpj_v001` | `localizacao` | ✅ |
| Geo-shape | `anm_v001` | `poligonos.geom` (nested) | ✅ |
| Geo-shape | `ibge_municipio_v001` | `poligono` | ✅ |
| k-NN (vetor) | `rfb_cnae_v001` | `embedding` | ✅ |
| k-NN (vetor) | `anm_substancia_v001` | `embedding` | ✅ |
| k-NN (vetor) | `anm_tipo-uso-substancia_v001` | `embedding` | ✅ |

### ⚠️ Limitações Atuais

| Tipo | Índice | Status | Observação |
|------|--------|--------|------------|
| k-NN (vetor) | `anm_v001` | ❌ Não existe | Não obrigatório - busca em 2 passos funciona |
| k-NN (vetor) | `cnpj_v001` | ❌ Não existe | Não obrigatório - busca em 2 passos funciona |
| Geo-shape | `cnpj_v001` | ❌ Apenas geo_point | Suficiente para busca por raio |
| Geo-shape | `anm_v001.poligonos.geom` | ⚠️ **VERIFICAR** | Campo pode estar vazio - dados em `poligonos.poligonos` |

---

## 📝 Implicações para os MCPs

### MCP Jazidas (anm_v001)

**Capacidades disponíveis:**
- ✅ Busca por substância (via nested query + busca semântica em `anm_substancia_v001`)
- ✅ Busca por município/UF
- ✅ Busca por fase do processo
- ✅ Busca por titular (nome da pessoa/empresa)
- ✅ Busca geoespacial por raio (nested em `poligonos.localizacao`)
- ✅ Busca geoespacial por polígono (nested em `poligonos.geom`)
- ✅ Detalhes completos incluindo dados do CNPJ do titular

**Estratégia de busca híbrida:**
1. Buscar termo semelhante em `anm_substancia_v001` usando embedding
2. Usar ID da substância para filtrar em `anm_v001`
3. Aplicar filtros geoespaciais (nested)

### MCP Empresas (cnpj_v001)

**Capacidades disponíveis:**
- ✅ Busca por razão social / nome fantasia
- ✅ Busca por CNAE (via busca semântica em `rfb_cnae_v001`)
- ✅ Busca por município/UF
- ✅ Busca por situação cadastral
- ✅ Busca geoespacial por raio
- ✅ Filtro por sócios (nested)
- ✅ Filtro por Simples Nacional / MEI

**Estratégia de busca híbrida:**
1. Buscar CNAE semelhante em `rfb_cnae_v001` usando embedding
2. Usar código CNAE para filtrar em `cnpj_v001`
3. Aplicar filtros geoespaciais

### MCP Geo (ibge_municipio_v001)

**Capacidades disponíveis:**
- ✅ Buscar município por nome
- ✅ Identificar município por coordenada (geo_shape contains)
- ✅ Listar municípios por UF/região
- ✅ Obter polígono do município para overlay
- ✅ Validar se ponto está dentro de município

---

## 🔴 Análise Crítica dos Mapeamentos

### ⚠️ Problemas Identificados

#### 1. `anm_v001` - Campo `geom` vs `poligonos.poligonos` (ERRO CRÍTICO)

**O Problema:**
- O mapeamento define `poligonos.geom` como `geo_shape`
- **Os dados reais estão em `poligonos.poligonos`** com estrutura `{type, coordinates}`

```json
// MAPEAMENTO espera:
"poligonos": {
  "geom": { "type": "geo_shape" }  // ❌ Campo vazio nos docs
}

// DADOS REAIS têm:
"poligonos": [{
  "localizacao": { "lat": -20.64, "lon": -43.61 },  // ✅ geo_point funciona
  "poligonos": {                                      // ❌ Nome confuso!
    "type": "polygon",
    "coordinates": [[[...], [...]]]
  }
}]
```

**Impacto:**
- ❌ Busca `geo_shape` no campo `geom` **NÃO RETORNA RESULTADOS**
- ✅ Busca `geo_distance` em `poligonos.localizacao` **FUNCIONA**

**Causa Provável:** ETL populando campo errado ou mapeamento criado após ETL

---

#### 2. Nesting Excessivo em `anm_v001`

**Estrutura atual (confusa):**
```
anm_v001
├── poligonos (nested)
│   ├── localizacao (geo_point) ✅
│   ├── poligonos (object) ← nome duplicado, confuso!
│   │   ├── type
│   │   └── coordinates
│   └── geom (geo_shape) ← vazio!
```

**Níveis de aninhamento:**
- `pessoas` → nested
- `pessoas.detalhesCNPJ.socios` → nested dentro de nested dentro de nested (3 níveis!)
- `pessoas.detalhesCNPJ.cnaeFiscalSecundaria` → nested (3 níveis)

**Impacto:**
- Queries complexas e lentas
- Maior uso de memória
- Difícil manutenção

---

#### 3. Denormalização Excessiva em `anm_v001`

**Dados do CNPJ copiados integralmente:**
```json
"pessoas": [{
  "detalhesCNPJ": {
    "empresa": { /* cópia completa */ },
    "socios": [ /* cópia completa */ ],
    "cnaeFiscalPrincipal": { /* cópia */ },
    "cnaeFiscalSecundaria": [ /* cópia */ ],
    // ... 40+ campos copiados
  }
}]
```

**Impacto:**
- Índice `anm_v001` maior do que necessário
- Dados podem ficar desatualizados (empresa muda, processo não atualiza)
- Não é um erro, mas é uma escolha de design discutível

---

### ✅ Aspectos Positivos

| Aspecto | Status | Observação |
|---------|--------|------------|
| Full-text search | ✅ | Analyzers `pt_brazilian` e `pt_ascii` bem configurados |
| Geo-point em `poligonos.localizacao` | ✅ | Funciona para busca por raio |
| Geo-point em `cnpj_v001.localizacao` | ✅ | Funciona perfeitamente |
| Geo-shape em `ibge_municipio_v001.poligono` | ✅ | Funciona para identificar município |
| Embeddings em índices auxiliares | ✅ | CNAE e Substância prontos para busca semântica |

---

### 📊 Sobre a Necessidade de Embeddings nos Índices Principais

> **Conclusão: NÃO É OBRIGATÓRIO, mas seria útil**

#### Por que NÃO é obrigatório:

| Razão | Explicação |
|-------|------------|
| Full-text funciona | BM25 com stemming brasileiro já indexado |
| Embeddings existem nos auxiliares | Busca semântica em 2 passos funciona |
| Performance | Adicionar 1536 dims * 25M docs = ~150GB extras |

#### Quando SERIA útil:

| Caso de Uso | Benefício |
|-------------|-----------|
| "Encontre jazidas de material para pavimentação" | Evitaria o passo intermediário |
| Busca por descrição livre | Melhor recall semântico |
| Queries muito específicas | "areia para fundação de ponte" |

#### Recomendação:

```
┌─────────────────────────────────────────────────────────────────┐
│  ESTRATÉGIA HÍBRIDA ATUAL (recomendada para MVP)                │
│                                                                 │
│  1. Busca semântica em índice auxiliar (rápido, ~1ms)           │
│  2. Query estruturada + geo no índice principal                 │
│  3. Cache de IDs em Redis para termos frequentes                │
│                                                                 │
│  ✅ Funciona sem alteração nos índices principais               │
│  ✅ Performance aceitável                                       │
│  ✅ Não aumenta storage                                         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  ESTRATÉGIA FUTURA (se necessário)                              │
│                                                                 │
│  - Adicionar campo `embedding_descricao` em anm_v001            │
│  - Texto: "{substancia} em {municipio} - {titular}"             │
│  - Permite busca semântica direta em 1 passo                    │
│  - Custo: ~150GB storage adicional                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Recomendações

### Correções Urgentes (Pré-MCP)

1. **🔴 Verificar campo `geom` vs `poligonos.poligonos`:**
   - Confirmar com equipe de ETL qual é o campo correto
   - Se `poligonos.poligonos` é o correto, atualizar mapeamento ou reindexar
   - Query de geo_shape precisa apontar para o campo certo

2. **Testar busca geo_shape:**
   ```bash
   # Este deve funcionar se o campo estiver correto:
   GET /anm_v001/_search
   {
     "query": {
       "nested": {
         "path": "poligonos",
         "query": {
           "geo_shape": {
             "poligonos.geom": {  # ou poligonos.poligonos?
               "shape": { "type": "point", "coordinates": [-43.6, -20.6] }
             }
           }
         }
       }
     }
   }
   ```

### Curto Prazo (MCPs S5-S6)

1. **Usar `poligonos.localizacao` (geo_point) para buscas por raio:**
   - Funciona hoje
   - Suficiente para maioria dos casos de uso

2. **Implementar busca híbrida em dois passos:**
   - Primeiro: busca semântica nos índices auxiliares (substância, CNAE)
   - Segundo: query estruturada + geo no índice principal

3. **Cache de IDs semânticos em Redis:**
   - Armazenar mapeamento "termo" → "IDs de substância"
   - Evitar re-executar busca vetorial para termos frequentes

### Médio Prazo (ETL - Correções)

1. **Corrigir campo geográfico em `anm_v001`:**
   - Renomear `poligonos.poligonos` para `poligonos.geom`
   - Ou criar novo campo `poligonos.geom` com os dados corretos

2. **Considerar desnormalização otimizada:**
   - Promover `poligonos.localizacao` para campo root-level
   - Evitar nested em buscas simples de proximidade

3. **Avaliar adição de embeddings (opcional):**
   - Só se a busca em 2 passos não atender requisitos de UX
   - Custo/benefício precisa ser avaliado

---

## Apêndice — `mr_geoquimica_v001` (CPRM Geoquímica)

Índice de **amostras analíticas** do SGB/CPRM (coleções OGC API `analises-rocha` + `analises-mineral-minerio` em `geoservicos.sgb.gov.br`). Documentação de mapping e criação: `backend/scripts/setup_indices.py` (`MR_GEOQUIMICA`). Ingestão: `mineral-radar-etl/bots/bot_geoquimica.py`. Uso no agente: tool MCP **`geoquimica_proxima`** (servidor Jazidas). Campos principais: `id_amostra`, `classe` (Rocha | Mineral/Minério), `location` (`geo_point`), `analitos` (keyword flat), **`analises`** (nested: `analito`, `valor`, `unidade`, `qualificador`), metadados de laboratório e projeto.

