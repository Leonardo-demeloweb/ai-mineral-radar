# MineralRadar - Documentação de Negócio

## 📋 Visão Geral

O **MineralRadar** (também referenciado como **SinergIA**) é uma plataforma de inteligência geoespacial e de negócios desenvolvida para o setor de **mineração e construção civil no Brasil**. O sistema integra múltiplas fontes de dados governamentais, permitindo consultas geográficas avançadas através de uma interface conversacional alimentada por Inteligência Artificial.

---

## 🎯 Propósito de Negócio

### Problema que Resolve

Empresas do setor de construção civil e mineração enfrentam desafios para:

-   Localizar jazidas minerais próximas a seus projetos
-   Identificar fornecedores de materiais (areia, brita, cascalho) em regiões específicas
-   Mapear a cadeia de suprimentos mineral em áreas geográficas delimitadas
-   Obter informações consolidadas sobre processos minerários e empresas do setor

### Solução Oferecida

O MineralRadar centraliza e correlaciona dados de múltiplas fontes governamentais, oferecendo:

-   **Busca geoespacial inteligente** de jazidas e empresas
-   **Interface conversacional** que permite consultas em linguagem natural
-   **Visualização geográfica** em mapas interativos
-   **Análise de proximidade** entre pontos de interesse e recursos minerais

---

## 🗂️ Fontes de Dados Integradas

### 1. ANM - Agência Nacional de Mineração

**O que é**: Órgão federal responsável pela gestão do patrimônio mineral brasileiro.

**Dados disponíveis**:

-   Processos minerários (autorizações, licenças, concessões)
-   Localização geográfica das jazidas (polígonos georreferenciados)
-   Titulares (empresas ou pessoas físicas detentoras dos direitos)
-   Substâncias minerais (areia, brita, cascalho, granito, calcário, etc.)
-   Usos permitidos (construção civil, pavimentação, etc.)
-   Fases do processo (pesquisa, lavra, licenciamento)
-   Eventos e histórico do processo

**Índice OpenSearch**: `anm_jazidas`

### 2. RFB - Receita Federal do Brasil

**O que é**: Base de dados do Cadastro Nacional de Pessoa Jurídica (CNPJ).

**Dados disponíveis**:

-   Razão social e nome fantasia
-   CNAE (Classificação Nacional de Atividades Econômicas)
-   Endereço completo com geolocalização
-   Situação cadastral (ativa, baixada, suspensa)
-   Dados de contato (telefone, email)
-   Sócios e quadro societário

**Índice OpenSearch**: `rfb_estabelecimentos`

### 3. IBGE - Instituto Brasileiro de Geografia e Estatística

**O que é**: Fonte oficial de dados geográficos e estatísticos do Brasil.

**Dados disponíveis**:

-   Coordenadas geográficas dos municípios brasileiros
-   Informações de UF (Unidade Federativa)
-   Identificação de capitais
-   Códigos oficiais IBGE

**Índice OpenSearch**: `ibge_municipios`

---

## 🔧 Funcionalidades Principais

### 1. Agente de Jazidas (AgenteJazidas)

**Propósito**: Localizar jazidas minerais próximas a um ponto geográfico.

**Parâmetros de busca**:
| Parâmetro | Descrição |
|-----------|-----------|
| Município/Cidade | Ponto central da busca |
| Raio (km) | Distância máxima de busca |
| Distância mínima (km) | Define anel de busca (exclui área interna) |
| Substâncias | Tipo de mineral (AREIA, CASCALHO, BRITA, GRANITO, etc.) |
| Usos | Finalidade (Construção civil, Pavimentação, etc.) |

**Exemplo de uso**:

> "Encontre jazidas de areia e cascalho em um raio de 50km de Campinas-SP"

**Informações retornadas**:

-   Número do processo ANM
-   Titular da jazida
-   Fase do processo (Concessão de Lavra, Licenciamento, etc.)
-   Área em hectares
-   Substâncias disponíveis
-   Distância do ponto central

### 2. Agente de Estabelecimentos (AgenteEstabelecimentos)

**Propósito**: Localizar empresas do setor mineral/construção próximas a um ponto.

**Parâmetros de busca**:
| Parâmetro | Descrição |
|-----------|-----------|
| Município/Cidade | Ponto central da busca |
| Raio (km) | Distância máxima de busca |
| CNAEs | Códigos de atividade econômica |
| Distância mínima (km) | Define anel de busca |

**CNAEs Relevantes para o setor**:

-   `0810-0/01` - Extração de pedra, areia e argila
-   `2391-5/01` - Britamento de pedras
-   `4744-0/01` - Comércio varejista de materiais de construção

**Exemplo de uso**:

> "Liste empresas de extração de areia em um raio de 30km de Sorocaba"

### 3. Visualização em Mapas (SinergiaMapas)

**Propósito**: Exibir resultados de buscas em mapas interativos.

**Funcionalidades**:

-   Marcadores com informações detalhadas (InfoWindow)
-   Polígonos das áreas de jazidas
-   Círculos indicando raio de busca
-   Anéis geográficos (busca em faixas de distância)
-   Navegação e zoom automático

### 4. Detalhes de Processos ANM

**Propósito**: Exibir informações completas de um processo minerário específico.

**Dados exibidos**:

-   Dados do processo (número, fase, área)
-   Titular e responsáveis
-   Municípios abrangidos
-   Substâncias e usos autorizados
-   Histórico de eventos
-   Documentos associados
-   Mapa da poligonal

---

## 🏗️ Arquitetura de Negócio

```
┌─────────────────────────────────────────────────────────────────┐
│                         USUÁRIO                                  │
│         (Engenheiro, Comprador, Analista de Suprimentos)        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    INTERFACE CONVERSACIONAL                      │
│              (Chat com IA - Azure OpenAI / GPT-4)               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     PLUGINS SEMANTIC KERNEL                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  ANMPlugin  │  │ CNPJPlugin  │  │ IBGEPlugin  │             │
│  │  (Jazidas)  │  │ (Empresas)  │  │(Municípios) │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  CAMADA DE BUSCA (OpenSearch)                    │
│  ┌───────────────┐  ┌────────────────────┐  ┌────────────────┐ │
│  │  anm_jazidas  │  │ rfb_estabelecimentos│  │ibge_municipios │ │
│  │  (Processos   │  │    (CNPJs com       │  │  (Lat/Long de  │ │
│  │   minerários) │  │    geolocalização)  │  │   municípios)  │ │
│  └───────────────┘  └────────────────────┘  └────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   FONTES DE DADOS ORIGINAIS                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │     ANM      │  │     RFB      │  │     IBGE     │          │
│  │ (dadosabertos│  │  (CNPJ       │  │ (Municípios) │          │
│  │  .anm.gov.br)│  │  Nacional)   │  │              │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📊 Casos de Uso de Negócio

### Caso 1: Planejamento de Obra

**Ator**: Engenheiro de Suprimentos

**Cenário**: Uma construtora está planejando uma obra em Ribeirão Preto-SP e precisa identificar fornecedores de areia e brita na região.

**Fluxo**:

1. Acessa o Agente de Jazidas
2. Consulta: "Encontre jazidas de areia e brita em um raio de 40km de Ribeirão Preto"
3. Sistema retorna lista de jazidas ordenadas por distância
4. Visualiza no mapa a localização das jazidas
5. Acessa detalhes do processo ANM para verificar se a jazida está em fase de exploração ativa
6. Utiliza o Agente de Estabelecimentos para encontrar empresas que comercializam esses materiais

### Caso 2: Due Diligence de Fornecedor

**Ator**: Analista de Compliance

**Cenário**: Verificar a regularidade de um fornecedor de agregados minerais.

**Fluxo**:

1. Consulta o CNPJ do fornecedor
2. Sistema retorna dados cadastrais completos (RFB)
3. Identifica os CNAEs e verifica compatibilidade com a atividade
4. Verifica se a empresa possui processos minerários associados (ANM)
5. Analisa a situação cadastral e histórico

### Caso 3: Análise de Mercado Regional

**Ator**: Analista de Inteligência de Mercado

**Cenário**: Mapear a oferta de materiais de construção em uma microrregião.

**Fluxo**:

1. Define a área de interesse (município + raio)
2. Consulta jazidas disponíveis por tipo de substância
3. Mapeia empresas do setor por CNAE
4. Gera visão consolidada da cadeia de suprimentos regional

---

## 📈 Métricas e Indicadores

### Dados Disponíveis para Análise

**Jazidas (ANM)**:

-   Total de jazidas por substância
-   Distribuição por fase do processo
-   Área total em hectares por região
-   Concentração de titulares

**Empresas (RFB)**:

-   Quantidade de empresas por CNAE
-   Distribuição geográfica
-   Situação cadastral (ativas vs. inativas)

---

## 🔐 Aspectos de Segurança e Compliance

### Fontes de Dados

-   Todos os dados são provenientes de **fontes públicas e oficiais**
-   Dados da ANM: Portal de Dados Abertos (dadosabertos.anm.gov.br)
-   Dados da RFB: Base pública de CNPJ

### Tratamento de Dados Sensíveis

-   CPFs de pessoas físicas titulares são tratados com mascaramento
-   Dados de contato (telefone, email) são exibidos conforme disponibilidade pública

---

## 🗺️ Glossário de Termos

| Termo                  | Definição                                                                         |
| ---------------------- | --------------------------------------------------------------------------------- |
| **Jazida**             | Depósito natural de substância mineral útil                                       |
| **Processo Minerário** | Procedimento administrativo na ANM para autorização de atividade mineral          |
| **Titular**            | Pessoa física ou jurídica detentora dos direitos minerários                       |
| **Fase do Processo**   | Estágio do processo minerário (Pesquisa, Licenciamento, Concessão de Lavra, etc.) |
| **Substância**         | Tipo de mineral (areia, brita, cascalho, granito, etc.)                           |
| **CNAE**               | Classificação Nacional de Atividades Econômicas                                   |
| **Concessão de Lavra** | Autorização definitiva para exploração mineral                                    |
| **Licenciamento**      | Regime simplificado para minerais de uso imediato na construção civil             |
| **Polígono/Poligonal** | Área georreferenciada que delimita a jazida                                       |

---

## 🔄 Ciclo de Atualização dos Dados

| Fonte             | Frequência  | Processo                            |
| ----------------- | ----------- | ----------------------------------- |
| ANM - Processos   | Sob demanda | Importação via API de Dados Abertos |
| ANM - Shapes      | Sob demanda | Download de arquivos shapefile      |
| RFB - CNPJ        | Mensal      | Importação de base pública          |
| IBGE - Municípios | Anual       | Importação de dados geográficos     |

---

## 📞 Integrações e APIs

### OpenSearch (AWS)

-   **Endpoint**: Amazon OpenSearch Service (sa-east-1)
-   **Índices**:
    -   `anm_jazidas` - Processos minerários com geolocalização
    -   `rfb_estabelecimentos` - Empresas com geolocalização
    -   `ibge_municipios` - Municípios brasileiros

### Azure OpenAI

-   **Modelo**: GPT-4o
-   **Uso**: Interface conversacional e processamento de linguagem natural

### Google Maps

-   **Uso**: Visualização de mapas, marcadores e polígonos

---

## 🎯 Público-Alvo

1. **Engenheiros de Suprimentos** - Identificação de fornecedores de agregados
2. **Compradores** - Cotação e análise de fornecedores
3. **Analistas de Compliance** - Verificação de regularidade de fornecedores
4. **Gestores de Projetos** - Planejamento logístico de materiais
5. **Analistas de Mercado** - Estudos de oferta e demanda regional

---

## 📝 Observações Finais

O MineralRadar representa uma ferramenta estratégica para otimização da cadeia de suprimentos do setor de construção civil e mineração no Brasil, oferecendo:

-   **Redução de tempo** na identificação de fornecedores
-   **Tomada de decisão** baseada em dados georreferenciados
-   **Conformidade** através de dados oficiais e atualizados
-   **Interface intuitiva** através de linguagem natural

O sistema é construído sobre a plataforma BlazorGPT, adaptada para as necessidades específicas do domínio mineral brasileiro.

