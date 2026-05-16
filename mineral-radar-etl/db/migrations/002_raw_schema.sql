-- =============================================================================
-- 002_raw_schema.sql
-- Tabelas raw_* — dados brutos ingeridos pelos bots, sem transformação de negócio.
-- Cada tabela segue o padrão:
--   • chave primária natural da fonte
--   • hash TEXT — xxhash do registro para controle de reindexação incremental
--   • ingested_at  — timestamp da última ingestão
--   • source_file  — arquivo de origem (para rastreabilidade)
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- CONTROLE DE INGESTÃO (compartilhado por todas as tabelas raw_*)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS etl_run_log (
    id             BIGSERIAL PRIMARY KEY,
    bot_name       TEXT        NOT NULL,
    run_id         UUID        NOT NULL DEFAULT uuid_generate_v4(),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ,
    status         TEXT        NOT NULL DEFAULT 'running'  -- running | success | error
                               CHECK (status IN ('running','success','error')),
    docs_processed BIGINT      DEFAULT 0,
    docs_inserted  BIGINT      DEFAULT 0,
    docs_updated   BIGINT      DEFAULT 0,
    docs_errors    BIGINT      DEFAULT 0,
    duration_s     NUMERIC(10,2),
    source_file    TEXT,
    error_message  TEXT,
    meta           JSONB
);

CREATE INDEX IF NOT EXISTS idx_etl_run_log_bot_name ON etl_run_log (bot_name);
CREATE INDEX IF NOT EXISTS idx_etl_run_log_started  ON etl_run_log (started_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. ANM — SIGMINE — Processos Minerários (geometrias)
--    Fonte: https://dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/{UF}.zip
--           https://dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/BRASIL.zip
--           https://dadosabertos.anm.gov.br/SIGMINE/PROCESSOS_MINERARIOS/PROCESSOS_INATIVOS.zip
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_anm_shapes (
    -- chave primária ANM
    numero_processo TEXT        NOT NULL,          -- ex: "860.136/1977"
    uf              CHAR(2)     NOT NULL,

    -- atributos do Shapefile SIGMINE
    nome            TEXT,
    fase            TEXT,                          -- "Requerimento de Pesquisa", "Concessão de Lavra"...
    sub             TEXT,                          -- substância principal (código ANM)
    area_ha         NUMERIC(18,4),
    ano             SMALLINT,
    ultimo_evento   TEXT,
    prioridade      TEXT,
    uso_solo        TEXT,
    subs_desc       TEXT,                          -- descrição da substância

    -- flag ativo/inativo (campo estratégico para filtro padrão)
    ativo           BOOLEAN     NOT NULL DEFAULT TRUE,

    -- geometria (EPSG:4326 — WGS84, reprojetada no bot)
    geom            GEOMETRY(MULTIPOLYGON, 4326),

    -- controle ETL
    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    indexed_at      TIMESTAMPTZ,                   -- preenchido pelo bot_indexador após indexar no OpenSearch
    last_indexed_hash TEXT,
    source_file     TEXT,

    PRIMARY KEY (numero_processo)
);

COMMENT ON TABLE raw_anm_shapes IS
    'Geometrias dos processos minerários ANM (SIGMINE). Ativos (~600K) + Inativos (~24M). '
    'Reprojetados para WGS84 (EPSG:4326) pelo bot_anm.py.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. ANM — Cadastro Mineiro SCM (dados tabulares relacionais)
--    Fonte: https://dadosabertos.anm.gov.br/SCM/microdados/microdados-scm.zip
--    Contém múltiplas tabelas; aqui modelamos a principal (processo)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_anm_processos (
    -- identificadores
    numero_processo     TEXT        NOT NULL,
    ds_processo         TEXT,                      -- descrição/nome do processo
    id_processo_anm     TEXT,                      -- ID interno ANM

    -- titular
    nm_titular          TEXT,
    cpf_cnpj_titular    TEXT,
    cnpj_titular_basico CHAR(8),                   -- 8 primeiros dígitos do CNPJ (para join com RFB)
    nm_pessoa_fisica    TEXT,

    -- localização
    uf                  CHAR(2),
    municipio           TEXT,
    cod_ibge_municipio  TEXT,

    -- substâncias (pode haver múltiplas — array ou JSON)
    substancias         TEXT[],                    -- array de códigos ANM
    uso_solo            TEXT,
    fase                TEXT,
    situacao            TEXT,

    -- datas
    dt_requerimento     DATE,
    dt_protocolo        DATE,
    dt_validade         DATE,
    dt_vigencia         DATE,
    dt_publicacao_dou   DATE,
    dt_ultimo_evento    TEXT,

    -- área
    area_ha             NUMERIC(18,4),

    -- controle ETL
    hash                TEXT,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    indexed_at          TIMESTAMPTZ,
    last_indexed_hash   TEXT,
    source_file         TEXT,

    PRIMARY KEY (numero_processo)
);

COMMENT ON TABLE raw_anm_processos IS
    'Dados tabulares do Cadastro Mineiro ANM (SCM microdados). '
    'Join com raw_anm_shapes via numero_processo.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. ANM — CFEM (Compensação Financeira pela Exploração Mineral)
--    Fonte: https://dadosabertos.anm.gov.br/CFEM/CFEM_Arrecadacao.csv (~221MB)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_anm_cfem (
    id              BIGSERIAL   PRIMARY KEY,
    ano             SMALLINT    NOT NULL,
    mes             SMALLINT    NOT NULL,

    numero_processo TEXT,
    cnpj_empresa    TEXT,
    cnpj_basico     CHAR(8),
    nm_empresa      TEXT,

    uf              CHAR(2),
    municipio       TEXT,
    cod_ibge        TEXT,

    substancia      TEXT,
    unidade         TEXT,
    quantidade      NUMERIC(18,4),
    valor_cfem      NUMERIC(18,2),
    aliquota        NUMERIC(6,4),

    dt_arrecadacao  DATE,

    -- controle ETL
    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     TEXT,

    UNIQUE (ano, mes, numero_processo, cnpj_empresa, substancia)
);

CREATE INDEX IF NOT EXISTS idx_cfem_numero_processo ON raw_anm_cfem (numero_processo);
CREATE INDEX IF NOT EXISTS idx_cfem_cnpj_basico     ON raw_anm_cfem (cnpj_basico);
CREATE INDEX IF NOT EXISTS idx_cfem_ano_mes         ON raw_anm_cfem (ano, mes);

COMMENT ON TABLE raw_anm_cfem IS
    'Arrecadação CFEM por substância, empresa e município. Série histórica desde 2010.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. ANM — RAL (Relatório Anual de Lavra / Produção)
--    Fonte: dados.gov.br → "Anuário Mineral / RAL"
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_anm_ral (
    id              BIGSERIAL   PRIMARY KEY,
    ano_base        SMALLINT    NOT NULL,
    numero_processo TEXT,
    cnpj_empresa    TEXT,
    cnpj_basico     CHAR(8),
    nm_empresa      TEXT,

    substancia      TEXT,
    unidade         TEXT,
    qtd_bruta       NUMERIC(18,4),
    qtd_beneficiada NUMERIC(18,4),
    qtd_agua_mineral NUMERIC(18,4),

    uf              CHAR(2),
    municipio       TEXT,

    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     TEXT,

    UNIQUE (ano_base, numero_processo, substancia)
);

COMMENT ON TABLE raw_anm_ral IS
    'Produção declarada por empresa no Relatório Anual de Lavra (RAL).';

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. CPRM — Ocorrências Minerais (GeoBank)
--    Fonte: geoportal.sgb.gov.br (WFS/GeoJSON)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_cprm_ocorrencias (
    id_ocorrencia   TEXT        NOT NULL PRIMARY KEY,  -- ID interno CPRM/GeoBank

    nome            TEXT,
    tipo_deposito   TEXT,
    substancias     TEXT[],
    teor            TEXT,
    sigla_mineral   TEXT,

    municipio       TEXT,
    uf              CHAR(2),
    cod_ibge        TEXT,

    -- ponto (latitude/longitude) — EPSG:4326
    geom            GEOMETRY(POINT, 4326),

    referencia_bibliografica TEXT,
    observacoes     TEXT,

    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     TEXT
);

CREATE INDEX IF NOT EXISTS idx_cprm_ocorrencias_geom
    ON raw_cprm_ocorrencias USING GIST (geom);

COMMENT ON TABLE raw_cprm_ocorrencias IS
    'Ocorrências minerais do GeoBank (CPRM/SGB). ~50K pontos georreferenciados.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. FUNAI — Terras Indígenas
--    Fonte: gov.br/funai (Shapefile/GeoJSON mensal)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_funai_ti (
    id_ti           TEXT        NOT NULL PRIMARY KEY,  -- código FUNAI

    nome_ti         TEXT,
    etnia           TEXT,
    municipio       TEXT,
    uf              CHAR(2),

    fase_demarcacao TEXT,   -- "Homologada", "Declarada", "Delimitada", "Em Estudo"
    modalidade      TEXT,
    area_ha         NUMERIC(18,2),
    perimetro_km    NUMERIC(10,2),

    dt_homologacao  DATE,
    dt_portaria     DATE,

    -- polígono (EPSG:4326)
    geom            GEOMETRY(MULTIPOLYGON, 4326),

    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     TEXT
);

CREATE INDEX IF NOT EXISTS idx_funai_ti_geom
    ON raw_funai_ti USING GIST (geom);

COMMENT ON TABLE raw_funai_ti IS
    'Terras Indígenas FUNAI. Principal restrição jurídica para processos minerários '
    '(CF/88 art. 231 §3º — proibição de mineração sem lei complementar).';

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. IBAMA — Unidades de Conservação (CNUC)
--    Fonte: dados.mma.gov.br (Shapefile mensal)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_ibama_uc (
    id_uc           TEXT        NOT NULL PRIMARY KEY,

    nome_uc         TEXT,
    categoria       TEXT,   -- "PARNA", "APA", "REBIO", "ESEC", "RPPN"...
    grupo           TEXT,   -- "Proteção Integral" | "Uso Sustentável"
    esfera          TEXT,   -- "Federal" | "Estadual" | "Municipal"

    municipio       TEXT,
    uf              CHAR(2),
    bioma           TEXT,

    area_ha         NUMERIC(18,2),
    dt_criacao      DATE,
    ato_legal       TEXT,

    -- polígono (EPSG:4326)
    geom            GEOMETRY(MULTIPOLYGON, 4326),

    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     TEXT
);

CREATE INDEX IF NOT EXISTS idx_ibama_uc_geom
    ON raw_ibama_uc USING GIST (geom);

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. IBAMA — Autuações e Embargos
--    Fonte: dados.gov.br → "Autuações IBAMA"
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_ibama_autucoes (
    id_autuacao     TEXT        NOT NULL PRIMARY KEY,

    cpf_cnpj        TEXT,
    cnpj_basico     CHAR(8),
    nm_autuado      TEXT,

    tipo_autuacao   TEXT,
    descricao       TEXT,
    valor_multa     NUMERIC(18,2),
    status          TEXT,

    municipio       TEXT,
    uf              CHAR(2),
    dt_autuacao     DATE,
    dt_embargo      DATE,

    numero_ai       TEXT,       -- número do auto de infração

    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     TEXT
);

CREATE INDEX IF NOT EXISTS idx_ibama_autucoes_cnpj_basico
    ON raw_ibama_autucoes (cnpj_basico);

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. RFB — Empresas CNPJ (bulk Receita Federal — pré-filtrado ~350K CNPJs)
--    Fonte: dados.rfb.gov.br (bulk mensal ~40GB total, filtro aplicado pelo bot)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_rfb_estabelecimentos (
    cnpj_completo   CHAR(14)    NOT NULL PRIMARY KEY, -- CNPJ 14 dígitos sem formatação
    cnpj_basico     CHAR(8)     NOT NULL,
    cnpj_ordem      CHAR(4),
    cnpj_dv         CHAR(2),

    razao_social    TEXT,
    nome_fantasia   TEXT,
    situacao_cadastral SMALLINT,
    dt_situacao     DATE,
    motivo_situacao SMALLINT,

    nm_cidade_exterior TEXT,
    cod_pais        SMALLINT,
    nm_pais         TEXT,

    cnae_principal  TEXT,                             -- código CNAE 7 dígitos
    cnaes_secundarios TEXT[],

    tipo_logradouro TEXT,
    logradouro      TEXT,
    numero          TEXT,
    complemento     TEXT,
    bairro          TEXT,
    cep             TEXT,
    uf              CHAR(2),
    municipio       TEXT,
    cod_ibge_municipio TEXT,

    ddd1            TEXT,
    telefone1       TEXT,
    ddd2            TEXT,
    telefone2       TEXT,
    email           TEXT,

    porte           SMALLINT,
    opcao_simples   BOOLEAN,
    dt_opcao_simples DATE,
    dt_excl_simples  DATE,

    capital_social  NUMERIC(18,2),
    dt_inicio_atividades DATE,
    dt_situacao_especial DATE,
    situacao_especial TEXT,

    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     TEXT
);

CREATE INDEX IF NOT EXISTS idx_rfb_estab_cnpj_basico
    ON raw_rfb_estabelecimentos (cnpj_basico);
CREATE INDEX IF NOT EXISTS idx_rfb_estab_cnae_principal
    ON raw_rfb_estabelecimentos (cnae_principal);

-- Sócios
CREATE TABLE IF NOT EXISTS raw_rfb_socios (
    id              BIGSERIAL   PRIMARY KEY,
    cnpj_basico     CHAR(8)     NOT NULL,
    tipo_socio      SMALLINT,                 -- 1=PF, 2=PJ, 3=Estrangeiro
    nm_socio        TEXT,
    cpf_cnpj_socio  TEXT,
    cnpj_basico_socio_pj CHAR(8),            -- preenchido quando tipo_socio=2
    cod_qualificacao SMALLINT,
    dt_entrada_sociedade DATE,
    pais_socio_estrangeiro TEXT,
    percentual_capital NUMERIC(5,2),

    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     TEXT
);

CREATE INDEX IF NOT EXISTS idx_rfb_socios_cnpj_basico
    ON raw_rfb_socios (cnpj_basico);
CREATE INDEX IF NOT EXISTS idx_rfb_socios_cnpj_basico_socio_pj
    ON raw_rfb_socios (cnpj_basico_socio_pj)
    WHERE cnpj_basico_socio_pj IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. ComexStat MDIC — Exportações e Importações por NCM
--     Fonte: api-comexstat.mdic.gov.br + CSV bulk mensal
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_mdic_comex (
    id              BIGSERIAL   PRIMARY KEY,
    ano             SMALLINT    NOT NULL,
    mes             SMALLINT    NOT NULL,
    tipo            CHAR(3)     NOT NULL CHECK (tipo IN ('EXP','IMP')),

    ncm             TEXT        NOT NULL,    -- 8 dígitos NCM
    ncm_descricao   TEXT,
    sh4             TEXT,
    sh2             TEXT,

    pais_destino_origem TEXT,
    cod_pais        SMALLINT,
    uf              CHAR(2),
    via_transporte  TEXT,

    kg_liquido      BIGINT,
    valor_fob       NUMERIC(18,2),      -- US$ FOB

    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     TEXT,

    UNIQUE (ano, mes, tipo, ncm, pais_destino_origem, uf)
);

CREATE INDEX IF NOT EXISTS idx_comex_ncm ON raw_mdic_comex (ncm);
CREATE INDEX IF NOT EXISTS idx_comex_ano_mes ON raw_mdic_comex (ano, mes);

COMMENT ON TABLE raw_mdic_comex IS
    'Exportações/importações mensais por NCM (MDIC ComexStat). '
    'Filtrado para NCMs minerais relevantes (terras raras, lítio, nióbio, cobalto, etc.).';

-- ─────────────────────────────────────────────────────────────────────────────
-- 11. IBGE — Municípios com polígonos
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_ibge_municipios (
    cod_ibge        CHAR(7)     NOT NULL PRIMARY KEY,
    nome            TEXT        NOT NULL,
    uf              CHAR(2)     NOT NULL,
    regiao          TEXT,

    area_km2        NUMERIC(12,2),
    populacao       BIGINT,                  -- último censo disponível

    -- polígono e centroide (EPSG:4326)
    geom            GEOMETRY(MULTIPOLYGON, 4326),
    centroide       GEOMETRY(POINT, 4326),

    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     TEXT
);

CREATE INDEX IF NOT EXISTS idx_ibge_municipios_geom
    ON raw_ibge_municipios USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_ibge_municipios_uf
    ON raw_ibge_municipios (uf);

-- ─────────────────────────────────────────────────────────────────────────────
-- 12. IBGE — Biomas
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_ibge_biomas (
    id_bioma        SERIAL      PRIMARY KEY,
    nome_bioma      TEXT        NOT NULL UNIQUE,
    area_km2        NUMERIC(14,2),
    geom            GEOMETRY(MULTIPOLYGON, 4326),
    hash            TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ibge_biomas_geom
    ON raw_ibge_biomas USING GIST (geom);
