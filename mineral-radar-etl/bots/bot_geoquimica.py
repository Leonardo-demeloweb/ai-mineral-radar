"""
bot_geoquimica.py — SGB/CPRM Geoquímica OGC API → mr_geoquimica_v001
======================================================================

Indexa análises geoquímicas do Serviço Geológico do Brasil (SGB/CPRM)
usando a API OGC Features padrão.

Coleções indexadas (OGC ``numberMatched``, 2026):
  analises-rocha            ~73K features
  analises-mineral-minerio  ~4,2K features
  Total API                 ~77K amostras → 1 doc raiz/amostra em OpenSearch

Nota: ``_cat/indices`` soma filhos **nested** ``analises[]`` (~15× o número de amostras).
Use ``GET mr_geoquimica_v001/_count`` para documentos raiz.

Campos por amostra:
  id_amostra, classe, projeto, laboratorio, abertura, leitura,
  classificacao_petrografica, unidade_litoestratigrafica,
  data_de_analise, location (geo_point), duplicata,
  analises (nested: analito, valor, unidade, qualificador),
  analitos (flat keyword list para faceting)

Uso:
  python -m bots.bot_geoquimica --index           # baixa + indexa as 2 coleções
  python -m bots.bot_geoquimica --index --classe rocha
  python -m bots.bot_geoquimica --index --classe mineral_minerio
  python -m bots.bot_geoquimica --dry-run         # simula sem escrever
  python -m bots.bot_geoquimica --count           # só conta docs na API

Fonte: https://geoservicos.sgb.gov.br/ogcapi/collections/geologia/geoquimica/
"""
from __future__ import annotations

import sys
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

INDEX_GEOQUIMICA = "mr_geoquimica_v001"
BATCH_INDEX      = 500
OGC_PAGE_SIZE    = 500
HTTP_TIMEOUT     = 30

OGC_BASE = "https://geoservicos.sgb.gov.br/ogcapi/collections/geologia/geoquimica"

# (collection_path, classe_label)
COLECOES: list[tuple[str, str]] = [
    ("analises-rocha",          "Rocha"),
    ("analises-mineral-minerio", "Mineral/Minério"),
]


# ─────────────────────────────────────────────────────────────────────────────
# OpenSearch client
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_geoquimica_index(os_client: OpenSearch) -> None:
    """Cria índice com mapping oficial (``location: geo_point``, nested ``analises``)."""
    backend_root = Path(__file__).resolve().parents[2] / "backend"
    if not backend_root.is_dir():
        log.warning(
            "index.setup.skip",
            msg="Pasta backend/ não encontrada — rode: "
                "python -m scripts.setup_indices --index mr_geoquimica_v001",
        )
        return
    root = str(backend_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from scripts.setup_indices import ALL_INDICES, create_index

        create_index(
            os_client,
            INDEX_GEOQUIMICA,
            ALL_INDICES[INDEX_GEOQUIMICA]["body"],
            recreate=False,
        )
        log.info("index.created", index=INDEX_GEOQUIMICA, source="setup_indices")
    except Exception as exc:
        log.warning("index.setup.failed", error=str(exc)[:200])


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

def _fetch_page(
    client: httpx.Client,
    collection: str,
    offset: int,
    limit: int = OGC_PAGE_SIZE,
) -> dict:
    url = f"{OGC_BASE}/{collection}/items"
    params = {
        "f":      "json",
        "limit":  limit,
        "offset": offset,
    }
    for attempt in range(1, 5):
        try:
            r = client.get(url, params=params, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            wait = attempt * 5
            log.warning("fetch.retry", collection=collection,
                        offset=offset, attempt=attempt, error=str(exc), wait=wait)
            time.sleep(wait)
    raise RuntimeError(f"Falha ao buscar {collection} offset={offset} após 4 tentativas")


def download_colecao(
    collection: str,
    dry_run: bool = False,
) -> Iterator[dict]:
    """Gera features de uma coleção via paginação offset/limit."""
    with httpx.Client(timeout=HTTP_TIMEOUT) as http:
        # primeiro request para obter o total
        first = _fetch_page(http, collection, offset=0, limit=1)
        total = first.get("numberMatched", first.get("totalFeatures", 0))
        log.info("collection.start", collection=collection, total=total)

        if dry_run:
            log.info("dry_run.skip", collection=collection)
            return

        offset = 0
        fetched = 0
        while offset < total:
            page = _fetch_page(http, collection, offset=offset, limit=OGC_PAGE_SIZE)
            features = page.get("features", [])
            if not features:
                break
            for feat in features:
                yield feat
            fetched += len(features)
            offset  += len(features)
            if offset % 5000 == 0 or offset >= total:
                log.info("collection.progress",
                         collection=collection, fetched=fetched, total=total)
            time.sleep(0.05)   # ~20 req/s — gentil com o servidor


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

def _coords_from_feature(props: dict, geo: dict) -> tuple[float, float] | None:
    """Lat/lon de properties; fallback para geometry Point (OGC GeoJSON)."""
    lat = props.get("latitude")
    lon = props.get("longitude")
    if lat is not None and lon is not None:
        return float(lat), float(lon)
    if (geo or {}).get("type") == "Point":
        coords = geo.get("coordinates") or []
        if len(coords) >= 2:
            return float(coords[1]), float(coords[0])
    return None


def _doc_id(collection: str, feat: dict, id_amostra: str) -> str:
    """
    ID OpenSearch único por feature OGC.

    ``numero_de_campo`` sozinho colide (~77K features → ~22,5K docs).
    O campo ``id`` da feature é único na API CPRM.
    """
    ogc_id = feat.get("id")
    if ogc_id is not None and str(ogc_id).strip() != "":
        return f"GEO:{collection}:{ogc_id}"
    props = feat.get("properties") or {}
    lab = (props.get("numero_de_laboratorio") or "").strip()
    proj = (props.get("projeto_amostragem") or "").strip()[:48]
    dt = (props.get("data_de_analise") or "")[:10]
    tail = "|".join(x for x in (lab, proj, dt) if x) or id_amostra
    safe = tail.replace("/", "-").replace(" ", "_")
    return f"GEO:{collection}:{id_amostra}:{safe}"


def parse_feature(
    feat: dict,
    collection: str,
    classe_label: str,
    now_iso: str,
) -> dict | None:
    props = feat.get("properties") or {}
    geo   = feat.get("geometry") or {}

    coords = _coords_from_feature(props, geo)
    if coords is None:
        return None
    lat, lon = coords

    id_amostra = props.get("numero_de_campo", "").strip()
    if not id_amostra:
        return None

    # Nested analises
    raw_analises = props.get("analises") or []
    analises: list[dict] = []
    analitos: list[str]  = []
    for a in raw_analises:
        simbolo = (a.get("analito") or "").strip()
        if not simbolo:
            continue
        analises.append({
            "analito":      simbolo,
            "valor":        a.get("valor"),
            "unidade":      a.get("unidade"),
            "qualificador": a.get("qualificador"),
        })
        if simbolo not in analitos:
            analitos.append(simbolo)

    # Data: "1978-11-01T00:00:00" → "1978-11-01"
    dt_raw = props.get("data_de_analise") or ""
    dt_analise: str | None = dt_raw[:10] if dt_raw else None

    ogc_id = feat.get("id")
    return {
        "_id":                        _doc_id(collection, feat, id_amostra),
        "ogc_feature_id":             str(ogc_id) if ogc_id is not None else None,
        "colecao_ogc":                collection,
        "id_amostra":                 id_amostra,
        "numero_laboratorio":         props.get("numero_de_laboratorio") or None,
        "classe":                     classe_label,
        "projeto":                    props.get("projeto_amostragem") or None,
        "projeto_publicacao":         props.get("projeto_publicacao") or None,
        "centro_de_custo":            props.get("centro_de_custo") or None,
        "laboratorio":                props.get("laboratorio") or None,
        "abertura":                   props.get("abertura") or None,
        "leitura":                    props.get("leitura") or None,
        "classificacao_petrografica": props.get("classificacao_petrografica") or None,
        "unidade_litoestratigrafica": props.get("unidade_litoestratigrafica") or None,
        "analises":                   analises,
        "analitos":                   analitos,
        "location":                   {"lat": lat, "lon": lon},
        "data_de_analise":            dt_analise,
        "duplicata":                  bool(props.get("duplicata")),
        "observacao":                 props.get("observacao") or None,
        "fonte":                      "SGB/CPRM",
        "indexed_at":                 now_iso,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Indexação em bulk
# ─────────────────────────────────────────────────────────────────────────────

def _actions(docs: list[dict]):
    for d in docs:
        doc_id = d.pop("_id")
        yield {
            "_index":  INDEX_GEOQUIMICA,
            "_id":     doc_id,
            "_source": d,
        }


def index_colecao(
    os_client: OpenSearch,
    collection: str,
    classe_label: str,
    dry_run: bool = False,
) -> dict[str, int]:
    now_iso = datetime.now(timezone.utc).isoformat()
    stats = {
        "total": 0,
        "indexed": 0,
        "skipped": 0,
        "skipped_sem_coords": 0,
        "skipped_sem_id": 0,
        "errors": 0,
    }
    batch: list[dict] = []

    for feat in download_colecao(collection, dry_run=dry_run):
        stats["total"] += 1
        props = feat.get("properties") or {}
        geo = feat.get("geometry") or {}
        if _coords_from_feature(props, geo) is None:
            stats["skipped"] += 1
            stats["skipped_sem_coords"] += 1
            continue
        id_amostra = (props.get("numero_de_campo") or "").strip()
        if not id_amostra:
            stats["skipped"] += 1
            stats["skipped_sem_id"] += 1
            continue
        doc = parse_feature(feat, collection, classe_label, now_iso)
        if doc is None:
            stats["skipped"] += 1
            continue
        batch.append(doc)

        if len(batch) >= BATCH_INDEX:
            ok, errs = helpers.bulk(
                os_client, _actions(batch),
                stats_only=True, raise_on_error=False,
            )
            stats["indexed"] += ok
            stats["errors"]  += errs
            batch = []

    if batch:
        ok, errs = helpers.bulk(
            os_client, _actions(batch),
            stats_only=True, raise_on_error=False,
        )
        stats["indexed"] += ok
        stats["errors"]  += errs

    return stats


def log_summary(stats_all: dict[str, dict], os_client: OpenSearch | None = None) -> None:
    log.info("=" * 50)
    total_indexed = 0
    for col, s in stats_all.items():
        log.info(
            "summary.colecao",
            collection=col,
            total=s["total"],
            indexed=s["indexed"],
            skipped=s["skipped"],
            skipped_sem_coords=s.get("skipped_sem_coords", 0),
            skipped_sem_id=s.get("skipped_sem_id", 0),
            errors=s["errors"],
        )
        total_indexed += s["indexed"]
    log.info("summary.total_indexed", total=total_indexed)
    if os_client:
        os_client.indices.refresh(index=INDEX_GEOQUIMICA)
        root = os_client.count(index=INDEX_GEOQUIMICA)["count"]
        log.info(
            "summary.opensearch",
            index=INDEX_GEOQUIMICA,
            docs_raiz=root,
            nota_cat="docs.count em _cat inclui nested analises[] (~15x docs_raiz)",
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--index",    is_flag=True, help="Baixa e indexa as coleções geoquímicas.")
@click.option(
    "--recreate",
    is_flag=True,
    help="Apaga mr_geoquimica_v001 antes de indexar (obrigatório após mudança de _id).",
)
@click.option("--dry-run",  is_flag=True, help="Simula sem escrever no OpenSearch.")
@click.option("--count",    is_flag=True, help="Apenas conta docs disponíveis na API.")
@click.option(
    "--classe",
    type=click.Choice(["rocha", "mineral_minerio", "todas"]),
    default="todas",
    show_default=True,
    help="Coleção a indexar.",
)
def main(index: bool, dry_run: bool, count: bool, classe: str, recreate: bool) -> None:
    """bot_geoquimica — SGB/CPRM Geoquímica OGC API → mr_geoquimica_v001."""

    if count:
        with httpx.Client(timeout=HTTP_TIMEOUT) as http:
            for col, label in COLECOES:
                page = _fetch_page(http, col, offset=0, limit=1)
                n = page.get("numberMatched", page.get("totalFeatures", "?"))
                click.echo(f"  {col}: {n} docs")
        return

    if not index and not dry_run:
        click.echo("Informe --index ou --dry-run")
        return

    colecoes_alvo: list[tuple[str, str]] = []
    if classe == "rocha":
        colecoes_alvo = [(c, l) for c, l in COLECOES if "rocha" in c]
    elif classe == "mineral_minerio":
        colecoes_alvo = [(c, l) for c, l in COLECOES if "mineral" in c]
    else:
        colecoes_alvo = COLECOES

    os_client = get_os_client() if not dry_run else None

    if recreate and os_client and not dry_run:
        if os_client.indices.exists(index=INDEX_GEOQUIMICA):
            os_client.indices.delete(index=INDEX_GEOQUIMICA)
            log.info("index.deleted", index=INDEX_GEOQUIMICA)
        _ensure_geoquimica_index(os_client)

    stats_all: dict[str, dict] = {}
    for col, label in colecoes_alvo:
        log.info("starting", collection=col, classe=label, dry_run=dry_run)
        t0 = time.time()
        stats = index_colecao(
            os_client=os_client,  # type: ignore[arg-type]
            collection=col,
            classe_label=label,
            dry_run=dry_run,
        )
        elapsed = time.time() - t0
        stats["elapsed_s"] = round(elapsed, 1)
        stats_all[col] = stats
        log.info("collection.done", collection=col,
                 indexed=stats["indexed"], elapsed_s=stats["elapsed_s"])

    log_summary(stats_all, os_client=os_client)


if __name__ == "__main__":
    main()
