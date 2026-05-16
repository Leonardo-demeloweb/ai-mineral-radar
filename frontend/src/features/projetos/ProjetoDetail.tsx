import { useNavigate, useParams, Link } from 'react-router-dom'
import {
  ArrowLeft,
  Pencil,
  MapPin,
  Radar,
  CalendarDays,
  Plus,
  FileText,
  Trash2,
} from 'lucide-react'
import { Badge, Button, Card, Dialog, Skeleton } from '@/components/ui'
import { EmptyState } from '@/components/shared/EmptyState'
import { useProjeto } from '@/hooks/useProjetos'
import { useAnalises, useDeleteAnalise, useCreateAnalise } from '@/hooks/useAnalises'
import {
  PROJETO_TIPO_LABEL,
  PROJETO_STATUS_LABEL,
  PROJETO_STATUS_VARIANT,
  ANALISE_CATEGORIA_LABEL,
  ANALISE_STATUS_LABEL,
  ANALISE_STATUS_VARIANT,
  formatDateTime,
  formatDate,
} from '@/lib/formatters'
import { useState } from 'react'

export function ProjetoDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const { data: projeto, isLoading: projetoLoading, isError } = useProjeto(id)
  const { data: analises = [], isLoading: analisesLoading } = useAnalises(id)
  const deleteMutation = useDeleteAnalise()
  const createMutation = useCreateAnalise()

  const [deleteTarget, setDeleteTarget] = useState<{ id: string; titulo: string } | null>(null)
  const [showNewAnalise, setShowNewAnalise] = useState(false)
  const [novoTitulo, setNovoTitulo] = useState('')
  const [tituloError, setTituloError] = useState('')
  const [novoIncluirMei, setNovoIncluirMei] = useState(false)

  function handleCreateAnalise() {
    const titulo = novoTitulo.trim()
    if (titulo.length < 3) {
      setTituloError('Mínimo 3 caracteres')
      return
    }
    createMutation.mutate(
      {
        projeto_id: id!,
        titulo,
        filtros: { filtros_cnpj: { incluir_mei: novoIncluirMei } },
      },
      {
        onSuccess: (data) => {
          setShowNewAnalise(false)
          setNovoTitulo('')
          setTituloError('')
          setNovoIncluirMei(false)
          navigate(`/workspace?projetoId=${id}&analiseId=${data._id}`)
        },
      },
    )
  }

  function handleDeleteAnalise() {
    if (!deleteTarget) return
    deleteMutation.mutate(deleteTarget.id, { onSettled: () => setDeleteTarget(null) })
  }

  if (projetoLoading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Skeleton className="h-8 w-8 rounded-md" />
          <div className="space-y-2">
            <Skeleton className="h-6 w-56" />
            <Skeleton className="h-4 w-32" />
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <Skeleton className="h-20 w-full rounded-lg" />
          <Skeleton className="h-20 w-full rounded-lg" />
          <Skeleton className="h-20 w-full rounded-lg" />
        </div>
        <div className="space-y-3">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-14 w-full rounded-lg" />
          <Skeleton className="h-14 w-full rounded-lg" />
        </div>
      </div>
    )
  }

  if (isError || !projeto) {
    return (
      <EmptyState
        title="Projeto não encontrado"
        description="O projeto solicitado não existe ou foi removido."
        action={<Button onClick={() => navigate('/projetos')}>Voltar para projetos</Button>}
      />
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate('/projetos')}>
            <ArrowLeft size={16} />
          </Button>
          <div>
            <h1 className="text-xl font-semibold text-(--color-text)">{projeto.nome}</h1>
            <p className="mt-0.5 text-sm text-(--color-text-muted)">
              {PROJETO_TIPO_LABEL[projeto.tipo] ?? projeto.tipo}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={PROJETO_STATUS_VARIANT[projeto.status]}>
            {PROJETO_STATUS_LABEL[projeto.status] ?? projeto.status}
          </Badge>
          <Link to={`/projetos/novo?edit=${projeto._id}`}>
            <Button variant="secondary" size="sm">
              <Pencil size={14} className="mr-1.5" />
              Editar
            </Button>
          </Link>
        </div>
      </div>

      {/* Info cards */}
      <div className="grid gap-4 sm:grid-cols-3">
        <InfoCard
          icon={<MapPin size={18} className="text-green-500" />}
          label="Localização"
          value={projeto.municipio && projeto.uf ? `${projeto.municipio}/${projeto.uf}` : 'Não definida'}
        />
        <InfoCard
          icon={<Radar size={18} className="text-emerald-500" />}
          label="Raio de busca"
          value={`${projeto.raio_busca_km} km`}
        />
        <InfoCard
          icon={<CalendarDays size={18} className="text-amber-500" />}
          label="Última atualização"
          value={formatDateTime(projeto.updated_at)}
        />
      </div>

      {/* Análises section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-(--color-text)">
            Análises ({analises.length})
          </h2>
          <Button size="sm" onClick={() => setShowNewAnalise(true)}>
            <Plus size={14} className="mr-1.5" />
            Nova análise
          </Button>
        </div>

        {analisesLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : analises.length === 0 ? (
          <EmptyState
            title="Nenhuma análise criada"
            description="Crie uma análise para buscar fornecedores de materiais para este projeto."
            action={
              <Button size="sm" onClick={() => setShowNewAnalise(true)}>
                <Plus size={14} className="mr-1.5" />
                Criar primeira análise
              </Button>
            }
          />
        ) : (
          <Card className="divide-y divide-(--color-border) p-0">
            {analises.map((analise) => (
              <div
                key={analise._id}
                className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
              >
                <FileText size={16} className="shrink-0 text-(--color-text-muted)" />
                <div
                  className="flex-1 min-w-0 cursor-pointer"
                  onClick={() => navigate(`/projetos/${projeto._id}/analises/${analise._id}`)}
                >
                  <p className="truncate text-sm font-medium text-(--color-text)">
                    {analise.titulo}
                  </p>
                  <p className="text-xs text-(--color-text-muted)">
                    {ANALISE_CATEGORIA_LABEL[analise.categoria]} · {analise.total_fornecedores} fornecedor{analise.total_fornecedores !== 1 ? 'es' : ''} · {formatDate(analise.updated_at)}
                  </p>
                </div>
                <Badge variant={ANALISE_STATUS_VARIANT[analise.status]}>
                  {ANALISE_STATUS_LABEL[analise.status] ?? analise.status}
                </Badge>
                <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                  <Link to={`/projetos/${projeto._id}/analises/novo?edit=${analise._id}`}>
                    <Button variant="ghost" size="sm" title="Editar">
                      <Pencil size={13} />
                    </Button>
                  </Link>
                  <Button
                    variant="ghost"
                    size="sm"
                    title="Excluir"
                    onClick={() => setDeleteTarget({ id: analise._id, titulo: analise.titulo })}
                  >
                    <Trash2 size={13} className="text-red-500" />
                  </Button>
                </div>
              </div>
            ))}
          </Card>
        )}
      </div>

      {/* Delete analise dialog */}
      <Dialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Excluir análise"
      >
        <p className="text-sm text-(--color-text)">
          Tem certeza que deseja excluir <strong>{deleteTarget?.titulo}</strong>? Todos os
          fornecedores vinculados serão removidos.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
            Cancelar
          </Button>
          <Button
            onClick={handleDeleteAnalise}
            disabled={deleteMutation.isPending}
            className="bg-red-600 text-white hover:bg-red-700"
          >
            {deleteMutation.isPending ? 'Excluindo...' : 'Excluir'}
          </Button>
        </div>
      </Dialog>

      {/* New analise dialog */}
      <Dialog
        open={showNewAnalise}
        onOpenChange={(open) => {
          if (!open) {
            setShowNewAnalise(false)
            setNovoTitulo('')
            setTituloError('')
            setNovoIncluirMei(false)
          }
        }}
        title="Nova análise"
      >
        <p className="text-sm text-(--color-text-muted) mb-4">
          Informe o título da análise. Os demais campos serão preenchidos automaticamente
          pelo agente IA durante a interação no chat.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            handleCreateAnalise()
          }}
          className="space-y-4"
        >
          {/* Título */}
          <div>
            <label className="block text-sm font-medium text-(--color-text) mb-1.5">
              Título <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              autoFocus
              value={novoTitulo}
              onChange={(e) => {
                setNovoTitulo(e.target.value)
                if (tituloError) setTituloError('')
              }}
              placeholder="Ex: Fornecedores de Areia Lavada"
              className="w-full rounded-md border border-(--color-border) bg-(--color-surface) px-3 py-2 text-sm text-(--color-text) placeholder:text-(--color-text-muted) focus:outline-none focus:ring-2 focus:ring-green-500"
            />
            {tituloError && (
              <p className="mt-1 text-xs text-red-500">{tituloError}</p>
            )}
          </div>

          {/* Filtro MEI */}
          <label className="flex items-center justify-between gap-3 cursor-pointer select-none rounded-lg border border-(--color-border) px-4 py-3">
            <div>
              <span className="text-sm text-(--color-text)">Incluir MEI</span>
              <p className="text-xs text-(--color-text-muted) mt-0.5">
                {novoIncluirMei
                  ? 'Microempreendedores serão exibidos nos resultados'
                  : 'Microempreendedores serão excluídos da busca'}
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={novoIncluirMei}
              onClick={() => setNovoIncluirMei((v) => !v)}
              className={[
                'relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent transition-colors focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-2',
                novoIncluirMei ? 'bg-green-600' : 'bg-gray-300 dark:bg-gray-600',
              ].join(' ')}
            >
              <span
                className={[
                  'pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow ring-0 transition-transform',
                  novoIncluirMei ? 'translate-x-5' : 'translate-x-0',
                ].join(' ')}
              />
            </button>
          </label>

          <div className="flex justify-end gap-2 pt-1">
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setShowNewAnalise(false)
                setNovoTitulo('')
                setTituloError('')
                setNovoIncluirMei(false)
              }}
            >
              Cancelar
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? 'Criando...' : 'Criar e iniciar busca'}
            </Button>
          </div>
        </form>
      </Dialog>
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
      <div>
        <p className="text-xs text-(--color-text-muted)">{label}</p>
        <p className="text-sm font-medium text-(--color-text)">{value}</p>
      </div>
    </Card>
  )
}
