"""
bot_terras_indigenas.py — FUNAI WFS → mr_terras_indigenas_v001

Fluxo:
  1. Download das ~657 Terras Indígenas via WFS GeoJSON (FUNAI GeoServer)
  2. Simplifica polígonos (shapely.simplify) e computa centróides
  3. Indexa em mr_terras_indigenas_v001
  4. (Opcional) --enrich-jazidas: spatial join ponto-em-polígono para marcar
     quais processos ANM se sobrepõem a TIs → atualiza n_restricoes_ti e
     restricoes_geo em mr_jazidas_v001

Fonte: https://geoserver.funai.gov.br/geoserver/Funai/ows
Layer: Funai:tis_poligonais (EPSG:4674 / SIRGAS 2000 ≈ WGS84)

Uso:
  python -m bots.bot_terras_indigenas --index
  python -m bots.bot_terras_indigenas --enrich-jazidas
  python -m bots.bot_terras_indigenas --all
  python -m bots.bot_terras_indigenas --all --dry-run
  python -m bots.bot_terras_indigenas --skip-download --index
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
from shapely.geometry import mapping, shape

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_TI     = "mr_terras_indigenas_v001"
INDEX_JAZIDAS = "mr_jazidas_v001"

BATCH_SIZE   = 50    # geo_shape payloads grandes
SCROLL_SIZE  = 2000

# FUNAI WFS — retorna todas as TIs em uma única requisição
FUNAI_WFS_URL = (
    "https://geoserver.funai.gov.br/geoserver/Funai/ows"
    "?service=WFS&version=1.0.0&request=GetFeature"
    "&typeName=Funai:tis_poligonais&outputFormat=application%2Fjson"
)

CACHE_FILE = "funai_tis.geojson"

# Tolerância de simplificação (~100m em graus decimais)
GEOMETRY_SIMPLIFY_TOLERANCE = 0.001

# Fases FUNAI em ordem crescente de proteção
FASE_ORDEM = {
    "Em Estudo": 1,
    "Declarada": 2,
    "Delimitada": 3,
    "Homologada": 4,
    "Regularizada": 5,
}


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


def _parse_date_br(val: str | None) -> str | None:
    """Converte DD/MM/AAAA → AAAA-MM-DD."""
    if not val:
        return None
    try:
        parts = val.strip().split("/")
        if len(parts) == 3:
            d, m, y = parts
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Download WFS
# ─────────────────────────────────────────────────────────────────────────────

def download_funai(data_dir: Path, skip: bool) -> Path:
    """
    Baixa todas as TIs via WFS GeoJSON e salva em cache local.
    Retorna path do GeoJSON.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / CACHE_FILE

    if skip and dest.exists() and dest.stat().st_size > 0:
        log.info("funai.download.skip",
                 file=dest.name,
                 size_kb=round(dest.stat().st_size / 1024, 1))
        return dest

    log.info("funai.download.start", url=FUNAI_WFS_URL)
    try:
        resp = httpx.get(FUNAI_WFS_URL, timeout=120, follow_redirects=True)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        data = resp.json()
        n = len(data.get("features", []))
        log.info("funai.download.done",
                 features=n,
                 size_kb=round(dest.stat().st_size / 1024, 1))
        return dest
    except Exception as exc:
        log.error("funai.download.error", error=str(exc)[:300])
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Parse GeoJSON → GeoDataFrame
# ─────────────────────────────────────────────────────────────────────────────

def load_tis_gdf(geojson_path: Path) -> gpd.GeoDataFrame:
    """
    Lê o GeoJSON FUNAI e retorna GeoDataFrame reprojetado para WGS84.
    EPSG:4674 (SIRGAS 2000) ≈ WGS84 — reprojeção é formal mas necessária para
    garantir o CRS correto no OpenSearch.
    """
    log.info("funai.parse.start", path=geojson_path.name)
    gdf = gpd.read_file(str(geojson_path))
    log.info("funai.parse.loaded",
             rows=len(gdf),
             crs=str(gdf.crs),
             cols=list(gdf.columns))

    # Reprojeta para WGS84 se necessário
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        log.info("funai.parse.reproject", from_epsg=gdf.crs.to_epsg())
        gdf = gdf.to_crs(epsg=4326)

    log.info("funai.parse.done", rows=len(gdf))
    return gdf


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Converter feature → documento OpenSearch
# ─────────────────────────────────────────────────────────────────────────────

def row_to_doc(row: pd.Series) -> dict | None:
    """Converte uma linha do GeoDataFrame em documento mr_terras_indigenas_v001."""
    geom = row.get("geometry")
    if geom is None or geom.is_empty:
        return None

    # Simplifica polígono para reduzir payload
    geom_simplified = geom.simplify(GEOMETRY_SIMPLIFY_TOLERANCE, preserve_topology=True)
    centroid = geom.centroid

    # Campos do WFS
    codigo   = str(row.get("terrai_codigo", "")).strip()
    nome     = (row.get("terrai_nome") or "").strip()
    etnia    = (row.get("etnia_nome") or "").strip()
    municipio= (row.get("municipio_nome") or "").strip()
    uf       = (row.get("uf_sigla") or "").strip()
    area_ha  = row.get("superficie_perimetro_ha")
    fase     = (row.get("fase_ti") or "").strip()
    modalidade = (row.get("modalidade_ti") or "").strip()
    faixa_fronteira = str(row.get("faixa_fronteira") or "").strip().lower() in ("sim", "s", "true", "t")
    dt_atualiz = _parse_date_br(str(row.get("data_atualizacao") or ""))

    if not codigo or not nome:
        return None

    try:
        area_f = float(area_ha) if area_ha is not None else None
    except (TypeError, ValueError):
        area_f = None

    now_iso = datetime.now(timezone.utc).isoformat()

    return {
        "id_ti":              codigo,
        "nome":               nome,
        "nome_normalizado":   _normalize(nome),
        "etnia":              etnia or None,
        "fase_funai":         fase or None,
        "modalidade":         modalidade or None,
        "faixa_fronteira":    faixa_fronteira,
        "municipios":         municipio or None,
        "uf":                 uf or None,
        "area_ha":            area_f,
        "populacao_estimada": None,  # não disponível no WFS
        "dt_homologacao":     None,  # não disponível no WFS
        "dt_atualizacao":     dt_atualiz,
        "centroide": {
            "lat": round(centroid.y, 6),
            "lon": round(centroid.x, 6),
        },
        "poligono": mapping(geom_simplified),
        "fonte":    "FUNAI",
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
            "_index":  INDEX_TI,
            "_id":     doc["id_ti"],
            "_source": doc,
        }
    log.info("funai.iter.done", ok=ok, skipped=skipped)


def bulk_index(client: OpenSearch, gdf: gpd.GeoDataFrame, dry_run: bool) -> dict[str, int]:
    """Bulk index das TIs em mr_terras_indigenas_v001."""
    log.info("funai.index.start", total=len(gdf))
    total_ok = total_err = 0
    batch: list[dict] = []

    for action in iter_actions(gdf):
        batch.append(action)
        if len(batch) >= BATCH_SIZE:
            if not dry_run:
                ok, errs = helpers.bulk(client, batch, raise_on_error=False)
                total_ok  += ok
                total_err += len(errs) if isinstance(errs, list) else errs
            else:
                total_ok += len(batch)
            batch = []

    if batch:
        if not dry_run:
            ok, errs = helpers.bulk(client, batch, raise_on_error=False)
            total_ok  += ok
            total_err += len(errs) if isinstance(errs, list) else errs
        else:
            total_ok += len(batch)

    log.info("funai.index.done", total_ok=total_ok, total_err=total_err,
             dry_run=dry_run)
    return {"ok": total_ok, "errors": total_err}


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Enriquecimento de mr_jazidas_v001 (sobreposição espacial)
# ─────────────────────────────────────────────────────────────────────────────

def enrich_jazidas_ti(
    client: OpenSearch,
    gdf_ti: gpd.GeoDataFrame,
    dry_run: bool,
) -> dict[str, int]:
    """
    Verifica quais processos ANM se sobrepõem a Terras Indígenas.

    Estratégia:
      1. Scroll mr_jazidas_v001 → location (lat/lon) de todos os processos
      2. Cria GeoDataFrame de pontos
      3. Spatial join (within) com polígonos das TIs
      4. Para cada jazida, conta quantas TIs a contêm e lista seus IDs
      5. Bulk update: n_restricoes_ti + restricoes_geo (append ao existente)

    Nota: um processo pode estar dentro de múltiplas TIs (limite ou sobreposição
    de perímetros homologados vs. declarados).
    """
    log.info("enrich_ti.start")

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
    hits = resp["hits"]["hits"]
    total = resp["hits"]["total"]["value"]
    log.info("enrich_ti.scroll.start", total=total)

    while hits:
        all_hits.extend(hits)
        resp = client.scroll(scroll_id=scroll_id, scroll="10m")
        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]

    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass
    log.info("enrich_ti.loaded", total=len(all_hits))

    # ── 2. GeoDataFrame de pontos ──
    from shapely.geometry import Point

    rows = []
    for h in all_hits:
        src = h.get("_source", {})
        loc = src.get("location") or {}
        lat = loc.get("lat")
        lon = loc.get("lon")
        if lat is not None and lon is not None:
            rows.append({
                "_id":          h["_id"],
                "restricoes_geo_orig": src.get("restricoes_geo") or [],
                "geometry":     Point(float(lon), float(lat)),
            })

    if not rows:
        log.warning("enrich_ti.no_points")
        return {"matched": 0, "ok": 0, "errors": 0}

    gdf_pts = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    log.info("enrich_ti.points_built", n=len(gdf_pts))

    # ── 3. Garante que TIs estão em WGS84 ──
    if gdf_ti.crs and gdf_ti.crs.to_epsg() != 4326:
        gdf_ti = gdf_ti.to_crs(epsg=4326)

    # Adiciona coluna id_ti ao GeoDataFrame das TIs
    gdf_ti = gdf_ti.copy()
    gdf_ti["_ti_id"] = gdf_ti["terrai_codigo"].astype(str)
    gdf_ti["_ti_nome"] = gdf_ti["terrai_nome"].fillna("")
    gdf_ti["_fase"] = gdf_ti["fase_ti"].fillna("")

    log.info("enrich_ti.sjoin.start",
             n_points=len(gdf_pts), n_polygons=len(gdf_ti))

    # ── 4. Spatial join — ponto dentro do polígono da TI ──
    joined = gpd.sjoin(
        gdf_pts[["_id", "geometry"]],
        gdf_ti[["_ti_id", "_ti_nome", "_fase", "geometry"]],
        how="left",
        predicate="within",
    )

    # Agrupa por jazida: conta TIs e monta lista de IDs
    # Formato restricoes_geo: "TI:{id_ti}:{nome_ti}"
    def _ti_key(ti_id: str, ti_nome: str) -> str:
        return f"TI:{ti_id}:{ti_nome}"

    jazida_tis: dict[str, list[str]] = {}
    for _, r in joined.dropna(subset=["_ti_id"]).iterrows():
        jid = r["_id"]
        key = _ti_key(str(r["_ti_id"]), str(r["_ti_nome"]))
        jazida_tis.setdefault(jid, []).append(key)

    matched = len(jazida_tis)
    log.info("enrich_ti.sjoin.done",
             matched=matched,
             total=len(gdf_pts),
             pct=round(matched / max(len(gdf_pts), 1) * 100, 2))

    # ── 5. Bulk update ──
    # Monta mapa _id → restricoes_geo originais para merge
    orig_map: dict[str, list[str]] = {
        r["_id"]: r["restricoes_geo_orig"] for r in rows
    }

    actions: list[dict] = []
    for jid, ti_keys in jazida_tis.items():
        # Merge com restricoes_geo existentes (mantém UCs, etc.)
        existing = list(orig_map.get(jid, []) or [])
        # Remove entradas TI anteriores e adiciona as novas
        existing_sem_ti = [e for e in existing if not e.startswith("TI:")]
        merged = existing_sem_ti + ti_keys

        actions.append({
            "_op_type": "update",
            "_index":   INDEX_JAZIDAS,
            "_id":      jid,
            "doc": {
                "n_restricoes_ti": len(ti_keys),
                "restricoes_geo":  merged,
            },
        })

    # Zera n_restricoes_ti para jazidas que não sobrepõem nenhuma TI
    # (idempotência: se rodou antes e agora não sobrepõe, reseta)
    ids_com_ti = set(jazida_tis.keys())
    for r in rows:
        jid = r["_id"]
        if jid not in ids_com_ti:
            existing = list(r["restricoes_geo_orig"] or [])
            if any(e.startswith("TI:") for e in existing):
                existing_sem_ti = [e for e in existing if not e.startswith("TI:")]
                actions.append({
                    "_op_type": "update",
                    "_index":   INDEX_JAZIDAS,
                    "_id":      jid,
                    "doc": {
                        "n_restricoes_ti": 0,
                        "restricoes_geo":  existing_sem_ti,
                    },
                })

    log.info("enrich_ti.updates_built", total=len(actions), with_ti=matched)

    ok = err = 0
    if not dry_run:
        for i in range(0, len(actions), BATCH_SIZE * 10):
            batch = actions[i:i + BATCH_SIZE * 10]
            _ok, _errs = helpers.bulk(client, batch, raise_on_error=False)
            ok  += _ok
            err += len(_errs) if isinstance(_errs, list) else _errs
            if (i // (BATCH_SIZE * 10)) % 10 == 0:
                log.info("enrich_ti.update.progress",
                         done=ok, errors=err)
    else:
        ok = len(actions)
        log.info("enrich_ti.dry_run", would_update=ok)

    log.info("enrich_ti.done", matched=matched, ok=ok, errors=err)
    return {"matched": matched, "ok": ok, "errors": err}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--all",            "run_all",       is_flag=True,
              help="Index + enrich-jazidas.")
@click.option("--index",          "do_index",      is_flag=True,
              help="Baixa WFS e indexa mr_terras_indigenas_v001.")
@click.option("--enrich-jazidas", "do_enrich",     is_flag=True,
              help="Spatial join → atualiza n_restricoes_ti em mr_jazidas_v001.")
@click.option("--skip-download",  is_flag=True,
              help="Reutiliza cache local do WFS.")
@click.option("--data-dir",       default=None,
              help="Diretório de cache (default: settings.etl_data_dir/funai).")
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
    """bot_terras_indigenas — FUNAI WFS → mr_terras_indigenas_v001 + enriquecimento."""
    t0 = time.time()

    _data_dir = Path(data_dir) if data_dir else settings.etl_data_dir / "funai"
    _data_dir.mkdir(parents=True, exist_ok=True)

    if run_all:
        do_index = do_enrich = True

    client = get_os_client()

    gdf_ti: gpd.GeoDataFrame | None = None

    if do_index:
        log.info("--- step 1: download + index TIs ---")
        geojson_path = download_funai(_data_dir, skip=skip_download)
        gdf_ti = load_tis_gdf(geojson_path)
        res = bulk_index(client, gdf_ti, dry_run=dry_run)
        log.info("step1.done", **res)

    if do_enrich:
        log.info("--- step 2: enrich jazidas com TIs ---")
        # Carrega GDF se não foi indexado nesta execução
        if gdf_ti is None:
            geojson_path = _data_dir / CACHE_FILE
            if not geojson_path.exists():
                geojson_path = download_funai(_data_dir, skip=False)
            gdf_ti = load_tis_gdf(geojson_path)

        res = enrich_jazidas_ti(client, gdf_ti, dry_run=dry_run)
        log.info("step2.done", **res)

    elapsed = round(time.time() - t0, 1)
    log.info("bot_terras_indigenas.done", elapsed_s=elapsed, dry_run=dry_run)


if __name__ == "__main__":
    main()
