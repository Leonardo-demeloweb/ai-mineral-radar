"""
Planeamento dinâmico de rotas a partir da mensagem do utilizador
================================================================

Extrai origem/destino, processos ANM citados e modo de viagem **da pergunta
actual** — sem exemplos fixos no system prompt. O resultado alimenta:

- bloco injectado no system message do agente (``route_execution_hint``)
- ``session_state`` para guardrails em ``tools.py`` (bloquear processos inventados)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp_servers.geo.services.place_resolver import enrich_route_query

# Números de processo ANM na mensagem (com ou sem pontos)
_PROCESSO_FIND_RE = re.compile(
    r"\b(\d{1,3}\.?\d{1,6}\s*/\s*\d{4})\b",
    re.IGNORECASE,
)

_ROUTE_INTENT_RE = re.compile(
    r"\b("
    r"rota\s+(?:da|de|do|entre|a\s+partir)|"
    r"trajeto\s+(?:de|da|do|entre)|"
    r"dist[âa]ncia\s+(?:da|de|do|entre|at[éae]\s|do\s)|"
    r"quantos?\s+km|"
    r"quanto\s+tempo\s+(?:de|leva|para)|"
    r"caminho\s+(?:de|da|do)|"
    r"como\s+chegar|"
    r"calcule?\s+(?:a\s+)?rota|"
    r"plot(?:e|ar)\s+(?:a\s+)?rota|"
    r"compare?\s+.*\b(?:rotas?|portos?|destinos?)"
    r")\b",
    re.IGNORECASE,
)

_TRUCK_MODE_RE = re.compile(
    r"\b(caminh[aã]o|truck|pesad[oa]s?|rodovi[aá]ri[oa]s?|carreta)\b",
    re.IGNORECASE,
)

# "da X até Y" — prioridade de "da/desde" sobre "de" solto (evita "rota de caminhão de…")
_DA_ATE_RE = re.compile(
    r"\b(?:da|desde|partindo\s+de|saindo\s+de)\s+"
    r"(?P<origem>.+?)\s+"
    r"(?:até|ate|para)\s+"
    r"(?:o\s+|a\s+)?"
    r"(?P<destino>.+?)"
    r"(?:\.|,|\?|$|\s+em\s+|\s+no\s+modo)",
    re.IGNORECASE,
)

# "de X para Y" / "do X até Y" — sem "de" após "rota de caminhão" etc.
_DE_ATE_RE = re.compile(
    r"(?<!\brota\s)(?<!\btrajeto\s)"
    r"\b(?:de|do)\s+"
    r"(?P<origem>.+?)\s+"
    r"(?:até|ate|para)\s+"
    r"(?:o\s+|a\s+)?"
    r"(?P<destino>.+?)"
    r"(?:\.|,|\?|$|\s+em\s+|\s+no\s+modo)",
    re.IGNORECASE,
)

_TRAVEL_MODE_PREFIX_RE = re.compile(
    r"^(?:caminh[aã]o|carro|truck|pesad[oa]s?|rodovi[aá]ri[oa]s?|carreta)\s+",
    re.IGNORECASE,
)

_ATE_DESTINO_RE = re.compile(
    r"(?:até|ate|para)\s+(?:o\s+|a\s+)?(?P<destino>.+?)(?:\.|,|\?|$|\s+em\s+|\s+no\s+modo)",
    re.IGNORECASE,
)

_NAME_TOKEN = (
    r"[A-ZÁÂÃÀÉÊÍÓÔÕÚÇ][\wÁÂÃÀÉÊÍÓÔÕÚÇáâãàéêíóôõúç\.\-/]+"
    r"(?:\s+(?:de|do|da|dos|das)\s+"
    r"[A-ZÁÂÃÀÉÊÍÓÔÕÚÇ][\wÁÂÃÀÉÊÍÓÔÕÚÇáâãàéêíóôõúç\.\-/]+)*"
)

_DESTINATION_LIST_RE = re.compile(
    rf"(?:{_NAME_TOKEN}\s*,\s*){{2,}}"
    rf"{_NAME_TOKEN}"
    rf"(?:\s+(?:e|ou)\s+{_NAME_TOKEN})?",
)


def _normalize_processo(numero: str) -> str:
    parts = numero.strip().split("/")
    if len(parts) == 2:
        return f"{parts[0].replace('.', '')}/{parts[1]}"
    return numero.strip().replace(".", "")


def extract_processos_from_text(text: str) -> list[str]:
    """Todos os processos ANM explicitamente escritos na mensagem."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _PROCESSO_FIND_RE.finditer(text):
        raw = m.group(1).strip()
        norm = _normalize_processo(raw)
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def user_asks_for_route(text: str) -> bool:
    if not text:
        return False
    return bool(_ROUTE_INTENT_RE.search(text))


def infer_travel_mode(text: str) -> str:
    return "truck" if _TRUCK_MODE_RE.search(text or "") else "car"


def _clean_place_fragment(s: str) -> str:
    t = (s or "").strip()
    t = re.sub(
        r"^(?:rota\s+(?:de\s+)?caminh[aã]o\s+(?:da|de|do)\s+)",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = _TRAVEL_MODE_PREFIX_RE.sub("", t)
    t = re.sub(
        r"^(?:da|de|do|dos|das)\s+",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\s+", " ", t).strip(" ,;.")
    return t


def _pair_from_match(m: re.Match[str]) -> tuple[str | None, str | None]:
    origem = _clean_place_fragment(m.group("origem"))
    destino = _clean_place_fragment(m.group("destino"))
    if origem and _PROCESSO_FIND_RE.search(origem):
        origem = None
    if destino and _PROCESSO_FIND_RE.search(destino):
        destino = None
    if not origem or not destino:
        return None, None
    if _TRAVEL_MODE_PREFIX_RE.match(origem):
        return None, None
    return origem, destino


def extract_origin_destination(text: str) -> tuple[str | None, str | None]:
    """Origem e destino nominais (não processo) extraídos de padrões PT."""
    if not text:
        return None, None
    m = _DA_ATE_RE.search(text)
    if m:
        pair = _pair_from_match(m)
        if pair[0]:
            return pair
    for m in _DE_ATE_RE.finditer(text):
        pair = _pair_from_match(m)
        if pair[0]:
            return pair
    return None, None


def extract_destination_only(text: str) -> str | None:
    """Destino após 'até/para' quando a origem é processo ou não foi parseada."""
    if not text:
        return None
    m = _ATE_DESTINO_RE.search(text)
    if not m:
        return None
    destino = _clean_place_fragment(m.group("destino"))
    if not destino or _PROCESSO_FIND_RE.search(destino):
        return None
    return destino


def extract_named_destination_list(text: str) -> list[str]:
    """Lista 'A, B e C' com ≥3 itens."""
    if not text:
        return []
    m = _DESTINATION_LIST_RE.search(text)
    if not m:
        return []
    chunk = m.group(0)
    items = re.split(
        r",|\s+e\s+(?=[A-ZÁÂÃÀÉÊÍÓÔÕÚÇ])|\s+ou\s+(?=[A-ZÁÂÃÀÉÊÍÓÔÕÚÇ])",
        chunk,
    )
    cleaned = [_clean_place_fragment(it) for it in items if it.strip()]
    return [c for c in cleaned if c and len(c) > 1]


RouteStrategy = Literal[
    "none",
    "calcular_rota_enderecos",
    "calcular_rota_processo_destino",
    "comparar_rotas",
]


@dataclass
class RouteExecutionPlan:
    """Plano derivado só da mensagem do utilizador neste turno."""

    is_route_request: bool = False
    strategy: RouteStrategy = "none"
    travel_mode: str = "car"
    processos_citados: list[str] = field(default_factory=list)
    origem_endereco: str | None = None
    destino_endereco: str | None = None
    origem_processo: str | None = None
    destinos_comparacao: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_route_request": self.is_route_request,
            "strategy": self.strategy,
            "travel_mode": self.travel_mode,
            "processos_citados": self.processos_citados,
            "origem_endereco": self.origem_endereco,
            "destino_endereco": self.destino_endereco,
            "origem_processo": self.origem_processo,
            "destinos_comparacao": self.destinos_comparacao,
        }


def analyze_route_request(user_message: str) -> RouteExecutionPlan:
    """
    Analisa a pergunta e devolve plano executável (valores reais da mensagem).
    """
    text = (user_message or "").strip()
    plan = RouteExecutionPlan(
        processos_citados=extract_processos_from_text(text),
        travel_mode=infer_travel_mode(text),
    )

    if not user_asks_for_route(text):
        return plan

    plan.is_route_request = True
    origem, destino = extract_origin_destination(text)
    if not destino:
        destino = extract_destination_only(text)
    destinos_lista = extract_named_destination_list(text)

    if destinos_lista and len(destinos_lista) >= 2:
        plan.strategy = "comparar_rotas"
        plan.destinos_comparacao = destinos_lista
        if plan.processos_citados:
            plan.origem_processo = plan.processos_citados[0]
        elif origem:
            plan.origem_endereco = enrich_route_query(origem, text)
        return plan

    if len(plan.processos_citados) == 1 and destino and not origem:
        plan.strategy = "calcular_rota_processo_destino"
        plan.origem_processo = plan.processos_citados[0]
        plan.destino_endereco = enrich_route_query(destino, text)
        return plan

    if origem and destino:
        plan.strategy = "calcular_rota_enderecos"
        plan.origem_endereco = enrich_route_query(origem, text)
        plan.destino_endereco = enrich_route_query(destino, text)
        return plan

    if len(plan.processos_citados) >= 2:
        plan.strategy = "comparar_rotas"
        return plan

    if plan.processos_citados and destino:
        plan.strategy = "calcular_rota_processo_destino"
        plan.origem_processo = plan.processos_citados[0]
        plan.destino_endereco = enrich_route_query(destino, text)
        return plan

    # Pedido de rota sem extrair pares — agente geocodifica a partir do texto literal
    plan.strategy = "calcular_rota_enderecos"
    return plan


def format_route_execution_hint(plan: RouteExecutionPlan) -> str:
    """
    Bloco injectado no system prompt — só quando há pedido de rota.
    Conteúdo 100% derivado de ``plan`` (mensagem actual).
    """
    if not plan.is_route_request:
        return ""

    lines = [
        "── Plano de rota (gerado automaticamente desta pergunta) ──",
        f"Modo de viagem inferido: {plan.travel_mode}",
    ]

    if plan.processos_citados:
        lines.append(
            "Processos ANM citados pelo utilizador (únicos permitidos em "
            f"detalhes_processo): {', '.join(plan.processos_citados)}"
        )
    else:
        lines.append(
            "Nenhum número de processo ANM na pergunta — NÃO invente processo; "
            "use origem_endereco/destino_endereco em calcular_rota ou comparar_rotas."
        )

    if plan.strategy == "comparar_rotas":
        lines.append("Ação recomendada: geo__comparar_rotas (1 chamada batch).")
        if plan.origem_processo:
            lines.append(
                f"  1) jazidas__detalhes_processo(ds_processo=\"{plan.origem_processo}\") "
                "→ origem_lat/origem_lon"
            )
        elif plan.origem_endereco:
            lines.append(
                f"  Origem (endereço): \"{plan.origem_endereco}\""
            )
        if plan.destinos_comparacao:
            for i, d in enumerate(plan.destinos_comparacao, 1):
                lines.append(f"  Destino {i}: \"{d}\"")
    elif plan.strategy == "calcular_rota_processo_destino":
        lines.append("Ação recomendada: detalhes_processo → calcular_rota.")
        lines.append(
            f"  1) jazidas__detalhes_processo(ds_processo=\"{plan.origem_processo}\")"
        )
        lines.append(
            f"  2) geo__calcular_rota(origem_lat/lon do processo, "
            f"destino_endereco=\"{plan.destino_endereco}\", modo=\"{plan.travel_mode}\")"
        )
    elif plan.strategy == "calcular_rota_enderecos":
        lines.append("Ação recomendada: geo__calcular_rota (geocodifica internamente).")
        if plan.origem_endereco and plan.destino_endereco:
            lines.append(
                f"  origem_endereco=\"{plan.origem_endereco}\", "
                f"destino_endereco=\"{plan.destino_endereco}\", "
                f"modo=\"{plan.travel_mode}\""
            )
        else:
            lines.append(
                "  Extraia origem e destino literalmente do texto da pergunta "
                "e passe em origem_endereco / destino_endereco."
            )

    lines.append(
        "Após a rota, chame geo__plotar_endereco no destino (endereco_resolvido da tool)."
    )
    lines.append("── Fim do plano de rota ──")
    return "\n".join(lines)
