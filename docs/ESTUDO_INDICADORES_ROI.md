# Estudo de Indicadores de ROI — MineralRadar 2.0

> **Data:** 07/04/2026
> **Autor:** Leonardo de Melo — Especialista em Engenharia de Software e IA
> **Audiência:** C-Level (CTO, CFO, COO)
> **Objetivo:** Mapear indicadores mensuráveis que demonstrem o retorno sobre o investimento alocado no MineralRadar 2.0, organizados por dimensão de valor.

---

## Sumário Executivo

O MineralRadar 2.0 é uma plataforma de inteligência geoespacial que centraliza ~246 milhões de registros de dados governamentais (ANM, RFB, IBGE), permitindo que equipes de suprimentos encontrem fornecedores de materiais minerais, realizem due diligence e planejem logística — tudo via linguagem natural com IA. Este documento propõe **32 indicadores** organizados em **6 dimensões** para mensuração contínua do valor gerado.

---

## 1. Indicadores de Eficiência Operacional

> **Narrativa para C-Level:** "Processos que antes levavam dias agora levam segundos."

### 1.1 Tempo Médio de Identificação de Fornecedor (TMIF)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Tempo entre a necessidade de um material e a obtenção de uma lista qualificada de fornecedores |
| **Antes (baseline)** | 2–5 dias úteis (pesquisa manual em sites da ANM, RFB, Google Maps, planilhas) |
| **Depois (target)** | < 30 segundos (uma pergunta no chat: "fornecedores de brita em 50km de Guarulhos") |
| **Fórmula** | `TMIF = timestamp_resposta - timestamp_pergunta` |
| **Fonte de dados** | Logs do chat (Redis/MongoDB: `chat_sessions.started_at` → `ended_at`) |
| **Impacto** | Redução de **99,9%** no tempo — libera engenheiros para atividades estratégicas |

### 1.2 Consultas por Hora por Analista (CHA)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Volume de pesquisas de mercado que um profissional consegue realizar por hora |
| **Antes** | 1–2 consultas/hora (navegar sites, copiar dados, cruzar planilhas) |
| **Depois** | 30–60 consultas/hora (perguntas sequenciais com memória contextual) |
| **Fórmula** | `CHA = total_consultas_usuario / horas_sessao` |
| **Fonte de dados** | MongoDB `chat_sessions.turn_count` + duração da sessão |
| **Impacto** | Aumento de **15–30x** na produtividade de pesquisa |

### 1.3 Etapas Eliminadas no Processo de Sourcing

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Quantidade de etapas manuais substituídas por automação |
| **Antes** | 8 etapas: (1) acessar site ANM → (2) buscar por substância → (3) filtrar por região → (4) copiar dados → (5) acessar RFB → (6) buscar CNPJ do titular → (7) obter contatos → (8) calcular rota no Google Maps |
| **Depois** | 1 etapa: perguntar ao MineralRadar (internamente executa busca semântica + cross-index + enriquecimento + rota em ~60ms) |
| **Fórmula** | `Etapas_eliminadas = 8 - 1 = 7` |
| **Fonte de dados** | Mapeamento de processo (BPM) |
| **Impacto** | **87,5%** de redução no número de etapas |

### 1.4 Taxa de Resolução na Primeira Interação (TRPI)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Percentual de consultas resolvidas sem necessidade de refinamento |
| **Antes** | N/A (processo manual) |
| **Depois** | Target: > 75% (busca híbrida semântica entende "pedra para pavimentação" → retorna brita, basalto, granito) |
| **Fórmula** | `TRPI = sessoes_1_turno / total_sessoes × 100` |
| **Fonte de dados** | MongoDB `chat_sessions.turn_count == 1` |
| **Impacto** | Menos iterações = menos tempo + menos custo de LLM |

---

## 2. Indicadores de Qualidade de Decisão

> **Narrativa para C-Level:** "Decisões melhores, baseadas em dados oficiais e georreferenciados."

### 2.1 Cobertura de Dados por Consulta (CDC)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Quantidade de fontes cruzadas em uma única resposta |
| **Antes** | 1 fonte por vez (consulta isolada na ANM OU na RFB) |
| **Depois** | 3 fontes simultâneas (ANM + RFB/CNPJ + IBGE/Geo) via cross-index em uma única tool call |
| **Fórmula** | `CDC = média(indices_consultados_por_pergunta)` |
| **Fonte de dados** | `chat_sessions.tool_usage` (contagem de tools por sessão) |
| **Impacto** | **3x mais contexto** por decisão — fornecedor + compliance + logística |

### 2.2 Precisão Semântica vs Full-Text

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Relevância dos resultados de busca comparando o sistema antigo (full-text) com o novo (híbrido) |
| **Antes** | Busca "pedra para pavimentação" → 0 resultados (full-text não encontra sinônimos) |
| **Depois** | Busca semântica → encontra brita, pedra britada, basalto, diabásio (k-NN embeddings com 862 substâncias) |
| **Fórmula** | `Precisão = resultados_relevantes / total_resultados` (amostrar e avaliar manualmente a cada trimestre) |
| **Fonte de dados** | Avaliação manual periódica + feedback do usuário |
| **Impacto** | Eliminação de **zero-results**, que antes geravam retrabalho |

### 2.3 Fornecedores Descobertos que Antes Eram Invisíveis (FDAI)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Quantidade de fornecedores encontrados pelo MineralRadar que não seriam encontrados no processo manual |
| **Antes** | Limitado a fornecedores já conhecidos + busca Google |
| **Depois** | 956.288 processos ANM + 69M empresas CNPJ indexadas com geo — fornecedores nunca antes contactados aparecem |
| **Fórmula** | `FDAI = fornecedores_novos_contactados / total_contactados × 100` |
| **Fonte de dados** | CRM/planilha de fornecedores + comparação com resultados do MineralRadar |
| **Impacto** | Maior competição entre fornecedores = **melhor negociação de preços** |

### 2.4 Índice de Compliance Automática (ICA)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Percentual de fornecedores verificados automaticamente quanto à situação cadastral ativa (CNPJ) e processo ANM vigente |
| **Antes** | Verificação manual — muitas vezes ignorada por falta de tempo |
| **Depois** | 100% dos fornecedores retornados já vêm com `situacaoCadastral.codigo == "02"` (ativa) e fase do processo ANM |
| **Fórmula** | `ICA = fornecedores_com_compliance_verificada / total_fornecedores_retornados × 100` |
| **Fonte de dados** | Campos retornados pela tool `buscar_fornecedores` (sempre inclui status) |
| **Impacto** | **Zero risco** de contratar fornecedor com CNPJ baixado ou processo ANM vencido |

---

## 3. Indicadores Financeiros e de ROI

> **Narrativa para C-Level:** "Economia mensuráve — de infraestrutura a horas-homem."

### 3.1 Economia em Horas-Homem de Pesquisa (EHH)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Horas economizadas por mês ao substituir pesquisa manual por IA |
| **Antes** | Estimativa: 4h por estudo de fornecedor × 20 estudos/mês = **80h/mês** por analista |
| **Depois** | Estimativa: 10min por estudo × 20 estudos/mês = **3,3h/mês** por analista |
| **Fórmula** | `EHH = (tempo_manual - tempo_supplyradar) × estudos_por_mes × num_analistas` |
| **Premissas** | 5 analistas, custo médio R$ 80/hora (eng. suprimentos) |
| **Economia mensal** | `(80h - 3,3h) × 5 × R$ 80 = R$ 30.680/mês = ~R$ 368.160/ano` |
| **Impacto** | Payback em **< 2 meses** considerando custo de infra de ~US$ 1.000/mês (~R$ 5.200) |

### 3.2 Custo por Consulta (CPC)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Custo unitário de cada consulta realizada no MineralRadar |
| **Fórmula** | `CPC = custo_infra_mensal / total_consultas_mes` |
| **Estimativa** | US$ 1.000/mês ÷ 3.000 consultas = **US$ 0,33/consulta** (~R$ 1,72) |
| **Comparação** | Custo manual equivalente: R$ 80/h × 0,5h = **R$ 40 por consulta manual** |
| **Fonte de dados** | AWS billing + contagem de requests (`chat_sessions` no MongoDB) |
| **Impacto** | Redução de **95,7%** no custo por consulta |

### 3.3 Economia em Infraestrutura Cloud (EIC)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Redução de custo com otimização do OpenSearch (consolidação de clusters) |
| **Antes** | 2 clusters OpenSearch: **US$ 3.052/mês** |
| **Depois** | 1 cluster otimizado (Cenário D): **US$ 909/mês** |
| **Fórmula** | `EIC = custo_anterior - custo_otimizado` |
| **Fonte de dados** | AWS Cost Explorer |
| **Impacto** | **Economia de US$ 2.143/mês (−70%)** = US$ 25.716/ano |

### 3.4 ROI Projetado

| Componente | Valor Anual |
|------------|-------------|
| **Investimento total** | |
| Infraestrutura AWS (OpenSearch + Redis + MongoDB + Compute) | ~US$ 10.900/ano (~R$ 56.680) |
| Azure OpenAI (LLM + Embeddings) | ~US$ 3.600/ano (~R$ 18.720) |
| Azure Maps APIs | ~US$ 600/ano (~R$ 3.120) |
| **Total investimento** | **~R$ 78.520/ano** |
| | |
| **Retorno estimado** | |
| Economia em horas-homem (5 analistas) | R$ 368.160/ano |
| Economia em infra (consolidação clusters) | ~R$ 133.700/ano (US$ 25.716 × 5,2) |
| Ganho por melhor negociação (2% saving em compras) | Variável — potencialmente R$ 500K+ |
| **Total retorno conservador** | **~R$ 501.860/ano** |
| | |
| **ROI** | **(R$ 501.860 − R$ 78.520) / R$ 78.520 = 539%** |

### 3.5 Tempo de Payback

| Atributo | Detalhe |
|----------|---------|
| **Fórmula** | `Payback = investimento_total / economia_mensal` |
| **Cálculo** | R$ 78.520 / (R$ 41.822/mês) = **1,9 meses** |
| **Impacto** | Payback em menos de **2 meses** — retorno extremamente rápido |

---

## 4. Indicadores de Uso e Adoção

> **Narrativa para C-Level:** "A plataforma está sendo usada, e cada vez mais."

### 4.1 Usuários Ativos Diários/Mensais (DAU/MAU)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Engajamento real da plataforma |
| **Fórmula** | `DAU = usuarios_unicos_dia` / `MAU = usuarios_unicos_mes` |
| **Target** | Stickiness ratio `DAU/MAU > 40%` (indica uso habitual, não pontual) |
| **Fonte de dados** | MongoDB `chat_sessions.user_id` (distinct por período) |
| **Impacto** | Demonstra adoção real — argumento fundamental para C-level |

### 4.2 Sessões de Chat por Usuário por Semana (SCUS)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Frequência de uso individual |
| **Fórmula** | `SCUS = total_sessoes_semana / usuarios_ativos_semana` |
| **Target** | > 5 sessões/semana por analista ativo |
| **Fonte de dados** | MongoDB `chat_sessions` agrupado por `user_id` e semana |
| **Impacto** | Uso frequente = ferramenta integrada ao dia-a-dia |

### 4.3 Taxa de Retenção Semanal (TRS)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Percentual de usuários que retornam na semana seguinte |
| **Fórmula** | `TRS = usuarios_ativos_semana_N ∩ usuarios_ativos_semana_N+1 / usuarios_ativos_semana_N × 100` |
| **Target** | > 70% (indica valor percebido alto) |
| **Fonte de dados** | MongoDB `chat_sessions` |
| **Impacto** | Retenção alta valida o investimento contínuo |

### 4.4 Distribuição de Uso por Funcionalidade (DUF)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Quais tools/funcionalidades são mais utilizadas |
| **Fórmula** | `DUF = contagem_por_tool / total_chamadas × 100` |
| **Fonte de dados** | MongoDB `chat_sessions.tool_usage` (já implementado — extrai tool_name e count) |
| **Impacto** | Direciona investimento: funcionalidades mais usadas recebem mais investimento |

**Exemplo de visualização esperada:**

| Tool | % de Uso | Categoria |
|------|----------|-----------|
| `jazidas__buscar_fornecedores` | ~35% | Core |
| `empresas__buscar_empresas` | ~20% | Core |
| `geo__calcular_rota_caminhao` | ~15% | Logística |
| `jazidas__buscar_jazidas` | ~12% | Pesquisa |
| `geo__buscar_municipio` | ~8% | Auxiliar |
| `empresas__detalhes_empresa` | ~5% | Due Diligence |
| Outros | ~5% | Diversos |

### 4.5 Obras/Estudos Criados por Mês (OEM)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Adoção do módulo colaborativo (feature de alto valor) |
| **Fórmula** | `OEM = count(obras_criadas_mes) + count(estudos_criados_mes)` |
| **Target** | Crescimento mensal > 10% nos primeiros 6 meses |
| **Fonte de dados** | MongoDB collections `obras` e `estudos` |
| **Impacto** | Indica uso estratégico (não apenas consultas pontuais, mas planejamento estruturado) |

---

## 5. Indicadores de Performance Técnica

> **Narrativa para C-Level:** "A plataforma é estável, rápida e escalável."

### 5.1 Latência Média de Resposta (LMR)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Tempo total entre pergunta do usuário e resposta completa |
| **Target** | P50 < 3s, P95 < 8s, P99 < 15s |
| **Fórmula** | Percentis calculados sobre `X-Response-Time` header (já implementado no middleware) |
| **Fonte de dados** | Logs HTTP (LoggingMiddleware: `duration_ms`) |
| **Impacto** | Experiência fluida = maior adoção |

### 5.2 Disponibilidade do Sistema (SLA)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Percentual de tempo em que o sistema está operacional |
| **Target** | > 99,5% (permite ~3,6h de downtime/mês) |
| **Fórmula** | `SLA = (minutos_total - minutos_downtime) / minutos_total × 100` |
| **Fonte de dados** | Health checks (`/health` endpoint) + AWS CloudWatch |
| **Impacto** | Confiança operacional para times que dependem da ferramenta |

### 5.3 Taxa de Erro de Tools (TET)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Percentual de chamadas MCP que falham |
| **Target** | < 2% |
| **Fórmula** | `TET = tool_calls_com_erro / total_tool_calls × 100` |
| **Fonte de dados** | Logs MCP (`mcp_calls.log`) + `ToolException` no bridge LangChain |
| **Impacto** | Confiabilidade das respostas — erros frequentes destroem confiança |

### 5.4 Cache Hit Rate (CHR)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Eficiência do cache Redis (evita re-processamento de embeddings e geocoding) |
| **Target** | > 60% após ramp-up |
| **Fórmula** | `CHR = cache_hits / (cache_hits + cache_misses) × 100` |
| **Fonte de dados** | Redis metrics + logs do SubstanciaResolver/CnaeResolver |
| **Impacto** | Mais cache hit = menor latência + menor custo de Azure OpenAI |

### 5.5 Custo de LLM por Sessão (CLS)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Gasto com tokens de Azure OpenAI por sessão de chat |
| **Fórmula** | `CLS = total_tokens_mes × custo_por_token / total_sessoes_mes` |
| **Target** | < US$ 0,15/sessão (com cache + sumarização eficiente) |
| **Fonte de dados** | Azure OpenAI usage dashboard + MongoDB session count |
| **Impacto** | Controle de custo variável mais significativo da operação |

---

## 6. Indicadores de Dados e Inteligência

> **Narrativa para C-Level:** "Nossa base de conhecimento é a mais completa do mercado."

### 6.1 Cobertura do Universo de Dados (CUD)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Percentual do universo de dados governamentais indexados |
| **Dados atuais** | |
| Processos ANM | 956.288 registros (14,2 GB) — cobertura nacional completa |
| Empresas CNPJ | ~69M estabelecimentos (68,5 GB) — todas as empresas ativas do Brasil |
| Municípios IBGE | 5.631 (com polígonos georreferenciados) — 100% do território |
| Substâncias minerais | 862 catalogadas com embeddings semânticos |
| CNAEs | 2.394 com embeddings semânticos |
| **Total** | **~246 milhões de documentos, ~75 GB** |
| **Impacto** | Base incomparável — nenhuma ferramenta de mercado integra ANM + RFB + IBGE com busca semântica e geo |

### 6.2 Frescor dos Dados (FD)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Tempo desde a última atualização de cada índice |
| **Target** | ANM: semanal / CNPJ: mensal / IBGE: semestral |
| **Fórmula** | `FD = data_atual - data_ultima_ETL_index` |
| **Fonte de dados** | Metadados do pipeline ETL (Prefect/Airflow — planejado) |
| **Impacto** | Dados frescos = decisões corretas. Dados desatualizados = risco de contratar fornecedor inativo |

### 6.3 Resolução Semântica Efetiva (RSE)

| Atributo | Detalhe |
|----------|---------|
| **O que mede** | Capacidade do sistema de resolver termos vagos em IDs precisos |
| **Exemplo** | "material para pavimentação" → resolve para IDs: BRITA, BASALTO, GRANITO, DIABÁSIO |
| **Fórmula** | `RSE = termos_vagos_resolvidos_com_sucesso / total_termos_vagos × 100` |
| **Fonte de dados** | Logs do SubstanciaResolver e CnaeResolver (k-NN results com score > threshold) |
| **Target** | > 90% de resolução com score > 0.7 |
| **Impacto** | Diferencial competitivo — elimina barreira de linguagem técnica |

---

## 7. Dashboard Proposto para C-Level

### 7.1 Visão Executiva (atualização mensal)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    SUPPLYRADAR — EXECUTIVE DASHBOARD                         │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐           │
│  │   ROI ACUMULADO  │  │  HORAS SALVAS    │  │  CONSULTAS/MÊS  │           │
│  │    ████ 539%     │  │   ████ 383h      │  │   ████ 3.200    │           │
│  │  (target: 300%)  │  │  (5 analistas)   │  │  (+22% vs M-1)  │           │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘           │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐           │
│  │  CUSTO/CONSULTA  │  │  USUÁRIOS ATIVOS │  │  UPTIME (SLA)   │           │
│  │   R$ 1,72        │  │   █████ 23 MAU   │  │   ████ 99,7%    │           │
│  │  (vs R$ 40 manual│  │  DAU/MAU: 48%    │  │  (target: 99,5%)│           │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘           │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │  TOOLS MAIS UTILIZADAS (mês)                                       │     │
│  │  buscar_fornecedores   ████████████████████████████████████  35%    │     │
│  │  buscar_empresas       █████████████████████                20%    │     │
│  │  calcular_rota         ██████████████████                   15%    │     │
│  │  buscar_jazidas        ████████████                         12%    │     │
│  │  outros                ██████████████████                   18%    │     │
│  └─────────────────────────────────────────────────────────────────────┘     │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │  ECONOMIA ACUMULADA (R$)                                           │     │
│  │         J     F     M     A     M     J                            │     │
│  │  40K   ╱─────╱─────╱─────╱─────╱─────╱                            │     │
│  │  30K  ╱     ╱     ╱     ╱     ╱     ╱                             │     │
│  │  20K ╱     ╱     ╱     ╱     ╱     ╱                              │     │
│  │       Horas-Homem    Infraestrutura    Negociação                  │     │
│  └─────────────────────────────────────────────────────────────────────┘     │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 7.2 Fontes de Dados para o Dashboard

| Indicador | Fonte | Coleta |
|-----------|-------|--------|
| ROI acumulado | Cálculo: economia / investimento | Mensal (manual) |
| Horas salvas | MongoDB sessions × tempo médio economizado | Automático |
| Consultas/mês | MongoDB `chat_sessions.count()` | Automático |
| Custo/consulta | AWS Billing / contagem sessões | Mensal |
| MAU/DAU | MongoDB `distinct(user_id)` por período | Automático |
| Uptime | AWS CloudWatch / health checks | Automático |
| Tools mais usadas | MongoDB `chat_sessions.tool_usage` | Automático (já implementado) |
| Economia acumulada | Soma de: horas-homem + infra + negociação | Mensal (parcialmente manual) |

---

## 8. Instrumentação Necessária

### 8.1 Já Implementado no Sistema

| Dado | Local | Status |
|------|-------|--------|
| Tool usage por sessão | `long_term.py → _extract_tool_usage()` | ✅ Funcional |
| Turn count por sessão | `chat_sessions.turn_count` | ✅ Funcional |
| User ID por sessão | `chat_sessions.user_id` | ✅ Funcional |
| Obra vinculada | `chat_sessions.obra_id` | ✅ Funcional |
| Duração de requests HTTP | `LoggingMiddleware → duration_ms` | ✅ Funcional |
| Tags e entidades por sessão | `chat_sessions.tags / entities` | ✅ Funcional |
| Route history (intents) | `chat_sessions.route_history` | ✅ Funcional |

### 8.2 A Implementar (baixo esforço)

| Dado | Onde implementar | Esforço |
|------|-----------------|---------|
| Contagem de tokens LLM por sessão | `graph.py` — capturar `response.usage_metadata` | 2h |
| Cache hit/miss counter | `SubstanciaResolver` / `CnaeResolver` — incrementar contador Redis | 2h |
| Timestamp de início/fim real da sessão | `conversation_buffer.py` — registrar first/last message time | 1h |
| Feedback do usuário (thumbs up/down) | Novo endpoint + campo em `chat_sessions` | 4h |
| Contagem de resultados retornados por tool | Cada MCP Server — adicionar `result_count` ao response | 3h |
| Latência por MCP Server (não só total HTTP) | `unified_mcp_provider.py` — medir `call_tool` time | 2h |
| ETL metadata (data_atualizacao por índice) | MongoDB collection `etl_runs` | Pipeline ETL |

### 8.3 A Implementar (médio esforço — dashboard)

| Componente | Descrição | Esforço |
|------------|-----------|---------|
| Endpoint `/api/v1/analytics/summary` | Agrega indicadores principais via MongoDB aggregation pipeline | 1–2 dias |
| Endpoint `/api/v1/analytics/tool-usage` | Breakdown de uso por tool, período, usuário | 1 dia |
| Endpoint `/api/v1/analytics/sessions` | Métricas de sessão (duração, turns, resolução) | 1 dia |
| Frontend: página de Analytics | Dashboard React com gráficos (Recharts ou Tremor) | 3–5 dias |

---

## 9. Cronograma de Coleta Sugerido

| Frequência | Indicadores |
|------------|-------------|
| **Real-time** | Latência (P50/P95), taxa de erro, uptime |
| **Diário** | DAU, consultas/dia, cache hit rate |
| **Semanal** | Sessões/usuário, retenção, tools mais usadas |
| **Mensal** | ROI, horas economizadas, custo/consulta, MAU, economia acumulada |
| **Trimestral** | Precisão semântica (avaliação manual), cobertura de dados, satisfação usuário |

---

## 10. Resumo para Apresentação C-Level (1 slide)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│   SUPPLYRADAR 2.0 — RETORNO SOBRE INVESTIMENTO                              │
│                                                                              │
│   INVESTIMENTO:  R$ 78.520/ano (infra cloud + IA + APIs)                    │
│   RETORNO:       R$ 501.860/ano (conservador)                               │
│   ROI:           539%                                                        │
│   PAYBACK:       < 2 meses                                                  │
│                                                                              │
│   ┌─────────────────────────────────────────────────────────────────┐        │
│   │  ANTES                          DEPOIS                         │        │
│   │  ──────                         ──────                         │        │
│   │  2-5 dias para encontrar        30 segundos (−99,9%)           │        │
│   │  fornecedores                                                  │        │
│   │                                                                │        │
│   │  1-2 consultas/hora             30-60 consultas/hora (+30x)    │        │
│   │                                                                │        │
│   │  R$ 40/consulta (manual)        R$ 1,72/consulta (−95,7%)     │        │
│   │                                                                │        │
│   │  1 fonte por vez                3 fontes cruzadas (+3x)        │        │
│   │                                                                │        │
│   │  0 fornecedores novos           956K processos + 69M empresas  │        │
│   │  descobertos                    indexados com geo + semântica   │        │
│   │                                                                │        │
│   │  Compliance manual              100% automática (CNPJ + ANM)   │        │
│   │  (frequentemente ignorada)                                     │        │
│   └─────────────────────────────────────────────────────────────────┘        │
│                                                                              │
│   246M registros | 75 GB | 15 tools IA | Busca semântica + geoespacial      │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 11. Indicadores Qualitativos (complementares)

Além dos indicadores quantitativos, estes fatores qualitativos devem ser mencionados a C-levels:

| Fator | Descrição |
|-------|-----------|
| **Vantagem competitiva** | Nenhuma ferramenta de mercado integra ANM + RFB + IBGE com busca semântica, geo e IA conversacional |
| **Escalabilidade de canais** | Arquitetura MCP permite expandir para mobile, chatbot Teams, parceiros B2B sem reescrever backend |
| **Propriedade intelectual** | Dados são públicos, mas o modelo de indexação, embeddings por domínio e cross-index enrichment são IP proprietário |
| **Efeito rede** | Quanto mais usuários, mais dados de uso → melhor memória IA → melhores respostas → mais adoção |
| **Barreira de entrada** | 246M docs indexados + pipelines ETL + modelo semântico treinado = difícil de replicar por concorrentes |
| **Compliance regulatória** | Todas as fontes são dados oficiais do governo federal — auditáveis e rastreáveis |

---

## 12. Próximos Passos

1. **Semana 1:** Implementar instrumentação de baixo esforço (Seção 8.2) — ~14h
2. **Semana 2-3:** Criar endpoints de analytics (Seção 8.3) — ~4 dias
3. **Semana 3-4:** Dashboard frontend com visualização dos indicadores
4. **Mês 2:** Primeira coleta completa de todos os indicadores
5. **Mês 3:** Primeira apresentação executiva com dados reais + tendências
