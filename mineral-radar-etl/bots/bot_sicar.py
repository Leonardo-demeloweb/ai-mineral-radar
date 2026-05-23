"""
bot_sicar.py — SICAR WFS → mr_sicar_v001
==========================================

Ingestão do Cadastro Ambiental Rural (CAR) via WFS GeoJSON público do SICAR.

Fonte:
  GeoServer SICAR: https://geoserver.car.gov.br/geoserver/sicar/wfs
  Camadas:  sicar:sicar_imoveis_{uf}  (27 UFs, ex: sicar:sicar_imoveis_mg)
  Protocolo: WFS 2.0.0 · GeoJSON · paginado (startIndex + count)

Campos retornados pelo WFS:
  cod_imovel        — código CAR único (UF-IBGE-UUID)
  status_imovel     — AT | PE | SU | CA
  tipo_imovel       — IRU | ASS | PCT | QUI
  municipio         — nome do município
  cod_municipio_ibge— código IBGE do município
  uf                — sigla do estado
  area              — área em hectares
  m_fiscal          — módulos fiscais
  condicao          — "Aguardando análise" | "Ativo" etc.
  dat_criacao       — data de cadastro (dt_inscricao no índice)
  data_atualizacao  — data de atualização (dt_retificacao no índice)
  geometry          — MultiPolygon em WGS84

Volume por UF (ref. mai/2026):  AC ~56K | MG ~1.2M | PA ~820K (total ~6.8M)

Uso:
  python -m bots.bot_sicar --uf AC --index
  python -m bots.bot_sicar --uf MG --uf PA --index
  python -m bots.bot_sicar --all-ufs --index
  python -m bots.bot_sicar --uf AC --index --dry-run
  python -m bots.bot_sicar --uf AC --index --resume
  python -m bots.bot_sicar --uf AC --index --reset-checkpoint
  python -m bots.bot_sicar --uf AC --index --dry-run --limit-pages 2
  python -m bots.bot_sicar --uf MG --enrich-jazidas
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import click
import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from opensearchpy import OpenSearch, helpers
from shapely.geometry import mapping, shape

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from bots.common.checkpoint import (
    load_checkpoint,
    mark_uf_done,
    mark_uf_failed,
    reset_checkpoint,
    resume_start_index,
    should_skip_uf,
    update_uf_progress,
)
from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

BOT_NAME      = "bot_sicar"
PHASE_INDEX   = "index"
INDEX_SICAR   = "mr_sicar_v001"
INDEX_JAZIDAS = "mr_jazidas_v001"

BATCH_SIZE   = 300    # docs por bulk (polígonos simplificados são leves)
PAGE_SIZE    = 1_000  # features por request WFS
SCROLL_SIZE  = 2_000

WFS_BASE = "https://geoserver.car.gov.br/geoserver/sicar/wfs"
WFS_HEADERS = {
    "User-Agent": "Mozilla/5.0 MineralRadar-ETL/1.0 (dados publicos SICAR)",
    "Accept":     "application/json",
}

# UFs em ordem decrescente de volume (maiores primeiro — permite interrupção parcial)
ALL_UFS = [
    "MG", "SP", "PA", "BA", "MT", "GO", "RS", "PR", "RO", "TO",
    "MA", "MS", "AM", "CE", "PI", "SC", "PE", "RN", "ES", "AL",
    "PB", "SE", "RR", "AC", "AP", "RJ", "DF",
]

# Tolerância de simplificação ~30 m em graus decimais
GEOMETRY_SIMPLIFY_TOLERANCE = 0.0003

# Tipo de imóvel — comunidades tradicionais
TIPOS_SENSIVEIS = {"PCT", "QUI", "ASS"}


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
# WFS helper
# ─────────────────────────────────────────────────────────────────────────────

def get_uf_total(uf: str, session: requests.Session) -> int:
    """
    Retorna o total de imóveis da UF.
    Faz request de 1 feature e lê totalFeatures (suportado pelo GeoServer SICAR).
    """
    resp = session.get(WFS_BASE, params={
        "service":      "WFS",
        "version":      "2.0.0",
        "request":      "GetFeature",
        "typeNames":    f"sicar:sicar_imoveis_{uf.lower()}",
        "count":        1,
        "startIndex":   0,
        "outputFormat": "application/json",
    }, verify=False, timeout=120)
    resp.raise_for_status()
    d = resp.json()
    # totalFeatures = total na camada; numberReturned = nesta página
    total = d.get("totalFeatures", d.get("numberMatched", 0))
    if not total:
        # fallback: se o servidor não informar, estimamos por estado
        # (valores ref. mai/2026)
        ESTIMATIVAS = {
            "MG": 1_200_000, "SP": 900_000, "PA": 820_000,
            "BA": 700_000,   "MT": 600_000, "GO": 550_000,
            "default": 100_000,
        }
        total = ESTIMATIVAS.get(uf.upper(), ESTIMATIVAS["default"])
        log.warning("sicar.wfs.total_estimate", uf=uf, estimated=total)
    return total


def fetch_page(uf: str, start: int, count: int, session: requests.Session) -> list[dict]:
    """Baixa uma página de features WFS para a UF."""
    resp = session.get(WFS_BASE, params={
        "service":     "WFS",
        "version":     "2.0.0",
        "request":     "GetFeature",
        "typeNames":   f"sicar:sicar_imoveis_{uf.lower()}",
        "count":       count,
        "startIndex":  start,
        "outputFormat": "application/json",
        "srsName":     "EPSG:4326",
    }, verify=False, timeout=120)
    resp.raise_for_status()
    return resp.json().get("features", [])


# ─────────────────────────────────────────────────────────────────────────────
# Parse / transform
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if f == f else None   # NaN check
    except (TypeError, ValueError):
        return None


def _safe_str(v) -> str | None:
    s = str(v).strip() if v is not None else None
    return s if s and s.lower() not in ("none", "null", "") else None


def _iso_date(v) -> str | None:
    """Normaliza datas ISO do WFS para YYYY-MM-DD."""
    s = _safe_str(v)
    if not s:
        return None
    # WFS retorna formato: 2018-11-12T01:15:29.133Z
    return s[:10] if len(s) >= 10 else s


def feature_to_doc(feat: dict, now_iso: str) -> dict | None:
    """Transforma uma feature WFS em documento OpenSearch."""
    props = feat.get("properties") or {}
    geom_raw = feat.get("geometry")

    cod_imovel = _safe_str(props.get("cod_imovel"))
    if not cod_imovel:
        return None

    # Geometria
    if not geom_raw:
        return None
    try:
        geom = shape(geom_raw)
        if geom.is_empty:
            return None
        # Simplifica
        geom_simple = geom.simplify(GEOMETRY_SIMPLIFY_TOLERANCE, preserve_topology=True)
        poligono_geo = mapping(geom_simple)
        c = geom_simple.centroid
        centroide_geo = {"lat": round(c.y, 6), "lon": round(c.x, 6)}
    except Exception:
        return None

    area_ha       = _safe_float(props.get("area"))
    modulos_fis   = _safe_float(props.get("m_fiscal"))
    status        = _safe_str(props.get("status_imovel"))
    tipo          = _safe_str(props.get("tipo_imovel"))
    uf            = _safe_str(props.get("uf"))
    municipio     = _safe_str(props.get("municipio"))
    cod_mun       = _safe_str(props.get("cod_municipio_ibge"))
    dt_inscricao  = _iso_date(props.get("dat_criacao"))
    dt_retif      = _iso_date(props.get("data_atualizacao"))

    doc: dict = {
        "cod_car":               cod_imovel,
        "uf":                    uf,
        "status_car":            (status or "").upper() or None,
        "tipo_imovel":           (tipo   or "").upper() or None,
        "municipio":             municipio,
        "cod_municipio_ibge":    cod_mun,
        "area_ha":               area_ha,
        "area_modulos_fiscais":  modulos_fis,
        "poligono":              poligono_geo,
        "centroide":             centroide_geo,
        "dt_inscricao":          dt_inscricao,
        "dt_retificacao":        dt_retif,
        "sobreposicao_ti":       False,
        "sobreposicao_uc":       False,
        "sobreposicao_area_anm": False,
        "indexed_at":            now_iso,
    }
    # Remove Nones
    return {k: v for k, v in doc.items() if v is not None}


# ─────────────────────────────────────────────────────────────────────────────
# Streaming iterator
# ─────────────────────────────────────────────────────────────────────────────

class _SicarTLSAdapter(HTTPAdapter):
    """
    Adaptador TLS com SECLEVEL=0 para geoserver.car.gov.br.
    O servidor SICAR usa cipher suite legado que Python rejeita no nível padrão.
    """
    def init_poolmanager(self, *args, **kwargs):
        import ssl as _ssl
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode    = _ssl.CERT_NONE
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=0")
        except _ssl.SSLError:
            pass
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _make_http_session() -> requests.Session:
    """Cria sessão requests com TLS permissivo para o SICAR WFS."""
    session = requests.Session()
    session.mount("https://", _SicarTLSAdapter())
    session.headers.update(WFS_HEADERS)
    return session


def iter_uf_docs(
    uf: str,
    dry_run: bool = False,
    *,
    start_from: int = 0,
    limit_pages: int | None = None,
    on_page_done=None,
) -> Iterator[dict]:
    """
    Faz paginação WFS e gera documentos prontos para bulk index.
    Yields dicts compatíveis com opensearch-py helpers.bulk().
    """
    now_iso    = datetime.now(timezone.utc).isoformat()
    parsed     = 0
    skipped    = 0
    pages_done = 0
    failed     = False

    with _make_http_session() as http:
        try:
            total = get_uf_total(uf, http)
        except Exception as e:
            log.error("sicar.wfs.total_error", uf=uf, error=str(e))
            return

        log.info("sicar.wfs.start", uf=uf, total=total, start_index=start_from)

        start = start_from
        while start < total:
            if limit_pages is not None and pages_done >= limit_pages:
                log.info("sicar.wfs.limit_pages", uf=uf, pages=pages_done)
                break

            try:
                features = fetch_page(uf, start, PAGE_SIZE, http)
            except Exception as e:  # noqa: E722
                log.error("sicar.wfs.page_error",
                          uf=uf, start=start, error=str(e))
                failed = True
                break

            if not features:
                break

            for feat in features:
                doc = feature_to_doc(feat, now_iso)
                if doc is None:
                    skipped += 1
                    continue
                parsed += 1
                if dry_run and parsed <= 3:
                    log.info("sicar.parse.sample", doc={
                        k: v for k, v in doc.items()
                        if k not in ("poligono",)
                    })
                yield {"_index": INDEX_SICAR, "_id": doc["cod_car"], "_source": doc}

            pages_done += 1
            next_start = start + PAGE_SIZE
            if on_page_done:
                on_page_done(
                    wfs_start_index=next_start,
                    docs_parsed=parsed,
                    page_raw=len(features),
                    failed=False,
                )

            start = next_start
            if start % 10_000 == 0:
                log.info("sicar.wfs.progress",
                         uf=uf, parsed=parsed, skipped=skipped,
                         pct=round(start / total * 100, 1))

    if failed and on_page_done:
        on_page_done(
            wfs_start_index=start,
            docs_parsed=parsed,
            page_raw=0,
            failed=True,
        )

    log.info("sicar.wfs.done", uf=uf, parsed=parsed, skipped=skipped, failed=failed)


# ─────────────────────────────────────────────────────────────────────────────
# Bulk index
# ─────────────────────────────────────────────────────────────────────────────

def bulk_index(
    client: OpenSearch,
    docs: Iterator[dict],
    uf: str,
    dry_run: bool,
) -> tuple[int, int]:
    """Faz bulk index em batches. Retorna (ok, erros)."""
    ok_total  = 0
    err_total = 0
    batch: list[dict] = []
    t0 = time.monotonic()

    def _flush():
        nonlocal ok_total, err_total
        if not batch:
            return
        if dry_run:
            ok_total += len(batch)
            batch.clear()
            return
        ok, errs = helpers.bulk(
            client, batch,
            raise_on_error=False,
            max_retries=3,
            initial_backoff=2,
            max_backoff=30,
        )
        ok_total  += ok
        err_total += len(errs)
        if errs:
            log.warning("sicar.index.errors", uf=uf, sample=str(errs[0])[:200])
        batch.clear()

    for doc in docs:
        batch.append(doc)
        if len(batch) >= BATCH_SIZE:
            _flush()
            elapsed = time.monotonic() - t0
            rate    = ok_total / elapsed if elapsed > 0 else 0
            if ok_total % 10_000 == 0:
                log.info("sicar.index.progress",
                         uf=uf, ok=ok_total, errs=err_total,
                         rate_s=round(rate, 0))

    _flush()
    elapsed = time.monotonic() - t0
    log.info("sicar.index.done",
             uf=uf, ok=ok_total, errs=err_total,
             elapsed_s=round(elapsed, 1),
             rate_s=round(ok_total / elapsed, 0) if elapsed > 0 else 0)
    return ok_total, err_total


# ─────────────────────────────────────────────────────────────────────────────
# Enriquecimento jazidas
# ─────────────────────────────────────────────────────────────────────────────

def run_enrich_jazidas(client: OpenSearch, uf: str | None, dry_run: bool):
    """
    Para cada jazida ANM com centróide, verifica se existe imóvel CAR
    num raio de 5 km e marca sobreposicao_car=True em mr_jazidas_v001.

    Nota: esta verificação usa centróide (não sobreposição exata de polígono).
    Para sobreposição exata, usar PostGIS.
    """
    log.info("sicar.enrich_jazidas.start", uf=uf)

    query: dict = {
        "size": SCROLL_SIZE,
        "_source": ["numero_processo", "centroide"],
        "query": {
            "bool": {
                "must":   [{"exists": {"field": "centroide"}}],
                "filter": [] if not uf else [{"term": {"uf": uf}}],
            }
        },
    }

    scroll = client.search(
        index=INDEX_JAZIDAS,
        body=query,
        params={"scroll": "5m"},
    )
    scroll_id = scroll["_scroll_id"]
    hits      = scroll["hits"]["hits"]
    enriched  = 0

    while hits:
        updates = []
        for hit in hits:
            src     = hit["_source"]
            centroid = src.get("centroide") or {}
            lat = centroid.get("lat")
            lon = centroid.get("lon")
            if lat is None or lon is None:
                continue

            check = client.search(
                index=INDEX_SICAR,
                body={
                    "size": 1,
                    "_source": [],
                    "query": {"bool": {"filter": [
                        {"term": {"status_car": "AT"}},
                        {"geo_distance": {
                            "distance": "5km",
                            "centroide": {"lat": lat, "lon": lon},
                        }},
                    ]}},
                },
            )
            if check["hits"]["total"]["value"] > 0:
                updates.append({
                    "_op_type": "update",
                    "_index":   INDEX_JAZIDAS,
                    "_id":      hit["_id"],
                    "doc":      {"sobreposicao_car": True},
                })
                enriched += 1

        if updates and not dry_run:
            helpers.bulk(client, updates, raise_on_error=False)

        scroll = client.scroll(scroll_id=scroll_id, params={"scroll": "5m"})
        scroll_id = scroll["_scroll_id"]
        hits      = scroll["hits"]["hits"]

    client.clear_scroll(scroll_id=scroll_id)
    log.info("sicar.enrich_jazidas.done", uf=uf, enriched=enriched)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--uf",             multiple=True, help="UF(s) a processar (ex: MG PA).")
@click.option("--all-ufs",        is_flag=True,  help="Processar todas as 27 UFs.")
@click.option("--index",          is_flag=True,  help="Indexar imóveis CAR em mr_sicar_v001.")
@click.option("--enrich-jazidas", is_flag=True,  help="Marcar sobreposicao_car em mr_jazidas_v001.")
@click.option("--dry-run",        is_flag=True,  help="Parse e log sem indexar.")
@click.option("--resume",         is_flag=True,
              help="Retoma UFs incompletas a partir do checkpoint JSON.")
@click.option("--reset-checkpoint", "clear_checkpoint", is_flag=True,
              help="Apaga checkpoint antes de executar.")
@click.option("--limit-pages", type=int, default=None,
              help="Máx. páginas WFS por UF (teste / dry-run).")
def main(
    uf: tuple[str, ...],
    all_ufs: bool,
    index: bool,
    enrich_jazidas: bool,
    dry_run: bool,
    resume: bool,
    clear_checkpoint: bool,
    limit_pages: int | None,
):
    """
    Bot de ingestão SICAR via WFS → mr_sicar_v001.

    Exemplos:
      python -m bots.bot_sicar --uf AC --index
      python -m bots.bot_sicar --uf MG --uf PA --index
      python -m bots.bot_sicar --all-ufs --index
      python -m bots.bot_sicar --uf MG --enrich-jazidas
    """
    if not uf and not all_ufs:
        raise click.UsageError("Informe --uf {UF} ou --all-ufs")
    if not index and not enrich_jazidas:
        raise click.UsageError("Informe pelo menos --index ou --enrich-jazidas")

    ufs_to_process = list(ALL_UFS if all_ufs else [u.upper() for u in uf])

    if clear_checkpoint:
        reset_checkpoint(BOT_NAME)
        log.info("sicar.checkpoint.reset")

    ckpt = load_checkpoint(BOT_NAME)
    persist_ckpt = not dry_run

    client = get_os_client()

    total_ok  = 0
    total_err = 0
    t_global  = time.monotonic()

    for state_uf in ufs_to_process:
        log.info("sicar.uf.start", uf=state_uf)

        if index:
            if should_skip_uf(ckpt, state_uf, PHASE_INDEX, resume=resume):
                log.info("sicar.uf.skip_done", uf=state_uf)
            else:
                start_from = resume_start_index(
                    ckpt, state_uf, PHASE_INDEX, resume=resume,
                )

                def _on_page(**kwargs):
                    if kwargs.get("failed"):
                        mark_uf_failed(
                            ckpt, state_uf, PHASE_INDEX,
                            wfs_start_index=kwargs["wfs_start_index"],
                            docs_parsed=kwargs["docs_parsed"],
                            docs_indexed=0,
                            error="wfs_page_error",
                            persist=persist_ckpt,
                        )
                    else:
                        update_uf_progress(
                            ckpt, state_uf, PHASE_INDEX,
                            wfs_start_index=kwargs["wfs_start_index"],
                            docs_parsed=kwargs["docs_parsed"],
                            persist=persist_ckpt,
                        )

                docs = iter_uf_docs(
                    state_uf,
                    dry_run=dry_run,
                    start_from=start_from,
                    limit_pages=limit_pages,
                    on_page_done=_on_page,
                )
                ok, err = bulk_index(client, docs, state_uf, dry_run=dry_run)
                total_ok  += ok
                total_err += err

                if limit_pages is None and not dry_run:
                    entry = ckpt["ufs"].get(f"{state_uf.upper()}:{PHASE_INDEX}", {})
                    if entry.get("status") == "failed":
                        log.warning(
                            "sicar.uf.checkpoint_failed",
                            uf=state_uf,
                            indexed=ok,
                            resume_from=entry.get("wfs_start_index"),
                        )
                    elif ok > 0:
                        mark_uf_done(ckpt, state_uf, PHASE_INDEX, ok)
                        log.info("sicar.uf.checkpoint_done", uf=state_uf, indexed=ok)
                    else:
                        mark_uf_failed(
                            ckpt, state_uf, PHASE_INDEX,
                            wfs_start_index=start_from,
                            docs_parsed=0,
                            docs_indexed=0,
                            error="wfs_total_error_or_empty",
                            persist=persist_ckpt,
                        )
                        log.warning("sicar.uf.checkpoint_failed", uf=state_uf, indexed=0)

        if enrich_jazidas:
            run_enrich_jazidas(client, state_uf, dry_run=dry_run)

    elapsed = time.monotonic() - t_global
    log.info(
        "sicar.run.done",
        ufs=len(ufs_to_process),
        ok=total_ok,
        err=total_err,
        elapsed_s=round(elapsed, 1),
    )


if __name__ == "__main__":
    main()
