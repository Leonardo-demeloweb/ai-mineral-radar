import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Layers, MapPin, MessageSquare, PanelLeftClose, FileText, X } from 'lucide-react'
import { MapContainer } from '@/components/map/MapContainer'
import { MapStyleToggle } from '@/components/map/MapStyleToggle'
import { MapLayerControl } from '@/components/map/MapLayerControl'
import { ChatShell } from '@/components/chat/ChatShell'
import { useUiStore } from '@/stores/uiStore'
import { useChatStore } from '@/stores/chatStore'
import { useFavoritesStore } from '@/stores/favoritesStore'
import { useMapStore } from '@/stores/mapStore'
import { useProjeto } from '@/hooks/useProjetos'
import { useAnalise } from '@/hooks/useAnalises'
import type { Fornecedor } from '@/types/api'
import type { MapaPonto, MapaPontoEmpresa } from '@/types/geojson'

const CHAT_W = 360
const CHAT_GAP = 12   // left-3 = 0.75rem = 12px
const OPEN_BTN_W = 40 // h-10 w-10 open-chat button width

function normalizeId(raw: string | undefined | null): string {
  return (raw ?? '').replace(/\D/g, '')
}

function fornecedorToEmpresa(f: Fornecedor): MapaPontoEmpresa {
  return {
    lat: f.localizacao!.lat,
    lon: f.localizacao!.lon,
    label: f.nome,
    cnpj_basico: normalizeId(f.cnpj),
    razao_social: f.nome,
    nome_fantasia: f.nome,
    cnpj_completo: f.cnpj ?? undefined,
    municipio: f.municipio ?? undefined,
    uf: f.uf ?? undefined,
    telefone: f.contato_telefone ?? undefined,
    email: f.contato_email ?? undefined,
    endereco: f.endereco ?? undefined,
    porte: f.porte ?? undefined,
    cnae_descricao: f.cnae_descricao ?? undefined,
    situacao: f.situacao_cadastral ?? undefined,
    distancia_km: f.distancia_km ?? undefined,
  }
}

function fornecedorToJazida(f: Fornecedor): MapaPonto {
  return {
    lat: f.localizacao!.lat,
    lon: f.localizacao!.lon,
    label: f.substancia ?? f.nome,
    id: f.processo_anm ?? f.id,
    substancias: f.substancia ? [f.substancia] : undefined,
    municipios: f.municipio ? [f.municipio] : undefined,
    uf: f.uf ? [f.uf] : undefined,
    fase: f.fase ?? undefined,
    distancia_km: f.distancia_km ?? undefined,
    endereco: f.endereco ?? undefined,
    telefone: f.contato_telefone ?? undefined,
    email: f.contato_email ?? undefined,
  }
}

type TopicMarkers = { empresa: MapaPontoEmpresa[], jazida: MapaPonto[] }

function plotFornecedoresOnMap(
  withLocation: Fornecedor[],
  setAllTopicMarkers: (topics: Record<string, TopicMarkers>, labels: string[]) => void,
) {
  const topics: Record<string, TopicMarkers> = {}
  const labelsSet = new Set<string>()

  for (const f of withLocation) {
    const isAnm = f.tipo_fonte === 'anm'
    const fallback = isAnm ? 'Jazidas (salvos)' : 'Empresas (salvos)'
    const label = f.topico || fallback

    if (!topics[label]) topics[label] = { empresa: [], jazida: [] }
    if (!labelsSet.has(label)) labelsSet.add(label)

    if (isAnm) {
      topics[label].jazida.push(fornecedorToJazida(f))
    } else {
      topics[label].empresa.push(fornecedorToEmpresa(f))
    }
  }

  setAllTopicMarkers(topics, [...labelsSet])
}

export function WorkspacePage() {
  const [chatOpen, setChatOpen] = useState(true)
  const [searchParams, setSearchParams] = useSearchParams()
  const activeProjetoId = useUiStore((s) => s.activeProjetoId)
  const setActiveProjetoId = useUiStore((s) => s.setActiveProjetoId)
  const mapTopics = useMapStore((s) => s.markerTopics)
  const setContext = useChatStore((s) => s.setContext)
  const activeAnaliseId = searchParams.get('analiseId')
  const { data: activeProjeto } = useProjeto(activeProjetoId ?? undefined)
  const { data: activeAnalise } = useAnalise(activeAnaliseId ?? undefined)
  const syncFromStudy = useFavoritesStore((s) => s.syncFromStudy)
  const resetFavorites = useFavoritesStore((s) => s.reset)
  const activeTopicFilter         = useMapStore((s) => s.activeTopicFilter)
  const setTopicFilter            = useMapStore((s) => s.setTopicFilter)
  const markersByTopic            = useMapStore((s) => s.markersByTopic)
  const setAllTopicMarkers        = useMapStore((s) => s.setAllTopicMarkers)
  const clearMarkers              = useMapStore((s) => s.clearMarkers)
  const clearActiveJazidaPolygons = useMapStore((s) => s.clearActiveJazidaPolygons)
  const clearRouteAndIsochrone    = useMapStore((s) => s.clearRouteAndIsochrone)
  const clearAddressPins          = useMapStore((s) => s.clearAddressPins)

  // Sync favorites from analise
  useEffect(() => {
    if (activeAnalise?.fornecedores) {
      syncFromStudy(activeAnalise.fornecedores)
    } else {
      resetFavorites()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeAnalise?._id, activeAnalise?.total_fornecedores])

  // Restore favorited fornecedores on the map when entering an analise.
  const plottedForAnalise = useRef<string | null>(null)
  const fornecedorCount = activeAnalise?.total_fornecedores ?? -1

  useEffect(() => {
    if (!activeAnaliseId) {
      clearMarkers()
      clearActiveJazidaPolygons()
      clearRouteAndIsochrone()
      clearAddressPins()
      plottedForAnalise.current = null
      return
    }

    if (plottedForAnalise.current === activeAnaliseId) return

    const fornecedores = activeAnalise?.fornecedores
    if (fornecedores == null) return

    plottedForAnalise.current = activeAnaliseId
    clearMarkers()
    clearActiveJazidaPolygons()
    clearRouteAndIsochrone()
    clearAddressPins()

    const withLocation = fornecedores.filter((f: Fornecedor) => f.localizacao)
    console.log('[workspace] restore-favorites: analise=%s total=%d withLoc=%d',
      activeAnaliseId, fornecedores.length, withLocation.length)
    if (fornecedores.length > 0 && withLocation.length === 0) {
      console.log('[workspace] sample fornecedor:', JSON.stringify(fornecedores[0], null, 2))
    }

    if (withLocation.length === 0) return

    plotFornecedoresOnMap(withLocation, setAllTopicMarkers)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeAnaliseId, fornecedorCount])

  const overlayLeft = chatOpen
    ? CHAT_GAP + CHAT_W + CHAT_GAP
    : CHAT_GAP + OPEN_BTN_W + CHAT_GAP

  useEffect(() => {
    const projetoId = searchParams.get('projetoId')
    const analiseId = searchParams.get('analiseId')
    setActiveProjetoId(projetoId)
    setContext(projetoId, analiseId)
    return () => {
      setActiveProjetoId(null)
      setContext(null, null)
    }
  }, [searchParams, setActiveProjetoId, setContext])

  function dismissProjeto() {
    const next = new URLSearchParams(searchParams)
    next.delete('projetoId')
    setSearchParams(next, { replace: true })
  }

  const hasOverlay = activeProjeto || activeAnalise || mapTopics.length > 0

  return (
    <div className="relative h-full min-h-0 w-full">
      {/* Map — fills entire area */}
      <div className="absolute inset-0">
        <MapContainer height="100%" />
      </div>

      {/* Chat panel — floating overlay, left side */}
      <div
        className="absolute top-3 bottom-3 left-3 z-10 min-h-0 transition-transform duration-300 ease-in-out"
        style={{
          width: CHAT_W,
          transform: chatOpen ? 'translateX(0)' : `translateX(calc(-100% - ${CHAT_GAP}px))`,
        }}
      >
        <div className="relative flex h-full min-h-0 w-full max-w-full overflow-hidden rounded-xl shadow-2xl">
          <ChatShell />
          <button
            onClick={() => setChatOpen(false)}
            title="Fechar chat"
            className="absolute top-3 right-3 z-20 flex h-7 w-7 items-center justify-center rounded-md text-(--color-text-muted) hover:bg-zinc-200 dark:hover:bg-zinc-700 transition-colors"
          >
            <PanelLeftClose size={14} />
          </button>
        </div>
      </div>

      {/* Context overlay — top of map, stretches between chat and zoom controls */}
      {hasOverlay && (
        <div
          className="absolute top-3 z-10 flex flex-wrap items-start gap-1.5 transition-[left] duration-300 ease-in-out"
          style={{ left: overlayLeft, right: 56 }}
        >
          {/* Projeto context chip — always first in flow */}
          {activeProjeto && (
            <Link
              to={`/projetos/${activeProjeto._id}`}
              className="flex items-center gap-2 rounded-xl bg-(--color-surface)/90 backdrop-blur-sm border border-(--color-border) shadow-lg px-3 py-2 max-w-[260px] shrink-0 hover:border-green-400 hover:shadow-green-100 dark:hover:shadow-none transition-all"
            >
              <MapPin size={13} className="shrink-0 text-green-500" />
              <div className="flex flex-col min-w-0 flex-1">
                <span className="text-xs font-semibold text-(--color-text) truncate leading-tight">
                  {activeProjeto.nome}
                </span>
                {activeProjeto.localizacao ? (
                  <span className="text-[11px] text-(--color-text-muted) leading-tight">
                    Raio: {activeProjeto.raio_busca_km} km
                  </span>
                ) : (
                  <span className="text-[11px] text-amber-500 leading-tight">
                    Sem localização
                  </span>
                )}
              </div>
              <button
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); dismissProjeto() }}
                title="Remover projeto do mapa"
                className="shrink-0 ml-1 flex h-5 w-5 items-center justify-center rounded text-(--color-text-muted) hover:bg-zinc-200 dark:hover:bg-zinc-700 transition-colors"
              >
                <X size={11} />
              </button>
            </Link>
          )}

          {/* Análise context chip */}
          {activeAnalise && (
            <Link
              to={`/projetos/${activeProjetoId}/analises/${activeAnaliseId}`}
              className="flex items-center gap-2 rounded-xl bg-(--color-surface)/90 backdrop-blur-sm border border-violet-300 dark:border-violet-700 shadow-lg px-3 py-2 max-w-[240px] shrink-0 hover:border-violet-500 hover:shadow-violet-100 dark:hover:shadow-none transition-all"
            >
              <FileText size={13} className="shrink-0 text-violet-500" />
              <div className="flex flex-col min-w-0">
                <span className="text-xs font-semibold text-(--color-text) truncate leading-tight">
                  {activeAnalise.titulo}
                </span>
                <span className="text-[11px] text-(--color-text-muted) leading-tight">
                  {activeAnalise.termo_busca || 'Aguardando busca via chat'}
                </span>
              </div>
            </Link>
          )}

          {/* Topic chips — clickable filter: active = show only that topic, inactive = show all */}
          {mapTopics.map((topic) => {
            const isActive = activeTopicFilter === topic
            const t = markersByTopic[topic]
            const count = (t?.empresa.length ?? 0) + (t?.jazida.length ?? 0)
            return (
              <button
                key={topic}
                title={isActive ? `Clique para ver todos os tópicos` : `Filtrar mapa: ${topic} (${count})`}
                onMouseDown={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  setTopicFilter(isActive ? null : topic)
                }}
                className={[
                  'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-[7px] text-[11px] font-medium shadow-sm max-w-[220px] shrink-0 transition-all select-none',
                  isActive
                    ? 'bg-green-600 border border-green-500 text-white shadow-green-300 dark:shadow-none scale-105'
                    : 'bg-(--color-surface)/90 backdrop-blur-sm border border-green-300 dark:border-green-700 text-green-700 dark:text-green-300 hover:border-green-500 hover:scale-105',
                ].join(' ')}
              >
                <Layers size={9} className="shrink-0 opacity-80" />
                <span className="truncate">{topic}</span>
                <span className="shrink-0 opacity-70 tabular-nums">({count})</span>
                <X size={9} className={['shrink-0', isActive ? 'opacity-80' : 'opacity-0 pointer-events-none'].join(' ')} />
              </button>
            )
          })}
        </div>
      )}

      {/* Map controls — bottom right, above attribution */}
      <div className="absolute bottom-8 right-3 z-10 flex items-center gap-2">
        <MapLayerControl />
        <MapStyleToggle />
      </div>

      {/* Open button — visible only when chat is closed */}
      {!chatOpen && (
        <button
          onClick={() => setChatOpen(true)}
          title="Abrir chat"
          className="absolute top-3 left-3 z-10 flex h-10 w-10 items-center justify-center rounded-xl shadow-lg bg-(--color-surface) border border-(--color-border) text-(--color-text-muted) hover:text-(--color-primary) transition-colors"
        >
          <MessageSquare size={18} />
        </button>
      )}
    </div>
  )
}
