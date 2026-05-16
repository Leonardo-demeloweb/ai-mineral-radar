export interface MapaPonto {
  lat: number
  lon: number
  label: string
  id: string
  /** geoquimica | afloramento | ocorrencia_mineral — vem do mapa MCP */
  tipo?: string
  projeto?: string
  substancia?: string
  analitos?: string[]
  // Enriched jazida fields
  substancias?: string[]
  municipios?: string[]
  uf?: string[]
  titulares?: string[]
  cnpj_titulares?: string[]
  fase?: string
  area_ha?: number
  distancia_km?: number
  tipo_requerimento?: string
  // Address fields (from CNPJ cross-reference)
  endereco?: string
  telefone?: string
  email?: string
  /** ANM process polygon vs CPRM occurrence buffer vs sem polígono (afloramentos). */
  polygonFetch?: 'anm' | 'cprm' | 'none'
}

export interface MapaPontoEmpresa {
  lat: number
  lon: number
  label: string
  cnpj_basico: string
  // Enriched empresa fields
  razao_social?: string
  nome_fantasia?: string
  cnpj_completo?: string
  municipio?: string
  uf?: string
  telefone?: string
  email?: string
  endereco?: string
  porte?: string
  capital_social?: number
  cnae_descricao?: string
  situacao?: string
  distancia_km?: number
}

export interface GeoJSONFeature {
  type: 'Feature'
  properties: Record<string, unknown>
  geometry: GeoJSONGeometry
}

export type GeoJSONGeometry =
  | { type: 'Point'; coordinates: [number, number] }
  | { type: 'Polygon'; coordinates: [number, number][][] }
  | { type: 'MultiPolygon'; coordinates: [number, number][][][] }
  | { type: 'LineString'; coordinates: [number, number][] }
  | { type: 'MultiLineString'; coordinates: [number, number][][] }

export interface RouteLine {
  id: string
  label: string
  origin: {
    lat: number
    lon: number
    endereco_consultado?: string | null
    endereco_resolvido?: string | null
    fonte?: 'coordenadas' | 'geocodificado' | string | null
  }
  destination: {
    lat: number
    lon: number
    endereco_consultado?: string | null
    endereco_resolvido?: string | null
    fonte?: 'coordenadas' | 'geocodificado' | string | null
  }
  points: { lat: number; lon: number }[]
  distance_km: number
  duration_min: number
  traffic_delay_min: number
  travel_mode: 'car' | 'truck'
  /**
   * Off-road distance (in km) between the requested origin coordinate and
   * the first reachable point of the road graph. Filled when the Azure Maps
   * router had to snap to the nearest road. 0 when the route covers the
   * full leg.
   */
  gap_origin_km?: number
  /** Same as ``gap_origin_km`` but for the destination side. */
  gap_destination_km?: number
  /** Convenience flag: true when ``gap_origin_km`` or ``gap_destination_km`` exceeds the backend threshold (0.1 km). */
  partial_access?: boolean
}

export interface KmlOverlay {
  id: string
  name: string
  geojson: {
    type: 'FeatureCollection'
    features: GeoJSONFeature[]
  }
  color?: string
}

/**
 * Address / coordinate pin emitted by the agent via geo__plotar_endereco.
 * Rendered as a standalone amber marker on the map (distinct from obra,
 * jazida and empresa markers) so the user can locate any address or
 * coordinate the chat references.
 */
/**
 * Estrutura flexível de metadados enriquecidos do pin. O agente preenche
 * com o que já obteve via outras tools (jazidas__detalhes_processo,
 * buscar_*, etc.). Chaves conhecidas (processo, substancia, area_ha,
 * fase, municipio, titulares, cnpj, telefone, email, distancia_km,
 * observacao) recebem ícone/formatação especial no popup; as demais são
 * exibidas como linhas "Chave: valor" genéricas.
 */
export type AddressPinDetailValue = string | number | boolean | string[]
export type AddressPinDetails = Record<string, AddressPinDetailValue>

export interface AddressPin {
  id: string
  lat: number
  lon: number
  label: string
  endereco_consultado?: string | null
  endereco_resolvido?: string | null
  fonte?: 'coordenadas' | 'geocodificado' | string | null
  detalhes?: AddressPinDetails | null
}
