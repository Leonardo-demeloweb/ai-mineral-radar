"""
bot_empresas.py — RFB/CNPJ filtrado para o universo mineral → mr_empresas_v001

Fluxo completo:
  1. --enrich-cnpj     : Enriquece mr_jazidas_v001.titular.cnpj_basico via join com
                         mr_cfem_v001 (número do processo como chave de ligação).
  2. --collect-universe: Coleta CNPJs únicos de CFEM + jazidas e salva em disco.
  3. --download        : Baixa os 10+10+10+1 arquivos RFB do mês mais recente disponível
                         (Empresas, Estabelecimentos, Socios, Municipios).
  4. --index           : Lê/filtra/indexa o subconjunto mineral em mr_empresas_v001.
  5. --enrich-jazidas  : Volta ao mr_jazidas_v001 e preenche titular.razao_social,
                         titular.situacao_rfb e titular.cnae_principal.
  6. --all             : Executa todos os passos em sequência.

  CNEFE (geo_point ``location``, sem API paga — ver ``bots/cnefe_geo.py``):
  - Com ``--index``: use ``--cnefe-dir`` (zips IBGE ``*_UF.zip``) e opcionalmente
    ``--download-cnefe`` para baixar só as UFs dos documentos.
  - Só regravar coords: ``--patch-cnefe-location`` + ``--cnefe-dir`` (e opcional
    ``--download-cnefe``). Não altera o geocode em runtime (Azure Maps).

Critérios de inclusão no índice (criterio_inclusao):
  - cfem_payer    : CNPJ que pagou royalties (mr_cfem_v001)
  - anm_titular   : CNPJ que é titular de processo ANM (mr_jazidas_v001)
  - cnae_mineracao: CNAE seção B — Indústrias Extrativas (05xx–09xx)

Uso:
  python -m bots.bot_empresas --enrich-cnpj
  python -m bots.bot_empresas --all
  python -m bots.bot_empresas --all --mes 2026-04
  python -m bots.bot_empresas --skip-download --index
"""
from __future__ import annotations

import io
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import click
import httpx
import polars as pl
from opensearchpy import OpenSearch, helpers

from bots.cnefe_geo import enrich_docs_location_cnefe, patch_index_locations_cnefe
from bots.common.cnpj_basico import normalize_cnpj_basico
from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_EMPRESAS = "mr_empresas_v001"
INDEX_JAZIDAS  = "mr_jazidas_v001"
INDEX_CFEM     = "mr_cfem_v001"

BATCH_SIZE    = 500
SCROLL_SIZE   = 5000
UNIVERSE_FILE = "cnpj_universe.txt"

# CNAEs da seção B (Indústrias Extrativas) — prefixos 05 a 09
CNAE_MINERACAO_PREFIXES = ("05", "06", "07", "08", "09")

# Situação cadastral RFB → texto legível
SITUACAO_MAP = {
    "01": "Nula",
    "02": "Ativa",
    "03": "Suspensa",
    "04": "Inapta",
    "08": "Baixada",
}

# Porte da empresa
PORTE_MAP = {
    "00": "Não informado",
    "01": "Micro Empresa",
    "03": "Empresa de Pequeno Porte",
    "05": "Demais",
}

# Nomes das colunas dos arquivos RFB (sem cabeçalho, separador "|", encoding latin-1)
COLS_EMPRESAS = [
    "cnpj_basico", "razao_social", "natureza_juridica",
    "qualificacao_responsavel", "capital_social", "porte", "ente_federativo",
]
COLS_ESTABELECIMENTOS = [
    "cnpj_basico", "cnpj_ordem", "cnpj_dv", "identificador",
    "nome_fantasia", "situacao_cadastral", "data_situacao",
    "motivo_situacao", "cidade_exterior", "pais",
    "data_inicio_atividade", "cnae_principal", "cnae_secundarios",
    "tipo_logradouro", "logradouro", "numero", "complemento",
    "bairro", "cep", "uf", "municipio_cod",
    "ddd1", "telefone1", "ddd2", "telefone2",
    "ddd_fax", "fax", "email",
    "situacao_especial", "data_situacao_especial",
]
COLS_SOCIOS = [
    "cnpj_basico", "identificador_socio", "nome_socio",
    "cnpj_cpf_socio", "qualificacao_socio", "data_entrada",
    "pais", "representante_legal", "nome_representante",
    "qualificacao_representante", "faixa_etaria",
]
COLS_MUNICIPIOS = ["codigo", "nome"]


# ─────────────────────────────────────────────────────────────────────────────
# OpenSearch client
# ─────────────────────────────────────────────────────────────────────────────

def get_os_client(timeout: int = 60) -> OpenSearch:
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
# Step 1: Enrich jazidas.titular.cnpj_basico via CFEM join
# ─────────────────────────────────────────────────────────────────────────────

def enrich_jazidas_cnpj(client: OpenSearch, dry_run: bool) -> dict[str, int]:
    """
    Para cada numero_processo do mr_cfem_v001, obtém o cnpj_basico mais frequente
    e faz bulk update em mr_jazidas_v001.

    Estratégia:
      - Scroll mr_cfem_v001 (apenas numero_processo + cnpj_basico)
      - Conta frequência: {processo: {cnpj: count}} → pega o mais comum
      - Bulk update partial (doc) nos processos de mr_jazidas_v001
    """
    log.info("enrich_cnpj.start")

    # ── 1. Scroll CFEM: constrói mapa {numero_processo → cnpj_basico} ──
    cnpj_map: dict[str, dict[str, int]] = {}  # {processo: {cnpj: contagem}}
    scroll_resp = client.search(
        index=INDEX_CFEM,
        scroll="5m",
        size=SCROLL_SIZE,
        body={
            "_source": ["numero_processo", "cnpj_basico"],
            "query": {
                "bool": {
                    "must": [
                        {"exists": {"field": "numero_processo"}},
                        {"exists": {"field": "cnpj_basico"}},
                    ]
                }
            },
        },
    )
    scroll_id = scroll_resp["_scroll_id"]
    hits = scroll_resp["hits"]["hits"]
    total_cfem = scroll_resp["hits"]["total"]["value"]
    log.info("enrich_cnpj.cfem_scroll.start", total=total_cfem)

    processed = 0
    while hits:
        for h in hits:
            src = h.get("_source", {})
            proc = src.get("numero_processo", "").strip()
            cnpj = src.get("cnpj_basico", "").strip()
            if proc and cnpj:
                cnpj_map.setdefault(proc, {})
                cnpj_map[proc][cnpj] = cnpj_map[proc].get(cnpj, 0) + 1
        processed += len(hits)
        scroll_resp = client.scroll(scroll_id=scroll_id, scroll="5m")
        scroll_id = scroll_resp["_scroll_id"]
        hits = scroll_resp["hits"]["hits"]

    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    # Para cada processo, pegar o cnpj mais frequente
    processo_cnpj: dict[str, str] = {
        proc: max(cnpjs, key=cnpjs.get)
        for proc, cnpjs in cnpj_map.items()
    }
    log.info("enrich_cnpj.cfem_map_built",
             unique_processos=len(processo_cnpj),
             cfem_docs=processed)

    # ── 2. Scroll jazidas: coleta _id + numero_processo ──
    jazida_hits: list[dict] = []
    scroll_resp = client.search(
        index=INDEX_JAZIDAS,
        scroll="5m",
        size=SCROLL_SIZE,
        body={
            "_source": ["numero_processo"],
            "query": {"match_all": {}},
        },
    )
    scroll_id = scroll_resp["_scroll_id"]
    hits = scroll_resp["hits"]["hits"]
    total_jaz = scroll_resp["hits"]["total"]["value"]
    log.info("enrich_cnpj.jazidas_scroll.start", total=total_jaz)

    while hits:
        jazida_hits.extend(hits)
        scroll_resp = client.scroll(scroll_id=scroll_id, scroll="5m")
        scroll_id = scroll_resp["_scroll_id"]
        hits = scroll_resp["hits"]["hits"]

    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    log.info("enrich_cnpj.jazidas_loaded", total=len(jazida_hits))

    # ── 3. Bulk update jazidas com cnpj_basico ──
    matched = 0
    actions: list[dict] = []

    for h in jazida_hits:
        proc_raw = h.get("_source", {}).get("numero_processo", "") or ""
        # numero_processo no índice é "NNNNNN/AAAA" — extrai só a parte numérica
        proc_num = proc_raw.split("/")[0].strip()
        cnpj = processo_cnpj.get(proc_num)
        if not cnpj:
            continue
        matched += 1
        actions.append({
            "_op_type": "update",
            "_index":   INDEX_JAZIDAS,
            "_id":      h["_id"],
            "doc":      {"titular": {"cnpj_basico": cnpj}},
        })

    log.info("enrich_cnpj.updates_built",
             matched=matched,
             total=len(jazida_hits),
             pct=round(matched / max(len(jazida_hits), 1) * 100, 1))

    ok = err = 0
    if not dry_run:
        # Garante que campo existe no mapping
        try:
            client.indices.put_mapping(
                index=INDEX_JAZIDAS,
                body={"properties": {"titular": {"properties": {"cnpj_basico": {"type": "keyword"}}}}},
            )
        except Exception:
            pass

        for i in range(0, len(actions), BATCH_SIZE):
            batch = actions[i:i + BATCH_SIZE]
            _ok, _errs = helpers.bulk(client, batch, raise_on_error=False)
            ok  += _ok
            err += len(_errs) if isinstance(_errs, list) else _errs
            if (i // BATCH_SIZE) % 20 == 0:
                log.info("enrich_cnpj.update.progress",
                         done=ok, errors=err)
    else:
        ok = matched
        log.info("enrich_cnpj.dry_run", would_update=ok)

    log.info("enrich_cnpj.done", ok=ok, errors=err)
    return {"matched": matched, "ok": ok, "errors": err}


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Coleta universo de CNPJs minerais
# ─────────────────────────────────────────────────────────────────────────────

def collect_cnpj_universe(client: OpenSearch, data_dir: Path) -> set[str]:
    """
    Coleta os cnpj_basico únicos de:
      - mr_cfem_v001 (pagadores de royalties)
      - mr_jazidas_v001.titular.cnpj_basico (titulares de processos)

    Salva em data_dir/cnpj_universe.txt (um por linha) e retorna o set.
    """
    log.info("universe.start")
    universe: set[str] = set()

    # Agrega via cardinality e depois terms para coleta real
    # (terms agg tem limite; usamos scroll para garantir todos)
    for index, field in [
        (INDEX_CFEM, "cnpj_basico"),
        (INDEX_JAZIDAS, "titular.cnpj_basico"),
    ]:
        scroll_resp = client.search(
            index=index,
            scroll="5m",
            size=SCROLL_SIZE,
            body={
                "_source": [field],
                "query": {"exists": {"field": field}},
            },
        )
        scroll_id = scroll_resp["_scroll_id"]
        hits = scroll_resp["hits"]["hits"]
        total = scroll_resp["hits"]["total"]["value"]
        log.info("universe.scroll.start", index=index, field=field, total=total)

        count = 0
        while hits:
            for h in hits:
                src = h.get("_source", {})
                # Suporte a campos nested (titular.cnpj_basico)
                val = src
                for part in field.split("."):
                    val = val.get(part, {}) if isinstance(val, dict) else None
                if val and isinstance(val, str):
                    nb = normalize_cnpj_basico(val)
                    if nb:
                        universe.add(nb)
                        count += 1
            scroll_resp = client.scroll(scroll_id=scroll_id, scroll="5m")
            scroll_id = scroll_resp["_scroll_id"]
            hits = scroll_resp["hits"]["hits"]

        try:
            client.clear_scroll(scroll_id=scroll_id)
        except Exception:
            pass
        log.info("universe.scroll.done", index=index, docs_with_cnpj=count)

    log.info("universe.total", unique_cnpjs=len(universe))

    # Salva em disco para reuso
    universe_file = data_dir / UNIVERSE_FILE
    universe_file.write_text("\n".join(sorted(universe)), encoding="utf-8")
    log.info("universe.saved", path=str(universe_file))
    return universe


def load_cnpj_universe(data_dir: Path) -> set[str]:
    """Lê universo salvo em disco."""
    f = data_dir / UNIVERSE_FILE
    if not f.exists():
        raise FileNotFoundError(f"Universo não encontrado: {f}. Execute --collect-universe primeiro.")
    universe = set(f.read_text(encoding="utf-8").splitlines())
    log.info("universe.loaded", total=len(universe))
    return universe


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Download arquivos RFB
# ─────────────────────────────────────────────────────────────────────────────

# Fontes de dados RFB em ordem de preferência
# Casa dos Dados = CDN Cloudflare; publica snapshots em datas fixas (~dia 14 de cada mês)
_CDN_BASE    = "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos/"
# RFB oficial novo (Nextcloud) — URL de pasta compartilhada, requer navegação web
_RFB_LEGACY  = "https://dadosabertos.rfb.gov.br/CNPJ/dados_abertos_cnpj/"


def _probe_cdn_snapshot(mes: str) -> str | None:
    """
    Descobre o snapshot mais recente disponível no CDN Casa dos Dados para o mês dado.
    Os snapshots têm datas como AAAA-MM-14 ou AAAA-MM-01.
    Retorna a URL base do snapshot ou None.
    """
    year, month = map(int, mes.split("-"))
    # Tenta os últimos 6 meses, dias comuns de publicação: 14, 1
    candidatos: list[str] = []
    for delta in range(6):
        m = month - delta
        y = year
        while m <= 0:
            m += 12
            y -= 1
        for dia in (14, 1, 21, 7):
            candidatos.append(f"{y:04d}-{m:02d}-{dia:02d}")

    for data in candidatos:
        url = f"{_CDN_BASE}{data}/Empresas0.zip"
        try:
            r = httpx.head(url, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                return f"{_CDN_BASE}{data}/"
        except Exception:
            pass
    return None


def _probe_rfb_legacy(mes: str) -> str | None:
    """Tenta o path legado dadosabertos.rfb.gov.br para o mês e os 3 anteriores."""
    year, month = map(int, mes.split("-"))
    for delta in range(4):
        m = month - delta
        y = year
        while m <= 0:
            m += 12
            y -= 1
        tentativa = f"{y:04d}-{m:02d}"
        url = f"{_RFB_LEGACY}{tentativa}/Empresas0.zip"
        try:
            r = httpx.head(url, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                return f"{_RFB_LEGACY}{tentativa}/"
        except Exception:
            pass
    return None


def _try_download(url: str, dest: Path, skip: bool) -> bool:
    """Retorna True se arquivo disponível (baixado ou já existia)."""
    if skip and dest.exists() and dest.stat().st_size > 0:
        log.info("rfb.download.skip", file=dest.name,
                 size_mb=round(dest.stat().st_size / 1e6, 1))
        return True
    log.info("rfb.download.start", url=url)
    try:
        with httpx.stream("GET", url, timeout=600, follow_redirects=True) as resp:
            if resp.status_code == 404:
                log.warning("rfb.download.not_found", url=url)
                return False
            resp.raise_for_status()
            with open(dest, "wb") as f:
                written = 0
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    written += len(chunk)
                    if written % (50 * 1024 * 1024) < 1024 * 1024:
                        log.info("rfb.download.progress",
                                 file=dest.name, mb=round(written / 1e6, 1))
        log.info("rfb.download.done", file=dest.name,
                 size_mb=round(dest.stat().st_size / 1e6, 1))
        return True
    except Exception as exc:
        log.error("rfb.download.error", url=url, error=str(exc)[:200])
        if dest.exists():
            dest.unlink()
        return False


def download_rfb(data_dir: Path, mes: str, skip: bool) -> dict[str, list[Path]]:
    """
    Baixa os arquivos RFB (10 Empresas + 10 Estabelecimentos + 10 Socios + Municipios).

    Tenta em ordem:
      1. CDN Casa dos Dados (https://dados-abertos-rf-cnpj.casadosdados.com.br)
         Snapshots mensais; formato de data AAAA-MM-DD.
      2. RFB legacy (https://dadosabertos.rfb.gov.br) — fallback.

    Retorna dict com listas de paths: empresas, estabelecimentos, socios, municipios.
    """
    data_dir.mkdir(parents=True, exist_ok=True)

    log.info("rfb.probe.start", mes=mes)
    base_url = _probe_cdn_snapshot(mes) or _probe_rfb_legacy(mes)
    if not base_url:
        raise RuntimeError(
            f"Nenhuma fonte RFB disponível para ~{mes}. "
            "Verifique manualmente: https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos/"
        )
    log.info("rfb.source_found", url=base_url)

    paths: dict[str, list[Path]] = {
        "empresas": [], "estabelecimentos": [], "socios": [], "municipios": [],
    }

    for i in range(10):
        for tipo in ("empresas", "estabelecimentos", "socios"):
            nome = tipo.capitalize()
            filename = f"{nome}{i}.zip"
            dest = data_dir / filename
            ok = _try_download(base_url + filename, dest, skip)
            if ok:
                paths[tipo].append(dest)

    for extra in ("Municipios.zip", "Cnaes.zip"):
        dest_extra = data_dir / extra
        ok = _try_download(base_url + extra, dest_extra, skip)
        if ok and extra == "Municipios.zip":
            paths["municipios"].append(dest_extra)

    log.info("rfb.download.summary",
             empresas=len(paths["empresas"]),
             estabelecimentos=len(paths["estabelecimentos"]),
             socios=len(paths["socios"]),
             municipios=len(paths["municipios"]))
    return paths


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Leitura, filtragem e indexação
# ─────────────────────────────────────────────────────────────────────────────

def _read_zip_csv(
    zip_path: Path,
    col_names: list[str],
    filter_col: str,
    filter_set: set[str] | None = None,
) -> pl.DataFrame:
    """
    Lê o primeiro CSV dentro de um ZIP e retorna DataFrame Polars.
    Todos os arquivos RFB usam separador ";" com aspas duplas, encoding latin-1.
    Se filter_set for fornecido, filtra pelo filter_col.
    """
    with zipfile.ZipFile(zip_path) as zf:
        inner = zf.namelist()[0]
        with zf.open(inner) as f:
            raw = f.read()

    buf = io.BytesIO(raw)
    df = pl.read_csv(
        buf,
        separator=";",
        quote_char='"',
        encoding="latin1",
        has_header=False,
        new_columns=col_names,
        infer_schema_length=0,  # tudo como string
        ignore_errors=True,
        truncate_ragged_lines=True,
    )

    if filter_set is not None:
        df = df.filter(pl.col(filter_col).is_in(filter_set))

    return df


def _load_municipios_rfb(paths: list[Path]) -> dict[str, str]:
    """
    Carrega tabela RFB de código de município → nome.
    Municipios.zip usa separador ";" com aspas (ex: "0001";"GUAJARA-MIRIM").
    """
    if not paths:
        return {}
    with zipfile.ZipFile(paths[0]) as zf:
        inner = zf.namelist()[0]
        with zf.open(inner) as f:
            raw = f.read()
    buf = io.BytesIO(raw)
    df = pl.read_csv(
        buf,
        separator=";",
        encoding="latin1",
        has_header=False,
        new_columns=COLS_MUNICIPIOS,
        infer_schema_length=0,
        ignore_errors=True,
        quote_char='"',
    )
    return dict(zip(df["codigo"].to_list(), df["nome"].to_list()))


def _safe_date(val: str | None) -> str | None:
    """Converte datas AAAAMMDD do RFB para ISO (AAAA-MM-DD)."""
    if not val or len(val) != 8 or val == "00000000":
        return None
    try:
        return f"{val[:4]}-{val[4:6]}-{val[6:8]}"
    except Exception:
        return None


def _parse_cnae_secundarios(val: str | None) -> list[str]:
    """Parsing de CNAEs secundários — separados por vírgula no RFB."""
    if not val:
        return []
    return [c.strip() for c in val.split(",") if c.strip()]


def build_empresa_docs(
    paths: dict[str, list[Path]],
    cnpj_universe: set[str],
    municipios_rfb: dict[str, str],
) -> dict[str, dict]:
    """
    Lê todos os arquivos RFB e constrói dict {cnpj_basico: doc} filtrando por:
      - cnpj_basico no universo mineral (cfem + anm_titular)
      - OU cnae_principal da seção B (mineração)

    Retorna dict com documentos prontos para indexação.
    """
    log.info("build_docs.start")

    # ── Empresas: razão social, porte, natureza jurídica ──
    empresa_frames: list[pl.DataFrame] = []
    for zp in paths.get("empresas", []):
        log.info("build_docs.read_empresas", file=zp.name)
        df = _read_zip_csv(zp, COLS_EMPRESAS, "cnpj_basico", cnpj_universe)
        empresa_frames.append(df)
        log.info("build_docs.empresas_rows", file=zp.name, rows=len(df))

    df_emp = pl.concat(empresa_frames) if empresa_frames else pl.DataFrame(
        schema={c: pl.Utf8 for c in COLS_EMPRESAS}
    )
    # Deduplicar (pode haver múltiplos registros por cnpj_basico)
    df_emp = df_emp.unique(subset=["cnpj_basico"])
    log.info("build_docs.empresas_total", unique=len(df_emp))

    # ── Estabelecimentos: endereço, CNAE, situação ──
    # Inclui também CNAEs de mineração mesmo que não estejam no universo inicial
    estab_frames: list[pl.DataFrame] = []
    for zp in paths.get("estabelecimentos", []):
        log.info("build_docs.read_estab", file=zp.name)
        df_all = _read_zip_csv(zp, COLS_ESTABELECIMENTOS, "cnpj_basico")
        # Filtra: in universe OU CNAE mineração OU matriz
        mask_universe = pl.col("cnpj_basico").is_in(cnpj_universe)
        mask_cnae = pl.col("cnae_principal").str.slice(0, 2).is_in(list(CNAE_MINERACAO_PREFIXES))
        # Só estabelecimentos matriz (identificador == "1")
        mask_matriz = pl.col("identificador") == "1"
        df_filt = df_all.filter((mask_universe | mask_cnae) & mask_matriz)
        estab_frames.append(df_filt)
        log.info("build_docs.estab_rows", file=zp.name,
                 all=len(df_all), filtered=len(df_filt))
        del df_all

    df_est = pl.concat(estab_frames) if estab_frames else pl.DataFrame(
        schema={c: pl.Utf8 for c in COLS_ESTABELECIMENTOS}
    )
    df_est = df_est.unique(subset=["cnpj_basico"])
    log.info("build_docs.estab_total", unique=len(df_est))

    # CNPJs novos descobertos via CNAE (mineração) não estavam no universo
    cnpj_cnae = set(df_est["cnpj_basico"].to_list())
    novos_cnae = cnpj_cnae - cnpj_universe
    if novos_cnae:
        log.info("build_docs.cnpj_cnae_novos", count=len(novos_cnae))

    # ── Sócios ──
    # Agrega por cnpj_basico: listas de nomes, cpf/cnpj, qualificações
    cnpj_all = cnpj_universe | cnpj_cnae
    socios_agg: dict[str, dict] = {}  # {cnpj_basico: {nomes: [], cpfs: [], quals: []}}
    for zp in paths.get("socios", []):
        log.info("build_docs.read_socios", file=zp.name)
        df = _read_zip_csv(zp, COLS_SOCIOS, "cnpj_basico", cnpj_all)
        for row in df.iter_rows(named=True):
            cnpj = row["cnpj_basico"]
            if cnpj not in socios_agg:
                socios_agg[cnpj] = {"nomes": [], "cpfs": [], "quals": []}
            nome = (row.get("nome_socio") or "").strip()
            cpf  = (row.get("cnpj_cpf_socio") or "").strip()
            qual = (row.get("qualificacao_socio") or "").strip()
            if nome:
                socios_agg[cnpj]["nomes"].append(nome)
            if cpf:
                socios_agg[cnpj]["cpfs"].append(cpf)
            if qual:
                socios_agg[cnpj]["quals"].append(qual)
    log.info("build_docs.socios_agg", cnpjs_com_socios=len(socios_agg))

    # ── Merge final: Empresas + Estabelecimentos + Sócios ──
    emp_dict   = {r["cnpj_basico"]: r for r in df_emp.iter_rows(named=True)}
    estab_dict = {r["cnpj_basico"]: r for r in df_est.iter_rows(named=True)}

    docs: dict[str, dict] = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    for cnpj in cnpj_all:
        emp   = emp_dict.get(cnpj, {})
        estab = estab_dict.get(cnpj, {})
        socios = socios_agg.get(cnpj, {})

        # Critério de inclusão
        criterios = []
        if cnpj in cnpj_universe:
            criterios.append("cfem_payer" if cnpj in cnpj_universe else "anm_titular")
        cnae = (estab.get("cnae_principal") or "").strip()
        if cnae[:2] in CNAE_MINERACAO_PREFIXES:
            criterios.append("cnae_mineracao")

        # Situação
        sit_code = (estab.get("situacao_cadastral") or "").strip()
        situacao = SITUACAO_MAP.get(sit_code, sit_code)

        # Porte
        porte_code = (emp.get("porte") or "").strip()
        porte = PORTE_MAP.get(porte_code, porte_code)

        # Municipio name (código → nome)
        mun_cod = (estab.get("municipio_cod") or "").strip()
        municipio = municipios_rfb.get(mun_cod, mun_cod)

        # CNPJ completo da matriz (basico + 0001 + DV)
        ordem = (estab.get("cnpj_ordem") or "").strip()
        dv    = (estab.get("cnpj_dv") or "").strip()
        cnpj_completo = f"{cnpj}{ordem}{dv}" if ordem and dv else cnpj

        razao = (emp.get("razao_social") or estab.get("nome_fantasia") or "").strip()
        if not razao:
            continue  # descarta se não temos razão social

        # CNAE secundários
        cnae_sec_raw = (estab.get("cnae_secundarios") or "").strip()
        cnae_sec = _parse_cnae_secundarios(cnae_sec_raw)

        # Telefones (DDD + número unificados)
        ddd1 = (estab.get("ddd1") or "").strip()
        tel1 = (estab.get("telefone1") or "").strip()
        telefone = f"({ddd1}){tel1}" if ddd1 and tel1 else tel1 or None

        ddd2 = (estab.get("ddd2") or "").strip()
        tel2 = (estab.get("telefone2") or "").strip()
        telefone2 = f"({ddd2}){tel2}" if ddd2 and tel2 else tel2 or None

        # Capital social (float ou None)
        cap_raw = (emp.get("capital_social") or "").strip().replace(",", ".")
        capital_social: float | None = None
        try:
            v = float(cap_raw)
            if v > 0:
                capital_social = round(v, 2)
        except (ValueError, TypeError):
            pass

        doc: dict = {
            "cnpj_basico":          cnpj,
            "cnpj_completo":        cnpj_completo,
            "razao_social":         razao,
            "nome_fantasia":        (estab.get("nome_fantasia") or "").strip() or None,
            "situacao":             situacao,
            "dt_situacao":          _safe_date(estab.get("data_situacao")),
            "dt_abertura":          _safe_date(estab.get("data_inicio_atividade")),
            "cnae_principal":       cnae or None,
            "cnae_desc":            None,  # enriquecido depois se mr_cnae_v001 tiver dados
            "cnaes_secundarios":    cnae_sec,
            "porte":                porte,
            "capital_social":       capital_social,
            "natureza_juridica":    (emp.get("natureza_juridica") or "").strip() or None,
            "criterio_inclusao":    "+".join(criterios) if criterios else "cnae_mineracao",
            "logradouro":           (
                ((estab.get("tipo_logradouro") or "") + " " +
                 (estab.get("logradouro") or "")).strip() or None
            ),
            "numero":               (estab.get("numero") or "").strip() or None,
            "complemento":          (estab.get("complemento") or "").strip() or None,
            "bairro":               (estab.get("bairro") or "").strip() or None,
            "municipio":            municipio or None,
            "uf":                   (estab.get("uf") or "").strip() or None,
            "cep":                  (estab.get("cep") or "").strip() or None,
            "telefone":             telefone,
            "telefone2":            telefone2,
            "email":                (estab.get("email") or "").strip().lower() or None,
            "socios_nomes":         socios.get("nomes", []),
            "socios_cpf_cnpj":      socios.get("cpfs", []),
            "socios_qualificacoes": socios.get("quals", []),
            "socios_count":         len(socios.get("nomes", [])),
            "processos_anm_count":  None,
            "fases_anm":            [],
            "indexed_at":           now_iso,
        }
        docs[cnpj] = doc

    log.info("build_docs.done", total_docs=len(docs))
    return docs


def index_empresas(client: OpenSearch, docs: dict[str, dict]) -> dict[str, int]:
    """Bulk index dos documentos em mr_empresas_v001."""
    log.info("index_empresas.start", total=len(docs))

    def _iter_actions() -> Iterator[dict]:
        for cnpj, doc in docs.items():
            yield {
                "_index":  INDEX_EMPRESAS,
                "_id":     cnpj,
                "_source": doc,
            }

    ok = err = 0
    batch: list[dict] = []
    for action in _iter_actions():
        batch.append(action)
        if len(batch) >= BATCH_SIZE:
            _ok, _errs = helpers.bulk(client, batch, raise_on_error=False)
            ok  += _ok
            err += len(_errs) if isinstance(_errs, list) else _errs
            batch = []
            if ok % 5000 < BATCH_SIZE:
                log.info("index_empresas.progress", ok=ok, errors=err)

    if batch:
        _ok, _errs = helpers.bulk(client, batch, raise_on_error=False)
        ok  += _ok
        err += len(_errs) if isinstance(_errs, list) else _errs

    log.info("index_empresas.done", ok=ok, errors=err)
    return {"ok": ok, "errors": err}


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Enrich jazidas com dados da empresa
# ─────────────────────────────────────────────────────────────────────────────

def enrich_jazidas_empresa(client: OpenSearch, dry_run: bool) -> dict[str, int]:
    """
    Para cada jazida com titular.cnpj_basico preenchido, busca em mr_empresas_v001
    e atualiza:
      - titular.razao_social
      - titular.situacao_rfb
      - titular.cnae_principal
    """
    log.info("enrich_empresa.start")

    # Scroll jazidas com cnpj_basico
    jazida_hits: list[dict] = []
    scroll_resp = client.search(
        index=INDEX_JAZIDAS,
        scroll="5m",
        size=SCROLL_SIZE,
        body={
            "_source": ["titular.cnpj_basico"],
            "query": {"exists": {"field": "titular.cnpj_basico"}},
        },
    )
    scroll_id = scroll_resp["_scroll_id"]
    hits = scroll_resp["hits"]["hits"]
    total = scroll_resp["hits"]["total"]["value"]
    log.info("enrich_empresa.jazidas_scroll.start", total=total)

    while hits:
        jazida_hits.extend(hits)
        scroll_resp = client.scroll(scroll_id=scroll_id, scroll="5m")
        scroll_id = scroll_resp["_scroll_id"]
        hits = scroll_resp["hits"]["hits"]
    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass
    log.info("enrich_empresa.loaded", total=len(jazida_hits))

    # Coleta CNPJs únicos e busca em mr_empresas_v001 em lotes
    cnpj_to_jaz: dict[str, list[str]] = {}  # {cnpj: [_id, ...]}
    for h in jazida_hits:
        cnpj = (h.get("_source", {}).get("titular", {}) or {}).get("cnpj_basico")
        if cnpj:
            cnpj_to_jaz.setdefault(cnpj, []).append(h["_id"])

    unique_cnpjs = list(cnpj_to_jaz.keys())
    log.info("enrich_empresa.unique_cnpjs", count=len(unique_cnpjs))

    empresa_data: dict[str, dict] = {}
    for i in range(0, len(unique_cnpjs), 500):
        chunk = unique_cnpjs[i:i + 500]
        resp = client.search(
            index=INDEX_EMPRESAS,
            body={
                "size": 500,
                "query": {"terms": {"cnpj_basico": chunk}},
                "_source": ["cnpj_basico", "razao_social", "situacao", "cnae_principal"],
            },
        )
        for h in resp["hits"]["hits"]:
            src = h["_source"]
            empresa_data[src["cnpj_basico"]] = src

    log.info("enrich_empresa.empresas_found", count=len(empresa_data))

    # Bulk update jazidas
    actions: list[dict] = []
    for cnpj, jaz_ids in cnpj_to_jaz.items():
        emp = empresa_data.get(cnpj)
        if not emp:
            continue
        for jaz_id in jaz_ids:
            actions.append({
                "_op_type": "update",
                "_index":   INDEX_JAZIDAS,
                "_id":      jaz_id,
                "doc": {
                    "titular": {
                        "cnpj_basico":     cnpj,
                        "razao_social":    emp.get("razao_social"),
                        "situacao_rfb":    emp.get("situacao"),
                        "cnae_principal":  emp.get("cnae_principal"),
                    }
                },
            })

    log.info("enrich_empresa.updates_built", total=len(actions))

    ok = err = 0
    if not dry_run:
        for i in range(0, len(actions), BATCH_SIZE):
            batch = actions[i:i + BATCH_SIZE]
            _ok, _errs = helpers.bulk(client, batch, raise_on_error=False)
            ok  += _ok
            err += len(_errs) if isinstance(_errs, list) else _errs
            if (i // BATCH_SIZE) % 20 == 0:
                log.info("enrich_empresa.progress", ok=ok, errors=err)
    else:
        ok = len(actions)
        log.info("enrich_empresa.dry_run", would_update=ok)

    log.info("enrich_empresa.done", ok=ok, errors=err)
    return {"ok": ok, "errors": err}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--all",            "run_all",         is_flag=True,
              help="Executa todos os passos em sequência.")
@click.option("--enrich-cnpj",    "enrich_cnpj",     is_flag=True,
              help="Enriquece jazidas.titular.cnpj_basico via join com CFEM.")
@click.option("--collect-universe","collect_universe",is_flag=True,
              help="Coleta CNPJs únicos e salva universe.txt.")
@click.option("--download",       "do_download",     is_flag=True,
              help="Baixa arquivos RFB do mês mais recente.")
@click.option("--index",          "do_index",        is_flag=True,
              help="Lê RFB, filtra e indexa mr_empresas_v001.")
@click.option("--enrich-jazidas", "enrich_jazidas",  is_flag=True,
              help="Enriquece jazidas com razao_social/situacao/cnae de mr_empresas_v001.")
@click.option("--skip-download",  is_flag=True,
              help="Reutiliza arquivos RFB já baixados.")
@click.option("--mes",            default="2026-05",  show_default=True,
              help="Mês de referência dos dados RFB (AAAA-MM).")
@click.option("--data-dir",       default=None,
              help="Diretório para arquivos temporários (default: settings.etl_data_dir/rfb).")
@click.option("--dry-run",        is_flag=True,
              help="Simula sem escrever no OpenSearch.")
@click.option(
    "--cnefe-dir",
    default=None,
    help="Pasta com zips CNEFE 2022 (Arquivos_CNEFE/CSV/UF, ex.: 15_PA.zip). "
         "Default com --download-cnefe: <etl_data_dir>/cnefe.",
)
@click.option(
    "--download-cnefe",
    is_flag=True,
    help="Baixa do FTP IBGE os zips CNEFE das UFs necessárias (só com --index ou --patch-cnefe-location).",
)
@click.option(
    "--patch-cnefe-location",
    is_flag=True,
    help="Atualiza só o campo ``location`` em mr_empresas_v001 via CNEFE (scroll + bulk).",
)
def main(
    run_all: bool,
    enrich_cnpj: bool,
    collect_universe: bool,
    do_download: bool,
    do_index: bool,
    enrich_jazidas: bool,
    skip_download: bool,
    mes: str,
    data_dir: str | None,
    dry_run: bool,
    cnefe_dir: str | None,
    download_cnefe: bool,
    patch_cnefe_location: bool,
) -> None:
    """bot_empresas — RFB/CNPJ filtrado para universo mineral."""
    t0 = time.time()

    _data_dir = Path(data_dir) if data_dir else settings.etl_data_dir / "rfb"
    _data_dir.mkdir(parents=True, exist_ok=True)

    client = get_os_client()

    if run_all:
        enrich_cnpj = collect_universe = do_download = do_index = enrich_jazidas = True

    if enrich_cnpj:
        log.info("--- step 1: enrich jazidas cnpj ---")
        res = enrich_jazidas_cnpj(client, dry_run=dry_run)
        log.info("step1.done", **res)

    if collect_universe:
        log.info("--- step 2: collect cnpj universe ---")
        universe = collect_cnpj_universe(client, _data_dir)
        log.info("step2.done", universe_size=len(universe))

    rfb_paths: dict[str, list[Path]] = {}
    if do_download:
        log.info("--- step 3: download rfb ---")
        rfb_paths = download_rfb(_data_dir, mes=mes, skip=skip_download)
        log.info("step3.done", files=sum(len(v) for v in rfb_paths.values()))

    if do_index:
        log.info("--- step 4: index mr_empresas_v001 ---")
        # Carrega universe do disco se não foi coletado nesta execução
        universe = collect_cnpj_universe(client, _data_dir) if collect_universe else load_cnpj_universe(_data_dir)

        # Carrega paths se não baixou nesta execução
        if not rfb_paths:
            rfb_paths = {
                "empresas":          sorted(_data_dir.glob("Empresas*.zip")),
                "estabelecimentos":  sorted(_data_dir.glob("Estabelecimentos*.zip")),
                "socios":            sorted(_data_dir.glob("Socios*.zip")),
                "municipios":        sorted(_data_dir.glob("Municipios.zip")),
            }
            log.info("step4.rfb_from_disk",
                     empresas=len(rfb_paths["empresas"]),
                     estab=len(rfb_paths["estabelecimentos"]),
                     socios=len(rfb_paths["socios"]))

        municipios_rfb = _load_municipios_rfb(rfb_paths.get("municipios", []))
        log.info("step4.municipios_rfb", total=len(municipios_rfb))

        docs = build_empresa_docs(rfb_paths, universe, municipios_rfb)

        if cnefe_dir or download_cnefe:
            cnefe_path = Path(cnefe_dir) if cnefe_dir else settings.etl_data_dir / "cnefe"
            log.info("step4.cnefe.start", dir=str(cnefe_path), download=download_cnefe)
            enrich_docs_location_cnefe(
                docs, cnefe_path, download_missing=download_cnefe
            )

        if not dry_run:
            res = index_empresas(client, docs)
            log.info("step4.done", **res)
        else:
            log.info("step4.dry_run", would_index=len(docs))

    if enrich_jazidas:
        log.info("--- step 5: enrich jazidas empresa ---")
        res = enrich_jazidas_empresa(client, dry_run=dry_run)
        log.info("step5.done", **res)

    if patch_cnefe_location:
        cnefe_path = Path(cnefe_dir) if cnefe_dir else settings.etl_data_dir / "cnefe"
        log.info("--- patch: CNEFE location on mr_empresas_v001 ---", dir=str(cnefe_path))
        res = patch_index_locations_cnefe(
            client,
            INDEX_EMPRESAS,
            cnefe_path,
            download_missing=download_cnefe,
            dry_run=dry_run,
        )
        log.info("patch.cnefe.done", **res)

    elapsed = round(time.time() - t0, 1)
    log.info("bot_empresas.done", elapsed_s=elapsed, dry_run=dry_run)


if __name__ == "__main__":
    main()
