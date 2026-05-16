-- =============================================================================
-- 004_views.sql
-- Views e Materialized Views consumidas pelo bot_indexador.py
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- View principal: vw_processos_completo
-- Consumida pelo bot_indexador.py via: SELECT * FROM vw_processos_completo
--                                      WHERE needs_reindex = TRUE
-- Produz o documento completo do processo para indexação no OpenSearch.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vw_processos_completo AS
SELECT
    -- identificação
    sp.numero_processo,
    sp.ativo,
    sp.fase,
    sp.situacao,

    -- substâncias e classificação estratégica
    sp.substancias,
    sp.substancias_desc,
    sp.categorias_estrategicas,
    sp.prioridade_estrategica,

    -- localização
    sp.uf,
    sp.municipio,
    sp.cod_ibge_municipio,
    mun.nome          AS municipio_nome_ibge,
    mun.regiao        AS regiao,

    -- área
    sp.area_ha,

    -- titular (enriquecido com dados RFB)
    sp.nm_titular,
    sp.cnpj_titular_basico,
    rfb.razao_social  AS titular_razao_social,
    rfb.situacao_cadastral AS titular_situacao_rfb,
    rfb.cnae_principal AS titular_cnae_principal,
    rfb.porte         AS titular_porte,

    -- datas ANM
    sp.dt_requerimento,
    sp.dt_validade,

    -- CFEM
    sp.cfem_total_historico,
    sp.cfem_ultimo_ano,
    sp.cfem_anos_producao,
    sp.cfem_ultima_arrecadacao,

    -- RAL
    sp.ral_ultimo_ano_base,
    sp.ral_qtd_bruta,
    sp.ral_unidade,

    -- restrições geográficas (resumo)
    sp.n_restricoes_ti,
    sp.n_restricoes_uc,
    sp.n_restricoes_bioma,
    sp.area_sobreposta_ti_ha,
    sp.area_sobreposta_uc_ha,

    -- restrições detalhadas (agregadas como JSON array)
    COALESCE(
        (SELECT json_agg(json_build_object(
            'tipo',        rg.tipo_restricao,
            'id',          rg.id_restricao,
            'nome',        rg.nome_restricao,
            'area_ha',     rg.area_sobreposta_ha,
            'pct',         rg.pct_processo_sobreposto
        ))
        FROM staging_restricoes_geo rg
        WHERE rg.numero_processo = sp.numero_processo),
        '[]'::json
    ) AS restricoes_geo,

    -- geometria centroide (para geo_point no OpenSearch)
    ST_Y(sp.centroide) AS lat,
    ST_X(sp.centroide) AS lon,

    -- geometria completa (para geo_shape no OpenSearch)
    ST_AsGeoJSON(sh.geom)::json AS geom_geojson,

    -- controle de reindexação
    sp.hash,
    sp.last_indexed_hash,
    sp.indexed_at,
    sp.needs_reindex,
    sp.updated_at

FROM staging_processos sp
LEFT JOIN raw_anm_shapes sh
       ON sh.numero_processo = sp.numero_processo
LEFT JOIN raw_rfb_estabelecimentos rfb
       ON rfb.cnpj_basico = sp.cnpj_titular_basico
      AND rfb.cnpj_ordem = '0001'   -- matriz
LEFT JOIN raw_ibge_municipios mun
       ON mun.cod_ibge = sp.cod_ibge_municipio;

COMMENT ON VIEW vw_processos_completo IS
    'Documento completo do processo ANM para indexação no OpenSearch. '
    'Consumida pelo bot_indexador.py via WHERE needs_reindex = TRUE.';

-- ─────────────────────────────────────────────────────────────────────────────
-- Materialized View: mv_cfem_por_processo
-- Agrega série histórica CFEM por processo (atualizada após bot_cfem.py)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_cfem_por_processo AS
SELECT
    numero_processo,
    SUM(valor_cfem)              AS cfem_total_historico,
    SUM(CASE WHEN ano = EXTRACT(YEAR FROM NOW())::int
             THEN valor_cfem ELSE 0 END) AS cfem_ano_corrente,
    MAX(dt_arrecadacao)          AS cfem_ultima_arrecadacao,
    COUNT(DISTINCT ano)          AS cfem_anos_producao,
    array_agg(DISTINCT substancia ORDER BY substancia) AS substancias_cfem
FROM raw_anm_cfem
WHERE numero_processo IS NOT NULL
GROUP BY numero_processo
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_cfem_processo
    ON mv_cfem_por_processo (numero_processo);

COMMENT ON MATERIALIZED VIEW mv_cfem_por_processo IS
    'CFEM agregado por processo. Atualizar com: REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cfem_por_processo;';

-- ─────────────────────────────────────────────────────────────────────────────
-- Materialized View: mv_metricas_dashboard
-- Métricas rápidas para monitoramento do ETL (dashboard interno)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_metricas_dashboard AS
SELECT
    'processos_total'            AS metrica,
    COUNT(*)::BIGINT             AS valor
FROM staging_processos
UNION ALL
SELECT 'processos_ativos',       COUNT(*) FROM staging_processos WHERE ativo = TRUE
UNION ALL
SELECT 'processos_inativos',     COUNT(*) FROM staging_processos WHERE ativo = FALSE
UNION ALL
SELECT 'processos_com_restricao_ti', COUNT(DISTINCT numero_processo) FROM staging_restricoes_geo WHERE tipo_restricao = 'terra_indigena'
UNION ALL
SELECT 'processos_com_cfem',     COUNT(*) FROM staging_processos WHERE cfem_total_historico > 0
UNION ALL
SELECT 'processos_estrategicos', COUNT(*) FROM staging_processos WHERE array_length(categorias_estrategicas, 1) > 0
UNION ALL
SELECT 'needs_reindex',          COUNT(*) FROM staging_processos WHERE needs_reindex = TRUE
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_metricas_dashboard_metrica
    ON mv_metricas_dashboard (metrica);
