# Estudo: Sistema de Memória — MineralRadar v2

> Data: 11/03/2026 | Sprint: S9 | Status: Análise completa

---

## 1. Visão Geral

O MineralRadar utiliza um agente IA (LangGraph + Azure OpenAI gpt-4o) que precisa
de dois tipos de memória:

| Tipo | Escopo | Armazenamento | Estado |
|------|--------|---------------|--------|
| **Curto prazo** | Intra-sessão (multi-turno) | Redis List | ✅ Implementado |
| **Longo prazo** | Cross-sessão (perfil do usuário) | MongoDB Collections | ❌ Não implementado |

---

## 2. Memória de Curto Prazo — Redis (Implementado ✅)

### 2.1 Classe: `RedisConversationBuffer`

**Arquivo:** `backend/app/memory/conversation_buffer.py`

| Propriedade | Valor |
|------------|-------|
| Chave Redis | `conv:{session_id}:messages` |
| Estrutura | Redis List (RPUSH + LTRIM) |
| TTL | 7.200s (2 horas), reset a cada escrita |
| Limite | 20 mensagens (≈10 turnos humano+IA) |
| Tipos serializados | `HumanMessage`, `AIMessage`, `ToolMessage`, `SystemMessage` |
| Formato de serialização | JSON compacto (`type`, `content`, `tool_calls`, `tool_call_id`) |
| Fallback se Redis offline | Lista vazia (agente funciona sem memória) |

### 2.2 Fluxo Atual (`POST /api/v1/chat`)

```
1. Cliente envia {message, session_id?}
2. session_id = body.session_id || uuid4()
3. buffer = RedisConversationBuffer(redis)
4. history = await buffer.load(session_id)       ← Redis LRANGE
5. all_messages = [*history, HumanMessage(msg)]
6. result = await graph.ainvoke({messages: all_messages, ...})
7. await buffer.append(session_id, [human, ai])  ← Redis RPUSH + LTRIM + EXPIRE
8. return ChatResponse(...)
```

### 2.3 O que funciona bem

- **Contexto multi-turno**: validado com sessão real (busca jazidas → detalhe
  do processo → dados cadastrais da empresa). O agente mantém contexto entre turnos.
- **Roteamento contextual**: o `route` muda automaticamente (mineral → empresa)
  com base na intenção, mas o agente mantém conhecimento do contexto anterior.
- **Serialização robusta**: suporta `tool_calls` e `ToolMessage`, mantendo o
  ciclo completo de raciocínio agent→tool→agent no histórico.
- **Graceful degradation**: se Redis cair, o buffer retorna lista vazia e o
  agente responde sem histórico (sem crash).

### 2.4 Limitações atuais

| Limitação | Impacto | Mitigação possível |
|-----------|---------|-------------------|
| **TTL fixo de 2h** | Sessão morre se usuário pausar >2h | Configurável via env var |
| **20 mensagens máximas** | Conversas longas perdem contexto antigo | Sumarização antes do truncamento |
| **Sem sumarização** | Quando LTRIM corta, informação é perdida silenciosamente | Criar `ConversationSummarizer` |
| **Sem vinculação a usuário** | Cada session_id é isolado, sem perfil cross-sessão | Implementar `user_id` no fluxo |
| **Sem persistência dos tool_calls** | Só content é salvo legível; tool_calls são objetos | Serialização já implementada mas não explorada para analytics |

---

## 3. Memória de Longo Prazo — MongoDB (Não Implementado ❌)

### 3.1 Objetivo

Permitir que o agente "lembre" informações entre sessões diferentes:

- Quais obras o usuário trabalha frequentemente
- Preferências de busca (raio, substâncias, UF)
- Resumo de conversas anteriores relevantes
- Fornecedores já avaliados / descartados

### 3.2 Arquitetura Proposta

Duas coleções MongoDB:

#### Collection: `chat_sessions`

Armazena o resumo de cada sessão encerrada.

```json
{
  "_id": "ObjectId",
  "session_id": "d2ad386f-0735-46bd-a216-36ae522d74cb",
  "user_id": "user@company.com",
  "obra_id": "69aaf0669e7baf8ee1de147a",
  "started_at": "2026-03-10T21:00:00Z",
  "ended_at": "2026-03-10T21:15:00Z",
  "turn_count": 6,
  "route_history": ["mineral", "mineral", "empresa"],
  "summary": "Usuário buscou jazidas de areia lavada próximas a Campinas/SP num raio de 100km. Encontrou 10 jazidas ANM. Detalhou o processo 820.322/2002 (Quibrita Mineradora). Obteve dados cadastrais completos: CNPJ 28.543.070/0001-05, telefone (11) 4591-0149, email nfe@quibrita.com.br.",
  "entities_mentioned": [
    {"type": "jazida", "id": "820.322/2002", "nome": "Quibrita Mineradora"},
    {"type": "empresa", "cnpj": "28543070000105", "nome": "Quibrita Mineradora Ltda"},
    {"type": "municipio", "nome": "Campinas", "uf": "SP"}
  ],
  "tags": ["areia", "campinas", "fornecedor-contatado"]
}
```

#### Collection: `user_memory`

Armazena fatos persistentes por usuário, atualizados incrementalmente.

```json
{
  "_id": "ObjectId",
  "user_id": "user@company.com",
  "updated_at": "2026-03-10T21:15:00Z",
  "preferences": {
    "default_raio_km": 100,
    "substancias_frequentes": ["areia", "brita", "calcário"],
    "ufs_interesse": ["SP", "MG", "MT"]
  },
  "obras_ativas": [
    {"obra_id": "69aaf0669e7baf8ee1de147a", "nome": "Rodovia BR-381", "uf": "MG"}
  ],
  "fornecedores_avaliados": [
    {
      "cnpj": "28543070000105",
      "nome": "Quibrita Mineradora Ltda",
      "avaliacao": "positiva",
      "nota": "Contato realizado, preço competitivo",
      "data": "2026-03-10"
    }
  ],
  "facts": [
    "Trabalha com obras rodoviárias no sudeste do Brasil",
    "Prefere fornecedores com Concessão de Lavra (fase mais avançada)",
    "Costuma buscar areia e brita para camadas de pavimentação"
  ]
}
```

### 3.3 Classe Proposta: `LongTermMemory`

**Arquivo:** `backend/app/memory/long_term.py`

```python
class LongTermMemory:
    """
    Memória de longo prazo persistida em MongoDB.

    Responsabilidades:
    - Sumarizar sessões encerradas (via LLM)
    - Extrair entidades e fatos da conversa
    - Atualizar perfil do usuário incrementalmente
    - Injetar contexto cross-sessão no system prompt
    """

    SESSIONS_COLLECTION = "chat_sessions"
    MEMORY_COLLECTION = "user_memory"

    async def summarize_session(session_id, user_id, messages) -> dict:
        """Sumariza uma sessão via LLM e salva em chat_sessions."""

    async def extract_facts(messages) -> list[str]:
        """Extrai fatos relevantes das mensagens via LLM."""

    async def update_user_memory(user_id, session_summary) -> None:
        """Atualiza user_memory com novos fatos/preferências."""

    async def load_context(user_id) -> str:
        """Retorna contexto formatado para injetar no system prompt."""

    async def get_recent_sessions(user_id, limit=5) -> list[dict]:
        """Retorna últimas N sessões resumidas."""
```

### 3.4 Trigger de Sumarização

Quando sumarizar uma sessão? Duas opções:

| Trigger | Prós | Contras |
|---------|------|---------|
| **On-demand (TTL expire)** | Sem custo extra durante a sessão | Precisa de job agendado para capturar antes do TTL |
| **Ao encerrar sessão** | Garante que nada se perde | Precisa de sinal explícito (botão "nova conversa" ou inatividade) |

**Recomendação:** Híbrido:
1. `DELETE /chat/{session_id}` → trigger imediato de sumarização
2. Background job (a cada 30min) varre sessões Redis com >4 turnos e sem
   correspondente em `chat_sessions` → sumariza preventivamente
3. Ao carregar sessão com 18+ mensagens (próximo do limite 20) → sumariza
   as 10 mais antigas, substitui por um `SystemMessage` com o resumo

### 3.5 Integração com o Chat Flow

Mudança no `POST /api/v1/chat`:

```
1. session_id = body.session_id || uuid4()
2. buffer = RedisConversationBuffer(redis)
3. history = await buffer.load(session_id)

4. [NOVO] long_term = LongTermMemory(mongodb)
5. [NOVO] user_context = await long_term.load_context(user_id)
6. [NOVO] recent_sessions = await long_term.get_recent_sessions(user_id, limit=3)
7. [NOVO] Enriquecer system_prompt com user_context + recent_sessions

8. all_messages = [SystemMessage(enriched_prompt), *history, HumanMessage(msg)]
9. result = await graph.ainvoke(...)

10. await buffer.append(session_id, [human, ai])
11. [NOVO] Se turn_count > threshold → await long_term.summarize_session(...)
```

---

## 4. Sumarização via LLM

### 4.1 Prompt de Sumarização

```text
Você receberá o histórico de uma conversa entre um usuário e o assistente MineralRadar.

Gere um JSON com:
1. "summary": resumo conciso em 2-3 frases do que foi discutido e decidido
2. "entities_mentioned": lista de entidades (jazida, empresa, município) com tipo e identificador
3. "facts": lista de até 5 fatos sobre o usuário (preferências, padrões) que seriam úteis em futuras conversas
4. "tags": lista de palavras-chave para categorização

Conversa:
{messages}
```

### 4.2 Modelo e Custo

| Parâmetro | Valor |
|-----------|-------|
| Modelo | `gpt-4o-mini` (mais barato para sumarização) |
| Max tokens | 512 |
| Temperature | 0.0 |
| Custo estimado | ~$0.0001 por sumarização |
| Quando executar | Assíncrono (background task, não bloqueia resposta) |

---

## 5. Injeção de Contexto no System Prompt

Quando o agente inicia uma nova sessão, o `SYSTEM_PROMPT` recebe blocos extras:

```text
[Prompt base existente...]

## Contexto do usuário (memória de longo prazo)
{user_context}

## Sessões recentes relevantes
{formatted_recent_sessions}
```

Exemplo de `user_context`:
```text
- O usuário trabalha com obras rodoviárias no sudeste do Brasil (SP, MG, MT)
- Substâncias de interesse frequente: areia, brita, calcário
- Raio de busca padrão: 100 km
- Fornecedor já contatado: Quibrita Mineradora (CNPJ: 28.543.070/0001-05) — avaliação positiva
```

Exemplo de `formatted_recent_sessions`:
```text
- [10/03 21:00] Buscou jazidas de areia em Campinas/SP. Contatou Quibrita Mineradora.
- [09/03 14:30] Pesquisou fornecedores de brita para obra BR-381 em MG. Raio 150km.
```

---

## 6. Diagrama de Arquitetura Completa

```
┌─────────────────────────────────────────────────────────────────────┐
│                      POST /api/v1/chat                              │
│─────────────────────────────────────────────────────────────────────│
│                                                                     │
│  ┌──────────────┐    ┌──────────────────────────┐                  │
│  │  Redis        │    │  MongoDB                  │                  │
│  │               │    │                           │                  │
│  │  conv:{sid}   │◄──►│  chat_sessions [❌]       │                  │
│  │  :messages    │    │  user_memory   [❌]       │                  │
│  │               │    │  obras         [✅]       │                  │
│  │  TTL: 2h      │    │  estudos       [✅]       │                  │
│  │  Limit: 20msg │    │                           │                  │
│  └──────┬───────┘    └──────────┬───────────────┘                  │
│         │                       │                                   │
│         ▼                       ▼                                   │
│  ┌──────────────────────────────────────────────┐                  │
│  │  Chat Route (chat.py)                         │                  │
│  │                                               │                  │
│  │  1. Load short-term history  ← Redis          │                  │
│  │  2. Load user context        ← MongoDB [❌]   │                  │
│  │  3. Build enriched prompt                     │                  │
│  │  4. Invoke LangGraph agent                    │                  │
│  │  5. Save turn to Redis                        │                  │
│  │  6. Trigger summarization    → MongoDB [❌]   │                  │
│  └──────────────────────────────────────────────┘                  │
│                       │                                             │
│                       ▼                                             │
│  ┌──────────────────────────────────────────────┐                  │
│  │  LangGraph Agent (graph.py)                   │                  │
│  │                                               │                  │
│  │  router → agent ⇄ tool_executor → END        │                  │
│  │                                               │                  │
│  │  LLM: Azure OpenAI gpt-4o (temp 0.1)         │                  │
│  │  Tools: MCP Jazidas + Empresas + Geo          │                  │
│  └──────────────────────────────────────────────┘                  │
│                                                                     │
│  [❌] = Não implementado (S9 pendente)                              │
│  [✅] = Implementado e validado                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. Índices MongoDB Necessários

```javascript
// chat_sessions
db.chat_sessions.createIndex({ "user_id": 1, "ended_at": -1 })
db.chat_sessions.createIndex({ "session_id": 1 }, { unique: true })
db.chat_sessions.createIndex({ "obra_id": 1, "ended_at": -1 })
db.chat_sessions.createIndex({ "tags": 1 })

// user_memory
db.user_memory.createIndex({ "user_id": 1 }, { unique: true })
```

---

## 8. Plano de Implementação

### Fase 1 — Fundação 

| # | Tarefa | Arquivo |
|---|--------|---------|
| 1.1 | Criar `LongTermMemory` class | `app/memory/long_term.py` |
| 1.2 | Exportar no `app/memory/__init__.py` | `app/memory/__init__.py` |
| 1.3 | Criar índices MongoDB | `app/db/mongodb.py` (startup) |
| 1.4 | Prompt de sumarização | `app/memory/prompts.py` |
| 1.5 | Testes unitários | `tests/memory/test_long_term.py` |

### Fase 2 — Integração no Chat 

| # | Tarefa | Arquivo |
|---|--------|---------|
| 2.1 | Injetar `user_context` no system prompt | `app/langgraph/graph.py` |
| 2.2 | Trigger de sumarização no chat route | `app/api/routes/chat.py` |
| 2.3 | Endpoint `GET /chat/sessions` | `app/api/routes/chat.py` |
| 2.4 | Background job de sumarização preventiva | `app/tasks/summarize.py` |

### Fase 3 — Auto-compactação 

| # | Tarefa | Arquivo |
|---|--------|---------|
| 3.1 | Detectar buffer >80% cheio (16+ msgs) | `app/memory/conversation_buffer.py` |
| 3.2 | Sumarizar mensagens antigas inline | `app/memory/long_term.py` |
| 3.3 | Substituir por SystemMessage resumo | `app/api/routes/chat.py` |

---

## 9. Priorização

| Prioridade | Item | Justificativa |
|------------|------|---------------|
| **P0** | `chat_sessions` + sumarização | Base para qualquer memória cross-sessão |
| **P0** | Injeção de contexto no prompt | O agente precisa "lembrar" entre sessões |
| **P1** | `user_memory` + extração de fatos | Personalização progressiva |
| **P1** | Auto-compactação do buffer | Evita perda silenciosa em conversas longas |
| **P2** | Background job preventivo | Robustez operacional |
| **P2** | UI de histórico de sessões | Frontend: listar conversas anteriores |

---

## 10. Dependências

| Dependência | Status |
|-------------|--------|
| MongoDB conectado | ✅ Pronto (`app/db/mongodb.py`) |
| Redis funcionando | ✅ Pronto (`app/db/redis.py`) |
| Azure OpenAI (gpt-4o-mini para sumarização) | ✅ Disponível (mesmo endpoint) |
| user_id no fluxo de autenticação | ⚠️ Parcial (hoje usa `dev-token`, sem user_id real) |

**Nota sobre `user_id`:** A memória de longo prazo depende de identificar o
usuário. Com o `dev-token` atual, pode-se usar um `user_id` fixo para
desenvolvimento. Em produção, virá do token JWT (Azure AD).
