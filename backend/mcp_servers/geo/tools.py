"""
Geo MCP Tools
==============

Regista até 18 tools (13 OpenSearch + 5 Azure quando a chave estiver configurada):

    OpenSearch (ibge_municipio_v001):
        1. buscar_municipio          — lookup by name/code/UF
        2. municipio_por_coordenada  — geo_shape contains (point-in-polygon)
        3. obter_poligono            — GeoJSON Feature export
        4. municipios_em_raio        — geo_distance + distance sort

    OpenSearch (mr_biomas_v001, mr_provincias_v001, mr_sigef_v001):
        5. bioma_por_coordenada      — geo_shape contains → bioma do ponto
        6. provincia_por_coordenada  — geo_shape contains → província geológica
        7. imoveis_rurais_em_area    — geo_distance → imóveis SIGEF próximos

    OpenSearch (mr_portos_v001): 8–10 portos
    OpenSearch (mr_ferrovias_v001): 11–13 malha ferroviária (buscar_ferrovia, ferrovias_proximas, obter_geometria_ferrovia)

    Azure Maps REST APIs:
        14. calcular_rota            — Route Directions (truck/car)
        15. comparar_rotas           — batch 1 origem × N destinos
        16. calcular_isocrona        — Route Range
        17. geocodificar             — Search Fuzzy + reverse
        18. plotar_endereco          — Pin no mapa

Each tool follows the pattern:
    1. Check Redis cache → return if hit
    2. Execute query (OpenSearch or Azure Maps HTTP)
    3. Format response
    4. Store in Redis cache
    5. Return structured result
"""

import asyncio
import logging
import math
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.common.config import mcp_settings
from mcp_servers.common.opensearch_client import OpenSearchService
from mcp_servers.common.redis_cache import RedisCache
from mcp_servers.geo.cache import GeoCache
from mcp_servers.geo.services.place_resolver import resolve_place_for_route

logger = logging.getLogger("mcp.geo.tools")

TOOLS_REGISTERED = 0

# Limite acima do qual consideramos que o ponto solicitado pelo usuário (jazida,
# endereço cadastral, coordenada manual…) não foi alcançado pelo grafo viário do
# Azure Maps. O roteador faz "snap" para o nó rodoviário mais próximo: se a
# diferença for maior que isto, o trecho final/inicial é off-road real e
# precisa ser reportado ao LLM e desenhado de forma diferente no mapa.
GAP_OFF_ROAD_THRESHOLD_KM = 0.1


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância de grande círculo entre dois pontos em quilômetros."""
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


def _compute_off_road_gaps(
    polyline: list[dict[str, float]],
    origem: dict[str, Any],
    destino: dict[str, Any],
) -> dict[str, Any]:
    """
    Compara as coordenadas solicitadas com o primeiro/último ponto da polyline
    para detectar trechos sem cobertura viária.

    Retorna um dict pronto para fazer merge no payload da rota:
        {
          "gap_origem_km":  float,    # 0.0 quando dentro do threshold
          "gap_destino_km": float,
          "acesso_apenas_parcial": bool,  # True se algum gap > threshold
          "snap_origem":  {"lat", "lon"} | None,  # primeiro nó viário
          "snap_destino": {"lat", "lon"} | None,  # último nó viário
        }
    """
    result: dict[str, Any] = {
        "gap_origem_km": 0.0,
        "gap_destino_km": 0.0,
        "acesso_apenas_parcial": False,
        "snap_origem": None,
        "snap_destino": None,
    }
    if not polyline:
        return result

    first = polyline[0]
    last = polyline[-1]

    if (
        first.get("lat") is not None
        and first.get("lon") is not None
        and origem.get("lat") is not None
        and origem.get("lon") is not None
    ):
        gap_o = _haversine_km(
            float(origem["lat"]), float(origem["lon"]),
            float(first["lat"]), float(first["lon"]),
        )
        result["gap_origem_km"] = round(gap_o, 3)
        result["snap_origem"] = {
            "lat": float(first["lat"]),
            "lon": float(first["lon"]),
        }

    if (
        last.get("lat") is not None
        and last.get("lon") is not None
        and destino.get("lat") is not None
        and destino.get("lon") is not None
    ):
        gap_d = _haversine_km(
            float(destino["lat"]), float(destino["lon"]),
            float(last["lat"]), float(last["lon"]),
        )
        result["gap_destino_km"] = round(gap_d, 3)
        result["snap_destino"] = {
            "lat": float(last["lat"]),
            "lon": float(last["lon"]),
        }

    result["acesso_apenas_parcial"] = (
        result["gap_origem_km"] > GAP_OFF_ROAD_THRESHOLD_KM
        or result["gap_destino_km"] > GAP_OFF_ROAD_THRESHOLD_KM
    )
    return result


def register_tools(
    mcp: FastMCP,
    os_service: OpenSearchService,
    redis_cache: RedisCache,
) -> None:
    """
    Register all Geo tools on the MCP Server instance.

    Called by server.py during startup (lazy import to avoid circular deps).
    """
    cache = GeoCache(redis_cache)

    registered = 0

    # ==================================================================
    # Tool 1: buscar_municipio
    # ==================================================================
    @mcp.tool()
    async def buscar_municipio(
        nome: str | None = None,
        codigo_ibge: str | None = None,
        uf: str | None = None,
        incluir_poligono: bool = False,
        limite: int = 10,
    ) -> dict[str, Any]:
        """
        Busca municípios brasileiros por nome, código IBGE ou UF.
        Consulta o índice ibge_municipio_v001 (5.631 municípios).

        Use cases:
        - "Onde fica Campinas?" → retorna dados geográficos completos
        - "Municípios de MG" → lista municípios do estado
        - "Código IBGE 3509502" → busca exata por código
        - Resolver nome de cidade → coordenadas para usar em outras tools

        Args:
            nome: Nome do município (busca fuzzy, ex: "Campinas", "Sao Paulo")
            codigo_ibge: Código IBGE 7 dígitos (busca exata, ex: "3509502")
            uf: Filtro por UF (ex: "SP", "MG"). Recomendado junto com nome para desambiguar.
            incluir_poligono: Retornar GeoJSON Feature com polígono completo (default: false)
            limite: Máximo de resultados (default: 10, max: 50)
        """
        from mcp_servers.geo.queries.municipios import executar_buscar_municipio

        logger.info(
            f"buscar_municipio: nome={nome}, codigo={codigo_ibge}, "
            f"uf={uf}, limite={limite}"
        )

        if not nome and not codigo_ibge:
            return {
                "sucesso": False,
                "mensagem": (
                    "Informe pelo menos 'nome' ou 'codigo_ibge'. "
                    "Exemplo: nome='Campinas', uf='SP'"
                ),
            }

        cache_params = {
            "nome": nome,
            "codigo_ibge": codigo_ibge,
            "uf": uf,
            "incluir_poligono": incluir_poligono,
            "limite": limite,
        }

        cached = await cache.get_municipio_busca(cache_params)
        if cached is not None:
            logger.info(f"buscar_municipio: Cache HIT ({cached.get('total', 0)} results)")
            return {"sucesso": True, **cached}

        try:
            resultado = await executar_buscar_municipio(
                os_service=os_service,
                nome=nome,
                codigo_ibge=codigo_ibge,
                uf=uf,
                incluir_poligono=incluir_poligono,
                limite=limite,
            )
        except Exception as e:
            logger.error(f"buscar_municipio: Query failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro na busca de municípios: {str(e)}",
            }

        if resultado["total"] == 0:
            if codigo_ibge:
                termo = codigo_ibge
            elif uf:
                termo = f"'{nome}' em {uf}"
            else:
                termo = f"'{nome}'"
            return {
                "sucesso": True,
                "total": 0,
                "municipios": [],
                "mensagem": f"Nenhum município encontrado para {termo}.",
            }

        await cache.store_municipio_busca(cache_params, resultado)

        return {"sucesso": True, **resultado}

    registered += 1

    # ==================================================================
    # Tool 2: municipio_por_coordenada
    # ==================================================================
    @mcp.tool()
    async def municipio_por_coordenada(
        latitude: float,
        longitude: float,
        incluir_poligono: bool = False,
    ) -> dict[str, Any]:
        """
        Identifica em qual município brasileiro está um ponto geográfico.
        Usa geo_shape contains (point-in-polygon) no índice ibge_municipio_v001.

        Use cases:
        - "Onde fica esta coordenada?" → retorna município, UF, região
        - Clique no mapa → contexto geográfico
        - Resolver localização de jazida/empresa em município

        Args:
            latitude: Latitude do ponto (-33.75 a 5.27 para Brasil)
            longitude: Longitude do ponto (-73.99 a -34.79 para Brasil)
            incluir_poligono: Retornar GeoJSON Feature com polígono completo (default: false)
        """
        from mcp_servers.geo.queries.municipios import executar_municipio_por_coordenada

        logger.info(
            f"municipio_por_coordenada: ({latitude}, {longitude}), "
            f"poligono={incluir_poligono}"
        )

        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return {
                "sucesso": False,
                "mensagem": (
                    f"Coordenadas inválidas: ({latitude}, {longitude}). "
                    "Latitude deve estar entre -90 e 90, longitude entre -180 e 180."
                ),
            }

        cached = await cache.get_municipio_coord(latitude, longitude)
        if cached is not None:
            logger.info("municipio_por_coordenada: Cache HIT")
            response = cached
            if not incluir_poligono:
                response.pop("feature", None)
            return {"sucesso": True, **response}

        try:
            resultado = await executar_municipio_por_coordenada(
                os_service=os_service,
                latitude=latitude,
                longitude=longitude,
                incluir_poligono=incluir_poligono,
            )
        except Exception as e:
            logger.error(f"municipio_por_coordenada: Query failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro na consulta geoespacial: {str(e)}",
            }

        if not resultado.get("encontrado"):
            return {
                "sucesso": True,
                "encontrado": False,
                "mensagem": (
                    f"Nenhum município encontrado para ({latitude}, {longitude}). "
                    "O ponto pode estar no oceano, em área de fronteira ou fora do Brasil."
                ),
            }

        await cache.store_municipio_coord(latitude, longitude, resultado)

        return {"sucesso": True, **resultado}

    registered += 1

    # ==================================================================
    # Tool 3: obter_poligono
    # ==================================================================
    @mcp.tool()
    async def obter_poligono(
        codigo_ibge: str | None = None,
        nome: str | None = None,
        uf: str | None = None,
    ) -> dict[str, Any]:
        """
        Retorna o polígono GeoJSON completo de um município brasileiro.
        Ideal para overlay no mapa, cálculo de intersecção e contexto visual.

        Use cases:
        - "Mostre o polígono de Campinas" → GeoJSON Feature para renderizar no mapa
        - "Área de Belo Horizonte" → polígono para cálculos geoespaciais
        - Contexto visual: destacar município no mapa durante análise

        Args:
            codigo_ibge: Código IBGE 7 dígitos (busca exata, preferencial). Ex: "3509502"
            nome: Nome do município (quando código é desconhecido). Ex: "Campinas"
            uf: UF para desambiguar busca por nome. Ex: "SP". Recomendado junto com nome.
        """
        from mcp_servers.geo.queries.municipios import executar_obter_poligono

        logger.info(
            f"obter_poligono: codigo={codigo_ibge}, nome={nome}, uf={uf}"
        )

        if not codigo_ibge and not nome:
            return {
                "sucesso": False,
                "mensagem": (
                    "Informe 'codigo_ibge' ou 'nome' do município. "
                    "Exemplo: codigo_ibge='3509502' ou nome='Campinas', uf='SP'"
                ),
            }

        resolved_id = codigo_ibge.strip() if codigo_ibge else None

        if resolved_id:
            cached = await cache.get_poligono(resolved_id)
            if cached is not None:
                logger.info(f"obter_poligono: Cache HIT ({resolved_id})")
                return {"sucesso": True, **cached}

        try:
            resultado = await executar_obter_poligono(
                os_service=os_service,
                codigo_ibge=codigo_ibge,
                nome=nome,
                uf=uf,
            )
        except Exception as e:
            logger.error(f"obter_poligono: Query failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro ao obter polígono: {str(e)}",
            }

        if not resultado.get("encontrado"):
            if codigo_ibge:
                termo = codigo_ibge
            elif uf:
                termo = f"'{nome}/{uf}'"
            else:
                termo = f"'{nome}'"
            return {
                "sucesso": True,
                "encontrado": False,
                "mensagem": f"Município não encontrado: {termo}.",
            }

        id_ibge = resultado["municipio"]["id_ibge"]
        await cache.store_poligono(id_ibge, resultado)

        return {"sucesso": True, **resultado}

    registered += 1

    # ==================================================================
    # Tool 4: municipios_em_raio
    # ==================================================================
    @mcp.tool()
    async def municipios_em_raio(
        latitude: float,
        longitude: float,
        raio_km: float = 50.0,
        uf: str | None = None,
        incluir_poligonos: bool = False,
        limite: int = 20,
    ) -> dict[str, Any]:
        """
        Lista municípios cujo centro geográfico está dentro de um raio.
        Resultados ordenados por distância (mais próximo primeiro).

        Use cases:
        - "Quais municípios ficam perto da obra?" → contexto regional
        - "Municípios em 100km de Sete Lagoas" → análise de cobertura
        - "Cidades próximas para logística" → planejamento de rotas

        Args:
            latitude: Latitude do centro da busca
            longitude: Longitude do centro da busca
            raio_km: Raio em km (default: 50, max: 500)
            uf: Filtro opcional por UF (ex: "MG"). Útil para limitar a um estado.
            incluir_poligonos: Retornar polígonos GeoJSON (default: false — pesado!)
            limite: Máximo de resultados (default: 20, max: 100)
        """
        from mcp_servers.geo.queries.municipios import executar_municipios_em_raio

        logger.info(
            f"municipios_em_raio: ({latitude}, {longitude}), "
            f"raio={raio_km}km, uf={uf}, limite={limite}"
        )

        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return {
                "sucesso": False,
                "mensagem": (
                    f"Coordenadas inválidas: ({latitude}, {longitude}). "
                    "Latitude deve estar entre -90 e 90, longitude entre -180 e 180."
                ),
            }

        cache_params = {
            "latitude": latitude,
            "longitude": longitude,
            "raio_km": raio_km,
            "uf": uf,
            "incluir_poligonos": incluir_poligonos,
            "limite": limite,
        }

        cached = await cache.get_municipios_raio(cache_params)
        if cached is not None:
            logger.info(
                f"municipios_em_raio: Cache HIT ({cached.get('total', 0)} results)"
            )
            return {"sucesso": True, **cached}

        try:
            resultado = await executar_municipios_em_raio(
                os_service=os_service,
                latitude=latitude,
                longitude=longitude,
                raio_km=raio_km,
                uf=uf,
                incluir_poligonos=incluir_poligonos,
                limite=limite,
            )
        except Exception as e:
            logger.error(f"municipios_em_raio: Query failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro na busca por raio: {str(e)}",
            }

        if resultado["total"] == 0:
            return {
                "sucesso": True,
                "total": 0,
                "municipios": [],
                "centro": resultado["centro"],
                "raio_km": resultado["raio_km"],
                "mensagem": (
                    f"Nenhum município encontrado em {raio_km}km de "
                    f"({latitude}, {longitude})"
                    + (f" no estado {uf}" if uf else "")
                    + "."
                ),
            }

        await cache.store_municipios_raio(cache_params, resultado)

        return {"sucesso": True, **resultado}

    registered += 1

    # ==================================================================
    # Tool 5: bioma_por_coordenada
    # ==================================================================
    @mcp.tool()
    async def bioma_por_coordenada(
        latitude: float,
        longitude: float,
        incluir_poligono: bool = False,
    ) -> dict[str, Any]:
        """
        Identifica em qual bioma brasileiro está uma coordenada geográfica.
        Consulta mr_biomas_v001 (6 biomas: Amazônia, Caatinga, Cerrado,
        Mata Atlântica, Pampa, Pantanal).

        Use cases:
        - "Esse processo está na Amazônia?" → contexto ambiental da jazida
        - "Qual bioma cobre essa coordenada?" → restrições e sensibilidade ecológica
        - Enriquecer análise de processos minerários com contexto de bioma

        Args:
            latitude: Latitude do ponto (ex: -15.7801)
            longitude: Longitude do ponto (ex: -47.9292)
            incluir_poligono: Incluir polígono GeoJSON do bioma (útil para overlay no mapa)
        """
        from mcp_servers.geo.queries.biomas import executar_bioma_por_coordenada

        logger.info(f"bioma_por_coordenada: ({latitude}, {longitude})")

        try:
            resultado = await executar_bioma_por_coordenada(
                os_service=os_service,
                latitude=latitude,
                longitude=longitude,
                incluir_poligono=incluir_poligono,
            )
        except Exception as e:
            logger.error(f"bioma_por_coordenada: Query failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro ao identificar bioma: {str(e)}",
            }

        if not resultado.get("encontrado"):
            return {
                "sucesso": True,
                "encontrado": False,
                "bioma": None,
                "mensagem": (
                    f"Nenhum bioma identificado para ({latitude}, {longitude}). "
                    "O ponto pode estar em área offshore, fronteira entre biomas "
                    "ou fora do território continental brasileiro."
                ),
            }

        return {"sucesso": True, **resultado}

    registered += 1

    # ==================================================================
    # Tool 6: provincia_por_coordenada
    # ==================================================================
    @mcp.tool()
    async def provincia_por_coordenada(
        latitude: float,
        longitude: float,
        incluir_poligono: bool = False,
    ) -> dict[str, Any]:
        """
        Identifica a província geológica mineral de uma coordenada no Brasil.
        Consulta mr_provincias_v001 (8 províncias: São Francisco, Borborema,
        Mantiqueira, Províncias Amazônicas, Tocantins, Paraná, Bacias Amazônicas,
        Parnaíba).

        Retorna nome da província, minerais principais, UFs cobertas e descrição
        geológica — contexto essencial para avaliar potencial mineral de uma jazida.

        Nota: os polígonos são aproximados (convex hull das ocorrências CPRM).
        Para análise geológica detalhada, use o Mapa Geológico do Brasil 1:1M (SGB).

        Use cases:
        - "Qual é a província geológica desse processo?" → contexto de potencial mineral
        - "Essa jazida está na Província Amazônica?" → confirmar Carajás, Tapajós, etc.
        - Enriquecer due diligence com contexto geológico de alto nível

        Args:
            latitude: Latitude do ponto (ex: -5.9283)
            longitude: Longitude do ponto (ex: -49.0611)
            incluir_poligono: Incluir polígono aproximado da província no mapa
        """
        from mcp_servers.geo.queries.provincias import executar_provincia_por_coordenada

        logger.info(f"provincia_por_coordenada: ({latitude}, {longitude})")

        try:
            resultado = await executar_provincia_por_coordenada(
                os_service=os_service,
                latitude=latitude,
                longitude=longitude,
                incluir_poligono=incluir_poligono,
            )
        except Exception as e:
            logger.error(f"provincia_por_coordenada: Query failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro ao identificar província geológica: {str(e)}",
            }

        if not resultado.get("encontrado"):
            return {
                "sucesso": True,
                "encontrado": False,
                "provincia": None,
                "mensagem": (
                    f"Nenhuma província identificada para ({latitude}, {longitude}). "
                    "O ponto pode estar em fronteira entre províncias ou fora do território "
                    "coberto pelas ocorrências CPRM."
                ),
            }

        return {"sucesso": True, **resultado}

    registered += 1

    # ==================================================================
    # Tool 7: imoveis_rurais_em_area
    # ==================================================================
    @mcp.tool()
    async def imoveis_rurais_em_area(
        latitude: float,
        longitude: float,
        raio_km: float = 50.0,
        apenas_certificadas: bool = True,
        uf: str | None = None,
        codigo_municipio: str | None = None,
        area_min_ha: float | None = None,
        area_max_ha: float | None = None,
        limite: int = 20,
    ) -> dict[str, Any]:
        """
        Busca imóveis rurais certificados pelo INCRA (SIGEF) próximos a uma
        coordenada. Consulta mr_sigef_v001 (~7M parcelas certificadas do Brasil).

        Essencial para análise fundiária de processos minerários:
        - Identificar proprietários de terras adjacentes a uma jazida
        - Verificar se um processo ANM cruza imóvel rural certificado (SIGEF)
        - Avaliar conflitos fundiários e necessidade de negociação com proprietários
        - Comparar com CAR (mr_sicar_v001) para visão completa do imóvel

        Diferença SIGEF vs CAR (SICAR):
        - SIGEF = georreferenciamento certificado pelo INCRA (titularidade formal)
        - CAR/SICAR = declaração ambiental (reserva legal, APP) feita pelo proprietário

        Use cases:
        - "Quais imóveis rurais certificados existem nessa área de 50km?"
        - "Esse processo ANM está dentro de algum imóvel SIGEF certificado?"
        - "Qual a titularidade fundiária da região da jazida X?"

        Args:
            latitude: Latitude do ponto central (ex: -19.5234)
            longitude: Longitude do ponto central (ex: -43.8765)
            raio_km: Raio de busca em km (padrão: 50km)
            apenas_certificadas: Filtrar apenas parcelas com status=CERTIFICADA (padrão: True)
            uf: Filtrar por UF (ex: "MG")
            codigo_municipio: Filtrar por código IBGE do município
            area_min_ha: Área mínima da parcela em hectares
            area_max_ha: Área máxima da parcela em hectares
            limite: Número máximo de resultados (padrão: 20, máx. recomendado: 50)
        """
        from mcp_servers.geo.queries.sigef import executar_imoveis_rurais_em_area

        logger.info(
            f"imoveis_rurais_em_area: lat={latitude}, lon={longitude}, "
            f"raio={raio_km}km, uf={uf}"
        )

        try:
            resultado = await executar_imoveis_rurais_em_area(
                os_service=os_service,
                latitude=latitude,
                longitude=longitude,
                raio_km=raio_km,
                apenas_certificadas=apenas_certificadas,
                uf=uf,
                codigo_municipio=codigo_municipio,
                area_min_ha=area_min_ha,
                area_max_ha=area_max_ha,
                limite=limite,
            )
        except Exception as e:
            logger.error(f"imoveis_rurais_em_area: Query failed: {e}")
            return {
                "sucesso": False,
                "mensagem": f"Erro ao buscar imóveis rurais: {str(e)}",
            }

        if resultado.get("total", 0) == 0:
            return {
                "sucesso": True,
                "total": 0,
                "retornados": 0,
                "raio_km": raio_km,
                "imoveis": [],
                "mensagem": (
                    f"Nenhum imóvel rural certificado (SIGEF) encontrado em raio de "
                    f"{raio_km}km de ({latitude}, {longitude}). "
                    "A área pode estar em terra indígena, unidade de conservação ou "
                    "imóvel não certificado pelo INCRA."
                ),
            }

        return {"sucesso": True, **resultado}

    registered += 1

    # ==================================================================
    # Tools 8–10: portos (mr_portos_v001)
    # ==================================================================
    @mcp.tool()
    async def buscar_porto(
        termo: str | None = None,
        codigo: str | None = None,
        uf: str | None = None,
        limite: int = 10,
    ) -> dict[str, Any]:
        """
        Catálogo de portos públicos (e futuros TUP) indexados em mr_portos_v001.

        Use cases:
        - Resolver "Porto de Vila do Conde (PA)" para coordenadas oficiais de acesso
        - Listar candidatos por nome ou código (PSV, PNG, …)

        Args:
            termo: Nome, alias ou texto livre (ex.: "Mucuripe", "porto de aratu")
            codigo: Código curto exato (ex.: "PSV", "VDC")
            uf: Filtro por UF (ex.: "PA") — reduz homónimos
            limite: Máximo de resultados (default 10, max 50)
        """
        from mcp_servers.geo.queries.portos import executar_buscar_porto

        if not termo and not codigo:
            return {
                "sucesso": False,
                "mensagem": "Informe `termo` ou `codigo`.",
            }

        try:
            resultado = await executar_buscar_porto(
                os_service,
                termo=termo,
                codigo=codigo,
                uf=uf,
                limite=limite,
            )
        except Exception as e:
            logger.error("buscar_porto: %s", e)
            return {"sucesso": False, "mensagem": str(e)}

        if resultado.get("total", 0) == 0:
            return {
                "sucesso": True,
                "total": 0,
                "portos": [],
                "mensagem": "Nenhum porto encontrado.",
            }
        return {"sucesso": True, **resultado}

    registered += 1

    @mcp.tool()
    async def porto_por_coordenada(
        latitude: float,
        longitude: float,
        incluir_poligono: bool = False,
    ) -> dict[str, Any]:
        """
        Indica se um ponto cai dentro da área poligonal de um porto (mr_portos_v001).

        Requer que o documento tenha campo `poligono` (ingestão CKAN).
        """
        from mcp_servers.geo.queries.portos import executar_porto_por_coordenada

        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return {"sucesso": False, "mensagem": "Coordenadas inválidas."}

        try:
            out = await executar_porto_por_coordenada(
                os_service,
                latitude=latitude,
                longitude=longitude,
                incluir_poligono=incluir_poligono,
            )
        except Exception as e:
            logger.error("porto_por_coordenada: %s", e)
            return {"sucesso": False, "mensagem": str(e)}

        return {"sucesso": True, **out}

    registered += 1

    @mcp.tool()
    async def obter_poligono_porto(
        codigo: str | None = None,
        nome: str | None = None,
        uf: str | None = None,
    ) -> dict[str, Any]:
        """
        Marca um porto para plot no mapa (metadados; polígono via REST sob demanda).

        O polígono GeoJSON é carregado pelo frontend em ``GET /api/v1/geo/porto/poligono``.
        """
        from mcp_servers.geo.queries.portos import executar_obter_poligono_porto

        if not codigo and not nome:
            return {"sucesso": False, "mensagem": "Informe codigo ou nome."}

        try:
            out = await executar_obter_poligono_porto(
                os_service, codigo=codigo, nome=nome, uf=uf
            )
        except Exception as e:
            logger.error("obter_poligono_porto: %s", e)
            return {"sucesso": False, "mensagem": str(e)}

        return out

    registered += 1

    # ==================================================================
    # Tools 11–13: ferrovias (mr_ferrovias_v001)
    # ==================================================================
    @mcp.tool()
    async def buscar_ferrovia(
        termo: str | None = None,
        codigo_sigla: str | None = None,
        uf: str | None = None,
        limite: int = 15,
    ) -> dict[str, Any]:
        """
        Pesquisa trechos da malha ferroviária federal indexada (ANTT / OpenSearch).

        Use cases:
        - "Onde passa a Estrada de Ferro Carajás?" → termo="Carajás"
        - Sigla ou código operacional → codigo_sigla="efvm" (case-insensitive)

        Args:
            termo: Nome ou texto livre (ex.: "Norte-Sul", "Vitória Minas")
            codigo_sigla: Filtro por sigla/código curto quando conhecido
            uf: Filtro por UF (ex.: "MG", "PA")
            limite: Máximo de resultados (default 15, max 50)
        """
        from mcp_servers.geo.queries.ferrovias import executar_buscar_ferrovia

        if not termo and not codigo_sigla:
            return {
                "sucesso": False,
                "mensagem": "Informe `termo` ou `codigo_sigla`.",
            }

        try:
            resultado = await executar_buscar_ferrovia(
                os_service,
                termo=termo,
                codigo_sigla=codigo_sigla,
                uf=uf,
                limite=limite,
            )
        except Exception as e:
            logger.error("buscar_ferrovia: %s", e)
            return {"sucesso": False, "mensagem": str(e)}

        if resultado.get("total", 0) == 0:
            return {
                "sucesso": True,
                "total": 0,
                "ferrovias": [],
                "mensagem": "Nenhum trecho ferroviário encontrado.",
            }
        return {"sucesso": True, **resultado}

    registered += 1

    @mcp.tool()
    async def ferrovias_proximas(
        latitude: float,
        longitude: float,
        raio_km: float = 50.0,
        uf: str | None = None,
        limite: int = 20,
    ) -> dict[str, Any]:
        """
        Lista trechos de malha cujo **centróide** está até ``raio_km`` do ponto
        (ordenado do mais próximo ao mais distante). Útil para logística
        escoamento, distância aproximada a ferrovia a partir de jazida ou CNPJ.

        Nota: o centróide do trecho é uma aproximação — a linha pode passar
        lateralmente ao ponto; reduza ``raio_km`` ou refine com ``buscar_ferrovia``.

        Args:
            latitude: Latitude WGS84 do ponto de referência
            longitude: Longitude WGS84
            raio_km: Raio em km (0,5–500; default 50)
            uf: Opcional — filtra trechos cuja UF cadastrada coincide
            limite: Máximo de trechos (default 20, max 50)
        """
        from mcp_servers.geo.queries.ferrovias import executar_ferrovias_proximas

        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return {"sucesso": False, "mensagem": "Coordenadas inválidas."}

        try:
            resultado = await executar_ferrovias_proximas(
                os_service,
                latitude=latitude,
                longitude=longitude,
                raio_km=raio_km,
                uf=uf,
                limite=limite,
            )
        except Exception as e:
            logger.error("ferrovias_proximas: %s", e)
            return {"sucesso": False, "mensagem": str(e)}

        if resultado.get("total", 0) == 0:
            return {
                "sucesso": True,
                "total": 0,
                "retornados": 0,
                "raio_km": raio_km,
                "ferrovias": [],
                "mensagem": (
                    f"Nenhum trecho com centróide em {raio_km} km de "
                    f"({latitude:.5f}, {longitude:.5f})."
                ),
            }
        return {"sucesso": True, **resultado}

    registered += 1

    @mcp.tool()
    async def obter_geometria_ferrovia(
        ferrovia_id: str,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> dict[str, Any]:
        """
        Marca um trecho ferroviário para plot no mapa (só metadados no retorno).

        A linha GeoJSON **não** vem neste JSON — o frontend busca sob demanda em
        ``GET /api/v1/geo/ferrovia/geometria``. Use o ``ferrovia_id`` exato de
        ``ferrovias_proximas`` / ``buscar_ferrovia`` (prefixo ``antt-``).

        Args:
            ferrovia_id: ID do documento no índice mr_ferrovias_v001.
            latitude: Opcional — fallback se o ID estiver errado (WGS84).
            longitude: Opcional — idem.
        """
        from mcp_servers.geo.queries.ferrovias import executar_obter_geometria_ferrovia

        try:
            out = await executar_obter_geometria_ferrovia(
                os_service,
                ferrovia_id=ferrovia_id,
                latitude=latitude,
                longitude=longitude,
            )
        except Exception as e:
            logger.error("obter_geometria_ferrovia: %s", e)
            return {"sucesso": False, "mensagem": str(e)}

        return out

    registered += 1

    # ==================================================================
    # Azure Maps Tools (14–18) — require subscription key
    # ==================================================================
    azure_maps_configured = bool(mcp_settings.azure_maps_subscription_key)

    if azure_maps_configured:
        from mcp_servers.geo.services import azure_maps

        # ==============================================================
        # Tool 5: calcular_rota
        # ==============================================================
        def _enrich_with_endpoints(
            payload: dict[str, Any],
            origem: dict[str, Any],
            destino: dict[str, Any],
        ) -> dict[str, Any]:
            """
            Substitui origem/destino do payload da rota pelos dicts ricos contendo
            endereco_consultado / endereco_resolvido / fonte / detalhes.
            """
            def _ep_dict(ep: dict[str, Any]) -> dict[str, Any]:
                d: dict[str, Any] = {
                    "lat": ep["lat"],
                    "lon": ep["lon"],
                    "endereco_consultado": ep.get("endereco_consultado"),
                    "endereco_resolvido": ep.get("endereco_resolvido"),
                    "fonte": ep.get("fonte"),
                }
                if ep.get("detalhes"):
                    d["detalhes"] = ep["detalhes"]
                return d

            payload["origem"] = _ep_dict(origem)
            payload["destino"] = _ep_dict(destino)
            return payload

        async def _resolve_endpoint(
            label: str,
            lat: float | None,
            lon: float | None,
            endereco: str | None,
            *,
            peer: dict[str, Any] | None = None,
            contexto_pergunta: str = "",
        ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
            """
            Resolve um endpoint (origem ou destino) para coordenadas + metadados.

            Retorna (endpoint_dict, error_dict). Apenas um deles é não-None.
            endpoint_dict tem o formato:
                {
                    "lat": float,
                    "lon": float,
                    "endereco_consultado": str | None,   # texto que veio do LLM
                    "endereco_resolvido": str | None,    # endereço normalizado pelo Azure
                    "fonte": "coordenadas" | "geocodificado",
                }
            """
            if endereco:
                logger.info(
                    "calcular_rota: resolvendo %s=%r (contexto=%d chars)",
                    label, endereco, len(contexto_pergunta or ""),
                )
                hit, err = await resolve_place_for_route(
                    os_service,
                    endereco,
                    user_message=contexto_pergunta,
                    peer=peer,
                )
                if err is not None:
                    return None, err
                if hit is not None:
                    logger.info(
                        "calcular_rota: %s → %s via %s",
                        label, hit.get("endereco_resolvido"), hit.get("fonte"),
                    )
                    return hit, None

            if lat is not None and lon is not None:
                return {
                    "lat": float(lat),
                    "lon": float(lon),
                    "endereco_consultado": None,
                    "endereco_resolvido": None,
                    "fonte": "coordenadas",
                }, None

            return None, {
                "sucesso": False,
                "mensagem": (
                    f"Forneça coordenadas ({label}_lat + {label}_lon) "
                    f"OU um endereço ({label}_endereco) para {label}."
                ),
            }

        async def _azure_route_directions_try_truck_then_car(
            origem: dict[str, Any],
            destino: dict[str, Any],
            modo: str,
            evitar_pedagios: bool,
        ) -> dict[str, Any]:
            """
            Chama Azure Route Directions. Se modo=truck e a API não devolver
            rota (ex.: restrições de veículo ou trechos sem malha para caminhão),
            tenta novamente em modo car como referência aproximada.
            """
            resultado = await azure_maps.route_directions(
                origin_lat=origem["lat"],
                origin_lon=origem["lon"],
                dest_lat=destino["lat"],
                dest_lon=destino["lon"],
                mode=modo,
                avoid_tolls=evitar_pedagios,
            )
            if resultado.get("distancia_km"):
                return resultado
            if modo != "truck":
                return resultado
            car_try = await azure_maps.route_directions(
                origin_lat=origem["lat"],
                origin_lon=origem["lon"],
                dest_lat=destino["lat"],
                dest_lon=destino["lon"],
                mode="car",
                avoid_tolls=evitar_pedagios,
            )
            if not car_try.get("distancia_km"):
                return resultado
            logger.info(
                "calcular_rota: truck sem rota — usando fallback car "
                "(%s,%s) → (%s,%s)",
                origem["lat"],
                origem["lon"],
                destino["lat"],
                destino["lon"],
            )
            car_try["modo_solicitado"] = "truck"
            car_try["aviso_roteamento"] = (
                "Rota de caminhão indisponível para este par origem/destino "
                "(restrições ou malha); exibindo rota para automóvel como "
                "referência aproximada de distância e tempo."
            )
            return car_try

        @mcp.tool()
        async def calcular_rota(
            origem_lat: float | None = None,
            origem_lon: float | None = None,
            destino_lat: float | None = None,
            destino_lon: float | None = None,
            origem_endereco: str | None = None,
            destino_endereco: str | None = None,
            modo: str = "truck",
            evitar_pedagios: bool = False,
            contexto_pergunta: str | None = None,
        ) -> dict[str, Any]:
            """
            Calcula rota rodoviária entre dois pontos usando Azure Maps Route Directions.
            Suporta caminhão (truck) e carro (car) com dados de tráfego em tempo real.

            Origem e destino podem ser informados de duas formas:
              • Coordenadas explícitas: origem_lat + origem_lon (e destino_lat + destino_lon)
              • Texto livre: origem_endereco / destino_endereco — a tool geocodifica
                internamente via Azure Maps Search e usa o primeiro resultado.

            Se ambas as formas forem fornecidas para o mesmo lado, o ENDEREÇO tem
            precedência (mais auditável — o nome consultado e o endereço resolvido
            ficam registrados no retorno).

            NUNCA chute coordenadas. Para nomes (porto, cidade, ponto de interesse),
            use *_endereco. Para uma jazida pelo número do processo, chame antes
            jazidas__detalhes_processo e use processo.localizacao em *_lat/*_lon.

            Use cases:
            - "Distância da jazida até o porto" → origem_lat/lon (do detalhes_processo)
              + destino_endereco="Porto de Aratu, BA"
            - "Rota de Campinas até Sete Lagoas" → origem_endereco / destino_endereco
            - "Quanto tempo de caminhão?" → estimativa com tráfego real

            Args:
                origem_lat: Latitude da origem (opcional se origem_endereco for dado)
                origem_lon: Longitude da origem (opcional se origem_endereco for dado)
                destino_lat: Latitude do destino (opcional se destino_endereco for dado)
                destino_lon: Longitude do destino (opcional se destino_endereco for dado)
                origem_endereco: Texto livre da origem (ex: "Porto de Aratu, BA").
                                 Geocodificado via Azure Maps Search.
                destino_endereco: Texto livre do destino. Geocodificado via Azure Maps Search.
                modo: 'truck' (caminhão, default — respeita restrições de peso/altura)
                      ou 'car' (carro). Se truck não retornar rota, a tool tenta
                      automaticamente modo car e inclui ``aviso_roteamento``.
                evitar_pedagios: Se true, evita rodovias pedagiadas (pode aumentar distância)
                contexto_pergunta: Uso interno — texto da pergunta do utilizador
                    (preenchido pelo backend para desambiguar geocode).
            """
            valid_modes = ("truck", "car")
            if modo not in valid_modes:
                return {
                    "sucesso": False,
                    "mensagem": f"Modo inválido: '{modo}'. Use 'truck' ou 'car'.",
                }

            ctx = (contexto_pergunta or "").strip()
            origem, err = await _resolve_endpoint(
                "origem", origem_lat, origem_lon, origem_endereco,
                contexto_pergunta=ctx,
            )
            if err is not None:
                return err
            destino, err = await _resolve_endpoint(
                "destino", destino_lat, destino_lon, destino_endereco,
                peer=origem,
                contexto_pergunta=ctx,
            )
            if err is not None:
                return err

            assert origem is not None and destino is not None

            logger.info(
                "calcular_rota: (%s,%s)[%s] → (%s,%s)[%s], modo=%s",
                origem["lat"], origem["lon"], origem["fonte"],
                destino["lat"], destino["lon"], destino["fonte"],
                modo,
            )

            cache_params = {
                "ol": round(origem["lat"], 5),
                "oo": round(origem["lon"], 5),
                "dl": round(destino["lat"], 5),
                "do": round(destino["lon"], 5),
                "m": modo,
                "tp": evitar_pedagios,
            }
            cached = await cache.get_rota(cache_params)
            if cached is not None:
                logger.info("calcular_rota: Cache HIT")
                return _enrich_with_endpoints(
                    {"sucesso": True, **cached}, origem, destino,
                )

            try:
                resultado = await _azure_route_directions_try_truck_then_car(
                    origem, destino, modo, evitar_pedagios,
                )
            except Exception as e:
                logger.error(f"calcular_rota: Azure Maps error: {e}")
                return {
                    "sucesso": False,
                    "mensagem": f"Erro ao calcular rota: {str(e)}",
                }

            if not resultado.get("distancia_km"):
                return _enrich_with_endpoints(
                    {"sucesso": False, **resultado}, origem, destino,
                )

            gap_info = _compute_off_road_gaps(
                resultado.get("polyline") or [], origem, destino,
            )
            resultado.update(gap_info)
            if gap_info["acesso_apenas_parcial"]:
                logger.info(
                    "calcular_rota: acesso parcial detectado — "
                    "gap_origem=%.3f km, gap_destino=%.3f km",
                    gap_info["gap_origem_km"], gap_info["gap_destino_km"],
                )

            await cache.store_rota(cache_params, resultado)
            return _enrich_with_endpoints(
                {"sucesso": True, **resultado}, origem, destino,
            )

        registered += 1

        # ==============================================================
        # Tool 6: comparar_rotas — batch (1 origem × N destinos, paralelo)
        # ==============================================================
        async def _route_one(
            origem: dict[str, Any],
            destino: dict[str, Any],
            modo: str,
            evitar_pedagios: bool,
            label: str,
        ) -> dict[str, Any]:
            """
            Roda UMA rota e retorna o payload pronto (com label, polilinha,
            gaps off-road). Usado pelo asyncio.gather de comparar_rotas.

            Reutiliza o cache do calcular_rota — se a rota A→B já foi
            calculada nos últimos N min, vem direto do Redis.
            """
            cache_params = {
                "ol": round(origem["lat"], 5),
                "oo": round(origem["lon"], 5),
                "dl": round(destino["lat"], 5),
                "do": round(destino["lon"], 5),
                "m": modo,
                "tp": evitar_pedagios,
            }
            cached = await cache.get_rota(cache_params)
            if cached is not None:
                resultado = dict(cached)
            else:
                try:
                    resultado = await _azure_route_directions_try_truck_then_car(
                        origem, destino, modo, evitar_pedagios,
                    )
                except Exception as e:
                    logger.error(
                        "comparar_rotas[%s]: Azure Maps error: %s", label, e,
                    )
                    return {
                        "sucesso": False,
                        "label": label,
                        "mensagem": f"Erro ao calcular rota '{label}': {e}",
                        "destino": destino,
                    }
                if resultado.get("distancia_km"):
                    gap_info = _compute_off_road_gaps(
                        resultado.get("polyline") or [], origem, destino,
                    )
                    resultado.update(gap_info)
                    await cache.store_rota(cache_params, resultado)

            if not resultado.get("distancia_km"):
                return {
                    "sucesso": False,
                    "label": label,
                    "mensagem": resultado.get(
                        "mensagem", "Nenhuma rota encontrada.",
                    ),
                    "destino": destino,
                }

            payload = {"sucesso": True, "label": label, **resultado}
            return _enrich_with_endpoints(payload, origem, destino)

        @mcp.tool()
        async def comparar_rotas(
            destinos: list[dict[str, Any]],
            origem_lat: float | None = None,
            origem_lon: float | None = None,
            origem_endereco: str | None = None,
            modo: str = "truck",
            evitar_pedagios: bool = False,
            contexto_pergunta: str | None = None,
        ) -> dict[str, Any]:
            """
            Calcula MÚLTIPLAS rotas (1 origem → N destinos) em paralelo numa
            única chamada. PREFERIR esta tool sobre N chamadas paralelas a
            calcular_rota quando o usuário pede "compare rotas para A, B, C",
            "principais portos", "ranking de destinos", etc.

            VANTAGENS sobre N chamadas a calcular_rota:
              • Garantia de que TODAS as N rotas são calculadas (atomicidade
                no backend — não depende de o LLM lembrar de disparar todas).
              • Cada rota desenha sua própria polilinha colorida no mapa.
              • Ranking automático por distância e por duração.
              • Reusa o mesmo cache Redis de calcular_rota.

            COMO PASSAR DESTINOS — cada item do array `destinos` é um dict:
              {"endereco": "Porto de Aratu, BA", "label": "Aratu"}        ← mais comum
              {"lat": -12.79, "lon": -38.41, "label": "Aratu"}            ← coords
              {"endereco": "...", "lat": ..., "lon": ..., "label": "..."}  ← endereço tem precedência
            O `label` é OPCIONAL — se omitido usa o `endereco`.

            ORIGEM: idêntica a calcular_rota — ou origem_lat+origem_lon, ou
            origem_endereco. Para JAZIDA por número de processo, chame antes
            jazidas__detalhes_processo e passe processo.localizacao em
            origem_lat/origem_lon.

            EXEMPLO — usuário pede "compare rotas da jazida 870.773/2012
            até os portos de Aratu, Salvador, Aracaju, Suape e Ilhéus":
              Passo 1: jazidas__detalhes_processo(ds_processo="870.773/2012")
              Passo 2: comparar_rotas(
                origem_lat=processo.localizacao.lat,
                origem_lon=processo.localizacao.lon,
                destinos=[
                  {"endereco": "Porto de Aratu, BA",    "label": "Aratu"},
                  {"endereco": "Porto de Salvador, BA", "label": "Salvador"},
                  {"endereco": "Porto de Aracaju, SE",  "label": "Aracaju"},
                  {"endereco": "Porto de Suape, PE",    "label": "Suape"},
                  {"endereco": "Porto de Ilhéus, BA",   "label": "Ilhéus"},
                ],
                modo="truck",
              )

            Args:
                destinos:        Lista de destinos (ver formato acima). Mínimo 2.
                origem_lat:      Latitude da origem (opcional se origem_endereco).
                origem_lon:      Longitude da origem (opcional se origem_endereco).
                origem_endereco: Endereço de origem (geocodificado internamente).
                modo:            'truck' (default) ou 'car'.
                evitar_pedagios: Se true, evita rodovias pedagiadas.

            Returns:
                {
                  "sucesso": true,
                  "modo": "truck",
                  "origem": {lat, lon, endereco_resolvido, …},
                  "total_destinos": 5,
                  "rotas_calculadas": 5,
                  "rotas": [
                    {
                      "sucesso": true,
                      "label": "Aratu",
                      "distancia_km": 695.1,
                      "duracao_min": 787.3,
                      "polyline": [...],         # consumida pelo frontend (mapa)
                      "origem": {...}, "destino": {...},
                      "gap_origem_km": 0.05, "gap_destino_km": 4.5,
                      "acesso_apenas_parcial": true,
                      ...
                    },
                    ...
                  ],
                  "ranking_distancia": ["Aratu", "Salvador", "Ilhéus", "Aracaju", "Suape"],
                  "ranking_duracao":   ["Aratu", "Salvador", "Aracaju", "Ilhéus", "Suape"],
                  "mais_curta": "Aratu",
                  "mais_rapida": "Aratu",
                }
            """
            if not destinos or len(destinos) < 2:
                return {
                    "sucesso": False,
                    "mensagem": (
                        "comparar_rotas exige pelo menos 2 destinos. Para 1 só "
                        "destino use geo__calcular_rota."
                    ),
                }
            if len(destinos) > 25:
                return {
                    "sucesso": False,
                    "mensagem": (
                        f"Limite de 25 destinos por comparar_rotas (recebidos: "
                        f"{len(destinos)}). Reduza a lista ou faça duas chamadas."
                    ),
                }
            if modo not in ("truck", "car"):
                return {
                    "sucesso": False,
                    "mensagem": f"Modo inválido: '{modo}'. Use 'truck' ou 'car'.",
                }

            ctx = (contexto_pergunta or "").strip()
            origem, err = await _resolve_endpoint(
                "origem", origem_lat, origem_lon, origem_endereco,
                contexto_pergunta=ctx,
            )
            if err is not None:
                return err
            assert origem is not None

            logger.info(
                "comparar_rotas: origem=(%s,%s)[%s], %d destinos, modo=%s",
                origem["lat"], origem["lon"], origem["fonte"],
                len(destinos), modo,
            )

            # Resolve todos os destinos em paralelo. Cada destino vira (endpoint, err).
            async def _resolve_one_dest(
                idx: int, item: dict[str, Any],
            ) -> tuple[int, dict[str, Any] | None, dict[str, Any] | None, str]:
                if not isinstance(item, dict):
                    return idx, None, {
                        "sucesso": False,
                        "mensagem": (
                            f"destinos[{idx}] inválido: esperado dict, recebido "
                            f"{type(item).__name__}."
                        ),
                    }, str(item)
                lat = item.get("lat") or item.get("latitude")
                lon = item.get("lon") or item.get("longitude")
                end = item.get("endereco")
                label = (
                    item.get("label")
                    or end
                    or (f"{lat},{lon}" if lat is not None else f"destino_{idx}")
                )
                ep, e = await _resolve_endpoint(
                    f"destinos[{idx}]", lat, lon, end,
                    peer=origem,
                    contexto_pergunta=ctx,
                )
                return idx, ep, e, label

            resolves = await asyncio.gather(
                *(_resolve_one_dest(i, d) for i, d in enumerate(destinos)),
                return_exceptions=False,
            )

            # Calcula em paralelo só os que resolveram OK; coleta erros por destino.
            route_tasks: list[Any] = []
            route_meta: list[tuple[int, str]] = []  # (idx, label)
            erros_resolucao: list[dict[str, Any]] = []
            for idx, ep, e, label in resolves:
                if e is not None:
                    erros_resolucao.append({
                        "label": label,
                        "indice": idx,
                        "mensagem": e.get("mensagem", "Endereço não resolvido."),
                    })
                    continue
                assert ep is not None
                route_tasks.append(
                    _route_one(origem, ep, modo, evitar_pedagios, label),
                )
                route_meta.append((idx, label))

            if not route_tasks:
                return {
                    "sucesso": False,
                    "mensagem": "Nenhum destino pôde ser resolvido.",
                    "erros_destinos": erros_resolucao,
                }

            rotas_brutas = await asyncio.gather(*route_tasks, return_exceptions=False)

            # Mantém ordem original do input + injeta erros de resolução.
            rotas_indexadas: dict[int, dict[str, Any]] = {}
            for (idx, _label), rota in zip(route_meta, rotas_brutas):
                rotas_indexadas[idx] = rota
            for err_d in erros_resolucao:
                rotas_indexadas[err_d["indice"]] = {
                    "sucesso": False,
                    "label": err_d["label"],
                    "mensagem": err_d["mensagem"],
                }
            rotas_ordenadas = [rotas_indexadas[i] for i in range(len(destinos))]

            # Ranking só sobre as bem-sucedidas.
            sucessos = [r for r in rotas_ordenadas if r.get("sucesso")]
            sucessos_ord_dist = sorted(
                sucessos, key=lambda r: r.get("distancia_km", float("inf")),
            )
            sucessos_ord_dur = sorted(
                sucessos, key=lambda r: r.get("duracao_min", float("inf")),
            )

            return {
                "sucesso": True,
                "modo": modo,
                "origem": {
                    "lat": origem["lat"],
                    "lon": origem["lon"],
                    "endereco_consultado": origem.get("endereco_consultado"),
                    "endereco_resolvido": origem.get("endereco_resolvido"),
                    "fonte": origem.get("fonte"),
                },
                "total_destinos": len(destinos),
                "rotas_calculadas": len(sucessos),
                "rotas": rotas_ordenadas,
                "ranking_distancia": [r["label"] for r in sucessos_ord_dist],
                "ranking_duracao":   [r["label"] for r in sucessos_ord_dur],
                "mais_curta":  sucessos_ord_dist[0]["label"] if sucessos_ord_dist else None,
                "mais_rapida": sucessos_ord_dur[0]["label"]  if sucessos_ord_dur  else None,
            }

        registered += 1

        # ==============================================================
        # Tool 7: calcular_isocrona
        # ==============================================================
        @mcp.tool()
        async def calcular_isocrona(
            latitude: float,
            longitude: float,
            criterio: str = "tempo",
            valor: float = 60,
            modo: str = "truck",
        ) -> dict[str, Any]:
            """
            Calcula isócrona (área alcançável) a partir de um ponto usando Azure Maps Route Range.
            Retorna polígono GeoJSON da região acessível por tempo ou distância.

            Use cases:
            - "Que região consigo cobrir em 2h de caminhão?" → polígono no mapa
            - "Área alcançável em 100km a partir da obra" → análise de cobertura
            - Identificar jazidas dentro da área de viabilidade logística
            - Análise de raio de atendimento para operação

            Args:
                latitude: Latitude do ponto central
                longitude: Longitude do ponto central
                criterio: 'tempo' (minutos, default) ou 'distancia' (km)
                valor: Valor do critério — minutos se 'tempo', km se 'distancia' (default: 60)
                modo: 'truck' (default) ou 'car'
            """
            logger.info(
                f"calcular_isocrona: ({latitude},{longitude}), "
                f"criterio={criterio}, valor={valor}, modo={modo}"
            )

            if criterio not in ("tempo", "distancia"):
                return {
                    "sucesso": False,
                    "mensagem": "Critério inválido. Use 'tempo' ou 'distancia'.",
                }

            if modo not in ("truck", "car"):
                return {
                    "sucesso": False,
                    "mensagem": "Modo inválido. Use 'truck' ou 'car'.",
                }

            cache_params = {
                "lat": round(latitude, 5),
                "lon": round(longitude, 5),
                "c": criterio,
                "v": valor,
                "m": modo,
            }
            cached = await cache.get_isocrona(cache_params)
            if cached is not None:
                logger.info("calcular_isocrona: Cache HIT")
                return {"sucesso": True, **cached}

            try:
                resultado = await azure_maps.route_range(
                    lat=latitude,
                    lon=longitude,
                    criterio=criterio,
                    valor=valor,
                    mode=modo,
                )
            except Exception as e:
                logger.error(f"calcular_isocrona: Azure Maps error: {e}")
                return {
                    "sucesso": False,
                    "mensagem": f"Erro ao calcular isócrona: {str(e)}",
                }

            if not resultado.get("feature"):
                return {"sucesso": False, **resultado}

            await cache.store_isocrona(cache_params, resultado)
            return {"sucesso": True, **resultado}

        registered += 1

        # ==============================================================
        # Tool 8: geocodificar
        # ==============================================================
        @mcp.tool()
        async def geocodificar(
            endereco: str | None = None,
            latitude: float | None = None,
            longitude: float | None = None,
            limite: int = 5,
        ) -> dict[str, Any]:
            """
            Geocodificação (endereço→coordenadas) e reversa (coordenadas→endereço)
            usando Azure Maps Search API.

            Use cases:
            - "Onde fica Rua Augusta 1200, SP?" → coordenadas + mapa
            - "Qual endereço desta coordenada?" → reverse geocoding
            - Converter endereço de obra/jazida em coordenadas para mapa
            - Validar e enriquecer dados de localização

            Args:
                endereco: Endereço para geocodificação direta (forward).
                          Ex: "Rua Augusta 1200, São Paulo, SP"
                latitude: Latitude para geocodificação reversa (se endereco não fornecido)
                longitude: Longitude para geocodificação reversa (se endereco não fornecido)
                limite: Máximo de resultados para geocoding direto (default: 5, max: 10)
            """
            is_reverse = (endereco is None) and (latitude is not None and longitude is not None)

            if not endereco and not is_reverse:
                return {
                    "sucesso": False,
                    "mensagem": (
                        "Forneça 'endereco' para geocodificação direta, "
                        "ou 'latitude' + 'longitude' para reversa."
                    ),
                }

            if is_reverse:
                logger.info(f"geocodificar (reverso): ({latitude}, {longitude})")

                cached = await cache.get_reverse_geocode(latitude, longitude)
                if cached is not None:
                    logger.info("geocodificar (reverso): Cache HIT")
                    return {"sucesso": True, "tipo_busca": "reversa", **cached}

                try:
                    resultado = await azure_maps.search_reverse(
                        lat=latitude, lon=longitude
                    )
                except Exception as e:
                    logger.error(f"geocodificar (reverso): Azure Maps error: {e}")
                    return {
                        "sucesso": False,
                        "mensagem": f"Erro na geocodificação reversa: {str(e)}",
                    }

                if resultado.get("encontrado"):
                    await cache.store_reverse_geocode(latitude, longitude, resultado)

                return {"sucesso": True, "tipo_busca": "reversa", **resultado}

            else:
                logger.info(f"geocodificar (direto): '{endereco}', limite={limite}")

                cache_key = azure_maps.geocode_query_cache_key(endereco)
                cached = await cache.get_geocode(cache_key)
                if cached is not None:
                    logger.info("geocodificar (direto): Cache HIT")
                    return {"sucesso": True, "tipo_busca": "direta", **cached}

                try:
                    resultado = await azure_maps.search_fuzzy(
                        query=endereco,
                        limit=min(limite, 10),
                    )
                except Exception as e:
                    logger.error(f"geocodificar (direto): Azure Maps error: {e}")
                    return {
                        "sucesso": False,
                        "mensagem": f"Erro na geocodificação: {str(e)}",
                    }

                if resultado["total"] == 0:
                    return {
                        "sucesso": True,
                        "tipo_busca": "direta",
                        "total": 0,
                        "resultados": [],
                        "mensagem": f"Nenhum resultado para '{endereco}'.",
                    }

                await cache.store_geocode(cache_key, resultado)
                return {"sucesso": True, "tipo_busca": "direta", **resultado}

        registered += 1

        # ==============================================================
        # Tool 9: plotar_endereco
        # ==============================================================
        @mcp.tool()
        async def plotar_endereco(
            endereco: str | None = None,
            latitude: float | None = None,
            longitude: float | None = None,
            label: str | None = None,
            detalhes: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """
            Adiciona um PIN no MAPA INTERATIVO da plataforma para um endereço
            ou coordenada. O ponto é desenhado automaticamente no MapLibre da
            UI — NÃO é necessário fornecer link externo do Google Maps.

            Aceita duas formas de entrada (uma das duas é obrigatória):
              • endereco: texto livre — geocodificado via Azure Maps Search
              • latitude + longitude: coordenadas explícitas (mais preciso)

            Se ambos forem fornecidos, o ENDEREÇO tem precedência (mais
            auditável — a tool registra endereco_consultado e
            endereco_resolvido no retorno).

            ENRIQUECIMENTO DO POPUP (parâmetro `detalhes`):
              Quando você está plotando uma jazida/empresa para a qual já
              obteve dados via jazidas__detalhes_processo ou buscar_*, passe
              um dict `detalhes` com as chaves abaixo para que o POPUP do pin
              no mapa mostre as mesmas informações que já apareceram no chat.
              Sem `detalhes`, o popup mostra só lat/lon + endereço resolvido.

              Chaves reconhecidas com renderização especial:
                • processo:    "NNN.NNN/AAAA"  (habilita "Ver polígono" no popup)
                • substancia:  "Mármore", "Areia Lavada", …
                • area_ha:     número de hectares (ex: 850)
                • fase:        "Em análise", "Ativa", "Disponível", …
                • municipio:   "Sento Sé/BA"
                • titulares:   ["LC Comercio EIRELI", "Mineração Juparanã Ltda"]
                                (lista) ou string com nomes separados por vírgula
                • cnpj:        "12.345.678/0001-90"
                • telefone:    "(74) 9999-9999"
                • email:       "contato@empresa.com.br"
                • distancia_km: distância até a obra ativa
                • observacao:  texto livre adicional

              Qualquer outra chave também é exibida como linha "Chave: valor"
              genérica — não invente dados: só passe o que veio das tools.

            Use cases:
            - "Mostre no mapa o endereço da empresa X" → endereco="..."
            - "Plote essas coordenadas: -20.05, -44.61" → latitude/longitude
            - "Plote a jazida 870.773/2012 com seus detalhes" →
                latitude/longitude + detalhes={"processo": "870.773/2012",
                "substancia": "Mármore", "area_ha": 850, "fase": "Em análise",
                "municipio": "Sento Sé/BA",
                "titulares": ["LC Comercio EIRELI", "Mineração Juparanã Ltda"]}

            Args:
                endereco: Texto livre do endereço/POI (ex: "Av. Itaúna, 200,
                          Itaúna, MG" ou "Porto de Aratu, BA")
                latitude: Latitude (opcional, se endereco for dado)
                longitude: Longitude (opcional, se endereco for dado)
                label: Rótulo curto a exibir no pin (ex: "LC Comercio EIRELI").
                       Se omitido, usa o endereço resolvido ou as coordenadas.
                detalhes: Dict com metadados estruturados a exibir no popup do
                          pin (substância, área, titulares, etc.). Veja seção
                          "ENRIQUECIMENTO DO POPUP" acima.
            """
            logger.info(
                f"plotar_endereco: endereco='{endereco}', "
                f"lat={latitude}, lon={longitude}, label='{label}', "
                f"detalhes_keys={list((detalhes or {}).keys())}"
            )

            ponto, err = await _resolve_endpoint(
                "ponto", latitude, longitude, endereco,
            )
            if err is not None:
                return err
            assert ponto is not None

            display_label = (
                label
                or ponto.get("endereco_resolvido")
                or ponto.get("endereco_consultado")
                or f"{ponto['lat']:.5f}, {ponto['lon']:.5f}"
            )

            pin_payload: dict[str, Any] = {
                "lat": ponto["lat"],
                "lon": ponto["lon"],
                "label": display_label,
                "endereco_consultado": ponto.get("endereco_consultado"),
                "endereco_resolvido": ponto.get("endereco_resolvido"),
                "fonte": ponto.get("fonte"),
            }
            if detalhes:
                # Sanitiza: drop None/"" keys e garante tipos serializáveis.
                sanitized: dict[str, Any] = {}
                for k, v in detalhes.items():
                    if v is None or v == "":
                        continue
                    if isinstance(v, (str, int, float, bool)):
                        sanitized[str(k)] = v
                    elif isinstance(v, (list, tuple)):
                        # Lista de titulares / contatos / etc.
                        sanitized[str(k)] = [str(item) for item in v if item]
                    else:
                        sanitized[str(k)] = str(v)
                if sanitized:
                    pin_payload["detalhes"] = sanitized

            return {
                "sucesso": True,
                "pin": pin_payload,
                "mensagem": (
                    f"Pin adicionado ao mapa em {ponto['lat']:.5f}, "
                    f"{ponto['lon']:.5f}."
                ),
            }

        registered += 1

    else:
        logger.warning(
            "Azure Maps key not configured — tools 14-18 "
            "(calcular_rota, comparar_rotas, calcular_isocrona, "
            "geocodificar, plotar_endereco) NOT registered"
        )

    global TOOLS_REGISTERED
    TOOLS_REGISTERED = registered

    logger.info(
        f"Registered {registered}/18 geo tools (max com Azure) | "
        f"Azure Maps: {'configured' if azure_maps_configured else 'not configured'}"
    )
