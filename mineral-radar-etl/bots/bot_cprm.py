"""
bot_cprm.py — SGB/CPRM OGC API → mr_cprm_v001
===============================================

Indexa as ~36.000 ocorrências minerais (Recursos Minerais) do OGC API Features
do Serviço Geológico do Brasil (SGB/CPRM).

Fonte (OGC API Features — padrão OGC, substitui ArcGIS FeatureServer):
  https://geoservicos.sgb.gov.br/ogcapi/collections/geologia/recursos-minerais/items
  35.918 features (mai/2026) — EPSG:4326

Campos principais:
  substancias, importancia, status_economico, situacao_mina, provincia,
  rochas_hospedeiras, rochas_encaixantes, morfologia, municipio, uf

Geo:
  - ``location`` (geo_point): centroide ou ponto OGC.
  - ``poligono`` (geo_shape): polígono real se a feature vier como Polygon/MultiPolygon;
    caso contrário (OGC atual = só Point), aproximação por **buffer métrico** de
    ~75 m em EPSG:5880 (campo ``poligono_fonte`` = ``buffer_ponto_75m``).

Paginação via offset/limit (padrão OGC, limite 500/req).

Uso:
  python -m bots.bot_cprm --index              # baixa + indexa
  python -m bots.bot_cprm --index --skip-download
  python -m bots.bot_cprm --dry-run            # simula sem escrever
  python -m bots.bot_cprm --enrich-jazidas     # correlaciona com mr_jazidas_v001
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import click
import httpx
from opensearchpy import OpenSearch, helpers

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_CPRM   = "mr_cprm_v001"
CACHE_FILE   = "cprm_ocorrencias.geojson"
BATCH_INDEX  = 200

# OGC API Features — padrão moderno, substitui ArcGIS FeatureServer proprietário
OGC_API_URL = (
    "https://geoservicos.sgb.gov.br/ogcapi/collections"
    "/geologia/recursos-minerais/items"
)
OGC_PAGE_SIZE = 500   # máximo seguro por requisição
HTTP_TIMEOUT  = 30

# Buffer em metros ao redor de Point (OGC não envia polígono de depósito).
BUFFER_PONTO_METROS = 75.0


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
    log.info("opensearch.ok", version=info["version"]["number"],
             cluster=info["cluster_name"])
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Download — OGC API Features
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_ogcapi_page(
    client: httpx.Client,
    offset: int,
    limit: int = OGC_PAGE_SIZE,
) -> tuple[list[dict], int]:
    """
    Baixa uma página da OGC API Features.

    Returns:
        (features, number_matched)
    """
    r = client.get(OGC_API_URL, params={
        "offset": offset,
        "limit":  limit,
        "f":      "json",
    })
    r.raise_for_status()
    data = r.json()
    return data.get("features", []), int(data.get("numberMatched", 0))


def download_cprm(data_dir: Path, skip: bool) -> Path:
    """
    Baixa todas as ocorrências do OGC API Features via paginação offset/limit.
    Salva GeoJSON consolidado em cache local.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / CACHE_FILE

    if skip and dest.exists() and dest.stat().st_size > 100_000:
        log.info("cprm.download.skip",
                 file=dest.name,
                 size_mb=round(dest.stat().st_size / 1024 / 1024, 1))
        return dest

    log.info("cprm.download.start", url=OGC_API_URL)
    all_features: list[dict] = []

    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        # Primeira página — obtém total
        first_page, total = _fetch_ogcapi_page(client, offset=0)
        all_features.extend(first_page)
        log.info("cprm.download.total", total=total, first_page=len(first_page))

        offset = OGC_PAGE_SIZE
        page = 1
        while offset < total:
            try:
                feats, _ = _fetch_ogcapi_page(client, offset=offset)
                if not feats:
                    break
                all_features.extend(feats)
                page += 1
                if page % 10 == 0 or offset + OGC_PAGE_SIZE >= total:
                    log.info("cprm.download.progress",
                             offset=offset, total=total,
                             fetched=len(all_features))
                offset += OGC_PAGE_SIZE
            except Exception as exc:
                log.error("cprm.download.page.error",
                          offset=offset, error=str(exc)[:200])
                time.sleep(3)
                offset += OGC_PAGE_SIZE  # pula página com erro

    # Garante formato GeoJSON FeatureCollection padrão
    geojson = {
        "type": "FeatureCollection",
        "features": [f for f in all_features if f.get("geometry")],
    }
    dest.write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")
    log.info("cprm.download.done",
             total=len(geojson["features"]),
             size_mb=round(dest.stat().st_size / 1024 / 1024, 1))
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# Parse
# ─────────────────────────────────────────────────────────────────────────────

def _parse_substancias(raw: str | None) -> list[str]:
    """Divide string de substâncias em lista normalizada."""
    if not raw:
        return []
    import re
    parts = re.split(r"[;,]|\se\s", raw, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def _clean(v: str | None) -> str | None:
    """Retorna string limpa ou None."""
    return (v or "").strip() or None


def _buffer_point_geojson(lon: float, lat: float, radius_m: float = BUFFER_PONTO_METROS) -> dict:
    """Circulo aproximado em WGS84 (buffer em SIRGAS polyconic Brasil)."""
    from shapely.geometry import Point, mapping
    from shapely.ops import transform
    import pyproj

    fwd = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:5880", always_xy=True).transform
    inv = pyproj.Transformer.from_crs("EPSG:5880", "EPSG:4326", always_xy=True).transform
    pt = Point(lon, lat)
    buf = transform(inv, transform(fwd, pt).buffer(radius_m))
    if not buf.is_valid:
        buf = buf.buffer(0)
    return mapping(buf)


def _geometry_centroid_lonlat(geom: dict) -> tuple[float, float] | None:
    """Centroide ou coordenada útil para ``location``."""
    from shapely.geometry import shape

    try:
        g = shape(geom)
    except Exception:
        return None
    if g.is_empty:
        return None
    if not g.is_valid:
        g = g.buffer(0)
    c = g.centroid
    if c.is_empty:
        return None
    lon, lat = float(c.x), float(c.y)
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return lon, lat


def _poligono_from_geometry(geom: dict) -> tuple[dict | None, str | None]:
    """
    Monta GeoJSON para ``poligono`` (OpenSearch geo_shape).

    Returns:
        (geojson_dict, poligono_fonte) — ``poligono_fonte`` explica a origem.
    """
    from shapely.geometry import shape, mapping

    gtype = (geom.get("type") or "").strip()
    if not gtype:
        return None, None

    if gtype in ("Polygon", "MultiPolygon"):
        try:
            g = shape(geom)
            if g.is_empty:
                return None, None
            if not g.is_valid:
                g = g.buffer(0)
            # Evita documentos gigantes no cluster
            if gtype == "Polygon" and len(g.exterior.coords) > 2000:
                g = g.simplify(0.00005, preserve_topology=True)
            elif gtype == "MultiPolygon":
                g = g.simplify(0.00005, preserve_topology=True)
            return mapping(g), "ogc_poligono"
        except Exception:
            return None, None

    if gtype == "Point":
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            return None, None
        lon, lat = float(coords[0]), float(coords[1])
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return None, None
        try:
            return _buffer_point_geojson(lon, lat), "buffer_ponto_75m"
        except Exception:
            return None, None

    return None, None


def parse_feature(feat: dict) -> dict | None:
    """
    Converte uma feature GeoJSON (OGC API ou ArcGIS legado) em documento mr_cprm_v001.

    Suporta:
      - Campos OGC API (minúsculas, snake_case) — fonte primária
      - Campos ArcGIS legado (maiúsculas) — fallback para cache local antigo
    """
    props = feat.get("properties") or {}
    geom  = feat.get("geometry") or {}

    cll = _geometry_centroid_lonlat(geom)
    if cll is None:
        return None
    lon, lat = cll

    poligono, poligono_fonte = _poligono_from_geometry(geom)

    # Normaliza substâncias (OGC: "substancias" str | ArcGIS: "SUBSTANCIAS" str)
    raw_subs = props.get("substancias") or props.get("SUBSTANCIAS") or ""
    substancias = _parse_substancias(raw_subs)

    # Datas: OGC API devolve ISO string; ArcGIS devolve timestamp em ms
    dt_raw = props.get("data_cadastro") or props.get("DATA_CADASTRO")
    if isinstance(dt_raw, (int, float)):
        try:
            dt_str = datetime.fromtimestamp(int(dt_raw) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            dt_str = None
    elif isinstance(dt_raw, str):
        dt_str = dt_raw[:10] if dt_raw else None
    else:
        dt_str = None

    doc: dict = {
        # ── Identificação ─────────────────────────────────────────────────
        "id_ocorrencia":  str(
            props.get("id_ocorrencia") or props.get("ID_OCORRENCIA") or
            props.get("objectid") or props.get("OBJECTID") or ""
        ),
        "id_afloramento": str(
            props.get("id_afloramento") or props.get("ID_AFLORAMENTO") or ""
        ),
        "nome": _clean(props.get("toponimia") or props.get("TOPONIMIA")),
        # ── Substâncias ───────────────────────────────────────────────────
        "substancia_principal": substancias[0] if substancias else None,
        "substancias":          substancias,
        "classes_utilitarias":  _clean(
            props.get("classes_utilitarias") or props.get("CLASSES_UTILITARIAS")
        ),
        # ── Classificação econômica (campos corretos OGC API) ─────────────
        # importancia: Depósito | Indício | Ocorrência | Indeterminado
        "importancia": _clean(
            props.get("importancia") or props.get("IMPORTANCIA")
        ),
        # status_economico: Mina | Garimpo | Não explotado | Indeterminado
        "status_economico": _clean(
            props.get("status_economico") or props.get("STATUS_ECONOMICO")
        ),
        "situacao_mina":    _clean(
            props.get("situacao_mina") or props.get("SITUACAO_MINA") or
            props.get("localizacao_mina") or props.get("LOCALIZACAO_MINA")
        ),
        "situacao_garimpo": _clean(
            props.get("situacao_garimpo") or props.get("SITUACAO_GARIMPO")
        ),
        # ── Geologia ──────────────────────────────────────────────────────
        "rochas_hospedeiras": _clean(
            props.get("rochas_hospedeiras") or props.get("ROCHAS_HOSPEDEIRAS")
        ),
        "rochas_encaixantes": _clean(
            props.get("rochas_encaixantes") or props.get("ROCHAS_ENCAIXANTES")
        ),
        "morfologia":   _clean(props.get("morfologia") or props.get("MORFOLOGIA")),
        "texturas":     _clean(props.get("texturas") or props.get("TEXTURAS")),
        "tipos_alteracao": _clean(
            props.get("tipos_alteracao") or props.get("TIPOS_ALTERACAO")
        ),
        # ── Contexto geográfico ───────────────────────────────────────────
        "provincia": _clean(props.get("provincia") or props.get("PROVINCIA")),
        "municipio":  _clean(props.get("municipio") or props.get("MUNICIPIO")),
        "uf":         _clean(props.get("uf") or props.get("UF")),
        "sureg":      _clean(props.get("sureg") or props.get("SUREG")),
        "projeto":    _clean(props.get("projeto") or props.get("PROJETO")),
        "folha":      _clean(props.get("folha") or props.get("FOLHA")),
        "metodo_geoposicionamento": _clean(
            props.get("metodo_geoposicionamento") or
            props.get("METODO_GEOPOSICIONAMENTO")
        ),
        # ── Geo ───────────────────────────────────────────────────────────
        "area_ha":  None,
        "location": {"lat": lat, "lon": lon},
        # ── Metadados ─────────────────────────────────────────────────────
        "descricao":    _clean(props.get("descricao") or props.get("DESCRICAO")),
        "dt_referencia": dt_str,
        "fonte":         "SGB/CPRM",
        "indexed_at":    datetime.now(timezone.utc).isoformat(),
    }

    if poligono:
        doc["poligono"] = poligono
    if poligono_fonte:
        doc["poligono_fonte"] = poligono_fonte

    return doc


def load_docs(geojson_path: Path) -> list[dict]:
    """Lê GeoJSON local e retorna lista de documentos."""
    log.info("cprm.parse.start", path=geojson_path.name)
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    features = data.get("features", [])

    docs, skipped = [], 0
    n_poly = 0
    for feat in features:
        doc = parse_feature(feat)
        if doc:
            docs.append(doc)
            if doc.get("poligono"):
                n_poly += 1
        else:
            skipped += 1

    log.info("cprm.parse.done", total=len(docs), skipped=skipped, com_poligono=n_poly)
    return docs


# ─────────────────────────────────────────────────────────────────────────────
# Index
# ─────────────────────────────────────────────────────────────────────────────

def iter_actions(docs: list[dict]) -> Iterator[dict]:
    for doc in docs:
        yield {
            "_index":  INDEX_CPRM,
            "_id":     doc["id_ocorrencia"],
            "_source": doc,
        }


def bulk_index(
    client: OpenSearch,
    docs: list[dict],
    dry_run: bool,
) -> dict[str, int]:
    """Bulk index das ocorrências em mr_cprm_v001."""
    log.info("cprm.index.start", total=len(docs))
    total_ok = total_err = 0

    for i in range(0, len(docs), BATCH_INDEX):
        batch = list(iter_actions(docs[i:i + BATCH_INDEX]))
        if not dry_run:
            ok, errs = helpers.bulk(client, batch, raise_on_error=False)
            total_ok  += ok
            total_err += len(errs) if isinstance(errs, list) else errs
            if errs and isinstance(errs, list):
                for e in errs[:2]:
                    log.error("cprm.index.error", error=str(e)[:200])
        else:
            total_ok += len(batch)

        if i % (BATCH_INDEX * 10) == 0:
            log.info("cprm.index.progress",
                     done=total_ok + total_err,
                     total=len(docs))

    log.info("cprm.index.done",
             total_ok=total_ok, total_err=total_err, dry_run=dry_run)
    return {"ok": total_ok, "errors": total_err}


# ─────────────────────────────────────────────────────────────────────────────
# Validation summary
# ─────────────────────────────────────────────────────────────────────────────

def enrich_jazidas_cprm(
    client: OpenSearch,
    cprm_geojson: Path,
    radius_km: float = 10.0,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Enriquece mr_jazidas_v001 com ocorrências CPRM no raio de `radius_km` km.

    Para cada jazida com `location`, calcula:
        - n_ocorrencias_cprm: total de pontos CPRM no raio
        - cprm_substancias:   substâncias únicas das ocorrências próximas
        - cprm_ids_proximos:  até 5 IDs mais próximos

    Usa GeoPandas sjoin com buffer reprojetado em EPSG:5880.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    EPSG_METRIC = "EPSG:5880"  # SIRGAS 2000 / Brazil Polyconic
    RADIUS_M    = radius_km * 1000

    log.info("cprm.enrich.start", radius_km=radius_km)

    # ── Carrega pontos CPRM ────────────────────────────────────────────────
    cprm_data = json.loads(cprm_geojson.read_text(encoding="utf-8"))
    cprm_rows = []
    for feat in cprm_data.get("features", []):
        props = feat.get("properties") or {}
        geom  = feat.get("geometry")  or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        cprm_rows.append({
            "id_ocorrencia": str(props.get("ID_OCORRENCIA") or props.get("OBJECTID", "")),
            "substancia":    (props.get("SUBSTANCIAS") or "").split(",")[0].strip(),
            "geometry":      Point(lon, lat),
        })
    cprm_gdf = gpd.GeoDataFrame(cprm_rows, crs="EPSG:4326").to_crs(EPSG_METRIC)
    log.info("cprm.enrich.cprm_loaded", total=len(cprm_gdf))

    # ── Scroll jazidas ─────────────────────────────────────────────────────
    jazz_rows: list[dict] = []
    scroll_q = {
        "size": 2000,
        "_source": ["location"],
        "query": {"exists": {"field": "location"}},
    }
    resp = client.search(index="mr_jazidas_v001", body=scroll_q,
                         scroll="5m")
    scroll_id = resp["_scroll_id"]
    while True:
        hits = resp["hits"]["hits"]
        if not hits:
            break
        for hit in hits:
            loc = hit["_source"].get("location") or {}
            lat = loc.get("lat")
            lon = loc.get("lon")
            if lat is None or lon is None:
                continue
            jazz_rows.append({
                "_id":     hit["_id"],
                "geometry": Point(float(lon), float(lat)),
            })
        resp = client.scroll(scroll_id=scroll_id, scroll="5m")
        scroll_id = resp["_scroll_id"]
    client.clear_scroll(scroll_id=scroll_id)

    log.info("cprm.enrich.jazidas_loaded", total=len(jazz_rows))
    if not jazz_rows:
        return {"updated": 0}

    # ── Spatial join com buffer ────────────────────────────────────────────
    jazz_gdf = gpd.GeoDataFrame(jazz_rows, crs="EPSG:4326").to_crs(EPSG_METRIC)
    jazz_buf = jazz_gdf.copy()
    jazz_buf["geometry"] = jazz_buf.geometry.buffer(RADIUS_M)

    joined = gpd.sjoin(
        cprm_gdf,
        jazz_buf[["_id", "geometry"]],
        how="inner",
        predicate="within",
    )

    log.info("cprm.enrich.joined", total_pairs=len(joined))

    # Agrega por jazida
    agg = (
        joined
        .groupby("_id")
        .agg(
            n_ocorrencias_cprm=("id_ocorrencia", "count"),
            cprm_substancias=("substancia", lambda x: sorted(set(s for s in x if s))),
            cprm_ids_proximos=("id_ocorrencia", lambda x: list(x)[:5]),
        )
        .reset_index()
    )

    # ── Bulk update jazidas ────────────────────────────────────────────────
    def _update_actions(df) -> Iterator[dict]:
        for _, row in df.iterrows():
            yield {
                "_op_type":  "update",
                "_index":    "mr_jazidas_v001",
                "_id":       row["_id"],
                "doc": {
                    "n_ocorrencias_cprm": int(row["n_ocorrencias_cprm"]),
                    "cprm_substancias":   row["cprm_substancias"],
                    "cprm_ids_proximos":  row["cprm_ids_proximos"],
                },
            }

    # Zera jazidas sem ocorrências CPRM próximas
    def _reset_actions(all_ids: set[str], enriched_ids: set[str]) -> Iterator[dict]:
        for jid in all_ids - enriched_ids:
            yield {
                "_op_type":  "update",
                "_index":    "mr_jazidas_v001",
                "_id":       jid,
                "doc": {"n_ocorrencias_cprm": 0, "cprm_substancias": [], "cprm_ids_proximos": []},
            }

    all_jazz_ids   = {row["_id"] for row in jazz_rows}
    enriched_ids   = set(agg["_id"].tolist())
    log.info("cprm.enrich.to_update", enriched=len(enriched_ids), total=len(all_jazz_ids))

    updated_ok = updated_err = 0
    if not dry_run:
        ok1, err1 = helpers.bulk(
            client,
            list(_update_actions(agg)),
            raise_on_error=False,
        )
        ok2, err2 = helpers.bulk(
            client,
            list(_reset_actions(all_jazz_ids, enriched_ids)),
            raise_on_error=False,
        )
        updated_ok  = ok1 + ok2
        updated_err = (len(err1) if isinstance(err1, list) else err1) + \
                      (len(err2) if isinstance(err2, list) else err2)
    else:
        updated_ok = len(enriched_ids) + len(all_jazz_ids - enriched_ids)

    log.info("cprm.enrich.done",
             enriched=len(enriched_ids),
             no_cprm=len(all_jazz_ids - enriched_ids),
             ok=updated_ok, errors=updated_err, dry_run=dry_run)

    return {"updated": updated_ok, "errors": updated_err,
            "enriched": len(enriched_ids)}


def log_summary(client: OpenSearch, docs: list[dict]) -> None:
    """Log de distribuição por UF, substância, importância e status econômico."""
    from collections import Counter

    ufs        = Counter(d["uf"] or "?" for d in docs)
    subs       = Counter(d["substancia_principal"] or "?" for d in docs)
    importancias = Counter(d.get("importancia") or "Indeterminado" for d in docs)
    status_ec  = Counter(d.get("status_economico") or "Indeterminado" for d in docs)

    log.info("cprm.summary.uf",
             top5={k: v for k, v in ufs.most_common(5)})
    log.info("cprm.summary.substancia_principal",
             top10={k: v for k, v in subs.most_common(10)})
    log.info("cprm.summary.importancia",
             dist=dict(importancias.most_common()))
    log.info("cprm.summary.status_economico",
             dist=dict(status_ec.most_common()))

    client.indices.refresh(index=INDEX_CPRM)
    r = client.search(index=INDEX_CPRM, body={
        "size": 0,
        "track_total_hits": True,
        "query": {"match_all": {}},
    })
    log.info("cprm.index.total_docs",
             total=r["hits"]["total"]["value"])


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--index",          "do_index",    is_flag=True,
              help="Baixa e indexa mr_cprm_v001.")
@click.option("--enrich-jazidas", is_flag=True,
              help="Correlaciona ocorrências CPRM com mr_jazidas_v001 (raio 10km).")
@click.option("--all",            "do_all",      is_flag=True,
              help="Executa --index + --enrich-jazidas.")
@click.option("--skip-download",  is_flag=True,
              help="Reutiliza cache local.")
@click.option("--radius-km",      default=10.0,  show_default=True,
              help="Raio em km para correlação CPRM-jazidas.")
@click.option("--dry-run",        is_flag=True,
              help="Simula sem escrever no OpenSearch.")
@click.option("--data-dir",       default=None,
              help="Diretório de cache (default: etl_data_dir/cprm).")
def main(
    do_index: bool,
    enrich_jazidas: bool,
    do_all: bool,
    skip_download: bool,
    radius_km: float,
    dry_run: bool,
    data_dir: str | None,
) -> None:
    """bot_cprm — SGB/CPRM GeoPortal → mr_cprm_v001."""
    t0 = time.time()

    _data_dir = Path(data_dir) if data_dir else settings.etl_data_dir / "cprm"
    _data_dir.mkdir(parents=True, exist_ok=True)

    if do_all:
        do_index = enrich_jazidas = True

    if not do_index and not enrich_jazidas:
        log.warning("bot_cprm.no_action",
                    msg="Use --index, --enrich-jazidas ou --all.")
        return

    client = get_os_client()

    if do_index:
        # ── Step 1: Download
        geojson_path = download_cprm(_data_dir, skip=skip_download)

        # ── Step 2: Parse
        docs = load_docs(geojson_path)

        # ── Step 3: Index
        res = bulk_index(client, docs, dry_run=dry_run)

        # ── Step 4: Summary
        if not dry_run:
            log_summary(client, docs)

        elapsed = round(time.time() - t0, 1)
        log.info("bot_cprm.index.done",
                 elapsed_s=elapsed, total=len(docs), dry_run=dry_run, **res)

    if enrich_jazidas:
        # Garante que o cache GeoJSON existe
        geojson_path = _data_dir / CACHE_FILE
        if not geojson_path.exists():
            geojson_path = download_cprm(_data_dir, skip=False)

        enrich_result = enrich_jazidas_cprm(
            client, geojson_path, radius_km=radius_km, dry_run=dry_run
        )
        log.info("bot_cprm.enrich.done",
                 elapsed_s=round(time.time() - t0, 1),
                 dry_run=dry_run,
                 **enrich_result)

    log.info("bot_cprm.done", elapsed_s=round(time.time() - t0, 1))


if __name__ == "__main__":
    main()
