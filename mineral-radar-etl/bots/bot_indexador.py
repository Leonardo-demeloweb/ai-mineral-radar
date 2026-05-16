"""
bot_indexador.py — Indexação dos processos ANM no OpenSearch MineralRadar

Fluxo:
  1. SELECT * FROM vw_processos_completo WHERE needs_reindex = TRUE
  2. (Opcional) Gerar embeddings via Azure OpenAI para processos estratégicos
  3. Bulk index no OpenSearch (índice anm_processos_v001)
  4. Atualiza indexed_at + last_indexed_hash no PostgreSQL

Uso:
  python -m bots.bot_indexador
  python -m bots.bot_indexador --batch-size 500 --limit 10000
  python -m bots.bot_indexador --full-reindex   # ignora needs_reindex
"""
from __future__ import annotations

import json
import time
from typing import Iterator

import click
from opensearchpy import OpenSearch, helpers

from bots.common.db import get_conn, start_run, finish_run
from bots.common.logging import get_logger
from bots.common.settings import settings

log = get_logger(__name__)

INDEX_NAME = "mr_jazidas_v001"
DEFAULT_BATCH = 200


def get_opensearch_client() -> OpenSearch:
    return OpenSearch(
        hosts=[settings.opensearch_url],
        http_auth=(settings.opensearch_user, settings.opensearch_pass),
        use_ssl=settings.opensearch_url.startswith("https"),
        verify_certs=False,
        timeout=30,
    )


def iter_processos(conn, full_reindex: bool, limit: int | None) -> Iterator[dict]:
    """Itera sobre os processos que precisam ser (re)indexados."""
    where = "" if full_reindex else "WHERE needs_reindex = TRUE"
    limit_clause = f"LIMIT {limit}" if limit else ""
    cursor = conn.execute(
        f"SELECT * FROM vw_processos_completo {where} ORDER BY updated_at ASC {limit_clause}"
    )
    for row in cursor:
        yield dict(row)


def processo_to_doc(row: dict) -> dict:
    """Converte linha da view em documento OpenSearch."""
    doc: dict = {
        "numero_processo":       row["numero_processo"],
        "ativo":                 row["ativo"],
        "fase":                  row["fase"],
        "situacao":              row["situacao"],
        "substancias":           row["substancias"] or [],
        "substancias_desc":      row["substancias_desc"] or [],
        "categorias_estrategicas": row["categorias_estrategicas"] or [],
        "prioridade_estrategica": row["prioridade_estrategica"],
        "uf":                    row["uf"],
        "municipio":             row["municipio_nome_ibge"] or row["municipio"],
        "regiao":                row["regiao"],
        "area_ha":               float(row["area_ha"]) if row["area_ha"] else None,
        "titular": {
            "nome":             row["nm_titular"],
            "cnpj_basico":      row["cnpj_titular_basico"],
            "razao_social":     row["titular_razao_social"],
            "situacao_rfb":     row["titular_situacao_rfb"],
            "cnae_principal":   row["titular_cnae_principal"],
        },
        "dt_requerimento":       row["dt_requerimento"].isoformat() if row["dt_requerimento"] else None,
        "dt_validade":           row["dt_validade"].isoformat() if row["dt_validade"] else None,
        "cfem": {
            "total_historico":  float(row["cfem_total_historico"]) if row["cfem_total_historico"] else 0,
            "ultimo_ano":       float(row["cfem_ultimo_ano"]) if row["cfem_ultimo_ano"] else 0,
            "anos_producao":    row["cfem_anos_producao"],
            "ultima_arrecadacao": row["cfem_ultima_arrecadacao"].isoformat() if row["cfem_ultima_arrecadacao"] else None,
        },
        "restricoes_geo":        row["restricoes_geo"] or [],
        "n_restricoes_ti":       row["n_restricoes_ti"] or 0,
        "n_restricoes_uc":       row["n_restricoes_uc"] or 0,
    }

    # geo_point para queries de distância
    if row["lat"] and row["lon"]:
        doc["location"] = {"lat": float(row["lat"]), "lon": float(row["lon"])}

    # geo_shape para queries de polígono
    if row["geom_geojson"]:
        doc["geom"] = row["geom_geojson"]

    return doc


def bulk_actions(docs: list[dict]) -> list[dict]:
    return [
        {
            "_index": INDEX_NAME,
            "_id": doc["numero_processo"],
            "_source": doc,
        }
        for doc in docs
    ]


@click.command()
@click.option("--batch-size", default=DEFAULT_BATCH, show_default=True)
@click.option("--limit", type=int, default=None, help="Máx. de documentos a indexar (teste)")
@click.option("--full-reindex", is_flag=True, help="Reindexar tudo (ignora needs_reindex)")
def main(batch_size: int, limit: int | None, full_reindex: bool) -> None:
    """Bot de indexação → OpenSearch anm_processos_v001."""
    os_client = get_opensearch_client()
    run_id = start_run("bot_indexador")
    t0 = time.time()
    indexed = errors = 0

    try:
        with get_conn() as conn:
            batch: list[dict] = []
            processed_ids: list[str] = []

            for row in iter_processos(conn, full_reindex, limit):
                doc = processo_to_doc(row)
                batch.append(doc)
                processed_ids.append(row["numero_processo"])

                if len(batch) >= batch_size:
                    ok, errs = helpers.bulk(os_client, bulk_actions(batch), raise_on_error=False)
                    indexed += ok
                    errors += len(errs)

                    # Marca indexed_at no PostgreSQL
                    conn.execute(
                        """
                        UPDATE staging_processos
                        SET indexed_at = NOW(), last_indexed_hash = hash
                        WHERE numero_processo = ANY(%s)
                        """,
                        (processed_ids,),
                    )
                    conn.commit()
                    log.info("indexador.batch", indexed=ok, errors=len(errs))
                    batch = []
                    processed_ids = []

            # flush último batch
            if batch:
                ok, errs = helpers.bulk(os_client, bulk_actions(batch), raise_on_error=False)
                indexed += ok
                errors += len(errs)
                conn.execute(
                    "UPDATE staging_processos SET indexed_at = NOW(), last_indexed_hash = hash "
                    "WHERE numero_processo = ANY(%s)",
                    (processed_ids,),
                )
                conn.commit()

        finish_run(
            run_id, status="success" if errors == 0 else "error",
            docs_processed=indexed + errors,
            docs_inserted=indexed,
            docs_errors=errors,
            duration_s=round(time.time() - t0, 2),
        )
        log.info("bot_indexador.done", indexed=indexed, errors=errors)

    except Exception as exc:
        finish_run(run_id, status="error", error_message=str(exc))
        log.error("bot_indexador.error", error=str(exc))
        raise


if __name__ == "__main__":
    main()
