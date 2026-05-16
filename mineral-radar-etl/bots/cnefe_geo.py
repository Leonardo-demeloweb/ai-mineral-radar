"""
CNEFE (Censo 2022) — geo_point para empresas sem API paga.

Fonte: CSVs em
  https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/
  Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/{cod}_{UF}.zip

Cada zip contém um CSV com colunas incluindo CEP, NOM_TIPO_SEGLOGR, NOM_TITULO_SEGLOGR,
NOM_SEGLOGR, NUM_ENDERECO, LATITUDE, LONGITUDE.

Estratégia:
  1. Filtra linhas cujo CEP está no conjunto desejado (streaming, baixa memória).
  2. Por CEP: guarda até N pontos com logradouro normalizado + média (centróide do CEP).
  3. Para cada empresa: melhor similaridade de texto entre logradouro RFB e CNEFE;
     se nenhuma passar do limiar, usa o centróide do CEP.
"""
from __future__ import annotations

import csv
import io
import math
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterator

import httpx

from bots.common.logging import get_logger

log = get_logger(__name__)

# Sigla UF → código IBGE (prefixo do arquivo 15_PA.zip)
SIGLA_PARA_CODIGO: dict[str, str] = {
    "RO": "11",
    "AC": "12",
    "AM": "13",
    "RR": "14",
    "PA": "15",
    "AP": "16",
    "TO": "17",
    "MA": "21",
    "PI": "22",
    "CE": "23",
    "RN": "24",
    "PB": "25",
    "PE": "26",
    "AL": "27",
    "SE": "28",
    "BA": "29",
    "MG": "31",
    "ES": "32",
    "RJ": "33",
    "SP": "35",
    "PR": "41",
    "SC": "42",
    "RS": "43",
    "MS": "50",
    "MT": "51",
    "GO": "52",
    "DF": "53",
}

CNEFE_UF_ZIP_URL = (
    "https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/"
    "Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/{codigo}_{sigla}.zip"
)

# Limiar conservador: logradouros RFB vs CNEFE raramente coincidem literalmente.
_STREET_MATCH_MIN = 0.46
# Evita estourar RAM em CEPs com muitas faces no CNEFE.
_MAX_STREET_SAMPLES_POR_CEP = 400


def normalize_cep(raw: str | None) -> str | None:
    """Somente dígitos, 8 caracteres (padding à esquerda se 7 dígitos)."""
    if not raw:
        return None
    d = "".join(c for c in str(raw) if c.isdigit())
    if not d:
        return None
    if len(d) == 7:
        d = "0" + d
    if len(d) != 8:
        return None
    return d


def _strip_accents_upper(s: str) -> str:
    nk = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nk if not unicodedata.combining(c)).upper()


def normalize_logradouro(s: str | None) -> str:
    if not s:
        return ""
    t = _strip_accents_upper(s)
    for ch in ".,;-/":
        t = t.replace(ch, " ")
    return " ".join(t.split())


def _cnefe_logradouro_row(row: dict[str, str]) -> str:
    tipo = (row.get("NOM_TIPO_SEGLOGR") or "").strip()
    titulo = (row.get("NOM_TITULO_SEGLOGR") or "").strip()
    nome = (row.get("NOM_SEGLOGR") or "").strip()
    return " ".join(p for p in (tipo, titulo, nome) if p).strip()


@dataclass
class _CepAgg:
    sum_lat: float = 0.0
    sum_lon: float = 0.0
    n: int = 0
    samples: list[tuple[float, float, str]] = field(default_factory=list)

    def add(self, lat: float, lon: float, street_norm: str) -> None:
        self.sum_lat += lat
        self.sum_lon += lon
        self.n += 1
        if len(self.samples) < _MAX_STREET_SAMPLES_POR_CEP:
            self.samples.append((lat, lon, street_norm))

    def centroid(self) -> tuple[float, float] | None:
        if self.n == 0:
            return None
        return (self.sum_lat / self.n, self.sum_lon / self.n)

    def best_point_for_rfb(self, rfb_logradouro: str | None) -> tuple[float, float] | None:
        rfb_n = normalize_logradouro(rfb_logradouro)
        best: tuple[float, float] | None = None
        best_score = 0.0
        for lat, lon, st in self.samples:
            if not st or not rfb_n:
                continue
            score = SequenceMatcher(None, rfb_n, st).ratio()
            if score > best_score:
                best_score = score
                best = (lat, lon)
        if best is not None and best_score >= _STREET_MATCH_MIN:
            return best
        return self.centroid()


def _first_csv_name_in_zip(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ValueError(f"Nenhum CSV em {zip_path}")
        return names[0]


def _iter_cnefe_rows(zip_path: Path) -> Iterator[dict[str, str]]:
    inner = _first_csv_name_in_zip(zip_path)
    with zipfile.ZipFile(zip_path) as zf, zf.open(inner) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        reader = csv.DictReader(text, delimiter=";")
        for row in reader:
            yield {k: (v if v is not None else "") for k, v in row.items()}


def _brazil_bounds(lat: float, lon: float) -> bool:
    return -34.0 <= lat <= 6.0 and -75.0 <= lon <= -32.0


def build_cep_aggregates(zip_path: Path, cep_filter: set[str]) -> dict[str, _CepAgg]:
    """
    Lê um zip CNEFE da UF e agrega apenas CEPs presentes em ``cep_filter``.
    """
    if not cep_filter:
        return {}
    out: dict[str, _CepAgg] = {}
    n_rows = 0
    for row in _iter_cnefe_rows(zip_path):
        n_rows += 1
        cep = normalize_cep(row.get("CEP", ""))
        if not cep or cep not in cep_filter:
            continue
        try:
            lat = float((row.get("LATITUDE") or "").replace(",", "."))
            lon = float((row.get("LONGITUDE") or "").replace(",", "."))
        except ValueError:
            continue
        if math.isnan(lat) or math.isnan(lon) or not _brazil_bounds(lat, lon):
            continue
        st = normalize_logradouro(_cnefe_logradouro_row(row))
        out.setdefault(cep, _CepAgg()).add(lat, lon, st)
        if n_rows % 500_000 == 0:
            log.debug("cnefe.scan.progress", file=zip_path.name, rows=n_rows, cep_hits=len(out))
    log.info(
        "cnefe.aggregate.done",
        zip=zip_path.name,
        rows_scanned=n_rows,
        distinct_ceps=len(out),
    )
    return out


def pick_location_for_doc(
    aggs: dict[str, _CepAgg],
    cep: str | None,
    logradouro: str | None,
) -> dict[str, float] | None:
    """Retorna dict ``lat``/``lon`` para indexação geo_point, ou None."""
    c = normalize_cep(cep)
    if not c:
        return None
    agg = aggs.get(c)
    if agg is None:
        return None
    pt = agg.best_point_for_rfb(logradouro)
    if pt is None:
        return None
    lat, lon = pt
    return {"lat": round(lat, 7), "lon": round(lon, 7)}


def download_cnefe_uf(sigla_uf: str, dest_dir: Path, skip_existing: bool = True) -> Path | None:
    """
    Baixa ``{cod}_{UF}.zip`` do FTP IBGE para ``dest_dir``.
    Retorna o path do zip ou None se falhar.
    """
    sigla = (sigla_uf or "").strip().upper()
    cod = SIGLA_PARA_CODIGO.get(sigla)
    if not cod:
        log.warning("cnefe.download.bad_uf", uf=sigla_uf)
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{cod}_{sigla}.zip"
    dest = dest_dir / fname
    if skip_existing and dest.exists() and dest.stat().st_size > 0:
        log.info("cnefe.download.skip", path=str(dest))
        return dest
    url = CNEFE_UF_ZIP_URL.format(codigo=cod, sigla=sigla)
    log.info("cnefe.download.start", url=url)
    try:
        with httpx.stream("GET", url, timeout=900.0, follow_redirects=True) as resp:
            if resp.status_code == 404:
                log.error("cnefe.download.not_found", url=url)
                return None
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
    except Exception as e:
        log.error("cnefe.download.error", error=str(e)[:300])
        if dest.exists():
            dest.unlink()
        return None
    log.info("cnefe.download.done", path=str(dest), mb=round(dest.stat().st_size / 1e6, 1))
    return dest


def enrich_docs_location_cnefe(
    docs: dict[str, dict],
    cnefe_dir: Path,
    download_missing: bool = False,
) -> dict[str, int]:
    """
    Preenche ``location`` nos documentos usando agregados CNEFE por UF.

    ``docs`` é mutado in-place.

    Returns:
        Contadores: matched, skipped_sem_cep, skipped_sem_zip, skipped_sem_coord
    """
    stats = defaultdict(int)

    # cnpj_basico -> (cep, logradouro, uf)
    by_uf: dict[str, list[tuple[str, str, str | None, str | None]]] = defaultdict(list)
    for cnpj, doc in docs.items():
        uf = (doc.get("uf") or "").strip().upper()
        cep = doc.get("cep")
        if not uf or len(uf) != 2:
            stats["skipped_sem_uf"] += 1
            continue
        c = normalize_cep(cep)
        if not c:
            stats["skipped_sem_cep"] += 1
            continue
        by_uf[uf].append((cnpj, c, doc.get("logradouro"), doc.get("municipio")))

    for uf, entries in by_uf.items():
        cep_set = {e[1] for e in entries}
        zip_path = _resolve_cnefe_zip(cnefe_dir, uf, download_missing)
        if not zip_path:
            stats["skipped_sem_zip"] += len(entries)
            continue
        aggs = build_cep_aggregates(zip_path, cep_set)
        for cnpj, c, logra, _mun in entries:
            loc = pick_location_for_doc(aggs, c, logra)
            if loc:
                docs[cnpj]["location"] = loc
                stats["matched"] += 1
            else:
                stats["skipped_sem_coord"] += 1

    log.info("cnefe.enrich.summary", **dict(stats))
    return dict(stats)


def _resolve_cnefe_zip(cnefe_dir: Path, uf: str, download_missing: bool) -> Path | None:
    cod = SIGLA_PARA_CODIGO.get(uf.upper())
    if not cod:
        return None
    fname = f"{cod}_{uf.upper()}.zip"
    p = cnefe_dir / fname
    if p.exists() and p.stat().st_size > 0:
        return p
    if download_missing:
        return download_cnefe_uf(uf.upper(), cnefe_dir, skip_existing=True)
    return None


def patch_index_locations_cnefe(
    client: Any,
    index: str,
    cnefe_dir: Path,
    download_missing: bool = False,
    scroll_size: int = 5000,
    bulk_chunk: int = 500,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Duas passagens: (1) coleta ``_id``, cep, uf, logradouro; (2) lê cada zip CNEFE
    uma vez por UF e aplica bulk update. Evita reler o mesmo ZIP a cada lote do scroll.

    Requer ``_source``: cep, uf, logradouro.
    """
    from opensearchpy import helpers

    stats: defaultdict[str, int] = defaultdict(int)
    records: list[tuple[str, str, str, str | None]] = []
    # (_id, uf, cep_norm, logradouro)

    scroll_resp = client.search(
        index=index,
        scroll="10m",
        size=scroll_size,
        body={"_source": ["cep", "uf", "logradouro"], "query": {"match_all": {}}},
    )
    scroll_id = scroll_resp["_scroll_id"]
    hits = scroll_resp["hits"]["hits"]

    while hits:
        for h in hits:
            src = h.get("_source") or {}
            uf = (src.get("uf") or "").strip().upper()
            if not uf or len(uf) != 2:
                stats["skipped_sem_uf"] += 1
                continue
            c = normalize_cep(src.get("cep"))
            if not c:
                stats["skipped_sem_cep"] += 1
                continue
            records.append((h["_id"], uf, c, src.get("logradouro")))
        scroll_resp = client.scroll(scroll_id=scroll_id, scroll="10m")
        scroll_id = scroll_resp["_scroll_id"]
        hits = scroll_resp["hits"]["hits"]

    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    by_uf: dict[str, list[tuple[str, str, str | None]]] = defaultdict(list)
    for _id, uf, cep_norm, logra in records:
        by_uf[uf].append((_id, cep_norm, logra))

    actions: list[dict[str, Any]] = []

    def flush_actions() -> None:
        nonlocal actions
        if not actions:
            return
        if dry_run:
            stats["bulk_ok"] += len(actions)
            actions = []
            return
        ok, errors = helpers.bulk(client, actions, raise_on_error=False)
        stats["bulk_ok"] += ok
        stats["bulk_err"] += len(errors) if isinstance(errors, list) else int(errors or 0)
        actions = []

    for uf, rows in by_uf.items():
        cep_set = {r[1] for r in rows}
        zip_path = _resolve_cnefe_zip(cnefe_dir, uf, download_missing)
        if not zip_path:
            stats["skipped_sem_zip"] += len(rows)
            continue
        aggs = build_cep_aggregates(zip_path, cep_set)
        for _id, cep_norm, logra in rows:
            loc = pick_location_for_doc(aggs, cep_norm, logra)
            if not loc:
                stats["skipped_sem_coord"] += 1
                continue
            stats["matched"] += 1
            actions.append(
                {
                    "_op_type": "update",
                    "_index": index,
                    "_id": _id,
                    "doc": {"location": loc},
                }
            )
            if len(actions) >= bulk_chunk:
                flush_actions()

    flush_actions()

    log.info("cnefe.patch.summary", **dict(stats))
    return dict(stats)
