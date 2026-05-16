"""
bot_provincias.py — Províncias Geológicas → mr_provincias_v001

Não depende de download externo.

Estratégia:
  1. Consulta mr_cprm_v001 (36k ocorrências já indexadas) agrupando por campo `provincia`
  2. Para cada província, coleta todos os pontos de ocorrência via Scroll
  3. Calcula convex hull + buffer (~1° / ~110 km) para preencher lacunas
     entre ocorrências esparsas e representar melhor a extensão real da província
  4. Indexa 8 documentos em mr_provincias_v001 com geo_shape + metadados

As 8 províncias geológicas reconhecidas pelo SGB/CPRM no Brasil:
  São Francisco · Borborema · Mantiqueira · Províncias Amazônicas
  Tocantins · Paraná · Bacias Amazônicas · Parnaíba

Nota sobre metodologia:
  Os polígonos são APROXIMADOS — derivados da distribuição espacial das
  ocorrências minerais. Representam bem o "onde os geólogos encontraram
  mineralizações nessa província", não as fronteiras tectônicas precisas.
  Para cartografia técnica, usar o Mapa Geológico do Brasil 1:1M (SGB, 2004).

Uso:
  python -m bots.bot_provincias               # gera + indexa
  python -m bots.bot_provincias --dry-run      # mostra polígonos sem indexar
  python -m bots.bot_provincias --recreate     # apaga e recria o índice
"""
from __future__ import annotations

import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import click
from opensearchpy import OpenSearch, helpers
from shapely.geometry import MultiPoint, mapping
from shapely.ops import unary_union

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_PROVINCIAS = "mr_provincias_v001"
INDEX_CPRM       = "mr_cprm_v001"

# Buffer em graus (~110 km por grau de latitude).
# 0.8° ≈ 90 km — suficiente para ligar clusters próximos de ocorrências
# e representar a extensão geológica sem exagerar.
BUFFER_DEGREES = 0.8

# Simplificação do polígono final (~10 km de tolerância)
SIMPLIFY_TOLERANCE = 0.1


# ─────────────────────────────────────────────────────────────────────────────
# Metadados curados das províncias
# ─────────────────────────────────────────────────────────────────────────────

PROVINCIA_META: dict[str, dict] = {
    "sao_francisco": {
        "nome": "São Francisco",
        "descricao": (
            "Cráton do São Francisco — núcleo crustal arqueano/paleoproterozoico "
            "estabilizado, flanqueado pelas faixas Brasília, Araçuaí e Riacho do Pontal. "
            "Inclui o Quadrilátero Ferrífero (MG), maior produtor de minério de ferro do Brasil, "
            "e depósitos de ouro (Nova Lima), manganês (Urucum-MG) e fosfato (Patos de Minas)."
        ),
        "minerais_principais": ["Ferro", "Ouro", "Manganês", "Fosfato", "Zinco", "Chumbo"],
    },
    "borborema": {
        "nome": "Borborema",
        "descricao": (
            "Faixa móvel neoproterozoica que ocupa o Nordeste brasileiro (RN, PB, PE, CE, AL, SE). "
            "Rica em tungstênio (scheelita — maior produtor mundial até os anos 1990), "
            "tantalita-columbita, berilo, topázio e pedras preciosas (esmeralda, água-marinha). "
            "Também relevante para caulim e grafita."
        ),
        "minerais_principais": ["Tungstênio", "Tantalita", "Berilo", "Esmeralda", "Caulim", "Grafita"],
    },
    "mantiqueira": {
        "nome": "Mantiqueira",
        "descricao": (
            "Sistema Orogênico Mantiqueira — cinturão neoproterozoico que inclui as faixas "
            "Ribeira, Araçuaí e Dom Feliciano. Cobre MG, RJ, SP, ES, SC, RS e PR. "
            "Principal província de gemas do Brasil: esmeralda (Itabira/MG), alexandrita, "
            "turmalina, topázio imperial. Também ferro (Minas do Espinhaço), grafita e nióbio."
        ),
        "minerais_principais": ["Esmeralda", "Turmalina", "Topázio", "Nióbio", "Ferro", "Grafita"],
    },
    "provincias_amazonicas": {
        "nome": "Províncias Amazônicas",
        "descricao": (
            "Cráton Amazônico — maior cráton do Brasil, cobre PA, AM, MT, RO, RR, AP e AC. "
            "Inclui as sub-províncias de Carajás (maior depósito de Fe do mundo — Vale S11D), "
            "Tapajós (ouro), Rondônia (estanho, nióbio — Pitinga), e Amazonas (manganês — Urucum). "
            "Principal fronteira mineral do Brasil com potencial para terras raras e lítio."
        ),
        "minerais_principais": ["Ferro", "Ouro", "Estanho", "Nióbio", "Manganês", "Cobre", "Terras Raras"],
    },
    "tocantins": {
        "nome": "Tocantins",
        "descricao": (
            "Faixa de dobramentos Tocantins / Faixa Brasília — zona de sutura entre o Cráton "
            "Amazônico e o São Francisco. Cobre TO, GO, MT e partes de MG e DF. "
            "Destaque para nióbio (Catalão/GO — segunda maior reserva mundial), fosfato "
            "(Catalão), cobre (Chapada/GO) e ouro (Alta Floresta/MT)."
        ),
        "minerais_principais": ["Nióbio", "Fosfato", "Cobre", "Ouro", "Níquel", "Cromo"],
    },
    "parana": {
        "nome": "Paraná",
        "descricao": (
            "Bacia do Paraná — sinéclise paleozoica-mesozoica cobre SP, PR, SC, RS, MS e GO. "
            "Petróleo e gás nas bordas, carvão mineral (SC/RS), calcário, areia e argila "
            "para construção civil. Associada a basaltos da Formação Serra Geral "
            "com potencial para depósitos de ágata, ametista (RS) e cobre sedimentar."
        ),
        "minerais_principais": ["Carvão", "Calcário", "Ametista", "Ágata", "Petróleo", "Areia Industrial"],
    },
    "bacias_amazonicas": {
        "nome": "Bacias Amazônicas",
        "descricao": (
            "Bacias sedimentares intracratônicas amazônicas (Amazonas, Solimões). "
            "Potencial para hidrocarbonetos (gás natural — COARI/AM), sal-gema, "
            "calcário e argilas. Menor importância para mineração sólida vs. energia."
        ),
        "minerais_principais": ["Gás Natural", "Sal-gema", "Calcário", "Argila"],
    },
    "parnaiba": {
        "nome": "Parnaíba",
        "descricao": (
            "Bacia do Parnaíba — sinéclise paleozoica que cobre MA, PI, TO, CE e BA. "
            "Potencial para gás natural (explorado por empresa estatal), calcário, "
            "carnaúba (fibras), fosforita e diamante aluvionar no noroeste."
        ),
        "minerais_principais": ["Gás Natural", "Calcário", "Fosforita", "Diamante"],
    },
}

# Nomes no campo `provincia` do mr_cprm_v001 → slug canônico
PROVINCIA_SLUG_MAP: dict[str, str] = {
    "São Francisco":         "sao_francisco",
    "Borborema":             "borborema",
    "Mantiqueira":           "mantiqueira",
    "Províncias Amazônicas": "provincias_amazonicas",
    "Tocantins":             "tocantins",
    "Paraná":                "parana",
    "Bacias Amazônicas":     "bacias_amazonicas",
    "Parnaíba":              "parnaiba",
    # "Indeterminada" é ignorada intencionalmente
}


# ─────────────────────────────────────────────────────────────────────────────
# OpenSearch client
# ─────────────────────────────────────────────────────────────────────────────

def get_os_client() -> OpenSearch:
    use_ssl = settings.opensearch_url.startswith("https")
    kwargs: dict = {
        "hosts": [settings.opensearch_url],
        "use_ssl": use_ssl,
        "verify_certs": False,
        "timeout": 120,
    }
    if settings.opensearch_user and settings.opensearch_pass:
        kwargs["http_auth"] = (settings.opensearch_user, settings.opensearch_pass)
    client = OpenSearch(**kwargs)
    info = client.info()
    log.info("opensearch.ok", version=info["version"]["number"],
             cluster=info["cluster_name"])
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Coleta de pontos por província via Scroll
# ─────────────────────────────────────────────────────────────────────────────

def collect_points_by_provincia(client: OpenSearch) -> dict[str, list[tuple[float, float]]]:
    """
    Scrolleia mr_cprm_v001 e retorna {slug_provincia: [(lon, lat), ...]}
    para as 8 províncias reconhecidas (ignora 'Indeterminada').
    """
    log.info("provincias.collect.start")

    result: dict[str, list[tuple[float, float]]] = {slug: [] for slug in PROVINCIA_META}

    resp = client.search(
        index=INDEX_CPRM,
        scroll="5m",
        size=1000,
        body={
            "_source": ["location", "provincia", "substancias", "uf"],
            "query": {
                "bool": {
                    "must": [
                        {"exists": {"field": "location"}},
                        {"terms": {"provincia.keyword": list(PROVINCIA_SLUG_MAP.keys())}},
                    ]
                }
            },
        },
    )
    scroll_id = resp["_scroll_id"]
    hits = resp["hits"]["hits"]
    total = resp["hits"]["total"]["value"]
    log.info("provincias.collect.scroll_start", total=total)

    while hits:
        for h in hits:
            src = h.get("_source", {})
            loc = src.get("location") or {}
            lat = loc.get("lat")
            lon = loc.get("lon")
            prov_nome = src.get("provincia", "")
            slug = PROVINCIA_SLUG_MAP.get(prov_nome)
            if slug and lat is not None and lon is not None:
                result[slug].append((float(lon), float(lat)))

        resp = client.scroll(scroll_id=scroll_id, scroll="5m")
        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]

    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    for slug, pts in result.items():
        log.info("provincias.collect.provincia",
                 slug=slug, n_points=len(pts))

    return result


def collect_substancias_by_provincia(client: OpenSearch) -> dict[str, list[str]]:
    """
    Agrega as substâncias mais frequentes por província (top-10) via agg OpenSearch.
    """
    aggs: dict = {}
    for nome_prov, slug in PROVINCIA_SLUG_MAP.items():
        aggs[slug] = {
            "filter": {"term": {"provincia.keyword": nome_prov}},
            "aggs": {
                "top_subs": {
                    "terms": {"field": "substancias", "size": 10}
                }
            },
        }

    resp = client.search(
        index=INDEX_CPRM,
        body={"size": 0, "aggs": aggs},
    )
    agg_result = resp.get("aggregations", {})
    return {
        slug: [b["key"] for b in agg_result.get(slug, {}).get("top_subs", {}).get("buckets", [])]
        for slug in PROVINCIA_META
    }


def collect_ufs_by_provincia(client: OpenSearch) -> dict[str, list[str]]:
    """
    Agrega as UFs de cada província.
    """
    aggs: dict = {}
    for nome_prov, slug in PROVINCIA_SLUG_MAP.items():
        aggs[slug] = {
            "filter": {"term": {"provincia.keyword": nome_prov}},
            "aggs": {"ufs": {"terms": {"field": "uf", "size": 30}}},
        }

    resp = client.search(
        index=INDEX_CPRM,
        body={"size": 0, "aggs": aggs},
    )
    agg_result = resp.get("aggregations", {})
    return {
        slug: [b["key"] for b in agg_result.get(slug, {}).get("ufs", {}).get("buckets", [])]
        for slug in PROVINCIA_META
    }


# ─────────────────────────────────────────────────────────────────────────────
# Geração de polígono aproximado
# ─────────────────────────────────────────────────────────────────────────────

def build_polygon(points: list[tuple[float, float]]) -> dict | None:
    """
    Gera um polígono GeoJSON aproximado a partir de uma lista de pontos (lon, lat):
      1. Convex hull do MultiPoint
      2. Buffer de BUFFER_DEGREES (~90 km) para preencher lacunas entre clusters
      3. Simplificação para reduzir tamanho do JSON

    Retorna GeoJSON geometry dict ou None se menos de 3 pontos.
    """
    if len(points) < 3:
        log.warning("provincias.polygon.too_few_points", n=len(points))
        return None

    mp = MultiPoint(points)
    hull = mp.convex_hull
    buffered = hull.buffer(BUFFER_DEGREES)
    simplified = buffered.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)

    if simplified.is_empty:
        return None

    return mapping(simplified)


# ─────────────────────────────────────────────────────────────────────────────
# Indexação
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_index(client: OpenSearch, recreate: bool) -> None:
    exists = client.indices.exists(index=INDEX_PROVINCIAS)
    if exists and not recreate:
        log.info("provincias.index.exists")
        return
    if exists and recreate:
        client.indices.delete(index=INDEX_PROVINCIAS)
        log.info("provincias.index.deleted")

    setup_path = (Path(__file__).parent.parent.parent
                  / "backend" / "scripts" / "setup_indices.py")
    mapping_body = None
    try:
        ns: dict = {}
        src = setup_path.read_text()
        exec(compile(src, str(setup_path), "exec"), ns)  # noqa: S102
        mapping_body = ns.get("MR_PROVINCIAS")
    except Exception as exc:
        log.warning("provincias.index.mapping_fallback", error=str(exc)[:200])

    if mapping_body is None:
        mapping_body = {
            "settings": {"index": {"number_of_shards": 1, "number_of_replicas": 0}},
            "mappings": {
                "properties": {
                    "slug":              {"type": "keyword"},
                    "nome":              {"type": "text",
                                         "fields": {"keyword": {"type": "keyword"}}},
                    "nome_normalizado":  {"type": "keyword"},
                    "n_ocorrencias":     {"type": "integer"},
                    "area_km2":          {"type": "double"},
                    "centroide":         {"type": "geo_point"},
                    "poligono":          {"type": "geo_shape"},
                    "minerais_principais": {"type": "keyword"},
                    "ufs":               {"type": "keyword"},
                    "fonte":             {"type": "keyword"},
                    "indexed_at":        {"type": "date"},
                }
            },
        }

    client.indices.create(index=INDEX_PROVINCIAS, body=mapping_body)
    log.info("provincias.index.created")


def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def build_docs(
    points_by_prov: dict[str, list[tuple[float, float]]],
    subs_by_prov: dict[str, list[str]],
    ufs_by_prov: dict[str, list[str]],
) -> list[dict]:
    """Constrói documentos OpenSearch para as 8 províncias."""
    from shapely.geometry import MultiPoint

    docs = []
    now = datetime.now(timezone.utc).isoformat()

    for slug, meta in PROVINCIA_META.items():
        points = points_by_prov.get(slug, [])
        n = len(points)

        if n < 3:
            log.warning("provincias.doc.skip", slug=slug, n_points=n,
                        reason="menos de 3 pontos — polígono não pode ser gerado")
            continue

        poligono = build_polygon(points)
        if poligono is None:
            log.warning("provincias.doc.polygon_failed", slug=slug)
            continue

        # Centróide do convex hull original (sem buffer)
        mp = MultiPoint(points)
        c = mp.convex_hull.centroid
        centroide = {"lat": round(c.y, 5), "lon": round(c.x, 5)}

        # Área aproximada em km² (grau² × ~111km²)
        from shapely.geometry import shape
        area_km2 = round(shape(poligono).area * (111.32 ** 2), 0)

        # Minerais: combina top do CPRM com a lista curada (curada tem precedência)
        minerais_cprm = subs_by_prov.get(slug, [])
        minerais_curados = meta.get("minerais_principais", [])
        # Curados primeiro, depois adiciona do CPRM que não estejam já listados
        minerais = list(minerais_curados)
        for m in minerais_cprm:
            if m not in minerais:
                minerais.append(m)
        minerais = minerais[:15]  # max 15

        docs.append({
            "slug":               slug,
            "nome":               meta["nome"],
            "nome_normalizado":   _normalize(meta["nome"]),
            "n_ocorrencias":      n,
            "area_km2":           area_km2,
            "centroide":          centroide,
            "poligono":           poligono,
            "descricao":          meta.get("descricao", ""),
            "minerais_principais": minerais,
            "ufs":                ufs_by_prov.get(slug, []),
            "fonte":              "SGB/CPRM (derivado de ocorrências)",
            "indexed_at":         now,
        })

        log.info("provincias.doc.built",
                 slug=slug, n_points=n, area_km2=area_km2,
                 ufs=ufs_by_prov.get(slug, []))

    return docs


def bulk_index(client: OpenSearch, docs: list[dict]) -> None:
    actions = [
        {"_index": INDEX_PROVINCIAS, "_id": d["slug"], "_source": d}
        for d in docs
    ]
    ok, errs = helpers.bulk(client, actions, raise_on_error=False)
    errs_n = len(errs) if isinstance(errs, list) else errs
    log.info("provincias.bulk.done", ok=ok, errors=errs_n)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--dry-run", is_flag=True,
              help="Mostra os polígonos gerados sem indexar")
@click.option("--recreate", is_flag=True,
              help="Apaga e recria mr_provincias_v001 (DESTRUTIVO)")
def main(dry_run: bool, recreate: bool) -> None:
    """
    Deriva polígonos aproximados das províncias geológicas do Brasil a partir
    das ocorrências do mr_cprm_v001 e indexa em mr_provincias_v001.
    Não requer download externo.
    """
    t0 = time.time()
    client = get_os_client()

    # ── 1. Verificar que mr_cprm_v001 existe e tem dados ──
    try:
        count = client.count(index=INDEX_CPRM)["count"]
        log.info("provincias.cprm.count", n=count)
        if count == 0:
            log.error("provincias.cprm.empty",
                      dica="Execute primeiro: python -m bots.bot_cprm --index")
            raise SystemExit(1)
    except Exception as exc:
        log.error("provincias.cprm.error", error=str(exc)[:200])
        raise SystemExit(1)

    # ── 2. Coletar pontos + metadados de mr_cprm_v001 ──
    points_by_prov  = collect_points_by_provincia(client)
    subs_by_prov    = collect_substancias_by_provincia(client)
    ufs_by_prov     = collect_ufs_by_provincia(client)

    # ── 3. Gerar documentos com polígonos ──
    docs = build_docs(points_by_prov, subs_by_prov, ufs_by_prov)
    log.info("provincias.docs.built", n=len(docs))

    if dry_run:
        for d in docs:
            log.info("dry_run.provincia",
                     slug=d["slug"], nome=d["nome"],
                     n_ocorrencias=d["n_ocorrencias"],
                     area_km2=d["area_km2"],
                     ufs=d["ufs"],
                     minerais=d["minerais_principais"][:5])
        log.info("dry_run.done", would_index=len(docs))
        return

    # ── 4. Criar índice + indexar ──
    _ensure_index(client, recreate)
    bulk_index(client, docs)

    log.info("bot_provincias.done",
             elapsed_s=round(time.time() - t0, 1), n=len(docs))


if __name__ == "__main__":
    main()
