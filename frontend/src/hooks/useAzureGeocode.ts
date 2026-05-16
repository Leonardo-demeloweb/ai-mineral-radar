/**
 * Geocoding Hook
 *
 * Forward:  address string → { lat, lon, label }
 * Reverse:  { lat, lon }   → { label, municipio, uf }
 *
 * Calls the backend endpoint POST /api/v1/geo/geocode which
 * wraps Azure Maps — the API key stays server-side.
 */

import { useState } from 'react'
import { api } from '@/lib/api'

export interface GeocodeResult {
  lat: number
  lon: number
  label: string
  municipio?: string
  uf?: string
}

interface GeocodeResponse {
  tipo: string
  resultados: GeocodeResult[]
}

export function useAzureGeocode() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function searchAddress(
    query: string,
    limit = 5,
  ): Promise<GeocodeResult[]> {
    if (!query.trim()) return []
    setLoading(true)
    setError(null)
    try {
      const data = await api
        .post('geo/geocode', { json: { endereco: query.trim(), limite: limit } })
        .json<GeocodeResponse>()
      return data.resultados
    } catch (e) {
      setError((e as Error).message)
      return []
    } finally {
      setLoading(false)
    }
  }

  async function reverseGeocode(
    lat: number,
    lon: number,
  ): Promise<GeocodeResult | null> {
    setLoading(true)
    setError(null)
    try {
      const data = await api
        .post('geo/geocode', { json: { lat, lon } })
        .json<GeocodeResponse>()
      return data.resultados[0] ?? null
    } catch (e) {
      setError((e as Error).message)
      return null
    } finally {
      setLoading(false)
    }
  }

  return { searchAddress, reverseGeocode, loading, error }
}
