# MineralRadar — Modelo de Monetização Institucional

**Versão:** 3.0 · 27 de maio de 2026  
**Produto único:** MineralRadar Corporativo — acesso irrestrito a toda a plataforma  
**Premissa:** Não existe tier básico ou plano reduzido. Qualquer cliente institucional que entra, entra com acesso total a todos os dados, todas as ferramentas e toda a inteligência disponível. O que varia é o perfil da organização — e portanto o valor que ela extrai e o preço que faz sentido cobrar.

---

## 1. O produto — acesso irrestrito a toda a plataforma

O contrato Corporativo entrega **tudo** que o MineralRadar possui, sem restrição de usuários, consultas ou módulos. A organização recebe:

### Inteligência Mineral

| Dimensão | O que está disponível |
|---|---|
| **Processos ANM** | 907.129 processos com polígono geoespacial, fase, histórico de eventos, substâncias, área e histórico completo |
| **CFEM histórico** | 3.289.871 registros de produção real declarada (2010–hoje) por processo, empresa e substância |
| **Ocorrências CPRM** | 36.472 ocorrências minerais com contexto geológico — potencial inexplorado sem processo ANM |
| **Geoquímica CPRM** | 77.180 amostras com 1.270.000+ registros de analitos — teores medidos em campo de Au, Ag, Cu, Li, Nb, TR, Co, U, Th e 40+ elementos |
| **Afloramentos geológicos** | Afloramentos mapeados no raio de qualquer jazida ou coordenada — contexto de rocha encaixante |
| **Províncias minerais** | 8 províncias com polígono e caracterização metalogenética (Carajás, Quadrilátero Ferrífero, Borborema, etc.) |
| **862 substâncias classificadas** | Busca semântica: "baterias de EV" → lítio + grafita + cobalto automaticamente |
| **Áreas em disponibilidade ANM** | Blocos disponíveis para requerimento — atualização contínua |

### Due Diligence e Risco

| Dimensão | O que está disponível |
|---|---|
| **Titular real via RFB** | 43.622 empresas com CNPJ, capital social, situação cadastral, data de abertura |
| **Estrutura societária** | Sócios, CPF/CNPJ, participação, outras empresas controladas pelo mesmo grupo |
| **Todos os processos de um CNPJ** | Busca reversa automática: qual é o portfólio completo de uma empresa |
| **Autuações IBAMA** | 55.043 autos de infração com nível de risco calculado (CRÍTICO / ALTO / MÉDIO / BAIXO) |
| **Terras Indígenas** | 657 TIs (homologadas, declaradas, em estudo, identificação) com polígono completo |
| **Unidades de Conservação** | 2.073 UCs com categoria e restrição à mineração |
| **SIGEF/INCRA** | 1.411.595 imóveis rurais certificados — titularidade da superfície |
| **SICAR/CAR** | 7.526.995 imóveis (26 estados) — reserva legal, APP, área consolidada |
| **Sobreposições pré-computadas** | Todos os cruzamentos TI + UC + bioma + SIGEF + SICAR calculados via PostGIS — resultado em < 500ms |
| **Score de risco geográfico** | Classificação automática BAIXO / MÉDIO / ALTO / CRÍTICO por processo |

### Inteligência Empresarial e de Mercado

| Dimensão | O que está disponível |
|---|---|
| **Empresas da cadeia mineral** | 43.622 empresas minerárias com busca semântica por atividade e CNAE |
| **Mineradoras B3/CVM** | 367 listadas com dados financeiros: ativo total, receita, resultado |
| **Exportações (ComexStat)** | 66.771 registros — volume e valor FOB por NCM mineral, destino e período |
| **Preços internacionais** | Li, TR (Nd, Pr, Dy, Tb), Co, Nb, Au, grafita — cotação atual + série histórica |
| **Principais destinos de exportação** | Para onde vai cada mineral brasileiro — geopolítica de supply chain |

### Logística e Escoamento

| Dimensão | O que está disponível |
|---|---|
| **Rotas rodoviárias** | Caminhão / carro com tráfego real — distância, tempo, trechos off-road |
| **Comparação de rotas** | Até 25 rotas em paralelo: mina → múltiplos portos em uma consulta |
| **36 portos catalogados** | Marítimos e fluviais com coordenadas oficiais para cálculo preciso |
| **1.865 trechos ferroviários** | Geometria ANTT — distância da jazida à ferrovia mais próxima |
| **Isócrona de acesso** | Área alcançável em X horas de caminhão a partir da mina |

### Monitoramento de Carteira

| Alerta | O que monitora |
|---|---|
| Prazo SCM | Vencimentos de autorização de pesquisa, lavra, RAL, DIPEM |
| Mudança de fase ou titular | Eventos SICOP para processos monitorados |
| DOU | Portarias e atos ANM para a carteira definida pelo cliente |
| IBAMA | Nova autuação contra empresa do watchlist institucional |

---

## 2. Precificação Corporativo

### Princípio de precificação

Não há plano com restrição de features. O preço varia pelo **tamanho e complexidade da organização** — o que determina quantos usuários a plataforma atende e qual o volume de inteligência que ela processa. O acesso ao produto é idêntico em todos os casos.

### Tabela de preços por perfil

| Perfil Organizacional | Exemplos | Preço anual |
|---|---|---|
| **Pequeno institucional** | Junior miner · exploradora · escritório de advocacia minerária · consultora técnica (≤ 20 pessoas na equipe de mineração) | **R$ 120.000/ano** |
| **Médio institucional** | Mineradora de médio porte · banco regional com mesa M&A · fundo PE/VC com carteira mineral · consultoria de engenharia | **R$ 240.000/ano** |
| **Grande institucional** | Grande mineradora · banco de investimento nacional · gestor de fundo Eco Invest · BNDES · secretaria estadual de mineração | **R$ 420.000/ano** |
| **Corporativo máximo** | Vale, Anglo American, BTG Pactual, MME, ANM, organismos multilaterais — integração sistêmica profunda | **R$ 600.000–R$ 960.000/ano** |

**O que inclui em todos os casos:**
- Usuários ilimitados na organização
- Consultas ilimitadas ao agente (46 ferramentas MCP)
- Acesso a todos os 22 índices de dados
- Exportação de relatórios (PDF / JSON / CSV)
- Alertas e monitoramento de carteira configurável
- Gerente de conta dedicado
- Onboarding presencial ou remoto para a equipe
- SLA contratual (99% a 99,9% conforme negociação)
- Suporte técnico em português com tempo de resposta garantido

**Descontos por comprometimento:**
- Pagamento anual antecipado: **10% de desconto**
- Contrato de 3 anos: **15% de desconto adicional**

---

## 3. Due Diligence On-Demand — como funciona ao lado do Corporativo

### O dilema do DD

Clientes Corporativo **fazem o próprio due diligence** dentro da plataforma — o agente responde qualquer consulta em tempo real, não há necessidade de relatório intermediário. A questão é: o MineralRadar deve também vender DD como **produto entregue** (managed service), e para quem?

### Dois casos de uso viáveis

**Caso A — Para clientes sem licença Corporativo**

Fundos e bancos que não têm contrato MineralRadar mas precisam de análise técnica para um deal específico compram o DD como produto único, sem assinatura. O MineralRadar produz o relatório internamente usando a plataforma e entrega o documento final.

| Produto DD | Escopo | Entrega | Preço |
|---|---|---|---|
| **DD Express** | 1 processo ANM — ficha completa: CFEM histórico, titular real + sócios, sobreposições TI/UC/SIGEF/SICAR, afloramentos e geoquímica próximos, rota logística | < 2 horas | **R$ 4.500** |
| **DD Portfólio** | 5 a 20 processos — análise comparativa com ranking por CFEM, risco ambiental, área efetiva livre e potencial geológico CPRM | < 24 horas | **R$ 15.000–R$ 35.000** |
| **DD Transacional** | 1 empresa + todos os processos + estrutura societária + passivo ambiental IBAMA + logística de escoamento + posição de mercado. Produto para comitê de M&A ou crédito | 48–72 horas | **R$ 75.000–R$ 150.000** |
| **DD de Área Greenfield** | Todos os processos (ativos e inativos), ocorrências CPRM, anomalias geoquímicas, TIs, UCs, SIGEF e SICAR dentro de um polígono definido | 48–72 horas | **R$ 30.000–R$ 60.000** |

**Âncora competitiva do DD:**

| Produto | MineralRadar | Consultoria tradicional | Diferencial |
|---|---|---|---|
| DD de 1 processo | R$ 4.500 · 2h | R$ 30.000–R$ 80.000 · 2–4 semanas | 85–95% mais barato · 100× mais rápido |
| DD Transacional | R$ 75k–150k · 72h | R$ 300k–R$ 1,5M · 4–12 semanas | 75–90% mais barato · 20–60× mais rápido |

**Caso B — Para clientes Corporativo que precisam de output formal**

O cliente Corporativo já faz a análise internamente. Mas para submeter ao comitê de investimento, ao board ou ao banco financiador, ele pode precisar de um **relatório assinado pelo MineralRadar** com metodologia, fontes e responsabilidade técnica declarada — não apenas o output do agente.

Neste caso, o DD On-Demand funciona como um **serviço de laudo técnico formal** ao lado da licença:

| Produto para Corporativo | Preço | Quando faz sentido |
|---|---|---|
| Laudo técnico formal de processo (certificado + metodologia + assinatura técnica) | R$ 2.500 / processo | Submissão a banco financiador, laudo para comitê, operação de M&A formal |
| Laudo de portfólio (5–20 processos com executive summary certificado) | R$ 10.000–R$ 25.000 | Due diligence de aquisição com valor contratual relevante (> R$ 50M) |

> **Implicação de modelo:** o DD On-Demand não concorre com o Corporativo — ele o complementa. O cliente Corporativo usa a plataforma para análise operacional diária; compra o laudo formal quando precisa de responsabilidade técnica declarada externamente.

---

## 4. Pacotes para o Ecossistema Eco Invest

O 5º Leilão Eco Invest estrutura 6 Fundos de Inovação de minerais críticos. Cada fundo gera três camadas de clientes naturais.

| Linha | Perfil | Preço/ano | Justificativa |
|---|---|---|---|
| **Gestor de Fundo** | Banco vencedor que gerencia o fundo — Corporativo máximo + relatório trimestral de portfólio para cotistas | **R$ 600.000** | Taxa de gestão de 2% sobre R$ 4,5B = R$ 90M/ano. O MineralRadar representa 0,67% dessa receita para eliminar custo de due diligence de dezenas de deals |
| **Portfolio Company** | Empresa investida pelo fundo gerindo seus processos ANM — Corporativo médio institucional | **R$ 240.000** | Recebe capital do fundo + inteligência para gerir o ativo com governança profissional |
| **Multilateral** | BID, BNDES, IFC, KfW — monitoramento de compliance de todo o portfólio do programa | **R$ 600.000** | Compliance ESG (TI + UC + IBAMA) de 15–30 projetos por fundo em um único dashboard auditável |

**Potencial de receita do ecossistema Eco Invest:**

| Linha | Volume | Ticket/ano | ARR |
|---|---|---|---|
| 6 gestores de fundo | 6 contratos | R$ 600.000 | **R$ 3.600.000** |
| Portfolio companies (15 / fundo × 6 fundos) | 90 empresas | R$ 240.000 | **R$ 21.600.000** |
| Multilaterais (BID + BNDES + 1 bilateral) | 3 organismos | R$ 600.000 | **R$ 1.800.000** |
| **Total potencial Eco Invest** | | | **~R$ 27.000.000 ARR** |

---

## 5. Viabilidade do empreendimento

### Custos operacionais por cliente Corporativo

| Componente | Custo estimado/mês |
|---|---|
| Azure OpenAI GPT-4o (com cache Redis 80% hit rate) | R$ 500–R$ 2.500 |
| OpenSearch cluster (amortizado) | R$ 700–R$ 1.400 |
| Azure Maps + infra complementar (amortizado) | R$ 300–R$ 600 |
| **Total COGS por cliente** | **R$ 1.500–R$ 4.500/mês** |

### Gross margin por faixa de preço

| Perfil | Receita/mês | COGS/mês | Gross Margin |
|---|---|---|---|
| Pequeno institucional | R$ 10.000 | R$ 1.800 | **82%** |
| Médio institucional | R$ 20.000 | R$ 2.800 | **86%** |
| Grande institucional | R$ 35.000 | R$ 4.200 | **88%** |
| DD Transacional (único) | R$ 75k–150k (único) | R$ 1.000–R$ 2.500 | **97–99%** |

### Break-even e trajetória

Estrutura enxuta: 2 engenheiros + 1 vendedor enterprise + 1 CS + 2 fundadores = ~R$ 110.000–R$ 130.000/mês

| Marco | Contratos | MRR | Resultado |
|---|---|---|---|
| **Mês 6** — 1 grande + 2 médios | 3 contratos | R$ 75.000 | Ainda queimando caixa (produto validado) |
| **Mês 12** — 2 grandes + 5 médios | 7 contratos | R$ 170.000 | **Break-even operacional** |
| **Mês 18** — primeiros fundos Eco Invest | 15–18 contratos | R$ 390.000 | Lucro + capacidade de reinvestir em produto |
| **Mês 24** — escala Eco Invest | 30–35 contratos | R$ 750.000+ | **~R$ 9M ARR** |

**R$ 10M ARR exige apenas 20–25 contratos Corporativo** — contra 440+ contratos SaaS individual para o mesmo resultado.

---

## 6. A proposição de valor em uma linha

> *Um geólogo sênior + advogado minerário + analista de compliance trabalhando por 3 semanas entregam o que o MineralRadar responde em 2 horas — com as mesmas fontes públicas, rastreáveis, verificáveis, exportáveis. O custo dessa equipe por due diligence é R$ 45.000–R$ 90.000. O MineralRadar cobre análises ilimitadas desse tipo por R$ 120.000–R$ 960.000 ao ano para toda a organização.*

---

*Documento de estratégia comercial — MineralRadar, maio de 2026.*  
*Versão 3.0 — produto único Corporativo, acesso irrestrito, DD como camada complementar.*  
*Uso interno e em apresentações a parceiros estratégicos e investidores.*
