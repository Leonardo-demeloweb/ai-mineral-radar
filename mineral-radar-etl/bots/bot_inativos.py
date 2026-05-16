"""
bot_inativos.py — Enriquecimento de processos inativos em mr_jazidas_v001

Problema:
  Os registros inativos (650k docs) ficam com campos críticos nulos após a
  indexação via bot_anm_direto.py:
    - titular.nome: 0%  (shapefile inativo não inclui coluna NOME)
    - substancias_desc:  pode estar vazia para processos antigos sem SUBS
    - categorias_estrategicas: 0%  (classificação só rodou sobre ativos)

Solução:
  Extrai ProcessoPessoa.txt, ProcessoSubstancia.txt e tabelas de lookup do
  microdados-scm.zip (disponível em /tmp/mineralradar_data/scm/) e faz
  bulk update parcial nos inativos via OpenSearch Scroll API.
  Apenas campos nulos/vazios são preenchidos — enriquecimentos anteriores
  (CFEM, CPRM, municípios) não são tocados.

Uso:
  python -m bots.bot_inativos                     # enriquece tudo
  python -m bots.bot_inativos --skip-pessoas      # pula titular.nome
  python -m bots.bot_inativos --skip-substancias  # pula substancias_desc
  python -m bots.bot_inativos --skip-classif      # pula categorias_estrategicas
  python -m bots.bot_inativos --dry-run           # só conta, não grava
  python -m bots.bot_inativos --also-ativos       # aplica também nos ativos
"""
from __future__ import annotations

import time
import zipfile
from io import StringIO
from pathlib import Path

import click
import polars as pl
from opensearchpy import OpenSearch, helpers

from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_JAZIDAS  = "mr_jazidas_v001"


def _add_dot(processo: str) -> str:
    """
    Converte formato sem ponto (shapefile inativo) → formato com ponto (microdados SCM).
    Ex: '300152/2013' → '300.152/2013'
        '803977/1975' → '803.977/1975'
        '1085/1941'   → '1.085/1941'
        '860.037/1980' → inalterado (já tem ponto)
    """
    if not processo or "." in processo:
        return processo
    parts = processo.split("/")
    if len(parts) != 2:
        return processo
    num, year = parts
    if len(num) > 3:
        return f"{num[:-3]}.{num[-3:]}/{year}"
    return processo
BATCH_SIZE     = 500
SCROLL_SIZE    = 500
SCROLL_TIMEOUT = "5m"

# ─── Tabelas de lookup de classificação estratégica ───────────────────────────
# Mesma lógica da classificação rodada sobre os ativos, agora centralizada aqui.

MAPA: dict[str, list[str]] = {
    # Metais preciosos
    "ouro":       ["metais_preciosos"],
    "prata":      ["metais_preciosos"],
    "platina":    ["metais_preciosos"],
    "paladio":    ["metais_preciosos"],
    "rhodio":     ["metais_preciosos"],
    # Minerais de bateria / energia
    "litio":      ["minerais_bateria", "transicao_energetica"],
    "niobio":     ["minerais_bateria", "transicao_energetica"],
    "cobalto":    ["minerais_bateria", "transicao_energetica"],
    "manganes":   ["minerais_bateria"],
    "grafita":    ["minerais_bateria", "transicao_energetica"],
    "grafite":    ["minerais_bateria", "transicao_energetica"],
    "vanadio":    ["minerais_bateria", "transicao_energetica"],
    # Terras raras / tecnologia
    "terras raras": ["terras_raras", "tecnologia_critica"],
    "terra rara":   ["terras_raras", "tecnologia_critica"],
    "cerio":      ["terras_raras"],
    "lantanio":   ["terras_raras"],
    "neodimio":   ["terras_raras", "tecnologia_critica"],
    "indio":      ["tecnologia_critica"],
    "germanio":   ["tecnologia_critica"],
    "galio":      ["tecnologia_critica"],
    "berilio":    ["tecnologia_critica"],
    "tantalo":    ["tecnologia_critica"],
    "wolframio":  ["tecnologia_critica"],
    "tungsten":   ["tecnologia_critica"],
    "molibdenio": ["tecnologia_critica"],
    "rhenio":     ["tecnologia_critica"],
    "bismuto":    ["tecnologia_critica"],
    "teluro":     ["tecnologia_critica"],
    # Metais de base / siderurgia
    "ferro":      ["metais_base"],
    "aco":        ["metais_base"],
    "cobre":      ["metais_base", "transicao_energetica"],
    "niquel":     ["metais_base", "transicao_energetica"],
    "zinco":      ["metais_base"],
    "chumbo":     ["metais_base"],
    "aluminio":   ["metais_base"],
    "bauxita":    ["metais_base"],
    "titanio":    ["metais_base", "tecnologia_critica"],
    "cromo":      ["metais_base"],
    "estanho":    ["metais_base"],
    # Fertilizantes / agronegócio
    "potassio":   ["fertilizantes"],
    "potassa":    ["fertilizantes"],
    "fosfato":    ["fertilizantes"],
    "fosforo":    ["fertilizantes"],
    "calcario":   ["fertilizantes"],
    "gesso":      ["fertilizantes"],
    "enxofre":    ["fertilizantes"],
    # Construção / industrial
    "areia":      ["minerios_industriais"],
    "brita":      ["minerios_industriais"],
    "argila":     ["minerios_industriais"],
    "granito":    ["minerios_industriais"],
    "marmore":    ["minerios_industriais"],
    "quartzo":    ["minerios_industriais"],
    "caulim":     ["minerios_industriais"],
    "talco":      ["minerios_industriais"],
    "amianto":    ["minerios_industriais"],
    "asbesto":    ["minerios_industriais"],
    "feldspato":  ["minerios_industriais"],
    "diatomito":  ["minerios_industriais"],
    "vermiculita": ["minerios_industriais"],
    "bentonita":  ["minerios_industriais"],
    # Energia fóssil / nuclear
    "uranio":     ["minerio_nuclear"],
    "torio":      ["minerio_nuclear"],
    "carvao":     ["energia_fosssil"],
    "turfa":      ["energia_fosssil"],
}

PRIORIDADE: dict[str, int] = {
    "minerais_bateria":   100,
    "terras_raras":        95,
    "tecnologia_critica":  90,
    "transicao_energetica": 85,
    "metais_preciosos":    80,
    "minerio_nuclear":     70,
    "metais_base":         60,
    "fertilizantes":       50,
    "minerios_industriais": 30,
    "energia_fosssil":     20,
}


def classificar(substancias_desc: list[str]) -> tuple[list[str], int | None]:
    """Classifica substâncias em categorias estratégicas e retorna prioridade."""
    cats: set[str] = set()
    for desc in substancias_desc:
        norm = (desc or "").lower()
        for keyword, categorias in MAPA.items():
            if keyword in norm:
                cats.update(categorias)
    if not cats:
        return [], None
    prio = max(PRIORIDADE.get(c, 0) for c in cats)
    return sorted(cats), prio


# ─────────────────────────────────────────────────────────────────────────────
# Leitura do microdados ZIP
# ─────────────────────────────────────────────────────────────────────────────

def _read_txt(zf: zipfile.ZipFile, fname: str) -> pl.DataFrame:
    """Lê um .txt do ZIP com separador ';' e retorna DataFrame."""
    with zf.open(fname) as f:
        content = f.read().decode("latin1", errors="replace")
    return pl.read_csv(
        StringIO(content),
        separator=";",
        infer_schema_length=0,
        ignore_errors=True,
        truncate_ragged_lines=True,
        null_values=["", "NULL", "null"],
    )


def build_pessoa_lookup(zip_path: Path) -> dict[str, str]:
    """
    Lê Pessoa.txt e ProcessoPessoa.txt do microdados.
    Retorna {processo: nome_titular} para todos os processos com IDTipoRelacao=1
    (Titular/Requerente).
    """
    log.info("inativos.pessoas.loading")
    pessoas: dict[str, str] = {}   # IDPessoa → NMPessoa

    with zipfile.ZipFile(zip_path) as zf:
        # 1. Carga de Pessoa.txt → dicionário id → nome
        df_p = _read_txt(zf, "microdados-scm/Pessoa.txt")
        for row in df_p.iter_rows(named=True):
            pid  = str(row.get("IDPessoa") or "").strip()
            nome = str(row.get("NMPessoa") or "").strip()
            if pid and nome and nome.lower() not in ("", "nan", "none"):
                pessoas[pid] = nome

        log.info("inativos.pessoas.lookup", total=len(pessoas))

        # 2. ProcessoPessoa.txt — filtra IDTipoRelacao = 1 (Titular/Requerente)
        df_pp = _read_txt(zf, "microdados-scm/ProcessoPessoa.txt")
        resultado: dict[str, str] = {}
        skipped = 0

        for row in df_pp.iter_rows(named=True):
            tipo = str(row.get("IDTipoRelacao") or "").strip()
            if tipo != "1":        # apenas Titular/Requerente
                continue
            processo = str(row.get("DSProcesso") or "").strip()
            pid      = str(row.get("IDPessoa")   or "").strip()
            if not processo or not pid:
                skipped += 1
                continue
            nome = pessoas.get(pid)
            if nome and processo not in resultado:
                resultado[processo] = nome
                # Indexa também sem ponto (formato do shapefile inativo)
                sem_ponto = processo.replace(".", "")
                if sem_ponto != processo:
                    resultado.setdefault(sem_ponto, nome)

        log.info("inativos.pessoas.done",
                 processos_com_titular=len(resultado), skipped=skipped)
        return resultado


def build_substancias_lookup(zip_path: Path) -> dict[str, list[str]]:
    """
    Lê Substancia.txt e ProcessoSubstancia.txt do microdados.
    Retorna {processo: [NMSubstancia, ...]} com lista única de substâncias.
    """
    log.info("inativos.substancias.loading")

    with zipfile.ZipFile(zip_path) as zf:
        # 1. Substancia.txt → id → nome
        df_s = _read_txt(zf, "microdados-scm/Substancia.txt")
        subs_lookup: dict[str, str] = {}
        for row in df_s.iter_rows(named=True):
            sid  = str(row.get("IDSubstancia") or "").strip()
            nome = str(row.get("NMSubstancia") or "").strip()
            if sid and nome:
                subs_lookup[sid] = nome

        log.info("inativos.substancias.lookup", total=len(subs_lookup))

        # 2. ProcessoSubstancia.txt → processo → [nomes]
        df_ps = _read_txt(zf, "microdados-scm/ProcessoSubstancia.txt")
        resultado: dict[str, list[str]] = {}

        for row in df_ps.iter_rows(named=True):
            processo = str(row.get("DSProcesso") or "").strip()
            sid      = str(row.get("IDSubstancia") or "").strip()
            if not processo or not sid:
                continue
            nome = subs_lookup.get(sid)
            if nome:
                lst = resultado.setdefault(processo, [])
                if nome not in lst:
                    lst.append(nome)
                # Indexa também sem ponto (formato do shapefile inativo)
                sem_ponto = processo.replace(".", "")
                if sem_ponto != processo:
                    lst2 = resultado.setdefault(sem_ponto, [])
                    if nome not in lst2:
                        lst2.append(nome)

        log.info("inativos.substancias.done", processos=len(resultado))
        return resultado


# ─────────────────────────────────────────────────────────────────────────────
# OpenSearch
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
    return OpenSearch(**kwargs)


def enrich_inativos(
    client: OpenSearch,
    map_pessoas: dict[str, str],
    map_substancias: dict[str, list[str]],
    skip_pessoas: bool,
    skip_substancias: bool,
    skip_classif: bool,
    dry_run: bool,
    also_ativos: bool,
    batch_size: int,
) -> None:
    """
    Scrolleia documentos inativos (ou todos se --also-ativos) e aplica:
      - titular.nome via map_pessoas (apenas se nulo)
      - substancias_desc via map_substancias (apenas se lista vazia/nula)
      - categorias_estrategicas + prioridade_estrategica via classificar()
    """
    filtro_ativo = [] if also_ativos else [{"term": {"ativo": False}}]

    query = {
        "size": SCROLL_SIZE,
        "track_total_hits": True,
        "query": {"bool": {"filter": filtro_ativo}} if filtro_ativo else {"match_all": {}},
        "_source": ["numero_processo", "titular", "substancias_desc",
                    "categorias_estrategicas", "ativo"],
    }

    resp = client.search(
        index=INDEX_JAZIDAS,
        body=query,
        scroll=SCROLL_TIMEOUT,
    )
    scroll_id = resp["_scroll_id"]
    total     = resp["hits"]["total"]["value"]
    log.info("inativos.scroll.start", total=total, also_ativos=also_ativos)

    ok = err = updated = skipped = 0
    batch: list[dict] = []

    def flush():
        nonlocal ok, err
        if not batch:
            return
        o, e = helpers.bulk(client, batch, raise_on_error=False, chunk_size=batch_size)
        ok  += o
        err += len(e) if isinstance(e, list) else e
        batch.clear()

    hits = resp["hits"]["hits"]
    while hits:
        for hit in hits:
            src      = hit["_source"]
            _id      = hit["_id"]
            processo = src.get("numero_processo") or _id

            doc_update: dict = {}

            # ── titular.nome ─────────────────────────────────────────────────
            if not skip_pessoas:
                titular = src.get("titular") or {}
                if not titular.get("nome"):
                    nome = map_pessoas.get(processo)
                    if nome:
                        doc_update["titular"] = {**titular, "nome": nome, "razao_social": nome}

            # ── substancias_desc ──────────────────────────────────────────────
            subs_desc = src.get("substancias_desc") or []
            if not skip_substancias and not subs_desc:
                novas = map_substancias.get(processo, [])
                if novas:
                    doc_update["substancias_desc"] = novas
                    subs_desc = novas          # usa para classificação abaixo

            # ── categorias_estrategicas ───────────────────────────────────────
            if not skip_classif and subs_desc:
                existing_cats = src.get("categorias_estrategicas") or []
                if not existing_cats:
                    cats, prio = classificar(subs_desc)
                    if cats:
                        doc_update["categorias_estrategicas"] = cats
                        doc_update["prioridade_estrategica"]  = prio

            if not doc_update:
                skipped += 1
                continue

            updated += 1
            if not dry_run:
                batch.append({
                    "_op_type":        "update",
                    "_index":          INDEX_JAZIDAS,
                    "_id":             _id,
                    "doc":             doc_update,
                    "retry_on_conflict": 3,
                })
                if len(batch) >= batch_size:
                    flush()
                    log.info("inativos.progress",
                             ok=ok, err=err, updated=updated, skipped=skipped)

        resp = client.scroll(scroll_id=scroll_id, scroll=SCROLL_TIMEOUT)
        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]

    flush()

    try:
        client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    log.info(
        "inativos.done",
        total_scrolled=ok + err + skipped + (updated if dry_run else 0),
        updated=updated if dry_run else ok,
        skipped_sem_dados=skipped,
        erros=err,
        dry_run=dry_run,
    )


def relatorio_cobertura(client: OpenSearch, ativo: bool) -> None:
    """Imprime cobertura dos campos críticos para ativos ou inativos."""
    label = "ATIVOS" if ativo else "INATIVOS"
    base_filter = [{"term": {"ativo": ativo}}]

    def pct(field: str) -> tuple[int, float]:
        resp = client.search(index=INDEX_JAZIDAS, body={
            "size": 0,
            "query": {"bool": {"filter": base_filter}},
            "aggs": {
                "total": {"value_count": {"field": "_id"}},
                "preenchidos": {"filter": {"exists": {"field": field}}},
            },
            "track_total_hits": True,
        })
        total = resp["hits"]["total"]["value"]
        preench = resp["aggregations"]["preenchidos"]["doc_count"]
        return preench, round(preench / max(total, 1) * 100, 1)

    total_resp = client.search(index=INDEX_JAZIDAS, body={
        "size": 0,
        "query": {"bool": {"filter": base_filter}},
        "track_total_hits": True,
    })
    total = total_resp["hits"]["total"]["value"]

    log.info(f"cobertura.{label.lower()}.start", total=total)
    for campo in ["titular.nome", "substancias_desc", "categorias_estrategicas",
                  "prioridade_estrategica", "dt_validade", "nup"]:
        n, p = pct(campo)
        status = "✅" if p >= 90 else ("⚠️" if p >= 30 else "─")
        log.info(f"cobertura.{label.lower()}.campo",
                 campo=campo, n=n, pct=p, status=status)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--skip-pessoas",     is_flag=True,
              help="Não preenche titular.nome")
@click.option("--skip-substancias", is_flag=True,
              help="Não preenche substancias_desc")
@click.option("--skip-classif",     is_flag=True,
              help="Não classifica categorias_estrategicas")
@click.option("--also-ativos",      is_flag=True,
              help="Aplica enriquecimento também nos processos ativos")
@click.option("--dry-run",          is_flag=True,
              help="Mostra quantos seriam atualizados, sem gravar")
@click.option("--report-only",      is_flag=True,
              help="Só exibe cobertura atual, sem enriquecimento")
@click.option("--batch-size",       default=BATCH_SIZE, show_default=True)
def main(
    skip_pessoas: bool,
    skip_substancias: bool,
    skip_classif: bool,
    also_ativos: bool,
    dry_run: bool,
    report_only: bool,
    batch_size: int,
) -> None:
    """
    Enriquece processos inativos em mr_jazidas_v001 com dados do microdados-scm.zip:
      - titular.nome   (ProcessoPessoa + Pessoa)
      - substancias_desc  (ProcessoSubstancia + Substancia)
      - categorias_estrategicas + prioridade_estrategica  (classificação)
    """
    client = get_os_client()

    if report_only:
        relatorio_cobertura(client, ativo=False)
        relatorio_cobertura(client, ativo=True)
        return

    # Procura o ZIP nos dois locais possíveis (novo padrão e legado /tmp)
    zip_candidates = [
        settings.etl_data_dir / "scm" / "microdados-scm.zip",
        Path("/tmp/mineralradar_data/scm/microdados-scm.zip"),
    ]
    zip_path = next((p for p in zip_candidates if p.exists()), None)
    if zip_path is None:
        log.error("inativos.zip.missing",
                  locais_verificados=[str(p) for p in zip_candidates],
                  dica="Execute primeiro: python -m bots.bot_sicop --microdados")
        raise SystemExit(1)
    log.info("inativos.zip.found", path=str(zip_path))

    # ── 1. Carrega lookups ────────────────────────────────────────────────────
    t0 = time.time()

    map_pessoas: dict[str, str] = {}
    if not skip_pessoas:
        map_pessoas = build_pessoa_lookup(zip_path)

    map_substancias: dict[str, list[str]] = {}
    if not skip_substancias or not skip_classif:
        map_substancias = build_substancias_lookup(zip_path)

    log.info("inativos.lookup.done", elapsed_s=round(time.time() - t0, 1),
             pessoas=len(map_pessoas), substancias=len(map_substancias))

    # ── 2. Enriquecimento via Scroll ──────────────────────────────────────────
    t1 = time.time()
    enrich_inativos(
        client       = client,
        map_pessoas  = map_pessoas,
        map_substancias = map_substancias,
        skip_pessoas = skip_pessoas,
        skip_substancias = skip_substancias,
        skip_classif = skip_classif,
        dry_run      = dry_run,
        also_ativos  = also_ativos,
        batch_size   = batch_size,
    )
    log.info("inativos.total_elapsed_s", s=round(time.time() - t1, 1))

    # ── 3. Relatório pós-enriquecimento ──────────────────────────────────────
    if not dry_run:
        relatorio_cobertura(client, ativo=False)
        if also_ativos:
            relatorio_cobertura(client, ativo=True)


if __name__ == "__main__":
    main()
