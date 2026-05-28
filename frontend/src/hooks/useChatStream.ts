/**
 * useChatStream
 * =============
 *
 * Connects the chat UI to POST /api/v1/chat/stream (SSE).
 *
 * SSE events consumed:
 *   meta       → captures session_id, shows route badge on thinking panel
 *   token      → first chunk removes the thinking row and opens the assistant
 *                bubble (spinner stays on assistant until ``done``); further
 *                chunks append to the assistant message
 *   tool_start → adds a running tool entry to the thinking panel
 *   tool_end   → marks that tool as completed (spinner → checkmark)
 *   done       → finalises assistant streaming; if no tokens arrived, the
 *                full answer comes from ``done.response`` (no stray "Concluído")
 *   map_data        → populates empresa/jazida markers on the map store
 *   route_data      → adds a route polyline (calcular_rota)
 *   isochrone_data  → sets the isochrone polygon (calcular_isocrona)
 *   pin_data        → adds an arbitrary address pin (plotar_endereco)
 *   context_geometry → refs porto/ferrovia; mapa busca GeoJSON via GET (sob demanda)
 *   analise_updated → invalidates analise query cache (auto-populated by agent)
 *   error           → shows error in chat
 */

import { useCallback, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { API_URL } from '@/lib/constants'
import {
  fetchContextGeometryRefs,
  type ContextGeometryRef,
} from '@/lib/api'
import { useChatStore } from '@/stores/chatStore'
import { useMapStore } from '@/stores/mapStore'
import { useUiStore } from '@/stores/uiStore'
import type { MapaPonto, MapaPontoEmpresa, GeoJSONFeature, RouteLine, AddressPin } from '@/types/geojson'

// ── Public types ──────────────────────────────────────────────────────────────

export type ChatMessageRole = 'user' | 'assistant' | 'thinking'

export interface ToolStep {
  name: string
  displayName: string
  status: 'running' | 'done'
}

export interface ChatMessage {
  id: string
  role: ChatMessageRole
  content: string
  isStreaming?: boolean
  /** Structured tool activity for the thinking panel. */
  tools?: ToolStep[]
  /** Intent route detected by the Router Agent. */
  route?: string
  /** Human-readable route reasoning. */
  routeReasoning?: string
}

// ── SSE payload shapes ────────────────────────────────────────────────────────

interface SseMetaPayload  { session_id?: string; route?: string; route_reasoning?: string }
interface SseTokenPayload { text: string }
interface SseToolPayload  { name: string; call_id: string }
interface SseDonePayload  { session_id: string; response: string; route: string; tool_calls_count: number }
interface SseErrorPayload { message: string }

/** Raw map point as returned by backend (empresa or jazida), with enriched fields. */
interface SseRawPonto {
  lat: number
  lon: number
  tipo?: string
  // ── empresa fields ────────────────────────────────────────────
  cnpj_basico?: string
  nome?: string
  cnae?: string
  razao_social?: string
  nome_fantasia?: string
  cnpj_completo?: string
  municipio?: string
  uf?: string | string[]
  telefone?: string
  email?: string
  endereco?: string
  porte?: string
  capital_social?: number
  cnae_descricao?: string
  situacao?: string
  distancia_km?: number
  // ── jazida fields ─────────────────────────────────────────────
  processo?: string
  substancia?: string
  substancias?: string[]
  municipios?: string[]
  titulares?: string[]
  cnpj_titulares?: string[]
  fase?: string
  area_ha?: number
  tipo_requerimento?: string
  ativo?: boolean
  // ── ocorrencia_mineral (CPRM) fields ──────────────────────────
  importancia?: string
  status_economico?: string
  descricao?: string
  projeto?: string
  provincia?: string
}

interface SseMapDataPayload {
  tool: string
  tipo: 'empresa' | 'jazida'
  pontos: SseRawPonto[]
}

interface SseRouteDataPayload extends RouteLine {}

/** Extrai pins dos endpoints da rota — mesmo payload SSE que desenha a polilinha. */
function addEndpointPinsFromRoutePayload(
  route: SseRouteDataPayload,
  lazyClearFlags: { pin: boolean },
  clearAddressPins: () => void,
  addAddressPin: (pin: AddressPin) => void,
): void {
  const pushEp = (ep: Record<string, unknown> | null | undefined, fallbackLabel: string) => {
    if (!ep || ep.lat == null || ep.lon == null) return
    const lat = Number(ep.lat)
    const lon = Number(ep.lon)
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return

    const pin: AddressPin = {
      id: crypto.randomUUID(),
      lat,
      lon,
      label: String(ep.endereco_resolvido ?? ep.endereco_consultado ?? fallbackLabel),
      endereco_consultado:
        typeof ep.endereco_consultado === 'string' ? ep.endereco_consultado : null,
      endereco_resolvido:
        typeof ep.endereco_resolvido === 'string' ? ep.endereco_resolvido : null,
      fonte: typeof ep.fonte === 'string' ? ep.fonte : undefined,
    }
    const det = ep.detalhes
    if (det && typeof det === 'object' && !Array.isArray(det)) {
      pin.detalhes = det as AddressPin['detalhes']
    }
    addAddressPin(pin)
  }

  if (lazyClearFlags.pin) {
    clearAddressPins()
    lazyClearFlags.pin = false
  }

  pushEp(route.origin as Record<string, unknown>, 'Origem')
  pushEp(route.destination as Record<string, unknown>, 'Destino')
}

interface SseIsochroneDataPayload {
  feature: GeoJSONFeature
}

interface SsePinDataPayload extends AddressPin {}

interface SseContextGeometryPayload {
  refs: ContextGeometryRef[]
}


// ── SSE frame parser ──────────────────────────────────────────────────────────

interface SseFrame { event: string; data: string }

function parseSseBuffer(buffer: string): { frames: SseFrame[]; remaining: string } {
  const normalised = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const parts = normalised.split('\n\n')
  const remaining = parts.pop() ?? ''
  const frames: SseFrame[] = []

  for (const block of parts) {
    let event = ''
    const dataParts: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7).trim()
      else if (line.startsWith('data: ')) dataParts.push(line.slice(6).trim())
    }
    const data = dataParts.join('\n')
    if (event && data) frames.push({ event, data })
  }

  return { frames, remaining }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const ROUTE_LABELS: Record<string, string> = {
  mineral: 'Mineração',
  empresa: 'Empresas',
  hybrid:  'Mineração + Empresas',
  geo:     'Geolocalização',
  general: 'Geral',
}

function toolDisplayName(raw: string): string {
  return raw.replace(/_/g, ' ')
}

const TOOL_LABEL_MAP: Record<string, string> = {
  buscar_jazidas: 'Buscando jazidas ANM',
  buscar_fornecedores: 'Buscando fornecedores',
  buscar_empresas: 'Resolvendo CNAEs',
  buscar_municipio: 'Resolvendo município',
  municipio_por_coordenada: 'Resolvendo município',
  obter_poligono: 'Obtendo polígono',
  municipios_em_raio: 'Buscando municípios',
  calcular_rota: 'Calculando distâncias',
  calcular_isocrona: 'Calculando isócrona',
  geocodificar: 'Resolvendo coordenadas',
  plotar_endereco: 'Plotando endereço no mapa',
  buscar_ferrovia: 'Buscando malha ferroviária',
  ferrovias_proximas: 'Ferrovias próximas',
  obter_geometria_ferrovia: 'Geometria ferroviária',
  obter_poligono_porto: 'Polígono do porto',
  detalhes_processo: 'Consultando processo ANM',
  geoquimica_detalhes_amostra: 'Detalhes da amostra geoquímica',
  detalhes_empresa: 'Consultando empresa',
  buscar_por_socio: 'Buscando por sócio',
  jazidas_por_poligono: 'Buscando jazidas por polígono',
}

function toolLabel(name: string): string {
  return TOOL_LABEL_MAP[name] ?? `Buscando ${toolDisplayName(name)}`
}

/**
 * Build the single-line thinking text concatenating steps with " · ".
 * De-duplicates repeated tool names so "buscar_fornecedores" called twice
 * only shows once.
 */
function buildThinkingText(tools: ToolStep[], prefix?: string): string {
  const parts: string[] = []
  if (prefix) parts.push(prefix)
  const seen = new Set<string>()
  for (const t of tools) {
    if (seen.has(t.name)) continue
    seen.add(t.name)
    parts.push(toolLabel(t.name))
  }
  if (parts.length === 0) return 'Analisando contexto do projeto…'
  return parts.join(' · ') + '…'
}

// ── Topic label derivation ───────────────────────────────────────────────────

// Strip verbs / subject words at the start (applied repeatedly)
const STRIP_PREFIX = /^(?:buscar?|encontrar?|me\s+(?:d[eê]|mostre|retorne|traga)|quero|preciso\s+de?|listar?|mostrar?|fornecedor(?:es)?\s+de?|empresa(?:s)?\s+de?|jazida(?:s)?\s+de?|material\s+de\s+constru[çc][aã]o\s+de?)\s+/i
// Strip location context at the end
const STRIP_LOCATION = /\s+(?:em|n[ao]s?|para|perto\s+de|pr[oó]xim[ao]s?\s+(?:ao?|de)?|dentro\s+de|num\s+raio\s+de)\s+.*/i

/** Normalize a raw phrase into a clean short label (≤ 3 words) */
function cleanLabel(phrase: string): string {
  let cleaned = phrase.trim()
  for (let i = 0; i < 3; i++) {
    const next = cleaned.replace(STRIP_PREFIX, '')
    if (next === cleaned) break
    cleaned = next
  }
  const words = cleaned.split(/\s+/).slice(0, 3).join(' ')
    .replace(/\s+(?:e|ou|de|da|do)\s*$/i, '')
  if (!words) return ''
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/**
 * Derive multiple topic labels by splitting on conjunction "e" / ",".
 * "areia lavada e cimento e pre moldados" → ["Areia lavada", "Cimento", "Pre moldados"]
 * "cimento"                               → ["Cimento"]
 */
function deriveSubTopicLabels(text: string): string[] {
  const noLoc = text.trim().replace(STRIP_LOCATION, '')
  // Strip outer prefixes before splitting (e.g. "fornecedores de areia e cimento" → "areia e cimento")
  let stripped = noLoc
  for (let i = 0; i < 3; i++) {
    const next = stripped.replace(STRIP_PREFIX, '')
    if (next === stripped) break
    stripped = next
  }
  // Split on " e " or ", "
  const parts = stripped.split(/\s+e\s+|,\s*/i).map(cleanLabel).filter(Boolean)
  return parts.length > 0 ? parts : [cleanLabel(noLoc) || 'Busca']
}

function isContextGeometryRef(x: unknown): x is ContextGeometryRef {
  if (!x || typeof x !== 'object') return false
  const o = x as Record<string, unknown>
  if (o.kind === 'ferrovia') return typeof o.ferrovia_id === 'string' && o.ferrovia_id.length > 0
  if (o.kind === 'porto') return typeof o.codigo === 'string' || typeof o.nome === 'string'
  return false
}

function flushPendingContextGeometry(
  pendingRefs: MutableRefObject<ContextGeometryRef[]>,
  addChatGeometries: (features: GeoJSONFeature[]) => void,
) {
  const refs = pendingRefs.current
  if (!refs.length) return
  pendingRefs.current = []
  void fetchContextGeometryRefs(refs).then((features) => {
    if (features.length) addChatGeometries(features)
  })
}

function applyContextGeometryRefs(
  refs: ContextGeometryRef[],
  lazyClearFlags: { marker: boolean },
  pendingRefs: MutableRefObject<ContextGeometryRef[]>,
  addChatGeometries: (features: GeoJSONFeature[]) => void,
) {
  if (!refs.length) return
  if (lazyClearFlags.marker) {
    pendingRefs.current.push(...refs)
    return
  }
  void fetchContextGeometryRefs(refs).then((features) => {
    if (features.length) addChatGeometries(features)
  })
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useChatStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)

  const sessionIdRef = useRef<string | null>(null)
  const abortRef     = useRef<AbortController | null>(null)
  /** Refs porto/ferrovia antes do primeiro ``map_data`` (lazy clear); GeoJSON via GET. */
  const pendingChatGeometryRefsRef = useRef<ContextGeometryRef[]>([])
  const queryClient  = useQueryClient()

  const { projetoId, analiseId } = useChatStore()
  const setMapTopics             = useUiStore((s) => s.setMapTopics)
  const setAllTopicMarkers       = useMapStore((s) => s.setAllTopicMarkers)
  const setTopicFilter           = useMapStore((s) => s.setTopicFilter)
  const clearMarkers             = useMapStore((s) => s.clearMarkers)
  const clearPolygons            = useMapStore((s) => s.clearPolygons)
  const setRouteLines            = useMapStore((s) => s.setRouteLines)
  const addRouteLine             = useMapStore((s) => s.addRouteLine)
  const setIsochroneFeature      = useMapStore((s) => s.setIsochroneFeature)
  const clearRouteAndIsochrone   = useMapStore((s) => s.clearRouteAndIsochrone)
  const addAddressPin            = useMapStore((s) => s.addAddressPin)
  const clearAddressPins         = useMapStore((s) => s.clearAddressPins)
  const addChatGeometries        = useMapStore((s) => s.addChatGeometries)

  const patchMessage = useCallback(
    (id: string, patch: Partial<ChatMessage> | ((m: ChatMessage) => ChatMessage)) => {
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id !== id) return m
          return typeof patch === 'function' ? patch(m) : { ...m, ...patch }
        }),
      )
    },
    [],
  )

  const sendMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || isStreaming) return

      abortRef.current?.abort()
      abortRef.current = new AbortController()

      // Reset active topic filter so first chip click always activates
      setTopicFilter(null)

      const userMsgId      = crypto.randomUUID()
      const thinkingMsgId  = crypto.randomUUID()
      const assistantMsgId = crypto.randomUUID()

      setMessages((prev) => [
        ...prev,
        { id: userMsgId, role: 'user', content: text },
      ])

      setIsStreaming(true)
      // NOTE: do NOT clear markers/polygons/routes upfront here.
      // Clearing is now decided in the `meta` SSE handler based on the
      // classified route, so geo follow-ups (calcular_rota, calcular_isocrona,
      // plotar_endereco) preserve the jazida/empresa markers and polygons
      // already on the map. Search routes (mineral/empresa/hybrid) still
      // do a full clear before re-populating.
      let assistantStarted = false

      // Derive topic labels: "areia lavada e cimento e pre moldados" → 3 labels
      const subTopicLabels = deriveSubTopicLabels(text)
      const topicLabel     = subTopicLabels[0]
      // Per-topic buffer: filled during stream, flushed once on done
      const topicBuffers = new Map<string, { empresa: MapaPontoEmpresa[], jazida: MapaPonto[] }>()
      const topicOrder: string[] = []
      const mapDataIndex: { current: number } = { current: 0 }
      // Lazy-clear flags: on first route_data / pin_data / map_data of THIS
      // turn we wipe the previous layer before adding the new one — but only
      // if the new event actually arrives. Multiple events of the same type
      // within the same turn accumulate (e.g. comparing 4 ports → 4 routes).
      // NOTE: `marker` flag defers clearMarkers()+clearPolygons() to the
      // first map_data event so that a follow-up geo request (e.g. "me traz
      // a rota") classified as 'empresa' by context inheritance does NOT
      // wipe company pins when no new search is actually performed.
      const lazyClearFlags = { route: true, pin: true, marker: false }
      pendingChatGeometryRefsRef.current = []

      try {
        const { getAccessToken } = await import('@/auth/getAccessToken')
        const accessToken = await getAccessToken()
        if (!accessToken) {
          throw new Error('Não autenticado — faça login novamente.')
        }

        const response = await fetch(`${API_URL}/chat/stream`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${accessToken}`,
          },
          body: JSON.stringify({
            message: text,
            session_id: sessionIdRef.current,
            user_id: 'dev-user',
            projeto_id: projetoId ?? undefined,
            analise_id: analiseId ?? undefined,
          }),
          signal: abortRef.current.signal,
        })

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        setMessages((prev) => [
          ...prev,
          {
            id: thinkingMsgId,
            role: 'thinking',
            content: 'Analisando contexto do projeto…',
            tools: [],
            isStreaming: true,
          },
        ])

        const reader  = response.body!.getReader()
        const decoder = new TextDecoder()
        let rawBuffer = ''
        const streamFlags = { sawDone: false, sawError: false }

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          rawBuffer += decoder.decode(value, { stream: true })
          const { frames, remaining } = parseSseBuffer(rawBuffer)
          rawBuffer = remaining

          for (const { event, data } of frames) {
            try {
              const payload = JSON.parse(data)
              handleSseEvent({
                event,
                payload,
                thinkingMsgId,
                assistantMsgId,
                assistantStarted,
                sessionIdRef,
                patchMessage,
                setMessages,
                topicLabel,
                subTopicLabels,
                mapDataIndex,
                topicBuffers,
                topicOrder,
                setAllTopicMarkers,
                setRouteLines,
                addRouteLine,
                setIsochroneFeature,
                addAddressPin,
                clearMarkers,
                clearPolygons,
                clearRouteAndIsochrone,
                clearAddressPins,
                addChatGeometries,
                pendingChatGeometryRefsRef,
                lazyClearFlags,
                streamFlags,
                onAssistantStarted: () => { assistantStarted = true },
                onAnaliseUpdated: (aid) => {
                  queryClient.invalidateQueries({ queryKey: ['analises', 'detail', aid] })
                  queryClient.invalidateQueries({ queryKey: ['analises'] })
                },
              })
            } catch (err) {
              console.warn(`[sse] Failed to parse ${event} frame:`, err, data?.slice(0, 200))
            }
          }
        }

        if (!streamFlags.sawDone && !streamFlags.sawError) {
          setMessages((prev) => {
            const hasAssistant = prev.some((m) => m.id === assistantMsgId)
            if (hasAssistant) {
              return prev
                .filter((m) => m.id !== thinkingMsgId)
                .map((m) =>
                  m.id === assistantMsgId && m.isStreaming
                    ? {
                        ...m,
                        isStreaming: false,
                        content:
                          (m.content?.trim() ?? '').length > 0
                            ? `${m.content}\n\n_(Resposta interrompida.)_`
                            : 'A resposta não foi concluída. Tente novamente.',
                      }
                    : m,
                )
            }
            if (prev.some((m) => m.id === thinkingMsgId)) {
              return [
                ...prev.filter((m) => m.id !== thinkingMsgId),
                {
                  id: assistantMsgId,
                  role: 'assistant',
                  content:
                    'A ligação encerrou sem confirmação final do servidor. Verifique o backend e tente novamente.',
                  isStreaming: false,
                },
              ]
            }
            return prev
          })
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') return

        setMessages((prev) => [
          ...prev.filter((m) => m.id !== thinkingMsgId),
          {
            id: crypto.randomUUID(),
            role: 'assistant',
            content: 'Não foi possível conectar ao servidor. Tente novamente.',
            isStreaming: false,
          },
        ])
      } finally {
        setIsStreaming(false)
      }
    },
    [isStreaming, projetoId, analiseId, setAllTopicMarkers, setTopicFilter, clearMarkers, clearPolygons, addRouteLine, setIsochroneFeature, clearRouteAndIsochrone, addAddressPin, clearAddressPins, addChatGeometries, patchMessage, queryClient],
  )

  const clearConversation = useCallback(() => {
    abortRef.current?.abort()
    setMessages([])
    setIsStreaming(false)
    sessionIdRef.current = null
    setMapTopics([])
    clearMarkers()
    clearPolygons()
    clearRouteAndIsochrone()
    clearAddressPins()
  }, [setMapTopics, clearMarkers, clearPolygons, clearRouteAndIsochrone, clearAddressPins])

  return {
    messages,
    isStreaming,
    sessionId: sessionIdRef.current,
    sendMessage,
    clearConversation,
    ROUTE_LABELS,
  }
}

// ── SSE event handler ────────────────────────────────────────────────────────

interface HandleSseEventArgs {
  event: string
  payload: unknown
  thinkingMsgId: string
  assistantMsgId: string
  assistantStarted: boolean
  sessionIdRef: React.MutableRefObject<string | null>
  topicLabel: string
  subTopicLabels: string[]
  /** Mutable counter — incremented on each map_data to assign the next label */
  mapDataIndex: { current: number }
  /** Mutable per-topic marker buffers — filled during stream, flushed on done */
  topicBuffers: Map<string, { empresa: MapaPontoEmpresa[], jazida: MapaPonto[] }>
  /** Mutable ordered list of topic labels (insertion order) */
  topicOrder: string[]
  patchMessage: (id: string, patch: Partial<ChatMessage> | ((m: ChatMessage) => ChatMessage)) => void
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>
  setAllTopicMarkers: (topics: Record<string, { empresa: MapaPontoEmpresa[], jazida: MapaPonto[] }>, orderedLabels: string[]) => void
  setRouteLines: (routes: RouteLine[]) => void
  addRouteLine: (route: RouteLine) => void
  setIsochroneFeature: (feature: GeoJSONFeature | null) => void
  addAddressPin: (pin: AddressPin) => void
  clearMarkers: () => void
  clearPolygons: () => void
  clearRouteAndIsochrone: () => void
  clearAddressPins: () => void
  addChatGeometries: (features: GeoJSONFeature[]) => void
  /** Refs porto/ferrovia antes do primeiro ``map_data`` (turno com busca). */
  pendingChatGeometryRefsRef: MutableRefObject<ContextGeometryRef[]>
  /** Tracks whether the next route_data / pin_data of this turn should
   *  trigger a one-time wipe of previous routes / pins before adding. */
  lazyClearFlags: { route: boolean; pin: boolean; marker: boolean }
  streamFlags: { sawDone: boolean; sawError: boolean }
  onAssistantStarted: () => void
  onAnaliseUpdated?: (analiseId: string) => void
}

function handleSseEvent({
  event,
  payload,
  thinkingMsgId,
  assistantMsgId,
  assistantStarted,
  sessionIdRef,
  topicLabel,
  subTopicLabels,
  mapDataIndex,
  topicBuffers,
  topicOrder,
  patchMessage,
  setMessages,
  setAllTopicMarkers,
  setRouteLines,
  addRouteLine,
  setIsochroneFeature,
  addAddressPin,
  clearMarkers,
  clearPolygons,
  clearRouteAndIsochrone,
  clearAddressPins,
  addChatGeometries,
  pendingChatGeometryRefsRef,
  lazyClearFlags,
  streamFlags,
  onAssistantStarted,
  onAnaliseUpdated,
}: HandleSseEventArgs): void {

  if (event === 'meta') {
    const meta = payload as SseMetaPayload
    if (meta.session_id) sessionIdRef.current = meta.session_id
    if (meta.route) {
      // Conditional cleanup based on the classified route:
      // - search routes (mineral/empresa/hybrid) → full clear, the new
      //   buscar_* tools will repopulate markers and polygons.
      // - 'geo' → preserve EVERYTHING. Routes / isochrones / pins are
      //   replaced lazily only when a NEW route_data / pin_data SSE arrives
      //   in this same turn (see `firstRouteOfTurn` / `firstPinOfTurn`).
      //   This way, follow-ups like "trajeto detalhado da rota anterior"
      //   that don't trigger a new tool call keep the existing layers.
      // - 'general' → no clear (text-only response).
      if (meta.route === 'mineral' || meta.route === 'empresa' || meta.route === 'hybrid') {
        // Defer marker/polygon clear to the first map_data event so that
        // follow-up geo requests classified as 'empresa' by context inheritance
        // don't wipe existing pins when no new search is actually performed.
        lazyClearFlags.marker = true
        // For a genuine new search, clear routes/isochrone and address pins
        // right away (they'll be replaced by the new search results).
        clearRouteAndIsochrone()
        clearAddressPins()
      } else if (meta.route === 'geo') {
        // For geo requests (routes, plotar_endereco): NEVER auto-clear address
        // pins — the user wants jazida/porto pins to ACCUMULATE alongside the
        // new route. Only the route lines themselves are replaced lazily on
        // the first route_data event (lazyClearFlags.route).
        lazyClearFlags.pin = false
      }

      patchMessage(thinkingMsgId, (m) => ({
        ...m,
        route: meta.route,
        routeReasoning: meta.route_reasoning,
        content: buildThinkingText(m.tools ?? [], 'Analisando contexto do projeto'),
      }))
    }
    return
  }

  if (event === 'tool_start') {
    const { name } = payload as SseToolPayload
    patchMessage(thinkingMsgId, (m) => {
      const updatedTools = [...(m.tools ?? []), { name, displayName: toolDisplayName(name), status: 'running' as const }]
      return {
        ...m,
        tools: updatedTools,
        content: buildThinkingText(updatedTools),
      }
    })
    return
  }

  if (event === 'tool_end') {
    const { name } = payload as SseToolPayload
    patchMessage(thinkingMsgId, (m) => {
      const updatedTools = (m.tools ?? []).map((t) =>
        t.name === name ? { ...t, status: 'done' as const } : t,
      )
      return {
        ...m,
        tools: updatedTools,
        content: buildThinkingText(updatedTools),
      }
    })
    return
  }

  if (event === 'map_data') {
    const { tipo, pontos } = payload as SseMapDataPayload

    // First map_data of this turn → now it is safe to clear previous markers
    // and polygons (a real new search is underway and will repopulate them).
    if (lazyClearFlags.marker) {
      clearMarkers()
      clearPolygons()
      lazyClearFlags.marker = false
    }
    flushPendingContextGeometry(pendingChatGeometryRefsRef, addChatGeometries)

    // Each map_data event = one product's results → assign the next sub-topic label
    const label = subTopicLabels[mapDataIndex.current] ?? topicLabel
    mapDataIndex.current++

    // Get or create the buffer for this topic
    if (!topicBuffers.has(label)) {
      topicBuffers.set(label, { empresa: [], jazida: [] })
      topicOrder.push(label)
    }
    const buf = topicBuffers.get(label)!

    if (tipo === 'empresa') {
      const markers: MapaPontoEmpresa[] = pontos
        .filter((p) => p.lat && p.lon)
        .map((p) => ({
          lat: p.lat,
          lon: p.lon,
          label: p.nome_fantasia ?? p.razao_social ?? p.nome ?? p.cnpj_basico ?? '',
          cnpj_basico: p.cnpj_basico ?? '',
          razao_social: p.razao_social,
          nome_fantasia: p.nome_fantasia,
          cnpj_completo: p.cnpj_completo,
          municipio: p.municipio,
          uf: typeof p.uf === 'string' ? p.uf : undefined,
          telefone: p.telefone,
          email: p.email,
          endereco: p.endereco,
          porte: p.porte,
          capital_social: p.capital_social,
          cnae_descricao: p.cnae_descricao,
          situacao: p.situacao,
          distancia_km: p.distancia_km,
        }))
      buf.empresa.push(...markers)
    } else {
      const markers: MapaPonto[] = pontos
        .filter((p) => p.lat && p.lon)
        .map((p) => {
          const isGeoquimica = p.tipo === 'geoquimica'
          const amostraId = String(p.id ?? p.label ?? p.processo ?? '').trim()
          return {
            lat: p.lat,
            lon: p.lon,
            tipo: typeof p.tipo === 'string' ? p.tipo : undefined,
            label: isGeoquimica
              ? amostraId
              : (p.substancias?.[0] ?? p.substancia) ?? p.processo ?? '',
            id: isGeoquimica ? amostraId : (p.processo ?? crypto.randomUUID()),
            projeto: typeof p.projeto === 'string' ? p.projeto : undefined,
            substancia: typeof p.substancia === 'string' ? p.substancia : undefined,
            analitos: Array.isArray(p.analitos)
              ? p.analitos
              : Array.isArray(p.substancias)
                ? p.substancias
                : undefined,
            substancias: p.substancias,
            municipios: p.municipios,
            uf: Array.isArray(p.uf) ? p.uf : (p.uf ? [p.uf as string] : undefined),
            titulares: p.titulares,
            cnpj_titulares: p.cnpj_titulares,
            fase: p.fase,
            area_ha: p.area_ha,
            distancia_km: p.distancia_km,
            tipo_requerimento: p.tipo_requerimento,
            endereco: p.endereco,
            telefone: p.telefone,
            email: p.email,
            nome: typeof p.nome === 'string' ? p.nome : undefined,
            importancia: p.importancia,
            status_economico: p.status_economico,
            descricao: p.descricao,
            provincia: p.provincia,
            polygonFetch:
              p.tipo === 'ocorrencia_mineral'
                ? 'cprm' as const
                : p.tipo === 'car'
                  ? 'car' as const
                  : p.tipo === 'afloramento' || p.tipo === 'geoquimica'
                    ? 'none' as const
                    : 'anm' as const,
            status: typeof p.status === 'string' ? p.status : undefined,
          }
        })
      buf.jazida.push(...markers)
    }
    return
  }

  if (event === 'route_data') {
    const route = payload as SseRouteDataPayload
    if (route?.points?.length) {
      // First route of this turn → wipe stale routes from previous turns
      // before adding (but keep isochrones and pins intact). Subsequent
      // route_data events in the same turn are additive (e.g. comparing
      // routes to multiple ports).
      if (lazyClearFlags.route) {
        setRouteLines([])
        lazyClearFlags.route = false
      }
      addRouteLine(route)
      // Pins nos mesmos endpoints da rota (robusto se pin_data SSE falhar ou
      // MapLibre só aplicar marcadores após style.load).
      addEndpointPinsFromRoutePayload(route, lazyClearFlags, clearAddressPins, addAddressPin)
    }
    return
  }

  if (event === 'isochrone_data') {
    const { feature } = payload as SseIsochroneDataPayload
    if (feature) {
      setIsochroneFeature(feature)
    }
    return
  }

  if (event === 'pin_data') {
    const pin = payload as SsePinDataPayload
    if (pin?.lat != null && pin?.lon != null) {
      // First pin of this turn → wipe pins from previous turns. Multiple
      // plotar_endereco calls in the same turn (e.g. "plote os portos de
      // Aratu, Salvador e Ilhéus") accumulate.
      if (lazyClearFlags.pin) {
        clearAddressPins()
        lazyClearFlags.pin = false
      }
      addAddressPin(pin)
    }
    return
  }

  if (event === 'context_geometry') {
    const raw = payload as SseContextGeometryPayload
    const refs = (raw?.refs ?? []).filter(isContextGeometryRef)
    applyContextGeometryRefs(refs, lazyClearFlags, pendingChatGeometryRefsRef, addChatGeometries)
    return
  }

  // Jazida: GET /geo/jazida/poligono. Portos/ferrovias: refs no SSE + GET sob demanda.

  if (event === 'token') {
    const { text: chunk } = payload as SseTokenPayload
    if (!chunk) return

    if (!assistantStarted) {
      onAssistantStarted()
      setMessages((prev) => {
        const withoutThinking = prev.filter((m) => m.id !== thinkingMsgId)
        const exists = withoutThinking.some((m) => m.id === assistantMsgId)
        if (exists) {
          return withoutThinking.map((m) =>
            m.id === assistantMsgId ? { ...m, content: m.content + chunk } : m,
          )
        }
        return [
          ...withoutThinking,
          { id: assistantMsgId, role: 'assistant', content: chunk, isStreaming: true },
        ]
      })
    } else {
      patchMessage(assistantMsgId, (m) => ({ ...m, content: m.content + chunk }))
    }
    return
  }

  if (event === 'done') {
    streamFlags.sawDone = true
    const done = payload as SseDonePayload
    sessionIdRef.current = done.session_id

    flushPendingContextGeometry(pendingChatGeometryRefsRef, addChatGeometries)

    if (assistantStarted) {
      patchMessage(assistantMsgId, (m) => {
        const cur = m.content ?? ''
        const doneText = done.response ?? ''
        // Servidor pode enviar texto completo no ``done`` quando o stream de
        // tokens perdeu parte da resposta (ver chat.py chain_end sync).
        const content =
          doneText.trim().length > cur.trim().length ? doneText : cur
        return { ...m, content, isStreaming: false, route: done.route }
      })
      setMessages((prev) => prev.filter((m) => m.id !== thinkingMsgId))
    } else {
      setMessages((prev) => {
        const base = prev.filter((m) => m.id !== thinkingMsgId)
        const exists = base.some((m) => m.id === assistantMsgId)
        if (exists) {
          return base.map((m) =>
            m.id === assistantMsgId
              ? {
                  ...m,
                  content: done.response,
                  isStreaming: false,
                  route: done.route,
                }
              : m,
          )
        }
        return [
          ...base,
          {
            id: assistantMsgId,
            role: 'assistant' as const,
            content: done.response,
            isStreaming: false,
            route: done.route,
          },
        ]
      })
    }

    // Flush all buffered markers to the store in a single update (one render → one fitBounds)
    if (topicBuffers.size > 0) {
      const record: Record<string, { empresa: MapaPontoEmpresa[], jazida: MapaPonto[] }> = {}
      for (const [label, buf] of topicBuffers) {
        record[label] = buf
      }
      setAllTopicMarkers(record, topicOrder)
    }
    return
  }

  if (event === 'analise_updated') {
    const { analise_id } = payload as { analise_id: string }
    if (analise_id && onAnaliseUpdated) {
      onAnaliseUpdated(analise_id)
    }
    return
  }

  if (event === 'error') {
    streamFlags.sawError = true
    const { message: errMsg } = payload as SseErrorPayload
    setMessages((prev) => {
      const base = prev.filter((m) => m.id !== thinkingMsgId)
      const exists = base.some((m) => m.id === assistantMsgId)
      if (exists) {
        return base.map((m) =>
          m.id === assistantMsgId
            ? {
                ...m,
                isStreaming: false,
                content:
                  (m.content?.trim() ?? '').length > 0
                    ? `${m.content}\n\n**Erro:** ${errMsg}`
                    : `**Erro:** ${errMsg}`,
              }
            : m,
        )
      }
      return [
        ...base,
        {
          id: assistantMsgId,
          role: 'assistant',
          content: `**Erro:** ${errMsg}`,
          isStreaming: false,
        },
      ]
    })
    // Flush any markers that arrived before the error
    if (topicBuffers.size > 0) {
      const record: Record<string, { empresa: MapaPontoEmpresa[], jazida: MapaPonto[] }> = {}
      for (const [label, buf] of topicBuffers) {
        record[label] = buf
      }
      setAllTopicMarkers(record, topicOrder)
    }
  }
}
