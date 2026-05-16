"""
bot_funai.py — Ingestão das Terras Indígenas FUNAI

Fonte: gov.br/funai — Shapefile/GeoJSON mensal
Atualiza raw_funai_ti e recalcula sobreposições (staging_restricoes_geo)
via PostGIS ST_Intersects.

Uso:
  python -m bots.bot_funai --url <URL_DO_SHAPEFILE>
  python -m bots.bot_funai --arquivo /caminho/local/ti.zip
"""
from __future__ import annotations

import time
from pathlib import Path

import click
import geopandas as gpd

from bots.common.db import get_conn, start_run, finish_run
from bots.common.hashing import hash_record
from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

# URL oficial FUNAI (verificar atualização mensal)
FUNAI_DEFAULT_URL = "https://geoserver.funai.gov.br/geoserver/Funai/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=Funai:tis_poligonais&outputFormat=SHAPE-ZIP"


@click.command()
@click.option("--url", default=FUNAI_DEFAULT_URL, show_default=True)
@click.option("--arquivo", type=click.Path(exists=True), default=None,
              help="Arquivo local .zip ou .shp (pula download)")
@click.option("--recalcular-sobreposicoes", is_flag=True, default=True,
              help="Recalcula staging_restricoes_geo após ingestão")
def main(url: str, arquivo: str | None, recalcular_sobreposicoes: bool) -> None:
    """Bot de ingestão FUNAI Terras Indígenas → raw_funai_ti."""
    data_dir = settings.etl_data_dir / "funai"
    data_dir.mkdir(parents=True, exist_ok=True)

    run_id = start_run("bot_funai", source_file=url)
    t0 = time.time()

    try:
        if arquivo:
            zip_path = Path(arquivo)
        else:
            import httpx
            zip_path = data_dir / "funai_ti.zip"
            log.info("funai.download.start", url=url)
            with httpx.stream("GET", url, headers={"User-Agent": settings.anm_user_agent},
                              timeout=180, follow_redirects=True) as resp:
                resp.raise_for_status()
                with open(zip_path, "wb") as f:
                    for chunk in resp.iter_bytes(1024 * 256):
                        f.write(chunk)
            log.info("funai.download.done")

        gdf = gpd.read_file(zip_path)
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)

        log.info("funai.parsed", rows=len(gdf))
        inserted = updated = 0

        with get_conn() as conn:
            for _, row in gdf.iterrows():
                record = {
                    "id_ti":           str(row.get("gid") or row.get("id") or row.get("co_cr")),
                    "nome_ti":         row.get("no_ti") or row.get("nome"),
                    "etnia":           row.get("ds_cr") or row.get("etnia"),
                    "municipio":       row.get("no_municipio") or row.get("municipio"),
                    "uf":              row.get("sg_uf") or row.get("uf"),
                    "fase_demarcacao": row.get("ds_fases_ti") or row.get("fase"),
                    "modalidade":      row.get("ds_modalidade"),
                    "area_ha":         row.get("nu_area_ha"),
                    "perimetro_km":    row.get("nu_perimetro_km"),
                    "dt_homologacao":  row.get("dt_homologacao"),
                    "dt_portaria":     row.get("dt_portaria"),
                    "geom_wkt":        row.geometry.wkt if row.geometry else None,
                    "source_file":     zip_path.name,
                }
                if not record["id_ti"]:
                    continue

                new_hash = hash_record(record)
                existing = conn.execute(
                    "SELECT hash FROM raw_funai_ti WHERE id_ti = %s", (record["id_ti"],)
                ).fetchone()

                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO raw_funai_ti
                            (id_ti, nome_ti, etnia, municipio, uf, fase_demarcacao,
                             modalidade, area_ha, perimetro_km, dt_homologacao,
                             dt_portaria, geom, hash, source_file)
                        VALUES
                            (%(id_ti)s, %(nome_ti)s, %(etnia)s, %(municipio)s, %(uf)s,
                             %(fase_demarcacao)s, %(modalidade)s, %(area_ha)s, %(perimetro_km)s,
                             %(dt_homologacao)s, %(dt_portaria)s,
                             ST_GeomFromText(%(geom_wkt)s, 4326), %(hash)s, %(source_file)s)
                        """,
                        {**record, "hash": new_hash},
                    )
                    inserted += 1
                elif existing["hash"] != new_hash:
                    conn.execute(
                        """
                        UPDATE raw_funai_ti SET
                            nome_ti = %(nome_ti)s, etnia = %(etnia)s,
                            fase_demarcacao = %(fase_demarcacao)s, area_ha = %(area_ha)s,
                            geom = ST_GeomFromText(%(geom_wkt)s, 4326),
                            hash = %(hash)s, ingested_at = NOW()
                        WHERE id_ti = %(id_ti)s
                        """,
                        {**record, "hash": new_hash},
                    )
                    updated += 1

            conn.commit()

        log.info("funai.upsert.done", inserted=inserted, updated=updated)

        # ── Recalcular sobreposições PostGIS ──────────────────────────────
        if recalcular_sobreposicoes:
            _calcular_sobreposicoes_ti()

        finish_run(
            run_id, status="success",
            docs_processed=len(gdf), docs_inserted=inserted, docs_updated=updated,
            duration_s=round(time.time() - t0, 2),
        )

    except Exception as exc:
        finish_run(run_id, status="error", error_message=str(exc))
        log.error("bot_funai.error", error=str(exc))
        raise


def _calcular_sobreposicoes_ti() -> None:
    """
    Calcula sobreposição ANM × Terras Indígenas via PostGIS.
    Atualiza staging_restricoes_geo.
    """
    log.info("sobreposicoes_ti.start")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO staging_restricoes_geo
                (numero_processo, tipo_restricao, id_restricao, nome_restricao,
                 area_processo_ha, area_sobreposta_ha, pct_processo_sobreposto, updated_at)
            SELECT
                p.numero_processo,
                'terra_indigena'                            AS tipo_restricao,
                ti.id_ti                                    AS id_restricao,
                ti.nome_ti                                  AS nome_restricao,
                p.area_ha                                   AS area_processo_ha,
                ST_Area(
                    ST_Intersection(p.geom, ti.geom)::geography
                ) / 10000.0                                 AS area_sobreposta_ha,
                ROUND(
                    100.0 * ST_Area(ST_Intersection(p.geom, ti.geom)::geography)
                    / NULLIF(ST_Area(p.geom::geography), 0),
                    2
                )                                           AS pct_processo_sobreposto,
                NOW()
            FROM raw_anm_shapes p
            JOIN raw_funai_ti ti ON ST_Intersects(p.geom, ti.geom)
            WHERE p.geom IS NOT NULL AND ti.geom IS NOT NULL
            ON CONFLICT (numero_processo, tipo_restricao, id_restricao)
            DO UPDATE SET
                area_sobreposta_ha     = EXCLUDED.area_sobreposta_ha,
                pct_processo_sobreposto = EXCLUDED.pct_processo_sobreposto,
                updated_at             = NOW()
            """
        )
        conn.commit()
    log.info("sobreposicoes_ti.done")


if __name__ == "__main__":
    main()
