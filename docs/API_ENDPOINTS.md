# MineralRadar - Documentação de API para Integração com Frontend

Este documento descreve os **endpoints de API** propostos para integração com um novo frontend. A aplicação atual utiliza **Blazor Server** com comunicação via SignalR, sem APIs REST tradicionais.

---

## 📋 Visão Geral da Arquitetura

### Arquitetura Atual
```
Frontend Blazor ←→ SignalR ←→ Backend C# ←→ OpenSearch (AWS)
                                         ←→ SQL Server (CNPJ)
                                         ←→ Azure OpenAI
```

### Arquitetura Proposta (REST API)
```
Novo Frontend (React/Vue/etc) ←→ REST API (.NET) ←→ OpenSearch (AWS)
                                                 ←→ SQL Server (CNPJ)
                                                 ←→ Azure OpenAI
```

---

## 🗂️ Índices OpenSearch Disponíveis

| Índice | Descrição | Dados |
|--------|-----------|-------|
| `anm_jazidas` | Jazidas minerárias | Processos ANM com localização geográfica |
| `rfb_estabelecimentos` | Estabelecimentos CNPJ | Empresas da Receita Federal com localização |
| `ibge_municipios` | Municípios IBGE | Dados de cidades brasileiras com coordenadas |
| `rfb_cnaes` | Códigos CNAE | Classificação de atividades econômicas |

---

## 📍 Endpoints de Jazidas (ANM)

### 1. Buscar Jazidas por Localização Geográfica

**Endpoint Proposto:**
```
POST /api/v1/jazidas/buscar-por-localizacao
```

**Descrição:** Consulta jazidas minerárias da ANM (Agência Nacional de Mineração) em um raio a partir de um ponto geográfico.

**Request Body:**
```json
{
  "latitude": -22.745,
  "longitude": -47.335,
  "municipio": "Campinas",
  "substancias": ["AREIA", "CASCALHO", "GRANITO"],
  "usos": ["Construção civil", "Brita"],
  "raioKm": 50,
  "distanciaInicialKm": 0,
  "limite": 10
}
```

**Parâmetros:**

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|-------------|-----------|
| `latitude` | `double` | ✅ | Latitude do ponto central (ex: -22.745) |
| `longitude` | `double` | ✅ | Longitude do ponto central (ex: -47.335) |
| `municipio` | `string` | ❌ | Nome do município de referência |
| `substancias` | `string[]` | ❌ | Lista de substâncias minerais (ver lista completa abaixo) |
| `usos` | `string[]` | ❌ | Lista de usos pretendidos (ver lista completa abaixo) |
| `raioKm` | `double` | ❌ | Raio de busca em km (default: 20) |
| `distanciaInicialKm` | `double` | ❌ | Distância mínima (anel interno) em km |
| `limite` | `int` | ❌ | Quantidade máxima de resultados (default: 10) |

**Response (200 OK):**
```json
{
  "sucesso": true,
  "parametros": {
    "latitude": -22.745,
    "longitude": -47.335,
    "raioKm": 50,
    "substancias": ["AREIA", "CASCALHO"],
    "usos": ["Construção civil"]
  },
  "totalEncontrado": 156,
  "totalRetornado": 10,
  "jazidas": [
    {
      "id": "abc123",
      "dsProcesso": "832.123/2015",
      "processo": "832.123",
      "numero": "2015",
      "titular": "EMPRESA MINERAÇÃO LTDA",
      "cnpjCpfTitular": "12.345.678/0001-99",
      "tipoPessoa": "Pessoa Jurídica",
      "fase": "Concessão de Lavra",
      "uf": "SP",
      "areaHa": 150.50,
      "substancias": ["AREIA", "CASCALHO"],
      "usos": ["Construção civil"],
      "ultimoEvento": "Publicação no DOU",
      "ano": 2015,
      "distanciaKm": 12.45,
      "localizacao": {
        "latitude": -22.800,
        "longitude": -47.400
      }
    }
  ]
}
```

---

### 2. Buscar Jazida por Número do Processo

**Endpoint Proposto:**
```
GET /api/v1/jazidas/{numeroProcesso}
```

**Exemplo:**
```
GET /api/v1/jazidas/832123/2015
```

**Parâmetros de URL:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `numeroProcesso` | `string` | Número do processo ANM (formato: XXX.XXX/AAAA ou XXXXXXAAAA) |

**Response (200 OK):**
```json
{
  "sucesso": true,
  "processo": {
    "dsProcesso": "832.123/2015",
    "nrProcesso": 832123,
    "anoProcesso": 2015,
    "nup": "48000.001234/2015-01",
    "ativo": true,
    "dataProtocolo": "2015-03-15T00:00:00Z",
    "areaHa": 150.50,
    "fase": {
      "id": 5,
      "descricao": "Concessão de Lavra"
    },
    "tipoRequerimento": {
      "id": 1,
      "nome": "Autorização de Pesquisa"
    },
    "unidadeAdministrativa": {
      "id": 3,
      "nome": "SP - São Paulo"
    },
    "municipios": [
      {
        "id": 3501,
        "nome": "Campinas",
        "uf": "SP"
      }
    ],
    "titulares": [
      {
        "cpfCnpj": "12.345.678/0001-99",
        "tipoPessoa": "J",
        "nome": "EMPRESA MINERAÇÃO LTDA",
        "razaoSocial": "EMPRESA MINERAÇÃO LTDA",
        "situacaoCadastral": "Ativa",
        "endereco": {
          "logradouro": "Rua das Pedras",
          "numero": "123",
          "bairro": "Centro",
          "municipio": "Campinas",
          "uf": "SP",
          "cep": "13000-000"
        },
        "contato": {
          "telefone1": "(19) 3333-4444",
          "email": "contato@empresa.com.br"
        }
      }
    ],
    "substancias": [
      {
        "id": 200200,
        "nome": "AREIA",
        "tipoUso": "Construção civil",
        "inicioVigencia": "2015-06-01T00:00:00Z",
        "fimVigencia": null
      }
    ],
    "eventos": [
      {
        "id": 1,
        "descricao": "Publicação de Concessão",
        "data": "2020-01-15T00:00:00Z",
        "publicacaoDOU": "Portaria n° 123/2020"
      }
    ],
    "associacoes": [
      {
        "processoAssociado": "832.124/2015",
        "tipoAssociacao": "Desmembramento",
        "dataAssociacao": "2016-05-20T00:00:00Z"
      }
    ],
    "quadroSocietario": [
      {
        "cnpjBasico": "12.345.678/0001-99",
        "cpfCnpj": "123.456.789-00",
        "nome": "João da Silva",
        "qualificacao": "Sócio-Administrador",
        "dataEntrada": "2015-01-01T00:00:00Z"
      }
    ],
    "localizacao": {
      "latitude": -22.800,
      "longitude": -47.400
    }
  }
}
```

---

### 3. Listar Substâncias Válidas

**Endpoint Proposto:**
```
GET /api/v1/jazidas/substancias
```

**Query Parameters:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `pagina` | `int` | Número da página (default: 1) |
| `tamanhoPagina` | `int` | Itens por página (default: 50) |
| `busca` | `string` | Filtro por nome |

**Response (200 OK):**
```json
{
  "sucesso": true,
  "totalItens": 870,
  "paginaAtual": 1,
  "totalPaginas": 18,
  "substancias": [
    { "codigo": "200200", "descricao": "AREIA" },
    { "codigo": "200201", "descricao": "AREIA ALUVIONAR" },
    { "codigo": "200202", "descricao": "AREIA COMUM" },
    { "codigo": "200600", "descricao": "CASCALHO" },
    { "codigo": "706100", "descricao": "GRANITO" }
  ]
}
```

---

### 4. Listar Usos Válidos

**Endpoint Proposto:**
```
GET /api/v1/jazidas/usos
```

**Response (200 OK):**
```json
{
  "sucesso": true,
  "usos": [
    { "codigo": "1", "descricao": "Construção civil" },
    { "codigo": "2", "descricao": "Fabricação de ligas" },
    { "codigo": "3", "descricao": "Ourivesaria" },
    { "codigo": "4", "descricao": "Corretivo de solo" },
    { "codigo": "5", "descricao": "Fabricação de cimento" },
    { "codigo": "6", "descricao": "Fabricação de cal" },
    { "codigo": "7", "descricao": "Revestimento" },
    { "codigo": "8", "descricao": "Metalurgia" },
    { "codigo": "9", "descricao": "Brita" },
    { "codigo": "10", "descricao": "Fertilizantes" },
    { "codigo": "11", "descricao": "Pigmento" },
    { "codigo": "12", "descricao": "Fabricação de vidro" },
    { "codigo": "13", "descricao": "Cerâmica vermelha" },
    { "codigo": "14", "descricao": "Pedra de talhe" },
    { "codigo": "15", "descricao": "Artesanato mineral" },
    { "codigo": "16", "descricao": "Pedra de coleção" },
    { "codigo": "17", "descricao": "Pedra decorativa" },
    { "codigo": "18", "descricao": "Energético" },
    { "codigo": "19", "descricao": "Balneoterapia" },
    { "codigo": "20", "descricao": "Engarrafamento" },
    { "codigo": "21", "descricao": "Industrial" },
    { "codigo": "22", "descricao": "Gema" },
    { "codigo": "23", "descricao": "Abrasivo" },
    { "codigo": "24", "descricao": "Insumo agrícola" }
  ]
}
```

---

## 🏢 Endpoints de Estabelecimentos (CNPJ/Receita Federal)

### 5. Buscar Empresas por CNAE e Localização

**Endpoint Proposto:**
```
POST /api/v1/estabelecimentos/buscar-por-cnae
```

**Descrição:** Consulta estabelecimentos da Receita Federal ativos em uma região geográfica, filtrados por códigos CNAE.

**Request Body:**
```json
{
  "latitude": -22.745,
  "longitude": -47.335,
  "municipio": "Campinas",
  "cnaes": ["0810-0/03", "0810-0/99", "2391-5/02"],
  "raioKm": 30,
  "distanciaInicialKm": 0,
  "limite": 20
}
```

**Parâmetros:**

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|-------------|-----------|
| `latitude` | `double` | ✅ | Latitude do ponto central |
| `longitude` | `double` | ✅ | Longitude do ponto central |
| `municipio` | `string` | ❌ | Nome do município de referência |
| `cnaes` | `string[]` | ✅ | Lista de códigos CNAE (com ou sem formatação) |
| `raioKm` | `double` | ❌ | Raio de busca em km (default: 20) |
| `distanciaInicialKm` | `double` | ❌ | Distância mínima em km |
| `limite` | `int` | ❌ | Quantidade máxima de resultados (default: 10) |

**Response (200 OK):**
```json
{
  "sucesso": true,
  "parametros": {
    "latitude": -22.745,
    "longitude": -47.335,
    "raioKm": 30,
    "cnaes": ["08100003", "08100099", "23915002"]
  },
  "totalEncontrado": 45,
  "totalRetornado": 10,
  "estabelecimentos": [
    {
      "id": "12345678000199",
      "cnpj": "12.345.678/0001-99",
      "razaoSocial": "EMPRESA AREIA E BRITA LTDA",
      "nomeFantasia": "AREIAS CAMPINAS",
      "matrizFilial": "Matriz",
      "cnaeFiscalPrincipal": "0810-0/03",
      "cnaeFiscalSecundaria": ["2391-5/02", "4663-0/00"],
      "endereco": {
        "tipoLogradouro": "ROD",
        "logradouro": "CAMPINAS MOGI",
        "numero": "KM 10",
        "complemento": "LADO DIREITO",
        "bairro": "ZONA RURAL",
        "municipio": "CAMPINAS",
        "uf": "SP",
        "cep": "13000-000"
      },
      "contato": {
        "telefone1": "(19) 3333-4444",
        "telefone2": "(19) 99999-8888",
        "email": "contato@areias.com.br"
      },
      "distanciaKm": 5.23
    }
  ]
}
```

---

### 6. Buscar Estabelecimento por CNPJ

**Endpoint Proposto:**
```
GET /api/v1/estabelecimentos/{cnpj}
```

**Exemplo:**
```
GET /api/v1/estabelecimentos/12345678000199
```

**Response (200 OK):**
```json
{
  "sucesso": true,
  "estabelecimento": {
    "id": "12345678000199",
    "cnpj": "12.345.678/0001-99",
    "cnpjBasico": "12345678",
    "cnpjOrdem": "0001",
    "cnpjDv": "99",
    "razaoSocial": "EMPRESA AREIA E BRITA LTDA",
    "nomeFantasia": "AREIAS CAMPINAS",
    "matrizFilial": "Matriz",
    "situacaoCadastral": {
      "codigo": "02",
      "descricao": "Ativa"
    },
    "dataSituacaoCadastral": "2010-05-15",
    "motivoSituacaoCadastral": {
      "codigo": "00",
      "descricao": "Ausência de Motivo"
    },
    "naturezaJuridica": {
      "codigo": "2062",
      "descricao": "Sociedade Empresária Limitada"
    },
    "qualificacaoResponsavel": {
      "codigo": "49",
      "descricao": "Sócio-Administrador"
    },
    "capitalSocial": 500000.00,
    "porte": {
      "codigo": "03",
      "descricao": "Empresa de Pequeno Porte"
    },
    "dataInicioAtividade": "2010-01-10",
    "cnaeFiscalPrincipal": {
      "codigo": "0810-0/03",
      "descricao": "Extração de granito e beneficiamento associado"
    },
    "cnaeFiscalSecundaria": [
      {
        "codigo": "2391-5/02",
        "descricao": "Aparelhamento de pedras para construção"
      }
    ],
    "endereco": {
      "tipoLogradouro": "ROD",
      "logradouro": "CAMPINAS MOGI",
      "numero": "KM 10",
      "complemento": "LADO DIREITO",
      "bairro": "ZONA RURAL",
      "municipio": "CAMPINAS",
      "uf": "SP",
      "cep": "13000-000"
    },
    "contato": {
      "ddd1": "19",
      "telefone1": "33334444",
      "ddd2": "19",
      "telefone2": "999998888",
      "email": "contato@areias.com.br"
    },
    "localizacao": {
      "latitude": -22.830,
      "longitude": -47.050
    },
    "opcaoSimples": "N",
    "opcaoMEI": "N"
  }
}
```

---

## 🗺️ Endpoints de Municípios (IBGE)

### 7. Buscar Município por Nome

**Endpoint Proposto:**
```
GET /api/v1/municipios/buscar
```

**Query Parameters:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|-------------|-----------|
| `nome` | `string` | ✅ | Nome do município |
| `uf` | `string` | ❌ | Sigla do estado (2 caracteres) ou nome completo |

**Exemplo:**
```
GET /api/v1/municipios/buscar?nome=Campinas&uf=SP
```

**Response (200 OK):**
```json
{
  "sucesso": true,
  "fonte": "IBGE (Instituto Brasileiro de Geografia e Estatística)",
  "totalEncontrado": 1,
  "municipios": [
    {
      "codigoIBGE": "3509502",
      "nome": "Campinas",
      "uf": "SP",
      "nomeEstado": "São Paulo",
      "capital": false,
      "ddd": "19",
      "fusoHorario": "America/Sao_Paulo",
      "areaKm2": 795.678,
      "localizacao": {
        "latitude": -22.9053,
        "longitude": -47.0608
      },
      "centroide": {
        "latitude": -22.9053,
        "longitude": -47.0608
      }
    }
  ]
}
```

---

### 8. Obter Município por Código IBGE

**Endpoint Proposto:**
```
GET /api/v1/municipios/{codigoIBGE}
```

**Exemplo:**
```
GET /api/v1/municipios/3509502
```

---

## 📚 Endpoints de CNAE

### 9. Buscar CNAEs

**Endpoint Proposto:**
```
GET /api/v1/cnaes/buscar
```

**Query Parameters:**

| Parâmetro | Tipo | Descrição |
|-----------|------|-----------|
| `termo` | `string` | Termo de busca na descrição |
| `secao` | `string` | Filtrar por seção CNAE |
| `divisao` | `string` | Filtrar por divisão CNAE |

**Exemplo:**
```
GET /api/v1/cnaes/buscar?termo=extração+areia
```

**Response (200 OK):**
```json
{
  "sucesso": true,
  "totalEncontrado": 5,
  "cnaes": [
    {
      "id": "08100003",
      "codigo": "0810-0/03",
      "secao": "B",
      "descricaoSecao": "Indústrias Extrativas",
      "divisao": "08",
      "descricaoDivisao": "Extração de minerais não-metálicos",
      "grupo": "08.1",
      "descricaoGrupo": "Extração de pedra, areia e argila",
      "classe": "08.10-0",
      "descricaoClasse": "Extração de pedra, areia e argila",
      "subclasse": "0810-0/03",
      "descricaoSubclasse": "Extração de granito e beneficiamento associado",
      "denominacao": "Extração de granito e beneficiamento associado"
    }
  ]
}
```

---

## 🤖 Endpoints de Chat/IA (Opcional)

### 10. Chat com Assistente IA

**Endpoint Proposto:**
```
POST /api/v1/chat/mensagem
```

**Descrição:** Envia uma mensagem em linguagem natural para o assistente IA processar consultas.

**Request Body:**
```json
{
  "conversationId": "guid-opcional-para-manter-contexto",
  "mensagem": "Encontre jazidas de areia em um raio de 50km de Campinas-SP"
}
```

**Response (200 OK):**
```json
{
  "sucesso": true,
  "conversationId": "abc123-guid",
  "resposta": {
    "texto": "Encontrei 15 jazidas de areia em um raio de 50km de Campinas-SP...",
    "markdown": "## Jazidas Encontradas\n\n...",
    "dados": {
      "tipo": "jazidas",
      "resultados": [...]
    },
    "acoes": [
      {
        "tipo": "centralizar_mapa",
        "latitude": -22.9053,
        "longitude": -47.0608,
        "zoom": 10
      }
    ]
  }
}
```

---

## 🔐 Autenticação

**Recomendação:** Implementar autenticação via JWT ou OAuth 2.0.

**Header de Autenticação:**
```
Authorization: Bearer <token>
```

---

## ⚠️ Códigos de Erro

| Código | Descrição |
|--------|-----------|
| `400` | Parâmetros inválidos |
| `401` | Não autenticado |
| `403` | Acesso negado |
| `404` | Recurso não encontrado |
| `429` | Rate limit excedido |
| `500` | Erro interno do servidor |
| `503` | Serviço indisponível (OpenSearch offline) |

**Formato de Erro:**
```json
{
  "sucesso": false,
  "erro": {
    "codigo": "VALIDATION_ERROR",
    "mensagem": "O campo 'latitude' é obrigatório",
    "detalhes": [
      {
        "campo": "latitude",
        "mensagem": "Valor não pode ser nulo"
      }
    ]
  }
}
```

---

## 📊 Principais Substâncias para Construção Civil

| Código | Substância | Uso Principal |
|--------|------------|---------------|
| `200200` | AREIA | Construção civil, concreto |
| `200600` | CASCALHO | Pavimentação, concreto |
| `706100` | GRANITO | Revestimento, brita |
| `200400` | BASALTO | Brita, pavimentação |
| `702400` | CALCÁRIO | Cimento, cal |
| `200300` | ARGILA | Cerâmica, tijolos |
| `711700` | QUARTZITO | Revestimento |

---

## 🔗 CNAEs Relevantes para Mineração

| Código | Descrição |
|--------|-----------|
| `0810-0/01` | Extração de ardósia e beneficiamento |
| `0810-0/02` | Extração de granito e beneficiamento |
| `0810-0/03` | Extração de mármore e beneficiamento |
| `0810-0/04` | Extração de calcário e dolomita |
| `0810-0/05` | Extração de gesso e caulim |
| `0810-0/06` | Extração de areia, cascalho ou pedregulho |
| `0810-0/07` | Extração de argila e beneficiamento |
| `0810-0/08` | Extração de saibro e beneficiamento |
| `0810-0/99` | Extração e beneficiamento de outros minerais não-metálicos |
| `2391-5/01` | Britamento de pedras (brita) |
| `2391-5/02` | Aparelhamento de pedras para construção |
| `2391-5/03` | Aparelhamento de placas e execução de trabalhos em mármore, granito |
| `4663-0/00` | Comércio atacadista de máquinas e equipamentos para mineração |

---

## 📝 Notas de Implementação

### Configurações Necessárias (appsettings.json)
```json
{
  "OpenSearch": {
    "Endpoint": "https://search-xxx.sa-east-1.es.amazonaws.com",
    "Username": "${OPENSEARCH_USERNAME}",
    "Password": "${OPENSEARCH_PASSWORD}"
  },
  "AzureOpenAI": {
    "Endpoint": "https://xxx.openai.azure.com/",
    "ApiKey": "${AZURE_OPENAI_KEY}",
    "DeploymentName": "gpt-4"
  }
}
```

### Índices OpenSearch

**Estrutura do índice `anm_jazidas`:**
```json
{
  "mappings": {
    "properties": {
      "dsprocesso": { "type": "keyword" },
      "localizacao": { "type": "geo_point" },
      "substancias": { "type": "keyword" },
      "usos": { "type": "keyword" },
      "fase": { "type": "keyword" },
      "uf": { "type": "keyword" }
    }
  }
}
```

**Estrutura do índice `rfb_estabelecimentos`:**
```json
{
  "mappings": {
    "properties": {
      "id": { "type": "keyword" },
      "localizacao": { "type": "geo_point" },
      "cnae_fiscal_principal": { "type": "keyword" },
      "cnae_fiscal_secundaria": { "type": "keyword" },
      "razao_social": { "type": "text" },
      "uf": { "type": "keyword" }
    }
  }
}
```

---

## 🚀 Próximos Passos para Implementação

1. **Criar projeto ASP.NET Core Web API**
2. **Configurar autenticação JWT**
3. **Mover credenciais para Azure Key Vault ou variáveis de ambiente**
4. **Implementar Controllers com os endpoints documentados**
5. **Adicionar Swagger/OpenAPI para documentação interativa**
6. **Configurar CORS para o novo frontend**
7. **Implementar rate limiting**
8. **Adicionar logging estruturado (Serilog)**
9. **Configurar health checks para OpenSearch**

---

## 📚 Referências

- [Documentação OpenSearch](https://opensearch.org/docs/latest/)
- [Dados Abertos ANM](https://dados.anm.gov.br/)
- [Dados Abertos CNPJ](https://dados.rfb.gov.br/)
- [IBGE Cidades](https://servicodados.ibge.gov.br/api/docs)

