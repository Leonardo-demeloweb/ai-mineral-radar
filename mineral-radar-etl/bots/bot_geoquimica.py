"""
bot_geoquimica.py — SGB/CPRM Geoquímica OGC API → mr_geoquimica_v001
======================================================================

Indexa análises geoquímicas do Serviço Geológico do Brasil (SGB/CPRM)
usando a API OGC Features padrão.

Coleções indexadas:
  analises-rocha          61.067 docs  (análises de amostras de rocha)
  analises-mineral-minerio 4.147 docs  (análises de mineral/minério)

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

import time
from datetime import datetime, timezone
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

def parse_feature(feat: dict, classe_label: str, now_iso: str) -> dict | None:
    props = feat.get("properties") or {}
    geo   = feat.get("geometry") or {}

    lat = props.get("latitude")
    lon = props.get("longitude")
    if lat is None or lon is None:
        return None

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

    return {
        "_id": f"GEO:{id_amostra}",
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
    stats = {"total": 0, "indexed": 0, "skipped": 0, "errors": 0}
    batch: list[dict] = []

    for feat in download_colecao(collection, dry_run=dry_run):
        stats["total"] += 1
        doc = parse_feature(feat, classe_label, now_iso)
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


def log_summary(stats_all: dict[str, dict]) -> None:
    log.info("=" * 50)
    total_indexed = 0
    for col, s in stats_all.items():
        log.info(
            "summary.colecao",
            collection=col,
            total=s["total"],
            indexed=s["indexed"],
            skipped=s["skipped"],
            errors=s["errors"],
        )
        total_indexed += s["indexed"]
    log.info("summary.total_indexed", total=total_indexed)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--index",    is_flag=True, help="Baixa e indexa as coleções geoquímicas.")
@click.option("--dry-run",  is_flag=True, help="Simula sem escrever no OpenSearch.")
@click.option("--count",    is_flag=True, help="Apenas conta docs disponíveis na API.")
@click.option(
    "--classe",
    type=click.Choice(["rocha", "mineral_minerio", "todas"]),
    default="todas",
    show_default=True,
    help="Coleção a indexar.",
)
def main(index: bool, dry_run: bool, count: bool, classe: str) -> None:
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

    log_summary(stats_all)


if __name__ == "__main__":
    main()
