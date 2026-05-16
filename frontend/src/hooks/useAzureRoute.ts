import { useCallback, useRef, useState } from 'react'
import type { RouteLine } from '@/types/geojson'

const AZURE_MAPS_KEY = import.meta.env.VITE_AZURE_MAPS_KEY
const BASE_URL = 'https://atlas.microsoft.com'

interface RouteRequest {
  originLat: number
  originLon: number
  destLat: number
  destLon: number
  mode?: 'truck' | 'car'
  avoidTolls?: boolean
  label?: string
}

interface RouteResponse {
  route: RouteLine | null
  error: string | null
}

export function useAzureRoute() {
  const [loading, setLoading] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const getRoute = useCallback(
    async (req: RouteRequest): Promise<RouteResponse> => {
      if (!AZURE_MAPS_KEY) {
        return { route: null, error: 'Azure Maps key not configured' }
      }

      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      setLoading(true)

      try {
        const { originLat, originLon, destLat, destLon, mode = 'truck', avoidTolls = false } = req
        const params = new URLSearchParams({
          'api-version': '1.0',
          'subscription-key': AZURE_MAPS_KEY,
          'query': `${originLat},${originLon}:${destLat},${destLon}`,
          'routeRepresentation': 'polyline',
          'computeTravelTimeFor': 'all',
          'traffic': 'true',
          'travelMode': mode,
        })

        if (mode === 'truck') {
          params.set('vehicleWeight', '40000')
          params.set('vehicleHeight', '4.5')
          params.set('vehicleWidth', '2.6')
          params.set('vehicleLength', '18.75')
        }
        if (avoidTolls) {
          params.set('avoid', 'tollRoads')
        }

        const resp = await fetch(
          `${BASE_URL}/route/directions/json?${params}`,
          { signal: controller.signal },
        )

        if (!resp.ok) {
          const text = await resp.text()
          return { route: null, error: `Azure Maps ${resp.status}: ${text}` }
        }

        const data = await resp.json()
        const azRoute = data.routes?.[0]
        if (!azRoute) {
          return { route: null, error: 'Nenhuma rota encontrada' }
        }

        const summary = azRoute.summary
        const points: { lat: number; lon: number }[] = []
        for (const leg of azRoute.legs ?? []) {
          for (const pt of leg.points ?? []) {
            points.push({ lat: pt.latitude, lon: pt.longitude })
          }
        }

        const route: RouteLine = {
          id: `route-${Date.now()}`,
          label: req.label ?? `${(summary.lengthInMeters / 1000).toFixed(1)} km`,
          origin: { lat: originLat, lon: originLon },
          destination: { lat: destLat, lon: destLon },
          points,
          distance_km: Math.round(summary.lengthInMeters / 100) / 10,
          duration_min: Math.round(summary.travelTimeInSeconds / 6) / 10,
          traffic_delay_min: Math.round((summary.trafficDelayInSeconds ?? 0) / 6) / 10,
          travel_mode: mode,
        }

        return { route, error: null }
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          return { route: null, error: null }
        }
        return { route: null, error: String(err) }
      } finally {
        setLoading(false)
      }
    },
    [],
  )

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  return { getRoute, loading, cancel }
}
