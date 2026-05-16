import { useRef, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import {
  ArrowLeft,
  Pencil,
  Search,
  Users,
  CalendarDays,
  Layers,
  MapPin,
  Star,
  Trash2,
  ChevronDown,
  Building2,
  Phone,
  Mail,
  FileText,
  User,
  Navigation,
  X,
} from 'lucide-react'
import { Badge, Button, Card, Skeleton } from '@/components/ui'
import { Dialog } from '@/components/ui/Dialog'
import { EmptyState } from '@/components/shared/EmptyState'
import { useAnalise, useRemoveFornecedor } from '@/hooks/useAnalises'
import type { Fornecedor } from '@/types/api'
import {
  ANALISE_CATEGORIA_LABEL,
  ANALISE_STATUS_LABEL,
  ANALISE_STATUS_VARIANT,
  formatDateTime,
} from '@/lib/formatters'

export function AnaliseDetail() {
  const { id: projetoId, aid } = useParams<{ id: string; aid: string }>()
  const navigate = useNavigate()

  const { data: analise, isLoading, isError } = useAnalise(aid)

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Skeleton className="h-8 w-8 rounded-md" />
          <div className="space-y-2">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-4 w-28" />
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-4">
          <Skeleton className="h-20 w-full rounded-lg" />
          <Skeleton className="h-20 w-full rounded-lg" />
          <Skeleton className="h-20 w-full rounded-lg" />
          <Skeleton className="h-20 w-full rounded-lg" />
        </div>
        <div className="space-y-3">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-32 w-full rounded-lg" />
        </div>
      </div>
    )
  }

  if (isError || !analise) {
    return (
      <EmptyState
        title="Análise não encontrada"
        description="A análise solicitada não existe ou foi removida."
        action={
          <Button onClick={() => navigate(`/projetos/${projetoId}`)}>Voltar para o projeto</Button>
        }
      />
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate(`/projetos/${projetoId}`)}>
            <ArrowLeft size={16} />
          </Button>
          <div>
            <h1 className="text-xl font-semibold text-(--color-text)">{analise.titulo}</h1>
            <p className="mt-0.5 text-sm text-(--color-text-muted)">
              {ANALISE_CATEGORIA_LABEL[analise.categoria]}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={ANALISE_STATUS_VARIANT[analise.status]}>
            {ANALISE_STATUS_LABEL[analise.status] ?? analise.status}
          </Badge>
          <Link to={`/workspace?projetoId=${projetoId}&analiseId=${analise._id}`}>
            <Button variant="secondary" size="sm">
              <MapPin size={14} className="mr-1.5" />
              Abrir no mapa
            </Button>
          </Link>
          <Link to={`/projetos/${projetoId}/analises/novo?edit=${analise._id}`}>
            <Button variant="secondary" size="sm">
              <Pencil size={14} className="mr-1.5" />
              Editar
            </Button>
          </Link>
        </div>
      </div>

      {/* Info cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <InfoCard
          icon={<Search size={18} className="text-green-500" />}
          label="Termo de busca"
          value={analise.termo_busca}
        />
        <InfoCard
          icon={<Users size={18} className="text-emerald-500" />}
          label="Fornecedores"
          value={String(analise.total_fornecedores)}
        />
        <InfoCard
          icon={<Layers size={18} className="text-violet-500" />}
          label="Categoria"
          value={ANALISE_CATEGORIA_LABEL[analise.categoria]}
        />
        <InfoCard
          icon={<CalendarDays size={18} className="text-amber-500" />}
          label="Atualizado em"
          value={formatDateTime(analise.updated_at)}
        />
      </div>

      {/* Fornecedores section */}
      <FornecedoresSection analise={analise} projetoId={projetoId!} />
    </div>
  )
}

function InfoCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: string
}) {
  return (
    <Card className="flex items-center gap-3">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-zinc-100 dark:bg-zinc-800">
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-xs text-(--color-text-muted)">{label}</p>
        <p className="truncate text-sm font-medium text-(--color-text)">{value}</p>
      </div>
    </Card>
  )
}

// ── Fornecedores section ───────────────────────────────────────────────────

const TIPO_FONTE_LABEL: Record<string, string> = {
  anm: 'Jazida',
  cnpj: 'Empresa',
  manual: 'Manual',
}

const TIPO_FONTE_COLOR: Record<string, string> = {
  anm: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  cnpj: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  manual: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
}

function FornecedoresSection({ analise, projetoId }: { analise: import('@/types/api').Analise; projetoId: string }) {
  const fornecedores = analise.fornecedores ?? []
  const count = fornecedores.length
  const removeMut = useRemoveFornecedor()
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const [confirmTarget, setConfirmTarget] = useState<Fornecedor | null>(null)

  const filtered = searchQuery.trim()
    ? fornecedores.filter((f) => {
        const q = searchQuery.toLowerCase()
        return (
          f.nome.toLowerCase().includes(q) ||
          f.cnpj?.toLowerCase().includes(q) ||
          f.processo_anm?.toLowerCase().includes(q) ||
          f.municipio?.toLowerCase().includes(q) ||
          f.uf?.toLowerCase().includes(q) ||
          f.cnae_descricao?.toLowerCase().includes(q) ||
          f.substancia?.toLowerCase().includes(q)
        )
      })
    : fornecedores

  function openSearch() {
    setSearchOpen(true)
    setTimeout(() => searchRef.current?.focus(), 50)
  }

  function closeSearch() {
    setSearchOpen(false)
    setSearchQuery('')
  }

  function handleRemove(e: React.MouseEvent, f: Fornecedor) {
    e.stopPropagation()
    setConfirmTarget(f)
  }

  function confirmRemove() {
    if (!confirmTarget) return
    removeMut.mutate({
      analiseId: analise._id,
      fornecedorId: confirmTarget.id,
      tipoFonte: confirmTarget.tipo_fonte,
    })
    setConfirmTarget(null)
  }

  function toggleExpand(f: Fornecedor) {
    const key = `${f.id}-${f.tipo_fonte}`
    setExpandedId((prev) => (prev === key ? null : key))
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-(--color-text) shrink-0">
          Fornecedores selecionados ({count})
        </h2>

        {count > 0 && (
          searchOpen ? (
            <div className="flex items-center gap-1.5 flex-1 max-w-xs">
              <div className="relative flex-1">
                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-(--color-text-muted)" />
                <input
                  ref={searchRef}
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Filtrar lista..."
                  className="w-full pl-7 pr-3 py-1.5 text-sm rounded-lg border border-(--color-border) bg-(--color-surface) text-(--color-text) placeholder:text-(--color-text-muted) focus:outline-none focus:ring-2 focus:ring-violet-400"
                  onKeyDown={(e) => { if (e.key === 'Escape') closeSearch() }}
                />
              </div>
              <button
                onClick={closeSearch}
                className="shrink-0 flex h-7 w-7 items-center justify-center rounded-lg text-(--color-text-muted) hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                title="Fechar filtro"
              >
                <X size={13} />
              </button>
            </div>
          ) : (
            <Button size="sm" variant="secondary" onClick={openSearch}>
              <Search size={13} className="mr-1.5" />
              Filtrar lista
            </Button>
          )
        )}
      </div>

      {count > 0 && filtered.length === 0 && (
        <p className="text-sm text-(--color-text-muted) text-center py-6">
          Nenhum fornecedor encontrado para "<span className="font-medium">{searchQuery}</span>"
        </p>
      )}

      {count === 0 ? (
        <Card className="flex flex-col items-center gap-3 py-10 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-100 dark:bg-zinc-800">
            <Star size={22} className="text-(--color-text-muted)" />
          </div>
          <div>
            <p className="text-sm font-medium text-(--color-text)">Nenhum fornecedor selecionado</p>
            <p className="mt-1 text-xs text-(--color-text-muted) max-w-xs">
              Busque fornecedores no chat e clique na estrela para selecionar os que deseja salvar nesta análise.
            </p>
          </div>
          <Link to={`/workspace?projetoId=${projetoId}&analiseId=${analise._id}`}>
            <Button size="sm" variant="secondary">
              <MapPin size={14} className="mr-1.5" />
              Iniciar busca no mapa
            </Button>
          </Link>
        </Card>
      ) : filtered.length > 0 ? (
        <Card className="divide-y divide-(--color-border) p-0 overflow-hidden">
          {filtered.map((f) => {
            const key = `${f.id}-${f.tipo_fonte}`
            const isOpen = expandedId === key
            return (
              <div key={key}>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => toggleExpand(f)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') toggleExpand(f) }}
                  className="flex items-center gap-3 px-4 py-3 cursor-pointer transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
                >
                  <Star size={14} className="shrink-0 text-yellow-500 fill-yellow-500" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-(--color-text) truncate">{f.nome}</p>
                    <p className="text-xs text-(--color-text-muted) truncate mt-0.5">
                      {[
                        f.cnpj,
                        f.processo_anm,
                        f.municipio && f.uf ? `${f.municipio}/${f.uf}` : f.municipio || f.uf,
                        f.substancia,
                      ].filter(Boolean).join(' · ')}
                    </p>
                  </div>
                  <span className={`shrink-0 text-[10px] font-semibold px-2 py-0.5 rounded-full ${TIPO_FONTE_COLOR[f.tipo_fonte] ?? TIPO_FONTE_COLOR.manual}`}>
                    {TIPO_FONTE_LABEL[f.tipo_fonte] ?? f.tipo_fonte}
                  </span>
                  <ChevronDown
                    size={14}
                    className={`shrink-0 text-(--color-text-muted) transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
                  />
                  <button
                    onClick={(e) => handleRemove(e, f)}
                    title="Remover fornecedor"
                    className="shrink-0 p-1 rounded text-(--color-text-muted) hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>

                {isOpen && <FornecedorDetail f={f} />}
              </div>
            )
          })}
        </Card>
      ) : null}

      <Dialog
        open={confirmTarget !== null}
        onOpenChange={(open) => { if (!open) setConfirmTarget(null) }}
        title="Remover fornecedor"
      >
        <p className="text-sm text-(--color-text-muted) mb-6">
          Remover <span className="font-medium text-(--color-text)">{confirmTarget?.nome}</span> dos fornecedores selecionados?
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={() => setConfirmTarget(null)}>
            Cancelar
          </Button>
          <Button
            size="sm"
            onClick={confirmRemove}
            disabled={removeMut.isPending}
            className="bg-red-600 text-white hover:bg-red-700 dark:bg-red-700 dark:hover:bg-red-600"
          >
            Remover
          </Button>
        </div>
      </Dialog>
    </div>
  )
}

function FornecedorDetail({ f }: { f: Fornecedor }) {
  const isEmpresa = f.tipo_fonte === 'cnpj'
  const isJazida  = f.tipo_fonte === 'anm'

  const locLabel = [f.municipio, f.uf].filter(Boolean).join('/')

  const situacaoLabel = isEmpresa ? f.situacao_cadastral : f.situacao
  const situacaoDot =
    situacaoLabel?.toUpperCase().includes('ATIVA') || situacaoLabel?.toUpperCase().includes('AUTORIZADA')
      ? 'bg-emerald-500'
      : situacaoLabel
        ? 'bg-red-500'
        : 'bg-zinc-400'

  return (
    <div className="bg-zinc-50 dark:bg-zinc-900/50 border-t border-(--color-border) px-4 py-3 space-y-1.5 text-[12px]">

      {isEmpresa && f.cnpj && (
        <PopupRow icon={<FileText size={11} className="text-(--color-text-muted)" />}>
          <span className="font-mono text-[11px]">{f.cnpj}</span>
        </PopupRow>
      )}
      {isJazida && f.processo_anm && (
        <PopupRow icon={<FileText size={11} className="text-(--color-text-muted)" />}>
          <span className="font-mono text-[11px]">{f.processo_anm}</span>
        </PopupRow>
      )}

      {locLabel && (
        <PopupRow icon={<span className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${situacaoDot}`} />}>
          <span>{locLabel}</span>
          {situacaoLabel && (
            <span className="ml-1 text-(--color-text-muted)">· {situacaoLabel}</span>
          )}
        </PopupRow>
      )}

      {isEmpresa && f.porte && (
        <PopupRow icon={<Building2 size={11} className="text-(--color-text-muted)" />}>
          {f.porte}
        </PopupRow>
      )}

      {isJazida && f.substancia && (
        <PopupRow icon={<Layers size={11} className="text-emerald-500" />}>
          <span className="text-emerald-600 dark:text-emerald-400 font-medium">{f.substancia}</span>
          {f.fase && <span className="ml-1 text-(--color-text-muted)">· {f.fase}</span>}
        </PopupRow>
      )}

      {f.contato_telefone && (
        <PopupRow icon={<Phone size={11} className="text-(--color-text-muted)" />}>
          {f.contato_telefone}
        </PopupRow>
      )}

      {f.contato_email && (
        <PopupRow icon={<Mail size={11} className="text-(--color-text-muted)" />}>
          <a
            href={`mailto:${f.contato_email}`}
            className="hover:underline text-green-500 dark:text-green-400"
            onClick={(e) => e.stopPropagation()}
          >
            {f.contato_email}
          </a>
        </PopupRow>
      )}

      {f.endereco && (
        <PopupRow icon={<MapPin size={11} className="text-(--color-text-muted)" />}>
          {f.endereco}
        </PopupRow>
      )}

      {isEmpresa && (f.cnae_descricao || f.cnae_principal) && (
        <PopupRow icon={<Layers size={11} className="text-(--color-text-muted)" />}>
          <span className="italic text-(--color-text-muted)">{f.cnae_descricao || f.cnae_principal}</span>
        </PopupRow>
      )}

      {f.contato_nome && (
        <PopupRow icon={<User size={11} className="text-(--color-text-muted)" />}>
          {f.contato_nome}
        </PopupRow>
      )}

      {f.distancia_km != null && (
        <PopupRow icon={<Navigation size={11} className="text-(--color-text-muted)" />}>
          {f.distancia_km.toFixed(2)} km
        </PopupRow>
      )}

      {f.notas && (
        <PopupRow icon={<FileText size={11} className="text-(--color-text-muted)" />}>
          {f.notas}
        </PopupRow>
      )}

      {f.adicionado_em && (
        <p className="text-[10px] text-(--color-text-muted) mt-2 pt-2 border-t border-(--color-border)">
          Adicionado em{' '}
          {new Date(f.adicionado_em).toLocaleDateString('pt-BR', {
            day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit',
          })}
        </p>
      )}
    </div>
  )
}

function PopupRow({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-1.5 leading-snug">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span className="text-(--color-text) break-all">{children}</span>
    </div>
  )
}
