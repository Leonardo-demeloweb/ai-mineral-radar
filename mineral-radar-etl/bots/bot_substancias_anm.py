"""
bot_substancias_anm.py — Catálogo oficial ANM → mr_substancias_v001

Carrega a tabela mestre de substâncias (~862) do Cadastro Mineiro ANM
(arquivo ``Substancia.txt`` dentro de ``microdados-scm.zip``), gera embeddings
e indexa em ``mr_substancias_v001``.

Enriquecimento opcional de ``tipo_uso`` a partir dos CSVs SCM (mesma lógica do bot_scm).

Uso:
  python -m bots.bot_substancias_anm
  python -m bots.bot_substancias_anm --skip-download   # reutiliza ZIP ~313 MB
  python -m bots.bot_substancias_anm --dry-run
  python -m bots.bot_substancias_anm --no-embeddings
  python -m bots.bot_substancias_anm --no-scm-enrich  # só tabela oficial
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import click
from opensearchpy import OpenSearch, helpers

from bots.bot_anm_direto import get_os_client
from bots.bot_scm import (
    EMBED_BATCH_SIZE,
    IDX_SUBSTANCIAS,
    _build_substancia_embedding_text,
    _ensure_substancias_fields,
    _tipo_uso_meta,
    download_scm_csvs,
    load_scm_dataframe,
)
from bots.bot_sicop import SCM_MICRODADOS_FILE, download_microdados
from bots.common.anm_substancias import (
    build_scm_tipo_uso_map,
    normalize_nome,
    parse_substancias_microdados,
)
from bots.common.embeddings import ZERO_VECTOR, embed_batch, get_embedding_client
from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

MIN_OFFICIAL_ROWS = 400  # alerta se muito abaixo de ~862


def build_documents(
    official_rows: list[dict[str, Any]],
    scm_tipo_map: dict[str, str],
    tipo_to_id: dict[str, int],
) -> list[dict[str, Any]]:
    """Monta documentos OpenSearch a partir do catálogo oficial + merge SCM."""
    docs: list[dict[str, Any]] = []

    for i, row in enumerate(official_rows, start=1):
        nome = row["nome"]
        nome_upper = nome.upper()
        nome_norm = row["nome_normalizado"] or normalize_nome(nome)
        id_anm = row.get("id_anm")

        tipo_uso = scm_tipo_map.get(nome_upper, "")
        meta = _tipo_uso_meta(tipo_uso) if tipo_uso else {
            "grupo": "Outros Minerais",
            "categoria": "outro",
            "estrategica": False,
        }
        if row.get("categoria_estrategica"):
            meta = {**meta, "estrategica": True}

        doc_id = str(id_anm) if id_anm is not None else nome_upper

        docs.append({
            "_id": doc_id,
            "id":     id_anm if id_anm is not None else i,
            "id_anm": id_anm,
            "codigo": str(id_anm) if id_anm is not None else nome_upper,
            "nome":   nome,
            "nome_normalizado": nome_norm,
            "tipo_uso":    tipo_uso or None,
            "tipo_uso_id": tipo_to_id.get((tipo_uso or "").lower().strip()),
            "grupo":       meta["grupo"],
            "categoria":   meta["categoria"],
            "estrategica": meta["estrategica"],
            "categoria_estrategica": row.get("categoria_estrategica"),
            "ativo":       row.get("ativo", True),
            "fonte":       "ANM/CadastroMineiro/Substancia",
        })

    return docs


def clear_substancias_index(client: OpenSearch, dry_run: bool) -> int:
    """
    Remove todos os documentos de mr_substancias_v001.

    Necessário ao migrar do modo legado (_id = nome UPPER, ~358 docs SCM)
    para o catálogo oficial (_id = id_anm, ~862 docs).
    """
    if dry_run:
        log.info("dry_run.substancias_clear", would_delete="all")
        return 0
    resp = client.delete_by_query(
        index=IDX_SUBSTANCIAS,
        body={"query": {"match_all": {}}},
        refresh=True,
        conflicts="proceed",
    )
    deleted = int(resp.get("deleted", 0))
    log.info("substancias.cleared", deleted=deleted)
    return deleted


def index_substancias_catalog(
    client: OpenSearch,
    docs: list[dict[str, Any]],
    embed_client,
    embed_deployment: str,
    dry_run: bool,
    *,
    replace: bool = True,
) -> None:
    """Gera embeddings e faz bulk index em mr_substancias_v001."""
    if not docs:
        log.error("substancias.no_docs")
        return

    if replace:
        clear_substancias_index(client, dry_run)

    log.info("substancias.embedding.start", total=len(docs))
    texts = [
        _build_substancia_embedding_text(
            d["nome"],
            d.get("tipo_uso") or "",
            d["grupo"],
            d["categoria"],
        )
        for d in docs
    ]

    if embed_client:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            chunk = texts[i: i + EMBED_BATCH_SIZE]
            vectors.extend(embed_batch(embed_client, chunk, embed_deployment))
            log.info("substancias.embed_batch.done", offset=i, chunk=len(chunk))
    else:
        vectors = [ZERO_VECTOR] * len(docs)

    for doc, vec in zip(docs, vectors):
        doc["embedding"] = vec

    if dry_run:
        log.info("dry_run.substancias", would_index=len(docs), sample=docs[:2])
        return

    _ensure_substancias_fields(client)
    client.indices.put_mapping(
        index=IDX_SUBSTANCIAS,
        body={"properties": {
            "id_anm": {"type": "integer"},
            "categoria_estrategica": {"type": "keyword"},
            "fonte": {"type": "keyword"},
        }},
    )

    actions = [
        {"_index": IDX_SUBSTANCIAS, "_id": d.pop("_id"), "_source": d}
        for d in docs
    ]
    ok, errs = helpers.bulk(client, actions, raise_on_error=False, chunk_size=100)
    log.info("substancias.indexed", ok=ok, errs=errs, total=len(docs))


@click.command()
@click.option("--skip-download", is_flag=True, help="Reutiliza microdados-scm.zip e CSVs SCM")
@click.option("--dry-run", is_flag=True, help="Não escreve no OpenSearch")
@click.option("--no-embeddings", is_flag=True, help="Vetores zero (sem Azure OpenAI)")
@click.option("--no-scm-enrich", is_flag=True,
              help="Não baixa CSVs SCM para enriquecer tipo_uso")
@click.option("--append", is_flag=True,
              help="Não apaga o índice antes (pode duplicar com ingest SCM antigo)")
def main(
    skip_download: bool,
    dry_run: bool,
    no_embeddings: bool,
    no_scm_enrich: bool,
    append: bool,
) -> None:
    """Catálogo oficial ANM (~862 substâncias) → mr_substancias_v001."""
    t0 = time.time()
    os_client = get_os_client()
    data_dir = settings.etl_data_dir

    embed_client = None if (no_embeddings or dry_run) else get_embedding_client()
    embed_deployment = settings.azure_openai_deployment_embedding

    # 1. Microdados SCM (tabela Substancia.txt)
    micro_zip = download_microdados(data_dir, skip=skip_download)
    if not micro_zip or not micro_zip.exists():
        log.error(
            "substancias.microdados.required",
            msg="Baixe microdados-scm.zip (SCM/microdados/) ou use --skip-download com ZIP local",
        )
        raise SystemExit(1)

    official = parse_substancias_microdados(micro_zip)
    if len(official) < MIN_OFFICIAL_ROWS:
        log.warning(
            "substancias.low_count",
            got=len(official),
            expected="~862",
            hint="verifique Substancia.txt no ZIP ou versão dos microdados",
        )

    # 2. Enriquecimento tipo_uso via CSVs SCM (opcional)
    scm_tipo_map: dict[str, str] = {}
    tipo_to_id: dict[str, int] = {}
    if not no_scm_enrich:
        scm_dir = data_dir / "scm"
        paths = download_scm_csvs(scm_dir, skip=skip_download)
        if paths:
            df = load_scm_dataframe(paths)
            scm_tipo_map = build_scm_tipo_uso_map(df)
            log.info("substancias.scm_tipo_uso", mapped=len(scm_tipo_map))

    docs = build_documents(official, scm_tipo_map, tipo_to_id)
    index_substancias_catalog(
        os_client, docs, embed_client, embed_deployment, dry_run,
        replace=not append,
    )

    elapsed = round(time.time() - t0, 1)
    log.info("bot_substancias_anm.done", elapsed_s=elapsed, total=len(docs))


if __name__ == "__main__":
    main()
