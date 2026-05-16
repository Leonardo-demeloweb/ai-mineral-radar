"""
Jazidas MCP Tools
==================

Custom tools registered via @mcp.tool() decorators.

Tools:
    1.  buscar_fornecedores       → cross 3 índices, raio circular
    1.b fornecedores_por_poligono → mesmo cross-walk, polígono GeoJSON
    2.  buscar_jazidas            → semântico k-NN + geo
    3.  detalhes_processo         → cross-index CNPJ
    4.  jazidas_por_poligono      → nested geo_shape
    5.  verificar_vigencia        → nested substancias
    6.  consultar_cfem_processo   → CFEM histórico mensal de um processo
    7.  ranking_cfem              → CFEM maiores pagadores por UF/substância/período
    8.  buscar_restricoes_geo     → TIs + UCs sobrepostas a um processo (nível de risco)

Resolução de município (nome → codigo_ibge / centroide / polígono) é
responsabilidade do MCP Geo (tool ``geo.buscar_municipio``).
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.common.redis_cache import RedisCache
from mcp_servers.common.embeddings import EmbeddingService

logger = logging.getLogger("mcp.jazidas.tools")


def _pontos_from_jazida_page(page_items: list[dict]) -> dict:
    """
    Build mapa.pontos directly from the current page of jazida/fornecedor items.

    Guarantees map = chat list (1-to-1). Each item already carries:
    - localizacao (lat/lon from ANM — precise mineral process location)
    - enriched fields: substancias, municipios, uf, titulares, endereco, etc.
    """
    pontos = []
    for item in page_items:
        # Fornecedor items nest everything under "processo"; jazida items are flat.
        proc = item.get("processo") if isinstance(item.get("processo"), dict) else {}

        # location (new schema) or localizacao (legacy) at root or inside processo
        loc = (item.get("location") or item.get("localizacao")
               or proc.get("location") or proc.get("localizacao"))
        if not isinstance(loc, dict):
            continue
        lat = loc.get("lat")
        lon = loc.get("lon")
        if not lat or not lon:
            continue

        # For flat jazida items proc is empty — fall back to item itself
        if not proc:
            proc = item
        ds_processo = proc.get("ds_processo") or item.get("ds_processo", "")
        contato = item.get("contato") or {}

        pontos.append({
            "lat": lat,
            "lon": lon,
            "tipo": "jazida",
            "processo": ds_processo,
            "substancias": item.get("substancias") or proc.get("substancias", []),
            "municipios": item.get("municipios") or proc.get("municipios", []),
            "uf": item.get("uf") or proc.get("uf", []),
            "titulares": item.get("titulares") or proc.get("titulares", []),
            "fase": item.get("fase") or proc.get("fase"),
            "area_ha": item.get("area_ha") or proc.get("area_ha"),
            "distancia_km": item.get("distancia_km") or proc.get("distancia_km"),
            "tipo_requerimento": item.get("tipo_requerimento") or proc.get("tipo_requerimento"),
            # Address fields from CNPJ cross-reference
            "endereco": item.get("endereco") or contato.get("endereco"),
            "telefone": item.get("telefone") or contato.get("telefone"),
            "email": item.get("email") or contato.get("email"),
        })
    return {"pontos": pontos, "total_pontos": len(pontos)}


def _mapa_from_disponibilidade_areas(areas: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Pontos no mesmo formato que ``_pontos_from_jazida_page`` para o SSE
    ``map_data`` (chat.py → ``_extract_mapa_pontos``).
    """
    pontos: list[dict[str, Any]] = []
    for a in areas:
        if not isinstance(a, dict):
            continue
        coord = a.get("coordenada")
        if not isinstance(coord, dict):
            continue
        lat, lon = coord.get("lat"), coord.get("lon")
        if lat is None or lon is None:
            continue
        subs = a.get("substancias_desc") or []
        if not isinstance(subs, list):
            subs = []
        mun = a.get("municipio")
        uf_raw = a.get("uf")
        if isinstance(uf_raw, str) and uf_raw.strip():
            uf_out: list[str] = [uf_raw.strip().upper()]
        elif isinstance(uf_raw, list):
            uf_out = [str(u).strip().upper() for u in uf_raw if u]
        else:
            uf_out = []

        pontos.append({
            "lat": float(lat),
            "lon": float(lon),
            "tipo": "jazida",
            "processo": str(a.get("numero_processo") or ""),
            "substancias": subs,
            "municipios": [mun] if mun else [],
            "uf": uf_out,
            "titulares": [],
            "fase": a.get("fase"),
            "area_ha": a.get("area_ha"),
            "distancia_km": a.get("distancia_km"),
            "tipo_requerimento": None,
        })
    return {"pontos": pontos, "total_pontos": len(pontos)}


def _mapa_from_detalhes_processo(
    processo: dict[str, Any] | None,
    empresa: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Um ponto de mapa alinhado a ``_pontos_from_jazida_page`` para o mesmo
    ``map_data`` / popup de jazida (CNPJ, telefone, e-mail da RFB quando houver).
    """
    if not isinstance(processo, dict):
        return {"pontos": [], "total_pontos": 0}
    loc = processo.get("localizacao")
    if not isinstance(loc, dict):
        return {"pontos": [], "total_pontos": 0}
    lat, lon = loc.get("lat"), loc.get("lon")
    if lat is None or lon is None:
        return {"pontos": [], "total_pontos": 0}

    ds_processo = str(processo.get("ds_processo") or "")
    titulares = processo.get("titulares_nomes") or []
    if not isinstance(titulares, list):
        titulares = []
    subs = processo.get("substancias_nomes") or []
    if not isinstance(subs, list):
        subs = []
    ufs = processo.get("uf") or []
    if not isinstance(ufs, list):
        ufs = []
    municipios = processo.get("municipios_nomes") or []
    if not isinstance(municipios, list):
        municipios = []

    cnpj_titulares: list[str] = []
    if isinstance(empresa, dict):
        cc = empresa.get("cnpj_completo")
        if cc:
            cnpj_titulares.append(str(cc))
    tit = processo.get("titular") if isinstance(processo.get("titular"), dict) else {}
    basico = tit.get("cnpj_basico") if isinstance(tit, dict) else None
    if basico and not cnpj_titulares:
        cnpj_titulares.append(str(basico).strip())

    contato: dict[str, Any] = {}
    if isinstance(empresa, dict):
        contato = empresa.get("contato") if isinstance(empresa.get("contato"), dict) else {}
    telefone = contato.get("telefone") or contato.get("telefone2")
    email = contato.get("email")

    ponto: dict[str, Any] = {
        "lat": float(lat),
        "lon": float(lon),
        "tipo": "jazida",
        "processo": ds_processo,
        "substancias": subs,
        "municipios": municipios,
        "uf": ufs,
        "titulares": titulares,
        "fase": processo.get("fase"),
        "area_ha": processo.get("area_ha"),
        "tipo_requerimento": processo.get("tipo_requerimento"),
        "endereco": contato.get("endereco"),
        "telefone": telefone,
        "email": email,
    }
    if cnpj_titulares:
        ponto["cnpj_titulares"] = cnpj_titulares

    return {"pontos": [ponto], "total_pontos": 1}


def _attach_page_geometry(
    response: dict,
    page_items: list[dict],
    geo_all: dict,
) -> None:
    """
    Filter full geometry collections down to the current page's processes
    and attach them to the tool response.

    Mutates `response` in place, adding:
      - response["geometrias_jazidas"]  — filtered FeatureCollection
      - response["geometrias_municipios"] — full municipality boundaries
    """
    geo_jazidas_all = geo_all.get("geometrias_jazidas")
    geo_municipios = geo_all.get("geometrias_municipios")

    if isinstance(geo_jazidas_all, dict) and geo_jazidas_all.get("features"):
        page_processos: set[str] = set()
        for item in page_items:
            proc = item.get("processo") if isinstance(item.get("processo"), dict) else {}
            ds = (
                item.get("ds_processo")
                or item.get("dsProcesso")
                or proc.get("ds_processo")
                or proc.get("dsProcesso")
                or ""
            )
            if ds:
                page_processos.add(ds)

        filtered = [
            f for f in geo_jazidas_all["features"]
            if f.get("properties", {}).get("processo") in page_processos
        ]
        if filtered:
            response["geometrias_jazidas"] = {
                "type": "FeatureCollection",
                "features": filtered,
            }
            logger.info(
                "_attach_page_geometry: jazidas=%d/%d for page",
                len(filtered), len(geo_jazidas_all["features"]),
            )

    if isinstance(geo_municipios, dict) and geo_municipios.get("features"):
        response["geometrias_municipios"] = geo_municipios
        logger.info(
            "_attach_page_geometry: municipios=%d features",
            len(geo_municipios["features"]),
        )


def register_tools(
    mcp: FastMCP,
    os_service: OpenSearchService,
    redis_cache: RedisCache,
    embedding_service: EmbeddingService,
) -> None:
    """
    Register all Jazidas MCP tools on the server instance.

    This function is called during server startup (server.py).
    Each tool is registered via the @mcp.tool() decorator pattern.

    Args:
        mcp: The FastMCP Server instance
        os_service: OpenSearch async client
        redis_cache: Redis cache (graceful degradation)
        embedding_service: Azure OpenAI embedding generator
    """

    # ------------------------------------------------------------------
    # Shared dependencies available to all tools via closure
    # ------------------------------------------------------------------
    from mcp_servers.jazidas.queries.substancia import SubstanciaResolver
    from mcp_servers.jazidas.cache import JazidasCache

    substancia_resolver = SubstanciaResolver(os_service, embedding_service, redis_cache)
    jazidas_cache = JazidasCache(redis_cache)

    # ==================================================================
    # Tool 1: buscar_fornecedores
    # ==================================================================
    @mcp.tool()
    async def buscar_fornecedores(
        substancia: str,
        latitude: float,
        longitude: float,
        raio_km: float = 50.0,
        uf: str | None = None,
        fase: str | None = None,
        apenas_ativos: bool = False,
        incluir_contatos: bool = True,
        incluir_socios: bool = False,
        incluir_geometria: bool = False,
        pagina: int = 1,
        por_pagina: int = 20,
    ) -> dict[str, Any]:
        """
        Busca jazidas e processos minerários ANM por substância mineral bruta + localização.
        Use SOMENTE para substâncias minerais extraídas da natureza (areia, brita, cascalho,
        saibro, argila, calcário, granito, basalto, pedra, minério de ferro, etc.).
        NÃO use para produtos industrializados/comerciais (cimento, concreto, ferro/aço,
        tijolo, telha, madeira, PVC, tinta, argamassa) — para esses, use buscar_empresas.

        Fluxo: substância(semântico) → processos ANM(geo) → empresa CNPJ(contatos).
        Cruza 3 índices: anm_substancia_v001 → anm_v003 → rfb_cnpj_v003.

        Args:
            substancia: Substância mineral bruta (ex: "areia lavada", "brita", "calcário")
            latitude: Latitude do ponto central da busca
            longitude: Longitude do ponto central da busca
            raio_km: Raio de busca em km (default: 50)
            uf: Filtrar por UF (ex: "SP", "MG")
            fase: Fase do processo (ex: "Concessão de Lavra")
            apenas_ativos: Filtrar apenas processos ativos (default: false — retorna ativos e inativos)
            incluir_contatos: Incluir telefone/email da empresa (default: true)
            incluir_socios: Incluir nomes dos sócios (default: false)
            incluir_geometria: Incluir polígonos GeoJSON das jazidas + fronteiras dos municípios para mapa (default: false)
            pagina: Página de resultados (default: 1)
            por_pagina: Resultados por página (default: 10, max: 50)
        """
        from mcp_servers.jazidas.queries.fornecedores import executar_busca_fornecedores

        logger.info(
            f"buscar_fornecedores: substancia='{substancia}', "
            f"geo=({latitude},{longitude}), raio={raio_km}km, "
            f"uf={uf}, fase={fase}, pagina={pagina}"
        )

        # Clamp por_pagina
        por_pagina = max(1, min(por_pagina, 50))

        # ── Build cache key params (exclude pagina/por_pagina) ──
        cache_params = {
            "substancia": substancia,
            "latitude": latitude,
            "longitude": longitude,
            "raio_km": raio_km,
            "uf": uf,
            "fase": fase,
            "apenas_ativos": apenas_ativos,
            "incluir_contatos": incluir_contatos,
            "incluir_socios": incluir_socios,
            "incluir_geometria": incluir_geometria,
        }

        # ── Check cache for existing results (pages 2+ are free) ──
        cache_id = jazidas_cache._search_key(
            jazidas_cache.PREFIX_FORNECEDORES, cache_params
        )

        cached_page = await jazidas_cache.get_page(cache_id, pagina, por_pagina)
        if cached_page is not None:
            page_items, meta = cached_page
            logger.info(
                f"buscar_fornecedores: Cache HIT — page {pagina}/{meta.total_paginas}"
            )
            response = jazidas_cache.build_paginated_response(
                page_items=page_items,
                meta=meta,
                cache_id=cache_id,
            )
            response["mapa"] = _pontos_from_jazida_page(page_items)
            if incluir_geometria:
                geo_cached = await jazidas_cache.get_geometry(cache_id)
                if geo_cached:
                    _attach_page_geometry(response, page_items, geo_cached)
            return response

        # ── Step 1: Resolve substance (k-NN + BM25) ──
        resolucao = await substancia_resolver.resolver(substancia)

        if not resolucao.encontrou:
            logger.warning(f"buscar_fornecedores: No resolution for '{substancia}'")
            return {
                "sucesso": False,
                "mensagem": (
                    f"Não foi possível identificar substâncias para o termo '{substancia}'. "
                    "Tente termos mais específicos como 'areia', 'brita', 'calcário', 'granito'."
                ),
                "resolucao": {
                    "metodo": resolucao.metodo,
                    "termo": resolucao.termo_original,
                },
            }

        logger.info(
            f"buscar_fornecedores: Step 1 OK — "
            f"{len(resolucao.ids_filter)} IDs via {resolucao.metodo}"
        )

        # ── Steps 2+3: Search anm_v003 + enrich from rfb_cnpj_v003 ──
        try:
            resultado = await executar_busca_fornecedores(
                os_service=os_service,
                resolucao=resolucao,
                latitude=latitude,
                longitude=longitude,
                raio_km=raio_km,
                uf=uf,
                fase=fase,
                apenas_ativos=apenas_ativos,
                incluir_contatos=incluir_contatos,
                incluir_socios=incluir_socios,
                incluir_geometria=incluir_geometria,
            )
        except Exception as e:
            logger.error(f"buscar_fornecedores: Query execution failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro na execução da busca: {str(e)}",
            }

        all_results = resultado.get("resultados", [])
        total = resultado.get("total", 0)

        if not all_results:
            return {
                "sucesso": True,
                "mensagem": (
                    f"Nenhum fornecedor encontrado para '{substancia}' "
                    f"no raio de {raio_km}km."
                ),
                "meta": {
                    "total": 0,
                    "pagina": 1,
                    "por_pagina": por_pagina,
                    "total_paginas": 0,
                },
                "dados": [],
                "resolucao": resultado.get("resolucao"),
            }

        # ── Store ALL results in cache (paginate from Redis) ──
        cache_id = await jazidas_cache.store_search(
            prefix=jazidas_cache.PREFIX_FORNECEDORES,
            params=cache_params,
            results=all_results,
        )

        # ── Get requested page ──
        cached_page = await jazidas_cache.get_page(cache_id, pagina, por_pagina)
        if cached_page is not None:
            page_items, meta = cached_page
        else:
            # Fallback: manual pagination (if Redis unavailable)
            offset = (pagina - 1) * por_pagina
            page_items = all_results[offset : offset + por_pagina]
            import math

            total_paginas = max(1, math.ceil(len(all_results) / por_pagina))
            from mcp_servers.common.schemas import SearchResultMeta

            meta = SearchResultMeta(
                total=len(all_results),
                pagina=min(pagina, total_paginas),
                por_pagina=por_pagina,
                total_paginas=total_paginas,
            )

        response = jazidas_cache.build_paginated_response(
            page_items=page_items,
            meta=meta,
            cache_id=cache_id,
        )

        # Map points derived from page_items — guarantees mapa = chat list
        response["mapa"] = _pontos_from_jazida_page(page_items)

        # Add metadata about the resolution + totals
        response["resolucao"] = resultado.get("resolucao")
        response["total_processos_opensearch"] = total
        response["total_cnpjs_unicos"] = resultado.get("total_cnpjs", 0)

        # Attach geometry for current page + persist full set in cache
        if incluir_geometria:
            mapa_data = resultado.get("mapa") or {}
            geo_all = {
                "geometrias_jazidas": mapa_data.get("geometrias_jazidas"),
                "geometrias_municipios": mapa_data.get("geometrias_municipios"),
            }
            await jazidas_cache.store_geometry(cache_id, geo_all)
            _attach_page_geometry(response, page_items, geo_all)

        logger.info(
            f"buscar_fornecedores: OK — "
            f"{total} processos, {resultado.get('total_cnpjs', 0)} CNPJs, "
            f"page {meta.pagina}/{meta.total_paginas}"
        )

        return response

    # ==================================================================
    # Tool 1.b: fornecedores_por_poligono (Phase 3 — polygon variant)
    # ==================================================================
    @mcp.tool()
    async def fornecedores_por_poligono(
        substancia: str,
        geometry: dict,
        uf: str | None = None,
        fase: str | None = None,
        apenas_ativos: bool = False,
        incluir_contatos: bool = True,
        incluir_socios: bool = False,
        incluir_geometria: bool = False,
        pagina: int = 1,
        por_pagina: int = 20,
    ) -> dict[str, Any]:
        """
        Busca fornecedores ANM (jazidas + empresa CNPJ contato) cuja
        ``localizacao`` cai DENTRO de um polígono GeoJSON. Use sempre que
        o usuário pedir fornecedores "dentro de uma isócrona", "na área de
        até X minutos", "no polígono de Y" — em vez de raio circular
        (geometricamente diferente).

        Mesmo cross-walk de 3 índices que ``buscar_fornecedores``:
            substância(semântico) → processos ANM(polígono) → CNPJ(contatos).

        Args:
            substancia: Substância mineral bruta (ex: "areia lavada", "brita").
            geometry: Polígono GeoJSON ({"type":"Polygon"|"MultiPolygon",
                "coordinates":[...]}). Tipicamente o `feature.geometry`
                retornado por geo__calcular_isocrona.
            uf: Filtrar por UF (ex: "SP")
            fase: Fase do processo (ex: "Concessão de Lavra")
            apenas_ativos: Filtrar apenas processos ativos (default: false — retorna ativos e inativos)
            incluir_contatos: Telefone/email da empresa (default: true)
            incluir_socios: Nomes de sócios (default: false)
            incluir_geometria: Polígonos das jazidas e municípios (default: false)
            pagina: Página de resultados (default: 1)
            por_pagina: Resultados por página (default: 20, max: 50)
        """
        from mcp_servers.jazidas.queries.fornecedores import (
            executar_busca_fornecedores_por_poligono,
        )

        if not isinstance(geometry, dict) or geometry.get("type") not in (
            "Polygon", "MultiPolygon",
        ):
            return {
                "sucesso": False,
                "mensagem": (
                    "Parâmetro 'geometry' inválido. Forneça um GeoJSON do tipo "
                    "Polygon ou MultiPolygon (ex.: feature.geometry de "
                    "geo__calcular_isocrona)."
                ),
            }

        logger.info(
            f"fornecedores_por_poligono: substancia='{substancia}', "
            f"geom={geometry.get('type')}, uf={uf}, fase={fase}, pagina={pagina}"
        )

        por_pagina = max(1, min(por_pagina, 50))

        # Cache key — usa hash do GeoJSON normalizado para reuso entre páginas
        # da MESMA isócrona. Polígonos diferentes geram chaves diferentes.
        import json
        geom_signature = json.dumps(geometry, sort_keys=True, separators=(",", ":"))
        cache_params = {
            "substancia": substancia,
            "geom_hash": hash(geom_signature),
            "uf": uf,
            "fase": fase,
            "apenas_ativos": apenas_ativos,
            "incluir_contatos": incluir_contatos,
            "incluir_socios": incluir_socios,
            "incluir_geometria": incluir_geometria,
        }
        cache_id = jazidas_cache._search_key(
            jazidas_cache.PREFIX_FORNECEDORES, cache_params
        )

        cached_page = await jazidas_cache.get_page(cache_id, pagina, por_pagina)
        if cached_page is not None:
            page_items, meta = cached_page
            logger.info(
                f"fornecedores_por_poligono: Cache HIT — page {pagina}/{meta.total_paginas}"
            )
            response = jazidas_cache.build_paginated_response(
                page_items=page_items, meta=meta, cache_id=cache_id,
            )
            response["mapa"] = _pontos_from_jazida_page(page_items)
            response["filtro"] = "poligono"
            response["geometria_tipo"] = geometry.get("type")
            if incluir_geometria:
                geo_cached = await jazidas_cache.get_geometry(cache_id)
                if geo_cached:
                    _attach_page_geometry(response, page_items, geo_cached)
            return response

        resolucao = await substancia_resolver.resolver(substancia)
        if not resolucao.encontrou:
            return {
                "sucesso": False,
                "mensagem": (
                    f"Não foi possível identificar substâncias para o termo "
                    f"'{substancia}'. Tente termos mais específicos como 'areia', "
                    "'brita', 'calcário', 'granito'."
                ),
                "resolucao": {
                    "metodo": resolucao.metodo,
                    "termo": resolucao.termo_original,
                },
            }

        try:
            resultado = await executar_busca_fornecedores_por_poligono(
                os_service=os_service,
                resolucao=resolucao,
                geometry=geometry,
                uf=uf,
                fase=fase,
                apenas_ativos=apenas_ativos,
                incluir_contatos=incluir_contatos,
                incluir_socios=incluir_socios,
                incluir_geometria=incluir_geometria,
            )
        except ValueError as e:
            return {"sucesso": False, "mensagem": f"Polígono inválido: {e}"}
        except Exception as e:
            logger.exception("fornecedores_por_poligono: query failed")
            return {"sucesso": False, "mensagem": f"Erro na busca: {e}"}

        all_results = resultado.get("resultados", [])
        total = resultado.get("total", 0)
        if not all_results:
            return {
                "sucesso": True,
                "filtro": "poligono",
                "geometria_tipo": geometry.get("type"),
                "mensagem": (
                    f"Nenhum fornecedor encontrado para '{substancia}' "
                    f"dentro do polígono."
                ),
                "meta": {"total": 0, "pagina": 1, "por_pagina": por_pagina, "total_paginas": 0},
                "dados": [],
                "resolucao": resultado.get("resolucao"),
            }

        cache_id = await jazidas_cache.store_search(
            prefix=jazidas_cache.PREFIX_FORNECEDORES,
            params=cache_params,
            results=all_results,
        )
        cached_page = await jazidas_cache.get_page(cache_id, pagina, por_pagina)
        if cached_page is not None:
            page_items, meta = cached_page
        else:
            import math
            from mcp_servers.common.schemas import SearchResultMeta
            offset = (pagina - 1) * por_pagina
            page_items = all_results[offset:offset + por_pagina]
            total_paginas = max(1, math.ceil(len(all_results) / por_pagina))
            meta = SearchResultMeta(
                total=len(all_results),
                pagina=min(pagina, total_paginas),
                por_pagina=por_pagina,
                total_paginas=total_paginas,
            )

        response = jazidas_cache.build_paginated_response(
            page_items=page_items, meta=meta, cache_id=cache_id,
        )
        response["mapa"] = _pontos_from_jazida_page(page_items)
        response["resolucao"] = resultado.get("resolucao")
        response["total_processos_opensearch"] = total
        response["total_cnpjs_unicos"] = resultado.get("total_cnpjs", 0)
        response["filtro"] = "poligono"
        response["geometria_tipo"] = geometry.get("type")

        if incluir_geometria:
            mapa_data = resultado.get("mapa") or {}
            geo_all = {
                "geometrias_jazidas": mapa_data.get("geometrias_jazidas"),
                "geometrias_municipios": mapa_data.get("geometrias_municipios"),
            }
            await jazidas_cache.store_geometry(cache_id, geo_all)
            _attach_page_geometry(response, page_items, geo_all)

        logger.info(
            f"fornecedores_por_poligono: OK — {total} processos, "
            f"{resultado.get('total_cnpjs', 0)} CNPJs, "
            f"page {meta.pagina}/{meta.total_paginas}"
        )
        return response

    # ==================================================================
    # Tool 2: buscar_jazidas (Phase 2)
    # ==================================================================
    @mcp.tool()
    async def buscar_jazidas(
        termo_busca: str | None = None,
        codigos_cnae_titular: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        raio_km: float = 50.0,
        uf: str | None = None,
        municipio: str | None = None,
        codigo_ibge: str | None = None,
        fase: str | None = None,
        apenas_ativos: bool = False,
        incluir_geometria: bool = False,
        area_min_ha: float | None = None,
        area_max_ha: float | None = None,
        pagina: int = 1,
        por_pagina: int = 20,
    ) -> dict[str, Any]:
        """
        Busca processos minerários por substância e/ou CNAE do titular (RFB),
        mais filtros e geolocalização.

        Fluxo: substância opcional (k-NN semântico) + filtro opcional em
        ``titular.cnae_principal`` + processos ANM (flat + geo_distance).

        Args:
            termo_busca:  Substância ou tipo de uso (ex: "areia", "manganês").
                          Opcional se ``codigos_cnae_titular`` for informado.
            codigos_cnae_titular: Um ou mais CNAEs do **titular** separados por
                          vírgula (ex: ``07.29-4``, ``0729-4/00,0729400``).
                          Corresponde ao campo enriquecido ``titular.cnae_principal``
                          em mr_jazidas_v001 (requer pipeline bot_empresas).
            latitude:     Latitude do centro da busca (opcional — sem geo busca por todo Brasil)
            longitude:    Longitude do centro da busca (opcional)
            raio_km:      Raio em km (default: 50, usado apenas se lat/lon informados)
            uf:           Filtrar por UF (ex: "SP")
            municipio:    Filtrar por nome do município (ex: "Campinas") — match textual aproximado
            codigo_ibge:  Código IBGE do município (ex: "3106200") — filtro exato e mais preciso que `municipio`
            fase:         Fase do processo (ex: "Concessão de Lavra")
            apenas_ativos: Filtrar apenas processos ativos (default: false)
            incluir_geometria: Incluir polígonos GeoJSON (default: false)
            area_min_ha: Área mínima em hectares (ex.: 100 para "mais de 100 ha")
            area_max_ha: Área máxima em hectares (opcional)
            pagina:       Página de resultados (default: 1)
            por_pagina:   Resultados por página (default: 10, max: 50)
        """
        from mcp_servers.jazidas.queries.jazidas import (
            executar_busca_jazidas,
            expand_cnae_titular_codigos,
        )
        from mcp_servers.jazidas.schemas import ResolucaoSubstancia

        tb = (termo_busca or "").strip()
        cc_raw = (codigos_cnae_titular or "").strip()
        if not tb and not cc_raw:
            return {
                "sucesso": False,
                "mensagem": (
                    "Informe ``termo_busca`` (substância ou tipo de uso) e/ou "
                    "``codigos_cnae_titular`` (CNAE do titular, separados por vírgula)."
                ),
            }

        cnae_list = expand_cnae_titular_codigos(cc_raw) if cc_raw else None

        has_geo = latitude is not None and longitude is not None
        logger.info(
            f"buscar_jazidas: termo='{tb or '(nenhum)'}', "
            f"cnae_titular={cnae_list or '—'}, "
            f"geo={'(' + str(latitude) + ',' + str(longitude) + ')' if has_geo else 'off'}, "
            f"raio={raio_km}km, uf={uf}, mun={municipio}, "
            f"ibge={codigo_ibge}, fase={fase}, "
            f"area_min_ha={area_min_ha}, area_max_ha={area_max_ha}, pagina={pagina}"
        )

        # Clamp por_pagina
        por_pagina = max(1, min(por_pagina, 50))

        # ── Cache key (exclude pagina/por_pagina) ──
        cache_params = {
            "termo_busca": tb or "",
            "codigos_cnae_titular": cc_raw or "",
            "latitude": latitude,
            "longitude": longitude,
            "raio_km": raio_km,
            "uf": uf,
            "municipio": municipio,
            "codigo_ibge": codigo_ibge,
            "fase": fase,
            "apenas_ativos": apenas_ativos,
            "incluir_geometria": incluir_geometria,
            "area_min_ha": area_min_ha,
            "area_max_ha": area_max_ha,
        }

        cache_id = jazidas_cache._search_key(
            jazidas_cache.PREFIX_JAZIDAS, cache_params
        )

        # ── Check cache (pages 2+ are free) ──
        cached_page = await jazidas_cache.get_page(cache_id, pagina, por_pagina)
        if cached_page is not None:
            page_items, meta = cached_page
            logger.info(
                f"buscar_jazidas: Cache HIT — page {pagina}/{meta.total_paginas}"
            )
            response = jazidas_cache.build_paginated_response(
                page_items=page_items,
                meta=meta,
                cache_id=cache_id,
            )
            response["mapa"] = _pontos_from_jazida_page(page_items)
            if incluir_geometria:
                geo_cached = await jazidas_cache.get_geometry(cache_id)
                if geo_cached:
                    _attach_page_geometry(response, page_items, geo_cached)
            return response

        # ── Step 1: Resolve substance (k-NN + BM25) — opcional ──
        if tb:
            resolucao = await substancia_resolver.resolver(tb)
        else:
            resolucao = ResolucaoSubstancia()

        if resolucao.encontrou:
            logger.info(
                f"buscar_jazidas: Step 1 OK — "
                f"{len(resolucao.ids_filter)} IDs via {resolucao.metodo}"
            )
        elif tb:
            logger.info(
                f"buscar_jazidas: No substance ID resolution for '{tb}' "
                f"— falling back to direct text match on substancias_desc"
            )

        # ── Step 2: Search mr_jazidas_v001 ──
        try:
            resultado = await executar_busca_jazidas(
                os_service=os_service,
                resolucao=resolucao,
                latitude=latitude,
                longitude=longitude,
                raio_km=raio_km,
                uf=uf,
                municipio=municipio,
                codigo_ibge=codigo_ibge,
                fase=fase,
                apenas_ativos=apenas_ativos,
                incluir_geometria=incluir_geometria,
                area_min_ha=area_min_ha,
                area_max_ha=area_max_ha,
                cnae_titular_codigos=cnae_list,
            )
        except Exception as e:
            logger.error(f"buscar_jazidas: Query execution failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro na execução da busca: {str(e)}",
            }

        all_results = resultado.get("resultados", [])
        total = resultado.get("total", 0)

        if not all_results:
            filtros_txt = []
            if tb:
                filtros_txt.append(f"substância '{tb}'")
            if cnae_list:
                filtros_txt.append(f"CNAE titular {cnae_list[:5]}")
            fdesc = " e ".join(filtros_txt) if filtros_txt else "filtros pedidos"
            return {
                "sucesso": True,
                "mensagem": (
                    f"Nenhuma jazida encontrada para {fdesc}"
                    + (f" no raio de {raio_km}km" if has_geo else "")
                    + (f" em {uf}" if uf else "")
                    + (f" — {municipio}" if municipio else "")
                    + ". Verifique o código CNAE (titular sem enriquecimento RFB "
                    "fica de fora) ou amplie o critério."
                ),
                "meta": {
                    "total": 0,
                    "pagina": 1,
                    "por_pagina": por_pagina,
                    "total_paginas": 0,
                },
                "dados": [],
                "resolucao": resultado.get("resolucao"),
            }

        # ── Store ALL results in cache ──
        cache_id = await jazidas_cache.store_search(
            prefix=jazidas_cache.PREFIX_JAZIDAS,
            params=cache_params,
            results=all_results,
        )

        # ── Get requested page ──
        cached_page = await jazidas_cache.get_page(cache_id, pagina, por_pagina)
        if cached_page is not None:
            page_items, meta = cached_page
        else:
            # Fallback: manual pagination (if Redis unavailable)
            offset = (pagina - 1) * por_pagina
            page_items = all_results[offset : offset + por_pagina]
            import math

            total_paginas = max(1, math.ceil(len(all_results) / por_pagina))
            from mcp_servers.common.schemas import SearchResultMeta

            meta = SearchResultMeta(
                total=len(all_results),
                pagina=min(pagina, total_paginas),
                por_pagina=por_pagina,
                total_paginas=total_paginas,
            )

        response = jazidas_cache.build_paginated_response(
            page_items=page_items,
            meta=meta,
            cache_id=cache_id,
        )

        # Map points derived from page_items — guarantees mapa = chat list
        response["mapa"] = _pontos_from_jazida_page(page_items)

        # Add resolution metadata
        response["resolucao"] = resultado.get("resolucao")
        response["total_processos_opensearch"] = total

        filtro_desc: list[str] = []
        if tb:
            filtro_desc.append(f"substância '{tb}'")
        if cnae_list:
            filtro_desc.append(f"CNAE titular ∈ {{{', '.join(cnae_list[:6])}{'…' if len(cnae_list) > 6 else ''}}}")

        # Explicit summary so the LLM reports the correct total, not just page size
        response["resumo"] = (
            f"Total encontrado: {total} jazidas"
            + (f" ({'; '.join(filtro_desc)})" if filtro_desc else "")
            + ". "
            f"Exibindo página {meta.pagina} de {meta.total_paginas} "
            f"({len(page_items)} de {min(200, total)} resultados carregados). "
            + ("Use pagina=2, 3... para ver mais." if meta.total_paginas > 1 else "")
        )

        # Attach geometry for current page + persist full set in cache
        if incluir_geometria:
            mapa_data = resultado.get("mapa") or {}
            geo_all = {
                "geometrias_jazidas": mapa_data.get("geometrias_jazidas"),
                "geometrias_municipios": mapa_data.get("geometrias_municipios"),
            }
            await jazidas_cache.store_geometry(cache_id, geo_all)
            _attach_page_geometry(response, page_items, geo_all)

        logger.info(
            f"buscar_jazidas: OK — "
            f"{total} processos, page {meta.pagina}/{meta.total_paginas}"
        )

        return response

    # ==================================================================
    # Tool 3: detalhes_processo (Phase 2)
    # ==================================================================
    @mcp.tool()
    async def detalhes_processo(
        ds_processo: str,
        incluir_empresa: bool = True,
        incluir_eventos: bool = False,
        incluir_titulos: bool = False,
    ) -> dict[str, Any]:
        """
        Obtém dados completos de um processo minerário pelo código dsProcesso.
        Enriquece com dados da empresa titular via rfb_cnpj_v003 (contatos, sócios).

        Fluxo: anm_v003(busca por dsProcesso) → rfb_cnpj_v003(enriquece empresa).
        Retorna: processo expandido (substâncias, pessoas, municípios, shapes, eventos, títulos).

        Args:
            ds_processo: Código do processo (ex: "832.145/2018")
            incluir_empresa: Incluir dados CNPJ do titular — contatos, sócios (default: true)
            incluir_eventos: Incluir histórico de eventos (default: false)
            incluir_titulos: Incluir títulos/documentos legais (default: false)
        """
        from mcp_servers.jazidas.queries.detalhes import executar_detalhes_processo

        logger.info(
            f"detalhes_processo: ds='{ds_processo}', "
            f"empresa={'on' if incluir_empresa else 'off'}, "
            f"eventos={'on' if incluir_eventos else 'off'}, "
            f"titulos={'on' if incluir_titulos else 'off'}"
        )

        ds_processo = ds_processo.strip()
        if not ds_processo:
            return {
                "sucesso": False,
                "mensagem": "Código do processo (ds_processo) é obrigatório.",
            }

        # ── Check cache ──
        cached = await jazidas_cache.get_processo(ds_processo)
        if cached is not None:
            logger.info(f"detalhes_processo: Cache HIT for '{ds_processo}'")
            proc = cached.get("processo")
            emp = cached.get("empresa")
            return {
                "sucesso": True,
                "processo": proc,
                "empresa": emp,
                "fonte": "cache",
                "mapa": _mapa_from_detalhes_processo(
                    proc if isinstance(proc, dict) else None,
                    emp if isinstance(emp, dict) else None,
                ),
            }

        # ── Execute query ──
        try:
            resultado = await executar_detalhes_processo(
                os_service=os_service,
                ds_processo=ds_processo,
                incluir_empresa=incluir_empresa,
                incluir_eventos=incluir_eventos,
                incluir_titulos=incluir_titulos,
            )
        except Exception as e:
            logger.error(f"detalhes_processo: Query failed for '{ds_processo}': {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro ao buscar processo '{ds_processo}': {str(e)}",
            }

        if not resultado.get("encontrado"):
            from mcp_servers.jazidas.queries.detalhes import looks_like_numero_processo

            if looks_like_numero_processo(ds_processo):
                msg = (
                    f"Processo '{ds_processo}' não encontrado no índice ANM. "
                    "Confira dígitos e ano (ex.: '833535/2014' ou '833.535/2014')."
                )
            else:
                msg = (
                    f"Não localizei '{ds_processo}' como processo ANM. "
                    "Para rotas entre locais (ex.: Mina do Salobo → Porto de Itaqui), "
                    "use geo__calcular_rota com origem_endereco e destino_endereco "
                    "(modo truck) — não invente número de processo."
                )
            return {"sucesso": False, "mensagem": msg}

        # ── Cache the result ──
        await jazidas_cache.store_processo(
            ds_processo=ds_processo,
            data={
                "processo": resultado["processo"],
                "empresa": resultado.get("empresa"),
            },
        )

        logger.info(
            f"detalhes_processo: OK — "
            f"'{ds_processo}', "
            f"fase={resultado['processo'].get('fase')}"
        )

        out_resp: dict[str, Any] = {
            "sucesso": True,
            "processo": resultado["processo"],
            "empresa": resultado.get("empresa"),
            "fonte": "opensearch",
            "mapa": _mapa_from_detalhes_processo(
                resultado["processo"],
                resultado.get("empresa") if isinstance(resultado.get("empresa"), dict) else None,
            ),
        }
        if incluir_empresa and resultado.get("empresa") is None:
            tit = (resultado.get("processo") or {}).get("titular") or {}
            cin = tit.get("cnpj_basico")
            cfmt = tit.get("cnpj_completo_formatado")
            extra = ""
            if cin:
                extra = f" Raiz (8 dígitos) na ANM/SIGMINE: '{cin}'."
                if cfmt:
                    extra += (
                        f" CNPJ formatado assumindo matriz 0001 (DVs calculados): '{cfmt}'."
                    )
            out_resp["aviso_empresa_rfb"] = (
                "Dados cadastrais completos (Receita Federal) não encontrados no índice "
                "mr_empresas_v001 para o CNPJ básico do titular deste processo."
                + extra
                + " Isso não invalida o processo na ANM: use processo.titular para nome e "
                "raiz; use 'empresas__detalhes_empresa' com o CNPJ básico (8 dígitos) para "
                "tentar fallback ANM ou ficha RFB quando existir."
            )
        return out_resp

    # ==================================================================
    # Tool 4: jazidas_por_poligono (Phase 3)
    # ==================================================================
    from mcp_servers.jazidas.queries.poligono import (
        executar_busca_por_poligono,
        resolver_poligono_municipio,
        validate_geometry,
    )

    @mcp.tool()
    async def jazidas_por_poligono(
        geometry: dict | None = None,
        nome_municipio: str | None = None,
        uf_municipio: str | None = None,
        substancia: str | None = None,
        fase: str | None = None,
        apenas_ativos: bool = False,
        localizacao_dentro_poligono: bool = False,
        pagina: int = 1,
        por_pagina: int = 20,
    ) -> dict[str, Any]:
        """
        Busca processos num polígono GeoJSON (Polygon/MultiPolygon).

        Modo default (``localizacao_dentro_poligono=False``): interseção do
        polígono da **concessão** (campo ``geom``) com a área — adequado a
        limites administrativos onde a área da concessão importa.

        Modo isócrona (``localizacao_dentro_poligono=True``): só processos cujo
        **ponto do mapa** (``location``) está dentro do polígono (pré-filtro
        por bounding box + point-in-polygon exato em Python), alinhado ao que
        o utilizador vê desenhado (ex.: isócrona Azure).

        Aceita DUAS formas de entrada:
        1. GeoJSON direto via 'geometry' (Polygon/MultiPolygon)
        2. Nome do município via 'nome_municipio' — busca o polígono automaticamente no ibge_municipio_v001

        Args:
            geometry: Polígono GeoJSON ({"type": "Polygon", "coordinates": [...]})
            nome_municipio: Nome do município (ex: "Guarulhos") — alternativa ao geometry
            uf_municipio: UF para desambiguar o município (ex: "SP")
            substancia: Filtrar por substância (opcional)
            fase: Filtrar por fase (opcional)
            apenas_ativos: Filtrar apenas processos ativos (default: false — retorna ativos e inativos)
            localizacao_dentro_poligono: se True, filtra pelo pin ``location`` dentro do polígono
            pagina: Página de resultados (default: 1)
            por_pagina: Resultados por página (default: 10, max: 50)
        """
        # ── Validate input: need geometry OR nome_municipio ──
        if geometry is None and nome_municipio is None:
            return {
                "sucesso": False,
                "mensagem": "Informe 'geometry' (GeoJSON) ou 'nome_municipio'.",
            }

        # ── Resolve municipality name to polygon if needed ──
        resolved_geometry = geometry
        municipio_resolvido = None

        if geometry is None and nome_municipio:
            resolved_geometry = await resolver_poligono_municipio(
                os_service, nome_municipio, uf_municipio
            )
            if resolved_geometry is None:
                return {
                    "sucesso": False,
                    "mensagem": (
                        f"Município '{nome_municipio}'"
                        f"{' (' + uf_municipio + ')' if uf_municipio else ''}"
                        " não encontrado ou sem polígono disponível."
                    ),
                }
            municipio_resolvido = {
                "nome": nome_municipio,
                "uf": uf_municipio,
                "tipo_geometry": resolved_geometry.get("type"),
            }

        # ── Validate geometry format ──
        geo_error = validate_geometry(resolved_geometry)
        if geo_error:
            return {"sucesso": False, "mensagem": f"Geometria inválida: {geo_error}"}

        # ── Pagination ──
        por_pagina = min(max(por_pagina, 1), 50)
        from_offset = (max(pagina, 1) - 1) * por_pagina

        # ── Execute query ──
        resultado = await executar_busca_por_poligono(
            os_service=os_service,
            geometry=resolved_geometry,
            substancia=substancia,
            fase=fase,
            apenas_ativos=apenas_ativos,
            size=por_pagina,
            from_offset=from_offset,
            localizacao_dentro_poligono=localizacao_dentro_poligono,
        )

        total = resultado["total"]
        total_paginas = (total + por_pagina - 1) // por_pagina if total > 0 else 0

        response: dict[str, Any] = {
            "sucesso": True,
            "total": total,
            "pagina": pagina,
            "por_pagina": por_pagina,
            "total_paginas": total_paginas,
            "resultados": resultado["resultados"],
            "mapa": resultado["mapa"],
        }

        if municipio_resolvido:
            response["municipio_resolvido"] = municipio_resolvido

        logger.info(
            f"jazidas_por_poligono: {total} processos encontrados "
            f"(pág {pagina}/{total_paginas})"
        )
        return response

    # ==================================================================
    # Tool 5: verificar_vigencia_substancia (Phase 3)
    # ==================================================================
    @mcp.tool()
    async def verificar_vigencia_substancia(
        ds_processo: str,
        id_substancia: int | None = None,
    ) -> dict[str, Any]:
        """
        Verifica a vigência de substâncias em um processo minerário.
        Usa nested query no campo substancias do anm_v003 para checar dtFimVigencia.

        Para cada substância, informa:
        - Se está vigente (dtFimVigencia IS NULL) ou encerrada
        - Tipo de uso, datas de início/fim, motivo do encerramento
        - Resumo geral: total, vigentes, encerradas, status

        Caso de uso: "O processo 832.145/2018 ainda está extraindo areia ou já esgotou?"

        Args:
            ds_processo: Código do processo (ex: "832.145/2018")
            id_substancia: ID da substância específica (se omitido, retorna todas)
        """
        from mcp_servers.jazidas.queries.vigencia import executar_verificacao_vigencia

        logger.info(
            f"verificar_vigencia: ds='{ds_processo}', "
            f"id_substancia={id_substancia or 'all'}"
        )

        ds_processo = ds_processo.strip()
        if not ds_processo:
            return {
                "sucesso": False,
                "mensagem": "Código do processo (ds_processo) é obrigatório.",
            }

        # ── Check process cache (reuse detalhes_processo cache) ──
        cached = await jazidas_cache.get_processo(ds_processo)
        if cached is not None:
            # If we have cached process data, extract substancias from it
            logger.info(f"verificar_vigencia: Cache HIT for '{ds_processo}'")
            processo_data = cached.get("processo", {})
            substancias_nested = processo_data.get("substancias", [])

            if substancias_nested:
                from mcp_servers.jazidas.queries.vigencia import (
                    build_vigencia_summary,
                    format_vigencia,
                )

                vigencias = [format_vigencia(sub) for sub in substancias_nested]
                if id_substancia is not None:
                    vigencias = [
                        v for v in vigencias if v.id_substancia == id_substancia
                    ]

                return {
                    "sucesso": True,
                    "ds_processo": processo_data.get("ds_processo", ds_processo),
                    "fase": processo_data.get("fase"),
                    "ativo": processo_data.get("ativo", True),
                    "substancias": [v.model_dump() for v in vigencias],
                    "resumo": build_vigencia_summary(vigencias),
                    "filtro_aplicado": {"id_substancia": id_substancia}
                    if id_substancia
                    else None,
                    "fonte": "cache",
                }

        # ── Execute query ──
        try:
            resultado = await executar_verificacao_vigencia(
                os_service=os_service,
                ds_processo=ds_processo,
                id_substancia=id_substancia,
            )
        except Exception as e:
            logger.error(f"verificar_vigencia: Query failed for '{ds_processo}': {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro ao verificar vigência do processo '{ds_processo}': {str(e)}",
            }

        if not resultado.get("encontrado"):
            return {
                "sucesso": False,
                "mensagem": (
                    f"Processo '{ds_processo}' não encontrado no índice. "
                    "Aceita com ou sem pontos antes da barra (ex.: "
                    "'833535/2014' ou '833.535/2014'). Confira dígitos e ano."
                ),
            }

        # ── Build response ──
        resumo = resultado.get("resumo", {})
        logger.info(
            f"verificar_vigencia: OK — '{ds_processo}' "
            f"status={resumo.get('status')}, "
            f"{resumo.get('vigentes', 0)} vigentes, "
            f"{resumo.get('encerradas', 0)} encerradas"
        )

        return {
            "sucesso": True,
            **resultado,
            "fonte": "opensearch",
        }

    # ==================================================================
    # Tool 6: consultar_cfem_processo
    # ==================================================================
    @mcp.tool()
    async def consultar_cfem_processo(
        numero_processo: str,
        ano_inicio: int | None = None,
        ano_fim: int | None = None,
    ) -> dict[str, Any]:
        """
        Retorna o histórico completo de arrecadação CFEM (royalties minerais) de
        um processo ANM específico, com série mensal e totais agregados.

        Use quando o usuário perguntar:
          - "Quanto esse processo pagou de CFEM / royalties?"
          - "Qual o histórico de arrecadação do processo X?"
          - "Esse processo tem produção ativa? Quando foi a última arrecadação?"
          - "Quanto de CFEM foi pago pelo processo 832145/2018 em 2023?"

        NÃO use esta tool em dezenas de processos em paralelo — para ranking
        de vários processos/UF/substância use ``ranking_cfem`` (uma chamada).

        A CFEM (Compensação Financeira pela Exploração Mineral) é o royalty pago
        pelas mineradoras ao Estado brasileiro proporcional ao valor vendido.
        Um processo com CFEM > 0 está em fase produtiva.

        Args:
            numero_processo: Número do processo ANM (ex: "832.145/2018", "800001/1980")
            ano_inicio: Ano inicial do período (ex: 2020). Omitir para histórico completo.
            ano_fim:    Ano final do período (ex: 2024). Omitir para histórico completo.
        """
        from mcp_servers.jazidas.queries.cfem import historico_cfem_processo

        logger.info(
            f"consultar_cfem_processo: processo='{numero_processo}', "
            f"periodo={ano_inicio}-{ano_fim}"
        )

        numero_processo = numero_processo.strip()
        if not numero_processo:
            return {"sucesso": False, "mensagem": "Número do processo é obrigatório."}

        try:
            resultado = await historico_cfem_processo(
                os_service=os_service,
                numero_processo=numero_processo,
                ano_inicio=ano_inicio,
                ano_fim=ano_fim,
            )
        except Exception as e:
            logger.error(f"consultar_cfem_processo: falha para '{numero_processo}': {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro ao consultar CFEM do processo '{numero_processo}': {e}",
            }

        if not resultado.get("encontrado"):
            return {
                "sucesso": False,
                "mensagem": resultado.get("mensagem", "Processo sem registros CFEM."),
            }

        resumo = resultado["resumo"]
        logger.info(
            f"consultar_cfem_processo: OK — '{numero_processo}' "
            f"total=R${resumo.get('total_arrecadado'):,.2f}, "
            f"anos={resumo.get('anos_com_producao')}, "
            f"subs={resumo.get('substancias')}"
        )

        return {
            "sucesso": True,
            "resumo":  resumo,
            "serie":   resultado["serie"],
            "fonte":   "mr_cfem_v001",
        }

    # ==================================================================
    # Tool 7: ranking_cfem
    # ==================================================================
    @mcp.tool()
    async def ranking_cfem(
        agrupar_por: str = "processo",
        uf: str | None = None,
        substancia: str | None = None,
        ano_inicio: int | None = None,
        ano_fim: int | None = None,
        top_n: int = 10,
        cnpj_basico: str | None = None,
        titular_anm_fragmentos: str | None = None,
    ) -> dict[str, Any]:
        """
        Retorna os maiores pagadores de CFEM (royalties minerais) agrupados por
        processo, empresa, município, substância ou UF.

        Use quando o usuário perguntar:
          - "Quais os maiores pagadores de CFEM em Minas Gerais?"
          - "Quais minas de bauxita/ferro estão pagando CFEM em MG nos últimos anos?"
          - "Qual processo paga mais royalties no Brasil?"
          - "Qual substância gera mais CFEM no Pará?"
          - "Quais empresas (CNPJ) pagaram mais CFEM em 2023?"
          - "Ranking de municípios com maior arrecadação mineral"
          - "Maiores mineradoras de ferro por CFEM"
          - Comparação entre empresas conhecidas: primeiro resolva CNPJ (ex.\
            ``empresas__buscar_empresas``), depois chame esta tool com \
            ``cnpj_basico`` e ``agrupar_por='cnpj'`` — o índice CFEM **não** \
            contém razão social, só ``cnpj_basico``. Sempre que comparar grandes \
            grupos (Vale, CSN, Anglo, …), passe também ``titular_anm_fragmentos`` \
            (CSV de marcas/trechos, ex. ``"Vale,Anglo American,Companhia Siderúrgica Nacional"``) \
            para unir CNPJ básico de titulares em processos ANM que pagam CFEM \
            com raízes diferentes da matriz RFB.

        Args:
            agrupar_por: Critério de agrupamento. Valores aceitos:
                         "processo"   → ranking por processo ANM (default)
                         "cnpj"       → ranking por empresa (CNPJ básico)
                         "municipio"  → ranking por município extrator
                         "substancia" → ranking por substância mineral
                         "uf"         → ranking por estado
            uf:          Filtrar por UF (ex: "MG", "PA", "MT"). Omitir para Brasil.
            substancia:  Filtrar por substância (ex: "FERRO", "AREIA", "OURO").
            ano_inicio:  Ano inicial do período (ex: 2020). Omitir para histórico completo.
            ano_fim:     Ano final do período (ex: 2024). Omitir para histórico completo.
            top_n:       Número de posições no ranking (default: 10, max: 50).
            cnpj_basico: Opcional — um ou mais CNPJs (8 dígitos ou completo com máscara),
                         separados por vírgula. Restringe a arrecadação desses contribuintes.
            titular_anm_fragmentos: Opcional — CSV de trechos de nome/razão social de titular
                         em processos ANM; expande o filtro ``cnpj_basico`` com raízes
                         encontradas em ``mr_jazidas_v001`` (declarante CFEM ≠ matriz RFB).
        """
        from mcp_servers.jazidas.queries.cfem import ranking_cfem as _ranking_cfem

        logger.info(
            f"ranking_cfem: agrupar_por='{agrupar_por}', uf={uf}, "
            f"substancia={substancia}, periodo={ano_inicio}-{ano_fim}, top_n={top_n}, "
            f"cnpj_basico={cnpj_basico!r}, titular_anm_fragmentos={titular_anm_fragmentos!r}"
        )

        try:
            resultado = await _ranking_cfem(
                os_service=os_service,
                uf=uf,
                substancia=substancia,
                ano_inicio=ano_inicio,
                ano_fim=ano_fim,
                agrupar_por=agrupar_por,
                top_n=top_n,
                cnpj_basico=cnpj_basico,
                titular_anm_fragmentos=titular_anm_fragmentos,
            )
        except Exception as e:
            logger.error(f"ranking_cfem: falha: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro ao gerar ranking CFEM: {e}",
            }

        n = len(resultado.get("ranking", []))
        total_geral = resultado.get("total_geral_arrecadado", 0)
        logger.info(
            f"ranking_cfem: OK — {n} posições, "
            f"total_geral=R${total_geral:,.2f}"
        )

        out: dict[str, Any] = {
            "sucesso": True,
            **resultado,
            "fonte": "mr_cfem_v001",
        }
        # Mapa: SSE map_data (chat.py) só emite pins se houver mapa.pontos com lat/lon.
        ap = (resultado.get("agrupar_por") or agrupar_por or "processo").lower().strip()
        if ap == "processo" and n > 0:
            from mcp_servers.jazidas.queries.detalhes import mapa_pontos_para_ranking_cfem

            try:
                out["mapa"] = await mapa_pontos_para_ranking_cfem(
                    os_service,
                    resultado.get("ranking") or [],
                )
                np = out["mapa"].get("total_pontos", 0)
                logger.info(f"ranking_cfem: mapa ANM — {np} pontos com coordenadas")
            except Exception as me:
                logger.warning("ranking_cfem: mapa ANM opcional falhou: %s", me)
                out["mapa"] = {"pontos": [], "total_pontos": 0}

        return out

    # ==================================================================
    # Tool 8: buscar_restricoes_geo
    # ==================================================================
    @mcp.tool()
    async def buscar_restricoes_geo(
        numero_processo: str,
        incluir_detalhes: bool = True,
    ) -> dict[str, Any]:
        """
        Retorna todas as restrições geoespaciais de um processo minerário ANM:
        Terras Indígenas (TI) e Unidades de Conservação (UC) que se sobrepõem
        ao ponto de localização do processo.

        Use quando o usuário perguntar:
          - "Esse processo tem restrições ambientais ou indígenas?"
          - "O processo X está dentro de alguma área protegida?"
          - "Quais UCs ou TIs afetam esse processo?"
          - "Qual o risco ambiental/indígena desse projeto de mineração?"

        Retorna um resumo estruturado com:
          - Total de restrições (TIs + UCs)
          - Nível de restrição: critico / alto / medio / baixo / nenhum
          - Lista detalhada das TIs sobrepostas (fase FUNAI, etnia, área)
          - Lista detalhada das UCs sobrepostas (categoria, grupo, esfera, área)

        Critério de nível:
          - critico: TI em fase Homologada ou Regularizada
          - alto:    TI em fase Delimitada/Declarada/Em Estudo, OU UC de Proteção Integral
          - medio:   UC de Uso Sustentável apenas
          - baixo:   apenas restricoes_geo sem detalhes ricos
          - nenhum:  sem sobreposições

        Args:
            numero_processo: Número do processo ANM (ex: "832.145/2018" ou "832145/2018")
            incluir_detalhes: Busca detalhes de cada TI/UC nos índices especializados
                              (default: true — retorna fase FUNAI, categoria UC, etc.)
        """
        numero_processo = numero_processo.strip()
        if not numero_processo:
            return {"sucesso": False, "mensagem": "Número do processo é obrigatório."}

        logger.info(
            f"buscar_restricoes_geo: processo='{numero_processo}', "
            f"detalhes={incluir_detalhes}"
        )

        # ── 1. Busca jazida no OpenSearch ──
        # Normaliza o número: remove pontos e barras para busca keyword
        numero_norm = numero_processo.replace(".", "").replace("/", "/")
        try:
            raw = await os_service.search("mr_jazidas_v001", {
                "size": 1,
                "_source": [
                    "numero_processo", "ds_processo",
                    "municipio", "uf", "substancia", "fase",
                    "n_restricoes_ti", "n_restricoes_uc", "restricoes_geo",
                    "location",
                ],
                "query": {
                    "bool": {
                        "should": [
                            {"term": {"numero_processo": numero_processo}},
                            {"term": {"numero_processo": numero_norm}},
                            {"match": {"ds_processo": numero_processo}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
            })
        except Exception as e:
            logger.error(f"buscar_restricoes_geo: query falhou: {e}")
            return {"sucesso": False, "mensagem": f"Erro ao buscar processo: {e}"}

        hits = raw.get("hits", {}).get("hits", [])
        if not hits:
            return {
                "sucesso": False,
                "mensagem": (
                    f"Processo '{numero_processo}' não encontrado. "
                    "Verifique o número (ex: '832145/2018' ou '832.145/2018')."
                ),
            }

        src = hits[0]["_source"]
        restricoes_geo: list[str] = src.get("restricoes_geo") or []
        n_ti  = int(src.get("n_restricoes_ti") or 0)
        n_uc  = int(src.get("n_restricoes_uc")  or 0)

        # Segrega entradas por tipo
        ti_entries: list[tuple[str, str]] = []   # (id, nome)
        uc_entries: list[tuple[str, str]] = []   # (cod_cnuc, nome)
        outras: list[str] = []

        for entry in restricoes_geo:
            parts = entry.split(":", 2)
            if parts[0] == "TI" and len(parts) >= 3:
                ti_entries.append((parts[1], parts[2]))
            elif parts[0] == "UC" and len(parts) >= 3:
                uc_entries.append((parts[1], parts[2]))
            else:
                outras.append(entry)

        # ── 2. Enriquece TIs com detalhes ──
        tis_detalhados: list[dict] = []
        if incluir_detalhes and ti_entries:
            ids_ti = [t[0] for t in ti_entries]
            try:
                raw_ti = await os_service.search("mr_terras_indigenas_v001", {
                    "size": len(ids_ti),
                    "_source": [
                        "id_ti", "nome", "fase_funai", "etnia",
                        "area_ha", "uf", "municipios",
                    ],
                    "query": {"terms": {"id_ti": ids_ti}},
                })
                ti_map = {
                    h["_source"]["id_ti"]: h["_source"]
                    for h in raw_ti.get("hits", {}).get("hits", [])
                }
            except Exception:
                ti_map = {}

            for tid, tnome in ti_entries:
                detail = ti_map.get(tid, {})
                tis_detalhados.append({
                    "id_ti":      tid,
                    "nome":       detail.get("nome") or tnome,
                    "fase_funai": detail.get("fase_funai"),
                    "etnia":      detail.get("etnia"),
                    "area_ha":    detail.get("area_ha"),
                    "uf":         detail.get("uf"),
                    "municipios": detail.get("municipios"),
                })
        else:
            tis_detalhados = [
                {"id_ti": tid, "nome": tnome}
                for tid, tnome in ti_entries
            ]

        # ── 3. Enriquece UCs com detalhes ──
        ucs_detalhadas: list[dict] = []
        if incluir_detalhes and uc_entries:
            ids_uc = [u[0] for u in uc_entries]
            try:
                raw_uc = await os_service.search("mr_ucs_v001", {
                    "size": len(ids_uc),
                    "_source": [
                        "cod_cnuc", "nome", "categoria", "grupo",
                        "esfera", "area_ha", "dt_criacao",
                    ],
                    "query": {"terms": {"cod_cnuc": ids_uc}},
                })
                uc_map = {
                    h["_source"]["cod_cnuc"]: h["_source"]
                    for h in raw_uc.get("hits", {}).get("hits", [])
                }
            except Exception:
                uc_map = {}

            for ucid, ucnome in uc_entries:
                detail = uc_map.get(ucid, {})
                ucs_detalhadas.append({
                    "cod_cnuc":  ucid,
                    "nome":      detail.get("nome") or ucnome,
                    "categoria": detail.get("categoria"),
                    "grupo":     detail.get("grupo"),
                    "esfera":    detail.get("esfera"),
                    "area_ha":   detail.get("area_ha"),
                    "dt_criacao": detail.get("dt_criacao"),
                })
        else:
            ucs_detalhadas = [
                {"cod_cnuc": ucid, "nome": ucnome}
                for ucid, ucnome in uc_entries
            ]

        # ── 4. Calcula nível de restrição ──
        def _nivel_restricao() -> str:
            # TIs homologadas ou regularizadas = crítico
            fases_criticas = {"Homologada", "Regularizada"}
            fases_altas    = {"Delimitada", "Declarada", "Em Estudo"}
            for ti in tis_detalhados:
                fase = ti.get("fase_funai") or ""
                if fase in fases_criticas:
                    return "critico"
            for ti in tis_detalhados:
                fase = ti.get("fase_funai") or ""
                if fase in fases_altas:
                    return "alto"
            # TI sem fase conhecida mas existente → alto
            if tis_detalhados:
                return "alto"
            # UC Proteção Integral → alto
            for uc in ucs_detalhadas:
                if (uc.get("grupo") or "").startswith("Proteção"):
                    return "alto"
            # UC Uso Sustentável → médio
            if ucs_detalhadas:
                return "medio"
            # Apenas outras restrições
            if outras:
                return "baixo"
            return "nenhum"

        nivel = _nivel_restricao()
        total = n_ti + n_uc + len(outras)

        # ── 5. Monta resposta ──
        _nivel_label = {
            "critico": "🔴 CRÍTICO — sobreposição com Terra Indígena homologada/regularizada",
            "alto":    "🟠 ALTO — sobreposição com TI em processo ou UC de Proteção Integral",
            "medio":   "🟡 MÉDIO — sobreposição com UC de Uso Sustentável",
            "baixo":   "🟢 BAIXO — restrições menores",
            "nenhum":  "✅ NENHUM — sem sobreposições geoespaciais registradas",
        }

        logger.info(
            f"buscar_restricoes_geo: OK — '{numero_processo}' "
            f"nivel={nivel}, ti={n_ti}, uc={n_uc}"
        )

        return {
            "sucesso":          True,
            "numero_processo":  src.get("numero_processo", numero_processo),
            "ds_processo":      src.get("ds_processo"),
            "municipio":        src.get("municipio"),
            "uf":               src.get("uf"),
            "substancia":       src.get("substancia"),
            "fase_processo":    src.get("fase"),
            "resumo": {
                "nivel_restricao":  nivel,
                "descricao_nivel":  _nivel_label[nivel],
                "total_restricoes": total,
                "n_restricoes_ti":  n_ti,
                "n_restricoes_uc":  n_uc,
                "n_outras":         len(outras),
            },
            "terras_indigenas":     tis_detalhados,
            "unidades_conservacao": ucs_detalhadas,
            "outras_restricoes":    outras,
            "fonte": "mr_jazidas_v001 + mr_terras_indigenas_v001 + mr_ucs_v001",
        }

    # ==================================================================
    # Tool 10: ocorrencias_minerais_proximas
    # ==================================================================
    @mcp.tool()
    async def ocorrencias_minerais_proximas(
        lat: float | None = None,
        lon: float | None = None,
        raio_km: float = 50.0,
        substancia: str | None = None,
        uf: str | None = None,
    ) -> dict[str, Any]:
        """
        Retorna ocorrências minerais do SGB/CPRM (GeoBank).

        Dois modos de uso (um deles é obrigatório):

        1. **Por coordenadas** — ``lat`` + ``lon`` + ``raio_km`` (busca radial).
        2. **Por estado** — ``uf`` (sigla ``MT`` ou nome ``Mato Grosso``): todas
           as ocorrências do índice naquele estado, opcionalmente filtradas por
           ``substancia``. Não exige município nem ``buscar_municipio``.

        Complementa o ``buscar_restricoes_geo`` com dados de **mineralogia** —
        enquanto aquela ferramenta foca em restrições ambientais (TIs e UCs),
        esta revela o potencial mineralógico da área com base nos 36.472
        pontos de ocorrência levantados pelo Serviço Geológico do Brasil.

        Use quando o usuário perguntar:
          - "Quais minerais existem nas proximidades dessa jazida?"
          - "Tem ouro na região de X?"
          - "Ocorrências de cobalto no Mato Grosso" / "no Pará" (use ``uf``)
          - "Há minas ativas próximas a esse processo?"
          - "Quais ocorrências de cobre o CPRM catalogou nessa região?"

        Retorna:
          - ``resumo_mineralogico``: distribuição de substâncias, tipos de depósito,
            situação das minas (ativa/inativa), minerais estratégicos presentes
          - ``ocorrencias``: lista ordenada por distância com id, substância,
            tipo de depósito, situação, município/UF e descrição geológica

        Args:
            lat:       Latitude (WGS84). Obrigatório se ``uf`` não for informado.
            lon:       Longitude (WGS84). Obrigatório se ``uf`` não for informado.
            raio_km:   Raio em km no modo radial (default: 50, max: 200)
            substancia: Filtro opcional (ex: "Ouro", "Cobalto", "Cobre")
            uf:        Sigla ou nome do estado para busca em todo o UF (ex: ``MT``,
                       ``Mato Grosso``). Quando preenchido, ``lat``/``lon`` são ignorados.
        """
        from mcp_servers.jazidas.queries.cprm import (
            executar_ocorrencias_minerais_proximas as _exec_geo,
            executar_ocorrencias_por_uf,
        )

        if uf and str(uf).strip():
            try:
                resultado = await executar_ocorrencias_por_uf(
                    os_service=os_service,
                    uf=str(uf).strip(),
                    substancia=substancia,
                    max_ocorrencias=50,
                )
            except Exception as e:
                logger.error(f"ocorrencias_minerais_proximas (uf): erro: {e}")
                return {"sucesso": False, "mensagem": str(e)}
            if "erro" in resultado:
                return {"sucesso": False, "mensagem": resultado["erro"]}
            resumo_min = resultado.get("resumo_mineralogico", {})
            total = resumo_min.get("total_ocorrencias", 0)
            logger.info(
                f"ocorrencias_minerais_proximas: OK (uf={resumo_min.get('uf')}) "
                f"— {total} ocorrências"
            )
            return {
                "sucesso": True,
                "dados": resultado,
                "mapa": resultado.get("mapa", {"pontos": []}),
            }

        if lat is None or lon is None:
            return {
                "sucesso": False,
                "mensagem": (
                    "Informe coordenadas (lat, lon) para busca por raio, "
                    "ou o parâmetro uf (ex: MT, Mato Grosso) para ocorrências em todo o estado."
                ),
            }

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return {
                "sucesso": False,
                "mensagem": "Coordenadas inválidas. lat: -90..90, lon: -180..180.",
            }

        raio_km = min(max(raio_km, 0.5), 200.0)

        logger.info(
            f"ocorrencias_minerais_proximas: lat={lat} lon={lon} "
            f"raio={raio_km}km substancia={substancia!r}"
        )

        try:
            resultado = await _exec_geo(
                os_service=os_service,
                lat=lat,
                lon=lon,
                raio_km=raio_km,
                substancia=substancia,
            )
        except Exception as e:
            logger.error(f"ocorrencias_minerais_proximas: erro: {e}")
            return {"sucesso": False, "mensagem": str(e)}

        if "erro" in resultado:
            return {"sucesso": False, "mensagem": resultado["erro"]}

        resumo_min = resultado.get("resumo_mineralogico", {})
        total        = resumo_min.get("total_ocorrencias", 0)
        estrategicos = resumo_min.get("minerios_estrategicos", [])
        n_depositos  = resumo_min.get("n_depositos_confirmados", 0)
        logger.info(
            f"ocorrencias_minerais_proximas: OK — {total} ocorrências, "
            f"depósitos={n_depositos}, estratégicos={estrategicos}"
        )

        return {
            "sucesso": True,
            "dados":   resultado,
            "mapa":    resultado.get("mapa", {"pontos": []}),
        }

    # ==================================================================
    # Tool 11: consultar_mercado_mineral
    # ==================================================================
    @mcp.tool()
    async def consultar_mercado_mineral(
        substancia_ou_ncm: str,
        fluxo: str = "export",
        uf: str | None = None,
        ano_inicio: int = 2019,
        ano_fim: int = 2025,
    ) -> dict[str, Any]:
        """
        Tendência de exportação ou importação de um mineral brasileiro (2019-2025).

        Consulta o índice mr_mercado_v001 (ComexStat/MDIC) e retorna a evolução
        anual de volumes e valores, com concentração geográfica por UF.

        O parâmetro ``substancia_ou_ncm`` pode ser **qualquer descrição** relacionada
        ao produto (ex.: "cobalto refinado", "óxidos de terras raras"): o backend
        descobre NCMs dinamicamente a partir de ``ncm_desc`` no período, além de
        um mapa curado de atalho para substâncias muito comuns.

        Use quando o usuário perguntar:
          - "Qual o volume exportado de ferro pelo Brasil?"
          - "Como evoluiu a exportação de nióbio nos últimos anos?"
          - "Quanto o Brasil importou de cobre em 2024?"
          - "Quais estados mais exportam bauxita?"
          - "A exportação de ouro cresceu ou caiu desde 2019?"

        Aceita substância por nome livre, frase ou código NCM de 8 dígitos
        (com ou sem pontuação).

        Args:
            substancia_ou_ncm: Nome da substância mineral OU código NCM de 8 dígitos
            fluxo:      "export" (padrão) ou "import"
            uf:         Sigla do estado (ex: "MG", "PA") — opcional, filtra origem/destino
            ano_inicio: Ano inicial da série (default: 2019)
            ano_fim:    Ano final da série (default: 2025)
        """
        fluxo = fluxo.lower().strip()
        if fluxo not in ("export", "import"):
            return {
                "sucesso": False,
                "mensagem": "fluxo deve ser 'export' ou 'import'.",
            }

        logger.info(
            f"consultar_mercado_mineral: subst={substancia_ou_ncm!r} "
            f"fluxo={fluxo} uf={uf} {ano_inicio}-{ano_fim}"
        )

        try:
            from mcp_servers.jazidas.queries.mercado import executar_consultar_mercado_mineral

            resultado = await executar_consultar_mercado_mineral(
                os_service=os_service,
                substancia_ou_ncm=substancia_ou_ncm,
                fluxo=fluxo,
                uf=uf,
                ano_inicio=ano_inicio,
                ano_fim=ano_fim,
            )
        except Exception as e:
            logger.error(f"consultar_mercado_mineral: erro: {e}")
            return {"sucesso": False, "mensagem": str(e)}

        if "erro" in resultado:
            return {"sucesso": False, "mensagem": resultado["erro"]}

        vl_bi = resultado.get("resumo", {}).get("total_vl_fob_bi_periodo", 0)
        logger.info(
            f"consultar_mercado_mineral: OK — total={vl_bi}bi "
            f"anos={len(resultado.get('tendencia_anual', []))}"
        )
        return {"sucesso": True, "dados": resultado}

    # ==================================================================
    # Tool 12: principais_destinos_mineral
    # ==================================================================
    @mcp.tool()
    async def principais_destinos_mineral(
        substancia_ou_ncm: str,
        fluxo: str = "export",
        uf: str | None = None,
        ano: int | None = None,
    ) -> dict[str, Any]:
        """
        Ranking dos principais países destino (exportação) ou origem (importação)
        de um mineral brasileiro, com participação percentual no valor total.

        Aceita nome/frase livre ou NCM — a resolução de NCMs é dinâmica no índice
        (igual a ``consultar_mercado_mineral``).

        Use quando o usuário perguntar:
          - "Para onde o Brasil exporta nióbio?"
          - "Quais países compram mais minério de ferro do Brasil?"
          - "De onde o Brasil importa cobre?"
          - "China domina a compra de ferro brasileiro?"
          - "Qual o principal mercado para o ouro de MG?"

        Aceita substância por nome livre, frase ou NCM de 8 dígitos.

        Args:
            substancia_ou_ncm: Nome, frase ou código NCM de 8 dígitos
            fluxo: "export" (padrão) ou "import"
            uf:    Sigla do estado de origem/destino — opcional (ex: "PA")
            ano:   Ano específico — opcional; se omitido, agrega todo o período
        """
        fluxo = fluxo.lower().strip()
        if fluxo not in ("export", "import"):
            return {
                "sucesso": False,
                "mensagem": "fluxo deve ser 'export' ou 'import'.",
            }

        logger.info(
            f"principais_destinos_mineral: subst={substancia_ou_ncm!r} "
            f"fluxo={fluxo} uf={uf} ano={ano}"
        )

        try:
            from mcp_servers.jazidas.queries.mercado import executar_principais_destinos_mineral

            resultado = await executar_principais_destinos_mineral(
                os_service=os_service,
                substancia_ou_ncm=substancia_ou_ncm,
                fluxo=fluxo,
                uf=uf,
                ano=ano,
            )
        except Exception as e:
            logger.error(f"principais_destinos_mineral: erro: {e}")
            return {"sucesso": False, "mensagem": str(e)}

        if "erro" in resultado:
            return {"sucesso": False, "mensagem": resultado["erro"]}

        top = resultado.get("resumo", {}).get("top_destinos") or \
              resultado.get("resumo", {}).get("top_origens") or []
        logger.info(
            f"principais_destinos_mineral: OK — "
            f"top={[p['pais'] for p in top[:3]]}"
        )
        return {"sucesso": True, "dados": resultado}

    # ==================================================================
    # Tool 13: imoveis_car_proximos
    # ==================================================================
    @mcp.tool()
    async def imoveis_car_proximos(
        lat: float,
        lon: float,
        raio_km: float = 5.0,
        status: str = "AT",
        tipo: str | None = None,
        max_registros: int = 15,
    ) -> dict[str, Any]:
        """
        Busca imóveis rurais do CAR (Cadastro Ambiental Rural) no entorno de
        uma coordenada geográfica.

        Retorna lista de imóveis com área cadastrada, nome do proprietário,
        status no CAR, alertas de sobreposição com TIs/UCs, e um resumo
        fundiário da área (nível de atenção: ALTO / MÉDIO / BAIXO / SEM_DADOS).

        **Nível ALTO** é atribuído quando há imóveis de comunidades
        tradicionais (PCT — Povos e Comunidades Tradicionais, QUI — Quilombola,
        ou ASS — Assentamento).

        Use quando o usuário perguntar:
          - "Existem propriedades rurais cadastradas no CAR nesta área?"
          - "Quem são os donos de terras próximos a este processo de mineração?"
          - "Há assentamentos ou terras quilombolas perto desta jazida?"
          - "Qual é a situação fundiária da área do processo ANM X?"
          - Complementar análise de restrições geográficas com tenure de terra

        Args:
            lat:           Latitude (WGS84, decimal)
            lon:           Longitude (WGS84, decimal)
            raio_km:       Raio de busca em km (default: 5 km)
            status:        Status CAR a filtrar — "AT" (ativo, padrão), "PE"
                           (pendente), "SU" (suspenso), "CA" (cancelado), ou
                           None para todos
            tipo:          Tipo de imóvel — "IRU" (imóvel rural), "ASS"
                           (assentamento), "PCT", "QUI" — opcional
            max_registros: Máximo de imóveis a retornar (default: 15)
        """
        logger.info(
            f"imoveis_car_proximos: lat={lat} lon={lon} "
            f"raio={raio_km}km status={status} tipo={tipo}"
        )

        try:
            from mcp_servers.jazidas.queries.sicar import executar_imoveis_car_proximos

            resultado = await executar_imoveis_car_proximos(
                os_service=os_service,
                lat=lat,
                lon=lon,
                raio_km=raio_km,
                status=status or None,
                tipo=tipo,
                max_registros=max_registros,
            )
        except Exception as e:
            logger.error(f"imoveis_car_proximos: erro: {e}")
            return {"sucesso": False, "mensagem": str(e)}

        if "erro" in resultado:
            return {"sucesso": False, "mensagem": resultado["erro"]}

        nivel = resultado.get("resumo_fundiario", {}).get("nivel_atencao_fundiaria", "—")
        total = resultado.get("total_encontrados", 0)
        logger.info(f"imoveis_car_proximos: OK — {total} imóveis nivel={nivel}")
        return {"sucesso": True, "dados": resultado}

    # ==================================================================
    # Tool 14: imoveis_car_proprietario
    # ==================================================================
    @mcp.tool()
    async def imoveis_car_proprietario(
        cpf_cnpj: str,
        uf: str | None = None,
        max_registros: int = 20,
    ) -> dict[str, Any]:
        """
        Busca todos os imóveis rurais do CAR (Cadastro Ambiental Rural)
        registrados em nome de um CPF ou CNPJ.

        Útil para cruzar o titular de um processo ANM com sua presença
        fundiária: "A empresa que pediu o processo de lavra possui propriedade
        rural cadastrada na área?"

        Use quando o usuário perguntar:
          - "A empresa X tem imóvel rural cadastrado no CAR?"
          - "Quantos hectares de imóvel rural a empresa X possui?"
          - "Em quais estados o titular do processo ANM tem propriedades CAR?"
          - "O CPF/CNPJ do titular tem terras registradas no CAR?"

        Args:
            cpf_cnpj:      CPF (11 dígitos) ou CNPJ (14 dígitos) do proprietário.
                           Pode conter pontuação — será normalizado.
            uf:            Filtrar por estado (ex: "MG") — opcional
            max_registros: Máximo de imóveis a retornar (default: 20)
        """
        # Normaliza: remove pontuação
        cpf_cnpj_norm = "".join(c for c in cpf_cnpj if c.isdigit())
        if len(cpf_cnpj_norm) not in (11, 14):
            return {
                "sucesso": False,
                "mensagem": (
                    f"CPF/CNPJ inválido: '{cpf_cnpj}'. "
                    "Informe 11 dígitos (CPF) ou 14 dígitos (CNPJ)."
                ),
            }

        logger.info(
            f"imoveis_car_proprietario: doc={cpf_cnpj_norm[:6]}*** uf={uf}"
        )

        try:
            from mcp_servers.jazidas.queries.sicar import executar_imoveis_por_cpf_cnpj

            resultado = await executar_imoveis_por_cpf_cnpj(
                os_service=os_service,
                cpf_cnpj=cpf_cnpj_norm,
                uf=uf,
                max_registros=max_registros,
            )
        except Exception as e:
            logger.error(f"imoveis_car_proprietario: erro: {e}")
            return {"sucesso": False, "mensagem": str(e)}

        if "erro" in resultado:
            return {"sucesso": False, "mensagem": resultado["erro"]}

        total     = resultado.get("resumo", {}).get("total_imoveis", 0)
        area_ha   = resultado.get("resumo", {}).get("area_total_ha", 0)
        logger.info(f"imoveis_car_proprietario: OK — {total} imóveis {area_ha:.0f} ha")
        return {"sucesso": True, "dados": resultado}

    # ==================================================================
    # Tool 14: areas_em_disponibilidade
    # ==================================================================
    @mcp.tool()
    async def areas_em_disponibilidade(
        substancia: str | None = None,
        uf: str | None = None,
        municipio: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        raio_km: float = 100.0,
        apenas_sem_restricoes: bool = False,
        incluir_apto: bool = True,
        somente_estrategicos: bool = False,
        area_min_ha: float | None = None,
        limite: int = 30,
    ) -> dict[str, Any]:
        """
        Busca áreas ANM em disponibilidade para novo requerimento mineral.

        Retorna processos extintos cujas áreas estão abertas para novo pedido
        (fase 'disponibilidade') e, opcionalmente, processos prestes a chegar
        nessa fase ('apto para disponibilidade').

        Total disponível no Brasil: ~20.400 (disponibilidade) + ~8.800 (apto) = ~29.200 áreas.

        Use cases:
        - "Quais áreas de lítio estão disponíveis em MG?"
        - "Há processos de terras raras para requerer próximos a essas coordenadas?"
        - "Mostre áreas estratégicas disponíveis sem sobreposição com TIs ou UCs"
        - "Onde posso requerer uma área de grafita no Nordeste?"

        Args:
            substancia:            Substância de interesse (ex: "lítio", "grafita", "terras raras").
                                   Será resolvida semanticamente contra o índice de substâncias.
            uf:                    Filtro por estado (ex: "MG", "PA")
            municipio:             Filtro por município (fuzzy match)
            latitude/longitude:    Coordenada central para busca por proximidade
            raio_km:               Raio de busca em km quando lat/lon fornecidos (default 100)
            apenas_sem_restricoes: Se True, exclui áreas com sobreposição de TI ou UC
            incluir_apto:          Se True, inclui 'apto para disponibilidade' além de 'disponibilidade'
            somente_estrategicos:  Se True, retorna apenas áreas classificadas como estratégicas
                                   (minerais críticos: TR, Li, Nb, Co, grafita, urânio, etc.)
            area_min_ha:           Área mínima em hectares (ex: 500 para filtrar áreas maiores)
            limite:                Máximo de resultados (default 30, max 200)
        """
        from mcp_servers.jazidas.queries.disponibilidades import (
            executar_areas_em_disponibilidade,
        )

        logger.info(
            f"areas_em_disponibilidade: substancia={substancia}, uf={uf}, "
            f"municipio={municipio}, raio={raio_km}km, "
            f"sem_restricoes={apenas_sem_restricoes}, estrategicos={somente_estrategicos}"
        )

        # Resolve substância semanticamente se fornecida (mesmo resolver que buscar_jazidas)
        substancias_desc: list[str] | None = None
        if substancia:
            try:
                resolucao = await substancia_resolver.resolver(substancia)
            except Exception as e:
                logger.warning(f"areas_em_disponibilidade: resolução substância falhou: {e}")
                resolucao = None

            if resolucao and resolucao.encontrou:
                if resolucao.ids_substancia:
                    substancias_desc = list(resolucao.ids_substancia)
                    logger.info(
                        f"areas_em_disponibilidade: substância resolvida → "
                        f"{substancias_desc[:5]}"
                    )
                elif resolucao.ids_tipo_uso:
                    return {
                        "sucesso": False,
                        "mensagem": (
                            "Esta busca de disponibilidade ANM filtra por substância mineral "
                            f"(ex.: cobre, ferro). O termo '{substancia}' foi interpretado como "
                            "tipo de uso, que não se aplica aqui. Reformule com o nome do minério."
                        ),
                    }
            else:
                return {
                    "sucesso": False,
                    "mensagem": (
                        f"Substância '{substancia}' não encontrada no catálogo ANM. "
                        "Tente nomes como 'lítio', 'grafita', 'nióbio', 'terras raras', "
                        "'ouro', 'ferro', 'cobre'."
                    ),
                }

        # Filtro por categorias estratégicas
        categorias: list[str] | None = None
        if somente_estrategicos:
            categorias = [
                "terra_rara", "litio", "niobio", "cobalto",
                "grafita", "uranio", "manganes", "titanio",
            ]

        limite = max(1, min(limite, 200))

        try:
            resultado = await executar_areas_em_disponibilidade(
                os_service=os_service,
                substancias_desc=substancias_desc,
                uf=uf.upper() if uf else None,
                municipio=municipio,
                latitude=latitude,
                longitude=longitude,
                raio_km=raio_km,
                apenas_sem_restricoes=apenas_sem_restricoes,
                incluir_apto=incluir_apto,
                categorias_estrategicas=categorias,
                area_min_ha=area_min_ha,
                limite=limite,
            )
        except Exception as e:
            logger.error(f"areas_em_disponibilidade: query failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro na busca de disponibilidades: {str(e)}",
            }

        areas = resultado.get("areas", [])

        if not areas:
            filtros_usados = []
            if substancia:
                filtros_usados.append(f"substância='{substancia}'")
            if uf:
                filtros_usados.append(f"UF={uf}")
            if municipio:
                filtros_usados.append(f"município='{municipio}'")
            if latitude:
                filtros_usados.append(f"raio={raio_km}km")
            if apenas_sem_restricoes:
                filtros_usados.append("sem restrições TI/UC")
            return {
                "sucesso": True,
                "total_encontrado": 0,
                "areas": [],
                "mensagem": (
                    f"Nenhuma área em disponibilidade encontrada"
                    + (f" com os filtros: {', '.join(filtros_usados)}" if filtros_usados else "")
                    + ". Tente ampliar o raio, remover filtros ou consultar outra substância."
                ),
            }

        logger.info(
            f"areas_em_disponibilidade: {len(areas)} áreas retornadas "
            f"(total={resultado['total_encontrado']})"
        )

        mapa = _mapa_from_disponibilidade_areas(areas)

        return {
            "sucesso":               True,
            "total_encontrado":      resultado["total_encontrado"],
            "retornados":            resultado["retornados"],
            "total_disponibilidade": resultado["total_disponibilidade"],
            "total_apto":            resultado["total_apto"],
            "areas":                 areas,
            "mapa":                  mapa,
            "nota": (
                "fase='disponibilidade': área disponível para novo requerimento ANM. "
                "fase='apto para disponibilidade': processo em fase final antes da abertura. "
                "Para requerer, acesse o portal ANM (sei.anm.gov.br)."
            ),
        }

    # ==================================================================
    # Tool 15: afloramentos_geologicos_proximos
    # ==================================================================
    @mcp.tool()
    async def afloramentos_geologicos_proximos(
        lat: float,
        lon: float,
        raio_km: float = 15.0,
        tipo_rocha: str | None = None,
        max_resultados: int = 25,
    ) -> dict[str, Any]:
        """
        Retorna afloramentos geológicos do SGB/CPRM (350.568 pontos de campo)
        próximos a uma coordenada, com resumo litológico da área.

        Complementa ``ocorrencias_minerais_proximas`` (que foca em ocorrências
        minerais catalogadas) com **observações de campo diretas**: cada
        afloramento é um ponto onde um geólogo descreveu rochas in situ,
        fornecendo o contexto litológico real da área.

        Use quando o usuário perguntar:
          - "Mapa dos afloramentos", "plotar afloramentos", "pontos de afloramento"
          - "Qual o tipo de rocha na área desse processo de mineração?"
          - "Há afloramentos de granito/gnaisse/xisto perto dessa coordenada?"
          - "Qual o contexto geológico de campo dessa região?"
          - "Tem afloramentos de pegmatito próximos? (potencial lítio/terras raras)"
          - "Quais rochas foram descritas pelo CPRM nessa área?"

        Se o pedido for por **processo ANM** (ex.: "880.111/2020"), primeiro chame
        ``detalhes_processo`` para obter ``processo.localizacao.lat/lon``, depois
        esta tool com essas coordenadas e ``raio_km`` (ex.: 15–25 km).

        Retorna:
          - ``resumo_litologico``: distribuição de rochas, tipos de afloramento
            e projetos CPRM que cobriram a área
          - ``afloramentos``: lista ordenada por distância com tipo de rocha,
            descrição, projeto, folha topográfica e dados do campo
          - ``mapa``: pontos para visualização no mapa

        Args:
            lat:            Latitude da coordenada central (WGS84)
            lon:            Longitude da coordenada central (WGS84)
            raio_km:        Raio de busca em km (default: 15, max: 100)
            tipo_rocha:     Filtro opcional por tipo de rocha
                            (ex: "granito", "xisto", "pegmatito", "basalto")
            max_resultados: Máximo de afloramentos a retornar (default: 25, max: 100)
        """
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return {
                "sucesso": False,
                "mensagem": "Coordenadas inválidas. lat: -90..90, lon: -180..180.",
            }

        raio_km = min(max(raio_km, 0.5), 100.0)

        logger.info(
            "afloramentos_geologicos_proximos: lat=%s lon=%s raio=%skm rocha=%r",
            lat, lon, raio_km, tipo_rocha,
        )

        try:
            from mcp_servers.jazidas.queries.afloramentos import (
                executar_afloramentos_proximos,
            )

            resultado = await executar_afloramentos_proximos(
                lat=lat,
                lon=lon,
                raio_km=raio_km,
                tipo_rocha=tipo_rocha,
                max_resultados=max_resultados,
            )
        except Exception as e:
            logger.error("afloramentos_geologicos_proximos: erro: %s", e)
            return {"sucesso": False, "mensagem": str(e)}

        if "erro" in resultado:
            return {"sucesso": False, "mensagem": resultado["erro"]}

        total = resultado.get("resumo_litologico", {}).get("total_no_raio", 0)
        rochas_est = resultado.get("resumo_litologico", {}).get(
            "rochas_possivelmente_estrategicas", []
        )
        logger.info(
            "afloramentos_geologicos_proximos: OK — %d no raio, estratégicos=%s",
            total, rochas_est,
        )

        return {"sucesso": True, "dados": resultado}

    # ==================================================================
    # Tool 16: consultar_preco_mineral
    # ==================================================================
    @mcp.tool()
    async def consultar_preco_mineral(
        mineral: str,
        incluir_variacao: bool = True,
        periodo_variacao_dias: int = 7,
    ) -> dict[str, Any]:
        """
        Retorna a cotação atual de um metal/mineral em tempo real via Metals-API.

        Cobre metais preciosos, industriais e estratégicos relevantes à
        mineração brasileira. Útil para contextualizar o valor econômico
        de uma jazida, comparar CFEM com preço de mercado, ou avaliar
        oportunidade de novos requerimentos.

        Use quando o usuário perguntar:
          - "Qual o preço do ouro hoje?"
          - "Quanto vale a tonelada de cobre?"
          - "Como está o preço do nióbio / lítio / cobalto?"
          - "O preço do ferro subiu ou caiu nos últimos 7 dias?"
          - "Qual a cotação da platina agora?"
          - "Quanto está o níquel? Tem impacto na minha jazida?"

        Minerais suportados (PT-BR ou EN):
          Preciosos:   ouro (XAU), prata (XAG), platina (XPT),
                       paládio (XPD), ródio (XRH)
          Industriais: cobre (COPPER), ferro (IRON), alumínio (ALU),
                       níquel (NICKEL), estanho (TIN), zinco (ZINC),
                       chumbo (LEAD)
          Estratégicos: nióbio (NIOBIUM), lítio (LITHIUM), cobalto (COBALT),
                        titânio (TITANIUM), tungstênio (TUNGSTEN),
                        vanádio (VANADIUM), manganês (MANGANESE),
                        molibdênio (MOLYBDENUM), cromo (CHROMIUM)

        Retorna:
          - ``preco_atual``: valor em USD/troy oz (preciosos) ou USD/t (industriais)
          - ``variacao_{N}d``: variação percentual e absoluta vs. N dias atrás
          - ``contexto_minerario``: relevância do metal para mineração brasileira
          - ``estrategico``: flag se é mineral crítico/estratégico

        Args:
            mineral:               Nome do mineral em PT-BR ou EN (ex: "ouro", "copper")
            incluir_variacao:      Buscar variação histórica (default: true)
            periodo_variacao_dias: Período da variação em dias (default: 7, max: 30)
        """
        from mcp_servers.common.config import mcp_settings
        from mcp_servers.jazidas.queries.precos_minerais import (
            executar_consultar_preco_mineral,
        )

        mineral = mineral.strip()
        if not mineral:
            return {"sucesso": False, "mensagem": "Nome do mineral é obrigatório."}

        periodo_variacao_dias = max(1, min(periodo_variacao_dias, 30))

        logger.info(
            "consultar_preco_mineral: mineral=%r variacao=%s periodo=%dd",
            mineral, incluir_variacao, periodo_variacao_dias,
        )

        resultado = await executar_consultar_preco_mineral(
            api_key=mcp_settings.metals_api_key,
            base_url=mcp_settings.metals_api_base_url,
            redis_cache=redis_cache,
            cache_ttl=mcp_settings.cache_metals_price_ttl,
            mineral=mineral,
            incluir_variacao=incluir_variacao,
            periodo_variacao_dias=periodo_variacao_dias,
        )

        if "erro" in resultado:
            return {"sucesso": False, "mensagem": resultado["erro"],
                    **{k: v for k, v in resultado.items() if k != "erro"}}

        preco = resultado.get("preco_atual", {}).get("valor_usd")
        unidade = resultado.get("preco_atual", {}).get("unidade", "")
        var_key = f"variacao_{periodo_variacao_dias}d"
        var_pct = resultado.get(var_key, {}).get("variacao_pct")
        emoji = resultado.get(var_key, {}).get("emoji", "")

        logger.info(
            "consultar_preco_mineral: OK — %s = %.4f %s %s (%s%%)",
            resultado.get("simbolo"), preco or 0, unidade,
            emoji, var_pct if var_pct is not None else "N/A",
        )

        return resultado

    # ==================================================================
    # Tool 17: geoquimica_proxima
    # ==================================================================
    @mcp.tool()
    async def geoquimica_proxima(
        lat: float,
        lon: float,
        raio_km: float = 25.0,
        analito: str | None = None,
        classe: str | None = None,
        valor_min: float | None = None,
        max_amostras: int = 25,
    ) -> dict[str, Any]:
        """
        Retorna análises geoquímicas do SGB/CPRM próximas a uma coordenada.

        Acessa ~65K amostras geoquímicas: 61K análises de rocha e 4K de
        mineral/minério coletadas em projetos de mapeamento geológico regional
        pelo Serviço Geológico do Brasil (SGB/CPRM). Cada amostra contém
        valores de múltiplos elementos (Au, Ag, Cu, Ni, Nb, TR, U, etc.)
        medidos em laboratório.

        Complementa ``ocorrencias_minerais_proximas`` (pontos de depósito)
        com **dados analíticos quantitativos** — teores reais de elementos
        em amostras de campo.

        Use quando o usuário perguntar:
          - "Qual o teor de ouro nas amostras de rocha dessa área?"
          - "Há anomalias de cobre na região?"
          - "O CPRM detectou nióbio ou terras raras nessa coordenada?"
          - "Quais análises geoquímicas existem próximas a essa jazida?"
          - "Amostras com Ce, La ou Nd a 150 km de Araxá?"
          - "Teor de Au > 0.5 ppm nas amostras a 50km de Ouro Preto?"
          - "Quais elementos foram analisados nas rochas da Carajás?"

        Retorna:
          - ``resumo``: total de amostras, analitos detectados, elementos
            estratégicos presentes, projetos CPRM na área
          - ``amostras``: lista ordenada por distância com todos os valores
            analíticos de cada amostra
          - ``mapa``: pontos para visualização no frontend

        Args:
            lat:         Latitude do centro da busca (WGS84)
            lon:         Longitude do centro da busca (WGS84)
            raio_km:     Raio de busca em km (default: 25, max: 500)
            analito:     Um ou mais símbolos químicos (ex: ``"Au"``, ``"Nb"``,
                         ``"Ce, La, Nd"`` ou ``"Ce ou La ou Nd"``) — lógica **OU**
                         entre elementos. Case-insensitive (``AU`` casa ``Au``).
                         Se omitido, retorna todas as amostras na área.
            classe:      Filtrar por classe — "Rocha" ou "Mineral/Minério".
                         Omitir para ambas.
            valor_min:   Valor mínimo do analito (ppm ou %) — aplica apenas
                         se ``analito`` for informado.
            max_amostras: Máximo de amostras a retornar (default: 25, max: 100)
        """
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return {
                "sucesso": False,
                "mensagem": "Coordenadas inválidas. lat: -90..90, lon: -180..180.",
            }

        raio_pedido = float(raio_km)
        raio_km     = min(max(raio_pedido, 0.5), 500.0)
        max_amostras = min(max(max_amostras, 1), 100)

        # Normaliza classe para o label exato do índice
        if classe:
            if "mineral" in classe.lower():
                classe = "Mineral/Minério"
            else:
                classe = "Rocha"

        logger.info(
            "geoquimica_proxima: lat=%s lon=%s raio=%skm analito=%r "
            "classe=%r valor_min=%s",
            lat, lon, raio_km, analito, classe, valor_min,
        )

        try:
            from mcp_servers.jazidas.queries.geoquimica import executar_geoquimica_proxima

            resultado = await executar_geoquimica_proxima(
                os_service=os_service,
                lat=lat,
                lon=lon,
                raio_km=raio_km,
                analito=analito,
                classe=classe,
                valor_min=valor_min,
                max_amostras=max_amostras,
            )
        except Exception as e:
            logger.error("geoquimica_proxima: erro: %s", e)
            return {"sucesso": False, "mensagem": str(e)}

        resumo     = resultado.get("resumo", {})
        total      = resumo.get("total_amostras", 0)
        estrateg   = resumo.get("analitos_estrategicos_na_area", [])
        n_analitos = resumo.get("n_analitos_distintos", 0)

        logger.info(
            "geoquimica_proxima: OK — %d amostras, %d analitos distintos, "
            "estratégicos=%s",
            total, n_analitos, estrateg,
        )

        out: dict[str, Any] = {
            "sucesso": True,
            "dados":   resultado,
            "mapa":    resultado.get("mapa", {"pontos": []}),
            "raio_km_efetivo": raio_km,
        }
        if raio_km < raio_pedido - 1e-6:
            out["aviso"] = (
                f"O raio pedido ({raio_pedido:g} km) excede o máximo da tool; "
                f"foi usado {raio_km:g} km."
            )
        return out

    # ==================================================================
    # Tool 18: geoquimica_detalhes_amostra
    # ==================================================================
    @mcp.tool()
    async def geoquimica_detalhes_amostra(
        id_amostra: str,
    ) -> dict[str, Any]:
        """
        Retorna **todos** os dados analíticos de **uma** amostra geoquímica CPRM
        pelo código de campo (``id_amostra``).

        Use quando o usuário pedir:
          - "detalhes da amostra 1182-LK-R-0039B"
          - "teores completos da amostra GEO:4212-PD-R-0010A"
          - "o que tem na amostra 1182-DB-R-0132A?"

        O documento no índice é ``GEO:{id_amostra}`` (prefixo ``GEO:`` é opcional
        na consulta). Plota o ponto no mapa quando houver coordenadas.

        Args:
            id_amostra: Código CPRM (ex.: ``1182-LK-R-0039B``, ``4212-PD-R-0037F``)
        """
        from mcp_servers.jazidas.queries.geoquimica import (
            executar_geoquimica_detalhes_amostra,
            normalize_id_amostra,
        )

        aid = normalize_id_amostra(id_amostra)
        if not aid:
            return {
                "sucesso": False,
                "mensagem": "Informe o código da amostra (ex.: 1182-LK-R-0039B).",
            }

        logger.info("geoquimica_detalhes_amostra: id=%r", aid)

        try:
            resultado = await executar_geoquimica_detalhes_amostra(
                os_service=os_service,
                id_amostra=aid,
            )
        except Exception as e:
            logger.error("geoquimica_detalhes_amostra: erro: %s", e)
            return {"sucesso": False, "mensagem": str(e)}

        if not resultado.get("sucesso"):
            return resultado

        logger.info(
            "geoquimica_detalhes_amostra: OK — %s (%d analitos)",
            aid,
            resultado.get("n_analitos", 0),
        )

        return {
            "sucesso": True,
            "dados": resultado,
            "mapa": resultado.get("mapa", {"pontos": []}),
        }

    logger.info(
        "Registered 18 tools: "
        "buscar_fornecedores, buscar_jazidas, detalhes_processo, "
        "jazidas_por_poligono, verificar_vigencia_substancia, "
        "consultar_cfem_processo, ranking_cfem, "
        "buscar_restricoes_geo, ocorrencias_minerais_proximas, "
        "consultar_mercado_mineral, principais_destinos_mineral, "
        "imoveis_car_proximos, imoveis_car_proprietario, "
        "areas_em_disponibilidade, afloramentos_geologicos_proximos, "
        "consultar_preco_mineral, geoquimica_proxima, geoquimica_detalhes_amostra"
    )
