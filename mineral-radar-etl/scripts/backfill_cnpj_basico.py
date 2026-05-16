"""
Backfill ``titular.cnpj_basico`` em ``mr_jazidas_v001``.

Dois passos distintos — rodar separadamente ou encadeados:

  PASSO 1 — zfill (obrigatório, ~40k docs)
  -----------------------------------------
  Documetos onde ``cnpj_basico`` tem 1-7 dígitos (zeros à esquerda perdidos
  no parse antigo).  Corrige via ``update_by_query`` com script Painless:
      "054003"  →  "00054003"
      "459445"  →  "00459445"

  PASSO 2 — resolução por razão social (opcional, ~361k docs null)
  ----------------------------------------------------------------
  Para cada processo sem ``cnpj_basico``, tenta encontrar a empresa em
  ``mr_empresas_v001`` por ``match_phrase`` na razão social + filtro de UF.
  Só grava quando **um único candidato** com score acima do threshold é
  encontrado, e registra ``titular.cnpj_basico_fonte = "nome_rfb"`` e
  ``titular.cnpj_basico_confianca`` para rastreabilidade.
  **Não** sobrescreve ``cnpj_basico`` preexistente.

  Limites do passo 2:
    - Homônimos / nomes genéricos ("MUNICIPIO DE …") podem retornar múltiplos
      candidatos e serão pulados.
    - Processos muito antigos (< 1990) provavelmente não têm cadastro na RFB.
    - Score-threshold e max_candidatos são configuráveis.

Execução:

    # Apenas passo 1 (rápido, seguro, recomendado primeiro)
    python scripts/backfill_cnpj_basico.py --passo 1

    # Apenas passo 2 (mais lento, revise os logs)
    python scripts/backfill_cnpj_basico.py --passo 2

    # Ambos em sequência
    python scripts/backfill_cnpj_basico.py

    # Dry-run (não grava, só conta / mostra exemplos)
    python scripts/backfill_cnpj_basico.py --dry-run

    # Limitar processos do passo 2 para testes
    python scripts/backfill_cnpj_basico.py --passo 2 --limit 500
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Iterator

import click
from dotenv import load_dotenv
from opensearchpy import OpenSearch, helpers

_ETL_ROOT = Path(__file__).resolve().parents[1]
if str(_ETL_ROOT) not in sys.path:
    sys.path.insert(0, str(_ETL_ROOT))

for _env in (_ETL_ROOT / ".env", _ETL_ROOT.parent / ".env"):
    if _env.exists():
        load_dotenv(_env, override=False)
        break

from bots.bot_anm_direto import get_os_client  # noqa: E402
from bots.common.logging import get_logger      # noqa: E402

log = get_logger(__name__)

INDEX_ANM      = "mr_jazidas_v001"
INDEX_EMPRESAS = "mr_empresas_v001"
SCROLL_TTL     = "5m"
SCROLL_SIZE    = 500
DEFAULT_BATCH  = 200
DEFAULT_SCORE_THRESHOLD = 6.0   # mínimo aceitável no passo 2 (match_phrase único)


# ─────────────────────────────────────────────────────────────────────────────
# PASSO 1: zfill via update_by_query (Painless)
# ─────────────────────────────────────────────────────────────────────────────

_ZFILL_SCRIPT = """
String raw = ctx._source.titular.cnpj_basico;
if (raw == null || raw.length() == 0) return;
// Não sobrescreve se já tem 8+ dígitos
if (raw.length() >= 8) return;
// Pad com zeros à esquerda até 8
String padded = raw;
while (padded.length() < 8) { padded = '0' + padded; }
ctx._source.titular.cnpj_basico = padded.substring(0, 8);
ctx._source.titular.cnpj_basico_fonte = 'zfill_backfill';
"""

# Docs com cnpj_basico presente mas < 8 dígitos (regex OpenSearch)
_ZFILL_QUERY = {
    "bool": {
        "must": {"exists": {"field": "titular.cnpj_basico"}},
        "must_not": {"regexp": {"titular.cnpj_basico": "[0-9]{8,}"}},
    }
}


def passo1_zfill(client: OpenSearch, dry_run: bool) -> None:
    """Corrige cnpj_basico com < 8 dígitos via update_by_query."""
    log.info("passo1.inicio")
    count_resp = client.count(index=INDEX_ANM, body={"query": _ZFILL_QUERY})
    total = int(count_resp.get("count", 0))
    log.info("passo1.total_afetados", count=total)
    if total == 0:
        log.info("passo1.nada_a_fazer")
        return

    if dry_run:
        log.info("passo1.dry_run", total=total, mensagem="Nenhuma alteração gravada.")
        _preview_zfill(client)
        return

    body = {
        "script": {
            "source": _ZFILL_SCRIPT,
            "lang": "painless",
        },
        "query": _ZFILL_QUERY,
    }
    t0 = time.monotonic()
    # ``timeout="10m"`` não pode ser passado aqui: o cliente opensearch-py trata
    # ``timeout``/``request_timeout`` como timeout HTTP (urllib3 = segundos float),
    # não como parâmetro ``timeout`` da API OpenSearch.
    resp = client.update_by_query(
        index=INDEX_ANM,
        body=body,
        conflicts="proceed",
        wait_for_completion=True,
        scroll_size=500,
        request_timeout=900,  # segundos — espera HTTP da operação completa
    )
    elapsed = round(time.monotonic() - t0, 1)
    log.info(
        "passo1.concluido",
        updated=resp.get("updated"),
        noops=resp.get("noops"),
        failures=len(resp.get("failures", [])),
        elapsed_s=elapsed,
    )


def _preview_zfill(client: OpenSearch, n: int = 8) -> None:
    """Exibe amostras do que seria corrigido (dry-run)."""
    resp = client.search(
        index=INDEX_ANM,
        body={
            "size": n,
            "query": _ZFILL_QUERY,
            "_source": ["numero_processo", "titular.cnpj_basico", "titular.nome"],
        },
    )
    log.info("passo1.preview_amostras")
    for h in resp["hits"]["hits"]:
        s = h["_source"]
        t = s.get("titular") or {}
        raw = t.get("cnpj_basico", "")
        padded = raw.zfill(8)[:8] if raw else "(nulo)"
        log.info(
            "preview",
            processo=s.get("numero_processo"),
            antes=raw,
            depois=padded,
            nome=t.get("nome", ""),
        )


# ─────────────────────────────────────────────────────────────────────────────
# PASSO 2: resolução por razão social
# ─────────────────────────────────────────────────────────────────────────────

# Docs com cnpj_basico ausente (null) e nome preenchido e não-genérico
_NULL_CNPJ_QUERY: dict[str, Any] = {
    "bool": {
        "must": {"exists": {"field": "titular.nome"}},
        "must_not": [
            {"exists": {"field": "titular.cnpj_basico"}},
            # "DADO NÃO CADASTRADO" é placeholder do SIGMINE — nenhuma busca resolve
            {"term": {"titular.nome.keyword": "DADO NÃO CADASTRADO"}},
        ],
    }
}


def _iter_null_cnpj(
    client: OpenSearch, limit: int | None
) -> Iterator[dict[str, Any]]:
    body: dict[str, Any] = {
        "size": SCROLL_SIZE,
        "_source": ["numero_processo", "uf", "titular"],
        "query": _NULL_CNPJ_QUERY,
        "sort": [{"_id": "asc"}],
    }
    resp = client.search(index=INDEX_ANM, body=body, scroll=SCROLL_TTL)
    scroll_id = resp["_scroll_id"]
    total_yielded = 0
    try:
        while True:
            hits = resp["hits"]["hits"]
            if not hits:
                break
            for h in hits:
                yield h
                total_yielded += 1
                if limit is not None and total_yielded >= limit:
                    return
            resp = client.scroll(scroll_id=scroll_id, scroll=SCROLL_TTL)
            scroll_id = resp["_scroll_id"]
    finally:
        try:
            client.clear_scroll(scroll_id=scroll_id)
        except Exception as e:
            log.warning("scroll.clear", error=str(e))


def _buscar_cnpj_por_nome(
    client: OpenSearch,
    nome: str,
    uf: str | None,
    score_threshold: float,
) -> tuple[str | None, float]:
    """
    Tenta resolver cnpj_basico a partir da razão social no mr_empresas_v001.

    Retorna (cnpj_basico, score) se único candidato acima do threshold,
    ou (None, 0.0) caso contrário.
    """
    must: list[dict] = [{"match_phrase": {"razao_social": nome}}]
    if uf:
        must.append({"term": {"uf": uf.upper()}})

    resp = client.search(
        index=INDEX_EMPRESAS,
        body={
            "size": 3,
            "query": {"bool": {"must": must}},
            "_source": ["cnpj_basico", "razao_social", "situacao"],
            "min_score": score_threshold,
        },
    )
    hits = resp["hits"]["hits"]
    if not hits:
        return None, 0.0

    # Aceita apenas resultado único (evita ambiguidade)
    if len(hits) > 1:
        return None, 0.0

    hit = hits[0]
    cnpj = (hit["_source"].get("cnpj_basico") or "").strip()
    if not cnpj or len(cnpj) < 8:
        return None, 0.0

    return cnpj[:8].zfill(8), float(hit.get("_score") or 0.0)


def passo2_resolver_por_nome(
    client: OpenSearch,
    dry_run: bool,
    limit: int | None,
    score_threshold: float,
    batch_size: int,
) -> None:
    """Para cada processo com cnpj_basico=null, tenta resolver via razão social na RFB."""
    count_resp = client.count(index=INDEX_ANM, body={"query": _NULL_CNPJ_QUERY})
    total_null = int(count_resp.get("count", 0))
    log.info("passo2.inicio", total_null=total_null, limit=limit, dry_run=dry_run)

    stats = {
        "processados": 0,
        "resolvidos": 0,
        "multiplos_candidatos": 0,
        "sem_match": 0,
        "nome_vazio": 0,
        "gravados": 0,
    }

    batch: list[dict] = []
    t0 = time.monotonic()

    for hit in _iter_null_cnpj(client, limit):
        src = hit["_source"]
        doc_id = hit["_id"]
        titular = src.get("titular") or {}
        nome = (titular.get("nome") or titular.get("razao_social") or "").strip()
        uf = (src.get("uf") or "").strip() or None
        stats["processados"] += 1

        if not nome:
            stats["nome_vazio"] += 1
            continue

        cnpj, score = _buscar_cnpj_por_nome(client, nome, uf, score_threshold)

        if cnpj is None:
            # Verifica se foi "sem match" ou "múltiplos"
            must: list[dict] = [{"match_phrase": {"razao_social": nome}}]
            if uf:
                must.append({"term": {"uf": uf.upper()}})
            r2 = client.count(
                index=INDEX_EMPRESAS,
                body={"query": {"bool": {"must": must}}},
            )
            if int(r2.get("count", 0)) > 1:
                stats["multiplos_candidatos"] += 1
            else:
                stats["sem_match"] += 1
            continue

        stats["resolvidos"] += 1
        if dry_run:
            log.info(
                "passo2.dry_run",
                processo=src.get("numero_processo"),
                nome=nome,
                cnpj_resolvido=cnpj,
                score=round(score, 2),
            )
            continue

        batch.append({
            "_op_type": "update",
            "_index": INDEX_ANM,
            "_id": doc_id,
            "script": {
                "source": (
                    "if (ctx._source.titular == null) { ctx._source.titular = new HashMap(); } "
                    "ctx._source.titular.cnpj_basico = params.cnpj; "
                    "ctx._source.titular.cnpj_basico_fonte = params.fonte; "
                    "ctx._source.titular.cnpj_basico_confianca = params.score;"
                ),
                "lang": "painless",
                "params": {
                    "cnpj": cnpj,
                    "fonte": "nome_rfb",
                    "score": round(score, 2),
                },
            },
            "retry_on_conflict": 3,
        })

        if len(batch) >= batch_size:
            ok, errs = helpers.bulk(client, batch, raise_on_error=False)
            stats["gravados"] += ok
            batch.clear()

    # flush restante
    if batch and not dry_run:
        ok, errs = helpers.bulk(client, batch, raise_on_error=False)
        stats["gravados"] += ok

    elapsed = round(time.monotonic() - t0, 1)
    log.info("passo2.concluido", elapsed_s=elapsed, **stats)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option(
    "--passo",
    type=click.Choice(["1", "2", "ambos"]),
    default="ambos",
    show_default=True,
    help="Qual passo executar: 1=zfill, 2=nome, ambos=sequência completa.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Apenas conta e exibe amostras; não grava no índice.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Máximo de docs a processar no passo 2 (útil para testes).",
)
@click.option(
    "--score-threshold",
    type=float,
    default=DEFAULT_SCORE_THRESHOLD,
    show_default=True,
    help="Score mínimo de match_phrase para aceitar um candidato (passo 2).",
)
@click.option(
    "--batch-size",
    type=int,
    default=DEFAULT_BATCH,
    show_default=True,
    help="Tamanho do lote de updates (passo 2).",
)
def main(
    passo: str,
    dry_run: bool,
    limit: int | None,
    score_threshold: float,
    batch_size: int,
) -> None:
    """
    Backfill de titular.cnpj_basico em mr_jazidas_v001.

    Passo 1: zfill para raízes com zeros à esquerda perdidos (~40k docs).
    Passo 2: resolução por razão social na RFB (~361k docs null).
    """
    client = get_os_client()

    if passo in ("1", "ambos"):
        click.echo("=== Passo 1: zfill cnpj_basico curto ===")
        passo1_zfill(client, dry_run=dry_run)

    if passo in ("2", "ambos"):
        click.echo("=== Passo 2: resolução por razão social ===")
        passo2_resolver_por_nome(
            client=client,
            dry_run=dry_run,
            limit=limit,
            score_threshold=score_threshold,
            batch_size=batch_size,
        )

    click.echo("Concluído.")


if __name__ == "__main__":
    main()
