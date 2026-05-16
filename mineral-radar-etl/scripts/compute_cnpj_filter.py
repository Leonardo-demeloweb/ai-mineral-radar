"""
compute_cnpj_filter.py

Calcula o conjunto de CNPJs relevantes para o domínio mineral,
a partir das tabelas raw_* já ingeridas.

Deve ser executado APÓS bot_anm + bot_cfem e ANTES de bot_rfb.

Critérios (ver SPEC_ETL_MINERALRADAR.md §9):
  1. Titulares de processos ANM (raw_anm_processos)
  2. CNAEs de Indústrias Extrativas — seção B (05xx–09xx)
  3. Maiores arrecadadores CFEM histórico (raw_anm_cfem)
  4. Sócios PJ de 1º nível das empresas dos critérios 1–3

Salva resultado em staging_cnpjs_relevantes.

Uso:
  python scripts/compute_cnpj_filter.py
  python scripts/compute_cnpj_filter.py --top-cfem 5000
"""
from __future__ import annotations

import click

from bots.common.db import get_conn, fetch_all
from bots.common.logging import get_logger

log = get_logger(__name__)

EXTRATIVA_CNAES = tuple(f"{i:02d}" for i in range(5, 10))  # '05' a '09'


@click.command()
@click.option("--top-cfem", default=10_000, show_default=True,
              help="Número de top arrecadadores CFEM para incluir no filtro")
def main(top_cfem: int) -> None:
    """Popula staging_cnpjs_relevantes com os CNPJs relevantes para mineração."""

    log.info("cnpj_filter.start")

    with get_conn() as conn:
        # ── Critério 1: titulares ANM ──────────────────────────────────────
        rows_anm = conn.execute(
            """
            SELECT DISTINCT cnpj_titular_basico AS cnpj_basico
            FROM raw_anm_processos
            WHERE cnpj_titular_basico IS NOT NULL
              AND length(cnpj_titular_basico) = 8
            """
        ).fetchall()
        cnpjs_anm = {r["cnpj_basico"] for r in rows_anm}
        log.info("criterio_1.titular_anm", total=len(cnpjs_anm))

        # ── Critério 2: CNAEs extrativos (lido do bulk RFB se já carregado) ─
        # Nota: se raw_rfb_estabelecimentos ainda está vazio, este critério
        # será populado no próximo refresh mensal da RFB.
        rows_cnae = conn.execute(
            """
            SELECT DISTINCT cnpj_basico
            FROM raw_rfb_estabelecimentos
            WHERE left(cnae_principal, 2) = ANY(%s::text[])
            """,
            (list(EXTRATIVA_CNAES),),
        ).fetchall()
        cnpjs_cnae = {r["cnpj_basico"] for r in rows_cnae}
        log.info("criterio_2.cnae_extrativa", total=len(cnpjs_cnae))

        # ── Critério 3: top arrecadadores CFEM ────────────────────────────
        rows_cfem = conn.execute(
            """
            SELECT cnpj_basico
            FROM (
                SELECT cnpj_basico, SUM(valor_cfem) AS total
                FROM raw_anm_cfem
                WHERE cnpj_basico IS NOT NULL AND length(cnpj_basico) = 8
                GROUP BY cnpj_basico
                ORDER BY total DESC
                LIMIT %s
            ) t
            """,
            (top_cfem,),
        ).fetchall()
        cnpjs_cfem = {r["cnpj_basico"] for r in rows_cfem}
        log.info("criterio_3.cfem_top", total=len(cnpjs_cfem))

        cnpjs_base = cnpjs_anm | cnpjs_cnae | cnpjs_cfem
        log.info("cnpjs_base", total=len(cnpjs_base))

        # ── Critério 4: sócios PJ de 1º nível ────────────────────────────
        if cnpjs_base:
            rows_socios = conn.execute(
                """
                SELECT DISTINCT cnpj_basico_socio_pj AS cnpj_basico
                FROM raw_rfb_socios
                WHERE cnpj_basico = ANY(%s::char(8)[])
                  AND cnpj_basico_socio_pj IS NOT NULL
                """,
                (list(cnpjs_base),),
            ).fetchall()
            cnpjs_socios = {r["cnpj_basico"] for r in rows_socios}
            log.info("criterio_4.socios_pj", total=len(cnpjs_socios))
        else:
            cnpjs_socios = set()

        cnpjs_final = cnpjs_base | cnpjs_socios
        log.info("cnpjs_final", total=len(cnpjs_final))

        # ── Upsert em staging_cnpjs_relevantes ───────────────────────────
        criterio_map = (
            [(c, "titular_anm")     for c in cnpjs_anm]
            + [(c, "cnae_extrativa") for c in cnpjs_cnae - cnpjs_anm]
            + [(c, "cfem_top")       for c in cnpjs_cfem - cnpjs_anm - cnpjs_cnae]
            + [(c, "socio_pj")       for c in cnpjs_socios - cnpjs_base]
        )

        conn.executemany(
            """
            INSERT INTO staging_cnpjs_relevantes (cnpj_basico, criterio, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (cnpj_basico) DO UPDATE
                SET criterio   = EXCLUDED.criterio,
                    updated_at = NOW()
            """,
            criterio_map,
        )
        conn.commit()

    log.info("cnpj_filter.done", total_saved=len(cnpjs_final))


if __name__ == "__main__":
    main()
