-- =============================================================================
-- 005_indexes.sql
-- Índices de performance para queries frequentes do ETL e do bot_indexador.
-- Executado após os dados serem carregados (não no schema vazio).
-- =============================================================================

-- ── raw_anm_shapes ───────────────────────────────────────────────────────────
-- Índice GIST para ST_Intersects (sobreposições geográficas)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_anm_shapes_geom
    ON raw_anm_shapes USING GIST (geom);

-- Busca por UF + fase para filtros parciais
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_anm_shapes_uf_fase
    ON raw_anm_shapes (uf, fase);

-- Índice de reindexação incremental
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_anm_shapes_indexed_at
    ON raw_anm_shapes (indexed_at NULLS FIRST);

-- ── raw_anm_processos ────────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_anm_processos_cnpj_basico
    ON raw_anm_processos (cnpj_titular_basico)
    WHERE cnpj_titular_basico IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_anm_processos_uf
    ON raw_anm_processos (uf);

-- Busca por nome do titular com trigrama (similar_to / similarity)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_anm_processos_nm_titular_trgm
    ON raw_anm_processos USING GIN (nm_titular gin_trgm_ops);

-- ── Sobreposições ANM × FUNAI (query mais pesada — GIST composto) ────────────
-- Esse índice acelera o INSERT INTO staging_restricoes_geo (cálculo diário)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_funai_ti_geom_gist
    ON raw_funai_ti USING GIST (geom);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ibama_uc_geom_gist
    ON raw_ibama_uc USING GIST (geom);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ibge_biomas_geom_gist
    ON raw_ibge_biomas USING GIST (geom);

-- ── staging_processos ────────────────────────────────────────────────────────
-- Índice parcial: documentos que precisam ser (re)indexados
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_staging_proc_reindex_partial
    ON staging_processos (updated_at DESC)
    WHERE needs_reindex = TRUE;

-- Índice para queries por substância estratégica (array GIN)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_staging_proc_categorias_gin
    ON staging_processos USING GIN (categorias_estrategicas);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_staging_proc_substancias_gin
    ON staging_processos USING GIN (substancias);

-- ── etl_run_log ──────────────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_etl_run_log_run_id
    ON etl_run_log (run_id);
