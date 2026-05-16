import { useState, useEffect } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Save } from 'lucide-react'
import { Button, Card, Input, Skeleton } from '@/components/ui'
import { useCreateAnalise, useUpdateAnalise, useAnalise } from '@/hooks/useAnalises'
import { useProjeto } from '@/hooks/useProjetos'
import { ANALISE_CATEGORIA_LABEL, PROJETO_TIPO_LABEL } from '@/lib/formatters'
import type { Analise } from '@/types/api'

type CategoriaKey = Analise['categoria']

const CATEGORIAS: CategoriaKey[] = [
  'material_mineracao',
  'produto_comercial',
  'servico',
  'hibrido',
]

const CATEGORIA_HINT: Record<CategoriaKey, string> = {
  material_mineracao: 'Busca jazidas no índice ANM (processos de lavra, pesquisa, etc.)',
  produto_comercial: 'Busca empresas fornecedoras no índice CNPJ',
  servico: 'Busca prestadores de serviços no índice CNPJ',
  hibrido: 'Busca em ANM e CNPJ simultaneamente',
}

interface FormState {
  titulo: string
  categoria: CategoriaKey
  termo_busca: string
  descricao: string
}

const EMPTY: FormState = {
  titulo: '',
  categoria: 'material_mineracao',
  termo_busca: '',
  descricao: '',
}

function validate(f: FormState): Partial<Record<keyof FormState, string>> {
  const errors: Partial<Record<keyof FormState, string>> = {}
  if (f.titulo.trim().length < 3) errors.titulo = 'Mínimo 3 caracteres.'
  if (f.termo_busca.trim().length < 2) errors.termo_busca = 'Mínimo 2 caracteres.'
  return errors
}

export function AnaliseForm() {
  const { id: projetoId } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const editId = searchParams.get('edit') ?? undefined
  const isEditing = !!editId

  const navigate = useNavigate()

  const { data: projeto, isLoading: projetoLoading } = useProjeto(projetoId)
  const { data: analise, isLoading: analiseLoading } = useAnalise(editId)

  const createMutation = useCreateAnalise()
  const updateMutation = useUpdateAnalise()

  const [form, setForm] = useState<FormState>(EMPTY)
  const [errors, setErrors] = useState<Partial<Record<keyof FormState, string>>>({})
  const [submitted, setSubmitted] = useState(false)

  useEffect(() => {
    if (isEditing && analise) {
      setForm({
        titulo: analise.titulo,
        categoria: analise.categoria,
        termo_busca: analise.termo_busca,
        descricao: '',
      })
    }
  }, [isEditing, analise])

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => {
      const next = { ...prev, [key]: value }
      if (submitted) setErrors(validate(next))
      return next
    })
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitted(true)
    const errs = validate(form)
    if (Object.keys(errs).length > 0) {
      setErrors(errs)
      return
    }

    if (isEditing && editId) {
      updateMutation.mutate(
        {
          id: editId,
          data: {
            titulo: form.titulo.trim(),
            categoria: form.categoria,
            termo_busca: form.termo_busca.trim(),
            ...(form.descricao.trim() ? { descricao: form.descricao.trim() } : {}),
          },
        },
        { onSuccess: () => navigate(`/projetos/${projetoId}`) },
      )
    } else {
      createMutation.mutate(
        {
          projeto_id: projetoId!,
          titulo: form.titulo.trim(),
          categoria: form.categoria,
          termo_busca: form.termo_busca.trim(),
          ...(form.descricao.trim() ? { descricao: form.descricao.trim() } : {}),
        },
        { onSuccess: (data) => navigate(`/projetos/${projetoId}/analises/${data._id}`) },
      )
    }
  }

  const isPending = createMutation.isPending || updateMutation.isPending
  const isLoading = projetoLoading || (isEditing && analiseLoading)

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate(`/projetos/${projetoId}`)}>
          <ArrowLeft size={16} />
        </Button>
        <div>
          <h1 className="text-xl font-semibold text-(--color-text)">
            {isEditing ? 'Editar análise' : 'Nova análise'}
          </h1>
          {projeto && (
            <p className="mt-0.5 text-sm text-(--color-text-muted)">
              {PROJETO_TIPO_LABEL[projeto.tipo] ?? projeto.tipo} — {projeto.nome}
            </p>
          )}
        </div>
      </div>

      <form onSubmit={handleSubmit} noValidate>
        <Card className="space-y-5">
          {/* Título */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-(--color-text)">
              Título <span className="text-red-500">*</span>
            </label>
            <Input
              value={form.titulo}
              onChange={(e) => set('titulo', e.target.value)}
              placeholder="Ex: Fornecedores de Areia Lavada"
              className={errors.titulo ? 'border-red-500' : ''}
            />
            {errors.titulo && (
              <p className="text-xs text-red-500">{errors.titulo}</p>
            )}
          </div>

          {/* Categoria */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-(--color-text)">
              Categoria <span className="text-red-500">*</span>
            </label>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {CATEGORIAS.map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => set('categoria', cat)}
                  className={[
                    'rounded-lg border px-3 py-2 text-left text-sm transition-colors',
                    form.categoria === cat
                      ? 'border-green-500 bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300'
                      : 'border-(--color-border) text-(--color-text-muted) hover:border-green-300 hover:text-(--color-text)',
                  ].join(' ')}
                >
                  {ANALISE_CATEGORIA_LABEL[cat]}
                </button>
              ))}
            </div>
            <p className="text-xs text-(--color-text-muted)">
              {CATEGORIA_HINT[form.categoria]}
            </p>
          </div>

          {/* Termo de busca */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-(--color-text)">
              Termo de busca <span className="text-red-500">*</span>
            </label>
            <Input
              value={form.termo_busca}
              onChange={(e) => set('termo_busca', e.target.value)}
              placeholder={
                form.categoria === 'material_mineracao'
                  ? 'Ex: areia lavada, brita, cascalho'
                  : form.categoria === 'servico'
                  ? 'Ex: transporte de carga, locação de equipamento'
                  : 'Ex: parafuso estrutural, vergalhão'
              }
              className={errors.termo_busca ? 'border-red-500' : ''}
            />
            {errors.termo_busca && (
              <p className="text-xs text-red-500">{errors.termo_busca}</p>
            )}
            <p className="text-xs text-(--color-text-muted)">
              O agente usará este termo para buscar fornecedores nos índices correspondentes.
            </p>
          </div>

          {/* Descrição */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-(--color-text)">
              Descrição <span className="text-xs font-normal text-(--color-text-muted)">(opcional)</span>
            </label>
            <textarea
              value={form.descricao}
              onChange={(e) => set('descricao', e.target.value)}
              rows={3}
              placeholder="Detalhe o contexto, especificações técnicas ou critérios adicionais..."
              className="w-full rounded-md border border-(--color-border) bg-(--color-surface) px-3 py-2 text-sm text-(--color-text) placeholder:text-(--color-text-muted) focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-0 resize-none"
            />
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => navigate(`/projetos/${projetoId}`)}
            >
              Cancelar
            </Button>
            <Button type="submit" disabled={isPending}>
              <Save size={14} className="mr-1.5" />
              {isPending ? 'Salvando...' : isEditing ? 'Salvar alterações' : 'Criar análise'}
            </Button>
          </div>
        </Card>
      </form>
    </div>
  )
}
