"""
Intent Router
==============

Classifies user intent to determine which tool subset the agent should use.

Routes:
    mineral  — Jazidas + Geo tools (ANM processes, mining, extraction)
    empresa  — Empresas + Geo tools (companies by CNAE, economic activity)
    hybrid   — All tools (mining + commercial, e.g. "fornecedores de brita")
    geo      — Geo tools only (locations, routes, distances)
    general  — No tools (greetings, general questions)

The router uses the same Azure OpenAI LLM with structured output binding
(with_structured_output) for reliable JSON classification in a single
low-token call before the main ReAct loop begins.
"""

import logging
import warnings
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("langgraph.router")

Route = Literal[
    "mineral", "empresa", "hybrid", "geo", "general",
    # Rotas dedicadas a buscas DENTRO de isócrona — restringem o tool set
    # à synthetic tool atômica geo__buscar_dentro_de_isocrona, eliminando
    # a chance do LLM esquecer de chamar calcular_isocrona ou tentar
    # buscar_empresas com raio quando o usuário pediu pela área.
    "mineral_em_isocrona", "empresa_em_isocrona", "hibrido_em_isocrona",
]

ROUTE_MINERAL = "mineral"
ROUTE_EMPRESA = "empresa"
ROUTE_HYBRID = "hybrid"
ROUTE_GEO = "geo"
ROUTE_GENERAL = "general"

ROUTE_MINERAL_EM_ISOCRONA = "mineral_em_isocrona"
ROUTE_EMPRESA_EM_ISOCRONA = "empresa_em_isocrona"
ROUTE_HIBRIDO_EM_ISOCRONA = "hibrido_em_isocrona"

DEFAULT_ROUTE = ROUTE_HYBRID


class RouteClassification(BaseModel):
    """Structured output for intent classification."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    route: Route = Field(description="The classified route for the user query")
    reasoning: str = Field(
        description="One-sentence reasoning for the chosen route"
    )


# ── Route configs: tool prefixes + system prompt hint per route ────────

ROUTE_CONFIGS: dict[str, dict] = {
    ROUTE_MINERAL: {
        "prefixes": ("jazidas__", "geo__", "empresas__"),
        "hint": (
            "O usuário busca jazidas ou processos ANM. "
            "Passos obrigatórios: "
            "(1) geo__buscar_municipio para resolver o local; "
            "(2) jazidas__buscar_jazidas para obter processos ANM; "
            "(3) jazidas__buscar_fornecedores se houver referência a titulares/fornecedores. "
            "Se a busca for DENTRO de um polígono específico (município, isócrona, área "
            "desenhada), use jazidas__jazidas_por_poligono(geometry=...) — para isócronas o "
            "geometry vem de feature.geometry de calcular_isocrona. "
            "Para análise de risco ambiental/jurídico de um processo ANM: após "
            "jazidas__detalhes_processo, chame **em paralelo** "
            "empresas__risco_ambiental_empresa(cnpj_basico=...) **e** "
            "empresas__autuacoes_por_area(lat, lon, raio_km) com o centróide da jazida "
            "(due diligence exige os dois: SIFISC por CNPJ + infrações georreferenciadas "
            "na área). "
            "Agrupe resultados por substância mineral no formato de saída. "
            "Para **comércio exterior** (export/import, NCM, FOB, volume, valor, "
            "países destino): use ``jazidas__consultar_mercado_mineral`` e/ou "
            "``jazidas__principais_destinos_mineral``. Para **preço de referência** "
            "de metal/mineral (Metals-API): ``jazidas__consultar_preco_mineral``."
        ),
    },
    ROUTE_EMPRESA: {
        "prefixes": ("empresas__", "geo__"),
        "hint": (
            "O usuário busca empresas por atividade econômica. "
            "Passos obrigatórios: "
            "(1) geo__buscar_municipio para resolver o local; "
            "(2) empresas__buscar_empresas com termo semântico. "
            "Se o usuário pedir 'maiores', 'principais' ou 'maior porte', "
            "passe ordenar_por='capital_social' para ranquear por capital social (proxy de porte). "
            "Senão, use ordenar_por='distancia' (padrão). "
            "Para empresas DENTRO de uma isócrona/polígono específico, use "
            "empresas_por_poligono(geometry=..., termo_busca=...) em vez do raio. "
            "Agrupe resultados por atividade/CNAE no formato de saída."
        ),
    },
    ROUTE_HYBRID: {
        "prefixes": ("jazidas__", "empresas__", "geo__"),
        "hint": (
            "O usuário busca fornecedores que podem ser extratores (ANM) e/ou "
            "empresas comerciais (CNPJ). "
            "CLASSIFICAÇÃO OBRIGATÓRIA POR MATERIAL — antes de chamar qualquer tool, "
            "classifique CADA material/substância individualmente:\n"
            "  • buscar_fornecedores (jazidas ANM): SOMENTE substâncias minerais brutas "
            "extraídas da natureza — areia, brita, cascalho, saibro, argila, calcário, "
            "granito, basalto, pedra.\n"
            "  • buscar_empresas (CNPJ): produtos industrializados/comerciais — cimento, "
            "concreto, ferro/aço, madeira, PVC, tintas, argamassa, telhas, tijolos, "
            "vidros, cal, gesso, pré-moldados, transportadoras.\n"
            "Exemplo: 'areia, brita e cimento' → buscar_fornecedores('areia'), "
            "buscar_fornecedores('brita'), buscar_empresas(termo_busca='cimento').\n"
            "Passos obrigatórios: "
            "(1) geo__buscar_municipio para resolver o local; "
            "(2) para cada material, chamar a tool CORRETA conforme classificação acima. "
            "Se o usuário pedir 'maiores', 'principais' ou 'maior porte', "
            "passe ordenar_por='capital_social' em buscar_empresas. "
            "Para buscas DENTRO de um polígono (isócrona, município, área desenhada), use "
            "as variantes por polígono: jazidas_por_poligono / empresas_por_poligono — "
            "o backend reescreve automaticamente buscas por raio para variante de polígono "
            "se uma isócrona acabou de ser calculada no mesmo turno. "
            "Separe os resultados em seções distintas por origem (jazidas x empresas) "
            "e por substância/material. Cruze por CNPJ para remover duplicatas."
        ),
    },
    ROUTE_GEO: {
        # jazidas__ está incluído para que detalhes_processo possa ser usado
        # quando a query mencionar um número de processo ANM (origem/destino
        # de rota a partir de uma jazida — regra 7.b do SYSTEM_PROMPT).
        "prefixes": ("geo__", "jazidas__"),
        "hint": (
            "O usuário faz uma consulta puramente geográfica (localização, rota, distância). "
            "Tools disponíveis: geo__buscar_municipio, calcular_rota (1 origem → 1 destino), "
            "comparar_rotas (1 origem → N destinos em paralelo, PREFERIR para 2+ destinos), "
            "calcular_isocrona, municipios_em_raio, geocodificar, plotar_endereco, "
            "buscar_ferrovia, ferrovias_proximas (malha ferroviária federal indexada), "
            "obter_geometria_ferrovia (GeoJSON do trecho). "
            "Para imóveis rurais certificados pelo INCRA (SIGEF) no entorno de um ponto "
            "ou jazida, use geo__imoveis_rurais_em_area — NÃO use geo__buscar_dentro_de_isocrona "
            "(essa tool é só para isócrona + jazidas/fornecedores/CNAE). "
            "Para qualquer comparação de rotas (compare/principais/todas/ranking), USE "
            "geo__comparar_rotas em vez de N chamadas a calcular_rota — atomicidade do "
            "backend garante que TODAS as polilinhas vão para o mapa. "
            "Se a pergunta mencionar um NÚMERO DE PROCESSO ANM (formato NNN.NNN/AAAA) "
            "como origem ou destino de rota, chame PRIMEIRO "
            "jazidas__detalhes_processo(ds_processo=...) para obter as coordenadas reais "
            "da jazida em processo.localizacao — NUNCA assumir que a jazida fica nas "
            "coordenadas do pino do projeto ativo. "
            "Se o usuário pedir 'mostrar/plotar/ver no mapa' um endereço ou coordenada, "
            "chame geo__plotar_endereco — o pin aparece automaticamente no mapa MapLibre "
            "da plataforma. NÃO ofereça link do Google Maps para isso (regra 8 do system prompt)."
        ),
    },
    ROUTE_GENERAL: {
        "prefixes": (),
        "hint": (
            "Se a pergunta estiver FORA DO ESCOPO da plataforma (câmbio genérico, "
            "bolsa sem mineral, clima, notícias, entretenimento, política, "
            "tecnologia geral), RECUSE educadamente em 1 frase e ofereça ajuda com "
            "inteligência mineral (jazidas, empresas, geo) ou dados indexados na plataforma. "
            "NÃO responda a pergunta fora do escopo sob nenhuma circunstância. "
            "Se for saudação ou dúvida sobre o MineralRadar, responda normalmente sem tools. "
            "NÃO recuse exportação Comex, NCM/FOB ou preço de metal — esses tópicos "
            "não devem cair nesta rota (erro de classificação)."
        ),
    },
    # ───────────────────────────────────────────────────────────────────
    # Rotas DEDICADAS para "dentro da isócrona" — restringem o conjunto
    # de tools ao MÍNIMO necessário, ancorando tudo na synthetic tool
    # atômica geo__buscar_dentro_de_isocrona. Usar allow-list `tools`
    # (em vez de prefixos) garante que o LLM não veja calcular_isocrona,
    # buscar_empresas, *_por_poligono, etc. — só existe um caminho.
    # ───────────────────────────────────────────────────────────────────
    ROUTE_MINERAL_EM_ISOCRONA: {
        "prefixes": (),
        "tools": (
            "geo__buscar_dentro_de_isocrona",
            "jazidas__detalhes_processo",
            "geo__plotar_endereco",
            "geo__buscar_municipio",
        ),
        "hint": (
            "O usuário pediu jazidas/processos ANM DENTRO de uma isócrona "
            "(área alcançável em X MINUTOS/HORAS de viagem — critério TEMPO). "
            "USE OBRIGATORIAMENTE geo__buscar_dentro_de_isocrona com substancia=… "
            "e criterio='tempo', valor=<minutos> numa única chamada — "
            "ela faz tudo (calcular_isocrona + jazidas_por_poligono em paralelo). "
            "NÃO chame calcular_isocrona separadamente.\n"
            "ORIGEM DA ISÓCRONA — regra de precedência OBRIGATÓRIA:\n"
            "  1. Se o usuário mencionou um NÚMERO DE PROCESSO ANM (ex: '871.269/2024') "
            "como referência/origem: PRIMEIRO chame jazidas__detalhes_processo para "
            "obter processo.localizacao.lat/lon e passe ESSAS coordenadas como "
            "latitude/longitude — NÃO use o pino do projeto como origem.\n"
            "  2. Se o usuário mencionou uma cidade ou endereço específico como origem: "
            "use geo__buscar_municipio para resolver e passe as coordenadas do município.\n"
            "  3. Somente se não houver origem explícita: use as coordenadas do pino do projeto "
            "(vêm do contexto) como latitude/longitude.\n"
            "Para detalhes de processos específicos retornados, use jazidas__detalhes_processo."
        ),
    },
    ROUTE_EMPRESA_EM_ISOCRONA: {
        "prefixes": (),
        "tools": (
            "geo__buscar_dentro_de_isocrona",
            "jazidas__detalhes_processo",
            "empresas__detalhes_empresa",
            "geo__plotar_endereco",
            "geo__buscar_municipio",
        ),
        "hint": (
            "O usuário pediu empresas (CNAE) DENTRO de uma isócrona de TEMPO. "
            "USE OBRIGATORIAMENTE geo__buscar_dentro_de_isocrona com "
            "termo_busca=… (ou codigos_cnae=…), criterio='tempo', valor=<minutos> "
            "numa única chamada — ela faz tudo em paralelo. "
            "NÃO chame calcular_isocrona separadamente.\n"
            "ORIGEM DA ISÓCRONA — regra de precedência OBRIGATÓRIA:\n"
            "  1. Se o usuário mencionou um NÚMERO DE PROCESSO ANM (ex: '871.269/2024') "
            "como referência/origem: PRIMEIRO chame jazidas__detalhes_processo para "
            "obter processo.localizacao.lat/lon e passe ESSAS coordenadas como "
            "latitude/longitude — NÃO use o pino do projeto como origem.\n"
            "  2. Se o usuário mencionou uma cidade ou endereço específico como origem: "
            "use geo__buscar_municipio para resolver e passe as coordenadas do município.\n"
            "  3. Somente se não houver origem explícita: use as coordenadas do pino do projeto "
            "(vêm do contexto) como latitude/longitude.\n"
            "Para ficha completa de uma empresa específica, use empresas__detalhes_empresa."
        ),
    },
    ROUTE_HIBRIDO_EM_ISOCRONA: {
        "prefixes": (),
        "tools": (
            "geo__buscar_dentro_de_isocrona",
            "jazidas__detalhes_processo",
            "empresas__detalhes_empresa",
            "geo__plotar_endereco",
            "geo__buscar_municipio",
        ),
        "hint": (
            "O usuário pediu jazidas E empresas (mineral + comercial) DENTRO "
            "de uma isócrona. USE OBRIGATORIAMENTE geo__buscar_dentro_de_isocrona "
            "passando AMBOS substancia=… e termo_busca=… numa única chamada — a "
            "tool dispara as duas buscas em paralelo dentro do mesmo polígono.\n"
            "ORIGEM DA ISÓCRONA — regra de precedência OBRIGATÓRIA:\n"
            "  1. Se o usuário mencionou um NÚMERO DE PROCESSO ANM como referência: "
            "PRIMEIRO chame jazidas__detalhes_processo para obter as coordenadas reais "
            "e passe-as como latitude/longitude — NÃO use o pino do projeto como origem.\n"
            "  2. Se o usuário mencionou cidade/endereço como origem: resolva com "
            "geo__buscar_municipio primeiro.\n"
            "  3. Sem origem explícita: use as coordenadas do pino do projeto do contexto.\n"
            "Para detalhes individuais use jazidas__detalhes_processo / "
            "empresas__detalhes_empresa."
        ),
    },
}


# ── Classification prompt ──────────────────────────────────────────────

CLASSIFICATION_PROMPT = """\
Você classifica intenções para o MineralRadar — plataforma de inteligência mineral.

ESCOPO DA PLATAFORMA: jazidas minerais, fornecedores de agregados e insumos da cadeia mineral, \
empresas por CNAE ligadas a logística e materiais, rotas/distâncias geográficas, \
**comércio exterior de minerais do Brasil** (ComexStat/MDIC: volume, valor FOB/USD, \
NCM, evolução por ano, principais países destino ou origem), e **preço de referência** \
de metais/minerais estratégicos servidos pela base (ex.: ouro, lítio, nióbio, cobre).

FORA DO ESCOPO (rota **general**): câmbio turístico ou paridade EUR/USD isolada, \
índices de bolsa genéricos (IBOVESPA), ações/cotas de empresas, criptomoedas, \
clima, notícias, saúde, entretenimento, política, tecnologia geral — sem ligação a \
mineração ou cadeia mineral sem dados correspondentes nas tools do MineralRadar.

IMPORTANTE — NÃO use **general** para:
- Exportação/importação de **minerais ou NCM** ("volume exportado", "valor FOB", \
  "para onde o Brasil exporta X", "destinos da exportação", "Comex", "MDIC").
  → Classifique como **mineral** (ou **hybrid** se misturar com fornecedores CNPJ).
- "Preço atual" / "cotação" de **metal ou mineral** da cadeia (lítio, nióbio, ouro, \
  cobre, ferro, etc.) para contexto mineral.
  → **mineral** (a plataforma tem tool de preço de referência de minerais).

Classifique em UMA das rotas abaixo:

- **mineral**: Jazidas, processos ANM, substâncias minerais, vigência de títulos, \
pedreiras (foco em extração ou dados regulatórios ANM). \
Áreas licenciadas, concessões, fases de processo. \
Inclui contexto geológico CPRM (ocorrências minerais, **afloramentos** de campo) \
quando o usuário pedir mapa/rochas/afloramentos ligados a mineração. \
Inclui **estatísticas de comércio exterior** (export/import por NCM ou nome de \
mineral) e **preço de referência** de metais estratégicos quando a pergunta for \
sobre commodities minerais — use tools ``jazidas__consultar_mercado_mineral``, \
``jazidas__principais_destinos_mineral``, ``jazidas__consultar_preco_mineral``.

- **empresa**: Empresas por atividade econômica (CNAE) fornecedoras civis ou logísticas \
sem foco em processo ANM ou substância mineral. Ex: madeireiras, cimenteiras, transportadoras, \
pré-moldados, pavimentação, ferragens.

- **hybrid**: Envolve TANTO mineração QUANTO empresas comerciais. \
Ex: "fornecedores de brita" (quem extrai + quem vende), "empresas de areia".

- **geo**: Consultas puramente geográficas — localização, rotas, distâncias. \
Sem busca de jazidas ou empresas.

- **general**: Saudações, perguntas sobre o MineralRadar, tópicos FORA DO ESCOPO \
(ver lista acima) — o agente vai recusar educadamente na rota general. \
Nunca classifique como general perguntas de exportação/importação mineral, NCM, \
FOB ou preço de lítio/nióbio/ouro/etc. que as tools de mercado cobrem.

ROTAS DEDICADAS A ISÓCRONA — APENAS quando a pergunta menciona \
explicitamente tempo de viagem: "isócrona", "em X minutos", "em X horas", \
"alcance de Y min", "tempo de caminhão", "tempo de viagem", "no polígono violeta", \
"região alcançável em X min". \
ATENÇÃO CRÍTICA: pedidos com raio em KM ("num raio de X km", "em até X km", \
"X km de distância", "dentro de X km") NÃO são isócrona — são buscas normais \
com filtro geográfico (rotas mineral/empresa/hybrid). \
Só use *_em_isocrona quando o critério for TEMPO, não distância em km:

- **mineral_em_isocrona**: jazidas/processos minerais brutos dentro de \
uma isócrona DE TEMPO. Ex: "jazidas de areia em até 60 min de caminhão", \
"fornecedores de brita na isócrona de 1h".

- **empresa_em_isocrona**: empresas comerciais (CNAE) dentro de uma \
isócrona DE TEMPO. Ex: "pré-moldados em 60 min", "transportadoras em 90 minutos".

- **hibrido_em_isocrona**: jazidas E empresas juntas, dentro de isócrona \
DE TEMPO. Ex: "areia e cimento em até 1h do pino do projeto".

Regras de desempate:
1. Pergunta inclui "isócrona" ou "em X minutos/horas" de viagem \
   → rota *_em_isocrona conforme o tipo de entidade.
1b. Pergunta inclui APENAS "X km" / "raio de X km" / "num raio" SEM mencionar \
   tempo → NÃO é isócrona → use rota base (mineral, empresa ou hybrid).
2. Substância mineral + "fornecedor/quem vende" SEM isócrona → hybrid.
3. Substância mineral + ANM/licença/vigência SEM isócrona → mineral.
4. Fornecedor comercial sem mineração ANM SEM isócrona → empresa.
5. Localização/rota/distância apenas → geo.
6. Fora do escopo da plataforma (lista FORA DO ESCOPO acima) → general.
6b. Exportação/importação mineral, NCM, FOB, volume/valor Comex, "para onde exporta" \
→ **mineral** (não general).
6c. Preço/cotação de metal ou mineral (ex.: carbonato de lítio enquanto commodity \
de lítio, nióbio, ouro) → **mineral** — a menos que seja claramente bolsa/câmbio \
genérico sem mineral.
7. Na dúvida entre mineral e hybrid → hybrid.
8. Na dúvida entre *_em_isocrona e rota base: PREFIRA *_em_isocrona SOMENTE \
   se houver menção explícita a TEMPO de viagem/alcance.

REGRA ESPECIAL — ROTAS/GEO têm PRIORIDADE ABSOLUTA sobre herança de contexto:
Se a mensagem contiver qualquer uma destas palavras, classifique SEMPRE como **geo**,
independentemente do contexto anterior (mesmo que a conversa anterior tenha sido sobre
empresas ou jazidas):
"rota", "rotas", "trajeto", "distância", "calcular rota", "rota para", "mais próximo",
"porto mais próximo", "rota até", "me traz a rota", "mostrar rota", "ver rota",
"traçar rota", "ir até", "chegar até", "quanto tempo", "km até".

REGRA ESPECIAL — mensagens de acompanhamento (follow-up):
Se a mensagem atual for curta ou ambígua (ex: "sim", "pode", "tente", "ok",
"amplie o raio", "tente com 200km", "busque mais longe", "não encontrou nada?"),
analise o CONTEXTO DA CONVERSA fornecido abaixo e herde a rota da pergunta anterior.
NÃO classifique como "general" se o contexto deixar claro que é continuação de uma
busca de jazidas, empresas, fornecedores, **mercado Comex/exportação** ou **preço de mineral**.
pergunta anterior mencionou tempo de viagem (não apenas km).
ATENÇÃO: a herança de contexto NUNCA se aplica a mensagens que mencionam rotas/trajetos
(ver regra anterior — essas vão para geo).\
"""


# ── Public API ─────────────────────────────────────────────────────────


async def classify_route(
    user_message: str,
    llm: AzureChatOpenAI,
    recent_messages: list[BaseMessage] | None = None,
) -> RouteClassification:
    """
    Classify the user's intent using structured output.

    Args:
        user_message: The latest human message to classify.
        llm: Azure OpenAI LLM instance.
        recent_messages: Optional recent conversation history (up to last 6
            messages) so the router can handle follow-up / confirmation messages
            correctly (e.g. "sim, pode ampliar" after a failed search).

    Falls back to DEFAULT_ROUTE (hybrid) if classification fails.
    """
    # Build system prompt — append conversation context when available so the
    # router can correctly inherit the route for follow-up messages.
    system_content = CLASSIFICATION_PROMPT
    if recent_messages:
        context_lines: list[str] = []
        for m in recent_messages[-6:]:
            if isinstance(m, HumanMessage):
                context_lines.append(f"Usuário: {str(m.content)[:300]}")
            elif isinstance(m, AIMessage):
                context_lines.append(f"Assistente: {str(m.content)[:300]}")
        if context_lines:
            system_content += (
                "\n\nCONTEXTO RECENTE DA CONVERSA (use para classificar "
                "mensagens de acompanhamento):\n"
                + "\n".join(context_lines)
            )

    try:
        router_llm = llm.with_structured_output(RouteClassification)
        # Suppress Pydantic v2 serialization warning from LangChain's structured output
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Pydantic serializer warnings",
                category=UserWarning,
            )
            result = await router_llm.ainvoke([
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_message},
            ])

        logger.info(
            f"Router: '{user_message[:80]}' → {result.route} "
            f"({result.reasoning})"
        )
        return result

    except Exception as e:
        logger.warning(
            f"Router classification failed: {e}. "
            f"Falling back to '{DEFAULT_ROUTE}'"
        )
        return RouteClassification(
            route=DEFAULT_ROUTE,
            reasoning=f"Fallback — classification error: {str(e)[:100]}",
        )


def filter_tools_by_route(
    all_tools: list[StructuredTool],
    route: Route,
) -> list[StructuredTool]:
    """
    Return the subset of tools relevant to the classified route.

    Estratégia:
      1. Se a config da rota tem ``tools`` (tupla de nomes exatos), usa
         allow-list rigorosa — só essas tools são expostas ao LLM. Esta é
         a forma mais determinística e foi adotada para as rotas
         ``*_em_isocrona`` que ancoram a operação na synthetic tool
         atômica ``geo__buscar_dentro_de_isocrona``.
      2. Caso contrário, faz match por prefixo via ``prefixes`` (forma
         tradicional, usada nas rotas mineral/empresa/hybrid/geo).
      3. ``general``: sem prefixos nem tools → retorna lista vazia.
    """
    config = ROUTE_CONFIGS.get(route, ROUTE_CONFIGS[DEFAULT_ROUTE])

    explicit_tools = config.get("tools")
    if explicit_tools:
        allowed = set(explicit_tools)
        filtered = [t for t in all_tools if t.name in allowed]
        # Log para auditoria — fica claro nos logs quais tools cada rota expõe.
        logger.info(
            "Route %s → allow-list (%d/%d tools): %s",
            route, len(filtered), len(allowed),
            [t.name for t in filtered],
        )
        return filtered

    prefixes = config.get("prefixes") or ()
    if not prefixes:
        return []

    return [t for t in all_tools if t.name.startswith(prefixes)]


def get_route_hint(route: Route) -> str:
    """Return the system prompt hint for a classified route."""
    config = ROUTE_CONFIGS.get(route, ROUTE_CONFIGS[DEFAULT_ROUTE])
    return config["hint"]
