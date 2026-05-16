"""
bot_sicop.py — SCM/SICOP → prazos e datas administrativas em mr_jazidas_v001

Problema que resolve:
  O bot_anm_direto.py popula mr_jazidas_v001 a partir do shapefile SIGMINE, que
  contém apenas dados geométricos + fase + substância. Os campos administrativos
  ficam nulos após a indexação inicial:

    dt_requerimento  → NULL (shapefile tem apenas o ANO, não a data completa)
    dt_validade      → NULL (prazo de vencimento do título ausente no shapefile)
    dt_outorga       → NULL (data da portaria/alvará/publicação DOU)
    nup              → NULL (Número Único de Protocolo ANM)
    situacao_titulo  → NULL (situação administrativa do título vigente)

  Os CSVs do SCM (dadosabertos.anm.gov.br/SCM/) contêm todos esses campos.
  Este bot os lê e faz bulk update parcial em mr_jazidas_v001, preenchendo
  apenas os campos nulos — sem reindexar os demais dados geométricos/CFEM.

Fonte dos dados:
  https://dadosabertos.anm.gov.br/SCM/Alvara_de_Pesquisa.csv
  https://dadosabertos.anm.gov.br/SCM/Portaria_de_Lavra.csv
  https://dadosabertos.anm.gov.br/SCM/Licenciamento.csv
  ... (mesmos arquivos do bot_scm.py — reutiliza downloads se já existirem)

Semântica por tipo de título:
  Autorização de Pesquisa (AP)  → dt_validade = prazo de 3 anos (renovável)
  Concessão de Lavra            → dt_validade = NULL (title permanente, sem vencimento)
  Licenciamento / Reg. Extração → dt_validade = validade da licença
  Requerimentos (pendentes)     → dt_validade = NULL (aguardando concessão)

Uso:
  python -m bots.bot_sicop                       # enriquece todos
  python -m bots.bot_sicop --skip-download       # reutiliza CSVs já baixados
  python -m bots.bot_sicop --dry-run             # estatísticas sem atualizar OpenSearch
  python -m bots.bot_sicop --uf MG               # filtra apenas processos de uma UF
  python -m bots.bot_sicop --dias-alerta 90      # mostra processos vencendo em N dias
  python -m bots.bot_sicop --report-only         # só relatório, sem enriquecimento
"""
from __future__ import annotations

import csv as csvlib
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import click
import polars as pl
from opensearchpy import OpenSearch, helpers

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_JAZIDAS = "mr_jazidas_v001"
BATCH_SIZE    = 500   # updates por bulk request (updates são mais pesados que inserts)

SCM_BASE = "https://dadosabertos.anm.gov.br/SCM"

# CSVs individuais por tipo de título — formato simplificado (10 colunas, sem datas)
# Fonte de: situacao_titulo, fase_atual, titular, municipio, substancia
SCM_FILES = [
    "Alvara_de_Pesquisa.csv",
    "Portaria_de_Lavra.csv",
    "Licenciamento.csv",
    "Requerimento_de_Pesquisa.csv",
    "Requerimento_de_Lavra.csv",
    "Registro_de_Extracao_Publicado.csv",
    "Relatorio_de_Pesquisa_Aprovado.csv",
    "Requerimento_de_Licenciamento.csv",
    "Requerimento_de_PLG.csv",
    "PLG.csv",
]

# ZIP de microdados — formato relacional completo (~313 MB), contém datas administrativas
# Fonte de: dt_requerimento, dt_validade, dt_outorga, nup
SCM_MICRODADOS_URL  = f"{SCM_BASE}/microdados/microdados-scm.zip"
SCM_MICRODADOS_FILE = "microdados-scm.zip"

HEADERS = {"User-Agent": settings.anm_user_agent}

# ─── Tipo de título → tem validade? ────────────────────────────────────────────
# Concessões de Lavra são perpétuas (decreto); Alvarás e Licenciamentos têm prazo.
TITULO_TEM_VALIDADE = {
    "Alvara_de_Pesquisa.csv":              True,
    "Licenciamento.csv":                   True,
    "Registro_de_Extracao_Publicado.csv":  True,
    "PLG.csv":                             True,
    "Portaria_de_Lavra.csv":               False,
    "Requerimento_de_Pesquisa.csv":        False,
    "Requerimento_de_Lavra.csv":           False,
    "Relatorio_de_Pesquisa_Aprovado.csv":  False,
    "Requerimento_de_Licenciamento.csv":   False,
    "Requerimento_de_PLG.csv":             False,
}

# ─── Candidatos de colunas por papel semântico ────────────────────────────────
# A ANM usa nomes levemente diferentes dependendo da versão do CSV.
# Tentamos cada candidato em ordem; o primeiro que existir no DataFrame é usado.
COL_PROCESSO = [
    "processo", "numero_do_processo", "nr_processo",
    "numero_processo", "ds_processo",
    "dsprocesso",                           # microdados: DSProcesso
]
COL_REQUERIMENTO = [
    "data_do_requerimento", "data_requerimento", "dt_requerimento",
    "data_de_requerimento", "data_protocolo", "dt_protocolo",
    "dtprotocolo",                          # microdados: DTProtocolo
    "dtprioridade",                         # microdados: DTPrioridade (fallback)
]
COL_OUTORGA = [
    "data_outorga", "data_de_outorga", "dt_outorga",
    "data_portaria", "data_alvara", "data_publicacao_dou",
    "data_publicacao", "data_inicio_vigencia",
    "dtpublicacao",                         # microdados/ProcessoTitulo: DTPublicacao
]
COL_VALIDADE = [
    "data_validade", "data_de_validade", "dt_validade",
    "prazo_de_validade", "prazo_validade", "data_vencimento",
    "data_fim_vigencia", "validade",
    "dtvencimento",                         # microdados/ProcessoTitulo: DTVencimento
]
COL_SITUACAO = [
    "situacao", "situacao_do_titulo", "situacao_titulo",
    "status", "fase_atual", "fase",
]
COL_NUP = [
    "nup", "numero_nup", "nr_nup", "nup_processo",
    "nrnup",                                # microdados: NRNUP
]
COL_UF = ["uf", "sigla_uf", "estado"]
COL_MUNICIPIO = ["municipio", "municipios", "nm_municipio", "nome_municipio"]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_col(col: str) -> str:
    """Normaliza nome de coluna: minúsculo, sem acentos, sem espaços."""
    return (
        col.strip()
        .replace("ê", "e").replace("â", "a").replace("ã", "a").replace("à", "a")
        .replace("ç", "c").replace("é", "e").replace("á", "a").replace("è", "e")
        .replace("ú", "u").replace("ó", "o").replace("í", "i").replace("ô", "o")
        .replace("(s)", "").replace("(", "").replace(")", "").replace("/", "")
        .strip().lower().replace(" ", "_").replace("-", "_")
    )


def _first_col(df_cols: list[str], candidates: list[str]) -> str | None:
    """Retorna o primeiro candidato de coluna que existe no DataFrame."""
    col_set = set(df_cols)
    for c in candidates:
        if c in col_set:
            return c
    return None


def _normalize_processo(p: str | None) -> str | None:
    """
    Normaliza número de processo para chave de deduplicação (remove pontos e espaços).
    Ex: "860.037/1980" → "860037/1980"
    Usado apenas como chave de dict — NÃO como _id do OpenSearch.
    """
    if not p:
        return None
    s = re.sub(r"[\s.]", "", str(p).strip())
    return s if s not in ("", "nan", "None") else None


def _clean_processo_raw(p: str | None) -> str | None:
    """
    Retorna o número do processo limpo mas preservando pontos/barras,
    igual ao formato gravado como _id pelo bot_anm_direto.py.
    Ex: "860.037/1980 " → "860.037/1980"
    """
    if not p:
        return None
    s = str(p).strip()
    return s if s.lower() not in ("", "nan", "none") else None


def _processo_para_opensearch_doc_id(proc_id: str | None) -> str | None:
    """
    Alinha o número ao `_id` de mr_jazidas_v001 (campo PROCESSO do SIGMINE).

    O microdados SCM costuma trazer DSProcesso sem ponto (ex.: 860037/1980),
    enquanto o shapefile ativo usa 860.037/1980 — update por _id falhava com
    document_missing. Mesma regra que bot_inativos._add_dot.
    """
    if not proc_id:
        return None
    s = str(proc_id).strip()
    if not s or "." in s:
        return s
    parts = s.split("/")
    if len(parts) != 2:
        return s
    num, year = parts[0], parts[1]
    if len(num) > 3:
        return f"{num[:-3]}.{num[-3:]}/{year}"
    return s


def _opensearch_id_candidates(proc_id: str | None) -> list[str]:
    """
    Possíveis `_id` no índice para o mesmo processo.

    - Ativos SIGMINE: geralmente com ponto (860.037/1980).
    - Inativos SIGMINE: frequentemente sem ponto (860037/1980).
    - SCM/microdados: variam entre os dois.

    Emitimos update para cada variante distinta; uma acerta o documento,
    a outra retorna document_missing (aceitável em bulk com raise_on_error=False).
    """
    dotted = _processo_para_opensearch_doc_id(proc_id)
    if not dotted:
        return []
    plain = dotted.replace(".", "")
    out: list[str] = [dotted]
    if plain != dotted:
        out.append(plain)
    return list(dict.fromkeys(out))


def _parse_date_br(val: str | None) -> str | None:
    """
    Converte datas no formato BR (DD/MM/AAAA ou DD-MM-AAAA) para ISO (AAAA-MM-DD).
    Retorna None para datas inválidas ou nulas.
    """
    if not val or str(val).strip().lower() in ("", "nan", "none", "null", "0"):
        return None
    s = str(val).strip()

    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = datetime.strptime(s, fmt).date()
            # Sanidade: datas válidas para processos ANM
            if date(1900, 1, 1) <= d <= date(2099, 12, 31):
                return d.isoformat()
        except ValueError:
            continue
    return None


def _detect_separator(path: Path) -> str:
    """Auto-detecta separador (`;` vs `,`) lendo o cabeçalho do arquivo."""
    try:
        raw = path.read_bytes()[:2000].decode("latin1", errors="replace")
        first_line = raw.split("\n")[0]
        return ";" if first_line.count(";") >= 5 else ","
    except Exception:
        return ","


# ─────────────────────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────────────────────

def download_microdados(data_dir: Path, skip: bool) -> Path | None:
    """
    Baixa microdados-scm.zip (~313 MB) para data_dir/scm/.
    Contém o schema relacional completo com datas administrativas:
    dt_requerimento, dt_validade, dt_outorga, nup.

    Retorna o path do ZIP se disponível, None em caso de falha.
    """
    import httpx

    scm_dir = data_dir / "scm"
    scm_dir.mkdir(parents=True, exist_ok=True)
    dest = scm_dir / SCM_MICRODADOS_FILE

    if skip and dest.exists():
        log.info("sicop.microdados.skip", size_mb=round(dest.stat().st_size / 1e6, 1))
        return dest

    log.info("sicop.microdados.download.start", url=SCM_MICRODADOS_URL,
             note="arquivo grande ~313 MB, pode demorar alguns minutos")
    try:
        with httpx.stream(
            "GET", SCM_MICRODADOS_URL, headers=HEADERS,
            timeout=600, follow_redirects=True,
        ) as resp:
            if resp.status_code == 404:
                log.warning("sicop.microdados.not_found",
                            fallback="usando apenas CSVs individuais (sem datas)")
                return None
            resp.raise_for_status()
            with open(dest, "wb") as f:
                downloaded = 0
                for chunk in resp.iter_bytes(1024 * 512):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded % (10 * 1024 * 1024) == 0:
                        log.info("sicop.microdados.progress",
                                 mb=round(downloaded / 1e6, 0))
        log.info("sicop.microdados.download.done",
                 size_mb=round(dest.stat().st_size / 1e6, 1))
        return dest
    except Exception as exc:
        log.warning("sicop.microdados.error", error=str(exc)[:300])
        return None


def parse_microdados_prazos(zip_path: Path, uf_filter: str | None = None) -> dict[str, dict]:
    """
    Lê microdados-scm.zip e extrai datas administrativas de dois arquivos-chave:

    Processo.txt (104 MB):
        DSProcesso → _id_os, NRNUP → nup, DTProtocolo → dt_requerimento

    ProcessoTitulo.txt (31 MB):
        DSProcesso → _id_os, DTVencimento → dt_validade, DTPublicacao → dt_outorga
        (cada processo pode ter múltiplos títulos; prevalece o mais recente)

    Os arquivos usam separador ";" e encoding latin1.
    ProcessoEvento.txt (~1 GB) e outros irrelevantes são ignorados.
    """
    import zipfile
    from io import StringIO

    prazos: dict[str, dict] = {}

    if not zip_path.exists():
        return prazos

    # Apenas os dois arquivos que contêm os dados que precisamos
    RELEVANT = {
        "microdados-scm/Processo.txt",
        "microdados-scm/ProcessoTitulo.txt",
    }

    try:
        with zipfile.ZipFile(zip_path) as zf:
            available = set(zf.namelist())
            to_process = [f for f in RELEVANT if f in available]
            log.info("sicop.microdados.files",
                     target=sorted(RELEVANT), found=to_process,
                     total_in_zip=len(available))

            for fname in to_process:
                info = zf.getinfo(fname)
                log.info("sicop.microdados.reading",
                         file=fname, size_mb=round(info.file_size / 1e6, 1))

                with zf.open(fname) as f:
                    content = f.read().decode("latin1", errors="replace")

                try:
                    df = pl.read_csv(
                        StringIO(content),
                        separator=";",
                        infer_schema_length=0,
                        ignore_errors=True,
                        truncate_ragged_lines=True,
                        null_values=["", "NULL", "null"],
                    )
                    df = df.rename({col: _normalize_col(col) for col in df.columns})
                except Exception as exc:
                    log.warning("sicop.microdados.parse_failed",
                                file=fname, error=str(exc)[:200])
                    continue

                cols = df.columns
                c_proc = _first_col(cols, COL_PROCESSO)
                c_req  = _first_col(cols, COL_REQUERIMENTO)
                c_out  = _first_col(cols, COL_OUTORGA)
                c_val  = _first_col(cols, COL_VALIDADE)
                c_nup  = _first_col(cols, COL_NUP)

                log.info("sicop.microdados.cols", file=fname,
                         mapped={"processo": c_proc, "req": c_req,
                                 "out": c_out, "val": c_val, "nup": c_nup})

                if not c_proc or not any([c_req, c_out, c_val, c_nup]):
                    log.info("sicop.microdados.csv.skip", file=fname,
                             reason="sem colunas uteis", all_cols=cols)
                    continue

                ok = skipped = 0
                for row in df.iter_rows(named=True):
                    proc_raw_val = row.get(c_proc)
                    proc_id  = _clean_processo_raw(
                        str(proc_raw_val) if proc_raw_val is not None else None
                    )
                    proc_key = _normalize_processo(proc_id)
                    if not proc_key:
                        skipped += 1
                        continue

                    dt_req = _parse_date_br(row.get(c_req) if c_req else None)
                    dt_out = _parse_date_br(row.get(c_out) if c_out else None)
                    dt_val = _parse_date_br(row.get(c_val) if c_val else None)
                    nup_raw = str(row.get(c_nup) or "").strip() if c_nup else ""
                    nup = nup_raw if nup_raw.lower() not in ("", "nan", "none", "null") else None

                    existing = prazos.get(proc_key)
                    if existing is None:
                        prazos[proc_key] = {
                            "_id_os":          proc_id,
                            "dt_requerimento": dt_req,
                            "dt_validade":     dt_val,
                            "dt_outorga":      dt_out,
                            "nup":             nup,
                            "situacao_titulo": None,
                            "uf":              None,
                            "municipio_scm":   None,
                            "_fonte":          f"microdados:{fname}",
                        }
                    else:
                        if dt_req and (
                            not existing["dt_requerimento"]
                            or dt_req < existing["dt_requerimento"]
                        ):
                            existing["dt_requerimento"] = dt_req
                        if dt_out and (
                            not existing["dt_outorga"]
                            or dt_out > existing["dt_outorga"]
                        ):
                            existing["dt_outorga"] = dt_out
                            existing["_fonte"] = f"microdados:{fname}"
                        if dt_val and (
                            not existing["dt_validade"]
                            or dt_val > existing["dt_validade"]
                        ):
                            existing["dt_validade"] = dt_val
                        if nup and not existing.get("nup"):
                            existing["nup"] = nup
                    ok += 1

                log.info("sicop.microdados.file.done",
                         file=fname, parsed=ok, skipped=skipped,
                         with_dt_val=sum(1 for v in prazos.values() if v.get("dt_validade")),
                         with_dt_out=sum(1 for v in prazos.values() if v.get("dt_outorga")),
                         with_nup=sum(1 for v in prazos.values() if v.get("nup")))

    except Exception as exc:
        log.error("sicop.microdados.parse_error", error=str(exc)[:400])

    log.info("sicop.microdados.total", processos=len(prazos))
    return prazos


def download_scm_csvs(data_dir: Path, skip: bool) -> dict[str, Path]:
    """
    Baixa os CSVs do SCM para data_dir/scm/.
    Se skip=True e o arquivo já existe, pula o download.
    Retorna dict {filename: path}.
    """
    import httpx

    scm_dir = data_dir / "scm"
    scm_dir.mkdir(parents=True, exist_ok=True)
    downloaded: dict[str, Path] = {}

    for fname in SCM_FILES:
        dest = scm_dir / fname
        if skip and dest.exists():
            log.info("sicop.download.skip", file=fname, size_mb=round(dest.stat().st_size / 1e6, 1))
            downloaded[fname] = dest
            continue

        url = f"{SCM_BASE}/{fname}"
        log.info("sicop.download.start", url=url)
        try:
            with httpx.stream(
                "GET", url, headers=HEADERS, timeout=180, follow_redirects=True
            ) as resp:
                if resp.status_code == 404:
                    log.warning("sicop.download.not_found", file=fname)
                    continue
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes(1024 * 256):
                        f.write(chunk)
            log.info("sicop.download.done", file=fname,
                     size_mb=round(dest.stat().st_size / 1e6, 1))
            downloaded[fname] = dest
        except Exception as exc:
            log.warning("sicop.download.error", file=fname, error=str(exc)[:200])

    return downloaded


# ─────────────────────────────────────────────────────────────────────────────
# Parse CSVs → dict de prazos
# ─────────────────────────────────────────────────────────────────────────────

def _load_csv(path: Path, sep: str) -> pl.DataFrame | None:
    """Tenta carregar CSV com Polars; fallback para csv.reader do Python."""
    try:
        df = pl.read_csv(
            path,
            separator=sep,
            encoding="latin1",
            infer_schema_length=0,
            ignore_errors=True,
            truncate_ragged_lines=True,
        )
        if len(df.columns) > 3:
            return df.rename({col: _normalize_col(col) for col in df.columns})
    except Exception:
        pass

    # Fallback: csv.reader (mais robusto com aspas embutidas)
    try:
        rows = []
        with open(path, encoding="latin1", errors="replace") as f:
            reader = csvlib.reader(f, delimiter=sep, quotechar='"')
            headers = None
            for row in reader:
                if headers is None:
                    headers = [_normalize_col(h) for h in row]
                    continue
                if len(row) == len(headers):
                    rows.append(row)
        if headers and rows:
            return pl.DataFrame({headers[i]: [r[i] for r in rows] for i in range(len(headers))})
    except Exception:
        pass

    return None


def parse_scm_prazos(
    downloaded: dict[str, Path],
    uf_filter: str | None = None,
) -> dict[str, dict]:
    """
    Lê todos os CSVs SCM e extrai campos de prazo/data por processo.

    Retorna dict:
        { numero_processo_normalizado: {
            "dt_requerimento":  "AAAA-MM-DD" | None,
            "dt_validade":      "AAAA-MM-DD" | None,
            "dt_outorga":       "AAAA-MM-DD" | None,
            "nup":              str | None,
            "situacao_titulo":  str | None,
            "uf":               str | None,
            "municipio":        str | None,
            "_fonte":           "Alvara_de_Pesquisa.csv" | ...,
        } }

    Quando um processo aparece em múltiplos CSVs (ex: tem AP histórica e CL atual),
    a entrada mais recente (maior dt_outorga) prevalece para dt_outorga e situacao_titulo,
    mas a menor dt_requerimento (primeira ocorrência) sempre é preservada.
    """
    prazos: dict[str, dict] = {}

    for fname, path in downloaded.items():
        tem_validade = TITULO_TEM_VALIDADE.get(fname, False)
        sep = _detect_separator(path)

        df = _load_csv(path, sep)
        if df is None:
            # Tenta separador alternativo
            alt = "," if sep == ";" else ";"
            df = _load_csv(path, alt)
        if df is None:
            log.warning("sicop.csv.parse_failed", file=fname)
            continue

        cols = df.columns

        # Localiza colunas de interesse
        c_proc  = _first_col(cols, COL_PROCESSO)
        c_req   = _first_col(cols, COL_REQUERIMENTO)
        c_out   = _first_col(cols, COL_OUTORGA)
        c_val   = _first_col(cols, COL_VALIDADE) if tem_validade else None
        c_sit   = _first_col(cols, COL_SITUACAO)
        c_nup   = _first_col(cols, COL_NUP)
        c_uf    = _first_col(cols, COL_UF)
        c_mun   = _first_col(cols, COL_MUNICIPIO)

        if not c_proc:
            log.warning("sicop.csv.no_processo_col", file=fname, cols=cols[:8])
            continue

        log.info("sicop.csv.parsing", file=fname, rows=len(df),
                 all_cols=cols,
                 cols_found={
                     "processo": c_proc, "requerimento": c_req,
                     "outorga": c_out, "validade": c_val,
                     "situacao": c_sit, "nup": c_nup,
                 })

        # Aplica filtro de UF se solicitado
        if uf_filter and c_uf:
            df = df.filter(
                pl.col(c_uf).str.to_uppercase().str.strip_chars() == uf_filter.upper()
            )
            log.info("sicop.csv.uf_filter", file=fname, uf=uf_filter, rows_after=len(df))

        ok = skipped = 0
        for row in df.iter_rows(named=True):
            proc_raw_val = row.get(c_proc)
            # _id_os: número com pontos, igual ao _id gravado pelo bot_anm_direto.py
            # ex: "860.037/1980"
            proc_id   = _clean_processo_raw(str(proc_raw_val) if proc_raw_val else None)
            # chave de dict: normalizada (sem pontos) para deduplicação cross-file
            proc_key  = _normalize_processo(proc_id)
            if not proc_key:
                skipped += 1
                continue

            dt_req  = _parse_date_br(row.get(c_req) if c_req else None)
            dt_out  = _parse_date_br(row.get(c_out) if c_out else None)
            dt_val  = _parse_date_br(row.get(c_val) if c_val else None)
            sit_raw = str(row.get(c_sit, "") or "").strip()
            situacao = sit_raw if sit_raw.lower() not in ("", "nan", "none") else None
            nup_raw = str(row.get(c_nup, "") or "").strip() if c_nup else ""
            nup = nup_raw if nup_raw.lower() not in ("", "nan", "none") else None
            uf_val = str(row.get(c_uf, "") or "").strip().upper() if c_uf else None
            mun_val = str(row.get(c_mun, "") or "").strip() if c_mun else None

            existing = prazos.get(proc_key)
            if existing is None:
                prazos[proc_key] = {
                    "_id_os":          proc_id,    # valor original para lookup por _id
                    "dt_requerimento": dt_req,
                    "dt_validade":     dt_val,
                    "dt_outorga":      dt_out,
                    "nup":             nup,
                    "situacao_titulo": situacao,
                    "uf":              uf_val if uf_val and len(uf_val) == 2 else None,
                    "municipio_scm":   mun_val,
                    "_fonte":          fname,
                }
            else:
                # Preserva a data de requerimento mais antiga (primeira ocorrência histórica)
                if dt_req and (not existing["dt_requerimento"] or dt_req < existing["dt_requerimento"]):
                    existing["dt_requerimento"] = dt_req

                # Prevalece a outorga mais recente (representa o título vigente)
                if dt_out and (not existing["dt_outorga"] or dt_out > existing["dt_outorga"]):
                    existing["dt_outorga"] = dt_out
                    existing["situacao_titulo"] = situacao
                    existing["_fonte"] = fname

                # Validade: usa a mais recente (título mais novo)
                if dt_val and (not existing["dt_validade"] or dt_val > existing["dt_validade"]):
                    existing["dt_validade"] = dt_val

                # NUP: preenche se vazio
                if nup and not existing["nup"]:
                    existing["nup"] = nup

            ok += 1

        log.info("sicop.csv.done", file=fname, parsed=ok, skipped=skipped)

    log.info("sicop.prazos.total", processos=len(prazos))
    return prazos


# ─────────────────────────────────────────────────────────────────────────────
# OpenSearch client
# ─────────────────────────────────────────────────────────────────────────────

def get_os_client() -> OpenSearch:
    use_ssl = settings.opensearch_url.startswith("https")
    kwargs: dict = {
        "hosts": [settings.opensearch_url],
        "use_ssl": use_ssl,
        "verify_certs": False,
        "timeout": 60,
    }
    if settings.opensearch_user and settings.opensearch_pass:
        kwargs["http_auth"] = (settings.opensearch_user, settings.opensearch_pass)
    client = OpenSearch(**kwargs)
    info = client.info()
    log.info("opensearch.ok", version=info["version"]["number"], cluster=info["cluster_name"])
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Bulk update parcial em mr_jazidas_v001
# ─────────────────────────────────────────────────────────────────────────────

def _generate_updates(
    prazos: dict[str, dict],
    dry_run: bool,
) -> Iterator[dict]:
    """
    Gera ações de update parcial para o OpenSearch bulk helper.

    Estratégia de _id:
      bot_anm_direto.py usa PROCESSO do shapefile como `_id` (com ou sem ponto).
      `_opensearch_id_candidates` emite um update por variante (pontuado e sem
      ponto quando diferem), para atingir ativos e inativos.

    O script Painless só preenche campos que ainda estão nulos no documento —
    não sobrescreve dados existentes.
    """
    SCRIPT = """
        if (params.dt_requerimento != null && (ctx._source.dt_requerimento == null || ctx._source.dt_requerimento == ''))
            { ctx._source.dt_requerimento = params.dt_requerimento; }
        if (params.dt_validade != null && (ctx._source.dt_validade == null || ctx._source.dt_validade == ''))
            { ctx._source.dt_validade = params.dt_validade; }
        if (params.dt_outorga != null && (ctx._source.dt_outorga == null || ctx._source.dt_outorga == ''))
            { ctx._source.dt_outorga = params.dt_outorga; }
        if (params.nup != null && (ctx._source.nup == null || ctx._source.nup == ''))
            { ctx._source.nup = params.nup; }
        if (params.situacao_titulo != null && (ctx._source.situacao_titulo == null || ctx._source.situacao_titulo == ''))
            { ctx._source.situacao_titulo = params.situacao_titulo; }
    """

    for _proc_key, data in prazos.items():
        params = {
            "dt_requerimento": data["dt_requerimento"],
            "dt_validade":     data["dt_validade"],
            "dt_outorga":      data["dt_outorga"],
            "nup":             data["nup"],
            "situacao_titulo": data["situacao_titulo"],
        }

        # Só emite update se pelo menos um campo tem valor
        if not any(v for v in params.values()):
            continue

        raw_id = data.get("_id_os") or _proc_key
        doc_ids = _opensearch_id_candidates(raw_id)
        if not doc_ids:
            continue

        if not dry_run:
            for doc_id in doc_ids:
                yield {
                    "_op_type": "update",
                    "_index":   INDEX_JAZIDAS,
                    "_id":      doc_id,
                    "script": {
                        "source": SCRIPT,
                        "lang":   "painless",
                        "params": params,
                    },
                    # retry_on_conflict: resolve race conditions em runs paralelas
                    "retry_on_conflict": 3,
                }
        else:
            yield params   # dry_run: só contagem


def bulk_update(
    client: OpenSearch,
    prazos: dict[str, dict],
    batch_size: int,
    dry_run: bool,
) -> tuple[int, int]:
    """
    Executa bulk update parcial.
    Retorna (docs_atualizados, erros).
    """
    if dry_run:
        with_dates = sum(1 for d in prazos.values() if any(v for v in d.values() if isinstance(v, str) and v))
        log.info("dry_run.update", processos_com_datas=with_dates, total=len(prazos))
        return with_dates, 0

    total_ok = total_err = 0
    gen = _generate_updates(prazos, dry_run=False)

    batch: list[dict] = []
    for action in gen:
        batch.append(action)
        if len(batch) >= batch_size:
            ok, errs = helpers.bulk(
                client, batch,
                raise_on_error=False,
                max_retries=3,
                chunk_size=batch_size,
            )
            total_ok  += ok
            batch_err = len(errs) if isinstance(errs, list) else errs
            total_err += batch_err
            log.info(
                "batch.update.done",
                batch_ok=ok,
                batch_errors=batch_err,
                cumulative_ok=total_ok,
                cumulative_errors=total_err,
            )
            batch = []

    if batch:
        ok, errs = helpers.bulk(
            client, batch, raise_on_error=False, max_retries=3
        )
        total_ok  += ok
        batch_err = len(errs) if isinstance(errs, list) else errs
        total_err += batch_err
        log.info(
            "batch.update.done",
            batch_ok=ok,
            batch_errors=batch_err,
            cumulative_ok=total_ok,
            cumulative_errors=total_err,
        )

    return total_ok, total_err


# ─────────────────────────────────────────────────────────────────────────────
# Relatório de alertas de prazo
# ─────────────────────────────────────────────────────────────────────────────

def relatorio_vencimentos(
    client: OpenSearch,
    dias: int,
) -> None:
    """
    Busca em mr_jazidas_v001 os processos com dt_validade nos próximos N dias
    e exibe relatório no log. Útil para monitoramento proativo de prazos.
    """
    hoje = date.today()
    limite = hoje + timedelta(days=dias)

    query = {
        "size": 100,
        "query": {
            "bool": {
                "must": [
                    {"term": {"ativo": True}},
                    {"range": {
                        "dt_validade": {
                            "gte": hoje.isoformat(),
                            "lte": limite.isoformat(),
                        }
                    }},
                ]
            }
        },
        "sort": [{"dt_validade": "asc"}],
        "_source": ["numero_processo", "fase", "substancias_desc", "uf", "municipio",
                    "dt_validade", "dt_outorga", "titular.nome", "situacao_titulo"],
    }

    resp = client.search(index=INDEX_JAZIDAS, body=query)
    hits = resp["hits"]["hits"]
    total = resp["hits"]["total"]["value"]

    log.info(
        "relatorio.vencimentos",
        dias_alerta=dias,
        total_vencendo=total,
        exibindo=len(hits),
        periodo=f"{hoje.isoformat()} → {limite.isoformat()}",
    )

    for h in hits:
        s = h["_source"]
        log.warning(
            "alerta.prazo",
            processo=s.get("numero_processo"),
            fase=s.get("fase"),
            substancias=s.get("substancias_desc", [])[:3],
            uf=s.get("uf"),
            municipio=s.get("municipio"),
            dt_validade=s.get("dt_validade"),
            titular=s.get("titular", {}).get("nome"),
            situacao_titulo=s.get("situacao_titulo"),
        )

    # Resumo por UF
    uf_counts: dict[str, int] = defaultdict(int)
    for h in hits:
        uf = h["_source"].get("uf", "??")
        uf_counts[uf] += 1

    log.info("relatorio.por_uf", distribuicao=dict(sorted(uf_counts.items())))


def relatorio_cobertura(client: OpenSearch) -> None:
    """Mostra % de jazidas ativas com cada campo de data preenchido."""
    aggs = {
        "size": 0,
        "track_total_hits": True,   # sem isso OpenSearch retorna máx. 10K
        "query": {"term": {"ativo": True}},
        "aggs": {
            "total": {"value_count": {"field": "numero_processo"}},
            "com_dt_requerimento": {
                "filter": {"exists": {"field": "dt_requerimento"}}
            },
            "com_dt_validade": {
                "filter": {"exists": {"field": "dt_validade"}}
            },
            "com_dt_outorga": {
                "filter": {"exists": {"field": "dt_outorga"}}
            },
            "com_nup": {
                "filter": {"exists": {"field": "nup"}}
            },
            "com_situacao_titulo": {
                "filter": {"exists": {"field": "situacao_titulo"}}
            },
        },
    }

    resp = client.search(index=INDEX_JAZIDAS, body=aggs)
    total = resp["hits"]["total"]["value"]
    if total == 0:
        log.warning("cobertura.zero_docs")
        return

    agg = resp["aggregations"]

    def pct(field: str) -> str:
        n = agg.get(field, {}).get("doc_count", 0)
        return f"{n:,} ({n * 100 / total:.1f}%)"

    log.info(
        "cobertura.datas",
        total_ativos=f"{total:,}",
        dt_requerimento=pct("com_dt_requerimento"),
        dt_validade=pct("com_dt_validade"),
        dt_outorga=pct("com_dt_outorga"),
        nup=pct("com_nup"),
        situacao_titulo=pct("com_situacao_titulo"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--skip-download",  is_flag=True,
              help="Reutiliza CSVs já baixados em etl_data_dir/scm/")
@click.option("--microdados",     is_flag=True,
              help="Baixa microdados-scm.zip (~313 MB) para extrair datas (dt_validade, dt_outorga, nup)")
@click.option("--dry-run",        is_flag=True,
              help="Só conta/analisa sem gravar no OpenSearch")
@click.option("--uf",             default=None, metavar="UF",
              help="Filtra apenas processos de uma UF (ex: --uf MG)")
@click.option("--dias-alerta",    default=0,    type=int,
              help="Após enriquecimento, mostra processos vencendo em N dias")
@click.option("--report-only",    is_flag=True,
              help="Só exibe relatório de cobertura e alertas (sem download/update)")
@click.option("--batch-size",     default=BATCH_SIZE, show_default=True)
def main(
    skip_download: bool,
    microdados: bool,
    dry_run: bool,
    uf: str | None,
    dias_alerta: int,
    report_only: bool,
    batch_size: int,
) -> None:
    """
    Enriquece mr_jazidas_v001 com prazos e datas administrativas do SCM/SICOP.

    CSVs individuais (~55 MB total) → situacao_titulo, fase_atual, municipio.
    Microdados ZIP (~313 MB, via --microdados) → dt_requerimento, dt_validade,
    dt_outorga, nup (datas administrativas completas).
    """
    os_client = get_os_client()
    data_dir  = settings.etl_data_dir

    if report_only:
        log.info("mode.report_only")
        relatorio_cobertura(os_client)
        if dias_alerta > 0:
            relatorio_vencimentos(os_client, dias_alerta)
        return

    # ── 1. Download CSVs individuais ──────────────────────────────────────────
    log.info("sicop.download.csvs", skip=skip_download)
    downloaded = download_scm_csvs(data_dir, skip=skip_download)

    if not downloaded:
        log.error("sicop.no_files", msg="Nenhum CSV SCM disponível. Abortando.")
        raise SystemExit(1)

    # ── 2. Parse CSVs individuais → situacao_titulo, fase, municipio ──────────
    log.info("sicop.parse.start", files=len(downloaded))
    t0 = time.time()
    prazos = parse_scm_prazos(downloaded, uf_filter=uf)
    log.info("sicop.parse.done", processos=len(prazos),
             elapsed_s=round(time.time() - t0, 1))

    # ── 3. (opcional) Microdados → dt_requerimento, dt_validade, dt_outorga, nup
    if microdados:
        log.info("sicop.microdados.fase",
                 note="baixando microdados completos para extrair datas administrativas")
        micro_zip = download_microdados(data_dir, skip=skip_download)
        if micro_zip:
            micro_prazos = parse_microdados_prazos(micro_zip, uf_filter=uf)
            log.info("sicop.microdados.merge", processos_micro=len(micro_prazos))
            # Merge: microdados têm prioridade para datas; CSVs individuais para situacao_titulo
            for key, mdata in micro_prazos.items():
                existing = prazos.get(key)
                if existing is None:
                    prazos[key] = mdata
                else:
                    # Datas dos microdados prevalecem (mais confiáveis)
                    for date_field in ("dt_requerimento", "dt_validade", "dt_outorga", "nup"):
                        if mdata.get(date_field) and not existing.get(date_field):
                            existing[date_field] = mdata[date_field]
                    # Situacao do CSV individual prevalece (mais legível que dos microdados)
                    if not existing.get("situacao_titulo") and mdata.get("situacao_titulo"):
                        existing["situacao_titulo"] = mdata["situacao_titulo"]
            log.info("sicop.merged.total", processos=len(prazos))
        else:
            log.warning("sicop.microdados.skip",
                        msg="Microdados não disponíveis — dt_validade/dt_outorga/nup não serão preenchidos")

    if not prazos:
        log.error("sicop.empty_prazos", msg="Nenhum processo encontrado nos CSVs.")
        raise SystemExit(1)

    # ── 4. Bulk update ────────────────────────────────────────────────────────
    log.info("sicop.update.start", dry_run=dry_run)
    t1 = time.time()
    ok, errs = bulk_update(os_client, prazos, batch_size=batch_size, dry_run=dry_run)
    elapsed = round(time.time() - t1, 1)

    log.info(
        "sicop.update.done",
        atualizados=ok,
        erros=errs,
        elapsed_s=elapsed,
        docs_per_sec=round(ok / max(elapsed, 1)),
    )

    # ── 5. Relatório de cobertura pós-enriquecimento ──────────────────────────
    if not dry_run:
        log.info("sicop.cobertura.pos_enriquecimento")
        relatorio_cobertura(os_client)

    # ── 6. Alertas de prazo ───────────────────────────────────────────────────
    if dias_alerta > 0 and not dry_run:
        relatorio_vencimentos(os_client, dias_alerta)

    if errs:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
