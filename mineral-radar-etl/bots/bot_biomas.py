"""
bot_biomas.py — IBGE Biomas → OpenSearch (mr_biomas_v001)

Fluxo:
  1. Download do shapefile de Biomas do IBGE GeoFTP
  2. Parse com GeoPandas + reprojeção para WGS84
  3. Indexa os 6 biomas brasileiros com geo_shape (polígono) + geo_point (centróide)
  4. (Opcional) --enrich-jazidas: spatial join para adicionar campo `bioma`
     em todos os documentos de mr_jazidas_v001

Os 6 biomas:
  Amazônia · Caatinga · Cerrado · Mata Atlântica · Pampa · Pantanal

Uso:
  python -m bots.bot_biomas                     # indexa os 6 biomas
  python -m bots.bot_biomas --skip-download      # reusar shapefile já baixado
  python -m bots.bot_biomas --enrich-jazidas     # enriquece mr_jazidas_v001 com campo bioma
  python -m bots.bot_biomas --enrich-only        # só enriquece (sem baixar/indexar)
  python -m bots.bot_biomas --dry-run            # não escreve no OpenSearch
"""
from __future__ import annotations

import time
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import click
import geopandas as gpd
import httpx
from opensearchpy import OpenSearch, helpers
from shapely.geometry import mapping

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_BIOMAS  = "mr_biomas_v001"
INDEX_JAZIDAS = "mr_jazidas_v001"

# Biomas são polígonos grandes e complexos — simplificação maior é adequada (~500m)
GEOMETRY_SIMPLIFY_TOLERANCE = 0.005

HEADERS = {"User-Agent": settings.anm_user_agent}

# IBGE GeoFTP — diretório "vetores" (verificado em maio/2026)
# Tenta 250mil (mais preciso), fallback para 5000mil (menor)
IBGE_URLS = [
    "https://geoftp.ibge.gov.br/informacoes_ambientais/estudos_ambientais/biomas/vetores/Biomas_250mil.zip",
    "https://geoftp.ibge.gov.br/informacoes_ambientais/estudos_ambientais/biomas/vetores/Biomas_5000mil.zip",
]

# Mapeamento normalizado → slug canônico (ID no OpenSearch)
BIOMA_SLUG: dict[str, str] = {
    "amazônia":      "amazonia",
    "amazonia":      "amazonia",
    "caatinga":      "caatinga",
    "cerrado":       "cerrado",
    "mata atlântica": "mata_atlantica",
    "mata atlantica": "mata_atlantica",
    "pampa":         "pampa",
    "pantanal":      "pantanal",
}

# Nome canônico para exibição
BIOMA_NOME: dict[str, str] = {
    "amazonia":      "Amazônia",
    "caatinga":      "Caatinga",
    "cerrado":       "Cerrado",
    "mata_atlantica": "Mata Atlântica",
    "pampa":         "Pampa",
    "pantanal":      "Pantanal",
}


# ─────────────────────────────────────────────────────────────────────────────
# OpenSearch client
# ─────────────────────────────────────────────────────────────────────────────

def get_os_client(timeout: int = 120) -> OpenSearch:
    """Cliente OpenSearch com timeout configurável para bulk de geo_shape."""
    use_ssl = settings.opensearch_url.startswith("https")
    kwargs: dict = {
        "hosts": [settings.opensearch_url],
        "use_ssl": use_ssl,
        "verify_certs": False,
        "timeout": timeout,
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
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase sem acentos."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _bioma_slug(nome: str) -> str | None:
    """Retorna o slug canônico do bioma ou None se não reconhecido."""
    return BIOMA_SLUG.get(_normalize(nome.strip()))


# ─────────────────────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────────────────────

def download_shapefile(data_dir: Path, skip: bool) -> Path:
    """
    Baixa o ZIP do shapefile de Biomas do IBGE.
    Tenta escala 250mil, fallback 500mil. Retorna path do ZIP local.
    """
    data_dir.mkdir(parents=True, exist_ok=True)

    for url in IBGE_URLS:
        fname = url.rsplit("/", 1)[-1]
        dest = data_dir / fname

        if skip and dest.exists():
            log.info("biomas.download.skip", file=fname,
                     size_mb=round(dest.stat().st_size / 1e6, 1))
            return dest

        log.info("biomas.download.start", url=url)
        try:
            with httpx.stream("GET", url, headers=HEADERS, timeout=300,
                              follow_redirects=True) as resp:
                if resp.status_code == 404:
                    log.warning("biomas.download.not_found", url=url)
                    continue
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    written = 0
                    for chunk in resp.iter_bytes(chunk_size=512 * 1024):
                        f.write(chunk)
                        written += len(chunk)
            log.info("biomas.download.done", file=fname,
                     size_mb=round(dest.stat().st_size / 1e6, 1))
            return dest
        except Exception as exc:
            log.warning("biomas.download.error", url=url, error=str(exc)[:200])
            if dest.exists():
                dest.unlink()

    raise RuntimeError("Não foi possível baixar o shapefile de Biomas do IBGE.")


# ─────────────────────────────────────────────────────────────────────────────
# Parse shapefile
# ─────────────────────────────────────────────────────────────────────────────

def load_biomas_gdf(zip_path: Path) -> gpd.GeoDataFrame:
    """
    Carrega o shapefile de biomas e reprojecta para WGS84.
    Retorna GeoDataFrame com colunas normalizadas (NM_BIOMA, CD_BIOMA).
    """
    log.info("biomas.parse.start", zip=zip_path.name)

    with zipfile.ZipFile(zip_path) as zf:
        shp_files = [f for f in zf.namelist() if f.lower().endswith(".shp")]
        if not shp_files:
            raise ValueError(f"Nenhum .shp encontrado em {zip_path}")
        extract_dir = zip_path.parent / zip_path.stem
        extract_dir.mkdir(exist_ok=True)
        zf.extractall(extract_dir)

    gdf = gpd.read_file(extract_dir / shp_files[0])
    log.info("biomas.parse.loaded",
             rows=len(gdf), crs=str(gdf.crs), cols=list(gdf.columns))

    if gdf.crs and gdf.crs.to_epsg() != 4326:
        log.info("biomas.parse.reproject", from_epsg=gdf.crs.to_epsg())
        gdf = gdf.to_crs(epsg=4326)

    # Normalizar colunas para UPPERCASE
    gdf.columns = [c.upper() if c.lower() != "geometry" else c for c in gdf.columns]

    # Detectar coluna de nome do bioma (NM_BIOMA, BIOMA, NM_BIOMA1, etc.)
    nm_col = next(
        (c for c in gdf.columns
         if "NM_BIOMA" in c or c == "BIOMA" or c == "NM_BIOMA1"),
        None,
    )
    cd_col = next((c for c in gdf.columns if "CD_BIOMA" in c), None)

    log.info("biomas.parse.cols_detected", nm=nm_col, cd=cd_col,
             all_cols=list(gdf.columns))

    if not nm_col:
        raise ValueError(
            f"Coluna de nome do bioma não encontrada. Colunas: {list(gdf.columns)}"
        )

    gdf = gdf.rename(columns={nm_col: "NM_BIOMA"})
    if cd_col:
        gdf = gdf.rename(columns={cd_col: "CD_BIOMA"})

    log.info("biomas.parse.done", rows=len(gdf))
    return gdf


# ─────────────────────────────────────────────────────────────────────────────
# Converter row → documento OpenSearch
# ─────────────────────────────────────────────────────────────────────────────

def row_to_doc(row) -> dict | None:
    """Converte uma linha do GeoDataFrame em documento para mr_biomas_v001."""
    nm_bioma = str(row.get("NM_BIOMA", "") or "").strip()
    if not nm_bioma:
        return None

    slug = _bioma_slug(nm_bioma)
    if slug is None:
        log.warning("biomas.row.unrecognized", nm_bioma=nm_bioma)
        return None

    nome_canonico = BIOMA_NOME.get(slug, nm_bioma)
    cd_bioma = str(row.get("CD_BIOMA", "") or "").strip()

    geom = row.get("geometry")
    if geom is None or geom.is_empty:
        return None

    centroid = geom.centroid
    if not geom.contains(centroid):
        centroid = geom.representative_point()

    try:
        geom_simple = geom.simplify(GEOMETRY_SIMPLIFY_TOLERANCE, preserve_topology=True)
        if geom_simple.is_empty:
            geom_simple = geom
        poligono_geojson = mapping(geom_simple)
    except Exception:
        try:
            poligono_geojson = mapping(geom)
        except Exception:
            return None

    return {
        "slug":             slug,
        "nome":             nome_canonico,
        "nome_normalizado": _normalize(nome_canonico),
        "codigo":           cd_bioma,
        "area_km2":         round(geom.area * (111.32 ** 2), 0) if geom else None,
        "centroide": {
            "lat": round(centroid.y, 6),
            "lon": round(centroid.x, 6),
        },
        "poligono":         poligono_geojson,
        "fonte":            "IBGE",
        "indexed_at":       datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Indexação
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_index(client: OpenSearch) -> None:
    """Cria mr_biomas_v001 se não existir."""
    if client.indices.exists(index=INDEX_BIOMAS):
        log.info("biomas.index.exists")
        return

    setup_path = (Path(__file__).parent.parent.parent
                  / "backend" / "scripts" / "setup_indices.py")
    mapping_body = None
    try:
        ns: dict = {}
        src = setup_path.read_text()
        exec(compile(src, str(setup_path), "exec"), ns)  # noqa: S102
        mapping_body = ns.get("MR_BIOMAS")
    except Exception as exc:
        log.warning("biomas.index.mapping_fallback", error=str(exc)[:200])

    if mapping_body is None:
        mapping_body = {
            "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
            "mappings": {
                "properties": {
                    "slug":             {"type": "keyword"},
                    "nome":             {"type": "text",
                                        "fields": {"keyword": {"type": "keyword"}}},
                    "nome_normalizado": {"type": "keyword"},
                    "codigo":           {"type": "keyword"},
                    "area_km2":         {"type": "double"},
                    "centroide":        {"type": "geo_point"},
                    "poligono":         {"type": "geo_shape"},
                    "fonte":            {"type": "keyword"},
                    "indexed_at":       {"type": "date"},
                }
            },
        }

    client.indices.create(index=INDEX_BIOMAS, body=mapping_body)
    log.info("biomas.index.created")


def iter_actions(gdf: gpd.GeoDataFrame) -> Iterator[dict]:
    """Gera actions de bulk index a partir do GeoDataFrame."""
    ok = skipped = 0
    for _, row in gdf.iterrows():
        doc = row_to_doc(row)
        if doc is None:
            skipped += 1
            continue
        ok += 1
        yield {
            "_index":  INDEX_BIOMAS,
            "_id":     doc["slug"],
            "_source": doc,
        }
    log.info("biomas.iter.done", ok=ok, skipped=skipped)


def bulk_index(client: OpenSearch, gdf: gpd.GeoDataFrame) -> None:
    """Faz bulk index dos biomas (apenas 6 documentos)."""
    actions = list(iter_actions(gdf))
    if not actions:
        log.warning("biomas.bulk.empty")
        return
    ok, errs = helpers.bulk(client, actions, raise_on_error=False)
    errs_n = len(errs) if isinstance(errs, list) else errs
    log.info("biomas.bulk.done", total_ok=ok, total_err=errs_n)


# ─────────────────────────────────────────────────────────────────────────────
# Enriquecimento de mr_jazidas_v001
# ─────────────────────────────────────────────────────────────────────────────

def enrich_jazidas_bioma(
    client: OpenSearch,
    gdf: gpd.GeoDataFrame,
    batch_size: int,
    dry_run: bool,
) -> None:
    """
    Adiciona campo `bioma` em mr_jazidas_v001 via spatial join (ponto-em-polígono).

    Estratégia:
      1. Carrega todos os location (lat/lon) de mr_jazidas_v001 via Scroll
      2. Converte para GeoDataFrame de pontos
      3. Faz sjoin contra os 6 polígonos de biomas (within)
      4. Bulk update dos documentos com campo `bioma` (slug canônico)

    Nota: os polígonos de bioma cobrem ~98% do território continental.
    Jazidas offshore ou em áreas de fronteira podem não ter bioma atribuído.
    """
    log.info("enrich_jazidas_bioma.start")

    # ── 1. Preparar GeoDataFrame dos biomas para o sjoin ──
    import pandas as pd

    docs_bioma = []
    for _, row in gdf.iterrows():
        doc = row_to_doc(row)
        if doc:
            docs_bioma.append({"slug": doc["slug"], "geometry": row.geometry})

    gdf_biomas = gpd.GeoDataFrame(docs_bioma, crs="EPSG:4326")
    log.info("enrich_jazidas_bioma.biomas_loaded", n=len(gdf_biomas))

    # ── 2. Scroll todos os locations de mr_jazidas_v001 ──
    scroll_size = 2000
    resp = client.search(
        index=INDEX_JAZIDAS,
        scroll="10m",
        size=scroll_size,
        body={
            "_source": ["location"],
            "query": {"exists": {"field": "location"}},
        },
    )
    scroll_id = resp["_scroll_id"]
    hits = resp["hits"]["hits"]
    total = resp["hits"]["total"]["value"]
    log.info("enrich_jazidas_bioma.scroll.start", total=total)

    all_hits: list[dict] = []
    while hits:
        all_hits.extend(hits)
        resp = client.scroll(scroll_id=scroll_id, scroll="10m")
        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]
        if len(all_hits) % 100_000 < scroll_size:
            log.info("enrich_jazidas_bioma.scroll.progress", loaded=len(all_hits))

    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    log.info("enrich_jazidas_bioma.loaded", total=len(all_hits))

    # ── 3. GeoDataFrame de pontos ──
    from shapely.geometry import Point

    rows = []
    for h in all_hits:
        src = h.get("_source", {})
        loc = src.get("location") or {}
        lat = loc.get("lat")
        lon = loc.get("lon")
        if lat is not None and lon is not None:
            rows.append({"_id": h["_id"], "geometry": Point(float(lon), float(lat))})

    if not rows:
        log.warning("enrich_jazidas_bioma.no_points")
        return

    gdf_pts = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    log.info("enrich_jazidas_bioma.points_built", n=len(gdf_pts))

    # ── 4. Spatial join ──
    log.info("enrich_jazidas_bioma.sjoin.start",
             n_points=len(gdf_pts), n_biomas=len(gdf_biomas))

    gdf_joined = gpd.sjoin(
        gdf_pts,
        gdf_biomas[["slug", "geometry"]],
        how="left",
        predicate="within",
    )

    matched = gdf_joined["slug"].notna().sum()
    log.info("enrich_jazidas_bioma.sjoin.done",
             matched=int(matched), total=len(gdf_joined),
             pct=round(matched / len(gdf_joined) * 100, 1))

    if dry_run:
        sample = gdf_joined[gdf_joined["slug"].notna()].head(5)
        for _, r in sample.iterrows():
            log.info("dry_run.sample", id=r["_id"], bioma=r["slug"])
        log.info("dry_run.enrich_bioma", would_update=int(matched))
        return

    # ── 5. Garante campo no mapping ──
    try:
        client.indices.put_mapping(
            index=INDEX_JAZIDAS,
            body={"properties": {"bioma": {"type": "keyword"}}},
        )
    except Exception as exc:
        log.warning("enrich_jazidas_bioma.mapping_skip", error=str(exc)[:100])

    # ── 6. Bulk update ──
    actions = []
    total_ok = total_err = 0
    for _, row in gdf_joined.iterrows():
        slug = row.get("slug")
        if pd.isna(slug) or not slug:
            continue
        actions.append({
            "_op_type": "update",
            "_index":   INDEX_JAZIDAS,
            "_id":      row["_id"],
            "doc":      {"bioma": str(slug)},
        })
        if len(actions) >= batch_size:
            ok, errs = helpers.bulk(client, actions, raise_on_error=False)
            total_ok  += ok
            total_err += len(errs) if isinstance(errs, list) else errs
            log.info("enrich_jazidas_bioma.update.progress",
                     ok=total_ok, errs=total_err)
            actions = []

    if actions:
        ok, errs = helpers.bulk(client, actions, raise_on_error=False)
        total_ok  += ok
        total_err += len(errs) if isinstance(errs, list) else errs

    log.info("enrich_jazidas_bioma.done",
             matched=int(matched), updated=total_ok, errors=total_err)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--skip-download", is_flag=True,
              help="Reusar shapefile já baixado (pula download)")
@click.option("--enrich-jazidas", is_flag=True,
              help="Após indexar, faz spatial join para adicionar campo bioma em mr_jazidas_v001")
@click.option("--enrich-only", is_flag=True,
              help="Só faz o enriquecimento (pula download e indexação)")
@click.option("--batch-size", default=500, show_default=True,
              help="Documentos por batch no bulk update do enriquecimento")
@click.option("--dry-run", is_flag=True,
              help="Não escreve no OpenSearch")
def main(
    skip_download: bool,
    enrich_jazidas: bool,
    enrich_only: bool,
    batch_size: int,
    dry_run: bool,
) -> None:
    """IBGE Biomas → mr_biomas_v001 (+ campo bioma em mr_jazidas_v001)."""
    t0 = time.time()
    client = get_os_client()
    data_dir = settings.etl_data_dir / "biomas"

    if not enrich_only:
        zip_path = download_shapefile(data_dir, skip=skip_download)
        gdf = load_biomas_gdf(zip_path)

        log.info("biomas.summary", total=len(gdf),
                 nomes=list(gdf["NM_BIOMA"].unique()))

        if dry_run:
            for _, row in gdf.iterrows():
                d = row_to_doc(row)
                if d:
                    log.info("dry_run.sample", slug=d["slug"], nome=d["nome"],
                             area_km2=d["area_km2"])
            log.info("dry_run.biomas", would_index=len(gdf))
        else:
            _ensure_index(client)
            bulk_index(client, gdf)
    else:
        zip_path = download_shapefile(data_dir, skip=True)
        gdf = load_biomas_gdf(zip_path)

    if enrich_jazidas or enrich_only:
        log.info("--- enriquecimento mr_jazidas_v001 ---")
        enrich_jazidas_bioma(client, gdf, batch_size, dry_run)

    log.info("bot_biomas.done",
             elapsed_s=round(time.time() - t0, 1), dry_run=dry_run)


if __name__ == "__main__":
    main()
