-- =============================================================================
-- 003_staging_schema.sql
-- Tabelas staging_* — dados transformados e prontos para indexação no OpenSearch.
-- Populadas via queries SQL / PostGIS a partir das tabelas raw_*.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. CNPJs relevantes para filtrar o bulk da Receita Federal
--    Populada por scripts/compute_cnpj_filter.py ANTES do bot_rfb.py
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS staging_cnpjs_relevantes (
    cnpj_basico     CHAR(8)     NOT NULL PRIMARY KEY,
    criterio        TEXT        NOT NULL,   -- 'titular_anm' | 'cnae_extrativa' | 'cfem_top' | 'socio_pj'
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE staging_cnpjs_relevantes IS
    'Lista de CNPJ-básicos relevantes para o domínio mineral. '
    'Pré-filtro aplicado antes do bot_rfb.py para reduzir 221M → ~350K.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Sobreposições geográficas (ANM × TI × UC × Bioma)
--    Calculadas pelo PostGIS via ST_Intersects + ST_Area(ST_Intersection)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS staging_restricoes_geo (
    id              BIGSERIAL   PRIMARY KEY,

    numero_processo TEXT        NOT NULL,
    tipo_restricao  TEXT        NOT NULL
                    CHECK (tipo_restricao IN (
                        'terra_indigena',
                        'unidade_conservacao',
                        'bioma_amazonia',
                        'bioma_cerrado',
                        'bioma_outro'
                    )),
    id_restricao    TEXT        NOT NULL,   -- id_ti | id_uc | id_bioma
    nome_restricao  TEXT,

    area_processo_ha       NUMERIC(18,4),
    area_sobreposta_ha     NUMERIC(18,4),
    pct_processo_sobreposto NUMERIC(6,2),   -- % da área do processo coberta pela restrição

    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (numero_processo, tipo_restricao, id_restricao)
);

CREATE INDEX IF NOT EXISTS idx_restricoes_geo_processo
    ON staging_restricoes_geo (numero_processo);
CREATE INDEX IF NOT EXISTS idx_restricoes_geo_tipo
    ON staging_restricoes_geo (tipo_restricao);

COMMENT ON TABLE staging_restricoes_geo IS
    'Sobreposições pré-computadas no PostGIS entre processos ANM e restrições '
    '(TIs, UCs, biomas). Campo pré-computado indexado no OpenSearch — sem custo em query time.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Processos enriquecidos (join ANM + CFEM + RAL + RFB)
--    Tabela desnormalizada, atualizada incrementalmente pelo bot_anm.py
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS staging_processos (
    numero_processo     TEXT        NOT NULL PRIMARY KEY,

    -- dados ANM (shapes + SCM)
    ativo               BOOLEAN     NOT NULL DEFAULT TRUE,
    fase                TEXT,
    situacao            TEXT,
    substancias         TEXT[],
    substancias_desc    TEXT[],
    area_ha             NUMERIC(18,4),
    uf                  CHAR(2),
    municipio           TEXT,
    cod_ibge_municipio  TEXT,
    dt_requerimento     DATE,
    dt_validade         DATE,
    nm_titular          TEXT,
    cnpj_titular_basico CHAR(8),

    -- classificação estratégica (calculada pelo classificador de substâncias)
    categorias_estrategicas TEXT[],  -- ['terra_rara', 'litio', 'niobio', ...]
    prioridade_estrategica   SMALLINT DEFAULT 0,  -- 0-10

    -- CFEM (série histórica agregada)
    cfem_total_historico    NUMERIC(18,2),
    cfem_ultimo_ano         NUMERIC(18,2),
    cfem_anos_producao      SMALLINT,
    cfem_ultima_arrecadacao DATE,

    -- RAL (último ano de produção declarada)
    ral_ultimo_ano_base     SMALLINT,
    ral_qtd_bruta           NUMERIC(18,4),
    ral_unidade             TEXT,

    -- restrições geo (contagens — detalhes em staging_restricoes_geo)
    n_restricoes_ti         SMALLINT DEFAULT 0,
    n_restricoes_uc         SMALLINT DEFAULT 0,
    n_restricoes_bioma      SMALLINT DEFAULT 0,
    area_sobreposta_ti_ha   NUMERIC(18,4),
    area_sobreposta_uc_ha   NUMERIC(18,4),

    -- geometria centroide (para queries geo simples no OpenSearch)
    centroide               GEOMETRY(POINT, 4326),

    -- controle ETL
    hash                    TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    indexed_at              TIMESTAMPTZ,
    last_indexed_hash       TEXT,

    -- flag de reindexação (TRUE quando hash != last_indexed_hash)
    needs_reindex           BOOLEAN GENERATED ALWAYS AS (
                                hash IS DISTINCT FROM last_indexed_hash
                                OR indexed_at IS NULL
                            ) STORED
);

CREATE INDEX IF NOT EXISTS idx_staging_proc_needs_reindex
    ON staging_processos (needs_reindex) WHERE needs_reindex = TRUE;
CREATE INDEX IF NOT EXISTS idx_staging_proc_ativo
    ON staging_processos (ativo);
CREATE INDEX IF NOT EXISTS idx_staging_proc_cnpj
    ON staging_processos (cnpj_titular_basico);
CREATE INDEX IF NOT EXISTS idx_staging_proc_uf
    ON staging_processos (uf);
CREATE INDEX IF NOT EXISTS idx_staging_proc_centroide
    ON staging_processos USING GIST (centroide);

COMMENT ON TABLE staging_processos IS
    'Processos ANM desnormalizados, prontos para indexação no OpenSearch. '
    'Campo needs_reindex = TRUE indica que o hash mudou desde a última indexação.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Ocorrências CPRM enriquecidas
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS staging_ocorrencias (
    id_ocorrencia           TEXT        NOT NULL PRIMARY KEY,
    nome                    TEXT,
    tipo_deposito           TEXT,
    substancias             TEXT[],
    categorias_estrategicas TEXT[],

    municipio               TEXT,
    uf                      CHAR(2),
    geom                    GEOMETRY(POINT, 4326),

    -- número de processos ANM no raio de 10km
    n_processos_prox        SMALLINT,

    hash                    TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    indexed_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_staging_ocorrencias_geom
    ON staging_ocorrencias USING GIST (geom);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Mercado mineral (ComexStat filtrado por NCMs minerais)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS staging_mercado (
    id              BIGSERIAL   PRIMARY KEY,
    ano             SMALLINT    NOT NULL,
    mes             SMALLINT    NOT NULL,
    tipo            CHAR(3)     NOT NULL,  -- 'EXP' | 'IMP'

    ncm             TEXT        NOT NULL,
    substancia_mineral TEXT,               -- mapeamento NCM → nome mineral
    categoria_mineral  TEXT,               -- 'terra_rara' | 'litio' | 'niobio'...

    pais            TEXT,
    uf              CHAR(2),

    kg_liquido      BIGINT,
    valor_fob_usd   NUMERIC(18,2),
    preco_medio_kg  NUMERIC(10,4),         -- valor_fob / kg_liquido

    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (ano, mes, tipo, ncm, pais, uf)
);

CREATE INDEX IF NOT EXISTS idx_mercado_substancia ON staging_mercado (substancia_mineral);
CREATE INDEX IF NOT EXISTS idx_mercado_ano ON staging_mercado (ano, mes);

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Métricas de cobertura ETL (monitoramento de qualidade)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS etl_coverage_metrics (
    id          BIGSERIAL   PRIMARY KEY,
    run_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metric      TEXT        NOT NULL,
    value       NUMERIC,
    meta        JSONB
);

CREATE INDEX IF NOT EXISTS idx_coverage_metric ON etl_coverage_metrics (metric, run_at DESC);
