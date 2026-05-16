# Especificação: Controle de Acesso por Níveis — MineralRadar v2

> **Data:** 23/04/2026
> **Autor:** Leonardo de Melo — Especialista em Engenharia de Software e IA
> **Status:** Rascunho para revisão
> **Objetivo:** Definir arquitetura, modelo de dados, fluxos de negócio e contrato de API para o sistema de controle de acesso com dois perfis de usuário: **Admin** e **Membro**.

---

## 1. Visão Geral

O modelo de acesso é inspirado no Microsoft Teams: o **Admin** gerencia quem pode acessar o quê, e os **Membros** trabalham dentro do escopo que lhes foi concedido. Não há autoregistro — membros só entram se forem adicionados pelo admin.

Para facilitar o cadastro, o admin não precisa digitar e-mails manualmente: a plataforma consulta a **API do Microsoft Graph** para listar usuários ativos do Active Directory da AG com autocomplete por nome. O usuário já vem validado pelo AD — basta o admin selecionar e confirmar.

### Regras Centrais

| Ação | Admin | Membro |
|---|:---:|:---:|
| Criar obras | ✅ | ❌ |
| Editar / excluir obras | ✅ | ❌ |
| Visualizar obras atribuídas | ✅ | ✅ |
| Adicionar membros a uma obra | ✅ | ❌ |
| Remover membros de uma obra | ✅ | ❌ |
| Criar estudos (nas obras que tem acesso) | ✅ | ✅ |
| Editar / excluir próprios estudos | ✅ | ✅ |
| Editar / excluir estudos de outros | ✅ | ❌ |
| Visualizar estudos da obra | ✅ | ✅ (só da sua obra) |
| Gerenciar usuários (cadastro global) | ✅ | ❌ |

---

## 2. Integração com Microsoft Graph API (Autocomplete de Usuários AD)

### 2.1 Objetivo

Ao adicionar um membro a uma obra, o admin digita parte do nome ou e-mail e a plataforma retorna sugestões de usuários **ativos no AD da AG** em tempo real — igual ao comportamento do Teams ("Adicionar membros"). Campos retornados: `displayName` e `mail` (ou `userPrincipalName`).

### 2.2 Endpoint do MineralRadar (proxy para Graph API)

```
GET /api/v1/ad/users/search?q={termo}
Authorization: Bearer {token_admin}
```

**Resposta:**
```json
[
  { "ad_id": "oid-azure-abc", "nome": "Leonardo Siqueira de Melo", "email": "LEONARDO.MELO@ag.com.br" },
  { "ad_id": "oid-azure-def", "nome": "Leonardo Santos Martinho",  "email": "LEONARDO.MARTINHO@ag.com.br" }
]
```

> O backend faz a chamada à Graph API usando o token da aplicação (client credentials), não do usuário. Requer permissão `User.Read.All` no registro da aplicação no Azure AD.

### 2.3 Chamada ao Microsoft Graph

```python
# Backend — buscar usuários AD por nome/email
GET https://graph.microsoft.com/v1.0/users
  ?$filter=startswith(displayName,'{q}') or startswith(mail,'{q}')
  &$select=id,displayName,mail,userPrincipalName,accountEnabled
  &$filter=accountEnabled eq true
  &$top=10
Authorization: Bearer {app_token}
```

> Parâmetros mínimos: `id` (OID do Azure AD), `displayName` (nome), `mail` (e-mail). O `id` do Azure AD é usado como identificador na coleção `users` local, evitando duplicidade de identidade.

### 2.4 Fluxo de Adição de Membro (com autocomplete)

```
1. Admin abre tela "Gerenciar Membros" de uma obra
2. Admin digita "leona" no campo de busca
3. Frontend → GET /api/v1/ad/users/search?q=leona
4. Backend → Graph API → retorna lista de usuários AD ativos
5. Admin seleciona "Leonardo Siqueira de Melo" na lista
6. Admin clica "Adicionar"
7. Frontend → POST /api/v1/obras/{obra_id}/membros
   { "ad_id": "oid-azure-abc", "nome": "Leonardo Siqueira de Melo", "email": "LEONARDO.MELO@ag.com.br" }
8. Backend:
   a. Verifica se já existe registro em `users` com esse ad_id → cria se não existir
   b. Adiciona user._id em obras.membros_ids
9. Retorna lista atualizada de membros da obra
```

---

## 3. Modelo de Dados

### 3.1 Coleção `users` (nova — MongoDB)

```json
{
  "_id": "ObjectId",
  "ad_id": "oid-azure-abc123",
  "email": "leonardo.melo@ag.com.br",
  "nome": "Leonardo Siqueira de Melo",
  "role": "admin" | "membro",
  "ativo": true,
  "criado_por": "user_id_do_admin",
  "created_at": "ISODate",
  "updated_at": "ISODate"
}
```

| Campo | Tipo | Regra |
|---|---|---|
| `ad_id` | string | OID do Azure AD — identificador único, imutável |
| `email` | string | Único, case-insensitive, sincronizado do AD |
| `nome` | string | Sincronizado do AD no momento do cadastro |
| `role` | enum | `admin` \| `membro` — apenas admin pode alterar |
| `ativo` | bool | Admin pode desativar sem excluir (acesso bloqueado imediatamente) |
| `criado_por` | string | `_id` do admin que realizou o cadastro |

> **Índices:** `{ ad_id: 1 }` unique + `{ email: 1 }` unique + `{ role: 1, ativo: 1 }`.

---

### 3.2 Mudança na Coleção `obras` (existente)

Adicionar o campo:

```json
{
  "membros_ids": ["user_id_1", "user_id_2"]
}
```

| Campo | Tipo | Regra |
|---|---|---|
| `membros_ids` | `list[str]` | IDs (`_id` MongoDB) dos membros com acesso à obra. Gerenciado exclusivamente pelo admin. |

> O campo `created_by` já existe e passa a ser o identificador do admin criador.

---

### 3.3 Coleção `estudos` (existente — sem mudança estrutural)

A vinculação do estudo ao usuário já existe via `created_by`. As regras de visibilidade passam a ser derivadas do acesso à obra pai, não mais de `compartilhado_com` (que pode ser depreciado nesta versão).

---

## 4. Fluxos de Negócio

### 4.1 Primeiro Acesso de um Membro

```
Membro abre o MineralRadar → autentica com conta Microsoft (Azure AD)
Backend valida o JWT → extrai email/ad_id → busca em `users`
  └── Não encontrado → HTTP 403 "Acesso não autorizado.
                        Solicite ao administrador que adicione você ao sistema."
  └── Encontrado, ativo: false → HTTP 403 "Usuário desativado"
  └── Encontrado, ativo: true → acesso liberado com role do banco
```

> O membro não precisa ser pré-cadastrado manualmente antes de ser adicionado a uma obra. O cadastro na coleção `users` acontece automaticamente no momento em que o admin o seleciona via autocomplete (passo 8a do fluxo de adição).

---

### 4.2 Adição de Membro a uma Obra (detalhado)

```
Admin → POST /api/v1/obras/{obra_id}/membros
  { "ad_id": "oid-...", "nome": "...", "email": "..." }

Sistema:
  1. Valida que solicitante é admin
  2. Busca em `users` por ad_id
     └── Não existe → cria com role: "membro", ativo: true, criado_por: admin.id
     └── Existe e ativo: false → retorna 409 "Usuário desativado, reative antes de adicionar"
     └── Existe → usa o registro existente
  3. Adiciona user._id em obras.membros_ids (se já não estiver)
  4. Retorna lista atualizada de membros
```

---

### 4.3 Criação de Estudo (pelo Membro)

```
Membro → POST /api/v1/estudos
  { obra_id: "...", titulo: "..." }

Sistema:
  1. Valida autenticação
  2. Carrega obra
  3. Verifica se user._id ∈ obras.membros_ids OU user.role == "admin"
  4. Se não → HTTP 403 "Sem acesso a esta obra"
  5. Cria o estudo com created_by = user._id
```

---

### 4.4 Listagem de Obras (por perfil)

```
Admin  → GET /api/v1/obras → vê TODAS as obras
Membro → GET /api/v1/obras → vê apenas obras onde user._id ∈ membros_ids
```

---

### 4.5 Listagem de Estudos (por perfil)

```
Admin  → GET /api/v1/estudos?obra_id=X → todos os estudos da obra
Membro → GET /api/v1/estudos?obra_id=X → apenas se tiver acesso à obra
         (dentro da obra: vê todos os estudos, não só os seus)
```

---

## 5. Contrato de API (novos endpoints)

### 5.1 Busca de Usuários AD — `/api/v1/ad/users/search`

| Método | Rota | Quem acessa | Descrição |
|---|---|---|---|
| `GET` | `/ad/users/search?q={termo}` | Admin | Busca usuários ativos no AD com autocomplete |

**Resposta 200:**
```json
[
  { "ad_id": "oid-abc", "nome": "Leonardo Siqueira de Melo", "email": "LEONARDO.MELO@ag.com.br" },
  { "ad_id": "oid-def", "nome": "Leonardo Santos Martinho",  "email": "LEONARDO.MARTINHO@ag.com.br" }
]
```

> Mínimo 2 caracteres para disparar a busca. Limite de 10 resultados por query.

---

### 5.2 Usuários — `/api/v1/users`

| Método | Rota | Quem acessa | Descrição |
|---|---|---|---|
| `GET` | `/users` | Admin | Listar todos os usuários cadastrados no sistema |
| `GET` | `/users/{user_id}` | Admin | Detalhar usuário |
| `PUT` | `/users/{user_id}` | Admin | Alterar role / ativar / desativar |
| `DELETE` | `/users/{user_id}` | Admin | Soft delete via `ativo: false` |

> Não há `POST /users` manual — o cadastro ocorre automaticamente via fluxo de adição à obra.

---

### 5.3 Membros por Obra — `/api/v1/obras/{obra_id}/membros`

| Método | Rota | Quem acessa | Descrição |
|---|---|---|---|
| `GET` | `/obras/{id}/membros` | Admin | Listar membros com acesso |
| `POST` | `/obras/{id}/membros` | Admin | Adicionar membro (via seleção do AD) |
| `DELETE` | `/obras/{id}/membros/{user_id}` | Admin | Remover membro da obra |

**POST body:**
```json
{
  "ad_id": "oid-azure-abc",
  "nome": "Leonardo Siqueira de Melo",
  "email": "LEONARDO.MELO@ag.com.br"
}
```

**Resposta 200 (lista de membros da obra):**
```json
[
  {
    "_id": "664abc...",
    "ad_id": "oid-azure-abc",
    "email": "LEONARDO.MELO@ag.com.br",
    "nome": "Leonardo Siqueira de Melo",
    "ativo": true
  }
]
```

---

## 6. Mudanças na Camada de Autorização (Backend)

### 6.1 Validação do usuário no login (`deps.py`)

```python
async def get_current_user(token: ..., db: Database) -> TokenPayload:
    payload = validate_azure_ad_token(token)           # valida JWT Azure AD
    user = await db["users"].find_one({"ad_id": payload.sub})
    if not user:
        raise HTTPException(403, "Usuário não autorizado. Solicite acesso ao administrador.")
    if not user["ativo"]:
        raise HTTPException(403, "Usuário desativado.")
    payload.role = user["role"]          # role vem do banco, não do token
    payload.user_db_id = str(user["_id"])
    return payload
```

> A `role` vem do MongoDB, não do JWT Azure AD. Admin pode revogar/alterar permissões sem forçar novo login.

---

### 6.2 Guard de acesso à obra (`deps.py` — nova dependência)

```python
async def require_obra_access(
    obra_id: str,
    current_user: CurrentUser,
    db: Database,
) -> Obra:
    obra = await db["obras"].find_one({"_id": ObjectId(obra_id)})
    if not obra:
        raise HTTPException(404, "Obra não encontrada")
    if current_user.role == "admin":
        return obra                       # admin acessa tudo
    if current_user.user_db_id not in obra.get("membros_ids", []):
        raise HTTPException(403, "Sem acesso a esta obra")
    return obra
```

---

## 7. Mudanças no Frontend

### 7.1 Contexto de usuário global (`useAuth`)

```ts
interface CurrentUser {
  id: string       // _id MongoDB
  ad_id: string    // OID Azure AD
  email: string
  nome: string
  role: 'admin' | 'membro'
}
```

### 7.2 Proteção de UI por role

| Elemento | Condição de exibição |
|---|---|
| Botão "Nova obra" | `role === 'admin'` |
| Botão "Editar obra" | `role === 'admin'` |
| Botão "Excluir obra" | `role === 'admin'` |
| Botão "Gerenciar membros" (obra) | `role === 'admin'` |
| Menu de administração de usuários | `role === 'admin'` |
| Botão "Novo estudo" | `role === 'admin' \|\| obraAcessivel` |

> Proteção de UI é complementar — a API já bloqueia na origem.

### 7.3 Nova tela: Gerenciar Membros da Obra

Acessível via botão na tela de detalhe da obra (`ObraDetail.tsx`), visível apenas para admin:

- Lista os membros atuais com nome, e-mail e botão de remover
- Campo de busca com **autocomplete** chamando `GET /api/v1/ad/users/search?q=...`
- Ao digitar ≥ 2 caracteres, exibe dropdown com sugestões (nome + e-mail) — idêntico ao Teams
- Admin seleciona o usuário e clica "Adicionar"
- Feedback de confirmação ou erro (ex.: usuário já é membro, usuário desativado)

### 7.4 Nova tela: Administração de Usuários (`/admin/usuarios`)

Acessível apenas com `role === 'admin'`:

- Tabela de usuários cadastrados (nome, e-mail, role, ativo/inativo, data de criação)
- Toggle ativo/inativo por usuário
- Alteração de role (membro → admin e vice-versa)
- Opção de remover usuário (soft delete)

> Não há botão "Cadastrar manualmente" — o cadastro sempre parte da seleção via AD.

---

## 8. Sequência de Implementação Recomendada

```
Sprint 1 — Backend
  ├── Permissão User.Read.All no registro da app no Azure AD
  ├── Endpoint GET /ad/users/search (proxy para Graph API)
  ├── Criar coleção `users` com índices (ad_id, email)
  ├── Endpoint POST/GET/DELETE /obras/{id}/membros
  ├── Endpoint GET/PUT/DELETE /users (admin only)
  ├── Adaptar deps.py: validar user por ad_id + injetar role do banco
  ├── Adaptar require_obra_access (membros_ids)
  ├── Adaptar list_obras (filtrar por membros_ids para membro)
  └── Adaptar list_estudos (filtrar por acesso à obra)

Sprint 2 — Frontend
  ├── Atualizar useAuth com role, user_db_id e ad_id reais
  ├── Proteção condicional de UI por role
  ├── Componente autocomplete de usuários AD (reutilizável)
  ├── Painel "Gerenciar membros" na ObraDetail
  └── Tela /admin/usuarios

Sprint 3 — Azure AD (se ainda não implementado)
  ├── Conectar MSAL no frontend (substituir dev-token)
  ├── Validar JWT Azure AD no backend (substituir mock)
  └── Cruzar ad_id (sub do token) com coleção users
```

---

## 9. Decisões de Design e Justificativas

| Decisão | Justificativa |
|---|---|
| Role vem do banco, não do JWT | Admin pode alterar role sem forçar novo login do usuário |
| Cadastro automático via seleção AD | Elimina formulário manual; usuário já vem validado pelo AD da AG |
| `ad_id` como chave de identidade | OID do Azure AD é imutável e único — mais confiável que email (que pode mudar) |
| Sem autoregistro | Controle total do admin; equivalente ao fluxo de convite do Teams |
| Membro vê todos os estudos da obra | Colaboração é o objetivo; estudos por obra são sempre de contexto compartilhado |
| Soft delete em usuários | Preserva histórico de `created_by` em obras/estudos sem quebrar integridade referencial |
| `membros_ids` na coleção `obras` | Consulta direta sem join — mais performático para listagem no MongoDB |
| Admin sempre tem acesso a tudo | Simplifica o guard: `role == admin → bypass`; sem necessidade de cadastrá-lo em cada obra |

---

## 10. Impacto em Endpoints Existentes

| Endpoint | Mudança necessária |
|---|---|
| `POST /obras` | Adicionar guard `require_role("admin")` |
| `PUT /obras/{id}` | Adicionar guard `require_role("admin")` |
| `DELETE /obras/{id}` | Adicionar guard `require_role("admin")` |
| `GET /obras` | Filtrar por `membros_ids` se membro |
| `POST /estudos` | Validar acesso à obra pai via `require_obra_access` |
| `GET /estudos` | Filtrar por acesso à obra pai |
| `PUT /estudos/{id}` | Validar `created_by == user._id` OU `role == admin` |
| `DELETE /estudos/{id}` | Validar `created_by == user._id` OU `role == admin` |

---

## 11. Fora de Escopo (versão 1)

- Notificação por e-mail ao membro adicionado
- Múltiplos admins com hierarquia
- Perfis customizados além de admin/membro
- Auditoria de ações (log de quem fez o quê)
- Expiração de acesso por data
- Sincronização automática de desligamentos do AD (usuário demitido)

Esses itens podem ser endereçados em versões futuras sem quebrar a arquitetura aqui proposta.
