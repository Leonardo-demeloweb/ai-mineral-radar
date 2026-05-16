import { useState, useRef, useEffect } from 'react'
import {
  Layers,
  CircleDot,
  Circle,
  Building2,
  Navigation,
  MapPin,
  Radar,
  Hexagon,
  Square,
  Mountain,
  TrainFront,
} from 'lucide-react'
import { useMapStore, type LayerId } from '@/stores/mapStore'
import { cn } from '@/lib/cn'

interface LayerConfig {
  id: LayerId
  label: string
  icon: React.ReactNode
  description?: string
}

const LAYER_CONFIG: LayerConfig[] = [
  {
    id: 'obra-point',
    label: 'Pino do projeto',
    icon: <MapPin size={14} />,
    description: 'Marcador do projeto ativo no mapa',
  },
  {
    id: 'search-radius',
    label: 'Raio de busca',
    icon: <Radar size={14} />,
    description: 'Círculo de busca em torno do pino do projeto',
  },
  {
    id: 'jazida-points',
    label: 'Jazidas ANM',
    icon: <CircleDot size={14} />,
    description: 'Pontos de jazidas minerais',
  },
  {
    id: 'jazida-polygons',
    label: 'Polígonos Jazidas',
    icon: <Hexagon size={14} />,
    description: 'Polígonos selecionados via pin (Ver area)',
  },
  {
    id: 'empresa-points',
    label: 'Empresas',
    icon: <Building2 size={14} />,
    description: 'Fornecedores e empresas encontradas',
  },
  {
    id: 'municipio-borders',
    label: 'Limites Municipais',
    icon: <Square size={14} />,
    description: 'Fronteiras dos municípios envolvidos',
  },
  {
    id: 'chat-context',
    label: 'Portos e ferrovias (chat)',
    icon: <TrainFront size={14} />,
    description: 'Polígonos de portos e malha ferroviária pedidos na conversa',
  },
  {
    id: 'routes',
    label: 'Rotas',
    icon: <Navigation size={14} />,
    description: 'Rotas calculadas até fornecedores',
  },
  {
    id: 'isochrone',
    label: 'Isócrona',
    icon: <Circle size={14} />,
    description: 'Área alcançável por tempo ou distância',
  },
  {
    id: 'cprm-geologico',
    label: 'Geologia CPRM',
    icon: <Mountain size={14} />,
    description: 'Unidades litoestratigráficas 1:1M (CPRM/SGB)',
  },
]

export function MapLayerControl() {
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)
  const { layerVisibility, toggleLayer } = useMapStore()

  const activeCount = LAYER_CONFIG.filter((l) => layerVisibility[l.id]).length

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    if (open) document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  return (
    <div ref={panelRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title="Controle de camadas"
        className={cn(
          'flex items-center gap-1.5 h-9 px-3 rounded-lg border shadow-lg text-sm font-medium transition-colors',
          open
            ? 'bg-(--color-primary) border-(--color-primary) text-white'
            : 'bg-(--color-surface) border-(--color-border) text-(--color-text-muted) hover:text-(--color-text)',
        )}
      >
        <Layers size={15} />
        <span className="text-xs">{activeCount}/{LAYER_CONFIG.length}</span>
      </button>

      {open && (
        <div className="absolute bottom-11 right-0 w-56 rounded-xl border border-(--color-border) bg-(--color-surface) shadow-2xl overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-(--color-border)">
            <span className="text-xs font-semibold text-(--color-text) uppercase tracking-wide">
              Camadas
            </span>
            <button
              onClick={() => setOpen(false)}
              className="text-[10px] text-(--color-text-muted) hover:text-(--color-text) transition-colors"
            >
              fechar
            </button>
          </div>

          <div className="py-1">
            {LAYER_CONFIG.map((layer) => {
              const enabled = layerVisibility[layer.id]
              return (
                <button
                  key={layer.id}
                  onClick={() => toggleLayer(layer.id)}
                  className={cn(
                    'flex w-full items-center gap-3 px-3 py-2 text-left transition-colors',
                    'hover:bg-zinc-100 dark:hover:bg-zinc-800',
                  )}
                >
                  {/* Toggle pill */}
                  <div
                    className={cn(
                      'relative flex h-4 w-7 shrink-0 items-center rounded-full transition-colors duration-200',
                      enabled ? 'bg-green-500' : 'bg-zinc-300 dark:bg-zinc-600',
                    )}
                  >
                    <span
                      className={cn(
                        'absolute h-3 w-3 rounded-full bg-white shadow transition-transform duration-200',
                        enabled ? 'translate-x-3.5' : 'translate-x-0.5',
                      )}
                    />
                  </div>

                  {/* Icon + label */}
                  <span
                    className={cn(
                      'flex items-center gap-2',
                      enabled
                        ? 'text-(--color-text)'
                        : 'text-(--color-text-muted)',
                    )}
                  >
                    {layer.icon}
                    <span className="text-xs font-medium">{layer.label}</span>
                  </span>
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
