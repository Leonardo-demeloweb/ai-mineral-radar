# Especificação: Enriquecimento de Fornecedores via Busca na Web

> **Data:** 24/04/2026
> **Autor:** Leonardo de Melo — Especialista em Engenharia de Software e IA
> **Status:** Rascunho para revisão
> **Objetivo:** Definir arquitetura, provedores de busca, contrato de API, fluxos de UX e modelo de dados para enriquecer fornecedores selecionados em um estudo com informações públicas coletadas da web (reputação, presença digital, histórico, mídias, capacidade operacional).

---

## 1. Motivação de Negócio

Hoje o MineralRadar entrega dados oficiais (ANM + Receita Federal + IBGE) — cadastro regular, localização, CNAEs, situação. Falta a camada qualitativa: **o fornecedor é confiável? atende o porte da obra? tem histórico de problemas? é conhecido no mercado?**. Essa informação existe espalhada pela web (site institucional, Google, notícias, Reclame Aqui, LinkedIn corporativo, Instagram empresarial, YouTube, imprensa setorial) mas depende de pesquisa manual do analista — tipicamente 5 a 15 minutos por fornecedor.

**Valor:** transformar o MineralRadar de uma ferramenta de *descoberta* em uma ferramenta de *decisão qualificada*. O analista seleciona os fornecedores finalistas e a plataforma gera um dossiê consolidado em segundos.

---

## 2. Escopo da Versão 1

| Item | Status |
|---|:---:|
| Enriquecimento sob demanda de fornecedores favoritos | ✅ |
| Enriquecimento em lote (até N fornecedores selecionados) | ✅ |
| Consulta a buscador web + scraping leve das top URLs | ✅ |
| Sumarização via LLM com citação das fontes | ✅ |
| Persistência do dossiê no estudo (cache longo) | ✅ |
| Re-enriquecer (forçar atualização ignorando cache) | ✅ |
| Exportar dossiê em PDF / planilha | ❌ (v2) |
| Monitoramento contínuo (alertas de reputação) | ❌ (v2) |

---

## 3. Arquitetura em Alto Nível

```
┌─────────────────────────────────────────────────────────────────┐
│                     INTERFACE (Frontend)                         │
│  Analista seleciona fornecedores → clica "Enriquecer" → recebe  │
│  dossiê estruturado por fornecedor com citações                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              API MineralRadar — POST /estudos/{id}/enriquecer    │
│  Valida acesso → dispara job assíncrono → retorna job_id        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              Orquestrador de Enriquecimento (LangGraph)         │
│                                                                  │
│  Por fornecedor selecionado:                                    │
│    1. Cache lookup (Redis) — chave: {cnpj_ou_nome}_{versao}     │
│    2. Web Search API (Tavily/Brave/Serper) — top 10 URLs        │
│    3. Scraping leve das top URLs (HTML → texto + metadados)    │
│    4. LLM sumariza + estrutura em categorias                    │
│    5. Valida e persiste em MongoDB (estudo.enriquecimentos)    │
│    6. Atualiza cache Redis (TTL 30 dias)                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│           MongoDB                 Redis                          │
│  estudos.fornecedores[].          Cache: enrich:{hash} → dossiê │
│    enriquecimento: {...}                                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Provedores de Busca (Análise Comparativa)

| Provedor | Tipo | Custo aproximado | Qualidade BR | Ponto forte | Ponto fraco |
|---|---|---|:---:|---|---|
| **Tavily** | Busca + extração nativa | ~$0,008/query | ⭐⭐⭐⭐ | Retorna conteúdo limpo (sem scraping) | Limitado a 10k/mês no plano médio |
| **Brave Search API** | Busca pura | ~$3/1000 queries | ⭐⭐⭐⭐ | Independente (não Google/Bing), bom para BR | Requer scraping separado |
| **Serper (Google)** | Busca Google via API | ~$1/1000 queries | ⭐⭐⭐⭐⭐ | Qualidade Google | Custo escala rápido; dependência de terceiros |
| **DuckDuckGo (livre)** | Busca pura | Gratuito | ⭐⭐⭐ | Sem custo | Rate limit agressivo; qualidade inferior |
| **Scraping direto Google** | Manual | — | — | — | Violação de ToS; alto risco de bloqueio |

### Recomendação: **Tavily (primário) + Brave (fallback)**

**Por quê:**
1. Tavily já retorna conteúdo extraído e limpo — elimina dependência de scraping próprio (menos pontos de falha)
2. Brave como fallback garante resiliência caso Tavily fique indisponível ou exceda quota
3. Custo controlável — `~$0,01` por fornecedor enriquecido (Tavily + LLM)
4. Ambos permitem filtrar por idioma (`pt-BR`) e por domínio (`site:reclameaqui.com.br` etc.)

---

## 5. Modelo de Dados

### 5.1 Extensão em `FornecedorSelecionado`

Adicionar campo opcional `enriquecimento`:

```python
class Enriquecimento(BaseSchema):
    status: Literal["pendente", "processando", "concluido", "falhou"]
    versao: int                            # incremental, re-enrich → +1
    enriquecido_em: datetime | None
    enriquecido_por: str | None             # user_id que disparou

    # Conteúdo estruturado (LLM output)
    resumo: str | None                      # parágrafo executivo
    presenca_digital: PresencaDigital       # site, redes sociais, etc.
    reputacao: Reputacao                    # ReclameAqui, Google Reviews
    historico_midia: list[MidiaItem]        # notícias, menções
    capacidade_operacional: str | None      # porte, frota, equipamentos (se detectável)
    certificacoes: list[str]                # ISO, licenças adicionais
    alertas: list[Alerta]                   # ⚠️ bandeiras vermelhas

    # Auditoria
    fontes: list[FonteEnriquecimento]       # URLs consultadas + snippet
    custo_tokens_llm: int | None
    erro: str | None


class PresencaDigital(BaseSchema):
    site_oficial: str | None
    linkedin: str | None
    instagram: str | None
    facebook: str | None
    youtube: str | None
    whatsapp_comercial: str | None


class Reputacao(BaseSchema):
    reclameaqui_nota: float | None          # 0-10
    reclameaqui_url: str | None
    google_reviews_media: float | None      # 0-5
    google_reviews_total: int | None
    mencoes_negativas: int                  # contador heurístico


class MidiaItem(BaseSchema):
    titulo: str
    url: str
    data: str | None                        # YYYY-MM-DD
    veiculo: str | None                     # ex: "Valor Econômico"
    resumo_curto: str


class Alerta(BaseSchema):
    tipo: Literal["processo_judicial", "autuacao_ambiental",
                  "reclamacao_grave", "inativo_nas_redes",
                  "evidencia_fraude", "outro"]
    descricao: str
    fonte_url: str
    confianca: Literal["alta", "media", "baixa"]


class FonteEnriquecimento(BaseSchema):
    url: str
    titulo: str
    snippet: str
    data_coleta: datetime
    relevancia: float                       # 0-1
```

### 5.2 Cache Redis

```
Chave: enrich:{hash_sha1(cnpj_ou_nome+uf)}
Valor: JSON do Enriquecimento
TTL:   30 dias (configurável)
```

> Cache é compartilhado entre estudos — se dois usuários enriquecerem a mesma empresa em estudos diferentes, o segundo usa o cache sem custo adicional de API.

---

## 6. Contrato de API

### 6.1 Disparar Enriquecimento

```
POST /api/v1/estudos/{estudo_id}/enriquecer
Authorization: Bearer {token}
Body:
{
  "fornecedor_ids": ["cnpj_12345", "processo_678/2020"],   // opcional
  "apenas_favoritos": false,                                 // default
  "forcar_atualizacao": false                                // ignora cache
}
```

**Regras de seleção (em ordem de prioridade):**
1. Se `fornecedor_ids` vier preenchido → enriquece exatamente esses
2. Se `apenas_favoritos=true` → enriquece todos os favoritos do estudo
3. Senão → erro 400 "Especifique fornecedor_ids ou apenas_favoritos"

**Limite por chamada:** máximo **20 fornecedores** por requisição.

**Resposta 202 Accepted:**
```json
{
  "job_id": "enrich-job-abc123",
  "total": 8,
  "estimativa_conclusao_seg": 40
}
```

---

### 6.2 Consultar Status do Job

```
GET /api/v1/estudos/{estudo_id}/enriquecer/{job_id}
```

**Resposta 200:**
```json
{
  "job_id": "enrich-job-abc123",
  "status": "processando",
  "progresso": { "concluido": 3, "total": 8, "falhou": 0 },
  "fornecedores": [
    { "id": "cnpj_12345", "status": "concluido" },
    { "id": "cnpj_67890", "status": "processando" },
    { "id": "processo_678", "status": "pendente" }
  ]
}
```

---

### 6.3 Consultar Enriquecimento de um Fornecedor

```
GET /api/v1/estudos/{estudo_id}/fornecedores/{fornecedor_id}/enriquecimento
```

Retorna o objeto `Enriquecimento` completo (para exibição no painel de detalhes).

---

### 6.4 Stream de Atualizações (Opcional — v1.5)

Via SSE no mesmo job endpoint:
```
GET /api/v1/estudos/{estudo_id}/enriquecer/{job_id}/stream
Accept: text/event-stream

event: fornecedor_concluido
data: { "fornecedor_id": "cnpj_12345", "resumo": "..." }

event: job_concluido
data: { "total": 8, "concluidos": 7, "falhos": 1 }
```

---

## 7. Fluxo de UX (Frontend)

### 7.1 Ponto de Entrada

Na tela de detalhe do estudo (`EstudoDetail`), adicionar:

1. **Botão no cabeçalho:** *"Enriquecer favoritos"* → dispara enriquecimento em massa dos favoritos não enriquecidos
2. **Botão por fornecedor:** ícone de "adicionar detalhes" (lupa com +) em cada card de fornecedor → enriquece apenas aquele
3. **Seleção múltipla:** checkbox por fornecedor + botão contextual "Enriquecer selecionados (N)"

### 7.2 Indicadores Visuais

| Estado | Visual |
|---|---|
| Não enriquecido | Card normal, sem badge |
| Processando | Spinner + texto "Enriquecendo..." no card |
| Enriquecido recente (< 30 dias) | Badge azul "Dados enriquecidos" |
| Enriquecido vencido (> 30 dias) | Badge laranja "Atualizar" |
| Falhou | Badge vermelho "Erro — tentar de novo" |
| Tem alertas | Badge vermelho "⚠️ N alertas" no canto do card |

### 7.3 Painel de Dossiê

Ao clicar no card enriquecido, abrir drawer lateral com:

- **Resumo executivo** (2-3 linhas escritas pelo LLM)
- **Presença digital** (ícones clicáveis: site, LinkedIn, WhatsApp)
- **Reputação** (notas + link para ReclameAqui / Google Reviews)
- **Últimas notícias** (últimos 6 meses — timeline)
- **Alertas** (se houver — destacados em vermelho)
- **Fontes consultadas** (expandível — lista completa de URLs)
- Botão **"Atualizar dados"** (forçar re-enriquecimento)

---

## 8. Algoritmo de Enriquecimento

Pseudocódigo do fluxo por fornecedor:

```python
async def enriquecer_fornecedor(f: FornecedorSelecionado) -> Enriquecimento:
    # 1. Cache check
    cache_key = hash(f.cnpj or f.nome + f.uf)
    if cached := await redis.get(f"enrich:{cache_key}"):
        return Enriquecimento(**cached)

    # 2. Monta queries direcionadas
    queries = [
        f"{f.nome} {f.cnpj} site",
        f"{f.nome} {f.municipio} reclame aqui",
        f"{f.nome} {f.municipio} notícias",
        f"{f.nome} LinkedIn empresa",
        f"{f.nome} processo judicial OR autuação",
    ]

    # 3. Executa buscas (paralelas, com rate limit)
    search_results = await asyncio.gather(*[
        tavily.search(q, max_results=5, lang="pt") for q in queries
    ])
    urls = dedupe(flatten(search_results))[:12]

    # 4. Tavily já traz conteúdo — senão, scraping leve
    contents = [r.content for r in search_results if r.content]

    # 5. LLM estrutura
    prompt = build_enrichment_prompt(f, contents)
    dossie = await llm.structured_output(prompt, schema=Enriquecimento)

    # 6. Persist
    await mongo.update_fornecedor_enriquecimento(f.id, dossie)
    await redis.setex(f"enrich:{cache_key}", 30 * 86400, dossie.json())

    return dossie
```

### Prompt LLM (estruturado)

O LLM recebe:
- Dados oficiais do fornecedor (nome, CNPJ, localização, CNAE)
- Conteúdo das páginas coletadas (truncado em 20k tokens)
- Schema-obrigatório `Enriquecimento` (via *structured output*)

E é instruído a:
- **Citar fontes** em cada afirmação (URL no campo `fontes`)
- **Não inventar** — se o dado não está nas fontes, retornar `null`
- **Sinalizar alertas** com nível de confiança
- **Resumir** em linguagem objetiva (parágrafo único de 2-3 frases)

---

## 9. Impactos na Plataforma

### 9.1 Novos serviços / dependências

| Componente | Novo? | Impacto |
|---|:---:|---|
| Tavily API (provedor primário) | ✅ | Chave de API + quota mensal |
| Brave API (fallback) | ✅ | Chave de API |
| LangGraph node `enriquecer_fornecedor` | ✅ | Novo nó no grafo de agentes |
| Worker assíncrono (FastAPI BackgroundTasks ou Celery) | ✅ | Processamento fora do request |
| Migração MongoDB | ✅ | Nenhuma — campo novo é opcional |
| Cache Redis | — | Reutiliza infraestrutura atual |

### 9.2 Endpoints afetados

| Endpoint | Mudança |
|---|---|
| `POST /estudos/{id}/enriquecer` | **Novo** |
| `GET /estudos/{id}/enriquecer/{job_id}` | **Novo** |
| `GET /estudos/{id}/enriquecer/{job_id}/stream` | **Novo (v1.5)** |
| `GET /estudos/{id}` | Retorna `enriquecimento` quando existir |
| `DELETE /estudos/{id}/fornecedores/{fid}` | Remove também o enriquecimento em cache |

---

## 10. Custos e Limites Projetados

### 10.1 Custo por enriquecimento (estimativa)

| Item | Custo unitário | Qtd. por fornecedor | Subtotal |
|---|---|---|---|
| Tavily search | $0,008/query | 5 queries | $0,040 |
| LLM (GPT-4o, ~3k tokens in + 1k out) | $0,015/1k in + $0,06/1k out | — | $0,105 |
| **Total por fornecedor** | — | — | **~$0,15** |

### 10.2 Estimativa mensal

| Cenário | Enriquecimentos/mês | Custo/mês |
|---|---:|---:|
| Piloto (10 usuários × 20 enriq.) | 200 | ~$30 |
| Produção baixa (50 usuários × 40 enriq.) | 2.000 | ~$300 |
| Produção plena (200 usuários × 80 enriq.) | 16.000 | ~$2.400 |

> Cache compartilhado reduz esse custo em ~40% na prática (fornecedores populares como grandes mineradoras são enriquecidos apenas uma vez a cada 30 dias).

### 10.3 Limites operacionais (v1)

| Limite | Valor | Justificativa |
|---|---:|---|
| Enriquecimentos por chamada de API | 20 | Evita jobs muito longos |
| Enriquecimentos paralelos por usuário | 5 | Controle de carga |
| Enriquecimentos por minuto por usuário | 30 | Rate limit anti-abuso |
| TTL do cache | 30 dias | Equilíbrio entre frescor e custo |
| Tokens máximos enviados ao LLM | 20.000 | Proteção de custo e latência |

---

## 11. Decisões de Design e Justificativas

| Decisão | Justificativa |
|---|---|
| Enriquecimento é **opt-in** (sob demanda) | Evita custo recorrente para dados que talvez nunca sejam lidos |
| Cache compartilhado entre estudos | Mesma empresa aparece em múltiplos estudos — desperdício pagar duas vezes |
| TTL de 30 dias | Reputação e presença digital não mudam todo dia; garante frescor suficiente |
| Processamento assíncrono (job) | Enriquecer 20 fornecedores pode levar 60-90s — não bloqueia a UI |
| LLM com `structured_output` | Garante schema consistente; elimina parsing frágil |
| Tavily + Brave, não Google direto | Google ToS proíbe scraping; APIs profissionais têm preço previsível |
| Dossiê persistido no MongoDB | Analista deve poder reler sem reprocessar; histórico preservado |
| Alertas com nível de confiança | LLM pode errar; usuário decide quanto peso dar a cada sinal |
| Citação obrigatória de fontes | Auditabilidade — qualquer afirmação deve ter URL rastreável |

---

## 12. Considerações de Compliance e Ética

| Tema | Abordagem |
|---|---|
| **LGPD** | Apenas dados públicos indexáveis por buscadores são coletados; não há processamento de dados pessoais sensíveis |
| **Direitos autorais** | Conteúdo é resumido (fair use) e sempre acompanhado de link para a fonte original |
| **Viés do LLM** | Alertas sempre vinculados a fonte; usuário decide — plataforma não *classifica* fornecedores automaticamente |
| **Transparência** | Painel sempre mostra quando o dossiê foi gerado e quais URLs foram consultadas |
| **robots.txt** | Provedores (Tavily/Brave) já respeitam — não fazemos scraping próprio |

---

## 13. Sequência de Implementação Recomendada

```
Sprint 1 — Infraestrutura base
  ├── Registrar conta Tavily + Brave + obter API keys
  ├── Variáveis .env (TAVILY_API_KEY, BRAVE_API_KEY)
  ├── Cliente HTTP wrapper para Tavily/Brave com fallback
  ├── Schema Enriquecimento em backend/app/schemas/estudos.py
  └── Migração opcional (índice MongoDB em fornecedor_id)

Sprint 2 — Orquestração e persistência
  ├── Node LangGraph "enriquecer_fornecedor"
  ├── Endpoint POST /estudos/{id}/enriquecer (disparo assíncrono)
  ├── Endpoint GET /estudos/{id}/enriquecer/{job_id}
  ├── Cache Redis com TTL
  └── Testes com 3 fornecedores reais (mineradora + empresa média + MEI)

Sprint 3 — Frontend
  ├── Botão "Enriquecer favoritos" no EstudoDetail
  ├── Badges de estado no card de fornecedor
  ├── Drawer de dossiê com seções (resumo, reputação, mídia, alertas)
  ├── Polling do job_id (ou SSE em fase posterior)
  └── Tratamento de falhas com retry

Sprint 4 — Refinamento
  ├── Prompt engineering (iteração sobre qualidade dos resumos)
  ├── Ajuste fino de queries por tipo de fornecedor (ANM vs. CNPJ)
  ├── Monitoramento de custo (Grafana / Azure Application Insights)
  └── Quota per-user para proteção contra abuso
```

---

## 14. Fora de Escopo (versão 1)

- Exportação do dossiê em PDF
- Monitoramento contínuo com alertas automáticos (ex.: "nova notícia negativa sobre fornecedor X")
- Análise de sentimento agregada (ex.: gráfico temporal de menções)
- Cruzamento com fontes privadas pagas (Serasa, Boa Vista)
- Comparativo lado a lado entre fornecedores enriquecidos
- Enriquecimento automático de todos os fornecedores ao adicionar (poluiria custo)

Esses itens podem ser endereçados em versões futuras sem quebrar a arquitetura aqui proposta.

---

## 15. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|:---:|:---:|---|
| Provedor de busca indisponível | Média | Alto | Fallback Tavily → Brave; degradação graciosa |
| LLM gerar alerta falso | Média | Médio | Confiança obrigatória + citação; UI deixa claro que é sinal, não verdade |
| Custo escalar descontrolado | Média | Alto | Quota por usuário + monitoramento em tempo real |
| Fornecedor pequeno sem pegada digital | Alta | Baixo | Retornar "dados insuficientes" explícito em vez de inventar |
| Fontes em idioma estrangeiro | Baixa | Baixo | Filtro `lang=pt` nas queries + LLM traduz se necessário |
| Cache invalidado causa rebuild em massa | Baixa | Médio | TTL escalonado (não todos vencem no mesmo dia) |
