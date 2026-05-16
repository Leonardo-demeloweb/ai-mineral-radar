"""
bot_autuacoes.py — IBAMA SIFISC → mr_autuacoes_v001
====================================================

Indexa autuações ambientais, embargos e apreensões do IBAMA, filtrando para
o universo do MineralRadar (CNPJ básico em mr_empresas_v001 **ou** titular em
mr_jazidas_v001, ou texto com keywords minerais).

Fontes (CKAN público IBAMA):
  - auto_infracao_csv.zip  (~107MB)
  - termo_embargo_csv.zip  (~45MB)
  - termo_apreensao_csv.zip (~28MB)

Filtro de domínio mineral:
  - cnpj_basico (8 dígitos) está no conjunto união de mr_empresas_v001 + titulares
    mr_jazidas_v001.titular.cnpj_basico (normalizado), OU
  - texto da infração/descrição contém keywords minerais

Uso:
  python -m bots.bot_autuacoes --all
  python -m bots.bot_autuacoes --download
  python -m bots.bot_autuacoes --index
  python -m bots.bot_autuacoes --enrich-empresas
  python -m bots.bot_autuacoes --dry-run --skip-download
"""
from __future__ import annotations

import io
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import click
import httpx
import polars as pl
from opensearchpy import OpenSearch, helpers

from bots.common.cnpj_basico import normalize_cnpj_basico
from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_autuacoes = "mr_autuacoes_v001"
INDEX_EMPRESAS  = "mr_empresas_v001"
INDEX_JAZIDAS   = "mr_jazidas_v001"
BATCH_INDEX = 500

SOURCES = {
    "auto_infracao": {
        "url":  "https://dadosabertos.ibama.gov.br/dados/SIFISC/auto_infracao/auto_infracao/auto_infracao_csv.zip",
        "file": "auto_infracao_csv.zip",
        "tipo": "Autuacao",
    },
    "termo_embargo": {
        "url":  "https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/termo_embargo/termo_embargo_csv.zip",
        "file": "termo_embargo_csv.zip",
        "tipo": "Embargo",
    },
    "termo_apreensao": {
        "url":  "https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_apreensao/apreensao/apreensao_csv.zip",
        "file": "termo_apreensao_csv.zip",
        "tipo": "Apreensao",
    },
}

# Keywords que indicam contexto mineral nas descrições textuais
MINERAL_KEYWORDS = [
    "minera", "lavra", "jazida", "garimpo", "extra",
    "minério", "minerio", "areia", "brita", "argila",
    "calcário", "calcario", "granito", "ouro", "diamante",
    "ferro", "bauxita", "nióbio", "niobio", "manganês", "manganes",
    "carvão", "carvao", "pedreira", "saibreira", "rocha ornamental",
    "anm", "dnpm", "código de mineração", "codigo de mineracao",
    "atividade minerária", "atividade mineraria",
]

# Padrão regex compilado para detecção rápida (case-insensitive)
KW_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in MINERAL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# OpenSearch client
# ─────────────────────────────────────────────────────────────────────────────

def get_os_client() -> OpenSearch:
    use_ssl = settings.opensearch_url.startswith("https")
    kwargs: dict = {
        "hosts": [settings.opensearch_url],
        "use_ssl": use_ssl,
        "verify_certs": False,
        "timeout": 120,
    }
    if settings.opensearch_user and settings.opensearch_pass:
        kwargs["http_auth"] = (settings.opensearch_user, settings.opensearch_pass)
    client = OpenSearch(**kwargs)
    info = client.info()
    log.info("opensearch.ok",
             version=info["version"]["number"],
             cluster=info["cluster_name"])
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────────────────────

def download_all(data_dir: Path, skip: bool) -> dict[str, Path]:
    """Baixa os 3 ZIPs do IBAMA SIFISC e devolve {nome: Path}."""
    data_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "MineralRadar/1.0 (etl)"}
    results: dict[str, Path] = {}

    with httpx.Client(timeout=300, headers=headers,
                      follow_redirects=True, verify=False) as client:
        for name, conf in SOURCES.items():
            dest = data_dir / conf["file"]
            if skip and dest.exists() and dest.stat().st_size > 1_000_000:
                log.info("autuacoes.download.skip",
                         source=name,
                         size_mb=round(dest.stat().st_size / 1024 / 1024, 1))
                results[name] = dest
                continue

            log.info("autuacoes.download.start", source=name, url=conf["url"])
            t0 = time.time()
            with client.stream("GET", conf["url"]) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                with dest.open("wb") as f:
                    downloaded = 0
                    for chunk in r.iter_bytes(chunk_size=1 << 20):
                        f.write(chunk)
                        downloaded += len(chunk)
            log.info("autuacoes.download.done",
                     source=name,
                     size_mb=round(dest.stat().st_size / 1024 / 1024, 1),
                     elapsed_s=round(time.time() - t0, 1))
            results[name] = dest
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_float(v) -> float | None:
    """Converte string '1.234,56' ou '1234.56' → float."""
    if v is None or v == "":
        return None
    s = str(v).strip().replace(".", "").replace(",", ".") \
        if "," in str(v) else str(v).strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _is_valor_suspeito(valor: float | None, dt_autuacao: str | None) -> bool:
    """
    Detecta valores de multa em moeda pré-Plano Real (cruzeiros/cruzados).
    Critério: data anterior a 1994-07-01 E valor > R$ 1.000
    (R$ 1.000 em cruzeiros de 1993 ≠ R$ 1.000 de hoje; flag para evitar distorção).
    """
    if valor is None or valor <= 1_000:
        return False
    if dt_autuacao is None:
        return False
    return dt_autuacao < "1994-07-01"


def _enrich_valor(doc: dict) -> dict:
    """Adiciona valor_multa_suspeito e valor_multa_real ao doc."""
    valor = doc.get("valor_multa")
    dt    = doc.get("dt_autuacao")
    suspeito = _is_valor_suspeito(valor, dt)
    doc["valor_multa_suspeito"] = suspeito
    doc["valor_multa_real"]     = None if suspeito else valor
    return doc


def _parse_date(v) -> str | None:
    """Devolve 'YYYY-MM-DD' válido ou None (filtra datas implausíveis)."""
    if v is None or v == "":
        return None
    s = str(v)[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return None
    try:
        y = int(s[:4])
        if y < 1970 or y > datetime.now().year + 1:
            return None
        return s
    except ValueError:
        return None


def _clean_cpf_cnpj(raw) -> tuple[str | None, str | None, str | None]:
    """
    Devolve (cnpj_basico, cnpj_completo, cpf).
    - CNPJ (14 dig): retorna basico (8 dig) + completo
    - CPF  (11 dig): retorna apenas cpf
    - outros: None
    """
    if raw is None:
        return None, None, None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 14:
        return digits[:8], digits, None
    if len(digits) == 11:
        return None, None, digits
    return None, None, None


def _detect_mineral_keywords(*texts: str | None) -> list[str]:
    """Retorna keywords minerais encontradas (sem duplicatas)."""
    found = set()
    for t in texts:
        if not t:
            continue
        for m in KW_PATTERN.findall(str(t)):
            found.add(m.lower())
    return sorted(found)


# ─────────────────────────────────────────────────────────────────────────────
# Parse — Auto de Infração (múltiplos CSVs por ano dentro do ZIP)
# ─────────────────────────────────────────────────────────────────────────────

def parse_auto_infracao(
    zip_path: Path,
    cnpj_basico_mineral: set[str],
) -> list[dict]:
    """Lê todos os CSVs anuais de auto_infracao e filtra para domínio mineral."""
    log.info("autuacoes.parse.auto_infracao.start", zip=zip_path.name)
    docs: list[dict] = []

    with zipfile.ZipFile(zip_path) as zf:
        csv_files = [f for f in zf.namelist() if f.endswith(".csv")]
        log.info("autuacoes.parse.auto_infracao.files", n=len(csv_files))

        for fn in csv_files:
            with zf.open(fn) as f:
                raw = f.read()

            df = pl.read_csv(
                io.BytesIO(raw),
                separator=";",
                encoding="latin-1",
                ignore_errors=True,
                truncate_ragged_lines=True,
                infer_schema_length=0,        # tudo como string, parseamos depois
            )
            if df.is_empty():
                continue

            for row in df.iter_rows(named=True):
                cnpj_basico, cnpj_full, cpf = _clean_cpf_cnpj(
                    row.get("CPF_CNPJ_INFRATOR")
                )

                # Filtro de domínio mineral
                match_empresa = (cnpj_basico is not None
                                  and cnpj_basico in cnpj_basico_mineral)
                kw_hits = _detect_mineral_keywords(
                    row.get("DES_INFRACAO"),
                    row.get("DES_AUTO_INFRACAO"),
                    row.get("MOTIVACAO_CONDUTA"),
                    row.get("DES_LOCAL_INFRACAO"),
                    row.get("DS_ENQUADRAMENTO_COMPLEMENTAR"),
                )
                if not match_empresa and not kw_hits:
                    continue

                # Localização
                lat = _parse_float(row.get("NUM_LATITUDE_AUTO"))
                lon = _parse_float(row.get("NUM_LONGITUDE_AUTO"))
                loc = None
                if lat is not None and lon is not None \
                   and -90 <= lat <= 90 and -180 <= lon <= 180:
                    loc = {"lat": lat, "lon": lon}

                tp_pessoa = "PJ" if cnpj_full else ("PF" if cpf else "?")
                seq       = (row.get("SEQ_AUTO_INFRACAO") or "").strip()

                match_origem = []
                if match_empresa: match_origem.append("empresas_mineral")
                if kw_hits:       match_origem.append("keyword_match")

                docs.append(_enrich_valor({
                    "id":               f"AUTO:{seq}",
                    "tipo":             "Autuacao",
                    "numero_auto":      (row.get("NUM_AUTO_INFRACAO") or "").strip() or None,
                    "serie":            (row.get("SER_AUTO_INFRACAO") or "").strip() or None,
                    "numero_processo":  (row.get("NU_PROCESSO_FORMATADO") or "").strip() or None,
                    "fonte":            "IBAMA-SIFISC",
                    "status":           (row.get("DES_STATUS_FORMULARIO") or "").strip() or None,
                    "cancelado":        (row.get("SIT_CANCELADO") or "").strip().upper() == "S",
                    "cnpj_basico":      cnpj_basico,
                    "cnpj_autuado":     cnpj_full,
                    "cpf_autuado":      cpf,
                    "nome_autuado":     (row.get("NOME_INFRATOR") or "").strip() or None,
                    "tp_pessoa":        tp_pessoa,
                    "infracao":         (row.get("DES_INFRACAO") or "").strip() or None,
                    "fundamentacao":    (row.get("FUNDAMENTACAO_MULTA") or "").strip() or None,
                    "gravidade":        (row.get("GRAVIDADE_INFRACAO") or "").strip() or None,
                    "tipo_infracao":    (row.get("TIPO_INFRACAO") or "").strip() or None,
                    "enquadramento":    (row.get("DS_ENQUADRAMENTO_ADMINISTRATIVO") or "").strip() or None,
                    "valor_multa":      _parse_float(row.get("VAL_AUTO_INFRACAO")),
                    "tipo_multa":       (row.get("TIPO_MULTA") or "").strip() or None,
                    "municipio":        (row.get("MUNICIPIO") or "").strip() or None,
                    "uf":               (row.get("UF") or "").strip() or None,
                    "cod_municipio":    (row.get("COD_MUNICIPIO") or "").strip() or None,
                    "location":         loc,
                    "area_ha":          _parse_float(row.get("QT_AREA")),
                    "dt_autuacao":      _parse_date(row.get("DAT_HORA_AUTO_INFRACAO")),
                    "dt_atualizacao":   _parse_date(row.get("DT_ULT_ALTERACAO")),
                    "biomas":           [b.strip() for b in (row.get("DS_BIOMAS_ATINGIDOS") or "").split(",") if b.strip()] or None,
                    "unidade_conservacao": (row.get("UNIDADE_CONSERVACAO") or "").strip() or None,
                    "match_origem":     match_origem,
                    "match_keywords":   kw_hits or None,
                    "indexed_at":       datetime.now(timezone.utc).isoformat(),
                }))

    log.info("autuacoes.parse.auto_infracao.done", total=len(docs))
    return docs


# ─────────────────────────────────────────────────────────────────────────────
# Parse — Termo de Embargo
# ─────────────────────────────────────────────────────────────────────────────

def parse_termo_embargo(
    zip_path: Path,
    cnpj_basico_mineral: set[str],
) -> list[dict]:
    log.info("autuacoes.parse.termo_embargo.start", zip=zip_path.name)
    docs: list[dict] = []

    with zipfile.ZipFile(zip_path) as zf:
        fn = next(f for f in zf.namelist() if f.endswith(".csv"))
        with zf.open(fn) as f:
            raw = f.read()

    df = pl.read_csv(
        io.BytesIO(raw),
        separator=";",
        encoding="latin-1",
        ignore_errors=True,
        truncate_ragged_lines=True,
        infer_schema_length=0,
    )

    for row in df.iter_rows(named=True):
        cnpj_basico, cnpj_full, cpf = _clean_cpf_cnpj(
            row.get("CPF_CNPJ_EMBARGADO")
        )

        match_empresa = (cnpj_basico is not None
                         and cnpj_basico in cnpj_basico_mineral)
        kw_hits = _detect_mineral_keywords(
            row.get("DES_TAD"),
            row.get("DES_LOCALIZACAO"),
            row.get("NOME_IMOVEL"),
            row.get("TIPO_AREA"),
        )
        if not match_empresa and not kw_hits:
            continue

        lat = _parse_float(row.get("NUM_LATITUDE_TAD"))
        lon = _parse_float(row.get("NUM_LONGITUDE_TAD"))
        loc = None
        if lat is not None and lon is not None \
           and -90 <= lat <= 90 and -180 <= lon <= 180:
            loc = {"lat": lat, "lon": lon}

        tp_pessoa = "PJ" if cnpj_full else ("PF" if cpf else "?")
        seq = (row.get("SEQ_TAD") or "").strip()

        match_origem = []
        if match_empresa: match_origem.append("empresas_mineral")
        if kw_hits:       match_origem.append("keyword_match")

        docs.append(_enrich_valor({
            "id":              f"EMB:{seq}",
            "tipo":            "Embargo",
            "numero_auto":     (row.get("NUM_TAD") or "").strip() or None,
            "serie":           (row.get("SER_TAD") or "").strip() or None,
            "numero_processo": (row.get("NUM_PROCESSO") or "").strip() or None,
            "fonte":           "IBAMA-SIFISC",
            "status":          (row.get("DES_STATUS_FORMULARIO") or "").strip() or None,
            "cancelado":       (row.get("SIT_CANCELADO") or "").strip().upper() == "S",
            "cnpj_basico":     cnpj_basico,
            "cnpj_autuado":    cnpj_full,
            "cpf_autuado":     cpf,
            "nome_autuado":    (row.get("NOME_EMBARGADO") or "").strip() or None,
            "tp_pessoa":       tp_pessoa,
            "infracao":        (row.get("DES_TAD") or "").strip() or None,
            "municipio":       (row.get("MUNICIPIO") or "").strip() or None,
            "uf":              (row.get("UF") or "").strip() or None,
            "cod_municipio":   (row.get("COD_MUNICIPIO") or "").strip() or None,
            "location":        loc,
            "area_ha":         _parse_float(row.get("QTD_AREA_EMBARGADA")),
            "dt_autuacao":     _parse_date(row.get("DAT_EMBARGO")),
            "dt_julgamento":   _parse_date(row.get("DAT_DESEMBARGO")),
            "dt_atualizacao":  _parse_date(row.get("DAT_ULT_ALTERACAO")),
            "match_origem":    match_origem,
            "match_keywords":  kw_hits or None,
            "indexed_at":      datetime.now(timezone.utc).isoformat(),
        }))

    log.info("autuacoes.parse.termo_embargo.done", total=len(docs))
    return docs


# ─────────────────────────────────────────────────────────────────────────────
# Parse — Termo de Apreensão
# ─────────────────────────────────────────────────────────────────────────────

def parse_termo_apreensao(
    zip_path: Path,
    cnpj_basico_mineral: set[str],
) -> list[dict]:
    log.info("autuacoes.parse.termo_apreensao.start", zip=zip_path.name)
    docs: list[dict] = []

    with zipfile.ZipFile(zip_path) as zf:
        fn = next(f for f in zf.namelist() if f.endswith(".csv"))
        with zf.open(fn) as f:
            raw = f.read()

    df = pl.read_csv(
        io.BytesIO(raw),
        separator=";",
        encoding="latin-1",
        ignore_errors=True,
        truncate_ragged_lines=True,
        infer_schema_length=0,
    )

    for row in df.iter_rows(named=True):
        cnpj_basico, cnpj_full, cpf = _clean_cpf_cnpj(
            row.get("NU_CPF_CNPJ_PESSOA_APREENSAO")
        )

        match_empresa = (cnpj_basico is not None
                         and cnpj_basico in cnpj_basico_mineral)
        kw_hits = _detect_mineral_keywords(
            row.get("DS_TAD"),
            row.get("DS_COMPLEMENTAR"),
            row.get("DS_LOCALIZACAO"),
            row.get("DS_AUTO_INFRACAO"),
            row.get("DS_ENQUADRAMENTO_COMPLEMENTAR"),
        )
        if not match_empresa and not kw_hits:
            continue

        lat = _parse_float(row.get("NU_LATITUDE_TAD"))
        lon = _parse_float(row.get("NU_LONGITUDE_TAD"))
        loc = None
        if lat is not None and lon is not None \
           and -90 <= lat <= 90 and -180 <= lon <= 180:
            loc = {"lat": lat, "lon": lon}

        tp_pessoa = "PJ" if cnpj_full else ("PF" if cpf else "?")
        seq = (row.get("SEQ_TAD") or "").strip()
        cd  = (row.get("CD_TAD")  or "").strip()
        uid = seq or cd

        match_origem = []
        if match_empresa: match_origem.append("empresas_mineral")
        if kw_hits:       match_origem.append("keyword_match")

        docs.append(_enrich_valor({
            "id":              f"APR:{uid}",
            "tipo":            "Apreensao",
            "numero_auto":     cd or None,
            "serie":           (row.get("CD_SERIE_TAD") or "").strip() or None,
            "numero_processo": (row.get("NU_PROCESSO_FORMATADO") or "").strip() or None,
            "fonte":           "IBAMA-SIFISC",
            "status":          (row.get("DS_SIT_APREENSAO") or "").strip() or None,
            "cancelado":       (row.get("ST_CANCELADO") or "").strip().upper() == "S",
            "cnpj_basico":     cnpj_basico,
            "cnpj_autuado":    cnpj_full,
            "cpf_autuado":     cpf,
            "nome_autuado":    (row.get("NO_PESSOA_APREENSAO") or "").strip() or None,
            "tp_pessoa":       tp_pessoa,
            "infracao":        (row.get("DS_TAD") or "").strip() or None,
            "enquadramento":   (row.get("DS_ENQUADRAMENTO_ADMINISTRATIVO") or "").strip() or None,
            "valor_multa":     _parse_float(row.get("VL_MULTA")),
            "municipio":       (row.get("NO_MUNICIPIO") or "").strip() or None,
            "uf":              (row.get("SG_UF") or "").strip() or None,
            "cod_municipio":   (row.get("CD_MUNICIPIO") or "").strip() or None,
            "location":        loc,
            "dt_autuacao":     _parse_date(row.get("DT_APREENSAO")),
            "dt_atualizacao":  _parse_date(row.get("DT_ALTERACAO")),
            "match_origem":    match_origem,
            "match_keywords":  kw_hits or None,
            "indexed_at":      datetime.now(timezone.utc).isoformat(),
        }))

    log.info("autuacoes.parse.termo_apreensao.done", total=len(docs))
    return docs


# ─────────────────────────────────────────────────────────────────────────────
# Universo mineral (set de cnpj_basico)
# ─────────────────────────────────────────────────────────────────────────────

def load_cnpj_basico_mineral(client: OpenSearch) -> set[str]:
    """
    Conjunto de cnpj_basico (8 dígitos) para match_empresa no filtro SIFISC.

    União de:
      - mr_empresas_v001 (cadastro RFB mineral já indexado)
      - mr_jazidas_v001.titular.cnpj_basico (titulares ANM, mesmo sem linha em empresas)

    Assim ``bot_autuacoes --index`` não depende de ter rodado ``bot_empresas --index``
    para todo titular que já aparece nas jazidas.
    """
    log.info("autuacoes.cnpj_universe.load.start")
    cnpjs: set[str] = set()
    n_emp = n_jaz = 0

    def _scroll_add(index: str, field_path: str) -> int:
        added = 0
        body = {
            "size": 5000,
            "_source": [field_path],
            "query": {"exists": {"field": field_path}},
        }
        resp = client.search(index=index, body=body, scroll="5m")
        scroll_id = resp["_scroll_id"]
        while True:
            hits = resp["hits"]["hits"]
            if not hits:
                break
            for h in hits:
                src = h.get("_source", {})
                val = src
                for part in field_path.split("."):
                    val = val.get(part, {}) if isinstance(val, dict) else None
                if not val or not isinstance(val, str):
                    continue
                nb = normalize_cnpj_basico(val)
                if nb:
                    cnpjs.add(nb)
                    added += 1
            resp = client.scroll(scroll_id=scroll_id, scroll="5m")
            scroll_id = resp["_scroll_id"]
        try:
            client.clear_scroll(scroll_id=scroll_id)
        except Exception:
            pass
        return added

    n_emp = _scroll_add(INDEX_EMPRESAS, "cnpj_basico")
    n_jaz = _scroll_add(INDEX_JAZIDAS, "titular.cnpj_basico")

    log.info(
        "autuacoes.cnpj_universe.load.done",
        total=len(cnpjs),
        from_empresas_hits=n_emp,
        from_jazidas_hits=n_jaz,
    )
    return cnpjs


# ─────────────────────────────────────────────────────────────────────────────
# Index
# ─────────────────────────────────────────────────────────────────────────────

def iter_actions(docs: list[dict]) -> Iterator[dict]:
    for d in docs:
        # remove None top-level (mantém legibilidade)
        src = {k: v for k, v in d.items() if v is not None}
        yield {
            "_index":  INDEX_autuacoes,
            "_id":     d["id"],
            "_source": src,
        }


def bulk_index(
    client: OpenSearch,
    docs: list[dict],
    dry_run: bool,
) -> dict[str, int]:
    log.info("autuacoes.index.start", total=len(docs))
    total_ok = total_err = 0

    for i in range(0, len(docs), BATCH_INDEX):
        batch = list(iter_actions(docs[i:i + BATCH_INDEX]))
        if not dry_run:
            ok, errs = helpers.bulk(client, batch, raise_on_error=False)
            total_ok  += ok
            total_err += len(errs) if isinstance(errs, list) else errs
            if errs and isinstance(errs, list):
                for e in errs[:2]:
                    log.error("autuacoes.index.error", error=str(e)[:200])
        else:
            total_ok += len(batch)

        if (i // BATCH_INDEX) % 10 == 0:
            log.info("autuacoes.index.progress",
                     done=total_ok + total_err, total=len(docs))

    log.info("autuacoes.index.done",
             total_ok=total_ok, total_err=total_err, dry_run=dry_run)
    return {"ok": total_ok, "errors": total_err}


# ─────────────────────────────────────────────────────────────────────────────
# Enriquecimento mr_empresas com agregados IBAMA
# ─────────────────────────────────────────────────────────────────────────────

def run_enrich_empresas(client: OpenSearch, dry_run: bool) -> dict[str, int]:
    """
    Agrega autuações por cnpj_basico e atualiza mr_empresas_v001 com:
      - n_autuacoes, n_embargos, n_apreensoes
      - valor_total_multa
      - ultima_autuacao (data)
    """
    log.info("autuacoes.enrich_empresas.start")

    # Agregação no OpenSearch — mais eficiente que pull-loop
    agg_query = {
        "size": 0,
        "query": {"exists": {"field": "cnpj_basico"}},
        "aggs": {
            "por_cnpj": {
                "terms": {"field": "cnpj_basico", "size": 100000},
                "aggs": {
                    "tipos":          {"terms": {"field": "tipo"}},
                    "valor_total":    {"sum":   {"field": "valor_multa_real"}},  # exclui valores pré-Plano Real
                    "ultima_data":    {"max":   {"field": "dt_autuacao"}},
                },
            }
        },
    }
    resp = client.search(index=INDEX_autuacoes, body=agg_query)
    buckets = resp["aggregations"]["por_cnpj"]["buckets"]
    log.info("autuacoes.enrich_empresas.aggregated", cnpjs=len(buckets))

    actions = []
    for b in buckets:
        cnpj = b["key"]
        tipos = {t["key"]: t["doc_count"] for t in b["tipos"]["buckets"]}
        valor = b["valor_total"]["value"] or 0.0
        ult   = b["ultima_data"]["value_as_string"]

        actions.append({
            "_op_type": "update",
            "_index":   INDEX_EMPRESAS,
            "_id":      cnpj,
            "doc": {
                "n_autuacoes":      int(tipos.get("Autuacao",  0)),
                "n_embargos":       int(tipos.get("Embargo",   0)),
                "n_apreensoes":     int(tipos.get("Apreensao", 0)),
                "valor_total_multa": round(valor, 2),
                "ultima_autuacao":  ult,
                "tem_risco_ibama":  b["doc_count"] > 0,
            },
        })

    if dry_run:
        log.info("autuacoes.enrich_empresas.done", dry_run=True, updates=len(actions))
        return {"updated": len(actions)}

    ok, errs = helpers.bulk(client, actions, raise_on_error=False)
    log.info("autuacoes.enrich_empresas.done",
             updated=ok,
             errors=len(errs) if isinstance(errs, list) else errs)
    return {"updated": ok,
            "errors": len(errs) if isinstance(errs, list) else errs}


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def log_summary(client: OpenSearch) -> None:
    from collections import Counter

    client.indices.refresh(index=INDEX_autuacoes)
    r = client.search(index=INDEX_autuacoes, body={
        "size": 0,
        "track_total_hits": True,
        "aggs": {
            "por_tipo":   {"terms": {"field": "tipo"}},
            "por_uf":     {"terms": {"field": "uf", "size": 10}},
            "por_origem": {"terms": {"field": "match_origem", "size": 5}},
            "valor_total": {"sum": {"field": "valor_multa"}},
        },
    })
    log.info("autuacoes.summary.total",
             docs=r["hits"]["total"]["value"])
    log.info("autuacoes.summary.por_tipo",
             dist={b["key"]: b["doc_count"]
                   for b in r["aggregations"]["por_tipo"]["buckets"]})
    log.info("autuacoes.summary.por_uf",
             top10={b["key"]: b["doc_count"]
                    for b in r["aggregations"]["por_uf"]["buckets"]})
    log.info("autuacoes.summary.por_origem",
             dist={b["key"]: b["doc_count"]
                   for b in r["aggregations"]["por_origem"]["buckets"]})
    log.info("autuacoes.summary.valor_multa_total_brl",
             valor=round(r["aggregations"]["valor_total"]["value"] or 0, 2))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--download",       is_flag=True, help="Baixa os 3 ZIPs IBAMA.")
@click.option("--index",          is_flag=True, help="Parse + indexa mr_autuacoes_v001.")
@click.option("--enrich-empresas", is_flag=True, help="Agrega e enriquece mr_empresas_v001.")
@click.option("--all", "do_all",  is_flag=True, help="Executa download + index + enrich.")
@click.option("--skip-download",  is_flag=True, help="Reusa cache local.")
@click.option("--dry-run",        is_flag=True, help="Simula sem escrever.")
@click.option("--data-dir",       default=None,
              help="Diretório de cache (default: etl_data_dir/autuacoes).")
def main(
    download:       bool,
    index:          bool,
    enrich_empresas: bool,
    do_all:         bool,
    skip_download:  bool,
    dry_run:        bool,
    data_dir:       str | None,
) -> None:
    """bot_autuacoes — IBAMA SIFISC → mr_autuacoes_v001 (domínio mineral)."""
    t0 = time.time()
    _data_dir = Path(data_dir) if data_dir else settings.etl_data_dir / "autuacoes"

    if do_all:
        download = index = enrich_empresas = True

    if not any([download, index, enrich_empresas]):
        log.warning("bot_autuacoes.no_action",
                    msg="Use --download, --index, --enrich-empresas ou --all.")
        return

    client = get_os_client()

    # ── Download ──────────────────────────────────────────────────────────
    if download:
        download_all(_data_dir, skip=skip_download)

    # ── Index ─────────────────────────────────────────────────────────────
    if index:
        # Garante que os ZIPs estão presentes
        zips: dict[str, Path] = {}
        for name, conf in SOURCES.items():
            p = _data_dir / conf["file"]
            if not p.exists():
                p = download_all(_data_dir, skip=True)[name]
            zips[name] = p

        cnpj_mineral = load_cnpj_basico_mineral(client)

        all_docs: list[dict] = []
        all_docs += parse_auto_infracao   (zips["auto_infracao"],   cnpj_mineral)
        all_docs += parse_termo_embargo   (zips["termo_embargo"],   cnpj_mineral)
        all_docs += parse_termo_apreensao (zips["termo_apreensao"], cnpj_mineral)

        bulk_index(client, all_docs, dry_run=dry_run)

        if not dry_run:
            log_summary(client)

    # ── Enrich empresas ───────────────────────────────────────────────────
    if enrich_empresas:
        try:
            res = run_enrich_empresas(client, dry_run)
            log.info("autuacoes.enrich_empresas.summary", **res)
        except Exception as e:
            log.error("autuacoes.enrich_empresas.error", error=str(e))

    log.info("bot_autuacoes.done",
             elapsed_s=round(time.time() - t0, 1),
             dry_run=dry_run)


if __name__ == "__main__":
    main()
