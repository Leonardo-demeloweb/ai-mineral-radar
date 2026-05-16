import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import {
  Send, Sparkles, User, Loader2, MapPin, ChevronDown, Star,
} from 'lucide-react'

import { Button, Input } from '@/components/ui'
import { type ChatMessage, useChatStream } from '@/hooks/useChatStream'
import { useAddFornecedor, useRemoveFornecedor } from '@/hooks/useAnalises'
import { useChatStore } from '@/stores/chatStore'
import { useFavoritesStore } from '@/stores/favoritesStore'
import { useUiStore } from '@/stores/uiStore'
import { useMapStore } from '@/stores/mapStore'

export function ChatShell() {
  const { messages, isStreaming, sendMessage, clearConversation } = useChatStream()

  const [inputValue, setInputValue] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  // Keep latest sendMessage/isStreaming in a ref so the global listener
  // doesn't re-attach on every render.
  const sendRef = useRef(sendMessage)
  sendRef.current = sendMessage
  const streamingRef = useRef(isStreaming)
  streamingRef.current = isStreaming

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  /**
   * Global listener for the `chat:send` CustomEvent dispatched by the map
   * popup buttons (route-calc icon on jazida/empresa/pin popups). Lets the
   * map ask the agent to compute a route to a given point without coupling
   * the map component to this hook.
   */
  useEffect(() => {
    function onChatSend(e: Event) {
      const detail = (e as CustomEvent<{ text?: string }>).detail
      const text = detail?.text?.trim()
      if (!text) return
      if (streamingRef.current) return
      void sendRef.current(text)
    }
    window.addEventListener('chat:send', onChatSend)
    return () => window.removeEventListener('chat:send', onChatSend)
  }, [])

  const handleSubmit = () => {
    const text = inputValue.trim()
    if (!text || isStreaming) return
    setInputValue('')
    void sendMessage(text)
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 w-full max-w-full flex-col overflow-hidden bg-(--color-surface)">
      {/* Header — relative so "Limpar" can be anchored under the close button */}
      <div className="relative flex min-w-0 shrink-0 items-center gap-2.5 border-b border-(--color-border) px-4 py-3 pr-9">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-green-600">
          <Sparkles size={13} className="text-white" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-(--color-text)">MineralRadar IA</p>
          <p className="text-xs text-(--color-text-muted)">Assistente de inteligência mineral</p>
        </div>
        {/* Anchored to the right, visually below the absolute close button */}
        {messages.length > 0 && (
          <button
            onClick={clearConversation}
            className="absolute bottom-2.5 right-3 text-xs text-(--color-text-muted) hover:text-(--color-text) transition-colors"
          >
            Limpar
          </button>
        )}
      </div>

      {/* Messages */}
      <div className="min-h-0 min-w-0 flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-4 py-4">
        {messages.length === 0 && <EmptyState />}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="shrink-0 border-t border-(--color-border) px-3 py-3 min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <Input
            placeholder="Pergunte sobre jazidas, processos ANM, empresas ou o mapa…"
            className="flex-1 text-sm"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isStreaming}
          />
          <Button
            size="sm"
            className="shrink-0"
            onClick={handleSubmit}
            disabled={!inputValue.trim() || isStreaming}
          >
            {isStreaming ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Send size={14} />
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 py-12 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-green-100 dark:bg-green-950">
        <Sparkles size={18} className="text-green-600" />
      </div>
      <div>
        <p className="text-sm font-medium text-(--color-text)">Como posso ajudar?</p>
        <p className="text-xs text-(--color-text-muted) mt-0.5">
          Titulares, fornecedores, rotas a partir do pino do projeto, mercado mineral ou due diligence em cadeia.
        </p>
      </div>
    </div>
  )
}

// ── Message bubbles ───────────────────────────────────────────────────────────

interface MessageBubbleProps {
  message: ChatMessage
}

function MessageBubble({ message }: MessageBubbleProps) {
  if (message.role === 'thinking') {
    return <ThinkingBubble message={message} />
  }

  if (message.role === 'user') {
    return (
      <div className="flex items-start gap-2.5 justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-green-600 px-3.5 py-2.5">
          <p className="text-sm text-white leading-relaxed">{message.content}</p>
        </div>
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-zinc-200 dark:bg-zinc-700 mt-0.5">
          <User size={12} className="text-(--color-text-muted)" />
        </div>
      </div>
    )
  }

  // assistant
  return <AssistantBubble message={message} />
}

// ── Assistant bubble with structured rendering ────────────────────────────────
// Each content block (text summary OR result-group section) gets its own
// avatar row, exactly matching the mock layout.

function AssistantBubble({ message }: { message: ChatMessage }) {
  const blocks = useMemo(() => parseBlocks(message.content), [message.content])
  const setMapTopics = useUiStore((s) => s.setMapTopics)

  // Topic chips: only from result-groups that have a real subject heading
  const topicChips = useMemo(() => {
    if (message.isStreaming) return []
    return blocks
      .filter((b): b is Extract<ContentBlock, { type: 'result-group' }> =>
        b.type === 'result-group' && b.heading.length > 0 && looksLikeHeading(b.heading),
      )
      .map((b) => {
        const clean = b.heading.replace(/:$/, '')
        return clean.includes('—') ? clean.split('—')[0].trim() : clean.trim()
      })
      .filter(Boolean)
  }, [blocks, message.isStreaming])

  // Promote real topic headings to the map overlay chip strip
  useEffect(() => {
    if (!message.isStreaming && topicChips.length > 0) {
      setMapTopics(topicChips)
    }
  }, [message.isStreaming, topicChips, setMapTopics])

  const firstTextIdx = blocks.findIndex((b) => b.type === 'text')

  return (
    <div className="min-w-0 w-full space-y-3">
      {blocks.map((block, i) => {
        const isLastBlock = i === blocks.length - 1

        return (
          <div key={i} className="flex min-w-0 w-full items-start gap-2.5">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-600 mt-0.5">
              <Sparkles size={11} className="text-white" />
            </div>
            <div className="flex-1 min-w-0">

              {block.type === 'text' && (
                <>
                  <div className="min-w-0 rounded-2xl rounded-tl-sm bg-zinc-100 px-3.5 py-2.5 dark:bg-zinc-800">
                    <p
                      className="break-words text-sm leading-relaxed text-(--color-text)"
                      dangerouslySetInnerHTML={{ __html: renderInline(block.content) }}
                    />
                    {message.isStreaming && isLastBlock && (
                      <span className="inline-block w-1.5 h-3.5 ml-0.5 bg-green-500 animate-pulse rounded-sm align-middle" />
                    )}
                  </div>
                  {/* Topic chips after the first text block */}
                  {i === firstTextIdx && topicChips.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-2 pl-0.5">
                      {topicChips.map((topic) => (
                        <span
                          key={topic}
                          className="inline-flex items-center rounded-full bg-green-50 dark:bg-green-950 px-2.5 py-0.5 text-xs font-medium text-green-700 dark:text-green-300 border border-green-200 dark:border-green-800"
                        >
                          {topic}
                        </span>
                      ))}
                    </div>
                  )}
                </>
              )}

              {block.type === 'result-group' && (
                <div className="min-w-0 space-y-1.5">
                  {block.heading && (
                    <p className="text-sm font-bold text-(--color-text) leading-snug mb-2">
                      {block.heading.replace(/:$/, '')}
                    </p>
                  )}
                  <div className="space-y-1">
                    {block.items.map((item, j) => (
                      <ResultCard key={j} item={item} />
                    ))}
                  </div>
                  {message.isStreaming && isLastBlock && (
                    <span className="inline-block w-1.5 h-3.5 bg-green-500 animate-pulse rounded-sm" />
                  )}
                </div>
              )}

            </div>
          </div>
        )
      })}
    </div>
  )
}

/** Strips non-digit characters so CNPJ/Processo IDs match regardless of formatting. */
function normalizeId(raw: string): string {
  return raw.replace(/\D/g, '')
}

/** CPRM / GeoBank sample id — keep hyphens (ex. 1182-LK-R-0039B). */
function normalizeAmostraId(raw: string): string {
  return raw.trim().toUpperCase().replace(/\s+/g, '')
}

/** Id no título do card: "4212-PD-R-0037F (Vazante-Paracatu)" → 4212-PD-R-0037F */
function extractAmostraIdFromTitle(title: string): string | null {
  const head = title.split('(')[0].trim()
  if (!head || !/\d{3,}/.test(head)) return null
  if (/^(Processo|CNPJ)\b/i.test(head)) return null
  return normalizeAmostraId(head)
}

/** Look up lat/lon and topic label for a given normalised ID from the current map markers. */
function findMarkerInfo(
  normId: string,
  amostraId?: string | null,
): { localizacao: { lat: number; lon: number }; topico: string } | null {
  const { markersByTopic } = useMapStore.getState()
  const amostraNorm = amostraId ? normalizeAmostraId(amostraId) : null
  for (const [topicLabel, topic] of Object.entries(markersByTopic)) {
    for (const e of topic.empresa) {
      if (normalizeId(e.cnpj_completo ?? '') === normId) return { localizacao: { lat: e.lat, lon: e.lon }, topico: topicLabel }
    }
    for (const j of topic.jazida) {
      if (normalizeId(j.id) === normId) return { localizacao: { lat: j.lat, lon: j.lon }, topico: topicLabel }
      if (amostraNorm && normalizeAmostraId(j.id) === amostraNorm) {
        return { localizacao: { lat: j.lat, lon: j.lon }, topico: topicLabel }
      }
    }
  }
  return null
}

// ── Result card (dark card matching the mock) ─────────────────────────────────

interface ParsedResult {
  title: string
  details: string[]
}

/**
 * Map any fase string to a canonical display status.
 * Handles both clean ("Ativa") and raw ANM values ("Requerimento de Lavra").
 */
function mapFase(raw: string): string {
  const f = raw.toLowerCase()
  if (f === 'ativa' || f.includes('concessão de lavra') || f.includes('lavra garimpeira')) return 'Ativa'
  if (f.includes('lavra')) return 'Ativa'
  if (f === 'em análise' || f.includes('requerimento') || f.includes('pesquisa') || f.includes('autorização')) return 'Em análise'
  if (f === 'disponível' || f.includes('disponib')) return 'Disponível'
  if (f.includes('licen')) return 'Licenciada'
  return raw
}

function statusBadgeClass(status: string): string {
  const s = status.toLowerCase()
  if (s === 'ativa') return 'bg-green-900/50 text-green-400'
  if (s === 'em análise') return 'bg-yellow-900/50 text-yellow-400'
  if (s === 'disponível') return 'bg-zinc-700 text-zinc-300'
  if (s === 'licenciada') return 'bg-green-900/50 text-green-400'
  if (s === 'inativa' || s === 'baixada' || s === 'suspensa') return 'bg-red-900/50 text-red-400'
  return 'bg-zinc-700 text-zinc-300'
}

function stripCnpjSuffixFromTitle(title: string): string {
  return title.replace(/\s*\(\s*CNPJ[\s:]*[^\)]*\)/gi, '').trim()
}

function extractCnpjFromTitle(title: string): string {
  const m = title.match(/\(\s*CNPJ[\s:]*([0-9.\-/]+)\s*\)/i)
  return m?.[1]?.trim() ?? ''
}

function ResultCard({ item }: { item: ParsedResult }) {
  const [open, setOpen] = useState(false)
  const cardRef = useRef<HTMLDivElement>(null)

  const analiseId = useChatStore((s) => s.analiseId)

  const cnpjFromTitle = extractCnpjFromTitle(item.title)
  const cleanTitle = stripCnpjSuffixFromTitle(item.title)

  // Extract key metadata for subtitle
  const processoRaw  = item.details.find((d) => /^Processo\s*:/i.test(d))
  const processo     = processoRaw?.replace(/^Processo\s*:/i, '').trim().split(/\s*\(/)[0].trim() ?? ''
  const faseRaw      = item.details.find((d) => /^Fase\s*:/i.test(d))
  const situacaoRaw  = item.details.find((d) => /^Situação\s*:/i.test(d))
  const atividadeRaw = item.details.find((d) => /^(Atividade|CNAE)\s*:/i.test(d))

  let fase = faseRaw?.replace(/^Fase\s*:/i, '').trim() ?? ''
  if (!fase && processoRaw) {
    const m = processoRaw.match(/\(([^)]+)\)/)
    if (m) fase = m[1].trim()
  }

  // Status from Fase (jazidas) or Situação (empresas)
  let status = ''
  if (fase) {
    status = mapFase(fase)
  } else if (situacaoRaw) {
    status = situacaoRaw.replace(/^Situação\s*:/i, '').trim()
  }

  const atividade = atividadeRaw?.replace(/^(Atividade|CNAE)\s*:/i, '').trim() ?? ''

  const cnpjRaw = item.details.find((d) => /^CNPJ\s*:/i.test(d))
  const cnpj    = cnpjRaw?.replace(/^CNPJ\s*:/i, '').trim() ?? ''
  const cnpjDisplay = cnpj || cnpjFromTitle

  // Normalized digits-only ID: "800.335/1992" → "8003351992", "12.345.678/0001-90" → "12345678000190"
  // Processo takes priority (jazidas); CNPJ is fallback (empresas).
  const amostraId = extractAmostraIdFromTitle(cleanTitle)
  const ownId =
    normalizeId(processo)
    || normalizeId(cnpjDisplay)
    || (amostraId ? normalizeAmostraId(amostraId) : null)
  const isAmostraGeo = !!amostraId && !processo && !cnpjDisplay
  const tipoFonte = processo ? 'anm' : 'cnpj'

  // Favorites
  const isFav     = useFavoritesStore((s) => !!ownId && ownId in s.saved)
  const markSaved = useFavoritesStore((s) => s.markSaved)
  const markRemoved = useFavoritesStore((s) => s.markRemoved)
  const addMut    = useAddFornecedor()
  const removeMut = useRemoveFornecedor()

  const selectFeature = useMapStore((s) => s.selectFeature)
  const isSelected    = useMapStore((s) => !!ownId && s.selectedFeatureId === ownId)

  // Sync open/scroll state with selection
  useEffect(() => {
    if (isSelected) {
      if (hasDetails) setOpen(true)
      cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    } else {
      setOpen(false)
    }
  // hasDetails is stable (derived from item.details which doesn't change)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSelected])

  const subtitleParts = [
    cnpjDisplay,
    processo ? `Processo ${processo}` : '',
    atividade,
    status,
  ].filter(Boolean)
  const subtitle = subtitleParts.join(' · ')

  const hasDetails = item.details.length > 0

  const SUBTITLE_KEYS = /^(Processo|Fase|Situação|Situacao|Atividade|CNAE)\s*:/i
  const expandedDetails = item.details.filter((d) => !SUBTITLE_KEYS.test(d))

  // Extract extra metadata for AddFornecedorInput
  const municipioRaw   = item.details.find((d) => /^Município\s*:/i.test(d))
  const municipio      = municipioRaw?.replace(/^Município\s*:/i, '').trim() || null
  const enderecoRaw    = item.details.find((d) => /^Endereço\s*:/i.test(d))
  const endereco       = enderecoRaw?.replace(/^Endereço\s*:/i, '').trim() || null
  const ufRaw          = item.details.find((d) => /^UF\s*:/i.test(d))
  const uf             = ufRaw?.replace(/^UF\s*:/i, '').trim() || null
  // LLM formats phone as "Contato:" or "Telefone:" depending on context
  const telefoneRaw    = item.details.find((d) => /^(Telefone|Tel\.?|Contato)\s*:/i.test(d))
  const telefone       = telefoneRaw?.replace(/^(Telefone|Tel\.?|Contato)\s*:/i, '').trim() || null
  const emailRaw       = item.details.find((d) => /^E?-?mail\s*:/i.test(d))
  const email          = emailRaw?.replace(/^E?-?mail\s*:/i, '').trim() || null
  const porteRaw       = item.details.find((d) => /^Porte\s*:/i.test(d))
  const porte          = porteRaw?.replace(/^Porte\s*:/i, '').trim() || null
  const distanciaRaw   = item.details.find((d) => /^Dist[âa]ncia\s*:/i.test(d))
  const distanciaKm    = distanciaRaw
    ? parseFloat(distanciaRaw.replace(/^Dist[âa]ncia\s*:/i, '').replace(/[^0-9.]/g, '')) || null
    : null
  // "Situação Cadastral" (CNPJ) or fallback to plain "Situação" when tipo=cnpj
  const situacaoCadRaw = item.details.find((d) => /^Situação Cadastral\s*:/i.test(d))
                      || (!processo ? item.details.find((d) => /^Situação\s*:/i.test(d)) : undefined)
  const situacaoCad    = situacaoCadRaw?.replace(/^Situação(?: Cadastral)?\s*:/i, '').trim() || null
  const substanciaRaw  = item.details.find((d) => /^Substância\s*:/i.test(d))
  const substancia     = substanciaRaw?.replace(/^Substância\s*:/i, '').trim() || null

  function handleClick() {
    if (ownId) {
      selectFeature(isSelected ? null : ownId)
    } else if (hasDetails) {
      setOpen((v) => !v)
    }
  }

  function handleStarClick(e: React.MouseEvent) {
    e.stopPropagation()
    if (!ownId || !analiseId) return

    if (isFav) {
      markRemoved(ownId)
      removeMut.mutate({ analiseId, fornecedorId: ownId, tipoFonte })
    } else {
      markSaved(ownId, tipoFonte)
      const info = findMarkerInfo(ownId, amostraId)
      addMut.mutate({
        analiseId,
        data: {
          id: ownId,
          tipo_fonte: tipoFonte as 'anm' | 'cnpj',
          nome: cleanTitle,
          favorito: true,
          localizacao: info?.localizacao ?? null,
          topico: info?.topico ?? null,
          cnpj: cnpjDisplay || null,
          processo_anm: processo || null,
          cnae_descricao: atividade || null,
          fase: fase || null,
          substancia: substancia || null,
          situacao: tipoFonte === 'anm' ? (status || null) : null,
          situacao_cadastral: situacaoCad || (tipoFonte === 'cnpj' ? (status || null) : null),
          porte,
          contato_telefone: telefone,
          contato_email: email,
          municipio,
          uf,
          endereco,
          distancia_km: distanciaKm,
        },
      })
    }
  }

  return (
    <div ref={cardRef} className="min-w-0 w-full overflow-hidden rounded-xl">
      {/* Collapsed row — always visible */}
      <div
        role="button"
        tabIndex={0}
        onClick={handleClick}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleClick() }}
        className={[
          'w-full flex items-center gap-2 px-3 py-2.5 text-left transition-colors cursor-pointer',
          isSelected
            ? 'bg-green-400 hover:bg-green-500 text-white ring-2 ring-inset ring-white/60'
            : 'bg-green-600 hover:bg-green-700 text-white',
          open ? 'rounded-t-xl' : 'rounded-xl',
        ].join(' ')}
      >
        <MapPin size={11} className="shrink-0 opacity-80" />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold leading-tight break-words whitespace-normal">{cleanTitle}</p>
          {subtitle && (
            <p className="text-[10px] opacity-70 break-words whitespace-normal mt-0.5">{subtitle}</p>
          )}
        </div>
        {status && (
          <span className={`shrink-0 text-[10px] font-semibold px-1.5 py-0.5 rounded-md mr-1 ${statusBadgeClass(status)}`}>
            {status}
          </span>
        )}
        {analiseId && ownId && !isAmostraGeo && (
          <button
            onClick={handleStarClick}
            title={isFav ? 'Remover dos favoritos' : 'Salvar fornecedor'}
            className={[
              'shrink-0 p-0.5 rounded transition-all duration-200',
              isFav
                ? 'text-yellow-300 hover:text-yellow-200 scale-110'
                : 'text-white/30 hover:text-white/60 hover:scale-110',
            ].join(' ')}
          >
            <Star
              size={13}
              fill={isFav ? 'currentColor' : 'none'}
              strokeWidth={isFav ? 0 : 2}
            />
          </button>
        )}
        {hasDetails && (
          <ChevronDown
            size={12}
            className={`shrink-0 opacity-70 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
          />
        )}
      </div>

      {/* Expanded details */}
      {open && expandedDetails.length > 0 && (
        <div className="bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 border-t-0 rounded-b-xl px-3 py-2 space-y-0.5">
          {expandedDetails.map((d, i) => (
            <p key={i} className="text-[11px] text-green-800 dark:text-green-200 leading-relaxed">
              {d}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Content parser: splits assistant text into text blocks + result cards ──────

type ContentBlock =
  | { type: 'text'; content: string }
  | { type: 'result-group'; heading: string; items: ParsedResult[] }

/**
 * Keys that identify a "detail" dash line vs a new item title dash line.
 * e.g. "- Processo: 832..." is a detail; "- Dinamus Mineração Ltda" is a title.
 */
const DETAIL_KEY_RE =
  /^(Processo|Município|Municip|Fase|Área|Area|CNPJ|Endereço|Endereco|Telefone|Email|Contato|WhatsApp|Outros nomes|Obs|Site|Atividade|CNAE|Raio|Distância|Distancia|Situação|Situacao|Capital|Porte|Localização|Localizacao|Projeto|Classe|Laboratório|Laboratorio|Teores?|Amostra|Coordenadas|Analito)\s*:/i

/** Linhas de detalhe geoquímico (ex. Ce: 11 ppm | La: 11,5 ppm) */
function isGeoquimicaDetailLine(dashRaw: string): boolean {
  if (DETAIL_KEY_RE.test(dashRaw)) return true
  if (/^Localiza[çc][ãa]o\s*:/i.test(dashRaw)) return true
  if (/^[A-Za-z][A-Za-z0-9₀-₉]*(?:[₂₃₄₅]|[0-9])?\s*:/.test(dashRaw)) return true
  if (/ppm|mg\/kg/i.test(dashRaw) && dashRaw.includes(':')) return true
  return false
}

/**
 * Determine if a plain-text line is a section heading (subject/category)
 * rather than just a company name sitting above a dash list.
 */
function looksLikeHeading(text: string): boolean {
  if (text.includes('—')) return true
  if (text.endsWith(':')) return true
  if (/\d+\s+(jazida|empresa|resultado|fornecedor|item|encontrad)/i.test(text)) return true
  if (/encontrad[oa]s|setor|categoria|segmento|materiais?|mineração|comerci/i.test(text)) return true
  return false
}

/**
 * Parse the assistant's markdown response into structured blocks.
 *
 * Handles two agent output styles:
 *   A) Numbered items + indented dashes (preferred — enforced by prompt)
 *      "1. Dinamus Mineração\n   - Processo: ...\n   - Fase: ..."
 *   B) Dash-grouped items (fallback for non-compliant responses)
 *      "Areia Lavada\n- Dinamus Mineração\n- Processo: ...\n- Fase: ..."
 *
 * A plain-text line that is immediately followed by a list (numbered OR dash)
 * is treated as the section heading for that group.
 */
function parseBlocks(text: string): ContentBlock[] {
  const lines = text.split('\n')
  const blocks: ContentBlock[] = []
  let textBuffer = ''
  let currentGroup: { heading: string; items: ParsedResult[] } | null = null
  let currentItem: ParsedResult | null = null
  let pendingHeading = ''

  const flushText = () => {
    const t = textBuffer.trim()
    if (t) blocks.push({ type: 'text', content: t })
    textBuffer = ''
  }

  const flushItem = () => {
    if (currentItem && currentGroup) {
      currentGroup.items.push(currentItem)
      currentItem = null
    }
  }

  const flushGroup = () => {
    flushItem()
    if (currentGroup && currentGroup.items.length > 0) {
      // Narrative-bullet detection: real entity lists (jazidas / empresas /
      // fornecedores) always include at least one "Chave: valor" detail per
      // item (Processo:, CNPJ:, Município:, …). Bullet lists that come back
      // WITHOUT any such details are prose — e.g. "Principais etapas do
      // trajeto", "Vantagens", "Recomendações". Rendering them as chips
      // forces them through `truncate` and the user can't read past 30
      // chars. Demote those to a plain text block so the lines wrap.
      const hasAnyDetails = currentGroup.items.some((it) => it.details.length > 0)
      if (!hasAnyDetails && currentGroup.items.length >= 2) {
        const heading = currentGroup.heading.replace(/:$/, '').trim()
        const bulletLines = currentGroup.items.map((it) => `• ${it.title}`).join('\n')
        const textContent = heading ? `**${heading}**\n${bulletLines}` : bulletLines
        blocks.push({ type: 'text', content: textContent })
      } else {
        blocks.push({ type: 'result-group', ...currentGroup })
      }
    }
    currentGroup = null
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmed = line.trim()

    // Skip blank lines and horizontal rules (---, ***, ___)
    if (!trimmed || /^[-*_]{2,}$/.test(trimmed)) {
      if (currentGroup || pendingHeading) continue
      if (trimmed) continue  // skip --- even outside groups
      textBuffer += (textBuffer ? '\n' : '') + line
      continue
    }

    // Strip markdown headers, bold/italic markers before matching
    const clean = trimmed.replace(/^#{1,6}\s+/, '').replace(/\*\*/g, '').replace(/\*/g, '').trim()

    const numberedMatch = clean.match(/^\d+\.\s+(.+)/)
    const dashRaw       = clean.match(/^[-–•]\s+(.+)/)?.[1]?.trim()
    const isListItem    = !!numberedMatch || dashRaw !== undefined

    // ── Detect heading OR standalone name before a list ─────────────────
    if (!isListItem) {
      const nextNonEmptyRaw = lines.slice(i + 1).find((l) => {
        const t = l.trim()
        return t && !/^[-*_]{2,}$/.test(t)
      })?.trim() ?? ''
      const nextClean  = nextNonEmptyRaw.replace(/\*\*/g, '').replace(/\*/g, '').trim()
      const nextIsList = /^\d+\.\s+/.test(nextClean) || /^[-–•]\s+/.test(nextClean)

      if (nextIsList) {
        if (looksLikeHeading(clean)) {
          // Real heading: "Areia Lavada — 5 jazidas encontradas:"
          flushGroup()
          flushText()
          pendingHeading = clean.replace(/:$/, '')
        } else {
          // Company/entity name before details — treat as first item in headingless group
          flushGroup()
          flushText()
          pendingHeading = ''
          currentGroup = { heading: '', items: [] }
          currentItem = { title: clean, details: [] }
        }
        continue
      }
    }

    // ── Strategy A: numbered items ────────────────────────────────────────
    if (numberedMatch) {
      if (!currentGroup) {
        flushText()
        currentGroup = { heading: pendingHeading, items: [] }
        pendingHeading = ''
      }
      flushItem()
      currentItem = { title: numberedMatch[1].trim(), details: [] }
      continue
    }

    // ── Strategy B: dash items ────────────────────────────────────────────
    if (dashRaw !== undefined) {
      const isDetail = isGeoquimicaDetailLine(dashRaw)

      if (currentItem) {
        if (isDetail) {
          currentItem.details.push(dashRaw)
        } else {
          flushItem()
          currentItem = { title: dashRaw, details: [] }
        }
        continue
      }

      if (currentGroup || pendingHeading) {
        if (!currentGroup) {
          flushText()
          currentGroup = { heading: pendingHeading, items: [] }
          pendingHeading = ''
        }

        if (isDetail) {
          const last = currentGroup.items[currentGroup.items.length - 1]
          if (last) last.details.push(dashRaw)
        } else {
          flushItem()
          currentItem = { title: dashRaw, details: [] }
        }
        continue
      }
    }

    // ── Regular text line ────────────────────────────────────────────────
    if (currentGroup) flushGroup()
    textBuffer += (textBuffer ? '\n' : '') + line
  }

  flushGroup()
  flushText()

  return blocks
}

// ── Inline markdown renderer ──────────────────────────────────────────────────

function renderInline(text: string): string {
  return text
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br />')
}

// ── Thinking bubble (single-line, mock style) ────────────────────────────────

function ThinkingBubble({ message }: { message: ChatMessage }) {
  return (
    <div className="flex items-start gap-2.5">
      <div className={[
        'flex h-6 w-6 shrink-0 items-center justify-center rounded-full mt-0.5 transition-colors',
        message.isStreaming
          ? 'bg-green-100 dark:bg-green-900'
          : 'bg-zinc-100 dark:bg-zinc-800',
      ].join(' ')}>
        <Loader2
          size={12}
          className={[
            'transition-colors',
            message.isStreaming
              ? 'text-green-600 animate-spin'
              : 'text-(--color-text-muted)',
          ].join(' ')}
        />
      </div>
      <p className={[
        'text-xs italic pt-1 leading-relaxed max-w-[88%]',
        message.isStreaming
          ? 'text-green-600 dark:text-green-400'
          : 'text-(--color-text-muted)',
      ].join(' ')}>
        {message.content}
      </p>
    </div>
  )
}
