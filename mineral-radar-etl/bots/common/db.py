"""
Utilitários de conexão PostgreSQL (psycopg3) e helpers de upsert.
"""
from __future__ import annotations

import contextlib
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from .settings import settings


# ─────────────────────────────────────────────────────────────────────────────
# Conexão simples (síncrona)
# ─────────────────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    """Context manager que entrega uma conexão PostgreSQL com autocommit=False."""
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def execute(sql: str, params: Any = None) -> None:
    """Executa uma query sem retorno (DDL, INSERT, UPDATE, DELETE)."""
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()


def fetch_all(sql: str, params: Any = None) -> list[dict]:
    """Retorna todas as linhas de uma query como lista de dicts."""
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql: str, params: Any = None) -> dict | None:
    """Retorna uma linha ou None."""
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()


# ─────────────────────────────────────────────────────────────────────────────
# Registro de execução de bot no etl_run_log
# ─────────────────────────────────────────────────────────────────────────────

def start_run(bot_name: str, source_file: str | None = None) -> int:
    """Registra o início de uma execução. Retorna o ID do run_log."""
    row = fetch_one(
        """
        INSERT INTO etl_run_log (bot_name, source_file, status)
        VALUES (%s, %s, 'running')
        RETURNING id
        """,
        (bot_name, source_file),
    )
    return row["id"]  # type: ignore[index]


def finish_run(
    run_id: int,
    status: str = "success",
    docs_processed: int = 0,
    docs_inserted: int = 0,
    docs_updated: int = 0,
    docs_errors: int = 0,
    duration_s: float | None = None,
    error_message: str | None = None,
) -> None:
    """Atualiza o registro do run_log ao final da execução."""
    execute(
        """
        UPDATE etl_run_log
        SET status         = %s,
            finished_at    = NOW(),
            docs_processed = %s,
            docs_inserted  = %s,
            docs_updated   = %s,
            docs_errors    = %s,
            duration_s     = %s,
            error_message  = %s
        WHERE id = %s
        """,
        (
            status, docs_processed, docs_inserted,
            docs_updated, docs_errors, duration_s,
            error_message, run_id,
        ),
    )
