# MineralRadar — Whitepaper Executivo
**Versão 1.0 · Maio de 2026**

---

## Sumário

1. [O Problema](#1-o-problema)
2. [A Solução](#2-a-solução)
3. [Mercado e Contexto Global](#3-mercado-e-contexto-global)
4. [Arquitetura da Inteligência](#4-arquitetura-da-inteligência)
5. [Índices e Camada de Dados](#5-índices-e-camada-de-dados)
6. [Fluxo de Valor para o Usuário](#6-fluxo-de-valor-para-o-usuário)
7. [Módulos da Plataforma](#7-módulos-da-plataforma)
8. [Evolução da Plataforma](#8-evolução-da-plataforma)
9. [Modelo de Negócio](#9-modelo-de-negócio)
10. [Posicionamento Competitivo](#10-posicionamento-competitivo)
11. [Infraestrutura e Escala](#11-infraestrutura-e-escala)
12. [Conclusão](#conclusão)

---

## 1. O Problema

O Brasil é um dos países mais ricos em recursos minerais do planeta. Detém **98% das reservas mundiais de nióbio**, as terceiras maiores de terras raras e posições estratégicas em lítio, grafita, titânio e manganês — minerais no centro da transição energética global. Mas a riqueza vai muito além dos minerais críticos: areia, calcário, ferro, ouro, bauxita e fosfato movimentam bilhões de reais por ano e abastecem desde a construção civil até o agronegócio. No total, são **862 substâncias cadastradas** e **mais de 25 milhões de processos minerários** registrados na ANM.

Apesar dessa riqueza, a inteligência sobre esse ecossistema permanece **fragmentada, inacessível e analógica**:

- A ANM (Agência Nacional de Mineração) concentra **mais de 25 milhões de processos minerários** — de areia e calcário a nióbio e terras raras — distribuídos em arquivos de shapefile e sistemas legados de difícil consulta.
- Identificar o titular real de um processo, cruzar com CNPJ ativo, verificar restrições ambientais (TIs, UCs, CAR) e calcular a viabilidade logística de um depósito exige hoje semanas de trabalho manual entre geólogos, advogados e analistas de compliance — independente da substância.
- Não existe no mercado brasileiro uma plataforma que responda, em segundos, à pergunta central do setor:

> **"Qual é o real valor estratégico deste depósito, quem o controla, quais são os riscos jurídicos e ambientais, e como ele se conecta ao mercado global?"**

Essa lacuna afeta toda a cadeia mineral brasileira — do produtor de agregados para construção civil ao explorador de minerais críticos para exportação. Ela custa ao Brasil oportunidades de atração de capital, retarda decisões de licenciamento e deixa o mercado de fornecedores invisível para quem os precisa.

---

## 2. A Solução

**MineralRadar** é uma plataforma de inteligência mineral conversacional, geoespacial e estratégica.

Em vez de dashboards estáticos ou buscas tradicionais, o MineralRadar oferece um **agente de inteligência especializado** que compreende linguagem natural em português e entrega respostas síntese — cruzando em tempo real dados de mineração, empresas, logística, restrições ambientais e mercado internacional. A plataforma cobre **todos os processos minerários brasileiros**, de qualquer substância e fase, com o mesmo nível de profundidade analítica.

### Princípios Fundamentais

| Princípio | Descrição |
|-----------|-----------|
| **Conversacional** | O usuário pergunta em português como perguntaria a um analista sênior |
| **Contextual** | Cada resposta considera o projeto ativo, preferências e histórico do usuário |
| **Geoespacial** | Cada dado tem coordenada. Isócronas, rotas, polígonos e restrições são visualizados no mapa |
| **Verificável** | Toda informação tem fonte rastreável (ANM, CNPJ, IBAMA, FUNAI) |
| **Acionável** | O resultado de cada busca alimenta automaticamente uma análise de fornecedores salva no projeto |

---

## 3. Mercado e Contexto Global

### A corrida pelos minerais críticos — e o problema que vai além deles

A Agência Internacional de Energia (IEA) estima que a demanda por minerais críticos crescerá **400–600% até 2040** em cenários de transição energética acelerada. Lítio, cobalto, nióbio, terras raras e grafita estão no centro de disputas geopolíticas entre EUA, China, Europa e Brasil.

O Brasil, signatário do **Minerals Security Partnership (MSP)** com os EUA e em negociação com a União Europeia, posiciona-se como fornecedor estratégico preferencial — mas carece de ferramentas para mapear, qualificar e conectar sua cadeia mineral ao mercado comprador global.

Esse é o vetor de maior urgência, mas não é o único. **Toda** a atividade minerária brasileira — os 600 mil processos ativos que cobrem areia, brita, calcário, ferro, ouro, fosfato e dezenas de outras substâncias — enfrenta o mesmo deficit de inteligência: dados fragmentados, cruzamentos impossíveis e decisões tomadas sem informação consolidada.

### Oportunidade de mercado (Brasil)

- **+9.000 empresas** na cadeia mineral (mineração, beneficiamento, transporte, equipamentos, comércio)
- **+600.000 processos minerários ativos**, cobrindo **862 substâncias** em todas as regiões do país
- **R$ 12 bilhões/ano** em CFEM (Compensação Financeira pela Exploração de Recursos Minerais)
- Mercado de consultoria e due diligence mineral estimado em **R$ 800M–1,2B/ano**

### Usuários-alvo

| Perfil | Necessidade Principal |
|--------|----------------------|
| **Geólogo / Exploração** | Identificar processos por substância e região, cruzar com restrições, avaliar viabilidade |
| **Construção Civil e Infraestrutura** | Mapear cavas de areia, pedreiras de brita e jazidas de calcário próximas a obras |
| **Comprador Industrial** | Localizar fornecedores de qualquer insumo mineral em raio logístico específico |
| **M&A / Investidor** | Due diligence completa de ativos minerários: sócios, CFEM histórico, compliance |
| **Consultor Ambiental** | Verificar sobreposições com TIs, UCs, SICAR e autuações IBAMA por processo |
| **Trader / Exportador** | Conectar oferta nacional com demanda internacional por substância e NCM |
| **Gestor Público** | Monitorar concentração de titularidade, prazos SCM e arrecadação de CFEM por município |

---

## 4. Arquitetura da Inteligência

O MineralRadar opera sobre três camadas integradas:

```
┌─────────────────────────────────────────────────────────┐
│                   INTERFACE CONVERSACIONAL               │
│           Chat em PT-BR · Mapa Interativo · Workspace   │
└───────────────────────┬─────────────────────────────────┘
                        │ REST + SSE (streaming)
┌───────────────────────▼─────────────────────────────────┐
│                   AGENTE DE INTELIGÊNCIA                 │
│  Roteador de intenção → LangGraph → Ferramentas MCP      │
│  Memória de curto prazo (Redis) + longo prazo (MongoDB)  │
└──────┬──────────────────────┬──────────────────────┬─────┘
       │ MCP Jazidas          │ MCP Empresas         │ MCP Geo
┌──────▼──────┐    ┌──────────▼──────────┐  ┌────────▼────────┐
│ ANM · CFEM  │    │ CNPJ · CNAE · IBAMA │  │ Azure Maps      │
│ CPRM · SCM  │    │ Sócios · Autuações  │  │ Isócronas Rotas │
└──────┬──────┘    └──────────┬──────────┘  └────────┬────────┘
       │                      │                       │
┌──────▼──────────────────────▼───────────────────────▼─────┐
│                     CAMADA DE DADOS                         │
│   OpenSearch (busca híbrida) · MongoDB · Redis · PostGIS    │
└─────────────────────────────────────────────────────────────┘
```

### Roteamento de Intenção

O agente classifica automaticamente cada pergunta do usuário em uma das **8 rotas especializadas**:

| Rota | Exemplo de Pergunta |
|------|---------------------|
| `mineral` | "Quais são as jazidas de lítio ativas em Minas Gerais?" |
| `empresa` | "Mostre empresas de beneficiamento de nióbio próximas a Catalão" |
| `híbrido` | "Quem produz e quem compra grafita no Tocantins?" |
| `geo` | "Calcule a rota de caminhão da mina até o porto de Santos" |
| `mineral_em_isócrona` | "Que jazidas de cobalto existem a 4h de Manaus?" |
| `empresa_em_isócrona` | "Fornecedores de titânio a 6h de São Paulo" |
| `híbrido_em_isócrona` | "Toda a cadeia de terras raras acessível em 8h de Brasília" |
| `geral` | "Qual é o panorama do mercado de lítio no Brasil em 2026?" |

### Busca Híbrida

Cada consulta combina três dimensões de busca em paralelo:

1. **Semântica (k-NN)** — embeddings de 1.536 dimensões compreendem variações linguísticas ("terras raras" = "ETR" = "elementos do grupo das terras raras")
2. **Full-text (BM25)** — busca exata em nomes, CNPJ, siglas e denominações técnicas
3. **Geoespacial** — filtros por raio, polígono, isócrona ou município sobre geometrias reais (polígonos ANM, limites municipais IBGE)

---

## 5. Índices e Camada de Dados

### Universo de Dados Indexados

O MineralRadar consolida e normaliza dados de **12+ fontes públicas brasileiras e internacionais** em um cluster de busca otimizado para consultas em tempo real:

| Índice | Conteúdo | Escala |
|--------|----------|--------|
| `anm_processos` | Processos minerários (ativos + inativos), polígonos, substâncias, fases, titulares | **25+ milhões** de registros |
| `mr_empresas` | Empresas do domínio mineral pré-filtradas: CNPJ, CNAE, sócios, capital, geolocalização | **~350 mil** registros |
| `mr_cfem` | Série histórica de royalties por processo/empresa/município desde 2010 | **Milhões** de registros mensais |
| `ibge_municipios` | 5.631 municípios com polígonos de fronteira | 5.631 registros |
| `anm_substancias` | 862 substâncias minerais com embeddings semânticos | 862 registros |
| `rfb_cnae` | 2.394 atividades econômicas com embeddings | 2.394 registros |
| `mr_autuacoes` | Autos de infração IBAMA: multas, embargos, apreensões por CNPJ | **~55 mil** registros |
| `funai_tis` | Terras Indígenas homologadas, declaradas e em estudo | Cobertura nacional |
| `ibama_ucs` | Unidades de Conservação federais e estaduais | Cobertura nacional |
| `sicar_car` | Cadastro Ambiental Rural (propriedades rurais) | Cobertura nacional |
| `mr_cprm` (`mr_cprm_v001`) | Ocorrências minerais GeoBank SGB/CPRM — prospectividade sem processo ANM | **~36 mil** registros |
| `mr_geoquimica` (`mr_geoquimica_v001`) | Amostras geoquímicas CPRM — teores por analito (rocha + mineral/minério), nested `analises` | **~65 mil** amostras |
| `comexstat_mdic` | Exportações e importações por NCM mineral (ComexStat/MDIC) | Cobertura nacional |

### Capacidades dos Índices

**Busca semântica** — O índice de substâncias e CNAEs utiliza HNSW (Hierarchical Navigable Small World) com similaridade cosseno, permitindo que a pergunta "baterias de veículos elétricos" encontre automaticamente processos de lítio, grafita e cobalto sem que o usuário saiba os códigos ANM.

**Geometria real** — Polígonos ANM, limites de TIs, UCs e municípios são indexados como `geo_shape`, permitindo consultas de interseção, contenção e proximidade precisas — não apenas pontos centrais.

**Séries temporais CFEM** — O histórico de royalties permite calcular tendências de produção, identificar os maiores pagadores por substância/UF/período e construir rankings de relevância econômica real (não apenas declaratória).

**Cruzamento dinâmico de entidades** — A plataforma executa automaticamente a sequência: `Substância → Processo ANM → CNPJ titular → Empresa ativa → Sócios → Autuações IBAMA`, em um único fluxo de resposta ao usuário.

---

## 6. Fluxo de Valor para o Usuário

### Do chat à decisão em segundos

```
Usuário pergunta → Agente roteia → Ferramentas buscam em paralelo
        ↓
Resposta síntese + mapa atualizado + fornecedores salvos no projeto
        ↓
Análise estruturada exportável (PDF / API)
```

### Workspace de Projetos

Cada sessão de uso alimenta um **projeto persistente**:

- **Análises**: agrupamento de buscas por tema ou objetivo
- **Fornecedores**: lista automática de empresas e jazidas encontradas, deduplicada
- **Favoritos**: processos e empresas marcados para acompanhamento
- **Histórico de rotas**: logística calculada e salva para reanálise

O usuário não precisa reexportar dados nem refazer buscas — o contexto acumula ao longo do tempo.

---

## 7. Módulos da Plataforma

### 7.1 Inteligência Mineral (Jazidas)

- Localização de processos por substância, fase, UF, município ou polígono personalizado
- Verificação de vigência: validade real vs. prazo declarado (cálculo sobre SCM/SICOP)
- Histórico CFEM: produção real estimada por royalty pago, evolução temporal
- Sobreposição de restrições: TI, UC, CAR, APP — com nível de risco classificado
- Ocorrências CPRM: depósitos e indícios geológicos sem processo minerário associado
- Geoquímica CPRM: teores analíticos (Au, TR, Nb, etc.) em amostras de rocha e mineral/minério próximos a um ponto (`geoquimica_proxima`)

### 7.2 Inteligência Empresarial (Empresas)

- Busca semântica por atividade econômica + filtro geográfico
- Perfil completo: CNPJ, situação cadastral, capital social, CNAE principal e secundários
- Quadro societário com CPF/CNPJ de sócios e participação
- Risco ambiental: histórico IBAMA cruzado por CNPJ (multas, embargos, apreensões)
- Porte e relevância: ranking por capital social, CFEM pago, nº de processos titulados

### 7.3 Inteligência Geoespacial e Logística

- **Isócronas de tempo real**: área acessível em X horas de carro/caminhão a partir de qualquer ponto
- **Cálculo de rota** com detecção de trecho off-road e alertas de acessibilidade
- **Consulta dentro de isócrona**: "quais fornecedores de lítio estão a 6h de carro do porto de Paranaguá?"
- **Geocodificação bidirecional**: endereço → coordenada e coordenada → município/endereço

### 7.4 Inteligência de Mercado

- Exportações por NCM mineral: volume, valor FOB, destinos e tendência (ComexStat/MDIC)
- Preços de mercado de minerais industriais, metálicos e estratégicos (Metals-API / USGS / LME)
- Dados de empresas listadas na CVM/B3 com operações minerais
- Correlação entre preço internacional de commodities e atividade de processos ANM por substância

---

## 8. Evolução da Plataforma

O MineralRadar é concebido como uma plataforma em expansão contínua — cada nova camada de dados e inteligência amplifica o valor das anteriores. A arquitetura modular garante que novas fontes, módulos e integrações sejam incorporados sem ruptura para o usuário.

### Inteligência Ambiental e Administrativa

A dimensão de risco é central para qualquer decisão no setor mineral. A plataforma expande continuamente sua capacidade de cruzar dados de restrição e compliance:

- **Score de Risco Ambiental**: índice 0–10 por processo, combinando sobreposição com Terras Indígenas, Unidades de Conservação, Cadastro Ambiental Rural e histórico de autuações IBAMA
- **Alertas de prazo SCM/SICOP**: monitoramento proativo de vencimentos de direitos minerários, obrigações e pendências administrativas
- **Mapa de restrições integrado**: visualização simultânea de TI, UC, SICAR, CPRM e processos ANM em uma única camada geoespacial

### Inteligência de Mercado

A conexão entre o ativo geológico e o mercado comprador é o próximo salto de valor:

- **Fluxo de exportação por substância**: quem já exporta cada mineral, para onde, a que preço e em que volume (ComexStat/MDIC)
- **Preços de mercado em tempo real**: cotações de minerais industriais, metálicos e estratégicos integradas diretamente à análise de cada processo
- **Empresas listadas**: dados públicos de mineradoras na CVM/B3 — reservas declaradas, royalties pagos, guidance de produção

### Score de Atratividade Mineral (SAM)

O SAM é o índice proprietário do MineralRadar — um número único que combina oito dimensões de análise por processo ou região:

| Dimensão | O que mede |
|----------|------------|
| **Geologia** | Substância, ocorrências CPRM adjacentes, teores geoquímicos CPRM na área, reservas declaradas |
| **Produção** | Histórico CFEM, tendência de royalties, atividade real vs. declaratória |
| **Titularidade** | Situação do processo, vigência, concentração de titulares |
| **Compliance** | Autuações IBAMA, pendências SCM, regularidade cadastral |
| **Restrição** | Sobreposição com TI, UC, CAR, APP — nível de risco ambiental |
| **Logística** | Distância a portos, ferrovias, rodovias pavimentadas |
| **Mercado** | Preço internacional da substância, demanda global projetada |
| **Empresarial** | Capacidade financeira do titular, rede societária, outros ativos |

### Expansão Global

A arquitetura do MineralRadar é projetada para operar além das fronteiras brasileiras. A integração com bases internacionais como o USGS Mineral Resources Data System (MRDS) e o uso de sensoriamento remoto (Sentinel-2) para mapeamento espectral de afloramentos posicionam a plataforma como referência de inteligência mineral para toda a América do Sul.

---

## 9. Modelo de Negócio

O MineralRadar opera em modelo **SaaS B2B em três camadas**, complementado por receitas transacionais (API, relatórios sob demanda) e contratos enterprise (integração e white-label). A precificação foi calibrada contra referências reais do mercado brasileiro de inteligência setorial (Jazida.com, Cortex, Speedio, Bloomberg Terminal, Wood Mackenzie, S&P Capital IQ) e validada contra o ticket médio de consultoria mineral no Brasil — onde um único laudo de due diligence custa hoje entre **R$ 30 mil e R$ 150 mil** e leva semanas para ser entregue.

### 9.1 Planos de Assinatura

| Plano | ICP (Perfil Ideal) | Preço de Lista | Inclui |
|-------|---------------------|----------------|--------|
| **Explorador** | Geólogos autônomos, consultores individuais, escritórios pequenos, pesquisadores | **R$ 290 / mês** (R$ 2.990 / ano — economia de 14%) | 1 seat · busca conversacional ilimitada · até 5 projetos ativos · exportação PDF · 50 isócronas/mês |
| **Profissional** | PMEs de mineração, juniors, consultorias técnicas, traders, escritórios de advocacia minerária, M&A boutique | **R$ 1.890 / mês por seat** (R$ 18.900 / ano — economia de 17%) | Projetos ilimitados · alertas de prazo SCM · score de risco ambiental · 500 isócronas/mês · 1.000 chamadas API/mês · suporte prioritário |
| **Institucional** | Grandes mineradoras (Vale, CSN, CBA, Anglo American), bancos de investimento (Itaú BBA, BTG, XP), fundos de commodities, agências e governos estaduais | **A partir de R$ 9.500 / mês** (R$ 96k–R$ 180k / ano, conforme volume) | Seats ilimitados · API completa · white-label opcional · relatórios customizados · SLA contratual · onboarding dedicado · BI export (Power BI / Tableau) |

> **Âncoras de mercado:** o tier Explorador posiciona-se 20–40% acima do Jazida.com (R$ 200–400/mês) entregando ~10× mais funcionalidade. O Profissional alinha-se ao patamar do Cortex e do Speedio (R$ 1.500–3.000/mês por seat). O Institucional fica **40–60% abaixo** de um Bloomberg Terminal (R$ 144k/ano) ou de uma assinatura Wood Mackenzie Metals & Mining (USD 30–150k/ano), com escopo equivalente para o domínio mineral brasileiro.

### 9.2 Receitas Complementares

| Linha | Modelo | Pricing de Referência | Margem Esperada |
|-------|--------|------------------------|-----------------|
| **API de Dados** | Cobrança por volume mensal (índices ANM + CFEM + CNPJ minerador normalizados) | R$ 0,30 a R$ 1,80 por chamada · pacotes corporativos de **R$ 8k–R$ 40k / mês** | 85–92% |
| **Relatórios de Due Diligence** | Pay-per-report gerado pelo agente em < 5 min, exportável e auditável | **R$ 2.500 a R$ 12.000 / relatório** (vs. R$ 30k–R$ 150k em consultoria tradicional) | 75–85% |
| **Integração Enterprise** | One-time + manutenção: ETL custom, conectores ERP/CRM, white-label, sondas em sistemas internos | **R$ 60k–R$ 250k** setup + **15–22%** ARR de manutenção | 55–70% |
| **Dados Premium (futuro)** | Licenciamento de scores proprietários (SAM, Score de Risco) para fundos, bancos e seguradoras | **R$ 12k–R$ 50k / mês** por feed | 80–90% |

### 9.3 Mercado Endereçável

Calibrado sobre dados públicos do IBRAM, ANM e Receita Federal — sem extrapolações.

| Camada | Definição | Cálculo | Volume |
|--------|-----------|---------|--------|
| **TAM** (total addressable) | Universo total de potenciais compradores B2B no Brasil: empresas da cadeia mineral (9.000) + escritórios com prática minerária (~500) + bancos e fundos com mesa de M&A/commodities (~100) + consultorias técnicas (~150) + órgãos públicos (27 estados + secretarias) | ~9.800 entidades × ticket médio R$ 36k/ano | **~R$ 350M / ano** |
| **SAM** (serviceable available) | Entidades com maturidade digital e ticket de inteligência setorial — exclui micro produtores informais e mineradoras de fundo de quintal | ~2.500 entidades × R$ 30k/ano (média blended) | **~R$ 75M / ano** |
| **SOM** (serviceable obtainable, 5 anos) | Captura realista de 8–15% do SAM após 5 anos de operação, alinhada a benchmarks de SaaS B2B vertical no Brasil (Cortex, Resultados Digitais, Pipefy nos primeiros 5 anos) | 200–375 contratos ativos | **R$ 8M–R$ 15M ARR** |

Adicionalmente, o **mercado de consultoria e due diligence mineral** no Brasil é hoje estimado em **R$ 800M–R$ 1,2B/ano** (relatórios setoriais IBRAM/Deloitte). O MineralRadar não busca substituir consultorias, mas capturar a camada de **inteligência recorrente** que hoje é entregue de forma artesanal — potencialmente 10–15% desse mercado nos próximos 5 anos.

### 9.4 Unit Economics

Modelagem conservadora baseada em benchmarks de SaaS B2B brasileiro (RD Station, Cortex, Pipefy, Resultados Digitais) ajustados ao ciclo de venda do setor mineral.

| Métrica | Explorador | Profissional | Institucional |
|---------|-----------|--------------|---------------|
| **ARPU anual** | R$ 2.990 | R$ 22.700 (média 1,2 seats) | R$ 138.000 (média) |
| **CAC** | R$ 600–R$ 1.500 (self-service + conteúdo) | R$ 5.000–R$ 12.000 (inside sales) | R$ 30.000–R$ 80.000 (enterprise + POC) |
| **Churn anual** | 18–25% (esperado para SMB) | 8–12% | 3–6% |
| **LTV** (gross) | R$ 12k–R$ 18k | R$ 90k–R$ 180k | R$ 700k–R$ 1,5M |
| **LTV : CAC** | ~10× | ~12× | ~15× |
| **Payback** | 4–6 meses | 8–10 meses | 10–14 meses |
| **Gross margin** | ~88% | ~83% | ~76% (peso de SLA e custom) |

**Custos variáveis por usuário** (a serem cobertos pela receita): Azure OpenAI (LLM com cache Redis) custa entre R$ 0,05 e R$ 0,30 por consulta; OpenSearch dedicado em sa-east-1 ~R$ 8–14k/mês como custo fixo amortizado; Azure Maps ~R$ 0,05 por isócrona; MongoDB Atlas ~R$ 2–5k/mês. **Estimativa de custo variável por usuário Profissional ativo: R$ 80–R$ 200/mês**, viabilizando gross margin de classe SaaS (75–85%).

### 9.5 Trajetória de Receita — Marcos Potenciais

Projeção **conservadora**, não compromissória, calibrada por benchmarks de adoção em vertical SaaS B2B brasileiro de ticket médio-alto:

| Métrica | **Ano 1** (validação) | **Ano 2** (tração) | **Ano 3** (escala) |
|---------|------------------------|---------------------|---------------------|
| Explorador (assinantes) | 80 | 250 | 500 |
| Profissional (contratos) | 30 | 100 | 250 |
| Institucional (contratos) | 3 | 10 | 25 |
| ARR de assinaturas | ~R$ 1,3M | ~R$ 4,4M | ~R$ 10,4M |
| Receita transacional (API + DD + integração) | ~R$ 100k | ~R$ 400k | ~R$ 1,4M |
| **ARR Total** | **~R$ 1,4M** | **~R$ 4,8M** | **~R$ 11,8M** |
| % do SOM capturado | <2% | ~6% | ~12% |

Os números refletem **penetração de 12% do SOM em 3 anos**, compatível com a curva histórica de SaaS verticais brasileiros bem-sucedidos. A receita transacional cresce mais que proporcionalmente conforme a base de contratos enterprise se consolida e a API ganha tração com clientes não-finais (consultorias, ERPs setoriais, plataformas de M&A).

### 9.6 Defensibilidade e Lock-in Econômico

O MineralRadar combina cinco fontes de defensibilidade que aumentam progressivamente o switching cost do cliente:

1. **Dado curado proprietário** — 221M de registros CNPJ destilados em 350k empresas relevantes ao domínio mineral; substituir essa camada exige meses de ETL e ontologia
2. **Memória persistente** — projetos, análises, fornecedores e preferências do cliente acumulam ao longo do tempo, tornando a migração economicamente custosa
3. **Integração via API** — uma vez embutida em ERPs, BI ou workflows de M&A, a troca exige reengenharia
4. **Score de Atratividade Mineral (SAM)** — índice proprietário usado em comitês de investimento e laudos, gerando dependência analítica
5. **Network effect de dados** — cada consulta enriquece embeddings semânticos e melhora roteamento de intenção para todos os clientes (sem expor dados privados)

A combinação reduz churn estrutural e justifica um **NDR (Net Dollar Retention) projetado de 110–125%** no segmento Profissional/Institucional após o ano 2 — patamar de SaaS top-tier.

---

## 10. Posicionamento Competitivo

### Panorama atual

| Plataforma | Foco | Limitação |
|------------|------|-----------|
| **Jazida.com** | Marketéplace de processos ANM | Sem IA, sem cruzamento empresarial, sem logística |
| **SIGMINE (ANM)** | Portal oficial de processos | Interface legada, sem análise, sem cruzamento |
| **Sistemas GIS tradicionais** | Visualização geoespacial | Exigem expertise técnica, sem linguagem natural |
| **Consultorias** | Due diligence manual | Alto custo, baixa escala, semanas de entrega |
| **MineralRadar** | Inteligência conversacional completa | — |

### Diferenciais Proprietários

1. **Cruzamento automático de entidades**: o único sistema que conecta processo ANM → titular → CNPJ ativo → sócios → IBAMA → CFEM em uma única resposta conversacional
2. **Isócrona mineral**: consulta de "tudo que está a X horas" combinando geologia e logística — inédita no Brasil
3. **Semântica de substâncias**: o usuário não precisa saber os códigos ANM; a plataforma entende intenção ("baterias de EV" → lítio + grafita + cobalto)
4. **Contexto persistente**: o agente lembra do projeto, das análises anteriores e das preferências — não é uma busca sem memória
5. **Dado normalizado e curado**: 221 milhões de registros CNPJ filtrados para ~350 mil empresas relevantes ao domínio mineral

---

## 11. Infraestrutura e Escala

### Stack de Produção

```
Frontend        React 19 + Vite + MapLibre GL JS + TypeScript
API Backend     FastAPI (Python 3.11) + LangGraph + MCP SDK
LLM             Azure OpenAI GPT-4o (agent) + text-embedding-3-small (1536d)
Busca           OpenSearch (AWS sa-east-1) — cluster dedicado
Cache/Sessão    Redis (memória de curto prazo, TTL 2h)
App DB          MongoDB (projetos, análises, memória de longo prazo)
Routing/Geo     Azure Maps (isócronas, rotas, geocoding)
Auth            Azure Active Directory (MSAL, OAuth 2.0)
ETL             Python + GeoPandas + Polars → PostgreSQL/PostGIS → OpenSearch
Observab.       OpenTelemetry + LangSmith + structlog
```

### Volume de Dados

- **246+ milhões de documentos** no cluster OpenSearch
- **~75 GB** de dados indexados (sem contar backups)
- **12+ fontes públicas** ingeridas e normalizadas automaticamente
- **Atualização incremental** via hash de conteúdo — apenas registros alterados são reindexados

### Confiabilidade

- ETL com hash incremental: zero duplicatas, atualizações cirúrgicas
- Redis cache com TTL configurável por ferramenta — reduz latência e custo de LLM
- Auto-save de resultados no MongoDB: nenhum dado de sessão é perdido
- Monitoramento de saúde de ETL com alertas de falha por bot

---

## Conclusão

O MineralRadar resolve um problema que afeta toda a cadeia mineral brasileira — do geólogo que pesquisa areia quartzosa no interior de São Paulo ao investidor que avalia um ativo de nióbio no Pará: a inteligência sobre o setor é fragmentada, inacessível e analógica.

A plataforma é uma **camada de inteligência sobre os 25+ milhões de processos minerários do Brasil** — qualquer substância, qualquer fase, qualquer região. Não é um produto de nicho para minerais estratégicos; é a infraestrutura de informação que a cadeia mineral brasileira inteira nunca teve.

É um **analista mineral sênior disponível 24/7**, que fala português, conhece cada processo ANM, sabe quem são os titulares, entende os riscos ambientais, calcula a logística e acompanha o mercado — para o calcário tanto quanto para o lítio.

O Brasil tem os minerais. O MineralRadar tem a inteligência para desbloqueá-los.

---

*Versão 1.0 — Maio 2026*
*Para informações: contato@mineralradar.com.br*
