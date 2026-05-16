"""
Empresas MCP Tools
===================

4 custom tools for the Empresas MCP Server:
    1. buscar_empresas         — Busca semântica de empresas por CNAE + raio
    2. empresas_por_poligono   — Busca por CNAE filtrada por polígono GeoJSON
                                  (ex: dentro de uma isócrona)
    3. detalhes_empresa        — Ficha completa cross 3 índices
    4. buscar_por_socio        — Busca reversa: empresas onde pessoa é sócia

All tools are registered via @mcp.tool() decorator and use
module-level services (os_service, redis_cache, embedding_service).
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.common.redis_cache import RedisCache
from mcp_servers.common.embeddings import EmbeddingService

logger = logging.getLogger("mcp.empresas.tools")

# Module-level references (set by register_tools)
mcp: FastMCP | None = None
os_service: OpenSearchService | None = None
redis_cache: RedisCache | None = None
embedding_service: EmbeddingService | None = None


def register_tools(
    server: FastMCP,
    os_svc: OpenSearchService,
    cache: RedisCache,
    emb: EmbeddingService,
) -> None:
    """
    Register all 3 tools on the MCP server instance.

    Called from server.py during startup.
    """
    global mcp, os_service, redis_cache, embedding_service
    mcp = server
    os_service = os_svc
    redis_cache = cache
    embedding_service = emb

    # Register tools by referencing the decorated functions
    # (the @mcp.tool() decorators below handle the actual registration)
    _register_buscar_empresas()
    _register_empresas_por_poligono()
    _register_detalhes_empresa()
    _register_buscar_por_socio()
    _register_risco_ambiental_empresa()
    _register_autuacoes_por_area()
    _register_buscar_empresa_cvm()

    logger.info("Empresas tools registered: 7 tools (4 originais + 2 IBAMA + 1 CVM)")


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════


def _extract_latlon(loc: object) -> tuple[float, float] | None:
    """
    Normalise a ``localizacao`` value into (lat, lon).

    Handles three formats that OpenSearch may return for geo_point fields:
      • Standard object  {"lat": -23.55, "lon": -46.63}   (most common)
      • GeoJSON Point    {"type": "Point", "coordinates": [-46.63, -23.55]}
      • Legacy string    "-23.55,-46.63"                   (rare)

    Returns None when the value is missing or cannot be parsed.
    """
    if not loc:
        return None

    if isinstance(loc, dict):
        # Standard geo_point object
        lat = loc.get("lat")
        lon = loc.get("lon")
        if lat is not None and lon is not None:
            try:
                return float(lat), float(lon)
            except (ValueError, TypeError):
                pass
        # GeoJSON Point: coordinates are [lon, lat]
        coords = loc.get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            try:
                return float(coords[1]), float(coords[0])
            except (ValueError, TypeError):
                pass

    if isinstance(loc, str) and "," in loc:
        try:
            parts = loc.split(",")
            return float(parts[0].strip()), float(parts[1].strip())
        except (ValueError, IndexError):
            pass

    return None


def _pontos_from_page(page_items: list[dict]) -> dict:
    """
    Build mapa.pontos directly from the current page of resultado items.

    This guarantees the map shows EXACTLY the same items as the chat list,
    since pontos are derived 1-to-1 from the paginated dados (not from all
    200 raw hits). Each resultado already carries geocoded lat/lon in its
    ``localizacao`` field (set by ``_geocode_empresa_addresses``), plus all
    enriched fields (endereco, telefone, email, capital_social, etc.).
    """
    pontos = []
    skipped_no_loc = 0
    skipped_no_coords = 0
    for item in page_items:
        coords = _extract_latlon(item.get("localizacao"))
        if coords is None:
            skipped_no_loc += 1
            continue
        lat, lon = coords
        if lat == 0.0 and lon == 0.0:
            skipped_no_coords += 1
            continue
        pontos.append({
            "lat": lat,
            "lon": lon,
            "tipo": "empresa",
            "cnpj_basico": item.get("cnpj_basico", ""),
            "cnpj_completo": item.get("cnpj_completo", ""),
            "nome": item.get("nome_fantasia") or item.get("razao_social", ""),
            "razao_social": item.get("razao_social"),
            "nome_fantasia": item.get("nome_fantasia"),
            "municipio": item.get("municipio"),
            "uf": item.get("uf"),
            "telefone": item.get("telefone"),
            "email": item.get("email"),
            "endereco": item.get("endereco"),
            "porte": item.get("porte"),
            "capital_social": item.get("capital_social"),
            "cnae_descricao": item.get("cnae_descricao"),
            "distancia_km": item.get("distancia_km"),
        })
    logger.info(
        "_pontos_from_page: total=%d, com_coords=%d, sem_localizacao=%d, sem_coords=%d",
        len(page_items), len(pontos), skipped_no_loc, skipped_no_coords,
    )
    return {"pontos": pontos, "total_pontos": len(pontos)}


# ═══════════════════════════════════════════════════════════════════════
# TOOL 1: buscar_empresas
# ═══════════════════════════════════════════════════════════════════════


def _register_buscar_empresas():
    """Register buscar_empresas tool."""

    @mcp.tool()
    async def buscar_empresas(
        termo_busca: str | None = None,
        codigos_cnae: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        raio_km: float = 30.0,
        uf: str | None = None,
        apenas_ativas: bool = False,
        incluir_contatos: bool = True,
        incluir_geometria: bool = False,
        incluir_mei: bool = True,
        pagina: int = 1,
        por_pagina: int = 20,
        ordenar_por: str = "distancia",
    ) -> dict[str, Any]:
        """
        Busca empresas comerciais por atividade econômica (CNAE) e localização.
        Use para encontrar distribuidores, fabricantes e comerciantes de materiais
        de construção — especialmente produtos industrializados como cimento, concreto,
        ferro/aço, madeira, PVC, tintas, argamassa, telhas, tijolos, vidros, cal, gesso,
        e também para transportadoras, pré-moldados, pavimentação e serviços de construção.
        Para substâncias minerais brutas (areia, brita, cascalho, calcário), use buscar_fornecedores.

        Aceita busca semântica (termo_busca) OU códigos CNAE diretos.
        Combina resolução k-NN de CNAE + filtros flat + nested cnaeFiscalSecundaria.

        Fluxo: rfb_cnae_v001 (k-NN) → rfb_cnpj_v003 (flat + nested + geo)

        Args:
            termo_busca: Busca semântica (ex: "cimento", "madeira tratada", "pré-moldados")
            codigos_cnae: OU códigos CNAE diretos (ex: ["4930-2/01"])
            latitude: Latitude para busca geoespacial
            longitude: Longitude para busca geoespacial
            raio_km: Raio de busca em km (default: 30)
            uf: Filtrar por UF (ex: "SP")
            apenas_ativas: Filtrar apenas empresas ativas (default: false — retorna ativas e inativas)
            incluir_contatos: Incluir telefone/email/endereço (default: true)
            incluir_geometria: Incluir fronteiras municípios para mapa (default: false)
            incluir_mei: Incluir Microempreendedores Individuais (MEI) nos resultados (default: true). Passe false para excluir MEI da busca.
            pagina: Página de resultados (default: 1)
            por_pagina: Resultados por página (default: 20, max: 50)
            ordenar_por: "distancia" (mais próximas primeiro) ou "capital_social" (maior capital social primeiro — usar quando o usuário pedir "maiores", "principais", "maior porte")
        """
        from mcp_servers.empresas.queries.cnae import CnaeResolver
        from mcp_servers.empresas.cache import EmpresasCache

        lat_f = float(latitude) if latitude is not None else None
        lon_f = float(longitude) if longitude is not None else None
        cnae_list: list[str] | None = None
        if codigos_cnae:
            cnae_list = [c.strip() for c in codigos_cnae.split(",") if c.strip()]

        logger.info(
            f"buscar_empresas: termo='{termo_busca}', "
            f"codigos={cnae_list}, geo=({lat_f},{lon_f}), "
            f"raio={raio_km}km, uf={uf}"
        )

        # ── Validation ──
        if not termo_busca and not cnae_list:
            return {
                "sucesso": False,
                "mensagem": (
                    "Forneça 'termo_busca' (busca semântica) OU 'codigos_cnae' "
                    "(códigos diretos). Pelo menos um é obrigatório."
                ),
            }

        empresas_cache = EmpresasCache(redis_cache)

        # ── Check pagination cache ──
        cache_params = {
            "termo": termo_busca,
            "codigos": cnae_list,
            "lat": lat_f,
            "lon": lon_f,
            "raio": raio_km,
            "uf": uf,
            "ativas": apenas_ativas,
            "contatos": incluir_contatos,
            "geo": incluir_geometria,
            "mei": incluir_mei,
            "ordenar": ordenar_por,
        }

        cache_id = empresas_cache._search_key(
            empresas_cache.PREFIX_SEARCH, cache_params
        )
        cached_page = await empresas_cache.get_page(cache_id, pagina, por_pagina)

        if cached_page:
            page_items, meta = cached_page
            logger.info(
                f"buscar_empresas: Cache hit — page {pagina}/{meta.total_paginas}"
            )
            response = empresas_cache.build_paginated_response(
                page_items, meta, cache_id
            )
            response["mapa"] = _pontos_from_page(page_items)
            return response

        # ── Step 1: Resolve CNAE codes ──
        if cnae_list:
            resolucao = CnaeResolver.from_codigos(cnae_list)
        else:
            resolver = CnaeResolver(os_service, embedding_service, redis_cache)
            resolucao = await resolver.resolver(termo_busca)

        if not resolucao.encontrou:
            return {
                "sucesso": False,
                "mensagem": (
                    f"Nenhum CNAE encontrado para '{termo_busca}'. "
                    "Tente termos mais específicos (ex: 'transporte rodoviário de carga') "
                    "ou forneça códigos CNAE diretamente."
                ),
                "resolucao": {
                    "metodo": resolucao.metodo,
                    "codigos": [],
                    "termo": resolucao.termo_original,
                },
            }

        # ── Step 2: Search rfb_cnpj_v003 ──
        try:
            from mcp_servers.empresas.queries.empresas import executar_busca_empresas

            resultado = await executar_busca_empresas(
                os_service=os_service,
                resolucao=resolucao,
                latitude=lat_f,
                longitude=lon_f,
                raio_km=raio_km,
                uf=uf,
                apenas_ativas=apenas_ativas,
                incluir_contatos=incluir_contatos,
                incluir_geometria=incluir_geometria,
                ordenar_por=ordenar_por,
                incluir_mei=incluir_mei,
            )
        except Exception as e:
            logger.error(f"buscar_empresas: Query failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro na busca: {str(e)}",
            }

        # ── Cache full results for pagination ──
        all_results = resultado.get("resultados", [])
        cache_id = await empresas_cache.store_search(
            empresas_cache.PREFIX_SEARCH,
            cache_params,
            all_results,
        )

        # ── Paginate ──
        por_pagina = min(por_pagina, 50)
        offset = (pagina - 1) * por_pagina
        page_items = all_results[offset : offset + por_pagina]

        import math
        total = resultado.get("total", len(all_results))
        total_paginas = max(1, math.ceil(total / por_pagina))

        logger.info(
            f"buscar_empresas: OK — {total} results, "
            f"page {pagina}/{total_paginas}, "
            f"CNAE method={resolucao.metodo}"
        )

        return {
            "sucesso": True,
            "meta": {
                "total": total,
                "pagina": pagina,
                "por_pagina": por_pagina,
                "total_paginas": total_paginas,
            },
            "dados": page_items,
            "cache_id": cache_id,
            "resolucao": {
                "metodo": resolucao.metodo,
                "cnaes_resolvidos": resolucao.codigos,
                "termo": resolucao.termo_original,
            },
            "mapa": _pontos_from_page(page_items),
        }


# ═══════════════════════════════════════════════════════════════════════
# TOOL 2: empresas_por_poligono
# ═══════════════════════════════════════════════════════════════════════


def _register_empresas_por_poligono():
    """Register empresas_por_poligono tool."""

    @mcp.tool()
    async def empresas_por_poligono(
        geometry: dict,
        termo_busca: str | None = None,
        codigos_cnae: str | None = None,
        uf: str | None = None,
        apenas_ativas: bool = False,
        incluir_contatos: bool = True,
        incluir_geometria: bool = False,
        incluir_mei: bool = True,
        pagina: int = 1,
        por_pagina: int = 20,
    ) -> dict[str, Any]:
        """
        Busca empresas (CNPJ ativos) cujo ponto cadastral CAI DENTRO de um
        polígono GeoJSON. Use sempre que o usuário pedir empresas "dentro de
        uma isócrona", "na área de até X minutos", "no polígono de Y", em vez
        de raio circular (geometricamente diferente).

        Combina o filtro de polígono com o mesmo motor de CNAE de
        ``buscar_empresas`` (semântico ou códigos diretos).

        Args:
            geometry: Polígono GeoJSON ({"type":"Polygon"|"MultiPolygon","coordinates":[...]}).
                Tipicamente o `feature.geometry` retornado por
                ``geo__calcular_isocrona`` ou ``jazidas__obter_poligono``.
            termo_busca: Busca semântica (ex: "cimento", "pré-moldados",
                "transportadoras"). Resolvido via k-NN para CNAEs.
            codigos_cnae: Alternativa direta — códigos CNAE separados por vírgula.
            uf: Filtrar por UF (ex: "SP")
            apenas_ativas: Filtrar apenas empresas ativas (default: false — retorna ativas e inativas)
            incluir_contatos: Incluir telefone/email/endereço (default: true)
            incluir_geometria: Incluir fronteiras de municípios para o mapa (default: false)
            incluir_mei: Incluir MEIs (default: true). Passe false para excluir.
            pagina: Página de resultados (default: 1)
            por_pagina: Resultados por página (default: 20, max: 50)
        """
        from mcp_servers.empresas.queries.cnae import CnaeResolver
        from mcp_servers.empresas.queries.empresas import (
            executar_busca_empresas_por_poligono,
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

        cnae_list: list[str] | None = None
        if codigos_cnae:
            cnae_list = [c.strip() for c in codigos_cnae.split(",") if c.strip()]

        if not termo_busca and not cnae_list:
            return {
                "sucesso": False,
                "mensagem": (
                    "Forneça 'termo_busca' (busca semântica) OU 'codigos_cnae'. "
                    "Sem filtro de CNAE a busca retorna empresas demais."
                ),
            }

        # ── CNAE resolution ──
        resolver = CnaeResolver(os_service, embedding_service, redis_cache)
        if cnae_list:
            from mcp_servers.empresas.schemas import ResolucaoCnae
            resolucao = ResolucaoCnae(
                codigos=cnae_list,
                metodo="explicit",
                termo_original=",".join(cnae_list),
            )
        else:
            resolucao = await resolver.resolver(termo_busca or "")
            if not resolucao.encontrou:
                return {
                    "sucesso": False,
                    "mensagem": (
                        f"Não consegui resolver '{termo_busca}' em códigos CNAE. "
                        "Tente um termo mais específico (ex.: 'concreto pré-moldado')."
                    ),
                }

        logger.info(
            f"empresas_por_poligono: geom={geometry.get('type')}, "
            f"termo='{termo_busca}', codigos={cnae_list}, uf={uf}, "
            f"cnaes_resolvidos={resolucao.codigos[:5]}"
        )

        try:
            data = await executar_busca_empresas_por_poligono(
                os_service=os_service,
                geometry=geometry,
                resolucao=resolucao,
                uf=uf,
                apenas_ativas=apenas_ativas,
                incluir_contatos=incluir_contatos,
                incluir_geometria=incluir_geometria,
                incluir_mei=incluir_mei,
            )
        except ValueError as e:
            return {
                "sucesso": False,
                "mensagem": f"Polígono inválido: {e}",
            }
        except Exception as e:
            logger.exception("empresas_por_poligono: query failed")
            return {
                "sucesso": False,
                "mensagem": f"Erro ao consultar empresas no polígono: {e}",
            }

        total = data["total"]
        por_pagina = min(max(por_pagina, 1), 50)
        from_offset = (max(pagina, 1) - 1) * por_pagina
        all_results = data.get("resultados", [])
        page_items = all_results[from_offset:from_offset + por_pagina]
        total_paginas = (total + por_pagina - 1) // por_pagina if total > 0 else 0

        return {
            "sucesso": True,
            "filtro": "poligono",
            "geometria_tipo": geometry.get("type"),
            "total": total,
            "pagina": pagina,
            "por_pagina": por_pagina,
            "total_paginas": total_paginas,
            "resultados": page_items,
            "resolucao": {
                "metodo": resolucao.metodo,
                "cnaes_resolvidos": resolucao.codigos,
                "termo": resolucao.termo_original,
            },
            "mapa": _pontos_from_page(page_items),
        }


# ═══════════════════════════════════════════════════════════════════════
# TOOL 3: detalhes_empresa
# ═══════════════════════════════════════════════════════════════════════


def _register_detalhes_empresa():
    """Register detalhes_empresa tool."""

    @mcp.tool()
    async def detalhes_empresa(
        cnpj_basico: str,
        incluir_socios: bool = True,
        incluir_processos_anm: bool = False,
        incluir_cnaes_detalhados: bool = True,
    ) -> dict[str, Any]:
        """
        Ficha completa de uma empresa com enriquecimento cross-index.

        Cruza até 3 índices:
          1. mr_empresas_v001 (Receita Federal — dados completos: sócios, CNAEs,
             contatos, situação cadastral). Fonte primária.
          2. mr_cnae_v001 — hierarquia CNAE completa.
          3. mr_jazidas_v001 — fallback automático quando o CNPJ não está na
             base RFB (empresas extintas, gaps de ETL, CNPJs antigos). Retorna
             dados do titular extraídos do processo ANM com aviso no campo
             'dados.fonte' = "anm_jazidas".

        IMPORTANTE: se já tiver o número do processo ANM, prefira
        'jazidas__detalhes_processo' (retorna dados do titular do processo
        mais dados geocomputados da jazida em uma única chamada).

        Args:
            cnpj_basico: CNPJ básico 8 dígitos (ex: "60730348" ou "21.624.671")
            incluir_socios: Incluir sócios com qualificação (default: true)
            incluir_processos_anm: Incluir resumo de processos ANM (default: false)
            incluir_cnaes_detalhados: Incluir hierarquia CNAE completa (default: true)
        """
        from mcp_servers.empresas.cache import EmpresasCache

        logger.info(
            f"detalhes_empresa: cnpj='{cnpj_basico}', "
            f"socios={incluir_socios}, anm={incluir_processos_anm}, "
            f"cnaes={incluir_cnaes_detalhados}"
        )

        cnpj_basico = cnpj_basico.strip().replace(".", "").replace("/", "").replace("-", "")
        if not cnpj_basico or len(cnpj_basico) < 8:
            return {
                "sucesso": False,
                "mensagem": (
                    "CNPJ básico inválido. Forneça 8 dígitos "
                    "(ex: '60730348' ou '12345678')."
                ),
            }
        # Take first 8 digits
        cnpj_basico = cnpj_basico[:8]

        empresas_cache = EmpresasCache(redis_cache)

        # ── Check cache ──
        cached = await empresas_cache.get_empresa(cnpj_basico)
        if cached:
            logger.info(f"detalhes_empresa: Cache hit for {cnpj_basico}")
            return {"sucesso": True, "dados": cached}

        # ── Fetch from OpenSearch ──
        try:
            from mcp_servers.empresas.queries.detalhes import executar_detalhes_empresa

            resultado = await executar_detalhes_empresa(
                os_service=os_service,
                cnpj_basico=cnpj_basico,
                incluir_socios=incluir_socios,
                incluir_processos_anm=incluir_processos_anm,
                incluir_cnaes_detalhados=incluir_cnaes_detalhados,
            )
        except Exception as e:
            logger.error(
                f"detalhes_empresa: Query failed for '{cnpj_basico}': {e}"
            )
            return {
                "sucesso": False,
                "mensagem": f"Erro ao buscar detalhes: {str(e)}",
            }

        if resultado is None:
            # ── Fallback: try ANM jazidas index for titular data ──
            logger.info(
                f"detalhes_empresa: CNPJ '{cnpj_basico}' not in RFB index, "
                "trying ANM fallback..."
            )
            try:
                from mcp_servers.empresas.queries.detalhes import buscar_titular_no_anm
                anm_resultado = await buscar_titular_no_anm(os_service, cnpj_basico)
                if anm_resultado:
                    logger.info(
                        f"detalhes_empresa: ANM fallback OK for '{cnpj_basico}' — "
                        f"razao_social={anm_resultado.get('razao_social')}"
                    )
                    await empresas_cache.store_empresa(cnpj_basico, anm_resultado)
                    return {"sucesso": True, "dados": anm_resultado}
            except Exception as e:
                logger.warning(f"detalhes_empresa: ANM fallback failed: {e}")

            return {
                "sucesso": False,
                "mensagem": (
                    f"Empresa com CNPJ básico '{cnpj_basico}' não encontrada "
                    "no cadastro da Receita Federal nem no índice ANM. "
                    "Se tiver o número do processo ANM, use a tool "
                    "'jazidas__detalhes_processo' que retorna os dados do "
                    "titular diretamente a partir do processo."
                ),
            }

        # ── Enrich: IBAMA risk summary (lightweight, pré-agregado) ──
        try:
            from mcp_servers.empresas.queries.autuacoes import fetch_resumo_ibama_para_empresa
            risco_ibama = await fetch_resumo_ibama_para_empresa(os_service, cnpj_basico)
            if risco_ibama:
                resultado["risco_ambiental_ibama"] = risco_ibama
        except Exception as e:
            logger.warning(f"detalhes_empresa: falha ao buscar risco IBAMA: {e}")

        # ── Cache result ──
        await empresas_cache.store_empresa(cnpj_basico, resultado)

        logger.info(f"detalhes_empresa: OK — {cnpj_basico}")

        return {"sucesso": True, "dados": resultado}


# ═══════════════════════════════════════════════════════════════════════
# TOOL 3: buscar_por_socio
# ═══════════════════════════════════════════════════════════════════════


def _register_buscar_por_socio():
    """Register buscar_por_socio tool."""

    @mcp.tool()
    async def buscar_por_socio(
        nome_socio: str | None = None,
        cpf_cnpj_socio: str | None = None,
        uf: str | None = None,
        apenas_ativas: bool = False,
        pagina: int = 1,
        por_pagina: int = 20,
    ) -> dict[str, Any]:
        """
        Busca reversa: encontra empresas onde uma pessoa é sócia.

        Usa ``match`` em ``socios_nomes`` e/ou ``term`` em ``socios_cpf_cnpj`` no
        índice ``mr_empresas_v001``.

        Use cases: due diligence, compliance, mapeamento de participações.

        Args:
            nome_socio: Nome do sócio (busca textual)
            cpf_cnpj_socio: CPF ou CNPJ do sócio (busca exata, 11 ou 14 dígitos)
            uf: Filtrar por UF (ex: "SP")
            apenas_ativas: Filtrar apenas empresas ativas (default: false — retorna ativas e inativas)
            pagina: Página de resultados (default: 1)
            por_pagina: Resultados por página (default: 10, max: 50)
        """
        from mcp_servers.empresas.cache import EmpresasCache

        logger.info(
            f"buscar_por_socio: nome='{nome_socio}', "
            f"cpf_cnpj='{cpf_cnpj_socio}', uf={uf}"
        )

        # ── Validation ──
        if not nome_socio and not cpf_cnpj_socio:
            return {
                "sucesso": False,
                "mensagem": (
                    "Forneça 'nome_socio' (busca textual) OU 'cpf_cnpj_socio' "
                    "(busca exata). Pelo menos um é obrigatório."
                ),
            }

        # Normalize CPF/CNPJ (remove formatting)
        if cpf_cnpj_socio:
            cpf_cnpj_socio = (
                cpf_cnpj_socio.strip()
                .replace(".", "")
                .replace("/", "")
                .replace("-", "")
            )

        empresas_cache = EmpresasCache(redis_cache)

        # ── Check pagination cache ──
        cache_params = {
            "nome": nome_socio,
            "cpf_cnpj": cpf_cnpj_socio,
            "uf": uf,
            "ativas": apenas_ativas,
        }

        cache_id = empresas_cache._search_key(
            empresas_cache.PREFIX_SOCIO, cache_params
        )
        cached_page = await empresas_cache.get_page(cache_id, pagina, por_pagina)

        if cached_page:
            page_items, meta = cached_page
            logger.info(
                f"buscar_por_socio: Cache hit — page {pagina}/{meta.total_paginas}"
            )
            response = empresas_cache.build_paginated_response(
                page_items, meta, cache_id
            )
            response["socio_buscado"] = {
                "nome": nome_socio,
                "cpf_cnpj": cpf_cnpj_socio,
            }
            return response

        # ── Search rfb_cnpj_v003 (nested socios) ──
        try:
            from mcp_servers.empresas.queries.socios import executar_busca_por_socio

            resultado = await executar_busca_por_socio(
                os_service=os_service,
                nome_socio=nome_socio,
                cpf_cnpj_socio=cpf_cnpj_socio,
                uf=uf,
                apenas_ativas=apenas_ativas,
            )
        except Exception as e:
            logger.error(f"buscar_por_socio: Query failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro na busca por sócio: {str(e)}",
            }

        # ── Cache full results for pagination ──
        all_results = resultado.get("resultados", [])
        cache_id = await empresas_cache.store_search(
            empresas_cache.PREFIX_SOCIO,
            cache_params,
            all_results,
        )

        # ── Paginate ──
        por_pagina = min(por_pagina, 50)
        offset = (pagina - 1) * por_pagina
        page_items = all_results[offset : offset + por_pagina]

        import math
        total = resultado.get("total", len(all_results))
        total_paginas = max(1, math.ceil(total / por_pagina))

        logger.info(
            f"buscar_por_socio: OK — {total} empresas, "
            f"page {pagina}/{total_paginas}"
        )

        return {
            "sucesso": True,
            "meta": {
                "total": total,
                "pagina": pagina,
                "por_pagina": por_pagina,
                "total_paginas": total_paginas,
            },
            "socio_buscado": {
                "nome": nome_socio,
                "cpf_cnpj": cpf_cnpj_socio,
            },
            "dados": page_items,
            "cache_id": cache_id,
        }


# ═══════════════════════════════════════════════════════════════════════
# TOOL 5: risco_ambiental_empresa
# ═══════════════════════════════════════════════════════════════════════


def _register_risco_ambiental_empresa():
    """Register risco_ambiental_empresa tool."""

    @mcp.tool()
    async def risco_ambiental_empresa(
        cnpj_basico: str,
        max_registros: int = 20,
    ) -> dict[str, Any]:
        """
        Histórico completo de autuações, embargos e apreensões IBAMA de uma empresa.

        Consulta o índice mr_autuacoes_v001 filtrando pelo CNPJ básico da empresa.
        Retorna:
          - Nível de risco classificado (SEM_RISCO / BAIXO / MÉDIO / ALTO / CRÍTICO)
          - Totais por tipo (autuações, embargos, apreensões)
          - Valor total de multas em BRL (apenas valores pós-Plano Real confiáveis)
          - Lista dos registros individuais mais recentes

        Níveis de risco:
          CRÍTICO  → embargo ativo ou multa > R$ 10M
          ALTO     → ≥ 5 autuações ou multa > R$ 1M
          MÉDIO    → 1-4 autuações ou multa > R$ 100k
          BAIXO    → registros existem mas valores pequenos
          SEM_RISCO→ nenhum registro encontrado

        Args:
            cnpj_basico: CNPJ básico de 8 dígitos (ex: "60730348")
            max_registros: Número máximo de registros individuais a retornar (default: 20)
        """
        cnpj_basico = (
            cnpj_basico.strip().replace(".", "").replace("/", "").replace("-", "")
        )[:8]

        if len(cnpj_basico) < 8:
            return {
                "sucesso": False,
                "mensagem": "CNPJ básico inválido — forneça 8 dígitos.",
            }

        try:
            from mcp_servers.empresas.queries.autuacoes import executar_risco_ambiental_empresa

            resultado = await executar_risco_ambiental_empresa(
                os_service=os_service,
                cnpj_basico=cnpj_basico,
                max_registros=max_registros,
            )
        except Exception as e:
            logger.error(f"risco_ambiental_empresa: erro para {cnpj_basico}: {e}")
            return {"sucesso": False, "mensagem": str(e)}

        nivel = resultado.get("resumo", {}).get("nivel_risco", "SEM_RISCO")
        logger.info(
            f"risco_ambiental_empresa: {cnpj_basico} → "
            f"nivel={nivel}, registros={resultado.get('total_registros_index', 0)}"
        )

        return {"sucesso": True, "dados": resultado}


# ═══════════════════════════════════════════════════════════════════════
# TOOL 6: autuacoes_por_area
# ═══════════════════════════════════════════════════════════════════════


def _register_autuacoes_por_area():
    """Register autuacoes_por_area tool."""

    @mcp.tool()
    async def autuacoes_por_area(
        lat: float,
        lon: float,
        raio_km: float = 10.0,
        tipo: str | None = None,
        apenas_ativos: bool = False,
    ) -> dict[str, Any]:
        """
        Autuações, embargos e apreensões IBAMA dentro de um raio geográfico.

        Busca infrações ambientais registradas nas proximidades de uma coordenada.
        Ideal para avaliar o histórico ambiental de uma região antes de analisar
        uma jazida ou empreendimento mineral.

        Retorna:
          - Nível de risco da área (mesmo critério de risco_ambiental_empresa)
          - Totais por tipo com principais infratores
          - Lista de registros próximos ordenados por distância

        Args:
            lat:          Latitude da coordenada central (ex: -19.9167)
            lon:          Longitude da coordenada central (ex: -43.9345)
            raio_km:      Raio de busca em quilômetros (default: 10km, max recomendado: 50km)
            tipo:         Filtrar por tipo — "Autuacao", "Embargo" ou "Apreensao" (opcional)
            apenas_ativos: Excluir registros cancelados (default: false)
        """
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return {
                "sucesso": False,
                "mensagem": "Coordenadas inválidas. lat deve estar entre -90 e 90, lon entre -180 e 180.",
            }

        raio_km = min(max(raio_km, 0.5), 100.0)

        tipos_validos = {"Autuacao", "Embargo", "Apreensao"}
        if tipo and tipo not in tipos_validos:
            return {
                "sucesso": False,
                "mensagem": f"Tipo inválido. Use: {', '.join(sorted(tipos_validos))}",
            }

        try:
            from mcp_servers.empresas.queries.autuacoes import executar_autuacoes_por_area

            resultado = await executar_autuacoes_por_area(
                os_service=os_service,
                lat=lat,
                lon=lon,
                raio_km=raio_km,
                tipo=tipo,
                apenas_ativos=apenas_ativos,
            )
        except Exception as e:
            logger.error(f"autuacoes_por_area: erro lat={lat} lon={lon}: {e}")
            return {"sucesso": False, "mensagem": str(e)}

        nivel = resultado.get("resumo", {}).get("nivel_risco_area", "SEM_RISCO")
        logger.info(
            f"autuacoes_por_area: lat={lat} lon={lon} raio={raio_km}km → "
            f"nivel={nivel}, total={resultado.get('resumo', {}).get('total_encontrados', 0)}"
        )

        return {"sucesso": True, "dados": resultado}

    # ==================================================================
    # Tool 7: alertas_monitoramento
    # ==================================================================
    @mcp.tool()
    async def alertas_monitoramento(
        numero_processo: str | None = None,
        cnpj_basico: str | None = None,
        tipo_evento: str | None = None,
        apenas_acao: bool = False,
        apenas_nao_lidos: bool = False,
        max_registros: int = 20,
    ) -> dict[str, Any]:
        """
        Consulta alertas de monitoramento ativos para processos ANM.

        Retorna eventos gerados automaticamente pelo bot de monitoramento:
          - **PRAZO_ALERT**: processo com vencimento próximo (30/60/90 dias)
            ou vencido mas ainda ativo (risco de nulidade)
          - **STATUS_CHANGE**: processo que mudou de fase ou de titular
          - **DOU_PUBLICACAO**: portaria ANM ou ato publicado no DOU

        Cada alerta tem nível de relevância: ALTA (ação urgente), MEDIA ou BAIXA.
        O campo `acao_necessaria=true` marca os alertas que exigem providência imediata.

        Use quando o usuário perguntar:
          - "O processo ANM X tem algum alerta ou está prestes a vencer?"
          - "Quais processos da empresa X precisam de atenção urgente?"
          - "Há alguma portaria ANM publicada no DOU sobre esta empresa?"
          - "A empresa mudou de titular em algum processo recentemente?"
          - "Quais processos vencem nos próximos 30 dias?"
          - "Existe algum alerta de prazo para este processo de lavra?"

        Args:
            numero_processo: Número do processo ANM (ex: "860.201/2020") — opcional
            cnpj_basico:     CNPJ básico da empresa (8 dígitos) — opcional
            tipo_evento:     Filtrar por tipo: "PRAZO_ALERT", "STATUS_CHANGE"
                             ou "DOU_PUBLICACAO" — opcional
            apenas_acao:     Se True, retorna só alertas com acao_necessaria=True
            apenas_nao_lidos: Se True, retorna só alertas ainda não marcados como lidos
            max_registros:   Máximo de alertas a retornar (default: 20)
        """
        logger.info(
            f"alertas_monitoramento: processo={numero_processo} "
            f"cnpj={cnpj_basico} tipo={tipo_evento} acao={apenas_acao}"
        )

        try:
            from mcp_servers.empresas.queries.monitoring import executar_alertas_processo

            resultado = await executar_alertas_processo(
                os_service=os_service,
                numero_processo=numero_processo,
                cnpj_titular=cnpj_basico,
                tipo_evento=tipo_evento,
                apenas_nao_lidos=apenas_nao_lidos,
                apenas_acao=apenas_acao,
                max_registros=max_registros,
            )
        except Exception as e:
            logger.error(f"alertas_monitoramento: erro: {e}")
            return {"sucesso": False, "mensagem": str(e)}

        if "erro" in resultado:
            return {"sucesso": False, "mensagem": resultado["erro"]}

        nivel  = resultado.get("resumo", {}).get("nivel_geral", "SEM_ALERTAS")
        total  = resultado.get("resumo", {}).get("total_alertas", 0)
        logger.info(f"alertas_monitoramento: OK — {total} alertas nivel={nivel}")
        return {"sucesso": True, "dados": resultado}

    # ==================================================================
    # Tool 8: resumo_carteira_alertas
    # ==================================================================
    @mcp.tool()
    async def resumo_carteira_alertas(
        cnpjs: list[str] | None = None,
        apenas_acao: bool = True,
        max_registros: int = 20,
    ) -> dict[str, Any]:
        """
        Resumo executivo de alertas de monitoramento para uma carteira
        de empresas (lista de CNPJs).

        Retorna os alertas mais críticos ordenados por urgência, com
        contagem por tipo, relevância e total de ações necessárias.

        Use quando o usuário perguntar:
          - "Qual é o panorama geral de alertas da carteira de clientes?"
          - "Das empresas X, Y e Z, qual tem mais alertas urgentes?"
          - "Quais são os processos mais críticos para monitorar esta semana?"

        Args:
            cnpjs:         Lista de CNPJs básicos (8 dígitos) das empresas
            apenas_acao:   Se True (padrão), retorna só ações necessárias
            max_registros: Máximo de alertas a retornar (default: 20)
        """
        logger.info(
            f"resumo_carteira_alertas: cnpjs={cnpjs} apenas_acao={apenas_acao}"
        )

        try:
            from mcp_servers.empresas.queries.monitoring import executar_resumo_carteira

            resultado = await executar_resumo_carteira(
                os_service=os_service,
                cnpjs=cnpjs,
                apenas_acao=apenas_acao,
                max_registros=max_registros,
            )
        except Exception as e:
            logger.error(f"resumo_carteira_alertas: erro: {e}")
            return {"sucesso": False, "mensagem": str(e)}

        if "erro" in resultado:
            return {"sucesso": False, "mensagem": resultado["erro"]}

        nivel = resultado.get("resumo", {}).get("nivel_geral", "SEM_ALERTAS")
        logger.info(f"resumo_carteira_alertas: OK — nivel={nivel}")
        return {"sucesso": True, "dados": resultado}


# ═══════════════════════════════════════════════════════════════════════
# Tool 9: buscar_empresa_cvm
# ═══════════════════════════════════════════════════════════════════════


def _register_buscar_empresa_cvm() -> None:

    @mcp.tool()
    async def buscar_empresa_cvm(
        nome: str | None = None,
        cnpj: str | None = None,
        apenas_ativas: bool = True,
        apenas_listadas_bolsa: bool = False,
        max_resultados: int = 10,
    ) -> dict[str, Any]:
        """
        Busca companhias abertas registradas na CVM (Comissão de Valores Mobiliários)
        no índice mr_cvm_listadas_v001. Cobre empresas dos setores de Extração Mineral,
        Metalurgia/Siderurgia, Petróleo & Gás e Petroquímicos, além de qualquer empresa
        que seja titular de processo ANM ou pagante de CFEM.

        Use quando o usuário perguntar sobre:
          - "A VALE está listada na bolsa?"
          - "Qual é a situação da CSN na CVM?"
          - "Que mineradoras são companhias abertas?"
          - "Código CVM da Bradespar"
          - "Empresa X é listada na B3?"

        A CVM é a fonte regulatória oficial; o índice inclui:
          - Dados cadastrais: situação, setor, tipo de mercado (Bolsa/Balcão),
            categoria de registro (A ou B), datas, auditor e contatos.
          - Dados financeiros do DFP (quando disponíveis): ativo_total,
            receita_bruta, resultado_bruto, lucro_liquido — exercício mais recente
            em BRL com formatação legível (ex: "R$ 476,09 bilhões").

        Args:
            nome:                  Razão social ou nome comercial (busca por similaridade)
            cnpj:                  CNPJ completo (14 dígitos, com ou sem pontuação)
                                   ou CNPJ básico (8 dígitos)
            apenas_ativas:         Se True (padrão), retorna somente companhias com
                                   situação ATIVO na CVM
            apenas_listadas_bolsa: Se True, filtra apenas empresas com TP_MERC = BOLSA (B3)
            max_resultados:        Número máximo de resultados (padrão: 10)
        """
        logger.info(
            f"buscar_empresa_cvm: nome={nome!r} cnpj={cnpj} "
            f"ativas={apenas_ativas} bolsa={apenas_listadas_bolsa}"
        )

        try:
            from mcp_servers.empresas.queries.cvm import executar_buscar_empresa_cvm

            resultado = await executar_buscar_empresa_cvm(
                os_service=os_service,
                nome=nome,
                cnpj=cnpj,
                apenas_ativas=apenas_ativas,
                apenas_listadas_bolsa=apenas_listadas_bolsa,
                max_resultados=max_resultados,
            )
        except Exception as e:
            logger.error(f"buscar_empresa_cvm: erro: {e}")
            return {"sucesso": False, "mensagem": str(e)}

        if "erro" in resultado:
            return {"sucesso": False, "mensagem": resultado["erro"]}

        total = resultado.get("total", 0)
        logger.info(f"buscar_empresa_cvm: OK — {total} resultado(s)")
        return {"sucesso": True, "dados": resultado}
