"""
bot_cfem.py — Ingestão da CFEM → OpenSearch (mr_cfem_v001)

Fonte: https://dadosabertos.anm.gov.br/CFEM/CFEM_Arrecadacao.csv  (~221 MB, ~3M linhas)
       Atualizado diariamente pela ANM.

Fluxo:
  1. Download do CSV (streaming, salva em disco — pulado se já existe)
  2. Parse lazy com Polars (eficiente para 3M linhas)
  3. Bulk index → mr_cfem_v001 (1 doc por linha do CSV)
  4. (opcional) --enrich-jazidas: agrega por processo e atualiza mr_jazidas_v001.cfem

Uso:
  python -m bots.bot_cfem
  python -m bots.bot_cfem --ano 2024           # apenas 1 ano
  python -m bots.bot_cfem --dry-run            # conta sem indexar
  python -m bots.bot_cfem --force-download     # re-baixa mesmo se já existe
  python -m bots.bot_cfem --enrich-jazidas     # atualiza mr_jazidas_v001 com totais CFEM
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import click
import polars as pl
from opensearchpy import OpenSearch, helpers

from bots.bot_sicop import _opensearch_id_candidates
from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_CFEM    = "mr_cfem_v001"
INDEX_JAZIDAS = "mr_jazidas_v001"
BATCH_SIZE    = 2_000    # docs por bulk request
CFEM_URL      = f"{settings.anm_base_url}/CFEM/CFEM_Arrecadacao.csv"


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
# Download
# ─────────────────────────────────────────────────────────────────────────────

def download_cfem(url: str, dest: Path, force: bool) -> Path:
    if dest.exists() and not force:
        size_mb = round(dest.stat().st_size / 1e6, 1)
        log.info("cfem.download.skip", path=str(dest), size_mb=size_mb)
        return dest

    log.info("cfem.download.start", url=url)
    import httpx
    t0 = time.time()
    with httpx.stream(
        "GET", url,
        headers={"User-Agent": settings.anm_user_agent},
        timeout=600,
        follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(512 * 1024):
                f.write(chunk)

    size_mb = round(dest.stat().st_size / 1e6, 1)
    log.info("cfem.download.done", size_mb=size_mb, elapsed_s=round(time.time() - t0, 1))
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# Normalização
# ─────────────────────────────────────────────────────────────────────────────

def _to_float(val: object) -> float | None:
    """Converte string BR (vírgula decimal) ou número para float."""
    if val is None:
        return None
    s = str(val).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_processo(v: str | None) -> str:
    """Normaliza número de processo: remove espaços, mantém barras."""
    if not v:
        return ""
    return str(v).strip().replace(" ", "")


def _doc_id(ano: int, mes: int, processo: str, cnpj_basico: str, substancia: str) -> str:
    """ID determinístico para upsert idempotente."""
    raw = f"{ano}|{mes}|{processo}|{cnpj_basico}|{substancia}".lower()
    return hashlib.md5(raw.encode()).hexdigest()


def _competencia(ano: int, mes: int) -> str | None:
    try:
        return datetime(ano, mes, 1).strftime("%Y-%m-%d")
    except Exception:
        return None


def _build_doc(row: dict) -> dict | None:
    """Converte uma linha do CSV CFEM em documento OpenSearch.

    Suporta dois formatos do CSV ANM:
      - Antigo (separador ;): ANO, MES, PROCESSO, CNPJ, SUBSTANCIA, VALOR, QUANTIDADE, UNIDADE
      - Novo   (separador ,): Ano, Mês, Processo, CPF_CNPJ, Substância, ValorRecolhido,
                              QuantidadeComercializada, UnidadeDeMedida
    """
    try:
        ano = int(row.get("Ano") or row.get("ANO") or 0)
        mes = int(row.get("Mês") or row.get("MES") or row.get("M\u00eas") or 0)
        if ano < 2000 or mes < 1 or mes > 12:
            return None

        processo_num = _normalize_processo(row.get("Processo") or row.get("PROCESSO"))
        ano_proc     = str(row.get("AnoDoProcesso") or "").strip()
        # Reconstrói formato completo "910262/2002" quando disponível
        processo = f"{processo_num}/{ano_proc}" if ano_proc and "/" not in processo_num else processo_num
        cnpj_raw = str(row.get("CPF_CNPJ") or row.get("CNPJ") or "").strip()
        # Remove formatação (pontos, barras, traços) e pega os 8 primeiros dígitos
        cnpj_digits = "".join(c for c in cnpj_raw if c.isdigit())
        cnpj_basico = cnpj_digits[:8] if len(cnpj_digits) >= 8 else cnpj_digits or None
        substancia = str(row.get("Substância") or row.get("Subst\u00e2ncia") or row.get("SUBSTANCIA") or "").strip().upper()
        valor = _to_float(row.get("ValorRecolhido") or row.get("VALOR"))
        qtde  = _to_float(row.get("QuantidadeComercializada") or row.get("QUANTIDADE"))
        uf    = str(row.get("UF") or "").strip().upper() or None
        mun   = str(row.get("Município") or row.get("Munic\u00edpio") or row.get("MUNICIPIO") or "").strip() or None
        unid  = str(row.get("UnidadeDeMedida") or row.get("UNIDADE") or "").strip() or None

        return {
            "numero_processo":  processo,
            "cnpj_basico":      cnpj_basico or None,
            "ano":              ano,
            "mes":              mes,
            "competencia":      _competencia(ano, mes),
            "valor_arrecadado": valor,
            "quantidade":       qtde,
            "unidade_medida":   unid,
            "substancia":       substancia or None,
            "substancia_desc":  substancia or None,
            "municipio":        mun,
            "uf":               uf,
            "indexed_at":       datetime.now(timezone.utc).isoformat(),
            # _id interno para a action de bulk
            "_id": _doc_id(ano, mes, processo, cnpj_basico, substancia),
        }
    except Exception as e:
        log.debug("doc.skip", error=str(e))
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Iterador de batches via Polars lazy
# ─────────────────────────────────────────────────────────────────────────────

def _detect_separator(csv_path: Path) -> str:
    """Detecta separador lendo as primeiras linhas do CSV."""
    with open(csv_path, "rb") as f:
        sample = f.read(2048).decode("latin1", errors="replace")
    first_line = sample.splitlines()[0] if sample else ""
    return ";" if first_line.count(";") > first_line.count(",") else ","


def _read_cfem_df(csv_path: Path, ano_filtro: int | None) -> pl.DataFrame:
    """
    Lê o CSV CFEM completo com Polars read_csv (suporta latin1).
    Auto-detecta separador (o formato ANM mudou de ';' para ',' em 2024).
    Polars lê ~300MB em ~5s mantendo memória eficiente.
    """
    sep = _detect_separator(csv_path)
    log.info("cfem.csv.separator", separator=repr(sep))

    df = pl.read_csv(
        csv_path,
        separator=sep,
        encoding="latin1",
        infer_schema_length=5_000,
        ignore_errors=True,
        # Manter valores monetários e quantidades como string para lidar com
        # separador decimal brasileiro (vírgula) sem conversão automática errada
        schema_overrides={
            # Formato novo (separador ,)
            "ValorRecolhido":           pl.Utf8,
            "QuantidadeComercializada": pl.Utf8,
            # Formato antigo (separador ;)
            "VALOR":      pl.Utf8,
            "QUANTIDADE": pl.Utf8,
            "ALIQUOTA":   pl.Utf8,
        },
    )

    # Normaliza nomes de coluna para comparação ao filtrar por ano
    cols = df.columns
    ano_col = next((c for c in cols if c.lower() in ("ano", "ano")), None)
    if ano_filtro and ano_col:
        df = df.filter(pl.col(ano_col) == ano_filtro)

    return df


def iter_batches(csv_path: Path, ano_filtro: int | None, batch_size: int) -> Iterator[list[dict]]:
    """
    Lê o CSV CFEM e emite batches de docs OpenSearch prontos para bulk index.
    """
    df = _read_cfem_df(csv_path, ano_filtro)
    log.info("cfem.parsed", rows=len(df), colunas=df.columns)

    n_rows = len(df)
    for offset in range(0, n_rows, batch_size):
        chunk = df.slice(offset, batch_size)
        docs = []
        for row in chunk.iter_rows(named=True):
            doc = _build_doc(row)
            if doc:
                docs.append(doc)
        if docs:
            yield docs


# ─────────────────────────────────────────────────────────────────────────────
# Bulk index
# ─────────────────────────────────────────────────────────────────────────────

def bulk_index(client: OpenSearch, docs: list[dict]) -> tuple[int, int]:
    actions = [
        {
            "_index":  INDEX_CFEM,
            "_id":     d.pop("_id"),
            "_source": d,
        }
        for d in docs
    ]
    ok, errs = helpers.bulk(client, actions, raise_on_error=False, chunk_size=500)
    n_err = len(errs) if isinstance(errs, list) else int(errs)
    return ok, n_err


# ─────────────────────────────────────────────────────────────────────────────
# Enriquecimento mr_jazidas_v001 com agregados CFEM
# ─────────────────────────────────────────────────────────────────────────────

def enrich_jazidas_cfem(client: OpenSearch, csv_path: Path) -> None:
    """
    Agrega CFEM por numero_processo e atualiza o campo cfem em mr_jazidas_v001.
    Executa após a indexação completa no mr_cfem_v001.
    """
    log.info("enrich_jazidas.start")

    df_raw = _read_cfem_df(csv_path, ano_filtro=None)
    cols = df_raw.columns

    # Detecta nomes de coluna conforme o formato do CSV (antigo ou novo)
    col_processo     = next((c for c in cols if c.lower() == "processo"), "PROCESSO")
    # AnoDoProcesso → contém "ano" E "processo" no nome (independente de separadores)
    col_ano_processo = next(
        (c for c in cols if "ano" in c.lower() and "processo" in c.lower()), None
    )
    col_valor        = next((c for c in cols if "valor" in c.lower()), "VALOR")
    col_ano          = next((c for c in cols if c.lower() == "ano"), "ANO")
    col_mes          = next((c for c in cols if c.lower() in ("mês", "mes", "m\u00eas")), "MES")

    # Reconstrói o número completo do processo: "910262" + "2002" → "910262/2002"
    # para corresponder ao _id do mr_jazidas_v001.
    # Formato novo do CSV tem "AnoDoProcesso"; formato antigo já inclui o ano no "PROCESSO".
    if col_ano_processo and col_ano_processo in cols:
        df_raw = df_raw.with_columns([
            (
                pl.col(col_processo).cast(pl.Utf8).str.strip_chars()
                + pl.lit("/")
                + pl.col(col_ano_processo).cast(pl.Utf8).str.strip_chars()
            ).alias("_processo_full")
        ])
        col_processo_full = "_processo_full"
    else:
        col_processo_full = col_processo

    # Converte VALOR (string BR) → float no Polars
    df = (
        df_raw.lazy().with_columns([
            pl.col(col_valor)
              .str.replace_all(r"\.", "")
              .str.replace(",", ".")
              .cast(pl.Float64, strict=False)
              .alias("valor_num"),
        ])
        .group_by(col_processo_full)
        .agg([
            pl.col("valor_num").sum().alias("total_historico"),
            pl.col(col_ano).filter(pl.col("valor_num") > 0).max().alias("ultimo_ano"),
            pl.col(col_ano).filter(pl.col("valor_num") > 0).n_unique().alias("anos_producao"),
            pl.col(col_ano).max().alias("ano_max"),
            pl.col(col_mes).sort_by(col_ano).last().alias("mes_max"),
        ])
        .collect()
    )
    log.info("enrich_jazidas.aggregated", processos=len(df))

    # Bulk update por processo
    actions = []
    for row in df.iter_rows(named=True):
        processo = _normalize_processo(row.get(col_processo_full) or row.get(col_processo))
        if not processo:
            continue

        ano_max = row.get("ano_max")
        mes_max = row.get("mes_max")
        ultima = _competencia(int(ano_max), int(mes_max)) if ano_max and mes_max else None

        cfem_doc = {
            "total_historico":    round(float(row["total_historico"] or 0), 2),
            "ultimo_ano":         float(row["ultimo_ano"]) if row["ultimo_ano"] else None,
            "anos_producao":      int(row["anos_producao"] or 0),
            "ultima_arrecadacao": ultima,
        }
        doc_ids = _opensearch_id_candidates(processo)
        if not doc_ids:
            continue
        for doc_id in doc_ids:
            actions.append({
                "_op_type": "update",
                "_index":   INDEX_JAZIDAS,
                "_id":      doc_id,
                "doc":      {"cfem": cfem_doc},
                "doc_as_upsert": False,
            })

        if len(actions) >= 1_000:
            ok, errs = helpers.bulk(client, actions, raise_on_error=False)
            log.info("enrich_jazidas.batch", ok=ok, errs=errs)
            actions.clear()

    if actions:
        ok, errs = helpers.bulk(client, actions, raise_on_error=False)
        log.info("enrich_jazidas.batch", ok=ok, errs=errs)

    log.info("enrich_jazidas.done", processos=len(df))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--ano",             type=int,  default=None,  help="Filtrar por ano específico")
@click.option("--url",             default=CFEM_URL, show_default=True, help="URL do CSV CFEM")
@click.option("--batch-size",      default=BATCH_SIZE, show_default=True, help="Docs por bulk request")
@click.option("--dry-run",         is_flag=True,  help="Conta registros sem indexar")
@click.option("--force-download",  is_flag=True,  help="Re-baixa mesmo se CSV já existe")
@click.option("--enrich-jazidas",  is_flag=True,  help="Atualiza mr_jazidas_v001 com totais CFEM após indexação")
@click.option("--enrich-only",     is_flag=True,  help="Pula indexação; só atualiza mr_jazidas_v001 (requer CSV já baixado)")
def main(
    ano: int | None,
    url: str,
    batch_size: int,
    dry_run: bool,
    force_download: bool,
    enrich_jazidas: bool,
    enrich_only: bool,
) -> None:
    """Baixa CFEM_Arrecadacao.csv ANM e indexa em mr_cfem_v001."""
    data_dir = settings.etl_data_dir / "cfem"
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / "CFEM_Arrecadacao.csv"

    # ── Download ──────────────────────────────────────────────────────────────
    csv_path = download_cfem(url, dest, force=force_download)

    if dry_run:
        log.info("dry_run.count_start")
        df = _read_cfem_df(csv_path, ano_filtro=ano)
        log.info("dry_run.result", total_linhas=len(df), colunas=df.columns, ano_filtro=ano)
        return

    os_client = get_os_client()

    # ── Modo enrich-only: pula indexação ──────────────────────────────────────
    if enrich_only:
        log.info("enrich_only.mode")
        enrich_jazidas_cfem(os_client, csv_path)
        return

    # ── Indexação ─────────────────────────────────────────────────────────────
    if not os_client.indices.exists(index=INDEX_CFEM):
        log.error("index.missing", index=INDEX_CFEM,
                  msg="Execute setup_indices.py primeiro")
        raise SystemExit(1)

    t0 = time.time()
    total_ok = total_errs = total_batches = 0

    log.info("cfem.index.start", index=INDEX_CFEM, ano=ano, batch_size=batch_size)

    for batch in iter_batches(csv_path, ano_filtro=ano, batch_size=batch_size):
        ok, errs = bulk_index(os_client, batch)
        total_ok    += ok
        total_errs  += errs
        total_batches += 1

        if total_batches % 50 == 0:
            elapsed = round(time.time() - t0, 1)
            docs_s  = round(total_ok / elapsed, 0) if elapsed > 0 else 0
            log.info(
                "cfem.progress",
                batch=total_batches,
                indexed=total_ok,
                errors=total_errs,
                elapsed_s=elapsed,
                docs_per_s=docs_s,
            )

    elapsed = round(time.time() - t0, 1)
    log.info(
        "cfem.index.done",
        indexed=total_ok,
        errors=total_errs,
        elapsed_s=elapsed,
        docs_per_s=round(total_ok / elapsed, 0) if elapsed > 0 else 0,
    )

    # ── Enriquecimento mr_jazidas_v001 ────────────────────────────────────────
    if enrich_jazidas:
        enrich_jazidas_cfem(os_client, csv_path)


if __name__ == "__main__":
    main()
