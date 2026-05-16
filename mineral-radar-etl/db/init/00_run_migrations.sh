#!/bin/bash
# Executado automaticamente pelo container postgres na 1ª inicialização.
# Roda as migrations em ordem numérica.
set -e

MIGRATIONS_DIR="/migrations"

echo "=== MineralRadar ETL — Inicializando schema PostgreSQL + PostGIS ==="

for sql_file in "$MIGRATIONS_DIR"/0*.sql; do
    echo "  → Executando: $(basename "$sql_file")"
    psql -v ON_ERROR_STOP=1 \
         --username "$POSTGRES_USER" \
         --dbname   "$POSTGRES_DB" \
         --file     "$sql_file"
done

echo "=== Schema inicializado com sucesso ==="
