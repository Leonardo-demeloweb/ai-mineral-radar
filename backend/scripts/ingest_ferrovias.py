"""
ingest_ferrovias.py — Malha ferroviária (linhas) → OpenSearch (mr_ferrovias_v001)
================================================================================

Indexa feições **LineString** / **MultiLineString** de shapefile(s) dentro de um
ZIP (padrão: Declaração de Rede ANTT — Malha Ferroviária Federal). Cada registo
linear vira um documento com ``geo_shape`` + ``centroide`` (WGS84).

**IDs únicos:** ``ferrovia_id`` = ``antt-{ano}-{layer}-{OBJECTID|FID|índice}``
quando existir coluna de id; caso contrário ``antt-{ano}-h{hash16}``.

Pré-requisitos:
  - ``pip install -r requirements.txt`` (geopandas + fiona)
  - Índice: ``python -m scripts.setup_indices --index mr_ferrovias_v001``
  - Variáveis OpenSearch no .env (igual aos outros scripts)

Uso (a partir da pasta ``backend/``)::

    PYTHONPATH=. python -m scripts.ingest_ferrovias --help
    PYTHONPATH=. python -m scripts.ingest_ferrovias --dry-run
    PYTHONPATH=. python -m scripts.ingest_ferrovias --zip-path "$HOME/Downloads/malha-ferroviaria-federal-shp.zip"
    PYTHONPATH=. python -m scripts.ingest_ferrovias --download   # tenta URL default ANTT
    PYTHONPATH=. python -m scripts.ingest_ferrovias --simplify 0.0003 --dedupe

Se ``--download`` falhar (403 do gov.br), baixe o ZIP manualmente no browser e
passe ``--zip-path``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import httpx
import pandas as pd
from opensearchpy import OpenSearch, helpers
from shapely.geometry import mapping as shapely_mapping

try:
    import geopandas as gpd
except ImportError as e:  # pragma: no cover
    print("Instale geopandas: pip install geopandas fiona", file=sys.stderr)
    raise e

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_servers.common.config import mcp_settings
from scripts.setup_indices import ALL_INDICES, create_index

log = logging.getLogger("ingest_ferrovias")

INDEX = "mr_ferrovias_v001"

# Declaração de Rede 2024 — Malha Ferroviária Federal (SHP). Pode exigir download manual.
DEFAULT_ZIP_URL = (
    "https://www.gov.br/antt/pt-br/assuntos/ferrovias/declaracao-de-rede/"
    "declaracao-de-rede-2024/malha-ferroviaria-federal-shp.zip"
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 MineralRadar-ingest/1.0"
)


def get_os_client(timeout: int = 120) -> OpenSearch:
    endpoint = mcp_settings.opensearch_endpoint or "http://localhost:9200"
    kwargs: dict[str, Any] = {
        "hosts": [endpoint],
        "use_ssl": mcp_settings.opensearch_use_ssl,
        "verify_certs": mcp_settings.opensearch_verify_certs,
        "timeout": timeout,
    }
    if mcp_settings.opensearch_user and mcp_settings.opensearch_password:
        kwargs["http_auth"] = (mcp_settings.opensearch_user, mcp_settings.opensearch_password)
    return OpenSearch(**kwargs)


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm_key(s: str) -> str:
    return _strip_accents(str(s)).lower().replace(" ", "_")


def _first_scalar(row: pd.Series, candidates: list[str]) -> str | None:
    idx = {_norm_key(c): c for c in row.index}
    for cand in candidates:
        key = _norm_key(cand)
        col = idx.get(key)
        if col is None:
            continue
        v = row[col]
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        if isinstance(v, str):
            t = v.strip()
            if t:
                return t
        else:
            return str(v).strip()
    return None


def _first_float(row: pd.Series, candidates: list[str]) -> float | None:
    raw = _first_scalar(row, candidates)
    if not raw:
        return None
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _slug_layer(inner_path: str) -> str:
    base = Path(inner_path).stem
    s = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()
    return (s[:48] or "layer")


def _fid_from_row(row: pd.Series) -> int | None:
    for cand in ("OBJECTID", "FID", "OID_", "objectid", "fid"):
        v = _first_scalar(row, [cand])
        if v is None:
            continue
        try:
            return int(float(v.replace(",", ".")))
        except ValueError:
            continue
    return None


def _omit_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def list_shp_inside_zip(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return sorted(n for n in zf.namelist() if n.lower().endswith(".shp"))


def read_linear_gdf(zip_path: Path) -> gpd.GeoDataFrame:
    """Lê todos os .shp do ZIP e concatena só geometrias lineares."""
    inner_paths = list_shp_inside_zip(zip_path)
    if not inner_paths:
        raise RuntimeError(f"ZIP sem .shp: {zip_path}")

    zuri = zip_path.resolve().as_posix()
    frames: list[gpd.GeoDataFrame] = []
    for inner in inner_paths:
        uri = f"zip://{zuri}!{inner}"
        try:
            gdf = gpd.read_file(uri)
        except Exception as exc:
            log.warning("Ignorar %s: %s", inner, exc)
            continue
        if gdf.empty:
            continue
        gdf = gdf.rename(columns=lambda c: str(c).strip().lstrip("\ufeff"))
        types = gdf.geometry.geom_type
        mask = types.isin(("LineString", "MultiLineString"))
        gdf = gdf.loc[mask].copy()
        if gdf.empty:
            continue
        gdf["_mr_layer"] = inner
        frames.append(gdf)

    if not frames:
        raise RuntimeError(
            "Nenhuma camada com LineString/MultiLineString no ZIP. "
            "Confirme que é a malha linear (não só pontos/polígonos)."
        )
    merged = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True))
    if merged.crs is None and frames[0].crs is not None:
        merged.set_crs(frames[0].crs, inplace=True)
    return merged


def download_zip(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=300.0,
    ) as client:
        r = client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
    log.info("Download gravado em %s (%d bytes)", dest, dest.stat().st_size)


def row_to_doc(
    row: pd.Series,
    *,
    ano: int,
    seq: int,
    simplify_tol: float | None,
) -> tuple[str, dict[str, Any]] | None:
    geom = row.geometry
    if geom is None or geom.is_empty:
        return None
    if simplify_tol and simplify_tol > 0:
        geom = geom.simplify(simplify_tol, preserve_topology=True)
        if geom.is_empty:
            return None

    layer = str(row["_mr_layer"]) if "_mr_layer" in row.index else "unknown"
    layer_slug = _slug_layer(layer)

    fid = _fid_from_row(row)
    if fid is not None:
        ferrovia_id = f"antt-{ano}-{layer_slug}-{fid}"
    else:
        wkt = geom.wkb_hex[:4000]
        h = hashlib.sha256(f"{ano}|{layer}|{seq}|{wkt}".encode()).hexdigest()[:16]
        ferrovia_id = f"antt-{ano}-{layer_slug}-h{h}"

    nome = _first_scalar(row, ["NOME", "NM_LINHA", "LINHA", "NM_REDE", "DESCRICAO", "NOME_EF"])
    codigo = _first_scalar(
        row,
        ["SIGLA_EF", "SIGLA", "SIGLA_FER", "CD_LINHA", "CODIGO", "COD_LINH", "SIGLA_LINHA"],
    )
    operadora = _first_scalar(
        row,
        ["OPERADORA", "CONCESSION", "CONCESSIONARIA", "NM_OPERADO", "EMPRESA"],
    )
    ext_km = _first_float(row, ["EXTENSAO_KM", "EXT_KM", "KM", "COMPRIMENT", "LENGTH", "Shape_Leng"])
    uf = _first_scalar(row, ["UF", "SIGLA_UF", "SG_UF"])
    tipo = _first_scalar(row, ["TIPO", "CLASSE", "TIPO_MALH"])

    centroid = geom.centroid
    gj = shapely_mapping(geom)
    if gj.get("type") not in ("LineString", "MultiLineString"):
        return None

    nome_f = nome or codigo or ferrovia_id
    nome_norm = _strip_accents(nome_f).lower()[:256]

    doc: dict[str, Any] = {
        "ferrovia_id": ferrovia_id,
        "codigo_sigla": (codigo or nome_f)[:128],
        "nome": nome_f[:512],
        "nome_normalizado": nome_norm,
        "operadora": operadora[:256] if operadora else None,
        "extensao_km": ext_km,
        "uf": uf.upper()[:8] if uf else None,
        "tipo_malha": (tipo[:64] if tipo else "federal"),
        "geom": gj,
        "centroide": {"lat": float(centroid.y), "lon": float(centroid.x)},
        "fonte": "ANTT_DECLARACAO_REDE",
        "ano_referencia": ano,
        "shapefile_layer": layer[-256:],
        "shapefile_fid": int(fid) if fid is not None else None,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    }
    return ferrovia_id, _omit_none(doc)


@click.command()
@click.option(
    "--zip-path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help=(
        "Caminho absoluto ou relativo para o ZIP da malha (ficheiro real no disco; "
        "não use placeholders). Ex.: ~/Downloads/malha-ferroviaria-federal-shp.zip"
    ),
)
@click.option("--download", is_flag=True, default=False,
              help=f"Baixa o ZIP de --url (default ANTT) para --cache-dir e indexa.")
@click.option("--url", default=DEFAULT_ZIP_URL, show_default=True,
              help="URL do ZIP quando --download.")
@click.option("--cache-dir", type=click.Path(path_type=Path), default=None,
              help="Pasta para guardar o ZIP descarregado (default: backend/data/cache).")
@click.option("--ano", type=int, default=2024, show_default=True,
              help="Ano de referência (metadado + prefixo de id).")
@click.option("--simplify", type=float, default=0.0, show_default=True,
              help="Tolerância graus WGS84 para simplificar geometria (0=desligado).")
@click.option("--dedupe", is_flag=True, default=False,
              help="Remove duplicados exactos por ferrovia_id (útil se concat de camadas repetir).")
@click.option("--dry-run", is_flag=True, help="Não grava no OpenSearch.")
@click.option("--recreate-index", is_flag=True, help="Recria o índice antes do bulk.")
@click.option("--verbose", "-v", is_flag=True)
def main(
    zip_path: Path | None,
    download: bool,
    url: str,
    cache_dir: Path | None,
    ano: int,
    simplify: float,
    dedupe: bool,
    dry_run: bool,
    recreate_index: bool,
    verbose: bool,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s  %(message)s",
    )
    backend_root = Path(__file__).resolve().parents[1]
    cache = cache_dir or (backend_root / "data" / "cache")
    cache.mkdir(parents=True, exist_ok=True)

    zpath = zip_path
    if download:
        dest = cache / "malha_ferroviaria_federal_antt.zip"
        log.info("A descarregar: %s", url)
        try:
            download_zip(url, dest)
        except httpx.HTTPError as exc:
            log.error("Falha no download (%s). Use browser e --zip-path. Erro: %s", url, exc)
            sys.exit(1)
        zpath = dest
    if zpath is None:
        log.error("Indique --zip-path ou --download.")
        sys.exit(1)

    log.info("A ler shapefiles de %s", zpath)
    gdf = read_linear_gdf(zpath)
    if gdf.crs is None:
        log.warning("CRS ausente no shapefile — a assumir EPSG:4674 (SIRGAS 2000)")
        gdf = gdf.set_crs(4674, allow_override=True)
    gdf = gdf.to_crs(4326)
    log.info("Feicoes lineares: %d", len(gdf))

    simplify_tol = simplify if simplify > 0 else None
    actions: list[dict[str, Any]] = []
    seen_id: set[str] = set()

    for seq, (_, row) in enumerate(gdf.iterrows()):
        parsed = row_to_doc(row, ano=ano, seq=seq, simplify_tol=simplify_tol)
        if not parsed:
            continue
        ferrovia_id, doc = parsed
        if dedupe:
            if ferrovia_id in seen_id:
                continue
            seen_id.add(ferrovia_id)
        actions.append(
            {"_op_type": "index", "_index": INDEX, "_id": ferrovia_id, "_source": doc}
        )

    log.info("Documentos preparados: %d", len(actions))
    if dry_run:
        if verbose and actions:
            log.debug(json.dumps(actions[0]["_source"], ensure_ascii=False, indent=2)[:3000])
        log.info("dry-run: não enviado ao OpenSearch")
        return

    client = get_os_client()
    body = ALL_INDICES[INDEX]["body"]
    create_index(client, INDEX, body, recreate=recreate_index)

    success, failed = helpers.bulk(
        client,
        actions,
        refresh="wait_for",
        raise_on_error=False,
        chunk_size=300,
    )
    if failed:
        log.error("bulk com falhas: ok=%s erros=%s", success, failed[:3])
        sys.exit(1)
    log.info("bulk concluído: %d documentos em %s", success, INDEX)


if __name__ == "__main__":
    main()
