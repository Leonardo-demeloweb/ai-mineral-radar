"""
bot_sigef.py — INCRA SIGEF WFS (GML2) → mr_sigef_v001
=======================================================

Ingestão das parcelas rurais certificadas pelo INCRA via WFS GML2 público.

Fonte:
  WFS INCRA: https://acervofundiario.incra.gov.br/i3geo/ogc.php
  Camada:    certificada_sigef_particular_{uf}  (27 UFs)
  Protocolo: WFS 1.0.0 · GML2 · paginado (maxFeatures + startIndex)

Campos retornados pelo WFS:
  parcela_codigo    — UUID único da parcela (usado como _id)
  codigo_imovel     — código INCRA do imóvel (13 dígitos)
  nome_area         — nome da fazenda/gleba
  status            — CERTIFICADA | AGUARDANDO_ANALISE | INAPROVADA
  situacao_informada— REGISTRADA | PENDENTE
  art               — número ART do responsável técnico
  registro_matricula— matrícula no cartório
  data_submissao    — data de envio para certificação
  data_aprovacao    — data de aprovação pelo INCRA
  registro_data     — data de registro em cartório
  codigo_municipio  — código IBGE do município
  geometry          — Polygon em GML2/EPSG:4326

Campos derivados:
  uf                — extraído do nome da camada
  area_ha           — calculado da geometria (WGS84 → projeção local → ha)
  centroide         — centróide do polígono

Volume por UF (ref. mai/2026):  MG ~850K | SP ~600K | MT ~500K (total ~7M)

Uso:
  python -m bots.bot_sigef --uf MG --index
  python -m bots.bot_sigef --uf MG --uf PA --index
  python -m bots.bot_sigef --all-ufs --index
  python -m bots.bot_sigef --uf MG --dry-run
  python -m bots.bot_sigef --uf MG --index --resume
  python -m bots.bot_sigef --uf MG --index --reset-checkpoint
  python -m bots.bot_sigef --uf AC --index --dry-run --limit-pages 2
  python -m bots.bot_sigef --uf MG --enrich-jazidas
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterator

import click
import requests
from opensearchpy import OpenSearch, helpers
from shapely.geometry import mapping, shape
from shapely.ops import transform
import pyproj

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

BOT_NAME      = "bot_sigef"
PHASE_INDEX   = "index"
INDEX_SIGEF   = "mr_sigef_v001"
INDEX_JAZIDAS = "mr_jazidas_v001"

BATCH_SIZE  = 200   # docs por bulk (polígonos simplificados)
PAGE_SIZE   = 200   # features por request WFS (menor = menos stress no servidor INCRA)
SCROLL_SIZE = 2_000

# Rate limiting — o servidor INCRA bane por IP após muitas requests consecutivas
SLEEP_BETWEEN_PAGES_S = 0.5   # pausa entre cada página WFS
SLEEP_BETWEEN_UFS_S   = 30.0  # pausa entre UFs para deixar o servidor respirar
WFS_MAX_RETRIES       = 5     # tentativas em caso de erro 5xx

WFS_BASE = "https://acervofundiario.incra.gov.br/i3geo/ogc.php"
WFS_HEADERS = {
    "User-Agent": "Mozilla/5.0 MineralRadar-ETL/1.0 (dados publicos INCRA SIGEF)",
    "Accept":     "application/xml, text/xml, */*",
}

# Namespaces GML2
NS_MS  = "http://www.omsug.ca/osgis2004"
NS_GML = "http://www.opengis.net/gml"
NS_WFS = "http://www.opengis.net/wfs"

# Tolerância de simplificação ~30 m em graus decimais
GEOMETRY_SIMPLIFY_TOLERANCE = 0.0003

# UFs em ordem decrescente de volume estimado
ALL_UFS = [
    "MG", "SP", "MT", "GO", "PA", "BA", "RS", "PR", "RO", "TO",
    "MA", "MS", "AM", "CE", "PI", "SC", "PE", "RN", "ES", "AL",
    "PB", "SE", "RR", "AC", "AP", "RJ", "DF",
]


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
# WFS / GML2 helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(WFS_HEADERS)
    return session


def _layer_name(uf: str) -> str:
    return f"certificada_sigef_particular_{uf.lower()}"


def fetch_page_gml(uf: str, start: int, count: int, session: requests.Session) -> str:
    """
    Baixa uma página GML2 do WFS SIGEF com retry exponencial.
    O servidor INCRA retorna HTTP 500 quando sobrecarregado — aguarda e tenta novamente.
    """
    params = {
        "tema":        _layer_name(uf),
        "service":     "WFS",
        "version":     "1.0.0",
        "request":     "GetFeature",
        "maxFeatures": count,
        "startIndex":  start,
    }
    for attempt in range(1, WFS_MAX_RETRIES + 1):
        try:
            resp = session.get(WFS_BASE, params=params, timeout=120)
            resp.raise_for_status()
            return resp.text
        except requests.HTTPError as e:
            if resp.status_code in (500, 502, 503, 504) and attempt < WFS_MAX_RETRIES:
                wait = 30 * attempt  # 30s, 60s, 90s, 120s
                log.warning(
                    "sigef.wfs.retry",
                    uf=uf, start=start, attempt=attempt,
                    status=resp.status_code, wait_s=wait,
                )
                time.sleep(wait)
                continue
            raise
        except requests.RequestException as e:
            if attempt < WFS_MAX_RETRIES:
                wait = 30 * attempt
                log.warning("sigef.wfs.retry", uf=uf, start=start,
                            attempt=attempt, error=str(e), wait_s=wait)
                time.sleep(wait)
                continue
            raise


def _parse_gml2_coords(coords_text: str) -> list[tuple[float, float]]:
    """
    Converte string GML2 de coordenadas 'lon,lat lon,lat ...'
    em lista de tuplas (lon, lat).
    """
    pts = []
    for pair in coords_text.strip().split():
        parts = pair.split(",")
        if len(parts) >= 2:
            try:
                lon, lat = float(parts[0]), float(parts[1])
                pts.append((lon, lat))
            except ValueError:
                pass
    return pts


def _gml2_polygon_to_shapely(poly_el: ET.Element):
    """Converte elemento <gml:Polygon> em shapely.geometry.Polygon."""
    outer = poly_el.find(f".//{{{NS_GML}}}outerBoundaryIs//{{{NS_GML}}}coordinates")
    if outer is None or not outer.text:
        return None
    exterior = _parse_gml2_coords(outer.text)
    if len(exterior) < 3:
        return None

    holes = []
    for inner in poly_el.findall(f".//{{{NS_GML}}}innerBoundaryIs//{{{NS_GML}}}coordinates"):
        if inner.text:
            ring = _parse_gml2_coords(inner.text)
            if len(ring) >= 3:
                holes.append(ring)

    from shapely.geometry import Polygon as ShapelyPolygon
    try:
        return ShapelyPolygon(exterior, holes)
    except Exception:
        return None


def _compute_area_ha(geom) -> float | None:
    """
    Calcula área em hectares via projeção local (UTM ou Albers Brasil).
    Usa pyproj para reprojeção.
    """
    try:
        centroid = geom.centroid
        lon, lat = centroid.x, centroid.y
        # Zona UTM baseada no centróide
        zone = int((lon + 180) / 6) + 1
        hemisphere = "north" if lat >= 0 else "south"
        proj_utm = pyproj.CRS(f"+proj=utm +zone={zone} +{hemisphere} +datum=WGS84 +units=m")
        proj_wgs84 = pyproj.CRS("EPSG:4326")
        transformer = pyproj.Transformer.from_crs(proj_wgs84, proj_utm, always_xy=True)
        geom_proj = transform(transformer.transform, geom)
        area_m2 = geom_proj.area
        return round(area_m2 / 10_000, 4)  # m² → ha
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Parse / transform
# ─────────────────────────────────────────────────────────────────────────────

def _safe_str(v) -> str | None:
    s = str(v).strip() if v is not None else None
    return s if s and s.lower() not in ("none", "null", "") else None


def _iso_date(v) -> str | None:
    s = _safe_str(v)
    if not s:
        return None
    return s[:10] if len(s) >= 10 else s


def parse_gml_page(xml_text: str, uf: str, now_iso: str) -> tuple[list[dict], int]:
    """
    Parseia uma página GML2 e retorna (lista_docs, contagem_features_na_pagina).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.error("sigef.gml.parse_error", uf=uf, error=str(e))
        return [], 0

    layer = _layer_name(uf)
    tag_feature = f"{{{NS_MS}}}{layer}"

    docs = []
    members = root.findall(f".//{{{NS_GML}}}featureMember")
    raw_count = len(members)

    for member in members:
        feat = member.find(tag_feature)
        if feat is None:
            continue

        def _field(name: str) -> str | None:
            el = feat.find(f"{{{NS_MS}}}{name}")
            return _safe_str(el.text) if el is not None else None

        parcela_codigo = _field("parcela_codigo")
        if not parcela_codigo:
            continue

        # Geometria
        poly_el = feat.find(f".//{{{NS_GML}}}Polygon")
        if poly_el is None:
            continue
        geom = _gml2_polygon_to_shapely(poly_el)
        if geom is None or geom.is_empty:
            continue

        try:
            geom_simple = geom.simplify(GEOMETRY_SIMPLIFY_TOLERANCE, preserve_topology=True)
            poligono_geo = mapping(geom_simple)
            c = geom_simple.centroid
            centroide_geo = {"lat": round(c.y, 6), "lon": round(c.x, 6)}
            area_ha = _compute_area_ha(geom_simple)
        except Exception:
            continue

        doc: dict = {
            "parcela_codigo":       parcela_codigo,
            "codigo_imovel":        _field("codigo_imovel"),
            "nome_area":            _field("nome_area"),
            "uf":                   uf.upper(),
            "codigo_municipio":     _field("codigo_municipio"),
            "status":               (_field("status") or "").upper() or None,
            "situacao_informada":   (_field("situacao_informada") or "").upper() or None,
            "art":                  _field("art"),
            "registro_matricula":   _field("registro_matricula"),
            "area_ha":              area_ha,
            "poligono":             poligono_geo,
            "centroide":            centroide_geo,
            "dt_submissao":         _iso_date(_field("data_submissao")),
            "dt_aprovacao":         _iso_date(_field("data_aprovacao")),
            "dt_registro":          _iso_date(_field("registro_data")),
            "sobreposicao_area_anm": False,
            "sobreposicao_ti":       False,
            "sobreposicao_uc":       False,
            "indexed_at":            now_iso,
        }
        doc = {k: v for k, v in doc.items() if v is not None}
        docs.append(doc)

    return docs, raw_count


# ─────────────────────────────────────────────────────────────────────────────
# Streaming iterator
# ─────────────────────────────────────────────────────────────────────────────

def iter_uf_docs(
    uf: str,
    dry_run: bool = False,
    *,
    start_from: int = 0,
    limit_pages: int | None = None,
    on_page_done=None,
) -> Iterator[dict]:
    """
    Pagina o WFS SIGEF por UF e gera documentos para bulk index.
    Para sem total conhecido — continua até página vazia.
    """
    now_iso      = datetime.now(timezone.utc).isoformat()
    parsed       = 0
    skipped      = 0
    start        = start_from
    empty_streak = 0
    pages_done   = 0
    failed       = False

    if start_from > 0:
        log.info("sigef.wfs.resume", uf=uf, start_index=start_from)

    with _make_http_session() as http:
        while True:
            if limit_pages is not None and pages_done >= limit_pages:
                log.info("sigef.wfs.limit_pages", uf=uf, pages=pages_done)
                break

            try:
                xml_text = fetch_page_gml(uf, start, PAGE_SIZE, http)
            except Exception as e:
                log.error("sigef.wfs.page_error", uf=uf, start=start, error=str(e))
                failed = True
                break

            docs, raw_count = parse_gml_page(xml_text, uf, now_iso)

            if raw_count == 0:
                empty_streak += 1
                if empty_streak >= 2:
                    break
            else:
                empty_streak = 0

            for doc in docs:
                parsed += 1
                if dry_run and parsed <= 3:
                    log.info("sigef.parse.sample", doc={
                        k: v for k, v in doc.items()
                        if k not in ("poligono",)
                    })
                yield {"_index": INDEX_SIGEF, "_id": doc["parcela_codigo"], "_source": doc}

            skipped += raw_count - len(docs)
            pages_done += 1

            next_start = start + PAGE_SIZE
            if on_page_done:
                on_page_done(
                    wfs_start_index=next_start,
                    docs_parsed=parsed,
                    page_raw=raw_count,
                    failed=False,
                )

            if raw_count < PAGE_SIZE:
                break

            start = next_start
            if start % 10_000 == 0:
                log.info("sigef.wfs.progress",
                         uf=uf, parsed=parsed, skipped=skipped, start=start)

            time.sleep(SLEEP_BETWEEN_PAGES_S)

    if failed and on_page_done:
        on_page_done(
            wfs_start_index=start,
            docs_parsed=parsed,
            page_raw=0,
            failed=True,
        )

    log.info("sigef.wfs.done", uf=uf, parsed=parsed, skipped=skipped, failed=failed)


# ─────────────────────────────────────────────────────────────────────────────
# Bulk index
# ─────────────────────────────────────────────────────────────────────────────

def bulk_index(
    client: OpenSearch,
    docs: Iterator[dict],
    uf: str,
    dry_run: bool,
) -> tuple[int, int]:
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
            log.warning("sigef.index.errors", uf=uf, sample=str(errs[0])[:200])
        batch.clear()

    for doc in docs:
        batch.append(doc)
        if len(batch) >= BATCH_SIZE:
            _flush()
            elapsed = time.monotonic() - t0
            rate = ok_total / elapsed if elapsed > 0 else 0
            if ok_total % 5_000 == 0:
                log.info("sigef.index.progress",
                         uf=uf, ok=ok_total, errs=err_total,
                         rate_s=round(rate, 0))

    _flush()
    elapsed = time.monotonic() - t0
    log.info("sigef.index.done",
             uf=uf, ok=ok_total, errs=err_total,
             elapsed_s=round(elapsed, 1),
             rate_s=round(ok_total / elapsed, 0) if elapsed > 0 else 0)
    return ok_total, err_total


# ─────────────────────────────────────────────────────────────────────────────
# Enriquecimento jazidas
# ─────────────────────────────────────────────────────────────────────────────

def run_enrich_jazidas(client: OpenSearch, uf: str | None, dry_run: bool):
    """
    Para cada jazida ANM com centróide, verifica se existe imóvel SIGEF
    certificado num raio de 5 km e marca sobreposicao_sigef=True em mr_jazidas_v001.
    """
    log.info("sigef.enrich_jazidas.start", uf=uf)

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
            src      = hit["_source"]
            centroid = src.get("centroide") or {}
            lat = centroid.get("lat")
            lon = centroid.get("lon")
            if lat is None or lon is None:
                continue

            check = client.search(
                index=INDEX_SIGEF,
                body={
                    "size": 1,
                    "_source": [],
                    "query": {"bool": {"filter": [
                        {"term": {"status": "CERTIFICADA"}},
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
                    "doc":      {"sobreposicao_sigef": True},
                })
                enriched += 1

        if updates and not dry_run:
            helpers.bulk(client, updates, raise_on_error=False)

        scroll = client.scroll(scroll_id=scroll_id, params={"scroll": "5m"})
        scroll_id = scroll["_scroll_id"]
        hits      = scroll["hits"]["hits"]

    client.clear_scroll(scroll_id=scroll_id)
    log.info("sigef.enrich_jazidas.done", uf=uf, enriched=enriched)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--uf",             multiple=True, help="UF(s) a processar (ex: MG PA).")
@click.option("--all-ufs",        is_flag=True,  help="Processar todas as 27 UFs.")
@click.option("--index",          is_flag=True,  help="Indexar parcelas SIGEF em mr_sigef_v001.")
@click.option("--enrich-jazidas", is_flag=True,  help="Marcar sobreposicao_sigef em mr_jazidas_v001.")
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
    Bot de ingestão INCRA SIGEF via WFS GML2 → mr_sigef_v001.

    Exemplos:
      python -m bots.bot_sigef --uf MG --index
      python -m bots.bot_sigef --uf MG --uf PA --index
      python -m bots.bot_sigef --all-ufs --index
      python -m bots.bot_sigef --uf MG --dry-run
      python -m bots.bot_sigef --uf MG --enrich-jazidas
    """
    if not uf and not all_ufs:
        raise click.UsageError("Informe --uf {UF} ou --all-ufs")
    if not index and not enrich_jazidas:
        raise click.UsageError("Informe pelo menos --index ou --enrich-jazidas")

    ufs_to_process = list(ALL_UFS if all_ufs else [u.upper() for u in uf])

    if clear_checkpoint:
        reset_checkpoint(BOT_NAME)
        log.info("sigef.checkpoint.reset")

    ckpt = load_checkpoint(BOT_NAME)
    persist_ckpt = not dry_run

    client = get_os_client()

    total_ok  = 0
    total_err = 0
    t_global  = time.monotonic()

    for i, state_uf in enumerate(ufs_to_process):
        log.info("sigef.uf.start", uf=state_uf)

        if index:
            if should_skip_uf(ckpt, state_uf, PHASE_INDEX, resume=resume):
                log.info("sigef.uf.skip_done", uf=state_uf)
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

                if limit_pages is None:
                    mark_uf_done(ckpt, state_uf, PHASE_INDEX, ok)
                    log.info("sigef.uf.checkpoint_done", uf=state_uf, indexed=ok)

        if enrich_jazidas:
            run_enrich_jazidas(client, state_uf, dry_run=dry_run)

        # Pausa entre UFs para evitar rate limiting do servidor INCRA
        if i < len(ufs_to_process) - 1:
            log.info("sigef.uf.sleep", uf=state_uf, sleep_s=SLEEP_BETWEEN_UFS_S)
            time.sleep(SLEEP_BETWEEN_UFS_S)

    elapsed = time.monotonic() - t_global
    log.info(
        "sigef.run.done",
        ufs=len(ufs_to_process),
        ok=total_ok,
        err=total_err,
        elapsed_s=round(elapsed, 1),
    )


if __name__ == "__main__":
    main()
