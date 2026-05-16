"""
Memory Prompts
==============

LLM prompts used for conversation summarization and fact extraction.
"""

SUMMARIZE_SESSION_PROMPT = """\
Você receberá o histórico de uma conversa entre um usuário e o assistente \
MineralRadar (plataforma de inteligência para mineração estratégica e minerais críticos).

Analise a conversa e retorne um JSON válido com exatamente estas chaves:

{{
  "summary": "Resumo conciso em 2-3 frases do que foi discutido e decidido",
  "entities": [
    {{"type": "jazida|empresa|municipio|projeto", "id": "identificador", "nome": "nome legível"}}
  ],
  "facts": [
    "Fato sobre o usuário útil para futuras conversas (máximo 5)"
  ],
  "tags": ["palavra-chave-1", "palavra-chave-2"]
}}

Regras:
- "entities": inclua jazidas (nº processo), empresas (CNPJ), municípios e projetos mencionados.
- "facts": extraia preferências, padrões e decisões do usuário (não repita dados brutos).
- "tags": 3-6 palavras-chave para categorização.
- Retorne APENAS o JSON, sem markdown, sem explicação.

Conversa:
{conversation}"""


FORMAT_USER_CONTEXT_PROMPT = """\
Com base nas informações de perfil abaixo, gere um parágrafo conciso (3-5 linhas) \
descrevendo o usuário para contextualizar uma nova conversa com o assistente MineralRadar.

Perfil:
- Fatos conhecidos: {facts}
- Projetos ativos: {projetos}
- Fornecedores avaliados: {fornecedores}
- Substâncias frequentes: {substancias}
- UFs de interesse: {ufs}

Retorne apenas o texto descritivo, sem formatação markdown."""
