-- =============================================================================
-- 001_extensions.sql
-- Habilita extensões PostgreSQL necessárias para o MineralRadar ETL
-- =============================================================================

-- PostGIS: geometrias, índices GIST, ST_Intersects, ST_Area, ST_Distance...
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- UUID geração nativa
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Busca textual com trigramas (similaridade de nomes de titulares, municípios)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- btree_gist: índices compostos (btree + GIST) para buscas geo + filtros
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- hstore: pares chave-valor para metadados semi-estruturados
CREATE EXTENSION IF NOT EXISTS hstore;

-- Verifica versão do PostGIS instalada
DO $$
BEGIN
    RAISE NOTICE 'PostGIS version: %', PostGIS_Full_Version();
END $$;
