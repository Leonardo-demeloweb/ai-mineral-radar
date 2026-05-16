"""
bot_ucs.py — TerraBrasilis WFS → mr_ucs_v001
=============================================

Fluxo:
  1. Download das ~2.000 Unidades de Conservação via WFS TerraBrasilis (INPE)
     — dados por bioma (Amazônia, Cerrado, Caatinga, Mata Atlântica, Pampa, Pantanal)
     — deduplicação por id_cnuc para UCs que se estendem por mais de um bioma
  2. Simplifica polígonos, computa centróides e área_ha
  3. Indexa em mr_ucs_v001
  4. (Opcional) --enrich-jazidas: spatial join ponto-em-polígono para marcar
     quais processos ANM se sobrepõem a UCs → atualiza n_restricoes_uc e
     restricoes_geo em mr_jazidas_v001

Fonte: https://terrabrasilis.dpi.inpe.br/geoserver/ows (INPE/TerraBrasilis)
Layers: conservation_units_*_biome (6 camadas por bioma)

Uso:
  python -m bots.bot_ucs --index
  python -m bots.bot_ucs --enrich-jazidas
  python -m bots.bot_ucs --all
  python -m bots.bot_ucs --all --dry-run
  python -m bots.bot_ucs --skip-download --index
"""
from __future__ import annotations

import json
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import click
import geopandas as gpd
import httpx
import pandas as pd
from opensearchpy import OpenSearch, helpers
import shapely
from shapely.geometry import Point, mapping, shape

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_UC      = "mr_ucs_v001"
INDEX_JAZIDAS = "mr_jazidas_v001"

BATCH_SIZE  = 30    # polígonos grandes → batches menores
SCROLL_SIZE = 2000

# TerraBrasilis WFS base
TERRABRASILIS_WFS = "https://terrabrasilis.dpi.inpe.br/geoserver/ows"

# Camadas por bioma — exclui Legal Amazon (subconjunto da Amazônia)
BIOME_LAYERS = [
    "prodes-amazon-nb:conservation_units_amazon_biome",
    "prodes-cerrado-nb:conservation_units_cerrado_biome",
    "prodes-caatinga-nb:conservation_units_caatinga_biome",
    "prodes-mata-atlantica-nb:conservation_units_mata_atlantica_biome",
    "prodes-pampa-nb:conservation_units_pampa_biome",
    "prodes-pantanal-nb:conservation_units_pantanal_biome",
]

CACHE_FILE = "terrabrasilis_ucs.geojson"

# Tolerância de simplificação (~100 m em graus decimais)
GEOMETRY_SIMPLIFY_TOLERANCE = 0.001

# EPSG:5880 — Polyconic / Brazil (igual-área, para cálculo de área_ha)
EPSG_BRAZIL_AREA = 5880


# ─────────────────────────────────────────────────────────────────────────────
# OpenSearch client
# ─────────────────────────────────────────────────────────────────────────────

def get_os_client(timeout: int = 120) -> OpenSearch:
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
    nfkd = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _grupo_desc(grupo: str | None) -> str | None:
    """Converte código de grupo SNUC em descrição legível."""
    mapping_grupo = {
        "PI": "Proteção Integral",
        "US": "Uso Sustentável",
    }
    return mapping_grupo.get((grupo or "").upper().strip()) or grupo or None


def _ano_to_date(ano: str | None) -> str | None:
    """Converte ano (ex: '1972') em data ISO '1972-01-01'."""
    if not ano:
        return None
    try:
        y = int(str(ano).strip())
        if 1900 <= y <= 2100:
            return f"{y:04d}-01-01"
    except (ValueError, TypeError):
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Download WFS
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_biome_layer(layer: str, timeout: int = 120) -> list[dict]:
    """
    Baixa todas as features de uma camada WFS TerraBrasilis como GeoJSON.
    Retorna lista de features GeoJSON.
    """
    log.info("uc.download.layer.start", layer=layer)
    resp = httpx.get(
        TERRABRASILIS_WFS,
        params={
            "service": "WFS",
            "version": "1.0.0",
            "request": "GetFeature",
            "typeName": layer,
            "outputFormat": "application/json",
        },
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    data = resp.json()
    features = data.get("features", [])
    log.info("uc.download.layer.done", layer=layer, n=len(features))
    return features


def download_ucs(data_dir: Path, skip: bool) -> Path:
    """
    Baixa UCs de todas as camadas TerraBrasilis, deduplica por id_cnuc e
    salva GeoJSON consolidado em cache local. Retorna path do arquivo.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / CACHE_FILE

    if skip and dest.exists() and dest.stat().st_size > 0:
        log.info("uc.download.skip",
                 file=dest.name,
                 size_kb=round(dest.stat().st_size / 1024, 1))
        return dest

    log.info("uc.download.start", layers=len(BIOME_LAYERS))
    all_features: dict[int, dict] = {}  # id_cnuc → feature (deduplication)

    for layer in BIOME_LAYERS:
        try:
            features = _fetch_biome_layer(layer)
            new = 0
            for f in features:
                fid = f.get("properties", {}).get("id")
                if fid is not None and fid not in all_features:
                    all_features[fid] = f
                    new += 1
            log.info("uc.download.dedup",
                     layer=layer.split(":")[1],
                     fetched=len(features),
                     new=new,
                     total_unique=len(all_features))
        except Exception as exc:
            log.error("uc.download.layer.error",
                      layer=layer, error=str(exc)[:300])

    geojson = {
        "type": "FeatureCollection",
        "features": list(all_features.values()),
    }
    dest.write_text(json.dumps(geojson), encoding="utf-8")
    log.info("uc.download.done",
             total=len(all_features),
             size_kb=round(dest.stat().st_size / 1024, 1))
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Parse GeoJSON → GeoDataFrame
# ─────────────────────────────────────────────────────────────────────────────

def load_ucs_gdf(geojson_path: Path) -> gpd.GeoDataFrame:
    """
    Lê o GeoJSON consolidado e retorna GeoDataFrame reprojetado para WGS84.
    Os dados já saem do WFS em EPSG:4326 (WGS84).
    """
    log.info("uc.parse.start", path=geojson_path.name)
    gdf = gpd.read_file(str(geojson_path))
    log.info("uc.parse.loaded",
             rows=len(gdf),
             crs=str(gdf.crs),
             cols=list(gdf.columns))

    if gdf.crs and gdf.crs.to_epsg() != 4326:
        log.info("uc.parse.reproject", from_epsg=gdf.crs.to_epsg())
        gdf = gdf.to_crs(epsg=4326)

    log.info("uc.parse.done", rows=len(gdf))
    return gdf


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Converter feature → documento OpenSearch
# ─────────────────────────────────────────────────────────────────────────────

def _compute_area_ha(geom) -> float | None:
    """Calcula área em hectares usando projeção equal-area para o Brasil."""
    try:
        geom_proj = gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(epsg=EPSG_BRAZIL_AREA)
        area_m2 = float(geom_proj.iloc[0].area)
        return round(area_m2 / 10_000, 2)
    except Exception:
        return None


def row_to_doc(row: pd.Series) -> dict | None:
    """Converte uma linha do GeoDataFrame em documento mr_ucs_v001."""
    geom = row.get("geometry")
    if geom is None or geom.is_empty:
        return None

    cod_cnuc = row.get("id")
    nome     = (row.get("nome") or "").strip()
    if not cod_cnuc or not nome:
        return None

    # Corrige geometrias inválidas (auto-interseções, arestas coincidentes, etc.)
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
        if geom is None or geom.is_empty:
            return None

    centroid = geom.centroid

    # Simplifica polígono para reduzir payload (~100m precisão)
    geom_simplified = geom.simplify(GEOMETRY_SIMPLIFY_TOLERANCE, preserve_topology=True)
    # Garante que a geometria simplificada também é válida
    if not geom_simplified.is_valid:
        geom_simplified = shapely.make_valid(geom_simplified)

    # Verifica se após simplificação ainda há geometria utilizável
    # (polígonos muito pequenos podem colapsar em linha/ponto)
    if geom_simplified is None or geom_simplified.is_empty:
        geom_simplified = geom  # usa original sem simplificação

    # Tenta serializar — UCs com geometrias degeneradas (ex: RPPN minúscula)
    # são indexadas sem polígono mas mantêm centróide para buscas geo_point
    try:
        poligono_json = mapping(geom_simplified)
    except Exception:
        poligono_json = None

    categoria = (row.get("categoria") or "").strip() or None
    grupo_raw = (row.get("grupo") or "").strip()
    esfera    = (row.get("esfera") or "").strip() or None
    ano_cria  = str(row.get("ano_cria") or "").strip()

    area_ha = _compute_area_ha(geom)
    now_iso = datetime.now(timezone.utc).isoformat()

    return {
        "cod_cnuc":        str(cod_cnuc),
        "nome":            nome,
        "nome_normalizado": _normalize(nome),
        "categoria":       categoria,
        "grupo":           _grupo_desc(grupo_raw),
        "esfera":          esfera,
        "orgao_gestor":    None,   # não disponível no TerraBrasilis
        "municipios":      None,   # não disponível
        "uf":              None,   # não disponível
        "area_ha":         area_ha,
        "dt_criacao":      _ano_to_date(ano_cria),
        "dt_atualizacao":  None,
        "centroide": {
            "lat": round(centroid.y, 6),
            "lon": round(centroid.x, 6),
        },
        "poligono":  poligono_json,
        "fonte":     "TerraBrasilis/INPE",
        "indexed_at": now_iso,
    }


def iter_actions(gdf: gpd.GeoDataFrame) -> Iterator[dict]:
    ok = skipped = 0
    for _, row in gdf.iterrows():
        doc = row_to_doc(row)
        if doc is None:
            skipped += 1
            continue
        ok += 1
        yield {
            "_index":  INDEX_UC,
            "_id":     doc["cod_cnuc"],
            "_source": doc,
        }
    log.info("uc.iter.done", ok=ok, skipped=skipped)


def _bulk_with_geo_fallback(
    client: OpenSearch,
    batch: list[dict],
) -> tuple[int, int]:
    """
    Bulk index com fallback para geometrias que OpenSearch rejeita.
    Para documentos com erro de geo_shape, re-envia sem o campo 'poligono'.
    """
    ok, errs = helpers.bulk(client, batch, raise_on_error=False)
    if not errs:
        return ok, 0

    # Identifica IDs com erro de geo_shape e re-envia sem polígono
    failed_ids = set()
    for e in (errs if isinstance(errs, list) else []):
        idx_info = e.get("index", {})
        if "geo_shape" in str(idx_info.get("error", {})) or \
           "invalid_shape" in str(idx_info.get("error", {})) or \
           "Tessellate" in str(idx_info.get("error", {})) or \
           "coplanar" in str(idx_info.get("error", {})) or \
           "non-collinear" in str(idx_info.get("error", {})):
            failed_ids.add(idx_info.get("_id"))

    if not failed_ids:
        return ok, len(errs) if isinstance(errs, list) else errs

    # Retry sem poligono
    retry_batch = []
    for action in batch:
        if action.get("_id") in failed_ids:
            doc = {k: v for k, v in action["_source"].items() if k != "poligono"}
            retry_batch.append({**action, "_source": doc})

    log.warning("uc.index.geo_fallback",
                n=len(retry_batch),
                ids=sorted(failed_ids))

    ok2, errs2 = helpers.bulk(client, retry_batch, raise_on_error=False)
    return ok + ok2, len(errs2) if isinstance(errs2, list) else errs2


def bulk_index(
    client: OpenSearch,
    gdf: gpd.GeoDataFrame,
    dry_run: bool,
) -> dict[str, int]:
    """Bulk index das UCs em mr_ucs_v001 com fallback para geometrias degeneradas."""
    log.info("uc.index.start", total=len(gdf))
    total_ok = total_err = 0
    batch: list[dict] = []

    for action in iter_actions(gdf):
        batch.append(action)
        if len(batch) >= BATCH_SIZE:
            if not dry_run:
                ok, err = _bulk_with_geo_fallback(client, batch)
                total_ok  += ok
                total_err += err
            else:
                total_ok += len(batch)
            batch = []

    if batch:
        if not dry_run:
            ok, err = _bulk_with_geo_fallback(client, batch)
            total_ok  += ok
            total_err += err
        else:
            total_ok += len(batch)

    log.info("uc.index.done",
             total_ok=total_ok,
             total_err=total_err,
             dry_run=dry_run)
    return {"ok": total_ok, "errors": total_err}


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Enriquecimento de mr_jazidas_v001 (sobreposição espacial)
# ─────────────────────────────────────────────────────────────────────────────

def enrich_jazidas_uc(
    client: OpenSearch,
    gdf_uc: gpd.GeoDataFrame,
    dry_run: bool,
) -> dict[str, int]:
    """
    Verifica quais processos ANM se sobrepõem a Unidades de Conservação.

    Estratégia:
      1. Scroll mr_jazidas_v001 → location (lat/lon) de todos os processos
      2. Cria GeoDataFrame de pontos
      3. Spatial join (within) com polígonos das UCs
      4. Para cada jazida, conta quantas UCs a contêm e lista seus IDs
      5. Bulk update: n_restricoes_uc + restricoes_geo (append/replace UC entries)

    Nota: um processo pode estar dentro de múltiplas UCs (ex: APA que envolve
    uma Reserva Biológica).
    """
    log.info("enrich_uc.start")

    # ── 1. Scroll jazidas ──
    all_hits: list[dict] = []
    resp = client.search(
        index=INDEX_JAZIDAS,
        scroll="10m",
        size=SCROLL_SIZE,
        body={
            "_source": ["location", "restricoes_geo"],
            "query": {"exists": {"field": "location"}},
        },
    )
    scroll_id = resp["_scroll_id"]
    hits      = resp["hits"]["hits"]
    total     = resp["hits"]["total"]["value"]
    log.info("enrich_uc.scroll.start", total=total)

    while hits:
        all_hits.extend(hits)
        resp = client.scroll(scroll_id=scroll_id, scroll="10m")
        scroll_id = resp["_scroll_id"]
        hits      = resp["hits"]["hits"]

    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass
    log.info("enrich_uc.loaded", total=len(all_hits))

    # ── 2. GeoDataFrame de pontos ──
    rows = []
    for h in all_hits:
        src = h.get("_source", {})
        loc = src.get("location") or {}
        lat = loc.get("lat")
        lon = loc.get("lon")
        if lat is not None and lon is not None:
            rows.append({
                "_id":               h["_id"],
                "restricoes_geo_orig": src.get("restricoes_geo") or [],
                "geometry":          Point(float(lon), float(lat)),
            })

    if not rows:
        log.warning("enrich_uc.no_points")
        return {"matched": 0, "ok": 0, "errors": 0}

    gdf_pts = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    log.info("enrich_uc.points_built", n=len(gdf_pts))

    # ── 3. Garante WGS84 nas UCs ──
    if gdf_uc.crs and gdf_uc.crs.to_epsg() != 4326:
        gdf_uc = gdf_uc.to_crs(epsg=4326)

    gdf_uc = gdf_uc.copy()
    gdf_uc["_uc_id"]   = gdf_uc["id"].astype(str)
    gdf_uc["_uc_nome"] = gdf_uc["nome"].fillna("")

    log.info("enrich_uc.sjoin.start",
             n_points=len(gdf_pts),
             n_polygons=len(gdf_uc))

    # ── 4. Spatial join — ponto dentro do polígono da UC ──
    joined = gpd.sjoin(
        gdf_pts[["_id", "geometry"]],
        gdf_uc[["_uc_id", "_uc_nome", "geometry"]],
        how="left",
        predicate="within",
    )

    # Agrupa por jazida: monta lista de chaves UC
    # Formato restricoes_geo: "UC:{cod_cnuc}:{nome_uc}"
    def _uc_key(uc_id: str, uc_nome: str) -> str:
        return f"UC:{uc_id}:{uc_nome}"

    jazida_ucs: dict[str, list[str]] = {}
    for _, r in joined.dropna(subset=["_uc_id"]).iterrows():
        jid = r["_id"]
        key = _uc_key(str(r["_uc_id"]), str(r["_uc_nome"]))
        jazida_ucs.setdefault(jid, []).append(key)

    matched = len(jazida_ucs)
    log.info("enrich_uc.sjoin.done",
             matched=matched,
             total=len(gdf_pts),
             pct=round(matched / max(len(gdf_pts), 1) * 100, 2))

    # ── 5. Bulk update ──
    orig_map: dict[str, list[str]] = {
        r["_id"]: r["restricoes_geo_orig"] for r in rows
    }

    actions: list[dict] = []

    # Jazidas COM sobreposição de UC
    for jid, uc_keys in jazida_ucs.items():
        existing     = list(orig_map.get(jid, []) or [])
        existing_sem_uc = [e for e in existing if not e.startswith("UC:")]
        merged       = existing_sem_uc + uc_keys

        actions.append({
            "_op_type": "update",
            "_index":   INDEX_JAZIDAS,
            "_id":      jid,
            "doc": {
                "n_restricoes_uc": len(uc_keys),
                "restricoes_geo":  merged,
            },
        })

    # Jazidas SEM sobreposição — zera n_restricoes_uc (idempotência)
    ids_com_uc = set(jazida_ucs.keys())
    for r in rows:
        jid = r["_id"]
        if jid not in ids_com_uc:
            existing = list(r["restricoes_geo_orig"] or [])
            if any(e.startswith("UC:") for e in existing):
                # Remove entradas UC: obsoletas
                actions.append({
                    "_op_type": "update",
                    "_index":   INDEX_JAZIDAS,
                    "_id":      jid,
                    "doc": {
                        "n_restricoes_uc": 0,
                        "restricoes_geo":  [e for e in existing if not e.startswith("UC:")],
                    },
                })

    log.info("enrich_uc.updates_built",
             total=len(actions),
             with_uc=matched)

    ok = err = 0
    if not dry_run:
        batch_size = BATCH_SIZE * 10
        for i in range(0, len(actions), batch_size):
            batch = actions[i:i + batch_size]
            _ok, _errs = helpers.bulk(client, batch, raise_on_error=False)
            ok  += _ok
            err += len(_errs) if isinstance(_errs, list) else _errs
            if (i // batch_size) % 10 == 0:
                log.info("enrich_uc.update.progress", done=ok, errors=err)
    else:
        ok = len(actions)
        log.info("enrich_uc.dry_run", would_update=ok)

    log.info("enrich_uc.done", matched=matched, ok=ok, errors=err)
    return {"matched": matched, "ok": ok, "errors": err}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--all",            "run_all",    is_flag=True,
              help="Index + enrich-jazidas.")
@click.option("--index",          "do_index",   is_flag=True,
              help="Baixa WFS e indexa mr_ucs_v001.")
@click.option("--enrich-jazidas", "do_enrich",  is_flag=True,
              help="Spatial join → atualiza n_restricoes_uc em mr_jazidas_v001.")
@click.option("--skip-download",  is_flag=True,
              help="Reutiliza cache local do WFS.")
@click.option("--data-dir",       default=None,
              help="Diretório de cache (default: settings.etl_data_dir/ucs).")
@click.option("--dry-run",        is_flag=True,
              help="Simula sem escrever no OpenSearch.")
def main(
    run_all: bool,
    do_index: bool,
    do_enrich: bool,
    skip_download: bool,
    data_dir: str | None,
    dry_run: bool,
) -> None:
    """bot_ucs — TerraBrasilis WFS → mr_ucs_v001 + enriquecimento jazidas."""
    t0 = time.time()

    _data_dir = Path(data_dir) if data_dir else settings.etl_data_dir / "ucs"
    _data_dir.mkdir(parents=True, exist_ok=True)

    if run_all:
        do_index = do_enrich = True

    client = get_os_client()

    gdf_uc: gpd.GeoDataFrame | None = None

    if do_index:
        log.info("--- step 1: download + index UCs ---")
        geojson_path = download_ucs(_data_dir, skip=skip_download)
        gdf_uc = load_ucs_gdf(geojson_path)
        res = bulk_index(client, gdf_uc, dry_run=dry_run)
        log.info("step1.done", **res)

    if do_enrich:
        log.info("--- step 2: enrich jazidas com UCs ---")
        if gdf_uc is None:
            geojson_path = _data_dir / CACHE_FILE
            if not geojson_path.exists():
                geojson_path = download_ucs(_data_dir, skip=False)
            gdf_uc = load_ucs_gdf(geojson_path)

        res = enrich_jazidas_uc(client, gdf_uc, dry_run=dry_run)
        log.info("step2.done", **res)

    elapsed = round(time.time() - t0, 1)
    log.info("bot_ucs.done", elapsed_s=elapsed, dry_run=dry_run)


if __name__ == "__main__":
    main()
