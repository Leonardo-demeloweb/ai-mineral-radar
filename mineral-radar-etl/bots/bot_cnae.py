"""
bot_cnae.py — RFB Cnaes.zip → mr_cnae_v001
===========================================

Indexa a tabela de Classificação Nacional de Atividades Econômicas (CNAE)
da Receita Federal do Brasil, derivando a hierarquia (seção, divisão, grupo,
classe, subclasse) e gerando embeddings semânticos via Azure OpenAI.

Fonte: Cnaes.zip (baixado pelo bot_empresas --download-rfb)
Formato: "codigo";"descricao" (sem cabeçalho, latin-1)

Uso:
  python -m bots.bot_cnae                       # indexa com embeddings
  python -m bots.bot_cnae --no-embeddings       # indexa sem embeddings
  python -m bots.bot_cnae --dry-run             # simula sem escrever
"""
from __future__ import annotations

import io
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import click
import polars as pl
from opensearchpy import OpenSearch, helpers

from bots.common.embeddings import (
    ZERO_VECTOR,
    embed_batch,
    get_embedding_client as _get_embedding_client,
)
from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_CNAE  = "mr_cnae_v001"
BATCH_SIZE  = 200
EMBED_BATCH = 500


# ─────────────────────────────────────────────────────────────────────────────
# CNAE hierarchy helpers
# ─────────────────────────────────────────────────────────────────────────────

# Mapeamento divisão → seção (IBGE CNAE 2.0)
_DIVISAO_TO_SECAO: dict[str, str] = {
    **{str(d).zfill(2): "A" for d in range(1, 4)},    # 01-03
    **{str(d).zfill(2): "B" for d in range(5, 10)},   # 05-09
    **{str(d).zfill(2): "C" for d in range(10, 34)},  # 10-33
    "35": "D",
    **{str(d).zfill(2): "E" for d in range(36, 40)},  # 36-39
    **{str(d).zfill(2): "F" for d in range(41, 44)},  # 41-43
    **{str(d).zfill(2): "G" for d in range(45, 48)},  # 45-47
    **{str(d).zfill(2): "H" for d in range(49, 54)},  # 49-53
    **{str(d).zfill(2): "I" for d in range(55, 57)},  # 55-56
    **{str(d).zfill(2): "J" for d in range(58, 64)},  # 58-63
    **{str(d).zfill(2): "K" for d in range(64, 67)},  # 64-66
    "68": "L",
    **{str(d).zfill(2): "M" for d in range(69, 76)},  # 69-75
    **{str(d).zfill(2): "N" for d in range(77, 83)},  # 77-82
    "84": "O",
    "85": "P",
    **{str(d).zfill(2): "Q" for d in range(86, 89)},  # 86-88
    **{str(d).zfill(2): "R" for d in range(90, 94)},  # 90-93
    **{str(d).zfill(2): "S" for d in range(94, 97)},  # 94-96
    **{str(d).zfill(2): "T" for d in range(97, 99)},  # 97-98
    "99": "U",
}

_SECAO_NOME: dict[str, str] = {
    "A": "Agricultura, pecuária, produção florestal, pesca e aquicultura",
    "B": "Indústrias extrativas",
    "C": "Indústrias de transformação",
    "D": "Eletricidade e gás",
    "E": "Água, esgoto, atividades de gestão de resíduos e descontaminação",
    "F": "Construção",
    "G": "Comércio; reparação de veículos automotores e motocicletas",
    "H": "Transporte, armazenagem e correio",
    "I": "Alojamento e alimentação",
    "J": "Informação e comunicação",
    "K": "Atividades financeiras, de seguros e serviços relacionados",
    "L": "Atividades imobiliárias",
    "M": "Atividades profissionais, científicas e técnicas",
    "N": "Atividades administrativas e serviços complementares",
    "O": "Administração pública, defesa e seguridade social",
    "P": "Educação",
    "Q": "Saúde humana e serviços sociais",
    "R": "Artes, cultura, esporte e recreação",
    "S": "Outras atividades de serviços",
    "T": "Serviços domésticos",
    "U": "Organismos internacionais e outras instituições extraterritoriais",
}


def _parse_hierarchy(codigo: str) -> dict:
    """
    Deriva hierarquia CNAE a partir do código de 7 dígitos.

    Exemplo: '0500301'
      divisao:   '05'
      grupo:     '050'
      classe:    '05003'           (4 + 1 dígitos)
      subclasse: '0500301'         (full 7 dígitos)
      secao:     'B'               (Indústrias extrativas)
    """
    c = str(codigo).strip().zfill(7)
    divisao = c[:2]
    grupo   = c[:3]
    classe  = c[:5]
    secao   = _DIVISAO_TO_SECAO.get(divisao, "?")
    return {
        "secao":     secao,
        "divisao":   divisao,
        "grupo":     grupo,
        "classe":    classe,
        "subclasse": c,
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
        "timeout": 60,
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
# Parse CNAE CSV
# ─────────────────────────────────────────────────────────────────────────────

def load_cnaes(cnaes_zip: Path) -> pl.DataFrame:
    """Lê Cnaes.zip da RFB e retorna DataFrame com codigo + descricao."""
    log.info("cnae.load.start", path=cnaes_zip.name)
    with zipfile.ZipFile(cnaes_zip) as zf:
        with zf.open(zf.namelist()[0]) as f:
            buf = io.BytesIO(f.read())

    df = pl.read_csv(
        buf,
        separator=";",
        quote_char='"',
        encoding="latin1",
        has_header=False,
        new_columns=["codigo", "descricao"],
        infer_schema_length=0,
    )
    # Limpa espaços e remove linhas vazias
    df = df.with_columns([
        pl.col("codigo").str.strip_chars(),
        pl.col("descricao").str.strip_chars(),
    ]).filter(
        pl.col("codigo").str.len_chars() == 7
    )
    log.info("cnae.load.done", total=len(df))
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Build documents
# ─────────────────────────────────────────────────────────────────────────────

def build_docs(df: pl.DataFrame, embed_client, deployment: str) -> list[dict]:
    """
    Constrói lista de documentos para mr_cnae_v001.
    Gera embeddings em batches se embed_client for fornecido.
    """
    log.info("cnae.build.start", total=len(df))
    now_iso = datetime.now(timezone.utc).isoformat()

    docs: list[dict] = []
    for row in df.iter_rows(named=True):
        codigo   = row["codigo"]
        descricao = row["descricao"]
        hier     = _parse_hierarchy(codigo)

        docs.append({
            "codigo":    codigo,
            "descricao": descricao,
            "secao":     hier["secao"],
            "divisao":   hier["divisao"],
            "grupo":     hier["grupo"],
            "classe":    hier["classe"],
            "subclasse": hier["subclasse"],
            "embedding": ZERO_VECTOR,  # preenchido abaixo
            "indexed_at": now_iso,
        })

    # Gera embeddings em lotes
    if embed_client:
        log.info("cnae.embed.start", total=len(docs), deployment=deployment)
        texts = [f"CNAE {d['codigo']}: {d['descricao']}" for d in docs]
        for i in range(0, len(texts), EMBED_BATCH):
            chunk = texts[i:i + EMBED_BATCH]
            vecs  = embed_batch(embed_client, chunk, deployment)
            for doc, vec in zip(docs[i:i + EMBED_BATCH], vecs):
                doc["embedding"] = vec
            log.info("cnae.embed.progress",
                     done=min(i + EMBED_BATCH, len(docs)),
                     total=len(docs))
        log.info("cnae.embed.done")
    else:
        log.info("cnae.embed.skip", msg="embeddings desativados — vetor zero")

    return docs


# ─────────────────────────────────────────────────────────────────────────────
# Index
# ─────────────────────────────────────────────────────────────────────────────

def index_cnaes(
    client: OpenSearch,
    docs: list[dict],
    dry_run: bool,
) -> dict[str, int]:
    """Bulk index dos CNAEs em mr_cnae_v001."""
    log.info("cnae.index.start", total=len(docs))
    total_ok = total_err = 0

    actions = [
        {
            "_index":  INDEX_CNAE,
            "_id":     doc["codigo"],
            "_source": doc,
        }
        for doc in docs
    ]

    for i in range(0, len(actions), BATCH_SIZE):
        batch = actions[i:i + BATCH_SIZE]
        if not dry_run:
            ok, errs = helpers.bulk(client, batch, raise_on_error=False)
            total_ok  += ok
            total_err += len(errs) if isinstance(errs, list) else errs
            if errs and isinstance(errs, list):
                for e in errs[:3]:
                    log.error("cnae.index.error", error=str(e)[:200])
        else:
            total_ok += len(batch)

    log.info("cnae.index.done",
             total_ok=total_ok,
             total_err=total_err,
             dry_run=dry_run)
    return {"ok": total_ok, "errors": total_err}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--no-embeddings", is_flag=True,
              help="Indexa sem gerar embeddings semânticos.")
@click.option("--dry-run",       is_flag=True,
              help="Simula sem escrever no OpenSearch.")
@click.option("--data-dir",      default=None,
              help="Diretório com Cnaes.zip (default: settings.etl_data_dir/rfb).")
def main(
    no_embeddings: bool,
    dry_run: bool,
    data_dir: str | None,
) -> None:
    """bot_cnae — RFB Cnaes.zip → mr_cnae_v001 com hierarquia e embeddings."""
    t0 = time.time()

    _data_dir = Path(data_dir) if data_dir else settings.etl_data_dir / "rfb"
    cnaes_zip = _data_dir / "Cnaes.zip"

    if not cnaes_zip.exists():
        log.error("cnae.file.notfound",
                  path=str(cnaes_zip),
                  msg="Execute bot_empresas --download-rfb primeiro")
        raise SystemExit(1)

    client = get_os_client()

    # Embedding client
    embed_client = None
    deployment   = settings.azure_openai_deployment_embedding
    if not no_embeddings:
        embed_client = _get_embedding_client()
        if embed_client:
            log.info("cnae.embeddings.enabled", deployment=deployment)
        else:
            log.warning("cnae.embeddings.disabled",
                        msg="Azure OpenAI não configurado — indexando sem embeddings")

    df   = load_cnaes(cnaes_zip)
    docs = build_docs(df, embed_client, deployment)
    res  = index_cnaes(client, docs, dry_run=dry_run)

    # Distribuição por seção (log de validação)
    from collections import Counter
    secoes = Counter(d["secao"] for d in docs)
    for sec, n in sorted(secoes.items()):
        nome = _SECAO_NOME.get(sec, "?")
        log.info("cnae.secao", secao=sec, nome=nome[:50], total=n)

    elapsed = round(time.time() - t0, 1)
    log.info("bot_cnae.done",
             total_cnaes=len(docs),
             elapsed_s=elapsed,
             dry_run=dry_run,
             **res)


if __name__ == "__main__":
    main()
