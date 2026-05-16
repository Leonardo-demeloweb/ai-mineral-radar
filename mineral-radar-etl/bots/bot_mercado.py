"""
bot_mercado.py — ComexStat/MDIC → mr_mercado_v001
==================================================

Indexa dados de comércio exterior (exportação e importação) filtrados para
os capítulos NCM do domínio mineral:
  - Cap 25-27 : produtos minerais (sal, enxofre, minérios, combustíveis)
  - Cap 28    : produtos químicos inorgânicos (potassa, ácidos, etc.)
  - Cap 71    : pérolas, pedras preciosas, metais preciosos
  - Cap 72-81 : metais comuns e obras primárias (aço, cobre, níquel, alumínio…)

Granularidade do doc: (ncm × fluxo × uf × ano)
  _id = "{ncm}_{fluxo}_{uf}_{ano}"   ex: "26011100_export_MG_2024"

Fonte de dados (CSV abertos sem autenticação):
  https://balanca.economia.gov.br/balanca/bd/comexstat-bd/ncm/EXP_YYYY.csv
  https://balanca.economia.gov.br/balanca/bd/comexstat-bd/ncm/IMP_YYYY.csv
  https://balanca.economia.gov.br/balanca/bd/tabelas/NCM.csv
  https://balanca.economia.gov.br/balanca/bd/tabelas/PAIS.csv

Uso:
  python -m bots.bot_mercado --all              # download + index
  python -m bots.bot_mercado --download         # só baixa CSVs
  python -m bots.bot_mercado --index            # indexa do cache local
  python -m bots.bot_mercado --years 2022 2023  # anos específicos
  python -m bots.bot_mercado --dry-run          # sem gravar no OpenSearch
"""
from __future__ import annotations

import gc
import io
import time
from datetime import datetime, timezone
from pathlib import Path

import click
import httpx
import polars as pl
from opensearchpy import OpenSearch, helpers

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX = "mr_mercado_v001"
BATCH_INDEX = 800

BASE_URL    = "https://balanca.economia.gov.br/balanca/bd/comexstat-bd/ncm"
TABLES_URL  = "https://balanca.economia.gov.br/balanca/bd/tabelas"
CACHE_DIR   = Path(settings.etl_data_dir) / "mercado"

# Capítulos NCM do domínio mineral (primeiros 2 dígitos do NCM de 8 chars)
CAPITULOS_MINERAIS = {
    "25", "26", "27",            # produtos minerais + combustíveis
    "28",                        # químicos inorgânicos (potassa, ácidos…)
    "71",                        # metais preciosos, pedras preciosas
    "72", "74", "75", "76",      # ferro/aço, cobre, níquel, alumínio
    "78", "79", "80", "81",      # chumbo, zinco, estanho, outros metais
}

# Descrições dos capítulos (para enrichment do doc)
CAPITULO_DESC = {
    "25": "Sal, enxofre, terras, pedras, gesso, cal e cimento",
    "26": "Minérios, escórias e cinzas",
    "27": "Combustíveis minerais, óleos e ceras minerais",
    "28": "Produtos químicos inorgânicos",
    "71": "Pérolas, pedras preciosas, metais preciosos",
    "72": "Ferro fundido, ferro e aço",
    "74": "Cobre e suas obras",
    "75": "Níquel e suas obras",
    "76": "Alumínio e suas obras",
    "78": "Chumbo e suas obras",
    "79": "Zinco e suas obras",
    "80": "Estanho e suas obras",
    "81": "Outros metais comuns",
}

DEFAULT_YEARS = list(range(2019, 2026))   # 2019-2025


# ─────────────────────────────────────────────────────────────────────────────
# Tabelas auxiliares
# ─────────────────────────────────────────────────────────────────────────────

def _download_bytes(url: str) -> bytes:
    with httpx.Client(timeout=120, verify=False, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content


def load_ncm_table(cache_dir: Path) -> dict[str, dict]:
    """Retorna {ncm_code: {desc, capitulo, secao}} filtrando apenas minerais."""
    path = cache_dir / "NCM.csv"
    if not path.exists():
        log.info("mercado.ncm_table.download")
        path.write_bytes(_download_bytes(f"{TABLES_URL}/NCM.csv"))
        log.info("mercado.ncm_table.saved", path=str(path))

    df = pl.read_csv(
        path,
        separator=";",
        encoding="latin1",
        quote_char='"',
        infer_schema_length=0,
    )
    ncm_map: dict[str, dict] = {}
    for row in df.iter_rows(named=True):
        ncm = row["CO_NCM"].strip()
        cap = ncm[:2]
        if cap in CAPITULOS_MINERAIS:
            ncm_map[ncm] = {
                "desc":    row.get("NO_NCM_POR", "").strip(),
                "capitulo": cap,
            }
    log.info("mercado.ncm_table.loaded", total_mineral=len(ncm_map))
    return ncm_map


def load_pais_table(cache_dir: Path) -> dict[str, str]:
    """Retorna {co_pais: nome_pais}."""
    path = cache_dir / "PAIS.csv"
    if not path.exists():
        log.info("mercado.pais_table.download")
        path.write_bytes(_download_bytes(f"{TABLES_URL}/PAIS.csv"))

    df = pl.read_csv(
        path,
        separator=";",
        encoding="latin1",
        quote_char='"',
        infer_schema_length=0,
    )
    return {
        row["CO_PAIS"].strip(): row["NO_PAIS"].strip()
        for row in df.iter_rows(named=True)
    }


# ─────────────────────────────────────────────────────────────────────────────
# Download dos CSVs anuais
# ─────────────────────────────────────────────────────────────────────────────

def download_year(fluxo: str, year: int, cache_dir: Path, force: bool = False) -> Path:
    """Baixa o CSV anual EXP/IMP para o cache local."""
    prefix = "EXP" if fluxo == "export" else "IMP"
    filename = f"{prefix}_{year}.csv"
    path = cache_dir / filename
    if path.exists() and not force:
        log.info("mercado.download.skip_cached", file=filename)
        return path

    url = f"{BASE_URL}/{filename}"
    log.info("mercado.download.start", url=url)
    t0 = time.time()
    with httpx.Client(timeout=300, verify=False, follow_redirects=True) as c:
        with c.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            path.write_bytes(resp.read())
    elapsed = time.time() - t0
    size_mb = path.stat().st_size / 1e6
    log.info("mercado.download.done", file=filename,
             size_mb=round(size_mb, 1), elapsed_s=round(elapsed, 1))
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Parse e agregação
# ─────────────────────────────────────────────────────────────────────────────

def parse_and_aggregate(
    csv_path: Path,
    fluxo: str,
    ncm_map: dict[str, dict],
    pais_map: dict[str, str],
    year: int,
) -> list[dict]:
    """
    Lê um CSV anual, filtra NCMs minerais e agrega por (ncm, uf, ano).
    Retorna lista de documentos prontos para indexação.
    """
    log.info("mercado.parse.start", file=csv_path.name, fluxo=fluxo)

    is_imp = (fluxo == "import")
    schema_overrides = {
        "CO_NCM":    pl.Utf8,    # manter como string para filtro por prefixo
        "CO_PAIS":   pl.Utf8,
        "SG_UF_NCM": pl.Utf8,
        "CO_ANO":    pl.Int32,
        "CO_MES":    pl.Int8,
        "QT_ESTAT":  pl.Int64,
        "KG_LIQUIDO":pl.Int64,
        "VL_FOB":    pl.Int64,
    }
    if is_imp:
        schema_overrides["VL_FRETE"]  = pl.Int64
        schema_overrides["VL_SEGURO"] = pl.Int64

    df = pl.read_csv(
        csv_path,
        separator=";",
        encoding="latin1",
        quote_char='"',
        schema_overrides=schema_overrides,
        null_values=["", "NA"],
    )

    mineral_ncms = list(ncm_map.keys())
    df = df.filter(pl.col("CO_NCM").is_in(mineral_ncms))

    if df.is_empty():
        log.warning("mercado.parse.no_mineral_rows", file=csv_path.name)
        return []

    # Agrega totais por (NCM, UF, ANO)
    agg_cols = [
        pl.col("VL_FOB").sum().alias("vl_fob_usd"),
        pl.col("KG_LIQUIDO").sum().alias("kg_liquido"),
        pl.col("QT_ESTAT").sum().alias("qt_estat"),
        pl.col("CO_MES").n_unique().alias("n_meses"),
        pl.len().alias("n_operacoes"),
    ]
    if is_imp:
        agg_cols += [
            pl.col("VL_FRETE").sum().alias("vl_frete_usd"),
            pl.col("VL_SEGURO").sum().alias("vl_seguro_usd"),
        ]

    totals = (
        df.group_by(["CO_NCM", "SG_UF_NCM", "CO_ANO"])
        .agg(agg_cols)
    )

    # Top 10 países por VL_FOB para cada (NCM, UF, ANO)
    top_paises_df = (
        df.group_by(["CO_NCM", "SG_UF_NCM", "CO_ANO", "CO_PAIS"])
        .agg(pl.col("VL_FOB").sum().alias("vl_pais"))
        .sort(["CO_NCM", "SG_UF_NCM", "CO_ANO", "vl_pais"], descending=[False, False, False, True])
        .group_by(["CO_NCM", "SG_UF_NCM", "CO_ANO"])
        .agg([
            pl.col("CO_PAIS").head(10).alias("top_paises_cod"),
            pl.col("vl_pais").head(10).alias("top_paises_vl"),
        ])
    )

    merged = totals.join(top_paises_df, on=["CO_NCM", "SG_UF_NCM", "CO_ANO"], how="left")

    docs: list[dict] = []
    for row in merged.iter_rows(named=True):
        ncm_code = str(row["CO_NCM"]).strip()
        uf       = str(row["SG_UF_NCM"]).strip()
        ano      = int(row["CO_ANO"])
        ncm_meta = ncm_map.get(ncm_code, {})
        cap      = ncm_code[:2]

        # Resolve nomes dos países
        pais_cods = row.get("top_paises_cod") or []
        pais_vls  = row.get("top_paises_vl") or []
        top_nomes = [pais_map.get(str(c).strip(), str(c).strip()) for c in pais_cods]

        doc: dict = {
            "ncm":               ncm_code,
            "fluxo":             fluxo,
            "uf":                uf,
            "ano":               ano,
            "ncm_desc":          ncm_meta.get("desc", ""),
            "ncm_capitulo":      cap,
            "ncm_capitulo_desc": CAPITULO_DESC.get(cap, ""),
            "ncm_secao":         None,
            "vl_fob_usd":        float(row.get("vl_fob_usd") or 0),
            "kg_liquido":        float(row.get("kg_liquido") or 0),
            "qt_estat":          float(row.get("qt_estat") or 0),
            "vl_frete_usd":      float(row.get("vl_frete_usd") or 0) if is_imp else None,
            "vl_seguro_usd":     float(row.get("vl_seguro_usd") or 0) if is_imp else None,
            "vl_cif_usd":        None,
            "n_meses":           int(row.get("n_meses") or 0),
            "n_operacoes":       int(row.get("n_operacoes") or 0),
            "top_paises":        top_nomes,
            "top_paises_cod":    [str(c).strip() for c in pais_cods],
            "top_paises_vl_fob": [float(v) for v in pais_vls],
            "indexed_at":        datetime.now(timezone.utc).isoformat(),
        }
        # CIF = FOB + frete + seguro
        if is_imp:
            doc["vl_cif_usd"] = (
                doc["vl_fob_usd"]
                + (doc["vl_frete_usd"] or 0)
                + (doc["vl_seguro_usd"] or 0)
            )

        docs.append(doc)

    log.info("mercado.parse.done", file=csv_path.name, docs=len(docs))
    return docs


# ─────────────────────────────────────────────────────────────────────────────
# Indexação
# ─────────────────────────────────────────────────────────────────────────────

def bulk_index(client: OpenSearch, docs: list[dict], dry_run: bool = False) -> dict:
    if not docs:
        return {"indexed": 0, "errors": 0}

    actions = []
    for doc in docs:
        _id = f"{doc['ncm']}_{doc['fluxo']}_{doc['uf']}_{doc['ano']}"
        actions.append({
            "_op_type": "index",
            "_index":   INDEX,
            "_id":      _id,
            "_source":  doc,
        })

    if dry_run:
        log.info("mercado.bulk_index.dry_run", count=len(actions))
        return {"indexed": len(actions), "errors": 0}

    ok, errs = helpers.bulk(client, actions, raise_on_error=False, chunk_size=BATCH_INDEX)
    err_count = len(errs) if isinstance(errs, list) else errs
    if err_count:
        log.warning("mercado.bulk_index.errors", errors=err_count)
    return {"indexed": ok, "errors": err_count}


# ─────────────────────────────────────────────────────────────────────────────
# Resumo
# ─────────────────────────────────────────────────────────────────────────────

def log_summary(client: OpenSearch) -> None:
    client.indices.refresh(index=INDEX)
    r = client.search(index=INDEX, body={
        "size": 0,
        "track_total_hits": True,
        "aggs": {
            "por_fluxo":   {"terms": {"field": "fluxo"}},
            "por_capitulo":{"terms": {"field": "ncm_capitulo", "size": 20}},
            "por_ano":     {"terms": {"field": "ano", "size": 10, "order": {"_key": "desc"}}},
            "total_exp":   {"filter": {"term": {"fluxo": "export"}},
                            "aggs": {"vl": {"sum": {"field": "vl_fob_usd"}}}},
            "total_imp":   {"filter": {"term": {"fluxo": "import"}},
                            "aggs": {"vl": {"sum": {"field": "vl_fob_usd"}}}},
        },
    })
    agg = r["aggregations"]
    total_docs = r["hits"]["total"]["value"]
    log.info("mercado.summary.total", docs=total_docs)
    log.info("mercado.summary.por_fluxo",
             dist={b["key"]: b["doc_count"] for b in agg["por_fluxo"]["buckets"]})
    log.info("mercado.summary.por_capitulo",
             dist={b["key"]: b["doc_count"] for b in agg["por_capitulo"]["buckets"]})
    log.info("mercado.summary.por_ano",
             dist={b["key"]: b["doc_count"] for b in agg["por_ano"]["buckets"]})

    exp_bi = agg["total_exp"]["vl"]["value"] / 1e9
    imp_bi = agg["total_imp"]["vl"]["value"] / 1e9
    log.info("mercado.summary.valor_total",
             exportacao_bi_usd=round(exp_bi, 1),
             importacao_bi_usd=round(imp_bi, 1))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--download",  is_flag=True, help="Baixa CSVs anuais para o cache.")
@click.option("--index",     is_flag=True, help="Processa cache local e indexa no OpenSearch.")
@click.option("--all",       "run_all", is_flag=True, help="--download + --index.")
@click.option("--years",     multiple=True, type=int,
              help="Anos a processar (default: 2019-2025). Ex: --years 2023 --years 2024")
@click.option("--skip-download", is_flag=True,
              help="Pula download se o CSV já existir no cache (default: sim).")
@click.option("--force-download", is_flag=True,
              help="Força re-download mesmo se o CSV já existir no cache.")
@click.option("--dry-run",   is_flag=True, help="Sem gravação no OpenSearch.")
def main(
    download: bool,
    index: bool,
    run_all: bool,
    years: tuple[int, ...],
    skip_download: bool,
    force_download: bool,
    dry_run: bool,
) -> None:
    """Indexa dados ComexStat/MDIC de comércio mineral → mr_mercado_v001."""
    if run_all:
        download = index = True

    if not download and not index:
        click.echo("Especifique --download, --index ou --all.")
        raise SystemExit(1)

    target_years = list(years) if years else DEFAULT_YEARS
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    use_ssl = settings.opensearch_url.startswith("https")
    os_kwargs: dict = {
        "hosts":        [settings.opensearch_url],
        "use_ssl":      use_ssl,
        "verify_certs": False,
        "timeout":      120,
    }
    if settings.opensearch_user and settings.opensearch_pass:
        os_kwargs["http_auth"] = (settings.opensearch_user, settings.opensearch_pass)
    client = OpenSearch(**os_kwargs)

    t0 = time.time()
    ncm_map  = load_ncm_table(CACHE_DIR)
    pais_map = load_pais_table(CACHE_DIR)

    total_indexed = 0
    total_errors  = 0

    for year in target_years:
        for fluxo in ("export", "import"):
            # ── Download ──────────────────────────────────────────────────
            prefix  = "EXP" if fluxo == "export" else "IMP"
            csv_path = CACHE_DIR / f"{prefix}_{year}.csv"

            if download:
                download_year(fluxo, year, CACHE_DIR, force=force_download)

            if not index:
                continue

            if not csv_path.exists():
                log.warning("mercado.index.skip_missing_csv", path=str(csv_path))
                continue

            # ── Parse + Agrega ────────────────────────────────────────────
            docs = parse_and_aggregate(csv_path, fluxo, ncm_map, pais_map, year)

            # ── Indexa ────────────────────────────────────────────────────
            result = bulk_index(client, docs, dry_run=dry_run)
            total_indexed += result["indexed"]
            total_errors  += result["errors"]
            log.info("mercado.year_done",
                     year=year, fluxo=fluxo,
                     indexed=result["indexed"], errors=result["errors"])

            # libera memória antes do próximo arquivo
            del docs
            gc.collect()

    elapsed = round(time.time() - t0, 1)
    log.info("mercado.pipeline.done",
             total_indexed=total_indexed,
             total_errors=total_errors,
             elapsed_s=elapsed,
             dry_run=dry_run)

    if index and not dry_run and total_indexed > 0:
        log_summary(client)


if __name__ == "__main__":
    main()
