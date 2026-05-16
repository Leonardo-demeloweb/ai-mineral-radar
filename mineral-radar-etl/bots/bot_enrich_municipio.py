"""
bot_enrich_municipio.py — Enriquece o campo `municipio` e `regiao` em mr_jazidas_v001

Estratégia (spatial join, sem Postgres):
  1. Baixa malha de municípios do IBGE via geobr (5.570 municípios, polígonos WGS84)
  2. Faz scroll em mr_jazidas_v001 buscando docs com `location` definido
  3. Spatial join (centroide do processo × polígono do município) via GeoPandas
  4. Bulk update dos campos `municipio`, `municipio_cod` e `regiao` no OpenSearch

O campo `municipio` já está no mapeamento como `text` + `keyword`,
permitindo tanto busca full-text quanto filtros/agregações exatas.

Uso:
  python -m bots.bot_enrich_municipio                   # enriquece todos (sem municipio)
  python -m bots.bot_enrich_municipio --force           # re-enriquece todos (mesmo os que já têm)
  python -m bots.bot_enrich_municipio --dry-run         # testa sem escrever
  python -m bots.bot_enrich_municipio --uf MG           # só uma UF
  python -m bots.bot_enrich_municipio --year 2023       # ano da malha IBGE (padrão: 2022)
"""
from __future__ import annotations

import time
from typing import Iterator

import click
import geopandas as gpd
import pandas as pd
from opensearchpy import OpenSearch, helpers
from shapely.geometry import Point

from bots.bot_anm_direto import get_os_client
from bots.common.logging import get_logger

log = get_logger(__name__)

INDEX_NAME = "mr_jazidas_v001"
SCROLL_TTL = "5m"
DEFAULT_BATCH = 500

# Mapeamento UF → Região do Brasil
UF_TO_REGIAO: dict[str, str] = {
    "AC": "Norte",  "AM": "Norte",  "AP": "Norte",  "PA": "Norte",
    "RO": "Norte",  "RR": "Norte",  "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste",
    "PB": "Nordeste", "PE": "Nordeste", "PI": "Nordeste", "RN": "Nordeste",
    "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MS": "Centro-Oeste", "MT": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}


# ─────────────────────────────────────────────────────────────────────────────
# IBGE — download da malha de municípios via geobr
# ─────────────────────────────────────────────────────────────────────────────

def load_municipios(year: int, uf_filter: str | None = None) -> gpd.GeoDataFrame:
    """
    Baixa/usa cache local da malha de municípios do IBGE.
    Retorna GeoDataFrame com colunas: cod_mun, name_mun, abbrev_state, geometry.
    """
    import geobr  # lazy import — só necessário aqui

    log.info("ibge.loading", year=year, uf=uf_filter or "BR")
    if uf_filter:
        gdf = geobr.read_municipality(code_muni=uf_filter, year=year, simplified=False)
    else:
        gdf = geobr.read_municipality(code_muni="all", year=year, simplified=False)

    log.info("ibge.loaded", municipios=len(gdf), crs=str(gdf.crs))

    # Garante WGS84
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    # Normaliza colunas
    gdf = gdf.rename(columns={
        "code_muni":    "cod_mun",
        "name_muni":    "name_mun",
        "abbrev_state": "abbrev_state",
    })

    return gdf[["cod_mun", "name_mun", "abbrev_state", "geometry"]]


# ─────────────────────────────────────────────────────────────────────────────
# Scroll OpenSearch
# ─────────────────────────────────────────────────────────────────────────────

def scroll_docs(
    client: OpenSearch,
    batch_size: int,
    force: bool,
    uf_filter: str | None,
) -> Iterator[list[dict]]:
    """
    Retorna batches de docs que precisam de enriquecimento de município.
    Se force=False, só docs onde municipio está ausente.
    """
    must: list[dict] = [{"exists": {"field": "location"}}]
    if uf_filter:
        must.append({"term": {"uf": uf_filter.upper()}})

    if not force:
        # Só docs sem município ou com municipio vazio/null
        must.append({
            "bool": {
                "should": [
                    {"bool": {"must_not": {"exists": {"field": "municipio"}}}},
                    {"term": {"municipio.keyword": ""}},
                ],
                "minimum_should_match": 1,
            }
        })

    query = {"bool": {"must": must}}

    resp = client.search(
        index=INDEX_NAME,
        scroll=SCROLL_TTL,
        size=batch_size,
        body={
            "_source": ["numero_processo", "location", "uf"],
            "query": query,
        },
    )

    scroll_id = resp["_scroll_id"]
    hits = resp["hits"]["hits"]
    total = resp["hits"]["total"]["value"]
    log.info("scroll.start", total_docs=total, force=force, uf=uf_filter or "all")

    processed = 0
    while hits:
        batch = [
            {
                "_id":    h["_id"],
                "loc":    h["_source"].get("location"),
                "uf":     h["_source"].get("uf", ""),
            }
            for h in hits
            if h["_source"].get("location")
        ]
        processed += len(hits)

        if batch:
            yield batch

        log.info("scroll.progress", processed=processed, total=total)

        resp = client.scroll(scroll_id=scroll_id, scroll=SCROLL_TTL)
        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]

    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Spatial join
# ─────────────────────────────────────────────────────────────────────────────

def join_municipios(docs: list[dict], muni_gdf: gpd.GeoDataFrame) -> list[dict]:
    """
    Recebe lista de docs com {_id, loc: {lat, lon}, uf} e o GeoDataFrame de municípios.
    Retorna lista de {_id, municipio, municipio_cod, regiao}.
    """
    # Monta GeoDataFrame de pontos
    records = []
    for d in docs:
        loc = d["loc"]
        if not loc:
            continue
        lat = loc.get("lat") or loc.get("lat")
        lon = loc.get("lon") or loc.get("lon")
        if lat is None or lon is None:
            continue
        records.append({"_id": d["_id"], "uf": d["uf"], "geometry": Point(lon, lat)})

    if not records:
        return []

    pts_gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")

    # Spatial join: ponto dentro do polígono municipal
    joined = gpd.sjoin(
        pts_gdf,
        muni_gdf[["cod_mun", "name_mun", "abbrev_state", "geometry"]],
        how="left",
        predicate="within",
    )

    results = []
    for _, row in joined.iterrows():
        uf = row.get("abbrev_state") or row.get("uf") or ""
        municipio = row.get("name_mun")
        municipio_cod = str(int(row["cod_mun"])) if pd.notna(row.get("cod_mun")) else None
        regiao = UF_TO_REGIAO.get(str(uf).upper())

        if municipio and pd.notna(municipio):
            results.append({
                "_id":           row["_id"],
                "municipio":     str(municipio),
                "municipio_cod": municipio_cod,
                "regiao":        regiao,
            })
        elif regiao:
            # Sem município encontrado mas UF é conhecida — atualiza pelo menos a região
            results.append({
                "_id":           row["_id"],
                "municipio":     None,
                "municipio_cod": None,
                "regiao":        regiao,
            })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Bulk update
# ─────────────────────────────────────────────────────────────────────────────

def bulk_update_municipios(client: OpenSearch, updates: list[dict]) -> tuple[int, int]:
    """Bulk update apenas dos campos municipio/municipio_cod/regiao."""
    actions = []
    for u in updates:
        doc_fields: dict = {}
        if u.get("municipio"):
            doc_fields["municipio"] = u["municipio"]
        if u.get("municipio_cod"):
            doc_fields["municipio_cod"] = u["municipio_cod"]
        if u.get("regiao"):
            doc_fields["regiao"] = u["regiao"]
        if doc_fields:
            actions.append({
                "_op_type": "update",
                "_index":   INDEX_NAME,
                "_id":      u["_id"],
                "doc":      doc_fields,
            })

    if not actions:
        return 0, 0

    ok, errs = helpers.bulk(client, actions, raise_on_error=False, chunk_size=200)
    return ok, len(errs) if isinstance(errs, list) else errs


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--uf", default=None, help="Filtrar por UF (ex: MG). Padrão: todas.")
@click.option("--year", default=2022, show_default=True, help="Ano da malha IBGE")
@click.option("--batch-size", default=DEFAULT_BATCH, show_default=True)
@click.option("--force", is_flag=True, help="Re-enriquecer mesmo docs que já têm municipio")
@click.option("--dry-run", is_flag=True, help="Executa o join mas não escreve no OpenSearch")
def main(
    uf: str | None,
    year: int,
    batch_size: int,
    force: bool,
    dry_run: bool,
) -> None:
    """Enriquece municipio + regiao nos processos ANM via spatial join com malha IBGE."""
    os_client = get_os_client()

    # Carrega malha de municípios do IBGE
    muni_gdf = load_municipios(year=year, uf_filter=uf)
    log.info("municipios.ready", total=len(muni_gdf))

    total_updated = total_errors = total_no_match = 0
    t0 = time.time()

    for scroll_batch in scroll_docs(os_client, batch_size=batch_size, force=force, uf_filter=uf):
        updates = join_municipios(scroll_batch, muni_gdf)

        matched    = sum(1 for u in updates if u.get("municipio"))
        no_match   = len(scroll_batch) - matched
        total_no_match += no_match

        if dry_run:
            log.info(
                "dry_run.batch",
                batch=len(scroll_batch),
                matched=matched,
                no_match=no_match,
                sample=updates[:2] if updates else [],
            )
            continue

        if updates:
            ok, errs = bulk_update_municipios(os_client, updates)
            total_updated += ok
            total_errors  += errs
            log.info(
                "batch.updated",
                updated=ok,
                errors=errs,
                matched=matched,
                no_match=no_match,
                total_updated=total_updated,
            )

    elapsed = round(time.time() - t0, 1)
    log.info(
        "bot_enrich_municipio.done",
        total_updated=total_updated,
        total_errors=total_errors,
        total_no_match=total_no_match,
        elapsed_s=elapsed,
    )

    if dry_run:
        click.echo(f"\nDry-run concluído em {elapsed}s. Sem escrita no índice.")

    if total_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
