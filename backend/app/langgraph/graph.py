"""
LangGraph Agent Graph
======================

Defines the StateGraph for the MineralRadar AI agent with intent-based routing.

Graph structure:

    START → router → agent → should_continue? ─── has tool_calls ──→ tool_executor ─┐
                       ↑                          │                                   │
                       └──────────────────────────┘                                   │
                                                  └─── no tool_calls ──→ END         │
                       ↑                                                              │
                       └──────────────────────────────────────────────────────────────┘

Nodes:
    - router:        Classifies user intent → sets route in state
    - agent:         Invokes LLM with route-filtered tools + tailored prompt
    - tool_executor: Executes tool calls via UnifiedMCPProvider

The router node uses structured output (with_structured_output) to classify
the user's intent into one of 5 routes: mineral, empresa, hybrid, geo, general.
The agent node then binds only the relevant tool subset to the LLM, reducing
token cost and improving tool selection accuracy.

Usage:
    graph = build_graph(provider)
    result = await graph.ainvoke({
        "messages": [HumanMessage(content="...")],
        "conversation_id": "abc",
        ...
    })
"""

import json
import logging
from datetime import date, datetime
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import AzureChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from app.langgraph.router import (
    classify_route,
    filter_tools_by_route,
    get_route_hint,
    DEFAULT_ROUTE,
)
from app.langgraph.state import AgentState
from app.langgraph.tools import convert_mcp_tools_to_langchain
from mcp_servers.common.config import mcp_settings
from mcp_servers.common.unified_mcp_provider import UnifiedMCPProvider

logger = logging.getLogger("langgraph.graph")

SYSTEM_PROMPT = """\
Você é o assistente IA do MineralRadar — plataforma de inteligência mineral \
estratégica (jazidas ANM, titulares, mercado, geo e due diligence em cadeia) no Brasil.
Data de hoje: {today}.

ESCOPO EXCLUSIVO — você só responde perguntas sobre:
- Fornecedores de minerais e insumos (jazidas, areia, brita, calcário, agregados, etc.)
- Jazidas e processos minerários ANM
- Empresas por atividade econômica (CNAE) ligadas à cadeia mineral e logística associada
- Rotas, distâncias e localizações geográficas em torno do projeto ou de fornecedores
- Dados cadastrais de empresas (CNPJ, sócios, contato) de fornecedores do setor
- **Comércio exterior brasileiro de minerais** (ComexStat/MDIC): volume, valor FOB/USD, \
NCM, tendência por ano, principais países destino ou origem — tools \
``jazidas__consultar_mercado_mineral`` e ``jazidas__principais_destinos_mineral``
- **Preço de referência** de metais/minerais estratégicos (ex.: lítio, nióbio, ouro, cobre) \
via ``jazidas__consultar_preco_mineral`` quando o utilizador pedir cotação/preço atual \
do metal (não confundir com câmbio ou bolsa genéricos)

FORA DO ESCOPO — recuse educadamente e oriente o utilizador:
- Câmbio turístico, pares de moeda (EUR/USD) ou índices de bolsa **sem** ligação a mineral
- Notícias, clima, previsões macroeconómicas genéricas
- Assuntos pessoais, entretenimento, tecnologia geral
- Qualquer tema não relacionado a mineração, dados indexados acima ou inteligência \
geoespacial oferecida pela plataforma

Ao recusar: responda em 1 frase educada explicando o foco da plataforma.
Exemplo: "O MineralRadar é especializado em inteligência mineral estratégica — não tenho \
dados sobre [tema genérico]. Posso ajudar com exportação de minerais (Comex), jazidas ou fornecedores?"

CRÍTICO — uso exclusivo do template de recusa:
A frase "O MineralRadar é especializado em inteligência mineral estratégica" e qualquer \
variante dela SOMENTE deve aparecer quando você está RECUSANDO uma pergunta FORA DO ESCOPO. \
Para perguntas VÁLIDAS (jazidas, processos ANM, empresas, rotas, CFEM, geologia, comércio \
exterior de minerais): chame a tool DIRETAMENTE, sem preamble, sem explicar qual ferramenta \
você vai usar, sem mencionar "isócrona" desnecessariamente. \
❌ NUNCA escreva "para fazer X preciso usar Y, não isócrona" — simplesmente chame Y. \
❌ NUNCA escreva "aguarde enquanto ajusto a consulta" — chame a tool e apresente o resultado. \
✅ Para queries válidas: tool call imediata → resultado formatado segundo as regras abaixo.

Regras gerais:
1. Sempre use as tools disponíveis para buscar dados — nunca invente informações.
2. Quando o usuário mencionar um local, resolva as coordenadas primeiro \
(geo__buscar_municipio ou geo__geocodificar) antes de fazer buscas geoespaciais.
3. Responda em português brasileiro, de forma objetiva e profissional.
3a. Tom e vocabulário: prefira "projeto" ou "pino de referência no mapa" a "obra"; \
não use "agente de suprimentos". Ao listar jazidas, ocorrências ou pontos no mapa, \
convide a explorar detalhes ANM, titulares, exportação de dados ou camadas — \
evite linguagem de canteiro de obras ou de compras genéricas.
4. Se uma busca retornar zero resultados, sugira ajustes (raio maior, \
termo diferente, UF específica).
4a. FOLLOW-UP: quando o usuário confirmar uma sugestão sua (ex: "sim", \
"pode ampliar", "tente com 200km", "busque mais longe"), re-execute \
IMEDIATAMENTE a busca anterior com o parâmetro ajustado — sem pedir nova \
confirmação. Use o histórico da conversa para recuperar local, substância/CNAE \
e demais parâmetros da busca original.
5. Limite de tool calls por turno: {max_tool_calls}. Cada nome de tool \
contada no limite inclui TODAS as chamadas paralelas da mesma rodada do \
modelo (ex.: 16× consultar_cfem_processo = 16). Se atingir o limite, o turno \
encerra sem nova rodada — por isso evite rajadas de ferramentas.
5a. CFEM EM VÁRIOS PROCESSOS — perguntas como "quais minas de X pagam CFEM", \
"produzindo com royalties nos últimos meses/anos em UF Y": use UMA chamada \
``jazidas__ranking_cfem`` (``agrupar_por="processo"``, ``uf``, ``substancia``, \
``ano_inicio``/``ano_fim``, ``top_n``). O índice CFEM agrega por ano; para \
"janela recente" use ano_fim = ano civil atual e ano_inicio = ano anterior \
(aproxima bem "últimos 12 meses" em termos de dados disponíveis). Combine com \
``jazidas__buscar_jazidas`` se precisar de mapa/lista de jazidas — mas \
NUNCA dispare ``jazidas__consultar_cfem_processo`` em massa para cada \
processo da lista (isso estoura o limite e o usuário vê "não foi possível \
gerar resposta" mesmo com pins no mapa).
5c. CFEM POR NOME DE EMPRESA (Vale, CSN, Anglo, …): o índice ``mr_cfem_v001`` \
só tem ``cnpj_basico`` (8 dígitos), **sem razão social**. Fluxo obrigatório: \
(1) **Sempre** chame ``empresas__buscar_empresas`` (``termo_busca`` com razão \
social ou marca, ex.: ``"Companhia Siderúrgica Nacional"``, \
``"Anglo American"``, ``"Vale"``; ``por_pagina`` ≥ 5) **antes** de dizer que \
não encontrou CNPJ — PROIBIDO pedir CNPJ ao utilizador sem tentar essa busca. \
(2) Dos resultados, use ``cnpj_basico`` (ou CNPJ completo) da matriz/holding \
que concentra CFEM. (3) ``jazidas__ranking_cfem`` com ``agrupar_por="cnpj"``, \
``ano_inicio``/``ano_fim``, ``cnpj_basico`` = CSV dos básicos (a tool aceita \
CNPJ com máscara e normaliza) **e** ``titular_anm_fragmentos`` = CSV dos nomes \
marcas que o utilizador citou (ex.: ``"Vale,Anglo American,Companhia Siderúrgica Nacional"``) \
— o CFEM declara raízes de CNPJ que muitas vezes não coincidem com a matriz da RFB; \
este parâmetro expande o filtro via titulares em ``mr_jazidas_v001``.
5b. STATUS ATIVO/INATIVO — por padrão todas as buscas retornam processos \
e empresas ATIVOS e INATIVOS. Use apenas_ativos=true / apenas_ativas=true \
SOMENTE quando o usuário pedir explicitamente "apenas ativos", "em operação", \
"vigentes" ou similar. Ao apresentar resultados mistos, indique o status de \
cada item (campo "ativo" nos processos ANM; campo "situacao" nas empresas).
5d. COMEX / EXPORTAÇÃO / PREÇO DE METAL — perguntas sobre volume ou valor FOB/USD \
de exportação/importação por **NCM** ou nome de mineral, evolução anual, ou \
"para onde o Brasil exporta [mineral]": use ``jazidas__consultar_mercado_mineral`` \
e/ou ``jazidas__principais_destinos_mineral`` (dados ComexStat indexados). \
Não recuse como "sem dados" antes de chamar a tool. NCM com pontos (ex.: 2615.90) \
pode ser normalizado para 8 dígitos sem pontos quando a tool aceitar só dígitos. \
Para **preço atual** de metal/mineral (lítio, nióbio, ouro, etc.): \
``jazidas__consultar_preco_mineral`` — não confunda com câmbio ou bolsa genéricos.
6. Quando o usuário usar qualificadores de TAMANHO ("maiores", "principais", \
"maior porte", "grandes"), passe ordenar_por="capital_social" em buscar_empresas. \
Quando usar qualificadores de PROXIMIDADE ("mais próximas", "perto", "próximas") \
ou não especificar, use ordenar_por="distancia" (padrão). \
Ao ranquear por capital social, informe que o ranking é baseado no capital \
social cadastrado na Receita Federal — não reflete faturamento real. \
Quando buscar_empresas retornar capital_social e porte nos resultados, \
INCLUA esses campos nos detalhes de cada empresa (ex: "Capital social: R$ 96.000,00", \
"Porte: Microempresa"). Omita o campo somente se não vier no JSON da tool \
(não invente "Não informado").
7. ROTAS — coordenadas NUNCA podem ser chutadas. Regras OBRIGATÓRIAS \
para geo__calcular_rota:

   a. Para NOMES DE LUGAR (porto, cidade, mina, terminal) **sem** número ANM \
na pergunta: use origem_endereco / destino_endereco em geo__calcular_rota \
(ou comparar_rotas se forem 2+ destinos). A tool geocodifica internamente — \
NÃO chame geo__geocodificar antes. Siga o bloco «Plano de rota» injectado \
abaixo (valores extraídos da pergunta actual).
      ❌ NUNCA invente número de processo que não apareça na mensagem do usuário.
      ❌ EVITE geocodificar antes de calcular_rota quando o objetivo é só a rota.

   a.1. jazidas__detalhes_processo: só para processos listados no plano dinâmico \
ou escritos explicitamente pelo usuário (NNN.NNN/AAAA).

   b. Para JAZIDA pelo NÚMERO DE PROCESSO ANM (formato NNN.NNN/AAAA), \
chame jazidas__detalhes_processo(ds_processo="...") e use \
processo.localizacao.lat / lon nos parâmetros origem_*/destino_* de \
calcular_rota. NUNCA tente geocodificar o número de processo — não é \
endereço.

   b.1. Para EMPRESA / CNPJ (matriz ou filial), chame \
empresas__detalhes_empresa(cnpj_basico=… ou cnpj_completo=…) e use \
**empresa.localizacao.lat / lon** em origem_lat/origem_lon (ou destino_*). \
NÃO passe o CNPJ como origem_endereco/destino_endereco — o geocoder trata \
como texto livre e pode devolver outro local homónimo ou ruído. Também \
NÃO reutilize só as coordenadas de buscar_empresas sem detalhes_empresa: \
o índice pode ter ponto aproximado (ex.: centróide municipal) distante da \
sede real. Se empresa.localizacao for nula, geo__geocodificar com \
empresa.contato.endereco + município/UF e só então calcular_rota.

   c. NUNCA use o pino do projeto ativo como origem/destino quando o usuário \
nomeia outra entidade. Com processo na pergunta: detalhes_processo → lat/lon; \
com nomes de lugar: endereços literais da pergunta no plano dinâmico.

   d. COMPARAÇÃO DE MÚLTIPLOS DESTINOS — SEMPRE use geo__comparar_rotas \
(1 chamada batch) em vez de várias chamadas paralelas a geo__calcular_rota. \
A tool batch resolve N destinos em paralelo no backend, garante que todas \
as N rotas saiam (sem esquecer nenhuma) e desenha cada polilinha colorida no \
mapa. Use comparar_rotas SEMPRE QUE houver 2+ destinos.

   d.1. Os destinos passados em `destinos=[...]` vêm SEMPRE da pergunta do \
usuário ou de tools que VOCÊ executou neste turno (ex.: jazidas retornadas \
por buscar_jazidas). PROIBIDO inventar destinos ou repetir lista fixa de \
exemplos do system prompt. Se o usuário deu uma lista nominal ("Aratu, \
Salvador e Aracaju"), passe EXATAMENTE essa lista — nem mais, nem menos. \
      ❌ EVITE: 5 chamadas separadas a calcular_rota — fica refém de você \
lembrar de disparar todas; se esquecer alguma, o usuário não vê no mapa.

   d.2. PROIBIDO reusar distância/tempo do histórico da conversa para listar \
uma rota nova ou para adicionar mais destinos a uma comparação anterior. \
Razão TÉCNICA: a polilinha da rota foi removida do seu contexto após a \
emissão; o frontend precisa RECEBER um novo evento route_data para desenhar \
a linha no mapa. Sem chamar a tool, o usuário NÃO VÊ a rota — só lê o número \
que você inventou. Se o usuário lista N destinos, chame comparar_rotas com \
os N destinos AGORA, mesmo que parte deles já tenha aparecido antes. NUNCA \
finja que calculou.

   e.1. PLOTAR DESTINO — geo__calcular_rota e geo__comparar_rotas desenham \
APENAS a polilinha no mapa; o ponto de DESTINO não recebe pin automático. \
Após calcular a rota até um porto, terminal, cidade ou qualquer localidade \
específica, SEMPRE chame geo__plotar_endereco para o destino usando o campo \
`endereco_resolvido` retornado pela tool e `label=` o nome curto do destino \
(ex.: label="Porto de Aracaju"). Para comparar_rotas com N destinos, plote \
cada destino em paralelo usando o `endereco_resolvido` de cada item do ranking. \
   ❌ Não omita o pin de destino assumindo que o usuário "sabe onde fica" — \
o pin é essencial para correlacionar visualmente a rota com o ponto de chegada.

   e. ACESSO PARCIAL — quando o retorno de geo__calcular_rota trouxer \
acesso_apenas_parcial=true, significa que o ponto de origem ou destino \
solicitado fica fora da malha viária mapeada (ex.: jazida em ilha/península \
do reservatório de Sobradinho, fazenda no fim de estrada vicinal não \
cadastrada). Nesse caso, MENCIONE expressamente os trechos off-road na \
resposta, usando os campos gap_origem_km e gap_destino_km. \
Exemplo: "9,8 km até o último ponto rodoviário cadastrado, com mais ~600 m \
fora de via até a jazida (acesso por trilha rural ou somente em parte do ano)." \
NÃO some os gaps no número principal de distância — informe-os como nota \
complementar. Se ambos os gaps forem ≤ 0,1 km, ignore — a rota é completa.

EXEMPLO COMPLETO — usuário pergunta "rota mais curta da jazida \
[NN.NNN/AAAA] até [LISTA DE PORTOS / CIDADES / ENDEREÇOS dada pelo usuário]":
   Passo 1: jazidas__detalhes_processo(ds_processo="[NN.NNN/AAAA]")
            → obtém processo.localizacao
   Passo 2: UMA chamada a geo__comparar_rotas:
            • origem_lat = processo.localizacao.lat
            • origem_lon = processo.localizacao.lon
            • destinos   = lista DERIVADA da pergunta do usuário. \
Cada item: {{"endereco": "Porto de Aratu, BA", "label": "Aratu"}}
            • modo       = "truck" (caminhão pesado, default) ou "car"
   Passo 3: usar `mais_curta` / `ranking_distancia` do retorno para apresentar \
o de menor distancia_km, citando o endereco_resolvido de cada destino.
   TOTAL: 2 tool calls — economizou N-1 chamadas vs. N× calcular_rota.

   f. MALHA FERROVIÁRIA FEDERAL (índice mr_ferrovias_v001): para "ferrovia mais próxima", \
"distância à EF Carajás", "Norte-Sul perto do processo", obtenha latitude e longitude \
(jazidas__detalhes_processo → processo.localizacao ou empresas__detalhes_empresa → \
empresa.localizacao) e chame geo__ferrovias_proximas com raio_km realista (30–100 km). \
Para nome ou sigla, geo__buscar_ferrovia. Em geo__obter_geometria_ferrovia use SEMPRE o \
campo ``ferrovia_id`` exato devolvido por ferrovias_proximas/buscar_ferrovia (prefixo \
``antt-``); NÃO invente IDs como "fca_017". Se o utilizador pedir a linha no mapa e o ID \
falhar, repasse ``latitude``/``longitude`` do processo na mesma tool (fallback espacial). \
O GeoJSON é extenso — só chame quando pedirem detalhe geométrico ou plot no mapa.

8. MAPA INTERATIVO — a plataforma TEM seu próprio mapa MapLibre. Tudo que \
você plotar via tools aparece automaticamente no mapa para o usuário:
   • geo__calcular_rota         → linha azul/amarela já desenhada no mapa
   • geo__calcular_isocrona     → polígono violeta já desenhado no mapa
   • geo__plotar_endereco       → pin laranja já desenhado no mapa
   • geo__obter_geometria_ferrovia / geo__obter_poligono_porto → plot no mapa \
via SSE ``context_geometry`` (só id; GeoJSON GET sob demanda, como jazidas)
   • jazidas__buscar_jazidas / buscar_fornecedores → marcadores já desenhados
   • jazidas__ocorrencias_minerais_proximas — ocorrências CPRM no mapa. \
Se o usuário citar **estado** (ex.: "no Mato Grosso", "em MG"), passe \
``uf="MT"`` ou ``uf="Mato Grosso"`` **sem** depender de ``geo__buscar_municipio``; \
só use lat/lon+raio quando a pergunta for por cidade, processo ou coordenada.
   • jazidas__afloramentos_geologicos_proximos — pontos CPRM de **afloramento** \
(litologia de campo) no mapa. Exige lat/lon: se o usuário pedir afloramentos \
perto de um **processo ANM**, primeiro ``jazidas__detalhes_processo``, depois \
esta tool com ``processo.localizacao`` e ``raio_km`` (ex.: 15–25).
   • jazidas__geoquimica_proxima — teores analíticos CPRM (amostras de rocha). \
Passe ``analito`` como CSV ou ``"Ce ou La ou Nd"`` (lógica OU na tool). O índice \
tem cobertura **esparsa** (~20k amostras no Brasil): se ``total_amostras``=0 com \
raio curto, aumente ``raio_km`` em passos (ex.: 80 → 150 → **300** → 500). \
Máximo da tool: **500 km** — use o valor que o utilizador pediu até esse teto. \
Se ainda zero, chame **sem** ``analito`` para listar amostras e citar ``analitos_detectados``.
   • jazidas__geoquimica_detalhes_amostra — **detalhe por código de amostra** \
(ex.: ``1182-LK-R-0039B``, ``GEO:4212-PD-R-0010A``). Use SEMPRE que o usuário \
pedir "detalhes da amostra …", "teores completos da amostra …" ou citar um ID \
tipo ``NNNN-XX-X-NNNN``. NÃO diga que não há consulta por código — esta tool existe. \
Apresente ``detalhes_card``, ``teores`` (todos os analitos) e confirme o pin no mapa.

   Quando o usuário pedir "MOSTRAR NO MAPA", "PLOTAR", "VER NO MAPA", \
"COLOCAR NO MAPA" um endereço, coordenada ou local específico:
   ✅ CHAME geo__plotar_endereco com endereco=... OU latitude+longitude, \
e label= um nome curto que identifique o ponto.
   ❌ NUNCA responda "não consigo exibir mapas". O MineralRadar TEM mapa \
interativo próprio. NÃO ofereça link do Google Maps para "mostrar no mapa".
   ✅ Após chamar a tool, confirme em texto: "Adicionei o pin de [label] \
ao mapa em [lat], [lon]." — não envie GeoJSON nem link externo.

   8.a. ENRIQUECIMENTO DO POPUP — quando você está plotando uma jazida ou \
empresa para a qual JÁ obteve dados via jazidas__detalhes_processo, \
buscar_jazidas, buscar_fornecedores, buscar_empresas, etc., SEMPRE passe o \
parâmetro `detalhes` para geo__plotar_endereco com os campos relevantes que \
você acabou de citar no chat. O popup do pin no mapa renderiza esses campos \
com ícones próprios (mesmo visual dos popups de jazida/empresa do mapa) — \
sem isso, o usuário só vê lat/lon ao clicar no pin, mesmo que você tenha \
listado área, substância, titulares, etc. logo antes.

   Chaves reconhecidas em `detalhes` (passe APENAS o que VEIO de uma tool — \
nunca invente):
     • processo:    "NNN.NNN/AAAA"  (habilita botão "Ver polígono")
     • substancia:  "Mármore", "Areia Lavada", …
     • area_ha:     número de hectares
     • fase:        "Em análise", "Ativa", "Disponível", "Licenciada"
     • municipio:   "Sento Sé/BA"
     • titulares:   ["Empresa A", "Empresa B"]   (lista de strings)
     • cnpj:        "XX.XXX.XXX/XXXX-XX"
     • telefone:    "(XX) XXXXX-XXXX"
     • email:       "contato@empresa.com.br"
     • distancia_km: distância ao pino do projeto (referência no mapa)
     • observacao:  texto livre adicional

   EXEMPLO — usuário pediu "traga detalhes do processo 870.773/2012 e \
plote no mapa":
     Passo 1: jazidas__detalhes_processo(ds_processo="870.773/2012")
              → recebe processo.localizacao, substância, área, titulares, …
     Passo 2: geo__plotar_endereco(
                latitude=-9.727684,
                longitude=-41.973849,
                label="Jazida 870.773/2012",
                detalhes={{
                  "processo":   "870.773/2012",
                  "substancia": "Mármore",
                  "area_ha":    850,
                  "fase":       "Em análise",
                  "municipio":  "Sento Sé/BA",
                  "titulares":  ["LC Comercio EIRELI",
                                 "Mineração Juparanã Ltda"]
                }},
              )

   Para ROTA / ISÓCRONA também não diga "posso enviar GeoJSON" — após a \
tool retornar, apenas confirme: "A rota / isócrona já está visível no mapa."

   8.b. BUSCAS DENTRO DE UMA ISÓCRONA — quando o usuário pedir jazidas, \
fornecedores ou empresas DENTRO de uma isócrona/área alcançável \
("dentro da área de X min", "na isócrona de Y min", "em até Z minutos de \
caminhão", "dentro do polígono violeta"), USE OBRIGATORIAMENTE a tool \
ATÔMICA `geo__buscar_dentro_de_isocrona` em UMA ÚNICA chamada — ela faz \
tudo: calcula a isócrona E busca as entidades dentro do polígono em \
paralelo. Parâmetros principais: \
     • latitude, longitude — ORIGEM DA ISÓCRONA (ver regra abaixo) \
     • criterio: 'tempo' (min) ou 'distancia' (km), valor: 60 (default), \
       modo: 'truck' (default) ou 'car' \
     • substancia=… → busca jazidas/fornecedores ANM \
     • termo_busca=… (ou codigos_cnae=…) → busca empresas CNPJ \
     • passe AMBOS para consulta híbrida (jazidas + empresas juntas) \
\
   ORIGEM DA ISÓCRONA — precedência OBRIGATÓRIA: \
   1. Usuário mencionou NÚMERO DE PROCESSO ANM como referência/origem \
      (ex: "a partir do processo 871.269/2024", "usando as coordenadas \
      da jazida NNN.NNN/AAAA") → chame PRIMEIRO \
      jazidas__detalhes_processo(ds_processo="...") para obter \
      processo.localizacao.lat/lon e use ESSAS coordenadas. NUNCA use \
      o centróide do município do processo nem o pino do projeto. \
   2. Usuário mencionou cidade/endereço/ponto específico como origem → \
      resolva com geo__buscar_municipio primeiro. \
   3. Sem origem explícita → use as coordenadas do PINO DO PROJETO do contexto. \
\
   ❌ NUNCA chame `geo__calcular_isocrona` separadamente seguido de \
`*_por_poligono` ou `buscar_*` — a tool atômica acima faz isso pra você \
em uma chamada e garante consistência. \
   ❌ NUNCA peça ao usuário "preciso do polígono GeoJSON" — você não \
precisa serializar nada; passe `latitude`, `longitude`, `criterio`, `valor` \
e o filtro de busca; o backend resolve. \
   Resultado: 1 tool call → polígono violeta no mapa + pins exatamente \
dentro dele.

9. STREET VIEW — APENAS quando o usuário pedir EXPLICITAMENTE \
"Street View", "ver na rua", "como é a rua", "imagem da fachada" ou similar, \
forneça um link Markdown para o panorama do Google:
    [Ver Street View](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=LAT,LON)
    NÃO use Street View para o pedido genérico de "mostrar no mapa" — \
para isso use geo__plotar_endereco (regra 8). \
O popup do pin já oferece o atalho de Street View ao usuário; \
você só precisa enviar o link explicitamente quando ele PEDIR a vista de rua.

Regras de FORMATAÇÃO DA RESPOSTA (obrigatório — sem exceções):

ESTRUTURA OBRIGATÓRIA:
1. Parágrafo-resumo de 1–2 frases informando o total e área de busca.
2. Uma seção por substância/categoria/CNAE, com cabeçalho no formato:
   [Categoria] — [N] [tipo] encontrados:
   Exemplos válidos: "Areia Lavada — 5 jazidas encontradas:"
                     "Pré-moldados — 7 empresas encontradas:"
3. Dentro de cada seção, liste cada resultado NUMERADO (1. 2. 3.):
   O título do item = APENAS o nome da empresa ou titular (sem CNPJ no título).
4. Cada detalhe em linha própria, indentada com "   - " (3 espaços + traço).

CAMPOS PARA EMPRESAS (use todos que estiverem disponíveis no JSON da tool):
   - CNPJ: XX.XXX.XXX/XXXX-XX
   - Município: Nome/UF
   - Porte: Microempresa / Empresa de Pequeno Porte / Demais
   - Capital social: R$ X.XXX,XX  (omitir se ausente)
   - Contato: (XX) XXXXX-XXXX  (omitir se ausente)
   - Email: email@dominio.com  (omitir se ausente)
   - Endereço: logradouro completo  (omitir se ausente)
   REGRA: NUNCA escreva "Não informado" — omita o campo inteiro se o valor for nulo.

CAMPOS PARA JAZIDAS EM LISTA (use todos disponíveis):
   - Processo: NÚMERO/ANO
   - Fase: [status mapeado]
   - CNPJ: XX.XXX.XXX/XXXX-XX (se disponível)
   - CNAE titular: código (campo cnae_titular na lista, quando enriquecido)
   - Área: X ha (se disponível)
Quando o utilizador pedir limite de área em hectares ("mais de 100 ha", "entre 50 e 200 ha", \
"acima de 500 hectares"), chame jazidas__buscar_jazidas com area_min_ha e/ou area_max_ha \
(float). PROIBIDO aplicar esse filtro só no texto da resposta — sem estes parâmetros \
o mapa e a lista incluem jazidas fora do critério.
Para **listar processos cujo titular tenha um CNAE** (ex.: 07.29-4), use \
``jazidas__buscar_jazidas`` com ``codigos_cnae_titular="07.29-4"`` (CSV se vários) \
e, se quiser delimitar geograficamente, ``uf="MG"`` ou ``codigo_ibge``. \
O parâmetro ``termo_busca`` é opcional nesse caso (pode combinar CNAE + substância).

CAMPOS PARA DETALHES DE UM PROCESSO (jazidas__detalhes_processo) — \
apresente TODOS os campos não-nulos retornados em `processo` e `empresa`. \
OBRIGATÓRIO: mesmo que o usuário pergunte apenas por UM campo (ex.: "qual substância", \
"qual a fase", "quem é o titular"), SEMPRE emita DOIS CARDS NUMERADOS SEPARADOS \
(um para o processo, outro para a empresa titular). Isso permite ao usuário clicar \
em qualquer um para ver o marcador no mapa (pin de jazida e pin de empresa são \
distintos). NUNCA apresente como texto livre, único card, ou bullets soltos.

ESTRUTURA OBRIGATÓRIA — dois cards numerados:

1. Processo NÚMERO/ANO — [substância principal]
   - Processo: NÚMERO/ANO  ← OBRIGATÓRIO, sempre o primeiro detalhe (binda ao pin de jazida)
   - Substância(s): lista de processo.substancias_nomes
   - Fase ANM: mapeada conforme regra 5 acima
   - Área: processo.area_ha em ha
   - Município/UF: processo.municipios_nomes + processo.uf
   - Data de protocolo: processo.dt_protocolo (formato DD/MM/AAAA se disponível)
   - Validade da concessão: processo.dt_validade (omitir se ausente)
   - CFEM: se processo.cfem.total_historico > 0, informe total histórico e último ano \
     (ex: "Total histórico: R$ 1,2 M | Último ano: R$ 180 K"); se 0, escreva \
     "Nenhum recolhimento registrado"
   - Prioridade estratégica: processo.prioridade_estrategica (omitir se nulo)
   - Categorias estratégicas: processo.categorias_estrategicas (omitir se lista vazia)
   - Restrições: se processo.n_restricoes_ti > 0 OU processo.n_restricoes_uc > 0, \
     informe o número de TIs/UCs sobrepostas; se ambos = 0, escreva "Sem restrições de TI/UC"
   - Substâncias CPRM: processo.cprm_substancias + n_ocorrencias_cprm (omitir se ausente)

2. [RAZÃO SOCIAL DO TITULAR]
   - CNPJ: empresa.cnpj_completo formatado XX.XXX.XXX/XXXX-XX  ← primeiro detalhe \
     (binda ao pin de empresa)
   - Situação RFB: empresa.situacao_rfb (Ativa / Baixada / Inapta)
   - CNAE principal: empresa.cnae_principal — código + descrição
   - Capital social: empresa.capital_social formatado em R$ (omitir se nulo)
   - Porte: empresa.porte (omitir se nulo)
   - Telefone: empresa.contato.telefone (omitir se nulo)
   - Email: empresa.contato.email (omitir se nulo)
   - Endereço: empresa.contato.endereco (omitir se nulo)
   - Sócios: lista empresa.socios com nome + qualificação (omitir se lista vazia)

REGRA: NUNCA escreva "Não informado" — omita o campo inteiro se o valor for nulo ou 0.
REGRA: Se a tool não retornar bloco `empresa` (titular sem CNPJ enriquecido), emita \
APENAS o card 1 (processo) — não invente dados de empresa.

5. Mapeamento obrigatório de Fase ANM → Status legível:
   Concessão de Lavra / Lavra Garimpeira → Ativa
   Requerimento de Lavra / Autorização de Pesquisa / Requerimento de Pesquisa → Em análise
   Disponibilidade → Disponível
   Licenciamento → Licenciada
6. Ordem jazidas: Ativa → Em análise → Disponível.
7. Encerre com 1 frase oferecendo mais detalhes.

PROIBIDO (viola o formato da interface):
- NÃO use headers markdown: ### ou ## ou **Titulo**
- NÃO coloque CNPJ dentro do título do item — coloque em "   - CNPJ: ..."
- NÃO escreva "- NOME (CNPJ: ...)" — o item numerado deve ser só o nome
- NÃO misture formato livre com listas — siga o padrão exato
- NÃO escreva "Não informado" — omita o campo

EXEMPLO CORRETO (empresa):
Encontrei 7 empresas de pré-moldados em Montes Claros/MG.

Pré-moldados — 7 empresas encontradas:
1. Deposito Premoc Ltda
   - CNPJ: 02.166.871/0001-58
   - Município: Montes Claros/MG
   - Porte: Microempresa
   - Contato: (38) 3224-5678
   - Endereço: Rua das Flores, 123, Centro

2. Pré-Moldados Primavera
   - CNPJ: 43.146.568/0001-64
   - Município: Montes Claros/MG

EXEMPLO CORRETO (jazida):
Areia Lavada — 5 jazidas encontradas:
1. Dinamus Mineração Ltda
   - Processo: 832.596/2024
   - Fase: Em análise
   - CNPJ: 33.111.043/0001-01
   - Área: 2,96 ha\


10. ANÁLISE COMPLETA DE PROCESSO ANM — quando o usuário pedir "análise \
estratégica", "due diligence", "análise de risco" ou "análise completa" \
de um processo ou depósito mineral, SEMPRE execute o seguinte conjunto \
de tools em paralelo (após jazidas__detalhes_processo): \
   a. jazidas__consultar_cfem_processo → histórico de produção/royalties \
   b. jazidas__buscar_restricoes_geo   → sobreposição TI/UC \
   c. empresas__risco_ambiental_empresa(cnpj_basico=CNPJ_DO_TITULAR) → \
      autuações IBAMA (SIFISC) ligadas ao CNPJ básico do titular \
   d. empresas__autuacoes_por_area(lat, lon, raio_km) → mesma base IBAMA, \
      por proximidade geográfica ao centróide da jazida (capta casos sem \
      match de CNPJ ou infrações de terceiros na área) \
   ❌ NUNCA produza análise de risco jurídico/ambiental sem ter chamado \
estas quatro tools — declarar "não há sobreposição" ou "nenhuma autuação" \
sem dados das tools é informação não verificada.

Diretriz de rota: {route_hint}

{route_execution_hint}\
"""

MAX_TOOL_CALLS_DEFAULT = 24

SEARCH_TOOLS = {"buscar_fornecedores", "buscar_empresas", "buscar_por_socio"}

# Polygon geometry is now fetched on-demand via GET /api/v1/geo/jazida/{id}/poligono.
# Tools no longer receive incluir_geometria=True — keeps tool responses lean and
# prevents context window bloat from large GeoJSON payloads.
GEOMETRY_TOOLS: set[str] = set()


def _parse_tool_result(msg: ToolMessage) -> dict | None:
    """Parse a ToolMessage content into a dict, handling MCP block format."""
    raw = msg.content
    if isinstance(raw, list):
        for block in raw:
            if isinstance(block, dict) and block.get("type") == "text":
                raw = block.get("text", "")
                break
            if hasattr(block, "type") and getattr(block, "type", None) == "text":
                raw = getattr(block, "text", "")
                break
        else:
            return None
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None


def _infer_categoria(tool_name: str) -> str:
    """Map MCP tool name → estudo categoria."""
    clean = tool_name.split("__", 1)[-1] if "__" in tool_name else tool_name
    if clean == "buscar_fornecedores":
        return "material_mineracao"
    if clean in ("buscar_empresas", "buscar_por_socio"):
        return "produto_comercial"
    return "hibrido"


def _extract_termo_from_args(tool_call: dict) -> str:
    """Extract the search term from tool call arguments."""
    args = tool_call.get("args", {})
    return (
        args.get("termo_busca")
        or args.get("substancia")
        or args.get("nome_socio")
        or ""
    )


def _safe_str(val: Any) -> str:
    """Coerce a value to string, returning '' for dicts/lists/None."""
    if val is None or isinstance(val, (dict, list)):
        return ""
    return str(val)


def _empresa_to_fornecedor(item: dict) -> dict | None:
    """Convert an empresa result to AddFornecedorRequest-compatible dict."""
    cnpj = _safe_str(item.get("cnpj_completo") or item.get("cnpj_basico"))
    nome = _safe_str(item.get("razao_social") or item.get("nome_fantasia"))
    if not cnpj or not nome:
        return None

    loc = item.get("localizacao")
    geo = None
    if isinstance(loc, dict) and loc.get("lat") and loc.get("lon"):
        geo = {"lat": loc["lat"], "lon": loc["lon"]}

    return {
        "id": cnpj.replace(".", "").replace("/", "").replace("-", ""),
        "tipo_fonte": "cnpj",
        "nome": nome,
        "cnpj": cnpj,
        "localizacao": geo,
        "endereco": _safe_str(item.get("endereco")) or None,
        "municipio": _safe_str(item.get("municipio")) or None,
        "uf": _safe_str(item.get("uf")) or None,
        "cnae_principal": _safe_str(item.get("cnae_codigo")) or None,
        "cnae_descricao": _safe_str(item.get("cnae_descricao")) or None,
        "porte": _safe_str(item.get("porte")) or None,
        "situacao_cadastral": _safe_str(item.get("situacao")) or None,
        "distancia_km": item.get("distancia_km"),
        "adicionado_em": datetime.utcnow().isoformat(),
        "adicionado_por": "agente_ia",
    }


def _jazida_to_fornecedor(item: dict) -> dict | None:
    """Convert a jazida/fornecedor result to AddFornecedorRequest-compatible dict."""
    processo = _safe_str(item.get("ds_processo") or item.get("processo"))
    titular = item.get("titular") or {}
    if not isinstance(titular, dict):
        titular = {}
    nome = _safe_str(titular.get("nome") or titular.get("razao_social"))
    if not processo:
        return None

    loc = item.get("localizacao")
    geo = None
    if isinstance(loc, dict) and loc.get("lat") and loc.get("lon"):
        geo = {"lat": loc["lat"], "lon": loc["lon"]}

    substancias = item.get("substancias") or []
    if not isinstance(substancias, list):
        substancias = [str(substancias)]
    substancia = _safe_str(substancias[0]) if substancias else None

    municipios = item.get("municipios") or []
    if not isinstance(municipios, list):
        municipios = [str(municipios)]
    ufs = item.get("uf") or []
    if not isinstance(ufs, list):
        ufs = [str(ufs)]

    return {
        "id": processo.replace(".", "").replace("/", ""),
        "tipo_fonte": "anm",
        "nome": nome or f"Processo {processo}",
        "processo_anm": processo,
        "substancia": substancia,
        "fase": _safe_str(item.get("fase")) or None,
        "localizacao": geo,
        "municipio": _safe_str(municipios[0]) if municipios else None,
        "uf": _safe_str(ufs[0]) if ufs else None,
        "distancia_km": item.get("distancia_km"),
        "adicionado_em": datetime.utcnow().isoformat(),
        "adicionado_por": "agente_ia",
    }


async def _auto_update_analise(
    analise_id: str,
    categoria: str,
    termo_busca: str,
) -> bool:
    """Update análise fields in MongoDB after first search."""
    from app.db.mongodb import get_db_direct
    from bson import ObjectId

    try:
        db = await get_db_direct()
        oid = ObjectId(analise_id)
        doc = await db["analises"].find_one({"_id": oid}, {"termo_busca": 1})
        if doc and doc.get("termo_busca"):
            return False

        await db["analises"].update_one(
            {"_id": oid},
            {
                "$set": {
                    "categoria": categoria,
                    "termo_busca": termo_busca,
                    "status": "em_analise",
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        logger.info("Auto-updated análise %s: categoria=%s, termo=%s", analise_id, categoria, termo_busca)
        return True
    except Exception as e:
        logger.warning("Failed to auto-update análise %s: %s", analise_id, e)
        return False


async def _auto_save_fornecedores(
    analise_id: str,
    fornecedores: list[dict],
) -> int:
    """Bulk-add fornecedores to análise in MongoDB, skipping duplicates."""
    from app.db.mongodb import get_db_direct
    from bson import ObjectId

    if not fornecedores:
        return 0

    try:
        db = await get_db_direct()
        oid = ObjectId(analise_id)

        doc = await db["analises"].find_one({"_id": oid}, {"fornecedores": 1})
        existing_ids = set()
        if doc and doc.get("fornecedores"):
            for f in doc["fornecedores"]:
                existing_ids.add((f.get("id"), f.get("tipo_fonte")))

        new_forn = []
        for f in fornecedores:
            key = (f.get("id"), f.get("tipo_fonte"))
            if key not in existing_ids:
                new_forn.append(f)
                existing_ids.add(key)

        if not new_forn:
            return 0

        await db["analises"].update_one(
            {"_id": oid},
            {
                "$push": {"fornecedores": {"$each": new_forn}},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )
        logger.info("Auto-saved %d fornecedores to análise %s", len(new_forn), analise_id)
        return len(new_forn)
    except Exception as e:
        logger.warning("Failed to auto-save fornecedores to análise %s: %s", analise_id, e)
        return 0


def _build_llm() -> AzureChatOpenAI:
    """Build the Azure OpenAI chat model from config."""
    return AzureChatOpenAI(
        azure_deployment=mcp_settings.azure_openai_chat_deployment,
        azure_endpoint=mcp_settings.azure_openai_endpoint,
        api_key=mcp_settings.azure_openai_api_key,
        api_version=mcp_settings.azure_openai_api_version,
        temperature=mcp_settings.azure_openai_chat_temperature,
        max_tokens=mcp_settings.azure_openai_chat_max_tokens,
    )


def build_graph(
    provider: UnifiedMCPProvider,
    max_tool_calls: int = MAX_TOOL_CALLS_DEFAULT,
) -> StateGraph:
    """
    Build and compile the LangGraph agent graph with intent routing.

    Args:
        provider: Connected UnifiedMCPProvider with all MCP servers.
        max_tool_calls: Safety limit for tool calls per turn.

    Returns:
        Compiled StateGraph ready for ainvoke/astream.
    """
    all_lc_tools = convert_mcp_tools_to_langchain(provider)
    llm = _build_llm()

    tool_node = ToolNode(all_lc_tools)
    _estudo_updated_flags: dict[str, bool] = {}

    # Pre-compute tool subsets per route (avoids filtering on every cycle)
    tool_subsets: dict[str, list] = {}
    # Inclui as rotas dedicadas a buscas DENTRO de uma isócrona — restringem
    # a allow-list à synthetic tool atômica geo__buscar_dentro_de_isocrona,
    # garantindo que o LLM não desvie pra calcular_isocrona/buscar_*
    # quando o usuário deixou claro o contexto "dentro da área".
    for route_name in (
        "mineral", "empresa", "hybrid", "geo", "general",
        "mineral_em_isocrona", "empresa_em_isocrona", "hibrido_em_isocrona",
    ):
        tool_subsets[route_name] = filter_tools_by_route(all_lc_tools, route_name)

    logger.info(
        "Tool subsets built — "
        + ", ".join(
            f"{r}: {len(t)} tools" for r, t in tool_subsets.items()
        )
    )

    # ── Node: router ──────────────────────────────────────────────
    async def router_node(state: AgentState) -> dict:
        """Classify user intent and set route in state."""
        messages = state["messages"]
        last_human_msg = None
        last_human_idx = None

        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                last_human_msg = messages[i].content
                last_human_idx = i
                break

        if not last_human_msg:
            return {
                "route": DEFAULT_ROUTE,
                "route_reasoning": "No human message found — defaulting",
                "route_execution_hint": "",
            }

        # Pass conversation history before the latest message so the router
        # can correctly inherit the route for follow-up confirmations
        # (e.g. "sim, pode ampliar para 200km" after a zero-result search).
        prior_messages = messages[:last_human_idx] if last_human_idx else []

        classification = await classify_route(
            last_human_msg,
            llm,
            recent_messages=prior_messages,
        )

        from app.langgraph.route_planner import (
            analyze_route_request,
            format_route_execution_hint,
        )

        route_plan = analyze_route_request(str(last_human_msg))
        route_hint_block = format_route_execution_hint(route_plan)

        return {
            "route": classification.route,
            "route_reasoning": classification.reasoning,
            "route_execution_hint": route_hint_block,
        }

    # ── Node: agent ───────────────────────────────────────────────
    async def agent_node(state: AgentState) -> dict:
        """Invoke LLM with route-filtered tools and tailored prompt."""
        route = state.get("route", DEFAULT_ROUTE)
        messages = state["messages"]

        route_tools = tool_subsets.get(route, all_lc_tools)
        route_hint = get_route_hint(route)
        route_execution_hint = state.get("route_execution_hint") or ""
        projeto_context_str = state.get("projeto_context_str")
        analise_context_str = state.get("analise_context_str")

        system_content = SYSTEM_PROMPT.format(
            today=date.today().strftime("%d/%m/%Y"),
            max_tool_calls=max_tool_calls,
            route_hint=route_hint,
            route_execution_hint=route_execution_hint,
        )
        if analise_context_str:
            system_content = analise_context_str + "\n\n" + system_content
        if projeto_context_str:
            system_content = projeto_context_str + "\n\n" + system_content

        system_message = SystemMessage(content=system_content)

        # Always inject fresh system message (replace stale one if present)
        if messages and isinstance(messages[0], SystemMessage):
            messages = [system_message, *messages[1:]]
        else:
            messages = [system_message, *messages]

        if route_tools:
            bound_llm = llm.bind_tools(route_tools)
        else:
            bound_llm = llm

        response = await bound_llm.ainvoke(messages)

        new_count = state.get("tool_calls_count", 0)
        if hasattr(response, "tool_calls") and response.tool_calls:
            new_count += len(response.tool_calls)
            for tc in response.tool_calls:
                logger.info(
                    f"LLM tool_call: {tc['name']}({tc.get('args', {})})"
                )

        return {
            "messages": [response],
            "tool_calls_count": new_count,
        }

    # ── Node: post_tool — auto-populate estudo + save fornecedores ──
    async def post_tool_node(state: AgentState) -> dict:
        """After tool execution, auto-save results to análise if context exists."""
        analise_id = state.get("analise_id")
        if not analise_id:
            return {}

        try:
            messages = state["messages"]

            ai_msg = None
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.tool_calls:
                    ai_msg = msg
                    break

            if not ai_msg:
                return {}

            tool_call_map = {tc["id"]: tc for tc in ai_msg.tool_calls}
            tool_results: list[tuple[str, str, dict, dict]] = []
            for msg in messages:
                if isinstance(msg, ToolMessage) and msg.tool_call_id in tool_call_map:
                    tc = tool_call_map[msg.tool_call_id]
                    clean_name = tc["name"].split("__", 1)[-1] if "__" in tc["name"] else tc["name"]
                    if clean_name in SEARCH_TOOLS:
                        parsed = _parse_tool_result(msg)
                        if parsed and isinstance(parsed, dict):
                            tool_results.append((tc["name"], clean_name, tc, parsed))

            if not tool_results:
                return {}

            first_categoria = None
            first_termo = None

            for full_name, _clean_name, tc, _data in tool_results:
                if first_categoria is None:
                    first_categoria = _infer_categoria(full_name)
                    first_termo = _extract_termo_from_args(tc)
                    break

            conv_key = f"{state.get('conversation_id', '')}:{analise_id}"
            if first_categoria and first_termo and conv_key not in _estudo_updated_flags:
                updated = await _auto_update_analise(analise_id, first_categoria, first_termo)
                if updated:
                    _estudo_updated_flags[conv_key] = True

        except Exception as e:
            logger.exception("post_tool_node failed (non-fatal): %s", e)

        return {}

    # ── Edge: should_continue ─────────────────────────────────────
    def should_continue(state: AgentState) -> Literal["tool_executor", "__end__"]:
        """Decide whether to execute tools or finish."""
        last_message = state["messages"][-1]

        if not isinstance(last_message, AIMessage):
            return END

        if not last_message.tool_calls:
            return END

        current_count = state.get("tool_calls_count", 0)
        limit = state.get("max_tool_calls", max_tool_calls)
        if current_count >= limit:
            logger.warning(
                f"Tool call limit reached ({current_count}/{limit}). "
                "Forcing synthesis."
            )
            return END

        return "tool_executor"

    # ── Build graph ───────────────────────────────────────────────
    graph = StateGraph(AgentState)

    graph.add_node("router", router_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tool_executor", tool_node)
    graph.add_node("post_tool", post_tool_node)

    graph.set_entry_point("router")

    graph.add_edge("router", "agent")

    graph.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tool_executor": "tool_executor",
            END: END,
        },
    )

    graph.add_edge("tool_executor", "post_tool")
    graph.add_edge("post_tool", "agent")

    compiled = graph.compile()

    logger.info(
        f"LangGraph agent compiled — "
        f"{len(all_lc_tools)} tools, "
        f"{len(tool_subsets)} routes ({'/'.join(tool_subsets)}), "
        f"max_tool_calls={max_tool_calls}"
    )

    return compiled
