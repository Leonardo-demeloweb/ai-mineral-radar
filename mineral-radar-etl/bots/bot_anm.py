"""
bot_anm.py — Ingestão dos processos minerários ANM (SIGMINE)

Fluxo:
  1. Download dos ZIPs de Shapefiles por UF (ou BRASIL.zip + PROCESSOS_INATIVOS.zip)
  2. Unzip + parse com GeoPandas
  3. Reprojeção para WGS84 (EPSG:4326) se necessário
  4. Upsert em raw_anm_shapes (hash diferencial)
  5. Atualiza etl_run_log

Uso:
  python -m bots.bot_anm --uf AC          # só o Acre (teste)
  python -m bots.bot_anm --all-ativos     # BRASIL.zip (~123MB)
  python -m bots.bot_anm --inativos       # PROCESSOS_INATIVOS.zip (~150MB)
  python -m bots.bot_anm --uf AC --uf AM  # múltiplos estados
"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from typing import Generator

import click
import geopandas as gpd
import httpx
import psycopg
from tenacity import retry, stop_after_attempt, wait_exponential

from bots.common.db import get_conn, start_run, finish_run
from bots.common.hashing import hash_record
from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

ANM_SIGMINE_BASE = f"{settings.anm_base_url}/SIGMINE/PROCESSOS_MINERARIOS"
HEADERS = {"User-Agent": settings.anm_user_agent}

# UFs válidas para download individual
ALL_UFS = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO",
    "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR",
    "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
]


# ─────────────────────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=30))
def download_zip(url: str, dest: Path) -> Path:
    """Faz download de um ZIP da ANM com retry. Retorna o caminho do arquivo."""
    log.info("download.start", url=url)
    with httpx.stream("GET", url, headers=HEADERS, timeout=120, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                f.write(chunk)
    log.info("download.done", url=url, size_mb=round(dest.stat().st_size / 1e6, 1))
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# Parse
# ─────────────────────────────────────────────────────────────────────────────

def parse_shapefile(zip_path: Path, ativo: bool) -> gpd.GeoDataFrame:
    """
    Extrai e lê o Shapefile do ZIP da ANM.
    Reprojetar para WGS84 (EPSG:4326) se necessário.
    """
    with zipfile.ZipFile(zip_path) as zf:
        shp_files = [f for f in zf.namelist() if f.endswith(".shp")]
        if not shp_files:
            raise ValueError(f"Nenhum .shp encontrado em {zip_path}")

        # extrai para diretório temporário
        extract_dir = zip_path.parent / zip_path.stem
        zf.extractall(extract_dir)

    gdf = gpd.read_file(extract_dir / shp_files[0])

    # reprojeção para WGS84
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        log.info("reproject", from_crs=str(gdf.crs), to_crs="EPSG:4326")
        gdf = gdf.to_crs(epsg=4326)

    gdf["ativo"] = ativo
    return gdf


# ─────────────────────────────────────────────────────────────────────────────
# Upsert
# ─────────────────────────────────────────────────────────────────────────────

def upsert_shapes(gdf: gpd.GeoDataFrame, source_file: str) -> tuple[int, int, int]:
    """
    Upsert de um GeoDataFrame em raw_anm_shapes.
    Retorna: (docs_processed, docs_inserted, docs_updated)
    """
    from shapely import to_geojson
    import json

    inserted = updated = 0

    # Mapeamento de colunas SIGMINE → schema raw_anm_shapes
    # (os nomes reais dos campos do Shapefile ANM devem ser confirmados no bot_anm real)
    col_map = {
        "PROCESSO": "numero_processo",
        "UF":       "uf",
        "NOME":     "nome",
        "FASE":     "fase",
        "SUB":      "sub",
        "AREA_HA":  "area_ha",
        "ANO":      "ano",
        "ULT_EVEN": "ultimo_evento",
        "PRIORIDAD": "prioridade",
        "USO":      "uso_solo",
        "SUBS":     "subs_desc",
    }

    with get_conn() as conn:
        for _, row in gdf.iterrows():
            record: dict = {}
            for shp_col, db_col in col_map.items():
                if shp_col in gdf.columns:
                    record[db_col] = row.get(shp_col)

            record["ativo"] = row["ativo"]
            record["geom_wkt"] = row.geometry.wkt if row.geometry else None
            record["source_file"] = source_file

            if not record.get("numero_processo"):
                continue

            new_hash = hash_record(record)
            record["hash"] = new_hash

            existing = conn.execute(
                "SELECT hash FROM raw_anm_shapes WHERE numero_processo = %s",
                (record["numero_processo"],),
            ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO raw_anm_shapes
                        (numero_processo, uf, nome, fase, sub, area_ha, ano,
                         ultimo_evento, prioridade, uso_solo, subs_desc,
                         ativo, geom, hash, source_file)
                    VALUES
                        (%(numero_processo)s, %(uf)s, %(nome)s, %(fase)s, %(sub)s,
                         %(area_ha)s, %(ano)s, %(ultimo_evento)s, %(prioridade)s,
                         %(uso_solo)s, %(subs_desc)s, %(ativo)s,
                         ST_GeomFromText(%(geom_wkt)s, 4326),
                         %(hash)s, %(source_file)s)
                    """,
                    record,
                )
                inserted += 1
            elif existing["hash"] != new_hash:
                conn.execute(
                    """
                    UPDATE raw_anm_shapes SET
                        uf = %(uf)s, nome = %(nome)s, fase = %(fase)s, sub = %(sub)s,
                        area_ha = %(area_ha)s, ano = %(ano)s, ultimo_evento = %(ultimo_evento)s,
                        prioridade = %(prioridade)s, uso_solo = %(uso_solo)s,
                        subs_desc = %(subs_desc)s, ativo = %(ativo)s,
                        geom = ST_GeomFromText(%(geom_wkt)s, 4326),
                        hash = %(hash)s, source_file = %(source_file)s,
                        ingested_at = NOW()
                    WHERE numero_processo = %(numero_processo)s
                    """,
                    record,
                )
                updated += 1

        conn.commit()

    return len(gdf), inserted, updated


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--uf", multiple=True, help="UF(s) para download individual (ex: --uf AC --uf PA)")
@click.option("--all-ativos", is_flag=True, help="Baixar BRASIL.zip (todos os ativos)")
@click.option("--inativos", is_flag=True, help="Baixar PROCESSOS_INATIVOS.zip")
def main(uf: tuple[str, ...], all_ativos: bool, inativos: bool) -> None:
    """Bot de ingestão SIGMINE ANM → raw_anm_shapes."""
    data_dir = settings.etl_data_dir / "anm_shapes"
    data_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[str, bool]] = []  # (url, ativo)

    if all_ativos:
        jobs.append((f"{ANM_SIGMINE_BASE}/BRASIL.zip", True))
    if inativos:
        jobs.append((f"{ANM_SIGMINE_BASE}/PROCESSOS_INATIVOS.zip", False))
    for u in uf:
        jobs.append((f"{ANM_SIGMINE_BASE}/{u.upper()}.zip", True))

    if not jobs:
        click.echo("Especifique --uf UF, --all-ativos ou --inativos.")
        return

    total_proc = total_ins = total_upd = 0

    for url, ativo in jobs:
        fname = url.split("/")[-1]
        zip_path = data_dir / fname
        run_id = start_run("bot_anm", source_file=url)
        t0 = time.time()

        try:
            download_zip(url, zip_path)
            gdf = parse_shapefile(zip_path, ativo=ativo)
            log.info("shapefile.parsed", rows=len(gdf), ativo=ativo)

            proc, ins, upd = upsert_shapes(gdf, source_file=fname)
            total_proc += proc
            total_ins += ins
            total_upd += upd

            finish_run(
                run_id,
                status="success",
                docs_processed=proc,
                docs_inserted=ins,
                docs_updated=upd,
                duration_s=round(time.time() - t0, 2),
            )
            log.info("run.success", source=fname, inserted=ins, updated=upd)

        except Exception as exc:
            finish_run(run_id, status="error", error_message=str(exc))
            log.error("run.error", source=fname, error=str(exc))
            raise

    log.info(
        "bot_anm.done",
        total_processed=total_proc,
        total_inserted=total_ins,
        total_updated=total_upd,
    )


if __name__ == "__main__":
    main()
