"""
bot_cvm.py — CVM Companhias Abertas → mr_cvm_listadas_v001
===========================================================

Fonte primária: dados.cvm.gov.br (gratuita, sem autenticação)

  cad_cia_aberta.csv  — cadastro de todas as companhias abertas registradas
                        na CVM; atualizado diariamente.

  DFP (Demonstrações Financeiras Padronizadas) — demonstrações financeiras
      anuais: Balanço Patrimonial Ativo (BPA) + Demonstração de Resultado (DRE).
      Contém: ativo_total, receita_bruta, resultado_bruto, lucro_liquido.
      URL base: https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/
      Arquivo: dfp_cia_aberta_{ano}.zip  (~8–12 MB por ano)

Critérios de inclusão (OR):
  mineral_setor   — SETOR_ATIV contém extração mineral, metalurgia/siderurgia,
                    petróleo/gás, petroquímicos, ou suas variantes de holding
  cnpj_jazidas    — CNPJ_CIA (cnpj_basico) encontrado em mr_jazidas_v001
  cnpj_empresas   — CNPJ_CIA (cnpj_basico) encontrado em mr_empresas_v001

Fluxo:
  1. --download        Baixa cad_cia_aberta.csv (skip se já existir)
  2. --index           Filtra + indexa em mr_cvm_listadas_v001
  3. --enrich-jazidas  Adiciona cvm_cd_cia + cvm_sit em mr_jazidas_v001
  4. --enrich-dfp      Baixa DFP e enriquece mr_cvm_listadas_v001 com dados financeiros
  5. --all             Executa 1 → 2 → 3 → 4 em sequência

Uso:
  python -m bots.bot_cvm --all
  python -m bots.bot_cvm --download
  python -m bots.bot_cvm --index
  python -m bots.bot_cvm --index --include-all   # sem filtro de setor (todas ativas em bolsa)
  python -m bots.bot_cvm --enrich-jazidas
  python -m bots.bot_cvm --enrich-dfp                  # últimos 2 anos
  python -m bots.bot_cvm --enrich-dfp --anos 2024 2025  # anos específicos
  python -m bots.bot_cvm --dry-run --enrich-dfp
  python -m bots.bot_cvm --force-download --all
"""
from __future__ import annotations

import csv
import io
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import zipfile
from datetime import date

import click
import httpx
from opensearchpy import OpenSearch, helpers

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_CVM      = "mr_cvm_listadas_v001"
INDEX_JAZIDAS  = "mr_jazidas_v001"
INDEX_EMPRESAS = "mr_empresas_v001"
BATCH_SIZE     = 500

CVM_CAD_URL  = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"
DFP_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS"
CACHE_DIR    = Path(settings.etl_data_dir) / "cvm"

# ── DFP account codes to extract ──────────────────────────────────────────────
# BPA_con (Balanço Patrimonial Ativo consolidado)
_BPA_CONTAS: dict[str, str] = {
    "1": "ativo_total",
}
# DRE_con (Demonstração do Resultado do Exercício consolidada)
_DRE_CONTAS: dict[str, str] = {
    "3.01": "receita_bruta",
    "3.03": "resultado_bruto",
    "3.11": "lucro_liquido",
}

_ESCALA_MULT: dict[str, float] = {
    "MIL":     1_000.0,
    "UNIDADE": 1.0,
    "BILHÃO":  1_000_000_000.0,
}

# Setores da CVM relevantes para o universo mineral
SETORES_MINERAIS = {
    "Extração Mineral",
    "Metalurgia e Siderurgia",
    "Petróleo e Gás",
    "Petroquímicos e Borracha",
    "Emp. Adm. Part. - Extração Mineral",
    "Emp. Adm. Part. - Metalurgia e Siderurgia",
    "Emp. Adm. Part. - Petróleo e Gás",
    "Emp. Adm. Part. - Petroquímicos e Borracha",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _only_digits(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\D", "", s)


def _cnpj_basico(cnpj_cia: str) -> str:
    """Extrai os 8 primeiros dígitos do CNPJ formatado (XX.XXX.XXX/YYYY-ZZ)."""
    digits = _only_digits(cnpj_cia)
    return digits[:8] if len(digits) >= 8 else ""


def _parse_date(s: str | None) -> str | None:
    if not s or s.strip() == "":
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


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
    log.info("opensearch.ok", version=info["version"]["number"], cluster=info["cluster_name"])
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Index mapping
# ─────────────────────────────────────────────────────────────────────────────

INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "pt_folding": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            # Identificadores
            "cnpj_cia":      {"type": "keyword"},
            "cnpj_basico":   {"type": "keyword"},
            "cd_cvm":        {"type": "keyword"},
            # Razão social / nome
            "denom_social": {
                "type": "text",
                "analyzer": "pt_folding",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "denom_comerc": {
                "type": "text",
                "analyzer": "pt_folding",
                "fields": {"keyword": {"type": "keyword"}},
            },
            # Classificações
            "setor_ativ":          {"type": "keyword"},
            "tp_merc":             {"type": "keyword"},
            "categ_reg":           {"type": "keyword"},
            "sit":                 {"type": "keyword"},
            "sit_emissor":         {"type": "keyword"},
            "controle_acionario":  {"type": "keyword"},
            # Datas
            "dt_reg":     {"type": "date", "format": "yyyy-MM-dd"},
            "dt_const":   {"type": "date", "format": "yyyy-MM-dd"},
            "dt_cancel":  {"type": "date", "format": "yyyy-MM-dd"},
            "dt_ini_sit": {"type": "date", "format": "yyyy-MM-dd"},
            "motivo_cancel": {"type": "keyword"},
            # Localização
            "uf":  {"type": "keyword"},
            "mun": {"type": "keyword"},
            "pais": {"type": "keyword"},
            # Contato
            "email":      {"type": "keyword"},
            "email_resp": {"type": "keyword"},
            "resp":       {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            # Auditor
            "cnpj_auditor": {"type": "keyword"},
            "auditor":      {"type": "keyword"},
            # Metadados ETL
            "criterio_inclusao": {"type": "keyword"},
            "indexado_em":       {"type": "date"},
        }
    },
}


def ensure_index(client: OpenSearch) -> None:
    if client.indices.exists(index=INDEX_CVM):
        log.info("index.existe", index=INDEX_CVM)
        return
    client.indices.create(index=INDEX_CVM, body=INDEX_MAPPING)
    log.info("index.criado", index=INDEX_CVM)


# ─────────────────────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────────────────────

def download_cad(dest: Path, force: bool) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        size_kb = round(dest.stat().st_size / 1024, 0)
        log.info("cvm.download.skip", path=str(dest), size_kb=size_kb)
        return dest

    log.info("cvm.download.start", url=CVM_CAD_URL)
    t0 = time.time()
    with httpx.stream(
        "GET",
        CVM_CAD_URL,
        headers={"User-Agent": settings.anm_user_agent},
        timeout=120,
        follow_redirects=True,
    ) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes(chunk_size=65536):
                fh.write(chunk)
        tmp.rename(dest)

    elapsed = round(time.time() - t0, 1)
    size_kb = round(dest.stat().st_size / 1024, 0)
    log.info("cvm.download.ok", size_kb=size_kb, elapsed_s=elapsed)
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# Parse + filter
# ─────────────────────────────────────────────────────────────────────────────

def _load_cnpj_basicos_from_index(client: OpenSearch, index: str, field: str) -> set[str]:
    """
    Coleta os valores únicos de um campo keyword via terms aggregation.
    Usa scroll para cobrir > 10k valores distintos.
    """
    basicos: set[str] = set()
    try:
        resp = client.search(
            index=index,
            body={
                "size": 0,
                "aggs": {
                    "cnpjs": {
                        "terms": {"field": field, "size": 50_000}
                    }
                },
            },
            request_timeout=60,
        )
        for bucket in resp["aggregations"]["cnpjs"]["buckets"]:
            v = (bucket["key"] or "").strip()
            if v:
                basicos.add(v[:8])
    except Exception as e:
        log.warning("cnpj_basicos.erro", index=index, field=field, error=str(e))
    return basicos


def _iter_companies(
    csv_path: Path,
    cnpjs_jazidas: set[str],
    cnpjs_empresas: set[str],
    include_all: bool,
) -> Iterator[dict[str, Any]]:
    """
    Itera sobre cad_cia_aberta.csv e gera documentos para indexação.
    Critério de inclusão: setor mineral OU cnpj em jazidas OU cnpj em empresas.
    Se include_all=True: indexa TODAS as companhias sem filtro.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    count = 0

    with csv_path.open("rb") as raw:
        text = raw.read().decode("latin-1")

    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    for row in reader:
        cnpj_full = (row.get("CNPJ_CIA") or "").strip()
        basico = _cnpj_basico(cnpj_full)

        setor = (row.get("SETOR_ATIV") or "").strip()

        criterios: list[str] = []

        if include_all:
            criterios.append("include_all")
        else:
            if setor in SETORES_MINERAIS:
                criterios.append("mineral_setor")
            if basico and basico in cnpjs_jazidas:
                criterios.append("cnpj_jazidas")
            if basico and basico in cnpjs_empresas:
                criterios.append("cnpj_empresas")

        if not criterios:
            continue

        count += 1
        doc_id = _only_digits(cnpj_full) or cnpj_full  # 14 dígitos como _id

        yield {
            "_op_type": "index",
            "_index": INDEX_CVM,
            "_id": doc_id,
            "_source": {
                "cnpj_cia":           cnpj_full,
                "cnpj_basico":        basico or None,
                "cd_cvm":             (row.get("CD_CVM") or "").strip() or None,
                "denom_social":       (row.get("DENOM_SOCIAL") or "").strip() or None,
                "denom_comerc":       (row.get("DENOM_COMERC") or "").strip() or None,
                "setor_ativ":         setor or None,
                "tp_merc":            (row.get("TP_MERC") or "").strip() or None,
                "categ_reg":          (row.get("CATEG_REG") or "").strip() or None,
                "sit":                (row.get("SIT") or "").strip() or None,
                "sit_emissor":        (row.get("SIT_EMISSOR") or "").strip() or None,
                "controle_acionario": (row.get("CONTROLE_ACIONARIO") or "").strip() or None,
                "dt_reg":             _parse_date(row.get("DT_REG")),
                "dt_const":           _parse_date(row.get("DT_CONST")),
                "dt_cancel":          _parse_date(row.get("DT_CANCEL")),
                "dt_ini_sit":         _parse_date(row.get("DT_INI_SIT")),
                "motivo_cancel":      (row.get("MOTIVO_CANCEL") or "").strip() or None,
                "uf":                 (row.get("UF") or "").strip() or None,
                "mun":                (row.get("MUN") or "").strip() or None,
                "pais":               (row.get("PAIS") or "").strip() or None,
                "email":              (row.get("EMAIL") or "").strip().lower() or None,
                "email_resp":         (row.get("EMAIL_RESP") or "").strip().lower() or None,
                "resp":               (row.get("RESP") or "").strip() or None,
                "cnpj_auditor":       (row.get("CNPJ_AUDITOR") or "").strip() or None,
                "auditor":            (row.get("AUDITOR") or "").strip() or None,
                "criterio_inclusao":  criterios,
                "indexado_em":        now_iso,
            },
        }

    log.info("iter_companies.concluido", total_incluidos=count)


# ─────────────────────────────────────────────────────────────────────────────
# Index
# ─────────────────────────────────────────────────────────────────────────────

def cmd_index(
    client: OpenSearch,
    csv_path: Path,
    dry_run: bool,
    include_all: bool,
) -> None:
    log.info("index.inicio", csv=str(csv_path), dry_run=dry_run)

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV não encontrado: {csv_path}. Execute --download primeiro.")

    # Coleta cnpj_basico de ambos os índices para cross-reference
    log.info("cross_ref.carregando_jazidas")
    cnpjs_jazidas = _load_cnpj_basicos_from_index(client, INDEX_JAZIDAS, "titular.cnpj_basico")
    log.info("cross_ref.jazidas", total=len(cnpjs_jazidas))

    log.info("cross_ref.carregando_empresas")
    cnpjs_empresas = _load_cnpj_basicos_from_index(client, INDEX_EMPRESAS, "cnpj_basico")
    log.info("cross_ref.empresas", total=len(cnpjs_empresas))

    ensure_index(client)

    gen = _iter_companies(csv_path, cnpjs_jazidas, cnpjs_empresas, include_all)

    if dry_run:
        total = 0
        for doc in gen:
            total += 1
            if total <= 5:
                s = doc["_source"]
                log.info(
                    "dry_run.sample",
                    cnpj=s.get("cnpj_cia"),
                    nome=s.get("denom_social", "")[:50],
                    setor=s.get("setor_ativ"),
                    criterios=s.get("criterio_inclusao"),
                )
        log.info("dry_run.total", docs=total)
        return

    t0 = time.monotonic()
    ok, errs = helpers.bulk(
        client,
        gen,
        chunk_size=BATCH_SIZE,
        raise_on_error=False,
        request_timeout=120,
    )
    elapsed = round(time.monotonic() - t0, 1)
    log.info("index.concluido", indexados=ok, erros=len(errs) if isinstance(errs, list) else errs, elapsed_s=elapsed)
    if errs:
        for e in (errs[:3] if isinstance(errs, list) else []):
            log.warning("index.erro_bulk", detalhe=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Enrich jazidas — adiciona referência CVM em mr_jazidas_v001
# ─────────────────────────────────────────────────────────────────────────────

def cmd_enrich_jazidas(client: OpenSearch, dry_run: bool) -> None:
    """
    Para cada documento em mr_cvm_listadas_v001, faz update_by_query em
    mr_jazidas_v001 onde titular.cnpj_basico == cnpj_basico da CVM,
    adicionando os campos:
      titular.cvm_cd_cia   — código CVM da companhia
      titular.cvm_sit      — situação (ATIVO / CANCELADA)
      titular.cvm_tp_merc  — tipo de mercado (BOLSA, BALCÃO ORGANIZADO, …)
    """
    log.info("enrich_jazidas.inicio")

    # Scroll por todos os docs do índice CVM
    resp = client.search(
        index=INDEX_CVM,
        body={
            "size": 1000,
            "_source": ["cnpj_basico", "cd_cvm", "sit", "tp_merc", "denom_social"],
            "query": {
                "bool": {
                    "must": [
                        {"exists": {"field": "cnpj_basico"}},
                        {"term": {"sit": "ATIVO"}},
                    ]
                }
            },
        },
        scroll="5m",
        request_timeout=60,
    )
    scroll_id = resp["_scroll_id"]
    stats = {"processadas": 0, "atualizadas": 0, "erros": 0}

    try:
        while True:
            hits = resp["hits"]["hits"]
            if not hits:
                break

            for hit in hits:
                src = hit["_source"]
                basico = (src.get("cnpj_basico") or "").strip()
                cd_cvm = (src.get("cd_cvm") or "").strip()
                sit    = (src.get("sit") or "").strip()
                tp_merc = (src.get("tp_merc") or "").strip()

                if not basico or not cd_cvm:
                    continue

                stats["processadas"] += 1

                if dry_run:
                    log.info(
                        "enrich_jazidas.dry_run",
                        cnpj_basico=basico,
                        cd_cvm=cd_cvm,
                        nome=src.get("denom_social", "")[:40],
                    )
                    continue

                ubq_body = {
                    "script": {
                        "source": (
                            "if (ctx._source.titular == null) { ctx._source.titular = new HashMap(); } "
                            "ctx._source.titular.cvm_cd_cia  = params.cd_cvm; "
                            "ctx._source.titular.cvm_sit     = params.sit; "
                            "ctx._source.titular.cvm_tp_merc = params.tp_merc;"
                        ),
                        "lang": "painless",
                        "params": {
                            "cd_cvm":  cd_cvm,
                            "sit":     sit,
                            "tp_merc": tp_merc,
                        },
                    },
                    "query": {
                        "term": {"titular.cnpj_basico": basico}
                    },
                }
                try:
                    r = client.update_by_query(
                        index=INDEX_JAZIDAS,
                        body=ubq_body,
                        conflicts="proceed",
                        wait_for_completion=True,
                        request_timeout=120,
                    )
                    updated = r.get("updated", 0)
                    if updated:
                        stats["atualizadas"] += updated
                except Exception as e:
                    stats["erros"] += 1
                    log.warning("enrich_jazidas.ubq_erro", cnpj_basico=basico, error=str(e))

            resp = client.scroll(scroll_id=scroll_id, scroll="5m")
            scroll_id = resp["_scroll_id"]
    finally:
        try:
            client.clear_scroll(scroll_id=scroll_id)
        except Exception:
            pass

    log.info("enrich_jazidas.concluido", **stats)


# ─────────────────────────────────────────────────────────────────────────────
# DFP enrichment — dados financeiros anuais
# ─────────────────────────────────────────────────────────────────────────────

def _download_dfp_zip(ano: int, force: bool) -> Path | None:
    """Baixa dfp_cia_aberta_{ano}.zip; retorna None em caso de erro HTTP."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"dfp_cia_aberta_{ano}.zip"
    if dest.exists() and not force:
        log.info("dfp.download.skip", ano=ano, path=str(dest))
        return dest

    url = f"{DFP_BASE_URL}/dfp_cia_aberta_{ano}.zip"
    log.info("dfp.download.start", ano=ano, url=url)
    t0 = time.time()
    try:
        with httpx.stream(
            "GET", url,
            headers={"User-Agent": settings.anm_user_agent},
            timeout=300,
            follow_redirects=True,
        ) as r:
            if r.status_code == 404:
                log.warning("dfp.download.404", ano=ano)
                return None
            r.raise_for_status()
            tmp = dest.with_suffix(".tmp")
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes(chunk_size=65536):
                    fh.write(chunk)
            tmp.rename(dest)
    except Exception as e:
        log.error("dfp.download.erro", ano=ano, error=str(e))
        return None

    elapsed = round(time.time() - t0, 1)
    size_mb = round(dest.stat().st_size / 1e6, 1)
    log.info("dfp.download.ok", ano=ano, size_mb=size_mb, elapsed_s=elapsed)
    return dest


def _scale(value_str: str, escala: str) -> float | None:
    """Converte valor string + escala para BRL (float)."""
    try:
        v = float(value_str)
    except (ValueError, TypeError):
        return None
    mult = _ESCALA_MULT.get(escala.upper(), 1.0)
    return v * mult


def _extract_metricas_zip(
    zip_path: Path,
    cnpjs_alvo: set[str],
) -> dict[str, dict[str, Any]]:
    """
    Lê BPA_con + DRE_con (com fallback para _ind) dentro do ZIP do DFP.

    Retorna dict: cnpj_cia → {
        ativo_total, receita_bruta, resultado_bruto, lucro_liquido,
        dt_fim_exerc, consolidado, total_acoes, acoes_tesouraria
    }
    """
    resultado: dict[str, dict[str, Any]] = {}

    try:
        z = zipfile.ZipFile(zip_path)
    except Exception as e:
        log.error("dfp.zip.abrir", path=str(zip_path), error=str(e))
        return resultado

    # ── 1. BPA (Ativo Total) ──────────────────────────────────────────────────
    for suffix in ("_con", "_ind"):
        fname = next((n for n in z.namelist() if n.endswith(f"_BPA{suffix}_{zip_path.stem.split('_')[-1]}.csv")), None)
        if not fname:
            continue
        log.info("dfp.bpa.lendo", arquivo=fname)
        with z.open(fname) as f:
            text = io.TextIOWrapper(f, encoding="latin-1")
            reader = csv.DictReader(text, delimiter=";")
            # cnpj → {versao, row}
            melhores: dict[str, tuple[int, dict]] = {}
            for row in reader:
                cnpj = row.get("CNPJ_CIA", "").strip()
                if cnpj not in cnpjs_alvo:
                    continue
                if row.get("ORDEM_EXERC", "").strip() != "ÚLTIMO":
                    continue
                cd = row.get("CD_CONTA", "").strip()
                if cd not in _BPA_CONTAS:
                    continue
                versao = int(row.get("VERSAO", "0") or "0")
                prev = melhores.get(cnpj)
                if prev is None or versao > prev[0]:
                    melhores[cnpj] = (versao, row)

        for cnpj, (_, row) in melhores.items():
            campo = _BPA_CONTAS[row["CD_CONTA"].strip()]
            val = _scale(row.get("VL_CONTA", ""), row.get("ESCALA_MOEDA", "MIL"))
            if cnpj not in resultado:
                resultado[cnpj] = {}
            resultado[cnpj][campo] = val
            resultado[cnpj]["dt_fim_exerc"] = row.get("DT_FIM_EXERC", "").strip() or None
            resultado[cnpj]["consolidado"] = suffix == "_con"

        if melhores:
            break  # _con preferred; skip _ind if we got results

    # ── 2. DRE (Receita, Resultado Bruto, Lucro Líquido) ────────────────────
    for suffix in ("_con", "_ind"):
        fname = next((n for n in z.namelist() if n.endswith(f"_DRE{suffix}_{zip_path.stem.split('_')[-1]}.csv")), None)
        if not fname:
            continue
        log.info("dfp.dre.lendo", arquivo=fname)
        with z.open(fname) as f:
            text = io.TextIOWrapper(f, encoding="latin-1")
            reader = csv.DictReader(text, delimiter=";")
            # (cnpj, cd_conta) → {versao, row}
            melhores: dict[tuple, tuple[int, dict]] = {}
            for row in reader:
                cnpj = row.get("CNPJ_CIA", "").strip()
                if cnpj not in cnpjs_alvo:
                    continue
                if row.get("ORDEM_EXERC", "").strip() != "ÚLTIMO":
                    continue
                cd = row.get("CD_CONTA", "").strip()
                if cd not in _DRE_CONTAS:
                    continue
                versao = int(row.get("VERSAO", "0") or "0")
                key = (cnpj, cd)
                prev = melhores.get(key)
                if prev is None or versao > prev[0]:
                    melhores[key] = (versao, row)

        dre_cnpjs: set[str] = set()
        for (cnpj, cd), (_, row) in melhores.items():
            campo = _DRE_CONTAS[cd]
            val = _scale(row.get("VL_CONTA", ""), row.get("ESCALA_MOEDA", "MIL"))
            if cnpj not in resultado:
                resultado[cnpj] = {}
            resultado[cnpj][campo] = val
            resultado[cnpj].setdefault("dt_fim_exerc", row.get("DT_FIM_EXERC", "").strip() or None)
            resultado[cnpj].setdefault("consolidado", suffix == "_con")
            dre_cnpjs.add(cnpj)

        if dre_cnpjs:
            break  # _con preferred

    # ── 3. Composição do capital (ações) ─────────────────────────────────────
    fname_cap = next((n for n in z.namelist() if "composicao_capital" in n), None)
    if fname_cap:
        log.info("dfp.capital.lendo", arquivo=fname_cap)
        with z.open(fname_cap) as f:
            text = io.TextIOWrapper(f, encoding="latin-1")
            reader = csv.DictReader(text, delimiter=";")
            # versao mais recente por cnpj
            melhores_cap: dict[str, tuple[int, dict]] = {}
            for row in reader:
                cnpj = row.get("CNPJ_CIA", "").strip()
                if cnpj not in cnpjs_alvo:
                    continue
                versao = int(row.get("VERSAO", "0") or "0")
                prev = melhores_cap.get(cnpj)
                if prev is None or versao > prev[0]:
                    melhores_cap[cnpj] = (versao, row)

        for cnpj, (_, row) in melhores_cap.items():
            if cnpj not in resultado:
                resultado[cnpj] = {}
            try:
                # CVM does not provide a consistent ESCALA column for share counts.
                # Store the raw integer as published; callers must treat it as opaque.
                total   = int(row.get("QT_ACAO_TOTAL_CAP_INTEGR") or 0)
                tesouro = int(row.get("QT_ACAO_TOTAL_TESOURO") or 0)
                if total:
                    resultado[cnpj]["total_acoes_raw"] = total
                if tesouro:
                    resultado[cnpj]["acoes_tesouraria_raw"] = tesouro
            except (ValueError, TypeError):
                pass

    z.close()
    log.info("dfp.metricas.extraidas", empresas=len(resultado))
    return resultado


def cmd_enrich_dfp(
    client: OpenSearch,
    anos: list[int],
    force_download: bool,
    dry_run: bool,
) -> None:
    """
    Enriquece mr_cvm_listadas_v001 com dados financeiros do DFP.

    Estratégia multi-ano: o ano mais recente com dados para cada empresa prevalece.
    Campos adicionados ao documento (em `financeiro`):
        ativo_total, receita_bruta, resultado_bruto, lucro_liquido,
        total_acoes, acoes_tesouraria, dt_fim_exerc, consolidado, ano_dfp
    """
    log.info("enrich_dfp.inicio", anos=anos, dry_run=dry_run)

    # ── Coleta CNPJs do índice CVM ─────────────────────────────────────────
    resp = client.search(
        index=INDEX_CVM,
        body={"size": 5000, "_source": ["cnpj_cia"], "query": {"match_all": {}}},
        request_timeout=30,
    )
    cnpjs_alvo: set[str] = {
        h["_source"]["cnpj_cia"]
        for h in resp["hits"]["hits"]
        if h["_source"].get("cnpj_cia")
    }
    log.info("enrich_dfp.cnpjs_alvo", total=len(cnpjs_alvo))

    # ── Extrai métricas dos ZIPs (mais recente vence) ──────────────────────
    metricas_por_cnpj: dict[str, dict[str, Any]] = {}
    for ano in sorted(anos):  # processar em ordem crescente; mais recente sobrescreve
        zip_path = _download_dfp_zip(ano, force=force_download)
        if not zip_path:
            continue
        metricas_ano = _extract_metricas_zip(zip_path, cnpjs_alvo)
        for cnpj, dados in metricas_ano.items():
            dados["ano_dfp"] = ano
            metricas_por_cnpj[cnpj] = dados  # mais recente vence

    total_com_dados = len(metricas_por_cnpj)
    log.info("enrich_dfp.metricas_totais", empresas=total_com_dados)

    if dry_run:
        for cnpj, dados in list(metricas_por_cnpj.items())[:8]:
            log.info("enrich_dfp.dry_run", cnpj=cnpj, dados={
                k: (f"R$ {v/1e9:.2f}B" if isinstance(v, float) and abs(v) > 1e6 else v)
                for k, v in dados.items()
            })
        return

    # ── Atualiza os docs no OpenSearch ─────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()

    def _gen_updates() -> Iterator[dict]:
        for cnpj, dados in metricas_por_cnpj.items():
            cnpj_id = _only_digits(cnpj)
            if not cnpj_id:
                continue
            yield {
                "_op_type": "update",
                "_index": INDEX_CVM,
                "_id": cnpj_id,
                "doc": {
                    "financeiro": {**dados, "atualizado_em": now_iso},
                },
                "doc_as_upsert": False,
                "retry_on_conflict": 3,
            }

    t0 = time.monotonic()
    ok, errs = helpers.bulk(
        client,
        _gen_updates(),
        chunk_size=BATCH_SIZE,
        raise_on_error=False,
        request_timeout=120,
    )
    elapsed = round(time.monotonic() - t0, 1)
    log.info(
        "enrich_dfp.concluido",
        atualizados=ok,
        erros=len(errs) if isinstance(errs, list) else errs,
        elapsed_s=elapsed,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--download",       is_flag=True, help="Baixa o CSV cadastral da CVM.")
@click.option("--index",          is_flag=True, help="Filtra e indexa em mr_cvm_listadas_v001.")
@click.option("--enrich-jazidas", is_flag=True, help="Adiciona referência CVM em mr_jazidas_v001.")
@click.option("--enrich-dfp",     is_flag=True,
              help="Baixa DFP e enriquece mr_cvm_listadas_v001 com ativo_total, receita_bruta, lucro_liquido.")
@click.option("--all",   "run_all", is_flag=True,
              help="Executa download + index + enrich-jazidas + enrich-dfp.")
@click.option("--force-download",  is_flag=True, help="Re-baixa mesmo se o arquivo já existir.")
@click.option("--include-all",     is_flag=True,
              help="Indexa TODAS as companhias abertas (sem filtro por setor mineral).")
@click.option("--anos", multiple=True, type=int,
              help="Anos DFP a processar (default: ano atual e anterior). Ex: --anos 2024 --anos 2025")
@click.option("--dry-run",         is_flag=True, help="Exibe o que seria feito, sem gravar.")
def main(
    download: bool,
    index: bool,
    enrich_jazidas: bool,
    enrich_dfp: bool,
    run_all: bool,
    force_download: bool,
    include_all: bool,
    anos: tuple[int, ...],
    dry_run: bool,
) -> None:
    """
    Bot CVM — Companhias Abertas minerais/listadas → mr_cvm_listadas_v001.

    Fluxo padrão:
      python -m bots.bot_cvm --all

    Apenas dados financeiros (DFP):
      python -m bots.bot_cvm --enrich-dfp
      python -m bots.bot_cvm --enrich-dfp --anos 2024 --anos 2025
      python -m bots.bot_cvm --enrich-dfp --dry-run
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = CACHE_DIR / "cad_cia_aberta.csv"

    do_download    = download     or run_all
    do_index       = index        or run_all
    do_enrich_jaz  = enrich_jazidas or run_all
    do_enrich_dfp  = enrich_dfp   or run_all

    if not (do_download or do_index or do_enrich_jaz or do_enrich_dfp):
        click.echo(
            "Nenhuma opção selecionada. "
            "Use --all, --download, --index, --enrich-jazidas ou --enrich-dfp."
        )
        return

    # Anos default: ano atual e anterior (para empresas que ainda não fecharam o balanço)
    anos_dfp: list[int] = list(anos) if anos else [date.today().year - 1, date.today().year]

    client = get_os_client()

    if do_download:
        click.echo("=== Download CVM cadastro ===")
        download_cad(csv_path, force=force_download)

    if do_index:
        click.echo("=== Indexação mr_cvm_listadas_v001 ===")
        cmd_index(client, csv_path, dry_run=dry_run, include_all=include_all)

    if do_enrich_jaz:
        click.echo("=== Enriquecimento mr_jazidas_v001 com dados CVM ===")
        cmd_enrich_jazidas(client, dry_run=dry_run)

    if do_enrich_dfp:
        click.echo(f"=== Enriquecimento DFP (anos: {anos_dfp}) ===")
        cmd_enrich_dfp(client, anos=anos_dfp, force_download=force_download, dry_run=dry_run)

    click.echo("Concluído.")


if __name__ == "__main__":
    main()
