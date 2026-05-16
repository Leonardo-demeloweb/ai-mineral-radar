"""
bot_municipios.py — IBGE Malha Municipal → OpenSearch (mr_municipios_v001)

Fluxo:
  1. Download do shapefile BR_Municipios_YYYY.zip do IBGE GeoFTP
  2. Parse com GeoPandas + reprojeção para WGS84
  3. Indexa 5.570 municípios com geo_shape (polígono) + geo_point (centróide)
  4. (Opcional) --enrich-jazidas: spatial join para adicionar codigo_ibge
     e nome_municipio aos documentos de mr_jazidas_v001

Por que indexar municípios separadamente:
  - Habilita filtro "jazidas dentro do município X" via geo_shape query
  - Fornece polígonos para o mapa sem precisar do PostGIS
  - Permite resolver nomes de municípios com typos/variações via BM25

Uso:
  python -m bots.bot_municipios                     # indexa todos os municípios
  python -m bots.bot_municipios --skip-download      # reusar shapefile já baixado
  python -m bots.bot_municipios --uf MG              # só Minas Gerais (teste)
  python -m bots.bot_municipios --enrich-jazidas     # enriquece mr_jazidas_v001
  python -m bots.bot_municipios --enrich-only        # só enriquece (sem baixar/indexar)
  python -m bots.bot_municipios --dry-run            # não escreve no OpenSearch
"""
from __future__ import annotations

import json
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

INDEX_MUNICIPIOS = "mr_municipios_v001"
INDEX_JAZIDAS    = "mr_jazidas_v001"
DEFAULT_BATCH    = 50  # geo_shape payloads são grandes; batches menores evitam timeout

# Tolerância de simplificação geométrica (~100m em graus decimais a latitudes brasileiras).
# Reduz drasticamente o tamanho do JSON mantendo precisão adequada para queries geo.
GEOMETRY_SIMPLIFY_TOLERANCE = 0.001

HEADERS = {"User-Agent": settings.anm_user_agent}

# IBGE GeoFTP — tenta 2023, fallback 2022
IBGE_URLS = [
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/municipio_2023/Brasil/BR/BR_Municipios_2023.zip",
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/municipio_2022/Brasil/BR/BR_Municipios_2022.zip",
]

# ─────────────────────────────────────────────────────────────────────────────
# OpenSearch client — timeout maior para geo_shape bulk
# ─────────────────────────────────────────────────────────────────────────────

def get_os_client(timeout: int = 120) -> OpenSearch:
    """Cliente OpenSearch com timeout configurável (padrão 120s para bulk de geo_shape)."""
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
# Tabelas de referência IBGE
# ─────────────────────────────────────────────────────────────────────────────

UF_MAP: dict[str, dict[str, str]] = {
    "11": {"sigla": "RO", "nome": "Rondônia",           "regiao": "Norte"},
    "12": {"sigla": "AC", "nome": "Acre",                "regiao": "Norte"},
    "13": {"sigla": "AM", "nome": "Amazonas",            "regiao": "Norte"},
    "14": {"sigla": "RR", "nome": "Roraima",             "regiao": "Norte"},
    "15": {"sigla": "PA", "nome": "Pará",                "regiao": "Norte"},
    "16": {"sigla": "AP", "nome": "Amapá",               "regiao": "Norte"},
    "17": {"sigla": "TO", "nome": "Tocantins",           "regiao": "Norte"},
    "21": {"sigla": "MA", "nome": "Maranhão",            "regiao": "Nordeste"},
    "22": {"sigla": "PI", "nome": "Piauí",               "regiao": "Nordeste"},
    "23": {"sigla": "CE", "nome": "Ceará",               "regiao": "Nordeste"},
    "24": {"sigla": "RN", "nome": "Rio Grande do Norte", "regiao": "Nordeste"},
    "25": {"sigla": "PB", "nome": "Paraíba",             "regiao": "Nordeste"},
    "26": {"sigla": "PE", "nome": "Pernambuco",          "regiao": "Nordeste"},
    "27": {"sigla": "AL", "nome": "Alagoas",             "regiao": "Nordeste"},
    "28": {"sigla": "SE", "nome": "Sergipe",             "regiao": "Nordeste"},
    "29": {"sigla": "BA", "nome": "Bahia",               "regiao": "Nordeste"},
    "31": {"sigla": "MG", "nome": "Minas Gerais",        "regiao": "Sudeste"},
    "32": {"sigla": "ES", "nome": "Espírito Santo",      "regiao": "Sudeste"},
    "33": {"sigla": "RJ", "nome": "Rio de Janeiro",      "regiao": "Sudeste"},
    "35": {"sigla": "SP", "nome": "São Paulo",           "regiao": "Sudeste"},
    "41": {"sigla": "PR", "nome": "Paraná",              "regiao": "Sul"},
    "42": {"sigla": "SC", "nome": "Santa Catarina",      "regiao": "Sul"},
    "43": {"sigla": "RS", "nome": "Rio Grande do Sul",   "regiao": "Sul"},
    "50": {"sigla": "MS", "nome": "Mato Grosso do Sul",  "regiao": "Centro-Oeste"},
    "51": {"sigla": "MT", "nome": "Mato Grosso",         "regiao": "Centro-Oeste"},
    "52": {"sigla": "GO", "nome": "Goiás",               "regiao": "Centro-Oeste"},
    "53": {"sigla": "DF", "nome": "Distrito Federal",    "regiao": "Centro-Oeste"},
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase sem acentos para nome_normalizado."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _safe_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────────────────────

def download_shapefile(data_dir: Path, skip: bool) -> Path:
    """
    Baixa o ZIP da malha municipal IBGE (tenta 2023, fallback 2022).
    Retorna path do ZIP local.
    """
    data_dir.mkdir(parents=True, exist_ok=True)

    for url in IBGE_URLS:
        year = url.split("municipio_")[1][:4]
        dest = data_dir / f"BR_Municipios_{year}.zip"

        if skip and dest.exists():
            log.info("municipios.download.skip", file=dest.name,
                     size_mb=round(dest.stat().st_size / 1e6, 1))
            return dest

        log.info("municipios.download.start", url=url, year=year)
        try:
            with httpx.stream("GET", url, headers=HEADERS, timeout=300,
                              follow_redirects=True) as resp:
                if resp.status_code == 404:
                    log.warning("municipios.download.not_found", url=url)
                    continue
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    written = 0
                    for chunk in resp.iter_bytes(chunk_size=512 * 1024):
                        f.write(chunk)
                        written += len(chunk)
                        if written % (10 * 1024 * 1024) < 512 * 1024:
                            log.info("municipios.download.progress",
                                     mb=round(written / 1e6, 1))
            log.info("municipios.download.done", year=year,
                     size_mb=round(dest.stat().st_size / 1e6, 1))
            return dest
        except Exception as exc:
            log.warning("municipios.download.error", url=url, error=str(exc)[:200])
            if dest.exists():
                dest.unlink()

    raise RuntimeError("Não foi possível baixar o shapefile da malha municipal IBGE.")


# ─────────────────────────────────────────────────────────────────────────────
# Parse shapefile
# ─────────────────────────────────────────────────────────────────────────────

def load_municipios_gdf(zip_path: Path, uf_filter: list[str] | None = None) -> gpd.GeoDataFrame:
    """
    Carrega o shapefile da malha municipal e reprojecta para WGS84.
    Retorna GeoDataFrame com colunas normalizadas.
    """
    log.info("municipios.parse.start", zip=zip_path.name)

    with zipfile.ZipFile(zip_path) as zf:
        shp_files = [f for f in zf.namelist() if f.lower().endswith(".shp")]
        if not shp_files:
            raise ValueError(f"Nenhum .shp encontrado em {zip_path}")
        extract_dir = zip_path.parent / zip_path.stem
        extract_dir.mkdir(exist_ok=True)
        zf.extractall(extract_dir)

    gdf = gpd.read_file(extract_dir / shp_files[0])
    log.info("municipios.parse.loaded",
             rows=len(gdf), crs=str(gdf.crs), cols=list(gdf.columns))

    # Reprojetar para WGS84
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        log.info("municipios.parse.reproject", from_epsg=gdf.crs.to_epsg())
        gdf = gdf.to_crs(epsg=4326)

    # Normalizar nomes de colunas para UPPERCASE — preserva "geometry" (coluna especial GeoPandas)
    gdf.columns = [c.upper() if c.lower() != "geometry" else c for c in gdf.columns]

    # Detectar coluna do código IBGE (CD_MUN ou CD_GEOCODM etc.)
    cd_col = next((c for c in gdf.columns if c.startswith("CD_") and "MUN" in c), None)
    nm_col = next((c for c in gdf.columns if c.startswith("NM_") and "MUN" in c), None)
    uf_col = next((c for c in gdf.columns if "SIGLA" in c and "UF" in c), None) or \
             next((c for c in gdf.columns if c == "UF"), None)
    area_col = next((c for c in gdf.columns if "AREA" in c), None)

    log.info("municipios.parse.cols_detected",
             cd=cd_col, nm=nm_col, uf=uf_col, area=area_col)

    if not cd_col or not nm_col:
        raise ValueError(f"Colunas CD_MUN / NM_MUN não encontradas. Colunas: {list(gdf.columns)}")

    # Filtrar por UF se solicitado
    if uf_filter and uf_col:
        uf_upper = [u.upper() for u in uf_filter]
        gdf = gdf[gdf[uf_col].str.upper().isin(uf_upper)]
        log.info("municipios.parse.uf_filter", ufs=uf_upper, rows=len(gdf))

    gdf = gdf.rename(columns={
        cd_col:   "CD_MUN",
        nm_col:   "NM_MUN",
    })
    if uf_col:
        gdf = gdf.rename(columns={uf_col: "SIGLA_UF"})
    if area_col:
        gdf = gdf.rename(columns={area_col: "AREA_KM2"})

    log.info("municipios.parse.done", rows=len(gdf))
    return gdf


# ─────────────────────────────────────────────────────────────────────────────
# Converter row → documento OpenSearch
# ─────────────────────────────────────────────────────────────────────────────

def row_to_doc(row) -> dict | None:
    """Converte uma linha do GeoDataFrame em documento para mr_municipios_v001."""
    cd_mun = str(row.get("CD_MUN", "") or "").strip().zfill(7)
    nm_mun = str(row.get("NM_MUN", "") or "").strip()
    if not cd_mun or not nm_mun:
        return None

    sigla_uf = str(row.get("SIGLA_UF", "") or "").strip().upper()
    area_km2 = _safe_float(row.get("AREA_KM2"))

    # Lookup de UF → nome + região usando o prefixo de 2 dígitos do código IBGE
    estado_cod = cd_mun[:2]
    uf_info = UF_MAP.get(estado_cod, {})
    if not sigla_uf and uf_info:
        sigla_uf = uf_info.get("sigla", "")

    geom = row.get("geometry")
    if geom is None or geom.is_empty:
        return None

    # Centróide — garantir que está dentro do polígono (use representative_point para geometrias côncavas)
    centroid = geom.centroid
    if not geom.contains(centroid):
        centroid = geom.representative_point()

    # Simplificar geometria para reduzir payload JSON (~10x menor, mantém precisão ~100m)
    try:
        geom_simple = geom.simplify(GEOMETRY_SIMPLIFY_TOLERANCE, preserve_topology=True)
        if geom_simple.is_empty:
            geom_simple = geom  # fallback para original se simplificação destruiu a forma
        poligono_geojson = mapping(geom_simple)
    except Exception:
        try:
            poligono_geojson = mapping(geom)
        except Exception:
            return None

    return {
        "codigo_ibge":      cd_mun,
        "nome":             nm_mun,
        "nome_normalizado": _normalize(nm_mun),
        "uf":               sigla_uf,
        "uf_nome":          uf_info.get("nome", ""),
        "regiao":           uf_info.get("regiao", ""),
        "area_km2":         area_km2,
        "centroide": {
            "lat": round(centroid.y, 6),
            "lon": round(centroid.x, 6),
        },
        "poligono":         poligono_geojson,
        "indexed_at":       datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Indexação
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_index(client: OpenSearch) -> None:
    """Cria mr_municipios_v001 se não existir (usa MR_MUNICIPIOS de setup_indices.py)."""
    if client.indices.exists(index=INDEX_MUNICIPIOS):
        log.info("municipios.index.exists")
        return

    setup_path = (Path(__file__).parent.parent.parent
                  / "backend" / "scripts" / "setup_indices.py")
    mapping_body = None
    try:
        ns: dict = {}
        src = setup_path.read_text()
        # Executa o módulo de setup para obter MR_MUNICIPIOS
        exec(compile(src, str(setup_path), "exec"), ns)  # noqa: S102
        mapping_body = ns.get("MR_MUNICIPIOS")
    except Exception as exc:
        log.warning("municipios.index.mapping_fallback", error=str(exc)[:200])

    if mapping_body is None:
        mapping_body = {
            "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
            "mappings": {
                "properties": {
                    "codigo_ibge":      {"type": "keyword"},
                    "nome":             {"type": "text",
                                        "fields": {"keyword": {"type": "keyword"}}},
                    "nome_normalizado": {"type": "keyword"},
                    "uf":               {"type": "keyword"},
                    "uf_nome":          {"type": "keyword"},
                    "regiao":           {"type": "keyword"},
                    "area_km2":         {"type": "double"},
                    "populacao":        {"type": "long"},
                    "centroide":        {"type": "geo_point"},
                    "poligono":         {"type": "geo_shape"},
                    "indexed_at":       {"type": "date"},
                }
            },
        }

    client.indices.create(index=INDEX_MUNICIPIOS, body=mapping_body)
    log.info("municipios.index.created")


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
            "_index":  INDEX_MUNICIPIOS,
            "_id":     doc["codigo_ibge"],
            "_source": doc,
        }
    log.info("municipios.iter.done", ok=ok, skipped=skipped)


def bulk_index(client: OpenSearch, gdf: gpd.GeoDataFrame, batch_size: int) -> None:
    """Faz bulk index dos municípios em batches."""
    total_ok = total_err = 0
    batch: list[dict] = []

    for action in iter_actions(gdf):
        batch.append(action)
        if len(batch) >= batch_size:
            ok, errs = helpers.bulk(client, batch, raise_on_error=False)
            total_ok  += ok
            total_err += len(errs) if isinstance(errs, list) else errs
            log.info("municipios.bulk.progress",
                     indexed=total_ok, errors=total_err)
            batch = []

    if batch:
        ok, errs = helpers.bulk(client, batch, raise_on_error=False)
        total_ok  += ok
        total_err += len(errs) if isinstance(errs, list) else errs

    log.info("municipios.bulk.done", total_ok=total_ok, total_err=total_err)


# ─────────────────────────────────────────────────────────────────────────────
# Enriquecimento de mr_jazidas_v001 via spatial join
# ─────────────────────────────────────────────────────────────────────────────

def enrich_jazidas_municipio(
    client: OpenSearch,
    gdf_mun: gpd.GeoDataFrame,
    batch_size: int,
    dry_run: bool,
) -> None:
    """
    Adiciona `codigo_ibge` em mr_jazidas_v001 usando spatial join (ponto-em-polígono).

    Estratégia:
      1. Carrega locations (lat/lon) de todos os documentos de mr_jazidas_v001
      2. Converte para GeoDataFrame de pontos
      3. Faz sjoin com os polígonos municipais (within)
      4. Bulk update dos documentos com codigo_ibge + nome_municipio enriquecido

    Nota: exige que mr_municipios_v001 já esteja indexado OU que gdf_mun seja passado.
    """
    log.info("enrich_jazidas.start")

    # ── 1. Scroll todos os processos + locations de mr_jazidas_v001 ──
    scroll_size = 2000
    resp = client.search(
        index=INDEX_JAZIDAS,
        scroll="10m",
        size=scroll_size,
        body={
            "_source": ["location", "municipio"],
            "query": {"exists": {"field": "location"}},
        },
    )
    scroll_id = resp["_scroll_id"]
    hits = resp["hits"]["hits"]
    total = resp["hits"]["total"]["value"]
    log.info("enrich_jazidas.scroll.start", total=total)

    # Coleta todos os hits em memória (79K × ~50 bytes = ~4MB, ok)
    all_hits: list[dict] = []
    while hits:
        all_hits.extend(hits)
        resp = client.scroll(scroll_id=scroll_id, scroll="10m")
        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]

    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    log.info("enrich_jazidas.loaded", total=len(all_hits))

    # ── 2. Cria GeoDataFrame de pontos ──
    import pandas as pd
    from shapely.geometry import Point

    rows = []
    for h in all_hits:
        src = h.get("_source", {})
        loc = src.get("location") or {}
        lat = loc.get("lat")
        lon = loc.get("lon")
        if lat is not None and lon is not None:
            rows.append({
                "_id":       h["_id"],
                "municipio": src.get("municipio", ""),
                "geometry":  Point(float(lon), float(lat)),
            })

    if not rows:
        log.warning("enrich_jazidas.no_points")
        return

    gdf_pts = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    log.info("enrich_jazidas.points_built", n=len(gdf_pts))

    # ── 3. Spatial join: ponto dentro de polígono ──
    gdf_mun_simple = gdf_mun[["CD_MUN", "NM_MUN", "geometry"]].copy()
    gdf_mun_simple = gdf_mun_simple.rename(columns={
        "CD_MUN": "_codigo_ibge",
        "NM_MUN": "_nome_municipio",
    })

    log.info("enrich_jazidas.sjoin.start",
             n_points=len(gdf_pts), n_polygons=len(gdf_mun_simple))

    gdf_joined = gpd.sjoin(
        gdf_pts,
        gdf_mun_simple,
        how="left",
        predicate="within",
    )

    matched = gdf_joined["_codigo_ibge"].notna().sum()
    log.info("enrich_jazidas.sjoin.done",
             matched=int(matched), total=len(gdf_joined),
             pct=round(matched / len(gdf_joined) * 100, 1))

    if dry_run:
        sample = gdf_joined[gdf_joined["_codigo_ibge"].notna()].head(5)
        for _, r in sample.iterrows():
            log.info("dry_run.enrich_sample",
                     id=r["_id"], codigo=r["_codigo_ibge"],
                     municipio=r["_nome_municipio"])
        return

    # ── 4. Bulk update ──
    # Garante que o campo existe no mapping
    try:
        client.indices.put_mapping(
            index=INDEX_JAZIDAS,
            body={"properties": {
                "codigo_ibge": {"type": "keyword"},
            }},
        )
    except Exception as exc:
        log.warning("enrich_jazidas.mapping_skip", error=str(exc)[:100])

    actions = []
    for _, row in gdf_joined.iterrows():
        codigo = row.get("_codigo_ibge")
        if pd.isna(codigo) or not codigo:
            continue
        actions.append({
            "_op_type": "update",
            "_index":   INDEX_JAZIDAS,
            "_id":      row["_id"],
            "doc": {
                "codigo_ibge":    str(codigo),
            },
        })

        if len(actions) >= batch_size:
            ok, errs = helpers.bulk(client, actions, raise_on_error=False)
            log.info("enrich_jazidas.update.progress",
                     ok=ok, errs=errs if isinstance(errs, int) else len(errs))
            actions = []

    if actions:
        ok, errs = helpers.bulk(client, actions, raise_on_error=False)
        log.info("enrich_jazidas.update.progress",
                 ok=ok, errs=errs if isinstance(errs, int) else len(errs))

    log.info("enrich_jazidas.done", matched=int(matched))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--skip-download", is_flag=True,
              help="Reusar shapefile já baixado (pula download)")
@click.option("--uf", "ufs", multiple=True,
              help="Filtrar por UF(s) — pode repetir: --uf MG --uf SP")
@click.option("--enrich-jazidas", is_flag=True,
              help="Após indexar, faz spatial join para adicionar codigo_ibge em mr_jazidas_v001")
@click.option("--enrich-only", is_flag=True,
              help="Só faz o enriquecimento (pula download e indexação)")
@click.option("--batch-size", default=DEFAULT_BATCH, show_default=True,
              help="Documentos por batch no bulk index")
@click.option("--dry-run", is_flag=True,
              help="Não escreve no OpenSearch")
def main(
    skip_download: bool,
    ufs: tuple[str, ...],
    enrich_jazidas: bool,
    enrich_only: bool,
    batch_size: int,
    dry_run: bool,
) -> None:
    """IBGE Malha Municipal → mr_municipios_v001 (+ enriquece mr_jazidas_v001)."""
    t0 = time.time()
    client = get_os_client()
    data_dir = settings.etl_data_dir / "municipios"
    uf_filter = list(ufs) if ufs else None

    # ── Download + parse do shapefile ──
    if not enrich_only:
        zip_path = download_shapefile(data_dir, skip=skip_download)
        gdf = load_municipios_gdf(zip_path, uf_filter=uf_filter)

        log.info("municipios.summary",
                 total=len(gdf), ufs=uf_filter or "todas")

        if dry_run:
            sample_doc = None
            for _, row in gdf.head(3).iterrows():
                d = row_to_doc(row)
                if d:
                    sample_doc = {k: v for k, v in d.items() if k != "poligono"}
                    log.info("dry_run.sample", **sample_doc)
            log.info("dry_run.municipios", would_index=len(gdf))
        else:
            _ensure_index(client)
            bulk_index(client, gdf, batch_size)
    else:
        # Enrich-only: precisa carregar o shapefile para o spatial join
        zip_path = download_shapefile(data_dir, skip=True)
        gdf = load_municipios_gdf(zip_path, uf_filter=uf_filter)

    # ── Enriquecimento de mr_jazidas_v001 ──
    if enrich_jazidas or enrich_only:
        log.info("--- enriquecimento mr_jazidas_v001 ---")
        enrich_jazidas_municipio(client, gdf, batch_size, dry_run)

    elapsed = round(time.time() - t0, 1)
    log.info("bot_municipios.done", elapsed_s=elapsed, dry_run=dry_run)


if __name__ == "__main__":
    main()
