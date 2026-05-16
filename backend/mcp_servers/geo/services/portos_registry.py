"""
Portos Registry — In-memory lookup dos portos organizados públicos do Brasil
==============================================================================

Por que NÃO está no OpenSearch (decisão de Fase 1):
    O catálogo tem ~35 itens (lista oficial do MTransp). Mantê-los em RAM
    custa <100 KB e elimina round-trip de rede; um lookup direto leva µs vs
    ~5-15 ms via OpenSearch. Quando precisarmos de geo_shape contains ou
    fuzzy/k-NN sobre polígonos completos, migramos para o índice ``mr_portos_v001``
    sem alterar a assinatura pública desta classe (ver
    ``docs/SPEC_PORTOS_OPENSEARCH.md``).

Fonte de dados:
    ``backend/data/portos_brasil.csv`` — separador ';' (mesmo padrão dos
    CSVs do dataset CKAN do MTransp). Schema documentado no header do CSV.

Uso típico:

    >>> registry = PortosRegistry.load_default()
    >>> p = registry.find_by_query("aratu")
    >>> p.codigo, p.uf, p.latitude, p.longitude
    ('ATU', 'BA', -12.79667, -38.4925)

    >>> registry.find_by_query("Porto de Mucuripe")  # alias
    Porto(codigo='FOR', nome='Porto de Fortaleza (Mucuripe)', ...)

    >>> registry.nearest(-23.9, -46.3, k=3)
    [Porto(PSV), Porto(SBS), Porto(IGI)]

Concorrência:
    Estrutura imutável após o ``load()``. Pode ser compartilhada entre
    coroutines sem locks.
"""

from __future__ import annotations

import csv
import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("mcp.geo.portos_registry")


# Caminho default — resolve a partir da raiz do backend para que o módulo
# funcione tanto em ``python -m mcp_servers.geo.server`` quanto em testes.
_DEFAULT_CSV_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "portos_brasil.csv"
)

# Limiar de score (0–100) para o fuzzy match aceitar um candidato.
# Valores baixos pegam ruído; valores altos (>90) rejeitam variações
# legítimas como "Porto de Aratu" vs "Porto de Aratu-Candeias".
_FUZZY_SCORE_CUTOFF = 75.0

# Tokens que NÃO carregam informação para a busca por nome — removemos antes
# de comparar para que "Porto de Aratu" e "Aratu" deem match exato.
_STOPWORDS = frozenset({
    "porto", "portos", "do", "da", "de", "dos", "das",
    "complexo", "terminal", "terminais", "cais",
})


def _strip_accents(text: str) -> str:
    """Remove acentos e converte para ASCII puro (lowercase)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _normalize_name(text: str) -> str:
    """
    Normaliza um nome de porto para matching robusto.

    Pipeline:
        1. NFKD + strip accents + lowercase   ("São Sebastião" → "sao sebastiao")
        2. Substitui hífens, barras e pontuação por espaço
        3. Tokeniza, remove stopwords ("porto de", "complexo")
        4. Sort alfabético dos tokens (para que "Aratu-Candeias" e
           "Candeias Aratu" produzam a mesma chave)
        5. Junta com espaço
    """
    base = _strip_accents(text)
    base = re.sub(r"[\-/(),.]+", " ", base)
    tokens = [t for t in base.split() if t and t not in _STOPWORDS]
    tokens.sort()
    return " ".join(tokens)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância de grande círculo (km). Mesma fórmula usada em geo/tools.py."""
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


@dataclass(frozen=True, slots=True)
class Porto:
    """Um porto organizado público brasileiro."""

    codigo: str
    nome: str
    uf: str
    municipio: str
    latitude: float
    longitude: float
    endereco: str
    autoridade_portuaria: str
    cargas_principais: tuple[str, ...]
    aliases: tuple[str, ...]
    validacao_pendente: bool = False

    def to_dict(self) -> dict:
        """Serialização compatível com o retorno das tools MCP."""
        return {
            "codigo": self.codigo,
            "nome": self.nome,
            "uf": self.uf,
            "municipio": self.municipio,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "endereco": self.endereco,
            "autoridade_portuaria": self.autoridade_portuaria,
            "cargas_principais": list(self.cargas_principais),
            "aliases": list(self.aliases),
            "validacao_pendente": self.validacao_pendente,
        }


def _split_csv_field(raw: str) -> tuple[str, ...]:
    """
    Os campos ``cargas_principais`` e ``aliases`` são CSVs internos
    separados por vírgula (porque o separador externo já é ';').
    Trim em cada item e descarta vazios.
    """
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _parse_bool(raw: str) -> bool:
    """Aceita 'true', 'True', '1', 'sim' como verdadeiro; o resto é falso."""
    return raw.strip().lower() in {"true", "1", "sim", "yes"}


class PortosRegistry:
    """
    Cache em memória de portos com índices invertidos para lookup O(1)
    + fuzzy fallback quando o usuário escreve "Porto de Mucuripe" e o
    canônico é "Porto de Fortaleza (Mucuripe)".
    """

    __slots__ = (
        "_portos",
        "_by_codigo",
        "_by_normalized",
        "_by_uf",
        "_normalized_to_codigo",
    )

    def __init__(self, portos: Iterable[Porto]) -> None:
        self._portos: tuple[Porto, ...] = tuple(portos)
        self._by_codigo: dict[str, Porto] = {}
        self._by_normalized: dict[str, Porto] = {}
        self._by_uf: dict[str, list[Porto]] = {}
        # Mantemos o normalized → codigo separado pra rapidfuzz/difflib
        # buscar só sobre as chaves (mais rápido que iterar Porto).
        self._normalized_to_codigo: dict[str, str] = {}
        self._build_indexes()

    # ── Construção ────────────────────────────────────────────────────────

    def _build_indexes(self) -> None:
        """Popula os índices invertidos. Chamado uma vez no __init__."""
        for p in self._portos:
            if p.codigo in self._by_codigo:
                logger.warning(
                    "PortosRegistry: codigo duplicado '%s' (porto '%s' "
                    "vai sobrescrever entrada anterior)", p.codigo, p.nome,
                )
            self._by_codigo[p.codigo] = p

            # Indexa o nome canônico + município + cada alias.
            keys = {p.nome, p.municipio}
            keys.update(p.aliases)
            for k in keys:
                normalized = _normalize_name(k)
                if not normalized:
                    continue
                # Primeira ocorrência ganha — evita "salvador" (município)
                # sobrescrever um eventual alias de outro porto.
                if normalized not in self._by_normalized:
                    self._by_normalized[normalized] = p
                    self._normalized_to_codigo[normalized] = p.codigo

            self._by_uf.setdefault(p.uf.upper(), []).append(p)

        logger.info(
            "PortosRegistry: %d portos carregados, %d chaves normalizadas, "
            "%d UFs",
            len(self._portos),
            len(self._by_normalized),
            len(self._by_uf),
        )

    # ── Loaders ───────────────────────────────────────────────────────────

    @classmethod
    def from_csv(cls, csv_path: Path | str) -> PortosRegistry:
        """Carrega um CSV no formato de ``backend/data/portos_brasil.csv``."""
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"Portos CSV não encontrado: {path}")

        portos: list[Porto] = []
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(
                (line for line in f if not line.lstrip().startswith("#")),
                delimiter=";",
            )
            for row in reader:
                try:
                    porto = Porto(
                        codigo=row["codigo"].strip(),
                        nome=row["nome"].strip(),
                        uf=row["uf"].strip().upper(),
                        municipio=row["municipio"].strip(),
                        latitude=float(row["latitude"]),
                        longitude=float(row["longitude"]),
                        endereco=row["endereco"].strip(),
                        autoridade_portuaria=row["autoridade_portuaria"].strip(),
                        cargas_principais=_split_csv_field(
                            row.get("cargas_principais", "")
                        ),
                        aliases=_split_csv_field(row.get("aliases", "")),
                        validacao_pendente=_parse_bool(
                            row.get("validacao_pendente", "false")
                        ),
                    )
                except (KeyError, ValueError) as e:
                    logger.error(
                        "PortosRegistry: linha inválida em %s: %s — row=%r",
                        path, e, row,
                    )
                    continue
                portos.append(porto)

        return cls(portos)

    @classmethod
    def load_default(cls) -> PortosRegistry:
        """Carrega o CSV default em ``backend/data/portos_brasil.csv``."""
        return cls.from_csv(_DEFAULT_CSV_PATH)

    # ── Lookups ───────────────────────────────────────────────────────────

    def all(self) -> tuple[Porto, ...]:
        """Retorna todos os portos (snapshot imutável)."""
        return self._portos

    def by_codigo(self, codigo: str) -> Porto | None:
        return self._by_codigo.get(codigo.strip().upper())

    def by_uf(self, uf: str) -> list[Porto]:
        """Lista de portos da UF, ordenada por nome canônico."""
        items = list(self._by_uf.get(uf.strip().upper(), []))
        items.sort(key=lambda p: p.nome)
        return items

    def find_by_query(
        self,
        termo: str,
        uf: str | None = None,
        score_cutoff: float = _FUZZY_SCORE_CUTOFF,
    ) -> Porto | None:
        """
        Resolve um texto livre como "porto de aratu" ou "Mucuripe" para
        UM porto canônico. Estratégia em 3 camadas:

            1. Match exato após normalização (cobre 95% dos casos).
            2. Substring: o nome normalizado do porto está contido no termo
               (ou vice-versa) — pega "rota até Aratu" sem pontuação.
            3. Fuzzy via SequenceMatcher — pega typos e variações como
               "Mucurupe" → "Mucuripe".

        Retorna None se nenhum candidato passar do score_cutoff.
        Se ``uf`` for fornecido, filtra ANTES do fuzzy (não tem fallback).
        """
        if not termo or not termo.strip():
            return None

        normalized = _normalize_name(termo)
        if not normalized:
            return None

        candidatos: list[Porto] = (
            self.by_uf(uf) if uf else list(self._portos)
        )
        if not candidatos:
            return None

        # Camada 1: match exato sobre as chaves indexadas.
        exact = self._by_normalized.get(normalized)
        if exact and (uf is None or exact.uf == uf.strip().upper()):
            return exact

        # Camada 2: substring. Iteramos sobre as chaves do índice porque
        # cada porto pode ter várias (nome + município + aliases).
        substring_hits: list[tuple[float, Porto]] = []
        for key, codigo in self._normalized_to_codigo.items():
            porto = self._by_codigo[codigo]
            if uf and porto.uf != uf.strip().upper():
                continue
            if key == normalized:
                return porto  # belt-and-suspenders contra dessincronização
            if key in normalized or normalized in key:
                # Score proporcional ao quanto da chave foi casado.
                # Reduz "santos" pegando "porto velho" via "porto" sozinho.
                ratio = (
                    len(key) / max(len(normalized), 1)
                    if len(key) <= len(normalized)
                    else len(normalized) / max(len(key), 1)
                )
                substring_hits.append((ratio * 100, porto))

        if substring_hits:
            substring_hits.sort(key=lambda x: x[0], reverse=True)
            best_score, best_porto = substring_hits[0]
            if best_score >= score_cutoff:
                return best_porto

        # Camada 3: fuzzy via SequenceMatcher.
        best: tuple[float, Porto] | None = None
        for key, codigo in self._normalized_to_codigo.items():
            porto = self._by_codigo[codigo]
            if uf and porto.uf != uf.strip().upper():
                continue
            ratio = SequenceMatcher(None, normalized, key).ratio() * 100
            if best is None or ratio > best[0]:
                best = (ratio, porto)

        if best is not None and best[0] >= score_cutoff:
            return best[1]
        return None

    def nearest(
        self,
        latitude: float,
        longitude: float,
        k: int = 5,
        uf: str | None = None,
    ) -> list[tuple[Porto, float]]:
        """
        Retorna os k portos mais próximos do ponto, ordenados por distância
        (km, haversine). Brute-force sobre 35 itens ≪ 50 µs — não justifica
        kd-tree.
        """
        candidatos = self.by_uf(uf) if uf else list(self._portos)
        ranked = [
            (p, _haversine_km(latitude, longitude, p.latitude, p.longitude))
            for p in candidatos
        ]
        ranked.sort(key=lambda x: x[1])
        return ranked[: max(1, k)]


def iter_resolve_trials_from_endereco(endereco: str) -> list[tuple[str, str | None]]:
    """
    Gera tentativas (termo, uf) para resolver texto livre como porto público.

    Usado por ``resolve_endereco_if_public_port`` (CSV) e por
    ``queries.portos.resolve_endereco_via_portos_index`` (OpenSearch).
    """
    raw = (endereco or "").strip()
    if len(raw) < 4:
        return []

    uf_hint: str | None = None
    clean = raw
    m = re.search(r"\(\s*([A-Z]{2})\s*\)\s*$", raw, re.I)
    if m:
        uf_hint = m.group(1).upper()
        clean = raw[: m.start()].strip()
    else:
        m2 = re.search(r"[,/]\s*([A-Z]{2})\s*$", raw, re.I)
        if m2 and len(m2.group(1)) == 2:
            uf_hint = m2.group(1).upper()
            clean = raw[: m2.start()].strip()

    trials: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()

    def _add(term: str, uf: str | None) -> None:
        t = term.strip()
        if len(t) < 2:
            return
        key = (t.lower(), uf or "")
        if key in seen:
            return
        seen.add(key)
        trials.append((t, uf))

    _add(clean, uf_hint)
    if uf_hint:
        _add(clean, None)
    _add(raw, uf_hint)
    if uf_hint and raw.strip() != clean.strip():
        _add(raw, None)
    return trials


def resolve_endereco_if_public_port(endereco: str) -> dict[str, Any] | None:
    """
    Se ``endereco`` corresponde a um porto do CSV MTransp, devolve coordenadas
    oficiais + texto resolvido (mesmo formato que ``_resolve_endpoint`` usa).

    Usado por ``calcular_rota`` / ``comparar_rotas`` / ``plotar_endereco`` **antes**
    do geocode Azure, evitando homônimos (ex.: "Vila do Conde" → RS vs porto no PA).
    """
    reg = get_default_registry()
    if not reg.all():
        return None

    for term, uf in iter_resolve_trials_from_endereco(endereco):
        porto = reg.find_by_query(term, uf=uf)
        if porto is None:
            continue
        return {
            "lat": float(porto.latitude),
            "lon": float(porto.longitude),
            "endereco_consultado": endereco,
            "endereco_resolvido": f"{porto.nome} — {porto.municipio}/{porto.uf}",
            "fonte": "portos_registry",
            "detalhes": {
                "municipio": f"{porto.municipio}/{porto.uf}",
            },
        }
    return None


# ── Instância singleton compartilhada ────────────────────────────────────────
#
# O registry é imutável e barato (RAM<100KB), então mantemos uma instância
# global no módulo. As tools (geo__buscar_porto) e os guardrails do LangGraph
# acessam por ``get_default_registry()`` — lazy, evita carregar o CSV em
# tempo de import (importante para os testes que mockam o sistema de arquivos).

_DEFAULT_INSTANCE: PortosRegistry | None = None


def get_default_registry() -> PortosRegistry:
    """
    Retorna a instância global do registry, carregando o CSV default
    na primeira chamada. Em caso de erro de leitura, loga e retorna um
    registry vazio (gracefully degrade — as tools voltam ao fallback Azure).
    """
    global _DEFAULT_INSTANCE
    if _DEFAULT_INSTANCE is None:
        try:
            _DEFAULT_INSTANCE = PortosRegistry.load_default()
        except Exception as e:
            logger.error(
                "PortosRegistry: falha ao carregar CSV default (%s) — "
                "retornando registry vazio. Tools de porto NÃO terão "
                "fallback para o registry; comparar_rotas/calcular_rota "
                "seguirão chamando Azure Maps direto.", e,
            )
            _DEFAULT_INSTANCE = PortosRegistry(())
    return _DEFAULT_INSTANCE


def reset_default_registry() -> None:
    """Limpa a instância global. Útil em testes que recarregam o CSV."""
    global _DEFAULT_INSTANCE
    _DEFAULT_INSTANCE = None
