"""
Metals-API — Preços de Minerais em Tempo Real
===============================================

Consulta on-demand à Metals-API (metals-api.com) para retornar
cotações atualizadas de metais e minerais estratégicos.

Endpoints usados:
    /latest    → cotação atual (atualiza a cada 60 min no plano free)
    /<YYYY-MM-DD> → cotação histórica (para variação)

Lógica de preço:
    Quando base=USD, a API retorna taxas inversas (1 USD = X unidades do metal).
    Para obter o preço em USD por unidade: price_usd = 1 / rate
    A API também retorna diretamente "USD{SYMBOL}" = price_usd (apenas /latest).

Metais suportados (mapeamento PT-BR / EN → símbolo API):
    Preciosos  : ouro(XAU), prata(XAG), platina(XPT), paládio(XPD), ródio(XRH)
    Industriais: alumínio(ALU), cobre(COPPER), ferro(IRON), níquel(NICKEL),
                 estanho(TIN), zinco(ZINC), chumbo(LEAD)
    Estratégicos: nióbio(NIOBIUM), lítio(LITHIUM), cobalto(COBALT),
                  titânio(TITANIUM), tungstênio(TUNGSTEN)

Unidades:
    Metais preciosos  → USD/troy oz (onça troy = 31.1035 g)
    Metais industriais → USD/tonelada métrica (conversão interna)

Cache Redis:
    TTL configurável via ``cache_metals_price_ttl`` (default 300s = 5 min).
    Chave: "metals:price:{simbolo}:{moeda}"
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger("mcp.jazidas.queries.precos_minerais")

# ─────────────────────────────────────────────────────────────────────────────
# Mapeamento de minerais → símbolo Metals-API
# ─────────────────────────────────────────────────────────────────────────────

# Cada entrada: nome normalizado (lower, sem acento) → símbolo API
_SYMBOL_MAP: dict[str, str] = {
    # Ouro
    "ouro": "XAU", "gold": "XAU", "au": "XAU", "xau": "XAU",
    # Prata
    "prata": "XAG", "silver": "XAG", "ag": "XAG", "xag": "XAG",
    # Platina
    "platina": "XPT", "platinum": "XPT", "pt": "XPT", "xpt": "XPT",
    # Paládio
    "paladio": "XPD", "palladium": "XPD", "pd": "XPD", "xpd": "XPD",
    "paládio": "XPD",
    # Ródio
    "rodio": "XRH", "rhodium": "XRH", "rh": "XRH", "xrh": "XRH",
    "ródio": "XRH",
    # Alumínio
    "aluminio": "ALU", "aluminum": "ALU", "aluminium": "ALU",
    "alumínio": "ALU", "al": "ALU", "alu": "ALU",
    # Cobre
    "cobre": "COPPER", "copper": "COPPER", "cu": "COPPER",
    # Ferro
    "ferro": "IRON", "iron": "IRON", "fe": "IRON",
    # Níquel
    "niquel": "NICKEL", "nickel": "NICKEL", "ni": "NICKEL",
    "níquel": "NICKEL",
    # Estanho
    "estanho": "TIN", "tin": "TIN", "sn": "TIN",
    # Zinco
    "zinco": "ZINC", "zinc": "ZINC", "zn": "ZINC",
    # Chumbo
    "chumbo": "LEAD", "lead": "LEAD", "pb": "LEAD",
    # Nióbio
    "niobio": "NIOBIUM", "niobium": "NIOBIUM", "nb": "NIOBIUM",
    "nióbio": "NIOBIUM",
    # Lítio
    "litio": "LITHIUM", "lithium": "LITHIUM", "li": "LITHIUM",
    "lítio": "LITHIUM",
    "carbonato de litio": "LITHIUM", "carbonato de lítio": "LITHIUM",
    "lithium carbonate": "LITHIUM",
    # Cobalto
    "cobalto": "COBALT", "cobalt": "COBALT", "co": "COBALT",
    # Titânio
    "titanio": "TITANIUM", "titanium": "TITANIUM", "ti": "TITANIUM",
    "titânio": "TITANIUM",
    # Tungstênio
    "tungstenio": "TUNGSTEN", "tungsten": "TUNGSTEN", "w": "TUNGSTEN",
    "tungstênio": "TUNGSTEN",
    # Magnésio
    "magnesio": "MAGNESIUM", "magnesium": "MAGNESIUM", "mg": "MAGNESIUM",
    "magnésio": "MAGNESIUM",
    # Manganês
    "manganes": "MANGANESE", "manganese": "MANGANESE", "mn": "MANGANESE",
    "manganês": "MANGANESE",
    # Molibdênio
    "molibdenio": "MOLYBDENUM", "molybdenum": "MOLYBDENUM", "mo": "MOLYBDENUM",
    "molibdênio": "MOLYBDENUM",
    # Cromo
    "cromo": "CHROMIUM", "chromium": "CHROMIUM", "cr": "CHROMIUM",
    # Vanádio
    "vanadio": "VANADIUM", "vanadium": "VANADIUM", "v": "VANADIUM",
    "vanádio": "VANADIUM",
}

# Nome legível para exibição
_SYMBOL_NAMES: dict[str, str] = {
    "XAU": "Ouro (Gold)", "XAG": "Prata (Silver)", "XPT": "Platina (Platinum)",
    "XPD": "Paládio (Palladium)", "XRH": "Ródio (Rhodium)",
    "ALU": "Alumínio (Aluminum)", "COPPER": "Cobre (Copper)", "IRON": "Ferro (Iron)",
    "NICKEL": "Níquel (Nickel)", "TIN": "Estanho (Tin)", "ZINC": "Zinco (Zinc)",
    "LEAD": "Chumbo (Lead)", "NIOBIUM": "Nióbio (Niobium)", "LITHIUM": "Lítio (Lithium)",
    "COBALT": "Cobalto (Cobalt)", "TITANIUM": "Titânio (Titanium)",
    "TUNGSTEN": "Tungstênio (Tungsten)", "MAGNESIUM": "Magnésio (Magnesium)",
    "MANGANESE": "Manganês (Manganese)", "MOLYBDENUM": "Molibdênio (Molybdenum)",
    "CHROMIUM": "Cromo (Chromium)", "VANADIUM": "Vanádio (Vanadium)",
}

# Metais preciosos — preço por troy oz; industriais — preço por tonelada métrica
_PRECIOUS = {"XAU", "XAG", "XPT", "XPD", "XRH"}
_UNIT_LABEL: dict[str, str] = {s: "USD/troy oz" for s in _PRECIOUS}

# Classificação estratégica para contexto minerário
_ESTRATEGICOS = {
    "XAU", "XPT", "XPD", "XRH",
    "NIOBIUM", "LITHIUM", "COBALT", "TITANIUM", "TUNGSTEN",
    "VANADIUM", "MOLYBDENUM", "CHROMIUM", "MANGANESE",
}

HTTP_TIMEOUT = 10.0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalizar(nome: str) -> str:
    """Remove acentos simples e lowercase para lookup no _SYMBOL_MAP."""
    import unicodedata
    return unicodedata.normalize("NFD", nome.lower().strip()).encode("ascii", "ignore").decode()


def resolver_simbolo(mineral: str) -> str | None:
    """
    Resolve um nome de mineral em PT-BR ou EN para o símbolo Metals-API.
    Retorna None se não encontrado.
    """
    key = _normalizar(mineral)
    return _SYMBOL_MAP.get(key)


def _extrair_preco_usd(rates: dict, simbolo: str) -> float | None:
    """
    Extrai o preço em USD de rates Metals-API.

    A API retorna dois formatos quando base=USD:
      - rates["USD{SYMBOL}"] = preço direto (apenas /latest)
      - rates["{SYMBOL}"]    = taxa inversa → price = 1/rate

    Prioriza o campo direto (mais preciso), cai no inverso se ausente.
    """
    direct_key = f"USD{simbolo}"
    if direct_key in rates:
        v = rates[direct_key]
        return float(v) if v else None

    inv_key = simbolo
    if inv_key in rates:
        v = rates[inv_key]
        if v and float(v) != 0:
            return 1.0 / float(v)

    return None


def _unidade(simbolo: str) -> str:
    return _UNIT_LABEL.get(simbolo, "USD/t")


# ─────────────────────────────────────────────────────────────────────────────
# API caller
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_latest(
    api_key: str,
    base_url: str,
    simbolo: str,
) -> dict[str, Any]:
    """Chama /latest para um símbolo, base=USD. Suporta metalapi.com."""
    url = f"{base_url.rstrip('/')}/latest"
    params = {
        "api_key": api_key,
        "base": "USD",
        "symbols": simbolo,
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def _fetch_historical(
    api_key: str,
    base_url: str,
    simbolo: str,
    data: str,  # YYYY-MM-DD
) -> dict[str, Any]:
    """Chama /<data> para preço histórico, base=USD. Suporta metalapi.com."""
    url = f"{base_url.rstrip('/')}/{data}"
    params = {
        "api_key": api_key,
        "base": "USD",
        "symbols": simbolo,
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def executar_consultar_preco_mineral(
    api_key: str,
    base_url: str,
    redis_cache: Any,        # RedisCache instance (can be None)
    cache_ttl: int,
    mineral: str,
    incluir_variacao: bool = True,
    periodo_variacao_dias: int = 7,
) -> dict[str, Any]:
    """
    Consulta o preço atual de um mineral na Metals-API, com variação opcional
    em relação a N dias atrás.

    Args:
        api_key:                 Chave Metals-API
        base_url:                URL base da API
        redis_cache:             Instância RedisCache para TTL curto (5 min)
        cache_ttl:               TTL em segundos
        mineral:                 Nome do mineral em PT-BR ou EN (ex: "ouro", "cobre")
        incluir_variacao:        Se True, busca cotação histórica para calcular variação
        periodo_variacao_dias:   Número de dias para comparação (default: 7)

    Returns:
        {
          "mineral": "Ouro (Gold)",
          "simbolo": "XAU",
          "preco_atual": {...},
          "variacao_7d": {...},   # opcional
          "estrategico": True,
          "contexto_minerario": "..."
        }
    """
    simbolo = resolver_simbolo(mineral)
    if not simbolo:
        # Sugere alternativas
        sugestoes = sorted({
            v for k, v in _SYMBOL_MAP.items()
            if mineral.lower()[:3] in k
        })[:5]
        return {
            "erro": (
                f"Mineral '{mineral}' não reconhecido. "
                f"Exemplos válidos: ouro, prata, platina, cobre, ferro, níquel, "
                f"nióbio, lítio, cobalto, alumínio, zinco."
            ),
            "sugestoes_simbolo": sugestoes,
        }

    if not api_key or api_key in ("SUA_CHAVE_METALSAPI", ""):
        return {
            "erro": "METALS_API_KEY não configurada. Defina a variável no arquivo .env.",
            "configuracao": "Obtenha uma chave gratuita em https://metalapi.com",
        }

    cache_key = f"metals:price:{simbolo}:USD"

    # ── Cache hit ──
    cached = None
    if redis_cache and redis_cache.available:
        try:
            cached = await redis_cache.get(cache_key)
        except Exception:
            pass

    if cached:
        logger.info("consultar_preco_mineral: Cache HIT %s", cache_key)
        return {"sucesso": True, "fonte": "cache", **cached}

    # ── Fetch current price ──
    try:
        data_hoje = date.today()
        resp_latest = await _fetch_latest(api_key, base_url, simbolo)
    except httpx.TimeoutException:
        return {"erro": "Timeout ao consultar Metals-API (>10s)."}
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200]
        return {"erro": f"Metals-API HTTP {e.response.status_code}: {body}"}
    except Exception as e:
        logger.error("consultar_preco_mineral: fetch latest error: %s", e)
        return {"erro": str(e)}

    if not resp_latest.get("success"):
        err = resp_latest.get("error", {})
        return {
            "erro": f"Metals-API retornou erro: {err.get('info', err)}",
            "codigo": err.get("code"),
        }

    rates_latest = resp_latest.get("rates", {})
    preco_atual_usd = _extrair_preco_usd(rates_latest, simbolo)

    if preco_atual_usd is None:
        return {
            "erro": (
                f"Símbolo '{simbolo}' não disponível na sua assinatura Metals-API "
                "(muitos metais industriais/estratégicos exigem plano pago)."
            ),
            "alternativa": (
                "Use ``jazidas__consultar_mercado_mineral`` com "
                "``substancia_ou_ncm`` = nome do mineral ou NCM (ex.: ``'lítio'``, "
                "``'carbonato'``) e ``fluxo`` import/export para séries de valor FOB "
                "e volume do Brasil (ComexStat indexado)."
            ),
        }

    unidade = _unidade(simbolo)
    ts = resp_latest.get("timestamp", 0)
    dt_atualizacao = (
        datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")
        if ts else str(data_hoje)
    )

    preco_atual = {
        "valor_usd":       round(preco_atual_usd, 4),
        "unidade":         unidade,
        "data":            resp_latest.get("date", str(data_hoje)),
        "atualizado_em":   dt_atualizacao,
    }

    # ── Variação histórica ──
    variacao: dict[str, Any] | None = None
    if incluir_variacao:
        data_ant = (data_hoje - timedelta(days=periodo_variacao_dias)).strftime("%Y-%m-%d")
        try:
            resp_hist = await _fetch_historical(api_key, base_url, simbolo, data_ant)
            if resp_hist.get("success"):
                rates_hist = resp_hist.get("rates", {})
                preco_ant = _extrair_preco_usd(rates_hist, simbolo)
                if preco_ant and preco_ant != 0:
                    delta = preco_atual_usd - preco_ant
                    delta_pct = (delta / preco_ant) * 100
                    variacao = {
                        "periodo_dias":     periodo_variacao_dias,
                        "data_referencia":  data_ant,
                        "preco_referencia": round(preco_ant, 4),
                        "variacao_absoluta": round(delta, 4),
                        "variacao_pct":     round(delta_pct, 2),
                        "tendencia":        "alta" if delta > 0 else ("queda" if delta < 0 else "estavel"),
                        "emoji":            "📈" if delta > 0 else ("📉" if delta < 0 else "➡️"),
                    }
        except Exception as e:
            logger.warning("consultar_preco_mineral: variação histórica falhou: %s", e)
            # Não falha — retorna sem variação

    # ── Contexto minerário ──
    _CONTEXTOS = {
        "XAU": (
            "Reserva de valor global e benchmark da mineração preciosa. "
            "O Brasil é o 12º maior produtor mundial (MT-MG). "
            "CFEM alíquota: 1,5%."
        ),
        "XAG": (
            "Uso dual: industrial (eletrônica, solar) e precioso. "
            "Brasil importa a maior parte da prata consumida. "
            "CFEM alíquota: 2%."
        ),
        "XPT": "Catalisadores automotivos + joalheria. Produção global dominada por ZAF e RUS.",
        "XPD": "Alta demanda de catalisadores automotivos (diesel→gasolina). Escassez estrutural.",
        "ALU": (
            "Bauxita é a matéria-prima; Brasil é o 3º maior produtor de bauxita. "
            "Preço influenciado por energia elétrica (smelting)."
        ),
        "COPPER": (
            "Barômetro da economia global ('Dr. Copper'). "
            "Alta demanda em veículos elétricos e energia renovável. "
            "Brasil tem depósitos em PA, GO e BA."
        ),
        "IRON": (
            "Brasil é o 2º maior exportador mundial (Vale, CBMM). "
            "Preço sensível à demanda siderúrgica chinesa."
        ),
        "NICKEL": (
            "Essencial para baterias de veículos elétricos (NMC). "
            "Indonésia domina produção; Brasil tem reservas em GO/PA."
        ),
        "NIOBIUM": (
            "Brasil detém ~94% das reservas mundiais (CBMM — Araxá/MG). "
            "Usado em ligas de aço especial. Preço de referência LME."
        ),
        "LITHIUM": (
            "Metal da transição energética (baterias Li-ion). "
            "Brasil tem reservas significativas no 'Lithium Valley' (MG). "
            "Demanda crescente de EVs."
        ),
        "COBALT": (
            "Co-produto do cobre/níquel. Essencial em cátodos de baterias. "
            "RDC domina produção; Brasil tem ocorrências em GO/PA."
        ),
        "TIN": "Brasil é um dos maiores produtores (Rondônia). Usado em soldagem e eletrônica.",
        "ZINC":   "Galvanização + ligas. Brasil produz em MG e RS.",
        "MANGANESE": "Liga de aço. Brasil (PA, MG) é grande produtor e exportador.",
        "TITANIUM": "Aeroespacial + pigmentos (TiO₂). Ilmenita/rutilo nas praias do litoral.",
    }

    contexto = _CONTEXTOS.get(simbolo, f"Metal de referência no mercado de commodities ({simbolo}).")

    rate_limit = resp_latest.get("rate_limit") or {}
    resultado: dict[str, Any] = {
        "mineral":        _SYMBOL_NAMES.get(simbolo, simbolo),
        "simbolo":        simbolo,
        "estrategico":    simbolo in _ESTRATEGICOS,
        "preco_atual":    preco_atual,
        "contexto_minerario": contexto,
    }
    if variacao is not None:
        resultado[f"variacao_{periodo_variacao_dias}d"] = variacao
    if rate_limit:
        resultado["quota"] = {
            "plano":       rate_limit.get("plan"),
            "utilizadas":  rate_limit.get("used"),
            "restantes":   rate_limit.get("remaining"),
            "limite":      rate_limit.get("limit"),
            "reset_em":    rate_limit.get("reset_date"),
        }

    # ── Armazena em cache ──
    if redis_cache and redis_cache.available:
        try:
            await redis_cache.set(cache_key, resultado, ttl=cache_ttl)
        except Exception:
            pass

    logger.info(
        "consultar_preco_mineral: %s=%s USD/unit variacao=%s",
        simbolo, preco_atual_usd,
        variacao.get("variacao_pct") if variacao else "N/A",
    )

    return {"sucesso": True, "fonte": "metals-api", **resultado}


def listar_minerais_suportados() -> list[dict[str, str]]:
    """Retorna lista de minerais disponíveis para a tool."""
    seen: set[str] = set()
    result = []
    for simbolo, nome in _SYMBOL_NAMES.items():
        if simbolo not in seen:
            seen.add(simbolo)
            # Nomes em PT-BR para o símbolo
            nomes_pt = [k for k, v in _SYMBOL_MAP.items() if v == simbolo
                        and not k.startswith("x") and len(k) > 2][:3]
            result.append({
                "simbolo": simbolo,
                "nome": nome,
                "nomes_pt": ", ".join(nomes_pt),
                "estrategico": "✓" if simbolo in _ESTRATEGICOS else "",
            })
    return result
