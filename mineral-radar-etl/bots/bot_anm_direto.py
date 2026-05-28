"""
bot_anm_direto.py — Ingestão direta SIGMINE → OpenSearch (sem Postgres)

Fluxo simplificado para validação rápida do stack e do índice mr_jazidas_v001:
  1. Download do ZIP de shapefile por UF (ou BRASIL.zip)
  2. Parse com GeoPandas + reprojeção para WGS84
  3. Conversão para doc OpenSearch usando campos disponíveis no shapefile
  4. Bulk index em mr_jazidas_v001

Campos populados neste fluxo (direto):
  numero_processo, ativo, fase, situacao, substancias, substancias_desc,
  uf, area_ha, geom (geo_shape), location (centroide), titular (nome + cnpj_basico
  a partir de NR_CPF_CNP normalizado), indexed_at

Campos que ficam nulos (precisam do pipeline Postgres completo ou outro bot):
  cfem, restricoes_geo, n_restricoes_*, categorias_estrategicas,
  prioridade_estrategica, municipio (enriquecido), regiao

Uso:
  python -m bots.bot_anm_direto --uf MG              # Minas Gerais (~18K processos)
  python -m bots.bot_anm_direto --uf MG --limit 500  # validação rápida
  python -m bots.bot_anm_direto --uf SP --uf RJ       # múltiplas UFs
  python -m bots.bot_anm_direto --all-ativos          # Brasil completo (~267K, BRASIL.zip, carga única)
  python -m bots.bot_anm_direto --inativos          # ~664K inativos (chunks de 50K, baixo RAM)
  python -m bots.bot_anm_direto --inativos --chunk-size 100000
  python -m bots.bot_anm_direto --all-ativos --chunk-size 50000  # opcional p/ ativos
"""
from __future__ import annotations

import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import click
import geopandas as gpd
import httpx
from opensearchpy import OpenSearch, helpers
from tenacity import retry, stop_after_attempt, wait_exponential

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_NAME   = "mr_jazidas_v001"
DEFAULT_BATCH = 300
# Lotes de leitura do shapefile (--inativos usa chunked por padrão)
DEFAULT_SHAPEFILE_CHUNK = 50_000

ANM_SIGMINE_BASE = f"{settings.anm_base_url}/SIGMINE/PROCESSOS_MINERARIOS"
HEADERS = {"User-Agent": settings.anm_user_agent}

ALL_UFS = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO",
    "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR",
    "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
]

# Mapeamento shapefile SIGMINE → campos do índice
# Nomes reais confirmados no SIGMINE v2024
SIGMINE_COLS = {
    "PROCESSO":  "numero_processo",   # ex: "860.037/1980"
    "UF":        "uf",
    "NOME":      "nm_titular",        # nome do titular/requerente
    "FASE":      "fase",              # Autorização de Pesquisa, Concessão de Lavra, etc.
    "SUB":       "sub_codigo",        # código da substância principal
    "AREA_HA":   "area_ha",
    "ANO":       "ano_requerimento",
    "ULT_EVEN":  "ultimo_evento",
    "PRIORIDAD": "prioridade_raw",
    "USO":       "uso_solo",
    "SUBS":      "substancias_desc_raw",  # texto livre com substâncias
    "NR_CPF_CNP": "cnpj_titular_raw",
}

# Fases SIGMINE → situacao no índice
FASE_PARA_SITUACAO = {
    "Requerimento de Pesquisa":           "em_requerimento",
    "Autorização de Pesquisa":            "em_pesquisa",
    "Requerimento de Lavra":              "em_requerimento",
    "Concessão de Lavra":                 "em_lavra",
    "Licenciamento":                      "em_licenciamento",
    "Registro de Extração":               "em_extracao",
    "Requerimento de Lavra Garimpeira":   "em_requerimento",
    "Lavra Garimpeira":                   "em_lavra",
    "Requerimento de Registro de Extração": "em_requerimento",
    "Manifesto de Mina":                  "manifesto_mina",
}


# ─────────────────────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=30))
def download_zip(url: str, dest: Path) -> Path:
    log.info("download.start", url=url)
    with httpx.stream("GET", url, headers=HEADERS, timeout=180, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1024 * 512):
                f.write(chunk)
    log.info("download.done", size_mb=round(dest.stat().st_size / 1e6, 1))
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# Parse shapefile
# ─────────────────────────────────────────────────────────────────────────────

def extract_shapefile_path(zip_path: Path) -> Path:
    """Extrai ZIP ANM (se necessário) e retorna caminho do .shp."""
    with zipfile.ZipFile(zip_path) as zf:
        shp_files = [f for f in zf.namelist() if f.endswith(".shp")]
        if not shp_files:
            raise ValueError(f"Nenhum .shp em {zip_path}")
        extract_dir = zip_path.parent / zip_path.stem
        extract_dir.mkdir(exist_ok=True)
        shp_rel = shp_files[0]
        shp_path = extract_dir / shp_rel
        if not shp_path.exists():
            zf.extractall(extract_dir)
    return shp_path


def shapefile_feature_count(shp_path: Path) -> int:
    import pyogrio
    return int(pyogrio.read_info(shp_path)["features"])


def _read_shapefile_slice(shp_path: Path, skip: int, max_features: int | None) -> gpd.GeoDataFrame:
    """Lê fatia do shapefile via pyogrio (skip_features / max_features)."""
    kwargs: dict = {"engine": "pyogrio", "skip_features": skip}
    if max_features is not None:
        kwargs["max_features"] = max_features
    gdf = gpd.read_file(shp_path, **kwargs)
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    return gdf


def parse_shapefile(zip_path: Path, ativo: bool) -> gpd.GeoDataFrame:
    """Carga completa em memória — mantida para UFs e BRASIL.zip (~267K features, mai/2026)."""
    shp_path = extract_shapefile_path(zip_path)
    gdf = _read_shapefile_slice(shp_path, skip=0, max_features=None)
    log.info("shapefile.colunas", cols=list(gdf.columns))

    gdf["_ativo"] = ativo
    return gdf


def iter_shapefile_chunks(
    zip_path: Path,
    ativo: bool,
    chunk_size: int,
) -> Iterator[tuple[gpd.GeoDataFrame, int, int]]:
    """
    Itera lotes do shapefile sem carregar o arquivo inteiro (~664K inativos no ZIP ANM mai/2026).

    Yields (gdf_chunk, skip_offset, total_features).
    """
    shp_path = extract_shapefile_path(zip_path)
    total = shapefile_feature_count(shp_path)
    log.info("shapefile.chunked.start", path=shp_path.name, total=total, chunk_size=chunk_size)

    logged_cols = False
    for skip in range(0, total, chunk_size):
        gdf = _read_shapefile_slice(shp_path, skip=skip, max_features=chunk_size)
        if gdf.empty:
            break
        if not logged_cols:
            log.info("shapefile.colunas", cols=list(gdf.columns))
            logged_cols = True
        gdf["_ativo"] = ativo
        yield gdf, skip, total
        log.info(
            "shapefile.chunk.done",
            skip=skip,
            chunk_rows=len(gdf),
            pct=round(min(skip + len(gdf), total) / total * 100, 1),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Conversão para doc OpenSearch
# ─────────────────────────────────────────────────────────────────────────────

def _safe_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s not in ("", "None", "nan") else None


def _safe_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _digitos(nr: str | None) -> str:
    """Somente dígitos (NR_CPF_CNP do SIGMINE pode vir mascarado)."""
    if not nr:
        return ""
    return "".join(c for c in str(nr) if c.isdigit())


def _cnpj_basico_a_partir_nr_cpf_cnp(nr_bruto: str | None) -> str | None:
    """
    Extrai raiz de CNPJ (8 dígitos) a partir do campo SIGMINE NR_CPF_CNP.

    Regras (após extrair só dígitos):
    - 0 dígitos       → None (campo vazio).
    - 11 dígitos      → CPF de pessoa física; não popula cnpj_basico.
    - 14 dígitos      → CNPJ completo; retorna os 8 primeiros.
    - > 14 dígitos    → provavelmente concatenação errada; pega os 8 primeiros.
    - 1-7 dígitos     → raiz com zeros à esquerda perdidos; faz zfill(8).
                        (Ocorre quando o banco de origem omite leading zeros, ex.: "54003".)
    - 8-10 e 12-13 d  → raiz ou CNPJ truncado; pega os 8 primeiros após zfill.
    """
    d = _digitos(nr_bruto)
    n = len(d)
    if n == 0:
        return None
    if n == 11:
        return None
    # qualquer coisa ≥ 14 → pega raiz dos 8 primeiros dígitos
    if n >= 14:
        return d[:8]
    # 1-13 (exceto 11): pode ser raiz com zeros perdidos → zfill até 8, depois [:8]
    return d.zfill(8)[:8]


def _parse_substancias(sub_codigo: str | None, subs_desc: str | None) -> tuple[list[str], list[str]]:
    """Retorna (lista_codigos, lista_descricoes) a partir dos campos brutos."""
    codigos: list[str] = []
    descs: list[str] = []

    if sub_codigo:
        for c in str(sub_codigo).replace(";", ",").split(","):
            c = c.strip()
            if c:
                codigos.append(c.upper())

    if subs_desc:
        for d in str(subs_desc).replace(";", ",").split(","):
            d = d.strip()
            if d and d.lower() != "nan":
                descs.append(d)

    return codigos, descs


def row_to_doc(row, uf_hint: str) -> dict | None:
    """Converte uma linha do GeoDataFrame em documento para mr_jazidas_v001."""
    cols = {c.upper(): c for c in row.index}   # mapeamento case-insensitive

    def get(sigmine_key: str):
        real = cols.get(sigmine_key.upper())
        return row[real] if real else None

    numero = _safe_str(get("PROCESSO"))
    if not numero:
        return None

    fase_raw = _safe_str(get("FASE")) or "Desconhecida"
    # Shapefile entrega em CAIXA ALTA — normaliza lowercase para lookup case-insensitive
    _lookup = {k.upper(): v for k, v in FASE_PARA_SITUACAO.items()}
    situacao = _lookup.get(fase_raw.upper(), "ativo" if row["_ativo"] else "inativo")
    fase = fase_raw  # mantém o valor original (CAIXA ALTA) no doc
    sub_cod = _safe_str(get("SUB"))
    subs_desc_raw = _safe_str(get("SUBS"))
    substancias, substancias_desc = _parse_substancias(sub_cod, subs_desc_raw)

    # titular: mínimo do que temos no shapefile (CNPJ só após normalizar dígitos)
    titular_nome = _safe_str(get("NOME"))
    cnpj_raw = _safe_str(get("NR_CPF_CNP"))
    titular = {
        "nome": titular_nome,
        "cnpj_basico": _cnpj_basico_a_partir_nr_cpf_cnp(cnpj_raw),
        "razao_social": titular_nome,
        "situacao_rfb": None,
        "cnae_principal": None,
    }

    doc: dict = {
        "numero_processo":         numero,
        "ativo":                   bool(row["_ativo"]),
        "fase":                    fase,
        "situacao":                situacao,
        "substancias":             substancias,
        "substancias_desc":        substancias_desc,
        "categorias_estrategicas": [],
        "prioridade_estrategica":  None,
        "uf":                      _safe_str(get("UF")) or uf_hint,
        "municipio":               None,   # enriquecimento futuro via Postgres
        "regiao":                  None,
        "area_ha":                 _safe_float(get("AREA_HA")),
        "titular":                 titular,
        "dt_requerimento":         None,
        "dt_validade":             None,
        "cfem": {
            "total_historico": 0.0,
            "ultimo_ano":      0.0,
            "anos_producao":   0,
            "ultima_arrecadacao": None,
        },
        "restricoes_geo":  [],
        "n_restricoes_ti": 0,
        "n_restricoes_uc": 0,
        "indexed_at":      datetime.now(tz=timezone.utc).isoformat(),
    }

    # ANO de requerimento → dt_requerimento aproximado
    ano = _safe_str(get("ANO"))
    if ano and ano.isdigit() and 1900 <= int(ano) <= 2030:
        doc["dt_requerimento"] = f"{ano}-01-01"

    # geo_point: centroide
    if row.geometry and not row.geometry.is_empty:
        try:
            centroide = row.geometry.centroid
            doc["location"] = {"lat": round(centroide.y, 6), "lon": round(centroide.x, 6)}
        except Exception:
            pass

    # geo_shape: polígono completo como GeoJSON (via shapely.geometry.mapping)
    if row.geometry and not row.geometry.is_empty:
        try:
            from shapely.geometry import mapping as shapely_mapping
            doc["geom"] = dict(shapely_mapping(row.geometry))
        except Exception:
            pass

    return doc


# ─────────────────────────────────────────────────────────────────────────────
# OpenSearch client + bulk
# ─────────────────────────────────────────────────────────────────────────────
#
# A busca semântica do MineralRadar é feita via mr_substancias_v001 (k-NN sobre
# 862 vetores) → filtro `terms` em mr_jazidas_v001.substancias. Não há k-NN
# direto no índice principal, portanto não geramos embeddings por processo aqui.
# ─────────────────────────────────────────────────────────────────────────────

def get_os_client() -> OpenSearch:
    use_ssl = settings.opensearch_url.startswith("https")
    kwargs: dict = {
        "hosts": [settings.opensearch_url],
        "use_ssl": use_ssl,
        "verify_certs": False,
        "timeout": 30,
    }
    if settings.opensearch_user and settings.opensearch_pass:
        kwargs["http_auth"] = (settings.opensearch_user, settings.opensearch_pass)
    client = OpenSearch(**kwargs)
    info = client.info()
    log.info("opensearch.ok", version=info["version"]["number"], cluster=info["cluster_name"])
    return client


_SHAPEFILE_CAMPOS = """
    // Campos base vindos do shapefile SIGMINE — sempre atualizados
    ctx._source.fase             = params.fase;
    ctx._source.situacao         = params.situacao;
    ctx._source.ativo            = params.ativo;
    ctx._source.uf               = params.uf;
    ctx._source.area_ha          = params.area_ha;
    ctx._source.substancias      = params.substancias;
    ctx._source.substancias_desc = params.substancias_desc;
    ctx._source.indexed_at       = params.indexed_at;
    if (params.geom     != null) { ctx._source.geom     = params.geom;     }
    if (params.location != null) { ctx._source.location = params.location; }

    // titular — atualiza apenas campos base; preserva situacao_rfb e cnae_principal
    if (ctx._source.titular == null) { ctx._source.titular = new HashMap(); }
    ctx._source.titular.nome         = params.titular_nome;
    ctx._source.titular.razao_social = params.titular_razao_social;
    if (params.titular_cnpj_basico != null) {
        // Sobrescreve sempre que o novo valor é ≥ 8 dígitos (fix de fragmentos
        // curtos que podiam ter ficado gravados por versões anteriores do parse).
        String cur = ctx._source.titular.cnpj_basico;
        boolean cur_valido = (cur != null && cur.length() >= 8);
        if (!cur_valido || params.titular_cnpj_basico.length() >= 8) {
            ctx._source.titular.cnpj_basico = params.titular_cnpj_basico;
        }
    }

    // dt_requerimento aproximado (AAAA-01-01) — preserva valor do sicop se já existe
    if (ctx._source.dt_requerimento == null || ctx._source.dt_requerimento.endsWith('-01-01')) {
        ctx._source.dt_requerimento = params.dt_requerimento;
    }

    // Campos enriquecidos NÃO tocados aqui:
    //   municipio, regiao, codigo_ibge
    //   titular.situacao_rfb, titular.cnae_principal
    //   cfem.*, dt_validade, dt_outorga, nup, situacao_titulo
    //   categorias_estrategicas, prioridade_estrategica
    //   n_restricoes_ti, n_restricoes_uc, restricoes_geo
    //   cprm_ids_proximos, cprm_substancias, n_ocorrencias_cprm
"""


def bulk_index(client: OpenSearch, docs: list[dict]) -> tuple[int, int]:
    """
    Scripted upsert: insere documento completo se não existe;
    atualiza apenas campos do shapefile se já existe, preservando enriquecimentos.
    """
    actions = []
    for d in docs:
        params = {
            "fase":                 d.get("fase"),
            "situacao":             d.get("situacao"),
            "ativo":                d.get("ativo"),
            "uf":                   d.get("uf"),
            "area_ha":              d.get("area_ha"),
            "substancias":          d.get("substancias", []),
            "substancias_desc":     d.get("substancias_desc", []),
            "indexed_at":           d.get("indexed_at"),
            "geom":                 d.get("geom"),
            "location":             d.get("location"),
            "titular_nome":         (d.get("titular") or {}).get("nome"),
            "titular_razao_social": (d.get("titular") or {}).get("razao_social"),
            "titular_cnpj_basico":  (d.get("titular") or {}).get("cnpj_basico"),
            "dt_requerimento":      d.get("dt_requerimento"),
        }
        actions.append({
            "_op_type":         "update",
            "_index":           INDEX_NAME,
            "_id":              d["numero_processo"],
            "script": {
                "source": _SHAPEFILE_CAMPOS,
                "lang":   "painless",
                "params": params,
            },
            "upsert": d,          # documento completo se o doc não existe ainda
            "retry_on_conflict":  3,
        })
    ok, errs = helpers.bulk(client, actions, raise_on_error=False, chunk_size=200)
    return ok, len(errs) if isinstance(errs, list) else errs


def _index_gdf_rows(
    os_client: OpenSearch,
    gdf: gpd.GeoDataFrame,
    uf_hint: str,
    batch_size: int,
    limit: int | None,
    *,
    indexed_so_far: int,
) -> tuple[int, int, int, int]:
    """Indexa linhas de um GeoDataFrame. Retorna (indexed, errors, skipped, indexed_so_far)."""
    batch: list[dict] = []
    skipped = 0
    total_indexed = total_errors = 0
    rows_seen = 0

    for _, row in gdf.iterrows():
        if limit is not None and indexed_so_far + total_indexed >= limit:
            break
        rows_seen += 1
        doc = row_to_doc(row, uf_hint)
        if doc is None:
            skipped += 1
            continue
        batch.append(doc)

        if len(batch) >= batch_size:
            ok, errs = bulk_index(os_client, batch)
            total_indexed += ok
            total_errors += errs
            log.info("batch.done", indexed=ok, errors=errs, total=indexed_so_far + total_indexed)
            batch = []

    if batch:
        ok, errs = bulk_index(os_client, batch)
        total_indexed += ok
        total_errors += errs

    return total_indexed, total_errors, skipped, indexed_so_far + total_indexed


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--uf", multiple=True, help="UF(s) a indexar (ex: --uf MG --uf SP)")
@click.option("--all-ativos", is_flag=True, help="Baixar BRASIL.zip (todos ativos)")
@click.option("--inativos", is_flag=True, help="Baixar PROCESSOS_INATIVOS.zip (~664K features, chunked por padrão)")
@click.option("--batch-size", default=DEFAULT_BATCH, show_default=True)
@click.option(
    "--chunk-size", "shapefile_chunk", default=None, type=int,
    help=f"Lê shapefile em lotes de N features (default inativos: {DEFAULT_SHAPEFILE_CHUNK}). "
         "0 = carga completa em RAM (comportamento legado).",
)
@click.option("--no-chunk", is_flag=True, help="Com --inativos, força carga completa em RAM.")
@click.option("--limit", type=int, default=None, help="Máx. de docs a indexar (para testes)")
@click.option("--skip-download", is_flag=True, help="Reusar ZIP já baixado em etl_data_dir")
def main(
    uf: tuple[str, ...],
    all_ativos: bool,
    inativos: bool,
    batch_size: int,
    shapefile_chunk: int | None,
    no_chunk: bool,
    limit: int | None,
    skip_download: bool,
) -> None:
    """Indexação direta SIGMINE → OpenSearch mr_jazidas_v001 (sem Postgres)."""
    if not uf and not all_ativos and not inativos:
        click.echo("Especifique --uf UF, --all-ativos ou --inativos.")
        raise SystemExit(1)

    os_client = get_os_client()
    data_dir = settings.etl_data_dir / "anm_shapes"
    data_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[str, str, bool]] = []  # (url, uf_hint, ativo)
    if all_ativos:
        jobs.append((f"{ANM_SIGMINE_BASE}/BRASIL.zip", "BR", True))
    if inativos:
        jobs.append((f"{ANM_SIGMINE_BASE}/PROCESSOS_INATIVOS.zip", "BR", False))
    for u in uf:
        jobs.append((f"{ANM_SIGMINE_BASE}/{u.upper()}.zip", u.upper(), True))

    total_indexed = total_errors = 0
    indexed_so_far = 0
    t0 = time.time()

    for url, uf_hint, ativo in jobs:
        fname = url.split("/")[-1]
        zip_path = data_dir / fname

        if not skip_download or not zip_path.exists():
            download_zip(url, zip_path)
        else:
            log.info("download.skip", file=fname)

        # Chunked: default só para --inativos (~664K no ZIP). Ativos/UF mantêm carga única.
        use_chunk = False
        chunk_n = shapefile_chunk
        if inativos and fname == "PROCESSOS_INATIVOS.zip" and not no_chunk:
            use_chunk = True
            if chunk_n is None:
                chunk_n = DEFAULT_SHAPEFILE_CHUNK
        elif shapefile_chunk is not None and shapefile_chunk > 0:
            use_chunk = True
            chunk_n = shapefile_chunk

        skipped_job = 0

        if use_chunk:
            assert chunk_n is not None and chunk_n > 0
            log.info("shapefile.mode", mode="chunked", chunk_size=chunk_n, file=fname)
            for gdf_chunk, skip_off, total_feat in iter_shapefile_chunks(
                zip_path, ativo=ativo, chunk_size=chunk_n,
            ):
                log.info(
                    "shapefile.loaded",
                    uf=uf_hint,
                    rows=len(gdf_chunk),
                    ativo=ativo,
                    skip=skip_off,
                    total=total_feat,
                )
                ok, errs, skipped, indexed_so_far = _index_gdf_rows(
                    os_client, gdf_chunk, uf_hint, batch_size, limit,
                    indexed_so_far=indexed_so_far,
                )
                total_indexed += ok
                total_errors += errs
                skipped_job += skipped
                if limit is not None and indexed_so_far >= limit:
                    log.info("shapefile.limitado", limit=limit)
                    break
        else:
            gdf = parse_shapefile(zip_path, ativo=ativo)
            log.info("shapefile.loaded", uf=uf_hint, rows=len(gdf), ativo=ativo, mode="full")
            if limit:
                gdf = gdf.head(limit)
                log.info("shapefile.limitado", limit=limit)
            ok, errs, skipped, indexed_so_far = _index_gdf_rows(
                os_client, gdf, uf_hint, batch_size, limit=None,
                indexed_so_far=indexed_so_far,
            )
            total_indexed += ok
            total_errors += errs
            skipped_job = skipped

        log.info("uf.done", uf=uf_hint, skipped=skipped_job)

    elapsed = round(time.time() - t0, 1)
    log.info(
        "bot_anm_direto.done",
        total_indexed=total_indexed,
        total_errors=total_errors,
        elapsed_s=elapsed,
        docs_per_sec=round(total_indexed / max(elapsed, 1)),
    )

    if total_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
