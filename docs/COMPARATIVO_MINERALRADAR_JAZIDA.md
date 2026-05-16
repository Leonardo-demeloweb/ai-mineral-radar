# Comparativo: MineralRadar × Jazida.com

**Documento gerado em:** 05 de maio de 2026  
**Contexto:** Análise competitiva para o planejamento do MineralRadar — plataforma de inteligência para mineração estratégica e minerais críticos.

---

## 1. Posicionamento Estratégico


| Dimensão                    | MineralRadar                                                                              | Jazida.com                                                               |
| --------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| **Propósito central**       | Inteligência mineral estratégica: processos ANM, minerais críticos, due diligence e M&A  | Gerir a cadeia legal e burocrática de direitos minerários                |
| **Persona principal**       | Geólogo, explorador mineral, analista de M&A, gestor de compliance minerário             | Minerador, prospector, consultor mineral, advogado                       |
| **Lógica de negócio**       | "Qual é o potencial desta área? Quem controla? Há restrições? Qual o valor de mercado?"  | "Para este processo ANM, qual é o prazo, a obrigação e o próximo passo?" |
| **Diferencial tecnológico** | IA conversacional + minerais estratégicos + cross-index ANM↔RFB + logística mineral      | Automação legal + alertas diários + petições automáticas                 |
| **Maturidade de mercado**   | Em desenvolvimento (v1)                                                                   | +600 empresas clientes, produto maduro                                   |
| **Stack tecnológico**       | FastAPI + LangGraph + OpenSearch + React + MapLibre                                       | SaaS web (stack não público)                                             |


---

## 2. Funcionalidades — Mapa Detalhado

### 2.1 Exploração e Consulta de Processos ANM


| Funcionalidade                                                   | MineralRadar                        | Jazida.com                      |
| ---------------------------------------------------------------- | ----------------------------------- | ------------------------------- |
| Busca por substância + raio geográfico                           | ✅ (k-NN semântico + geo_distance)   | ✅ (buffer geográfico)           |
| Busca por empresa / CNPJ / titular                               | ✅ (cross-index ANM ↔ RFB)           | ✅                               |
| Ficha completa do processo (fases, eventos, títulos, sócios)     | ✅ (`detalhes_processo`)             | ✅                               |
| Busca por polígono desenhado                                     | ✅ (`jazidas_por_poligono`)          | ✅                               |
| Visualização no mapa com polígonos geo_shape                     | ✅ (MapLibre)                        | ✅                               |
| Verificação de vigência de substância                            | ✅ (`verificar_vigencia_substancia`) | Implícito no monitoramento      |
| Consulta processos inativos / histórico                          | Parcial (campo `btAtivo` no índice) | ✅ (ativo e inativo explícito)   |
| Filtro por fase ANM (fases reais)                                | Parcial (4 buckets agrupados)       | ✅ (fases reais do fluxo ANM)    |
| Busca em áreas livres (sem processo)                             | ❌                                   | ✅ ("Encontrar áreas livres")    |
| Editais de disponibilidade e leilões ANM                         | ❌                                   | ✅ (+73.000 áreas identificadas) |
| Desenhar requerimento de pesquisa (polígono exportável para ANM) | ❌                                   | ✅                               |


### 2.2 Monitoramento e Alertas


| Funcionalidade                                   | MineralRadar   | Jazida.com     |
| ------------------------------------------------ | -------------- | -------------- |
| Monitoramento de prazos do processo ANM          | ❌              | ✅ (tempo real) |
| Alertas diários por e-mail (prazos e pendências) | ❌              | ✅              |
| Acompanhamento de movimentações no SEI           | ❌              | ✅              |
| Leitura automática do Diário Oficial (DOU)       | ❌              | ✅              |
| Notificação de pendências com risco de multa     | ❌              | ✅              |
| Gestão de obrigações do ciclo de lavra           | ❌              | ✅              |


### 2.3 Inteligência Geográfica e Geológica


| Funcionalidade                                    | MineralRadar                                | Jazida.com                      |
| ------------------------------------------------- | ------------------------------------------- | ------------------------------- |
| Mapa interativo com polígonos de processos ANM    | ✅                                           | ✅                               |
| Isócrona de acesso (tempo de viagem truck/car)    | ✅ (Azure Maps)                              | ❌                               |
| Cálculo de rota de frete (origem → destino)       | ✅ (Azure Maps + custo estimado)             | ❌                               |
| Comparação de N rotas em paralelo                 | ✅                                           | ❌                               |
| Camadas SICAR (propriedades rurais)               | ❌                                           | ✅ (+6,8M propriedades mapeadas) |
| Camadas de áreas de proteção ambiental            | ❌                                           | ✅                               |
| Ocorrências minerais (CPRM / SGM)                 | ❌                                           | ✅                               |
| Mapas geológicos                                  | ❌                                           | ✅ (básico)                      |
| Upload de arquivos geoespaciais (KML / Shapefile) | Parcial (schema definido, não implementado) | ✅                               |
| Identificação de sobreposições SICAR × processo   | ❌                                           | ✅                               |


### 2.4 Gestão Jurídico-Ambiental


| Funcionalidade                                       | MineralRadar                                    | Jazida.com |
| ---------------------------------------------------- | ----------------------------------------------- | ---------- |
| Módulo ambiental (LP, LI, LO, condicionantes, taxas) | ❌                                               | ✅          |
| Vínculo processo minerário ↔ licença ambiental       | ❌                                               | ✅          |
| Módulo de contratos com superficiários               | ❌                                               | ✅          |
| Gestão de acordos de JV / confidencialidade          | ❌                                               | ✅          |
| Petições automáticas para ANM (PDF em 1 clique)      | ❌                                               | ✅          |
| Armazenamento de documentos por processo             | Parcial (schema `ArquivoKML`, não implementado) | ✅          |


### 2.5 Inteligência Artificial e Automação


| Funcionalidade                                              | MineralRadar                         | Jazida.com                                |
| ----------------------------------------------------------- | ------------------------------------ | ----------------------------------------- |
| Agente IA conversacional (chat em linguagem natural)        | ✅ (LangGraph + GPT-4o)               | Parcial ("Gestão Inteligente de Ofícios") |
| Resolução semântica de substância (busca por k-NN)          | ✅                                    | ❌ (busca textual)                         |
| Busca por isócrona em linguagem natural ("dentro de X min") | ✅                                    | ❌                                         |
| Cross-link automático ANM ↔ CNPJ (empresa real do titular)  | ✅                                    | Parcial                                   |
| Análise de rede societária (sócios do titular)              | ✅ (`detalhes_empresa` nested socios) | ❌                                         |
| Memória de sessão de longo prazo (Redis + Mongo + LLM)      | ✅                                    | ❌                                         |
| Streaming de resposta em tempo real (SSE)                   | ✅                                    | ❌                                         |
| Leitura automática e inteligente de ofícios ANM             | ❌                                    | ✅ (módulo dedicado com IA)                |


### 2.6 Análise de Mercado e Concorrência


| Funcionalidade                                      | MineralRadar         | Jazida.com                         |
| --------------------------------------------------- | -------------------- | ---------------------------------- |
| Identificar concorrentes por área geográfica        | ❌                    | ✅ ("Oportunidades e Concorrentes") |
| Identificar oportunidades de negócio em regiões     | Parcial (via chat)   | ✅ (módulo dedicado)                |
| Dashboard analítico de processos e taxas            | ❌ (tela vazia)       | ✅                                  |
| Filtro por ações / status processuais recentes      | ❌                    | ✅ (lançado recentemente)           |
| Identificar empresas produzindo minérios por região | Parcial (via agente) | ✅ (+5.600 empresas mapeadas)       |


---

## 3. Síntese — O que cada sistema faz melhor

### MineralRadar é superior em:

- Inteligência geográfica de **logística** (isócrona de caminhão, rota, custo de frete, comparação de N destinos)
- **IA conversacional** — o usuário pergunta em linguagem natural e o agente orquestra múltiplas fontes simultaneamente
- Conexão **ANM ↔ RFB ↔ IBGE** num único pipeline semântico com resolução por embeddings
- Identificação da **empresa real** por trás do processo (CNPJ, sócios, porte, CNAEs)
- **Infraestrutura técnica** moderna e extensível (LangGraph, MCP, OpenSearch vetorial, streaming SSE)
- Capacidade de **escalar novos domínios** sem reescrever — apenas novos MCPs e novas rotas no agente

### Jazida.com é superior em:

- **Gestão do ciclo de vida legal** do processo minerário (prazos, obrigações, multas, fases reais)
- **Alertas automáticos diários** (DOU, SEI, cadastro mineiro) — produto maduro em produção
- **Petições automáticas** geradas para a ANM em PDF
- **Módulo ambiental** completo (licenças, condicionantes, vínculo com o processo minerário)
- **Módulo de contratos** com superficiários e sobreposição SICAR
- **Exploração prospectiva** (áreas livres, leilões ANM, camadas geológicas CPRM, ocorrências minerais)
- **Produto consolidado** com 600+ clientes e dados próprios (ex: 73.000 áreas mapeadas)

---

## 4. Gaps Críticos — Minerais Estratégicos e Terras Raras

Nenhum dos dois sistemas atende plenamente ao domínio de **mineração estratégica / terras raras**:


| Gap                                                                                  | MineralRadar   | Jazida.com                     |
| ------------------------------------------------------------------------------------ | -------------- | ------------------------------ |
| Minerais estratégicos como classe dedicada (TR, Li, Nb, Co, grafita natural, urânio) | ❌              | ❌                              |
| Dados geológicos profundos (províncias, tipologia de depósito, mineralogia, idade)   | ❌              | Parcial (camadas CPRM básicas) |
| Recursos e reservas minerais (JORC / CRIRSCO — medido / indicado / inferido)         | ❌              | ❌                              |
| CFEM (Compensação Financeira por Exploração Mineral)                                 | ❌              | ❌                              |
| PAE / Relatório Final de Pesquisa (documentos ANM por processo)                      | ❌              | Parcial                        |
| Furos de sondagem e dados de exploração (drill holes, assays)                        | ❌              | ❌                              |
| Sobreposição com Terras Indígenas, UCs, faixas de fronteira                          | ❌              | Parcial (áreas de proteção)    |
| Cadeia de suprimento global de terras raras (contexto China / REE)                   | ❌              | ❌                              |
| Ciclo de capital de junior miners (B3, TSX, ASX, M&A)                                | ❌              | ❌                              |
| Modelagem de fases ANM como ciclo de vida completo com transições e prazos legais    | Parcial        | ✅                              |
| Alertas de vencimento de autorização de pesquisa (3 anos, prorrogável)               | ❌              | ✅                              |


---

## 5. Visão do MineralRadar — Diferencial Competitivo

### 5.1 Conceito

Construir o MineralRadar como plataforma independente focada em **mineração estratégica e terras raras**, combinando:

- IA conversacional, logística mineral e cross-index ANM↔RFB
- Monitoramento legal, alertas e ciclo de vida do processo (capacidades que o Jazida entrega)
- Uma camada de inteligência que **nenhum dos dois tem** (minerais críticos, geologia estratégica, supply chain global de TR)

### 5.2 Arquitetura base do MineralRadar


| Componente                                          | Situação                                                    |
| --------------------------------------------------- | ----------------------------------------------------------- |
| MCP Servers (Jazidas, Empresas, Geo)                | Construídos do zero — protocolo MCP open-source (MIT)       |
| LangGraph + agente IA conversacional                | Novo — grafo, router, system prompt e rotas originais       |
| OpenSearch (cluster próprio)                        | ETL e índices originais — `anm_processos_v001` etc.         |
| Frontend React + MapLibre + workspace + chat        | Construído do zero — bibliotecas open-source                |
| Módulo projetos / análises de exploração            | Novo — entidades `Projeto` e `Analise` (não obras/estudos)  |
| Azure Maps (isócrona, rota, geocoding)              | API de terceiro (Microsoft) — integração direta via SDK     |


### 5.3 O que construir novo

#### Módulos de alto impacto (curto prazo):

1. **Classificador de Minerais Estratégicos** — camada sobre dados ANM ingeridos via ETL próprio que identifica e prioriza substâncias críticas (17 terras raras, lítio, nióbio, tântalo, cobalto, grafita natural, urânio, vanádio)
2. **Novo system prompt** — persona de geólogo/explorador, vocabulário mineral estratégico, sem a restrição atual de "fora do escopo: cotações"
3. **Novas rotas no agente LangGraph** — `terras_raras`, `prospeccao`, `licenciamento_ambiental`

#### Módulos de médio prazo (novos ETLs e MCPs):

1. **MCP Monitoramento Legal** — leitura DOU, alertas de prazo, integração SEI (espelhar o que o Jazida faz)
2. **MCP Ambiental** — sobreposições com TIs (FUNAI/SIASI), UCs (CNUC/IBAMA), SICAR, licenças estaduais
3. **MCP CFEM** — ingestão dos dados públicos de compensação financeira da ANM (disponível via SICOM/ANM)
4. **ETL PAE/RFP** — ingestão de Planos de Aproveitamento Econômico e Relatórios Finais de Pesquisa disponíveis no portal ANM

#### Módulos de longo prazo (diferencial competitivo):

1. **Módulo de Prospecção** — áreas livres, mapa de potencial geológico, sobreposição com ocorrências CPRM
2. **Inteligência de Mercado Global** — cadeia de suprimento de terras raras, preços internacionais, políticas (IRA/USA, CMRD/Europa, regulação brasileira)
3. **Módulo de Due Diligence** — agregação de dados para M&A: CFEM histórico, histórico de fases, estrutura societária profunda, licenças, passivos ambientais

### 5.4 Fontes públicas disponíveis para conexão (Brasil)


| Fonte                            | Dado                                                 | Disponibilidade                               |
| -------------------------------- | ---------------------------------------------------- | --------------------------------------------- |
| ANM — Cadastro Mineiro (SIGMINE) | Processos, polígonos, fases, substâncias             | API pública + download bulk                   |
| ANM — CFEM                       | Compensação financeira por substância/município      | API pública SICOM                             |
| ANM — DOU                        | Publicações de atos minerários                       | RSS + API DOU                                 |
| ANM — SEI                        | Movimentações processuais                            | Consulta web (necessita scraping ou parceria) |
| CPRM / SGB                       | Ocorrências minerais, mapas geológicos, furos SIAGAS | Serviços WMS/WFS públicos                     |
| IBAMA — CNUC                     | Unidades de conservação (polígonos)                  | API pública / GeoServer                       |
| FUNAI — SIASI                    | Terras indígenas homologadas e em processo           | Download bulk GeoJSON                         |
| INCRA — SIGEF                    | Imóveis rurais cadastrados                           | WFS público                                   |
| MMA — SICAR                      | Cadastro Ambiental Rural                             | API pública                                   |
| Receita Federal                  | CNPJ, sócios, capital social                         | Download bulk mensal                          |
| IBGE                             | Municípios, mesorregiões, biomas                     | API IBGE + WFS                                |
| B3                               | Empresas listadas no setor de mineração              | API B3 (restrita, pode usar scraping)         |


---

## 6. Fluxo de Trabalho por Persona

### Fluxo típico (plataformas de suprimentos convencionais)

```
Obra georreferenciada
    → Criar estudo (substância buscada)
    → Agente IA busca jazidas + empresas no raio/isócrona
    → Plotar no mapa + calcular rotas
    → Favoritar fornecedores no estudo
    → (futuro) Enriquecer dossiê do fornecedor
```

### Fluxo proposto (MineralRadar — geólogo / explorador)

```
Alvo de exploração (ponto ou polígono)
    → Análise de potencial (substâncias na área, ocorrências CPRM, geologia)
    → Verificar disponibilidade legal (processos ANM, sobreposições TI/UC/SICAR)
    → Due diligence de processos vizinhos (titulares, fases, vigências, CFEMs)
    → Monitoramento contínuo (DOU, SEI, prazos, alertas)
    → Conexão com mercado (cadeia de suprimento, preços internacionais de TR)
```

---

## 7. Próximos Passos Sugeridos

1. **Reunião técnica** com o doutor em geologia para validar o fluxo proposto e priorizar módulos
2. **Explorar as APIs públicas** da ANM (SIGMINE, CFEM) e CPRM (WMS/WFS) — verificar qualidade e atualização dos dados
3. **Prototipar o classificador de minerais estratégicos** sobre os dados ANM ingeridos via ETL — investimento mínimo, impacto imediato
4. **Criar novo system prompt** de teste — persona de explorador mineral / geólogo, com novas instruções e sem restrições de cotação
5. **Definir prioridades de ETL** — quais fontes ANM ingerir primeiro (SIGMINE, CFEM, SCM)
6. **Formalizar especificação** (`docs/SPEC_MINERALRADAR.md`) após validação das prioridades

---

*Documento gerado como base de planejamento. Não representa compromisso de roadmap definido.*