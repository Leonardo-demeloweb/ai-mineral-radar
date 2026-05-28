# MineralRadar × Eco Invest Brasil — Análise de Posicionamento Estratégico

**Referência:** 5º Leilão Eco Invest Brasil (lançado 25/05/2026 — Ministério da Fazenda + MMA)  
**Documento elaborado em:** 27/05/2026  
**Status:** Análise estratégica de habilitação

---

## 1. O Programa em Síntese

O **Eco Invest Brasil** é a maior plataforma de financiamento climático e de inovação do país, gerenciada pelo Tesouro Nacional. O **5º Leilão**, lançado em 25 de maio de 2026, inaugura uma nova frente do programa voltada à **inovação tecnológica em cadeias estratégicas para a nova economia global**.

### 1.1 Instrumentos financeiros

| Instrumento | Volume público | Alavancagem mínima | Mecanismo |
|---|---|---|---|
| **6 Fundos de Inovação Eco Invest** | R$ 1,5B por fundo (Tesouro) | 2× privado → até R$ 4,5B por fundo | Dívida conversível nas investidas; bancos disputam via leilão |
| **Linha de crédito corporativo** | até R$ 1B (Tesouro) | 2× privado obrigatório | Crédito direto a empresas prontas para escalar; contrapartida P&D&I obrigatória |
| **Recursos não reembolsáveis** | Parcela dos recursos mobilizados | — | Pesquisa aplicada e empreendedorismo de base tecnológica nas fases iniciais |

**Total mobilizável estimado: R$ 2,5B público + alavancagem mínima → acima de R$ 10B em investimentos totais.**

### 1.2 Cadeias estratégicas elegíveis

1. Fertilizantes verdes
2. Combustíveis verdes avançados
3. **Automação e inteligência artificial aplicada à indústria** ← aderência direta
4. **Beneficiamento de minerais críticos** ← aderência direta (cadeia principal)
5. Sistemas de baterias e veículos elétricos
6. Química verde, biomateriais e circularidade de resíduos minerais e industriais

### 1.3 Quem participa e em qual papel

- **Instituições financeiras (bancos):** disputam o leilão para gerir os Fundos de Inovação. Quem vencer estrutura o fundo e seleciona empresas investidas.
- **Empresas investidas:** recebem capital (dívida conversível ou crédito) e devem contratar projetos de **P&D&I** com universidades, ICTs nacionais ou internacionais, ou startups de base tecnológica.
- **Empreendedores / startups / ICTs:** podem ser contratados para P&D&I ou habilitados diretamente nos recursos não reembolsáveis para empreendedorismo de base tecnológica.

> **Nota:** Os documentos oficiais do 5º Leilão ainda não foram publicados ("serão publicados oportunamente"). Este é o momento estratégico de se posicionar antes da abertura formal do processo.

---

## 2. O MineralRadar — Estado Real do Produto em 27/05/2026

O MineralRadar **não é um projeto em papel**. É uma plataforma de inteligência mineral funcionando, com cluster OpenSearch em produção local, agente de IA conversacional operacional e cobertura validada dos principais cenários de uso do setor mineral brasileiro.

### 2.1 Cluster OpenSearch — dados reais (medidos em 27/05/2026)

| Métrica | Valor |
|---|---|
| Versão OpenSearch | 3.6.0 |
| Índices `mr_*` ativos | **22 índices** |
| Documentos totais (cat) | **~14,6 milhões** (e crescendo) |
| Armazenamento primário | **~8,8 GB** (e crescendo) |
| Status cluster | Green/Yellow (nó único dev) |
| Estados carregados no SICAR | **26 UFs** + Bahia em ingestão ativa |

#### Índices canônicos por cadeia de valor

| Índice | Documentos | Tamanho | Conteúdo estratégico |
|---|---|---|---|
| `mr_sicar_v001` | **7.526.995** ¹ | **4,9 GB** | Imóveis rurais CAR/SICAR — 26 estados carregados + Bahia em ingestão (46,6% em 27/05/2026); sobreposição ambiental fundiária de qualquer jazida |
| `mr_jazidas_v001` | **907.129** | 1,6 GB | Todos os processos ANM com polígono geoespacial (267K ativos + 640K inativos), categoria estratégica, sobreposições pré-computadas, CFEM enriquecido |
| `mr_cfem_v001` | **3.289.871** | 691 MB | Série histórica completa de CFEM desde 2010 — proxy de produção mineral real por processo e empresa |
| `mr_sigef_v001` | **1.411.595** | 1,0 GB | Imóveis rurais certificados INCRA — sobreposição fundiária com jazidas |
| `mr_geoquimica_v001` | **1.273.685** ² | 55 MB | Análises geoquímicas CPRM — teores reais de Li, Nd, Co, Nb, Au e 50+ analitos por amostra de rocha e mineral |
| `mr_municipios_v001` | **5.572** | 227 MB | Malha municipal completa com `geo_shape` |
| `mr_cprm_v001` | **36.472** | 165 MB | Ocorrências minerais GeoBank — contexto geológico sem processo ANM associado |
| `mr_autuacoes_v001` | **55.043** | 30 MB | Autos de infração IBAMA — risco ambiental por empresa e área geográfica |
| `mr_mercado_v001` | **66.771** | 25 MB | Exportações/importações por NCM mineral (ComexStat MDIC) |
| `mr_ferrovias_v001` | **1.865** | 23 MB | Trechos ferroviários ANTT com geometria |
| `mr_cnae_v001` | **1.359** | 16 MB | CNAEs com embeddings k-NN para resolução semântica |
| `mr_ucs_v001` | **2.073** | 15 MB | Unidades de Conservação IBAMA/CNUC com categoria e restrição à mineração |
| `mr_substancias_v001` | **862** | 10 MB | 862 substâncias ANM com categoria estratégica + embeddings k-NN para busca semântica |
| `mr_terras_indigenas_v001` | **657** | 7,6 MB | Todas as Terras Indígenas (homologadas, declaradas, em estudo) com polígono |
| `mr_biomas_v001` | **6** | 3,5 MB | Biomas IBGE com `geo_shape` (identificação de bioma por ponto) |
| `mr_cvm_listadas_v001` | **367** | 266 KB | Mineradoras listadas na B3/CVM |
| `mr_empresas_v001` | **43.622** | 33 MB | Empresas da cadeia mineral (titulares ANM + CNAE extrativista + CFEM) com sócios |

> ¹ `mr_sicar_v001`: 7,5M docs correspondem a 26 estados brasileiros completamente carregados. O estado da Bahia estava em ingestão ativa em 27/05/2026 (46,6% concluído às 21h02 UTC-3), com `POST _bulk` status 200 a ~20ms por batch. O volume final de todo o Brasil será substancialmente maior.

> ² `mr_geoquimica_v001`: contagem `_cat/indices` inclui docs nested de analitos. O número de amostras (documentos raiz) é ~77K; os 1,27M representam registros individuais de análise por elemento/amostra.

> **Evidência operacional ao vivo:** Em 27/05/2026 às 21h, o cluster está em operação ativa com `bot_sicar.py --uf BA --index --resume` realizando bulk indexing contínuo — confirmando infraestrutura de ingestão e cluster de produção funcionais.

### 2.2 Agente conversacional — cobertura de cenários validada

O agente LangGraph com GPT-4o está operacional com **8 rotas de intenção**, **46 tools MCP** e cobertura de mais de **150 cenários documentados** por perfil de usuário, substância e módulo funcional.

#### Rotas operacionais do agente

| Rota | Cenários cobertos | Relevância para minerais críticos |
|---|---|---|
| `mineral` | Busca por substância, UF, fase, área, CFEM, vigência | **Direta** — lítio, TRs, nióbio, cobalto, grafita |
| `empresa` | CNPJ, CNAE semântico, sócios, risco ambiental IBAMA | **Direta** — due diligence de titulares de jazidas críticas |
| `hybrid` | Cadeia completa: extração + beneficiamento + empresas | **Direta** — quem produz e quem beneficia uma substância |
| `geo` | Rotas, isócronas, ferrovias, portos, geocodificação | Infraestrutura logística da cadeia mineral |
| `mineral_em_isocrona` | Jazidas dentro de tempo de caminhão a partir de ponto | Custo logístico de abastecimento |
| `empresa_em_isocrona` | Empresas de beneficiamento dentro de isócrona | Cadeia downstream por raio |
| `hibrido_em_isocrona` | Cadeia completa em raio logístico | Completo |
| `general` | Onboarding, escopo, recusas | — |

#### Exemplos de perguntas respondidas hoje pelo agente

> "Quais são as jazidas de lítio ativas em Minas Gerais?"  
> "Quem são os titulares de processos de terras raras no Pará, e qual o CFEM histórico de cada um?"  
> "Esse processo sobrepõe alguma Terra Indígena ou Unidade de Conservação?"  
> "Há anomalias de cobalto nas análises CPRM próximas ao processo X?"  
> "Rota de caminhão da mina do Salobo até o Porto de Itaqui — distância, tempo e custo estimado."  
> "Os sócios da empresa X controlam outras mineradoras no Brasil?"  
> "Volume exportado de nióbio (NCM 2615.90) em 2025 e principais destinos."

### 2.3 Classificador de Minerais Estratégicos

Componente original do MineralRadar: **862 substâncias ANM** classificadas em 14 categorias estratégicas com embeddings k-NN para resolução semântica. Nenhuma outra plataforma no mercado tem esse mapeamento. Permite perguntas como _"processos relacionados a baterias de veículos elétricos"_ → retorna automaticamente lítio + grafita + cobalto + níquel.

### 2.4 Stack tecnológico (todos open source ou padrão de mercado)

| Camada | Tecnologia |
|---|---|
| Busca e dados | OpenSearch 3.6.0 (k-NN + geo_shape + full-text + nested) |
| Agente IA | LangGraph + GPT-4o (Azure OpenAI) |
| Ferramentas | 46 MCP tools (protocolo Anthropic) |
| Geoespacial | Azure Maps + MapLibre GL JS |
| ETL | Python bots + PostgreSQL+PostGIS → OpenSearch |
| Frontend | React + Vite + Tailwind + Radix UI |

---

## 3. Cruzamento Direto: Eco Invest × MineralRadar

### 3.1 Cadeia "Beneficiamento de Minerais Críticos" — fit principal

**O argumento central:** Capital não consegue fluir eficientemente para a cadeia de beneficiamento de minerais críticos sem inteligência de localização, due diligence mineral e mapeamento de riscos. Antes de beneficiar, é preciso saber:

- Onde estão os depósitos (polígono, substância, fase ANM)?
- Quem os controla realmente (CNPJ, sócios, estrutura societária)?
- O depósito tem produção real ou está inativo de fato? (CFEM histórico)
- Há impedimentos legais ou ambientais? (TIs, UCs, biomas, SICAR)
- Qual a logística de escoamento? (rota, ferrovia, porto, custo estimado)
- Como esse depósito se encaixa no mercado global? (preços, exportações, contexto IRA/CMRD)

**O MineralRadar responde todas essas perguntas, hoje, com dados reais.**

| Necessidade da cadeia de minerais críticos | Capacidade MineralRadar | Índice / Tool |
|---|---|---|
| Localizar depósitos de Li, Co, TR, Nb, grafita | `buscar_jazidas(substancia=..., categoria="terra_rara")` | `mr_jazidas_v001` — 907K processos |
| Verificar produção real (não apenas declarada) | Histórico CFEM por processo | `mr_cfem_v001` — 3,28M registros |
| Identificar titular e estrutura societária | Cross-reference ANM ↔ RFB | `mr_empresas_v001` — 43K empresas filtradas |
| Sobreposição TI/UC/bioma/CAR | Pré-computado PostGIS — resultado instantâneo | `mr_terras_indigenas_v001`, `mr_ucs_v001`, `mr_sicar_v001` |
| Contexto geológico e teores reais | Análises CPRM por elemento no raio da jazida | `mr_geoquimica_v001` — 77K amostras com 50+ analitos |
| Risco ambiental do titular | Autuações IBAMA por CNPJ | `mr_autuacoes_v001` — 55K autos |
| Logística jazida → porto | Rota caminhão + ferrovias + portos | `mr_ferrovias_v001`, `mr_portos_v001`, Azure Maps |
| Mercado global de minerais críticos | Exportações NCM, preços internacionais | `mr_mercado_v001` — 66K registros ComexStat |

### 3.2 Cadeia "Automação e IA aplicada à indústria" — fit secundário e defensável

O MineralRadar é, por definição, **IA aplicada à indústria mineral**. O produto automatiza um processo que hoje exige semanas de trabalho manual de geólogos, advogados minerários e analistas de compliance: a due diligence mineral completa. A plataforma entrega em segundos o que uma equipe de 3 especialistas levaria 2–3 semanas para consolidar manualmente a partir de fontes públicas dispersas.

Evidências técnicas concretas:
- **LangGraph** orquestrando múltiplos agentes especializados com roteamento automático de intenção
- **k-NN semântico** (OpenSearch + embeddings text-embedding-3-small) resolvendo substâncias e CNAEs em linguagem natural
- **46 tools MCP** compondo análises multi-fonte em tempo real
- **Geoespacial em tempo real**: isócronas, sobreposições, identificação de bioma por coordenada

### 3.3 Cadeias "Sistemas de Baterias/VEs" e "Circularidade de Resíduos Minerais" — fit de suporte

Os minerais que o MineralRadar mapeia com maior profundidade — lítio, cobalto, terras raras (NdFeB), grafita, nióbio — são exatamente os insumos críticos das cadeias de baterias e veículos elétricos. O MineralRadar é a infraestrutura de inteligência upstream que habilita essas cadeias a encontrar seus insumos no Brasil.

---

## 4. Caminhos de Acesso ao Programa

### Caminho A — Recursos Não Reembolsáveis para Empreendedorismo de Base Tecnológica

**Por que é o mais acessível:** O instrumento foca explicitamente nas _"fases iniciais da inovação em que o risco é mais alto e o mercado privado raramente financia sozinho"_ — descrição precisa do estágio de um produto como o MineralRadar: funcional, com dados reais, mas ainda em expansão de escala.

**Requisitos prováveis:**
- CNPJ formalizado com CNAE adequado
- Parceria com ICT nacional ou internacional (universidade, instituto de pesquisa)
- Proposta de projeto P&D&I dentro de uma cadeia elegível

**Parceiros ICT naturais:**
- **CPRM/SGB** — já fornece dados ao MineralRadar (GeoBank, geoquímica); alinhamento institucional imediato
- **UFMG** (Escola de Engenharia de Minas e Geotecnia) — maior escola de engenharia de minas do país
- **UNICAMP** (Instituto de Geociências) — pesquisa em minerais críticos e geoprocessamento
- **LNCC** — computação científica, IA e grandes bases de dados

**Proposta de projeto:** _"Plataforma de inteligência mineral conversacional e geoespacial para habilitação da cadeia de beneficiamento de minerais críticos no Brasil"_ — pesquisa aplicada em integração de dados públicos minerários, IA conversacional de domínio e rastreabilidade de minerais estratégicos.

### Caminho B — Portfolio Company de um Fundo de Inovação Eco Invest

**Como funciona:** Os bancos vencedores do leilão estruturam fundos com dívida conversível. O fundo investe em empresas dentro da cadeia elegível. MineralRadar se candidata como empresa investida do fundo de **"beneficiamento de minerais críticos"**.

**Argumento para o gestor do fundo:** Um fundo gerindo R$ 4,5B em minerais críticos precisa de capacidade analítica para selecionar e monitorar investimentos. O MineralRadar é a ferramenta de deal sourcing e due diligence do próprio fundo — investir no MineralRadar é investir na infraestrutura de inteligência da carteira inteira.

**Timing:** os documentos do leilão ainda não foram publicados; a estruturação dos fundos leva meses. O MineralRadar tem janela para chegar a esse momento com produto maduro e tração demonstrável.

### Caminho C — Parceria com Banco Concorrente ao Leilão (mais estratégico a curto prazo)

**Movimento:** Aproximar-se agora de uma instituição financeira que provavelmente disputará o fundo de minerais críticos — BTG Pactual, Bradesco, Banco do Brasil e ABC Brasil estiveram todos no 4º Leilão — e oferecer o MineralRadar como infraestrutura de inteligência do fundo.

**Proposta de valor para o banco:**
- O banco diferencia sua proposta no leilão demonstrando capacidade analítica superior para selecionar e monitorar investimentos em minerais críticos
- O MineralRadar entrega due diligence mineral completa (localização, titular, CFEM, TIs, UCs, logística, mercado) — tudo o que o comitê de investimento do fundo vai precisar
- Contrato B2B com o banco como cliente âncora + visibilidade dentro do ecossistema Eco Invest

### Caminho D — Empresa receptora de P&D&I (via linha de crédito corporativo)

**Como funciona:** Empresas mineradoras que receberem a linha de crédito corporativo do Eco Invest são **obrigadas** a contratar P&D&I com universidades, ICTs ou startups de base tecnológica. O MineralRadar pode ser contratado como fornecedor de P&D&I por essas empresas (grandes mineradoras de lítio, terras raras, nióbio que acessarem o programa).

---

## 5. Evidências de Produto para Habilitação

A tabela abaixo consolida o que o MineralRadar já tem e o que demonstra para cada critério relevante do programa:

| Critério do Programa Eco Invest | Evidência MineralRadar (estado atual — 27/05/2026) |
|---|---|
| **Empreendedorismo de base tecnológica** | Plataforma SaaS com LangGraph + GPT-4o + OpenSearch k-NN + 46 tools MCP — stack de ponta, construído do zero com bibliotecas open source |
| **Inovação com impacto industrial** | Automatiza due diligence mineral que hoje exige 2–3 semanas de trabalho especializado manual → segundos; cenários validados e respondidos em produção |
| **Cadeia de minerais críticos** | 907K processos ANM (Brasil completo); 7,5M imóveis SICAR (26 estados); 1,27M registros geoquímicos com teores reais de Li, Nd, Co, Nb, Au; classificador de 862 substâncias ANM |
| **IA aplicada à indústria** | Agente conversacional com 8 rotas de intenção, 46 ferramentas MCP, k-NN semântico, geoespacial real-time com isócronas e sobreposição de polígonos |
| **Dados verificáveis de fontes públicas** | ANM, CPRM, IBAMA, FUNAI, INCRA, IBGE, RFB, ComexStat, ANTT — todas fontes governamentais oficiais, citadas em cada resposta do agente |
| **Escala de dados real** | **~14,6 milhões de documentos em 22 índices, ~8,8 GB** — já supera o roadmap original previsto para Fase 2 |
| **Infraestrutura de ingestão ativa** | Bot SICAR indexando Bahia em tempo real (27/05/2026, 21h); pipeline de ETL operacional para todas as fontes críticas |
| **Desenvolvimento nacional** | 100% nacional; dados brasileiros; produto para o mercado mineral brasileiro; Clean Room Design com bibliotecas open source |
| **Fase inicial (risco alto, financiamento privado escasso)** | Produto funcional e com dados reais, ainda em expansão de cobertura e monetização — estágio preciso para recursos não reembolsáveis |

---

## 6. O que Falta para Habilitação Completa

Dois itens operacionais — não tecnológicos — são pré-requisitos para qualquer instrumento do programa:

| Item | Situação | Ação |
|---|---|---|
| **CNPJ formalizado** | Necessário verificar | Constituição como startup (LTDA ou SA) com CNAE: `62.01-5` (desenvolvimento de programas) + `71.19-7` (serviços de consultoria técnica especializada) |
| **Parceria ICT** (para recursos não reembolsáveis) | Não formalizada | Contato com CPRM/SGB ou UFMG para carta de intenção de parceria em P&D&I |

O produto e os dados já estão prontos. O posicionamento institucional é o próximo passo.

---

## 7. Mensagem Central para Posicionamento

> O Brasil detém as maiores reservas de nióbio do mundo, reservas estratégicas de terras raras, lítio, grafita e cobalto — mas nenhuma plataforma de inteligência trata esses minerais como prioridade. O capital do Eco Invest Brasil destinado ao beneficiamento de minerais críticos só se torna produtivo quando existe inteligência adequada para localizar os depósitos, verificar quem os controla, mapear os riscos jurídicos e ambientais e conectá-los ao mercado global. O MineralRadar é essa inteligência — **operacional hoje**, com dados reais de 907 mil processos ANM, 7,5 milhões de imóveis SICAR de 26 estados, mais de 1,27 milhão de registros geoquímicos com teores reais de minerais críticos, e 14,6 milhões de documentos indexados no total — respondendo em segundos perguntas que hoje levam semanas de trabalho especializado.

---

## 8. Próximos Passos Recomendados

| # | Ação | Prazo sugerido | Instrumento alvo |
|---|---|---|---|
| 1 | Constituir CNPJ com CNAEs adequados | Imediato | Todos |
| 2 | Elaborar one-pager de posicionamento técnico para bancos | 2 semanas | Caminho C |
| 3 | Contato com CPRM/SGB ou UFMG para parceria ICT | 2–4 semanas | Caminho A |
| 4 | Monitorar publicação dos documentos oficiais do 5º Leilão no site Eco Invest | Contínuo | Todos |
| 5 | Identificar contato técnico nas áreas de inovação de BTG, Bradesco ou BB | 4 semanas | Caminho C |
| 6 | Consolidar demo gravada dos cenários de minerais críticos (M1, M8, H9, 3.4, 3.5b) | 3 semanas | Todos |

---

*Documento de análise estratégica — MineralRadar, maio de 2026.*  
*Baseado no anúncio oficial do 5º Leilão Eco Invest Brasil (gov.br/tesouronacional, 25/05/2026) e no estado real do produto em 27/05/2026.*  
*Os documentos formais do leilão não foram publicados até a data deste documento — critérios precisos de elegibilidade serão confirmados quando do edital oficial.*
