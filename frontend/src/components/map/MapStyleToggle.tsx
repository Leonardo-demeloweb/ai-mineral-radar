import { useState } from 'react'
import { Satellite, Map, Info } from 'lucide-react'
import { useMapStore } from '@/stores/mapStore'
import { cn } from '@/lib/cn'

const STYLE_META = {
  map: {
    label: 'Mapa vetorial',
    provider: 'CARTO Voyager',
    source: 'OpenStreetMap contributors',
    updated: 'Atualizado continuamente',
    resolution: '—',
  },
  satellite: {
    label: 'Satélite',
    provider: 'Esri World Imagery',
    source: 'Maxar, Earthstar Geographics',
    updated: 'Varia por região',
    resolution: '15 cm – 1 m',
  },
} as const

export function MapStyleToggle() {
  const { baseStyle, setBaseStyle } = useMapStore()
  const [showInfo, setShowInfo] = useState(false)
  const isSatellite = baseStyle === 'satellite'
  const meta = STYLE_META[baseStyle]

  return (
    <div className="relative flex flex-col items-end gap-1">
      {/* Info popover */}
      {showInfo && (
        <div className="absolute bottom-11 right-0 w-52 rounded-xl border border-(--color-border) bg-(--color-surface)/95 backdrop-blur-sm shadow-xl px-3 py-2.5 space-y-1.5">
          <p className="text-xs font-semibold text-(--color-text)">{meta.label}</p>
          <div className="space-y-0.5">
            <Row label="Provedor" value={meta.provider} />
            <Row label="Fonte" value={meta.source} />
            <Row label="Imagens" value={meta.updated} />
            {isSatellite && <Row label="Resolução" value={meta.resolution} />}
          </div>
        </div>
      )}

      {/* Toggle buttons */}
      <div className="flex items-center overflow-hidden rounded-lg border border-(--color-border) shadow-lg">
        <button
          onClick={() => setShowInfo((v) => !v)}
          title="Informações da camada"
          className={cn(
            'flex h-9 w-8 items-center justify-center transition-colors',
            showInfo
              ? 'bg-(--color-primary) text-white'
              : 'bg-(--color-surface) text-(--color-text-muted) hover:text-(--color-text) hover:bg-zinc-100 dark:hover:bg-zinc-800',
          )}
        >
          <Info size={13} />
        </button>
        <button
          onClick={() => setBaseStyle('map')}
          title="Mapa vetorial (CARTO)"
          className={cn(
            'flex h-9 w-9 items-center justify-center transition-colors',
            !isSatellite
              ? 'bg-(--color-primary) text-white'
              : 'bg-(--color-surface) text-(--color-text-muted) hover:text-(--color-text) hover:bg-zinc-100 dark:hover:bg-zinc-800',
          )}
        >
          <Map size={16} />
        </button>
        <button
          onClick={() => setBaseStyle('satellite')}
          title="Satélite (Esri / Maxar)"
          className={cn(
            'flex h-9 w-9 items-center justify-center transition-colors',
            isSatellite
              ? 'bg-(--color-primary) text-white'
              : 'bg-(--color-surface) text-(--color-text-muted) hover:text-(--color-text) hover:bg-zinc-100 dark:hover:bg-zinc-800',
          )}
        >
          <Satellite size={16} />
        </button>
      </div>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[11px] text-(--color-text-muted) shrink-0">{label}</span>
      <span className="text-[11px] text-(--color-text) text-right leading-tight">{value}</span>
    </div>
  )
}
