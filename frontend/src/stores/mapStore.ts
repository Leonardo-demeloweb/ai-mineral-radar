import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { MapaPonto, MapaPontoEmpresa, GeoJSONFeature, RouteLine, KmlOverlay, AddressPin } from '@/types/geojson'

export type BaseStyle = 'map' | 'satellite'

export type LayerId =
  | 'jazida-points'
  | 'jazida-polygons'
  | 'empresa-points'
  | 'municipio-borders'
  | 'chat-context'
  | 'routes'
  | 'kml-overlays'
  | 'search-radius'
  | 'obra-point'
  | 'isochrone'
  | 'address-pins'
  | 'cprm-geologico'

interface ViewportState {
  lng: number
  lat: number
  zoom: number
}

export interface TopicMarkers {
  empresa: MapaPontoEmpresa[]
  jazida: MapaPonto[]
}

interface MapState {
  viewport: ViewportState
  setViewport: (value: Partial<ViewportState>) => void
  flyTo: (lng: number, lat: number, zoom?: number) => void
  fitBounds: (bbox: [number, number, number, number]) => void

  baseStyle: BaseStyle
  setBaseStyle: (style: BaseStyle) => void

  /** All markers accumulated across all searches, keyed by topic label */
  markersByTopic: Record<string, TopicMarkers>
  /** Ordered list of topic labels (same keys as markersByTopic, kept in insertion order) */
  markerTopics: string[]
  /** Currently active topic filter — null means show all */
  activeTopicFilter: string | null

  polygons: GeoJSONFeature[]
  /** Portos / malha ferroviária vindos do chat (SSE ``context_geometry``). */
  chatGeometries: GeoJSONFeature[]
  jazidaPolygons: GeoJSONFeature[]
  municipioPolygons: GeoJSONFeature[]
  kmlOverlays: KmlOverlay[]
  routeLines: RouteLine[]
  isochroneFeature: GeoJSONFeature | null
  /** Standalone pins for arbitrary addresses/coordinates plotted via plotar_endereco */
  addressPins: AddressPin[]
  searchRadius: { center: [number, number]; km: number } | null

  layerVisibility: Record<LayerId, boolean>
  toggleLayer: (id: LayerId) => void
  /** Garante que polígonos de jazida fiquem visíveis (ex.: após "Ver area" no popup). */
  ensureJazidaPolygonsVisible: () => void

  selectedFeatureId: string | null
  selectFeature: (id: string | null) => void

  /** Add markers for a topic — also registers topic in markerTopics */
  addTopicMarkers: (topic: string, empresa: MapaPontoEmpresa[], jazida: MapaPonto[]) => void
  /** Batch-set all topics at once (single render) */
  setAllTopicMarkers: (topics: Record<string, TopicMarkers>, orderedLabels: string[]) => void
  /** Set or clear the active topic filter */
  setTopicFilter: (topic: string | null) => void

  setJazidaMarkers: (markers: MapaPonto[]) => void
  setEmpresaMarkers: (markers: MapaPontoEmpresa[]) => void
  clearMarkers: () => void
  addPolygons: (features: GeoJSONFeature[]) => void
  addChatGeometries: (features: GeoJSONFeature[]) => void
  setJazidaPolygons: (features: GeoJSONFeature[]) => void
  setMunicipioPolygons: (features: GeoJSONFeature[]) => void
  clearPolygons: () => void

  /** Set of normalised processo IDs whose polygons are currently visible on map */
  activeJazidaPolygonIds: Set<string>
  /**
   * On-demand polygon cache: processoId → GeoJSON features.
   * Populated lazily when the user toggles a polygon on for the first time.
   */
  jazidaPolygonCache: Record<string, GeoJSONFeature[]>
  /** Set of processo IDs currently being fetched (shows spinner on pin) */
  jazidaPolygonLoading: Set<string>

  toggleJazidaPolygon: (processoId: string) => void
  /** Called by MapContainer after a successful on-demand fetch */
  setJazidaPolygonData: (processoId: string, features: GeoJSONFeature[]) => void
  /** Mark a processo as loading / done loading */
  setJazidaPolygonLoading: (processoId: string, loading: boolean) => void
  clearActiveJazidaPolygons: () => void
  setRouteLines: (routes: RouteLine[]) => void
  addRouteLine: (route: RouteLine) => void
  setIsochroneFeature: (feature: GeoJSONFeature | null) => void
  clearRouteAndIsochrone: () => void
  addAddressPin: (pin: AddressPin) => void
  clearAddressPins: () => void
  setSearchRadius: (radius: MapState['searchRadius']) => void
  clearAll: () => void
}

const defaultLayerVisibility: Record<LayerId, boolean> = {
  'jazida-points': true,
  'jazida-polygons': true,
  'empresa-points': true,
  'municipio-borders': true,
  'chat-context': true,
  'routes': true,
  'kml-overlays': true,
  'search-radius': true,
  'obra-point': true,
  'isochrone': true,
  'address-pins': true,
  'cprm-geologico': false,  // off by default — camada densa, ativa por demanda
}

const initialMapData = {
  markersByTopic: {} as Record<string, TopicMarkers>,
  markerTopics: [] as string[],
  activeTopicFilter: null as string | null,
  polygons: [] as GeoJSONFeature[],
  chatGeometries: [] as GeoJSONFeature[],
  jazidaPolygons: [] as GeoJSONFeature[],
  municipioPolygons: [] as GeoJSONFeature[],
  kmlOverlays: [] as KmlOverlay[],
  routeLines: [] as RouteLine[],
  isochroneFeature: null as GeoJSONFeature | null,
  addressPins: [] as AddressPin[],
  searchRadius: null as MapState['searchRadius'],
  selectedFeatureId: null as string | null,
  activeJazidaPolygonIds: new Set<string>(),
  jazidaPolygonCache: {} as Record<string, GeoJSONFeature[]>,
  jazidaPolygonLoading: new Set<string>(),
}


export const useMapStore = create<MapState>()(
  persist(
  (set) => ({
  viewport: { lng: -46.633, lat: -23.55, zoom: 13 },
  baseStyle: 'map' as BaseStyle,
  ...initialMapData,
  layerVisibility: { ...defaultLayerVisibility },

  setBaseStyle: (baseStyle) => set({ baseStyle }),

  setViewport: (value) =>
    set((state) => ({ viewport: { ...state.viewport, ...value } })),

  flyTo: (lng, lat, zoom) =>
    set((state) => ({ viewport: { ...state.viewport, lng, lat, ...(zoom != null && { zoom }) } })),

  fitBounds: () => {
    // MapLibre fitBounds will be called imperatively via ref in MapContainer
  },

  toggleLayer: (id) =>
    set((state) => ({
      layerVisibility: {
        ...state.layerVisibility,
        [id]: !state.layerVisibility[id],
      },
    })),

  ensureJazidaPolygonsVisible: () =>
    set((state) => {
      if (state.layerVisibility['jazida-polygons']) return {}
      return {
        layerVisibility: {
          ...state.layerVisibility,
          'jazida-polygons': true,
        },
      }
    }),
  selectFeature: (id) => set({ selectedFeatureId: id }),

  addTopicMarkers: (topic, empresa, jazida) => {
    set((state) => ({
      markersByTopic: {
        ...state.markersByTopic,
        [topic]: {
          empresa: [...(state.markersByTopic[topic]?.empresa ?? []), ...empresa],
          jazida:  [...(state.markersByTopic[topic]?.jazida  ?? []), ...jazida],
        },
      },
      markerTopics: state.markerTopics.includes(topic)
        ? state.markerTopics
        : [...state.markerTopics, topic],
    }))
  },

  setAllTopicMarkers: (topics, orderedLabels) => {
    set({ markersByTopic: topics, markerTopics: orderedLabels })
  },

  setTopicFilter: (topic) => set({ activeTopicFilter: topic }),

  // Legacy setters — kept for backward compat (unused by new topic-based flow)
  setJazidaMarkers: (markers) => set({ jazidaMarkers: markers } as Partial<MapState>),
  setEmpresaMarkers: (markers) => set({ empresaMarkers: markers } as Partial<MapState>),
  clearMarkers: () => set({ markersByTopic: {}, markerTopics: [], activeTopicFilter: null }),
  addPolygons: (features) =>
    set((state) => ({ polygons: [...state.polygons, ...features] })),
  addChatGeometries: (features) =>
    set((state) => ({ chatGeometries: [...state.chatGeometries, ...features] })),
  setJazidaPolygons: (features) => set({ jazidaPolygons: features }),
  setMunicipioPolygons: (features) => set({ municipioPolygons: features }),
  clearPolygons: () => set({
    polygons: [],
    chatGeometries: [],
    jazidaPolygons: [],
    municipioPolygons: [],
    activeJazidaPolygonIds: new Set<string>(),
  }),

  toggleJazidaPolygon: (processoId) =>
    set((state) => {
      const next = new Set(state.activeJazidaPolygonIds)
      if (next.has(processoId)) {
        next.delete(processoId)
        return { activeJazidaPolygonIds: next }
      }
      // Turning on: activate ID immediately; MapContainer will fetch if not cached
      next.add(processoId)
      return { activeJazidaPolygonIds: next }
    }),

  setJazidaPolygonData: (processoId, features) =>
    set((state) => ({
      jazidaPolygonCache: { ...state.jazidaPolygonCache, [processoId]: features },
    })),

  setJazidaPolygonLoading: (processoId, loading) =>
    set((state) => {
      const next = new Set(state.jazidaPolygonLoading)
      if (loading) next.add(processoId)
      else next.delete(processoId)
      return { jazidaPolygonLoading: next }
    }),

  clearActiveJazidaPolygons: () => set({
    activeJazidaPolygonIds: new Set<string>(),
    jazidaPolygonLoading: new Set<string>(),
  }),
  setRouteLines: (routeLines) => set({ routeLines }),
  addRouteLine: (route) =>
    set((state) => ({ routeLines: [...state.routeLines, route] })),
  setIsochroneFeature: (isochroneFeature) => set({ isochroneFeature }),
  clearRouteAndIsochrone: () => set({ routeLines: [], isochroneFeature: null }),
  addAddressPin: (pin) =>
    set((state) => {
      const lat = Number(pin.lat)
      const lon = Number(pin.lon)
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return state

      const key = `${lat.toFixed(5)},${lon.toFixed(5)}`
      const idx = state.addressPins.findIndex(
        (p) => `${Number(p.lat).toFixed(5)},${Number(p.lon).toFixed(5)}` === key,
      )

      const normalized: AddressPin = {
        ...pin,
        lat,
        lon,
        id: pin.id?.trim() ? pin.id : crypto.randomUUID(),
      }

      if (idx >= 0) {
        const prev = state.addressPins[idx]
        const mergedDet =
          normalized.detalhes && Object.keys(normalized.detalhes).length > 0
            ? { ...(prev.detalhes ?? {}), ...normalized.detalhes }
            : prev.detalhes
        const merged: AddressPin = {
          ...prev,
          ...normalized,
          id: prev.id,
          label: normalized.label?.trim() ? normalized.label : prev.label,
          detalhes: mergedDet,
        }
        const next = [...state.addressPins]
        next[idx] = merged
        return { addressPins: next }
      }

      return { addressPins: [...state.addressPins, normalized] }
    }),
  clearAddressPins: () => set({ addressPins: [] }),
  setSearchRadius: (searchRadius) => set({ searchRadius }),

  clearAll: () => set({
    ...initialMapData,
    activeJazidaPolygonIds: new Set<string>(),
    jazidaPolygonCache: {},
    jazidaPolygonLoading: new Set<string>(),
    layerVisibility: { ...defaultLayerVisibility },
  }),

  }),
  {
    name: 'supplyradar-map',
    partialize: (state) => ({ baseStyle: state.baseStyle }),
  }
))
