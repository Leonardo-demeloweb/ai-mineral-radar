#!/usr/bin/env python3
"""
MineralRadar — Teste de Cenários
===============================================

Executa os 9 cenários reais da gerente de suprimentos diretamente contra
os MCP Servers + LangGraph, sem necessidade do endpoint de chat REST.

Pré-requisitos:
    1. MCP Server Jazidas rodando:  python -m mcp_servers.jazidas.server   (porta 8110)
    2. MCP Server Empresas rodando: python -m mcp_servers.empresas.server  (porta 8111)
    3. MCP Server Geo rodando:      python -m mcp_servers.geo.server       (porta 8112)
    4. Variáveis de ambiente no .env (OpenSearch, Azure OpenAI)

Uso:
    cd backend
    python scripts/test_cenarios.py                    # todos os cenários
    python scripts/test_cenarios.py --cenario 1        # cenário específico
    python scripts/test_cenarios.py --cenario 1,3,5    # cenários selecionados
    python scripts/test_cenarios.py --rapido           # sem esperar resposta completa (só rota)

Saída:
    - Console com resultados formatados por cenário
    - Traces automáticos no LangSmith (se LANGCHAIN_API_KEY configurada)
    - Arquivo JSON de resultados em scripts/resultados_cenarios.json
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ── Path setup ────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Cores ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def h1(text: str):
    print(f"\n{BOLD}{BLUE}{'═'*70}{RESET}")
    print(f"{BOLD}{BLUE}  {text}{RESET}")
    print(f"{BOLD}{BLUE}{'═'*70}{RESET}\n")

def h2(text: str):
    print(f"\n{CYAN}{'─'*70}{RESET}")
    print(f"{CYAN}  {text}{RESET}")
    print(f"{CYAN}{'─'*70}{RESET}")

def ok(text: str):   print(f"{GREEN}  ✅ {text}{RESET}")
def err(text: str):  print(f"{RED}  ❌ {text}{RESET}")
def warn(text: str): print(f"{YELLOW}  ⚠️  {text}{RESET}")
def info(text: str): print(f"     {text}")

# ── 9 Cenários da Gerente de Suprimentos ──────────────────────────────
#
# Filosofia dos testes:
#   - rota_esperada:    o router DEVE classificar nesta rota (ou próxima)
#   - dominios_validos: quais MCPs o agente PODE usar (não obrigatório usar todos)
#   - validacao:        a RESPOSTA deve conter esses termos (valida o output, não o caminho)
#
# O agente decide sozinho quais tools chamar dentro dos domínios disponíveis.
# O teste observa, não prescreve.
#
CENARIOS: list[dict[str, Any]] = [
    {
        "id": 1,
        "pergunta": "Quais as empresas de pré moldados existem na cidade de Montes Claros? Com CNPJ e dados para contato",
        "rota_esperada": "empresa",
        "rotas_aceitaveis": ["empresa", "hybrid"],
        "dominios_validos": ["geo__", "empresas__"],
        "validacao": lambda r: any(
            kw in r.lower() for kw in ["cnpj", "pré-moldado", "concreto", "empresa", "contato"]
        ),
    },
    {
        "id": 2,
        "pergunta": "Quais empresas podem me fornecer brita na região de Governador Valadares?",
        "rota_esperada": "hybrid",
        "rotas_aceitaveis": ["hybrid", "mineral", "empresa"],
        "dominios_validos": ["geo__", "jazidas__", "empresas__"],
        "validacao": lambda r: any(
            kw in r.lower() for kw in ["brita", "granito", "fornecedor", "empresa", "pedreira"]
        ),
    },
    {
        "id": 3,
        "pergunta": "Quais pedreiras existem na cidade de Belo Horizonte?",
        "rota_esperada": "hybrid",
        "rotas_aceitaveis": ["hybrid", "mineral", "empresa"],
        "dominios_validos": ["geo__", "jazidas__", "empresas__"],
        "validacao": lambda r: any(
            kw in r.lower() for kw in ["pedreira", "granito", "extração", "empresa", "mineração"]
        ),
    },
    {
        "id": 4,
        "pergunta": "Quais são as gráficas em Magé, RJ?",
        "rota_esperada": "empresa",
        "rotas_aceitaveis": ["empresa"],
        "dominios_validos": ["geo__", "empresas__"],
        "validacao": lambda r: any(
            kw in r.lower() for kw in ["gráfica", "impressão", "empresa", "cnpj", "magé"]
        ),
    },
    {
        "id": 5,
        "pergunta": "Me passar contatos de empresas de areia em Taubaté/SP",
        "rota_esperada": "hybrid",
        "rotas_aceitaveis": ["hybrid", "mineral", "empresa"],
        "dominios_validos": ["geo__", "jazidas__", "empresas__"],
        "validacao": lambda r: any(
            kw in r.lower() for kw in ["areia", "fornecedor", "contato", "telefone", "empresa"]
        ),
    },
    {
        "id": 6,
        "pergunta": "Quais áreas licenciadas na ANM para fornecimento de brita em Teofilo Otoni que não estão em operação hoje?",
        "rota_esperada": "mineral",
        "rotas_aceitaveis": ["mineral", "hybrid"],
        "dominios_validos": ["geo__", "jazidas__"],
        "validacao": lambda r: any(
            kw in r.lower() for kw in ["anm", "processo", "licença", "concessão", "inativo", "brita"]
        ),
    },
    {
        "id": 7,
        "pergunta": "Tem alguma pedreira licenciada na ANM em Teofilo Otoni que não está funcionando?",
        "rota_esperada": "mineral",
        "rotas_aceitaveis": ["mineral", "hybrid"],
        "dominios_validos": ["geo__", "jazidas__"],
        "validacao": lambda r: any(
            kw in r.lower() for kw in ["anm", "processo", "pedreira", "inativo", "licença", "concessão"]
        ),
    },
    {
        "id": 8,
        "pergunta": "Qual as maiores empresa de pre moldados na região do Rio de Janeiro?",
        "rota_esperada": "empresa",
        "rotas_aceitaveis": ["empresa", "hybrid"],
        "dominios_validos": ["geo__", "empresas__"],
        "validacao": lambda r: any(
            kw in r.lower() for kw in ["pré-moldado", "concreto", "empresa", "cnpj", "rio de janeiro"]
        ),
    },
    {
        "id": 9,
        "pergunta": "Quais empresas prestam o serviço de pavimentação proximo ao município de Itaguaí?",
        "rota_esperada": "empresa",
        "rotas_aceitaveis": ["empresa", "hybrid"],
        "dominios_validos": ["geo__", "empresas__"],
        "validacao": lambda r: any(
            kw in r.lower() for kw in ["pavimentação", "construção", "rodovia", "empresa", "itaguaí"]
        ),
    },
]


# ── Helpers ───────────────────────────────────────────────────────────

def extrair_tools_usadas(messages: list) -> list[str]:
    """Extrai nomes das tools chamadas a partir das mensagens do LangGraph."""
    tools = []
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                if name and name not in tools:
                    tools.append(name)
    return tools


def truncar(texto: str, max_chars: int = 1500) -> str:
    if len(texto) > max_chars:
        return texto[:max_chars] + f"... [{len(texto) - max_chars} chars omitidos]"
    return texto


# ── Core: executar um cenário ─────────────────────────────────────────

async def executar_cenario(
    cenario: dict,
    graph,
    apenas_rota: bool = False,
) -> dict[str, Any]:
    """Executa um único cenário e retorna resultado estruturado."""
    from langchain_core.messages import HumanMessage

    inicio = time.time()
    resultado = {
        "id": cenario["id"],
        "pergunta": cenario["pergunta"],
        "rota_esperada": cenario["rota_esperada"],
        "rotas_aceitaveis": cenario.get("rotas_aceitaveis", [cenario["rota_esperada"]]),
        "dominios_validos": cenario.get("dominios_validos", []),
        "sucesso": False,
        "erro": None,
        "rota_obtida": None,
        "route_reasoning": None,
        "tools_usadas": [],
        "resposta_resumo": None,
        "validacao_conteudo": False,
        "elapsed_s": 0,
    }

    try:
        state_input = {
            "messages": [HumanMessage(content=cenario["pergunta"])],
            "conversation_id": f"test-cenario-{cenario['id']}",
            "obra_id": None,
            "estudo_id": None,
            "route": "",
            "route_reasoning": "",
            "tool_calls_count": 0,
            "max_tool_calls": 5 if apenas_rota else 10,
        }

        # Se modo rápido, interrompe após o nó router
        if apenas_rota:
            # Executa só até o router (stream por nó)
            async for event in graph.astream(state_input, stream_mode="updates"):
                if "router" in event:
                    router_state = event["router"]
                    resultado["rota_obtida"] = router_state.get("route")
                    resultado["route_reasoning"] = router_state.get("route_reasoning")
                    resultado["sucesso"] = True
                    break
        else:
            # Executa completo (timeout de 120s por cenário)
            TIMEOUT_S = 120
            try:
                final_state = await asyncio.wait_for(
                    graph.ainvoke(state_input), timeout=TIMEOUT_S
                )
            except asyncio.TimeoutError:
                resultado["erro"] = f"Timeout após {TIMEOUT_S}s"
                resultado["elapsed_s"] = round(time.time() - inicio, 1)
                return resultado

            resultado["rota_obtida"] = final_state.get("route")
            resultado["route_reasoning"] = final_state.get("route_reasoning")
            resultado["tools_usadas"] = extrair_tools_usadas(final_state.get("messages", []))

            # Última mensagem do AI
            msgs = final_state.get("messages", [])
            from langchain_core.messages import AIMessage
            for msg in reversed(msgs):
                if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                    conteudo = msg.content
                    if isinstance(conteudo, list):
                        conteudo = " ".join(
                            p.get("text", "") if isinstance(p, dict) else str(p)
                            for p in conteudo
                        )
                    resultado["resposta_resumo"] = truncar(str(conteudo))
                    resultado["validacao_conteudo"] = cenario["validacao"](str(conteudo))
                    break

            resultado["sucesso"] = True

    except Exception as e:
        resultado["erro"] = f"{type(e).__name__}: {str(e)}"

    resultado["elapsed_s"] = round(time.time() - inicio, 1)
    return resultado


# ── Exibir resultado de um cenário ───────────────────────────────────

def exibir_resultado(res: dict, apenas_rota: bool = False):
    status = f"{GREEN}PASSOU{RESET}" if res["sucesso"] and not res["erro"] else f"{RED}FALHOU{RESET}"
    h2(f"Cenário {res['id']} — {status}  ({res['elapsed_s']}s)")

    info(f"Pergunta:  {res['pergunta']}")

    # Rota — aceita qualquer rota da lista rotas_aceitaveis
    rota_obtida = res["rota_obtida"] or "?"
    rotas_aceitaveis = res.get("rotas_aceitaveis", [res["rota_esperada"]])
    rota_ok = rota_obtida in rotas_aceitaveis
    rota_icon = "✅" if rota_ok else "⚠️ "
    print(f"  {rota_icon} Rota classificada: {BOLD}{rota_obtida}{RESET} "
          f"(esperada: {res['rota_esperada']} | aceitáveis: {', '.join(rotas_aceitaveis)})")
    if res["route_reasoning"]:
        info(f"     Reasoning: {res['route_reasoning']}")

    if not apenas_rota:
        # Tools — observa quais domínios foram usados (sem prescrever tools específicas)
        tools_usadas = res["tools_usadas"]
        dominios_validos = res.get("dominios_validos", [])

        # Verifica se as tools usadas pertencem aos domínios válidos para a rota
        tools_fora = [t for t in tools_usadas if not any(t.startswith(d) for d in dominios_validos)]

        print(f"  ℹ️  Tools usadas ({len(tools_usadas)}): {', '.join(tools_usadas) or 'nenhuma'}")

        if tools_fora:
            warn(f"Tools fora dos domínios esperados ({dominios_validos}): {', '.join(tools_fora)}")
        elif tools_usadas:
            ok(f"Todas as tools pertencem aos domínios válidos da rota")

        # Validação de conteúdo
        val_icon = "✅" if res["validacao_conteudo"] else "⚠️ "
        print(f"  {val_icon} Conteúdo válido: {'Sim' if res['validacao_conteudo'] else 'Não — palavras-chave ausentes na resposta'}")

        # Resposta
        if res["resposta_resumo"]:
            print(f"\n  {BOLD}Resposta:{RESET}")
            for linha in res["resposta_resumo"].split("\n")[:8]:
                info(f"  {linha}")

    if res["erro"]:
        err(f"Erro: {res['erro']}")


# ── Resumo final ──────────────────────────────────────────────────────

def exibir_resumo(resultados: list[dict], apenas_rota: bool):
    h1("RESUMO DOS TESTES")

    total = len(resultados)
    sucessos = sum(1 for r in resultados if r["sucesso"] and not r["erro"])
    rotas_corretas = sum(
        1 for r in resultados
        if r["rota_obtida"] in r.get("rotas_aceitaveis", [r["rota_esperada"]])
    )

    ok(f"Cenários executados: {total}")
    ok(f"Sem erros:           {sucessos}/{total}")
    ok(f"Rotas corretas:      {rotas_corretas}/{total}")

    if not apenas_rota:
        val_ok = sum(1 for r in resultados if r.get("validacao_conteudo"))
        ok(f"Conteúdo válido:     {val_ok}/{total}")

    elapsed_total = sum(r["elapsed_s"] for r in resultados)
    info(f"\n  Tempo total: {elapsed_total:.1f}s  |  Média: {elapsed_total/total:.1f}s/cenário")

    # Tabela por cenário
    print(f"\n  {'#':<4} {'Rota':<10} {'Esperada':<10} {'Conteúdo':<10} {'Tempo':<8} {'Status'}")
    print(f"  {'─'*55}")
    for r in resultados:
        rota_ok   = "✅" if r["rota_obtida"] in r.get("rotas_aceitaveis", [r["rota_esperada"]]) else "⚠️ "
        val_ok    = "✅" if r.get("validacao_conteudo") else ("—" if apenas_rota else "⚠️ ")
        status    = "OK" if r["sucesso"] and not r["erro"] else "ERRO"
        print(f"  {r['id']:<4} {(r['rota_obtida'] or '?'):<10} {r['rota_esperada']:<10} {val_ok:<10} {r['elapsed_s']:<8} {status}")

    # Salvar JSON
    output_file = ROOT / "scripts" / "resultados_cenarios.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "total": total,
                "sucessos": sucessos,
                "rotas_corretas": rotas_corretas,
                "resultados": resultados,
            },
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    ok(f"\nResultados salvos em: {output_file}")


# ── Verificar pré-requisitos ──────────────────────────────────────────

def verificar_env_vars() -> bool:
    """Verifica se as variáveis de ambiente críticas estão presentes."""
    from mcp_servers.common.config import mcp_settings

    h2("Verificando variáveis de ambiente")
    todas_ok = True

    checks = [
        ("AZURE_OPENAI_ENDPOINT", mcp_settings.azure_openai_endpoint, True),
        ("AZURE_OPENAI_API_KEY", mcp_settings.azure_openai_api_key, True),
        ("AZURE_OPENAI_CHAT_DEPLOYMENT", mcp_settings.azure_openai_chat_deployment, True),
        ("OPENSEARCH_ENDPOINT", mcp_settings.opensearch_endpoint, True),
        ("OPENSEARCH_PASSWORD", mcp_settings.opensearch_password, True),
        ("REDIS_HOST", mcp_settings.redis_host, False),
        ("LANGCHAIN_API_KEY", os.getenv("LANGCHAIN_API_KEY", ""), False),
    ]

    for nome, valor, obrigatorio in checks:
        if valor:
            ok(f"{nome}: configurado")
        elif obrigatorio:
            err(f"{nome}: NÃO CONFIGURADO — obrigatório")
            todas_ok = False
        else:
            warn(f"{nome}: não configurado (opcional)")

    return todas_ok


async def verificar_mcps() -> bool:
    """Testa conectividade HTTP com os 3 MCP Servers antes de rodar."""
    import httpx

    urls = {
        "Jazidas (:8110)":  "http://localhost:8110/mcp",
        "Empresas (:8111)": "http://localhost:8111/mcp",
        "Geo (:8112)":      "http://localhost:8112/mcp",
    }

    h2("Verificando MCP Servers")
    todos_ok = True

    async with httpx.AsyncClient(timeout=5) as client:
        for nome, url in urls.items():
            try:
                resp = await client.get(url)
                ok(f"{nome} → UP (HTTP {resp.status_code})")
            except httpx.ConnectError:
                err(f"{nome} → OFFLINE — inicie com: python -m mcp_servers.<server>.server")
                todos_ok = False
            except Exception as e:
                warn(f"{nome} → {type(e).__name__}: {e}")

    return todos_ok


# ── Main ──────────────────────────────────────────────────────────────

async def main(ids_cenarios: list[int], apenas_rota: bool):
    h1("MineralRadar — Teste de Cenários")
    print(f"  Timestamp: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"  Modo:      {'Apenas rota (rápido)' if apenas_rota else 'Completo (LangGraph + MCPs)'}")
    print(f"  Cenários:  {ids_cenarios}")

    # 1. Verificar variáveis de ambiente
    env_ok = verificar_env_vars()
    if not env_ok:
        print(f"\n{RED}Variáveis obrigatórias ausentes. Configure o .env e tente novamente.{RESET}")
        sys.exit(1)

    # 2. Verificar MCPs
    mcps_ok = await verificar_mcps()
    if not mcps_ok:
        print(f"\n{YELLOW}Alguns MCPs estão offline. Suba os servidores e tente novamente.{RESET}")
        print(f"\n{BOLD}Para subir os MCPs (3 terminais separados):{RESET}")
        print("  cd backend && python -m mcp_servers.jazidas.server")
        print("  cd backend && python -m mcp_servers.empresas.server")
        print("  cd backend && python -m mcp_servers.geo.server")
        sys.exit(1)

    # 3. Inicializar LangGraph
    h2("Inicializando LangGraph + Router Agent")
    try:
        from mcp_servers.common.unified_mcp_provider import UnifiedMCPProvider
        from app.langgraph.graph import build_graph

        provider = UnifiedMCPProvider()
        await provider.connect()

        status = provider.status()
        for server, info_s in status.items():
            if info_s["connected"]:
                ok(f"MCP {server}: {info_s['tool_count']} tools conectadas")
            else:
                warn(f"MCP {server}: não conectado")

        graph = build_graph(provider)
        ok("LangGraph compilado com Router Agent")

    except Exception as e:
        err(f"Falha ao inicializar LangGraph: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 4. Executar cenários selecionados
    cenarios_selecionados = [c for c in CENARIOS if c["id"] in ids_cenarios]
    resultados = []

    for cenario in cenarios_selecionados:
        h1(f"Cenário {cenario['id']}/9")
        info(f'"{cenario["pergunta"]}"')
        print()

        res = await executar_cenario(cenario, graph, apenas_rota)
        exibir_resultado(res, apenas_rota)
        resultados.append(res)

        # Pausa entre cenários para evitar rate limit da Azure OpenAI
        if cenario["id"] != cenarios_selecionados[-1]["id"]:
            await asyncio.sleep(3)

    # 5. Resumo final
    exibir_resumo(resultados, apenas_rota)

    # 6. Desconectar
    await provider.disconnect()
    ok("MCPs desconectados. Teste concluído.")


# ── Entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Testa os 9 cenários da gerente de suprimentos contra os MCPs reais."
    )
    parser.add_argument(
        "--cenario",
        type=str,
        default="1,2,3,4,5,6,7,8,9",
        help="IDs dos cenários separados por vírgula (ex: 1,3,5). Default: todos.",
    )
    parser.add_argument(
        "--rapido",
        action="store_true",
        help="Modo rápido: executa apenas o nó Router (sem aguardar resposta completa do LangGraph).",
    )
    args = parser.parse_args()

    ids = [int(x.strip()) for x in args.cenario.split(",") if x.strip().isdigit()]
    ids = sorted(set(ids))

    if not ids:
        print(f"{RED}IDs inválidos. Use --cenario 1,2,3{RESET}")
        sys.exit(1)

    asyncio.run(main(ids, args.rapido))
