import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, Search, Trash2, Pencil, Eye } from 'lucide-react'
import {
  Badge,
  Button,
  Card,
  Dialog,
  Input,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Skeleton,
} from '@/components/ui'
import { EmptyState } from '@/components/shared/EmptyState'
import { useProjetos, useDeleteProjeto } from '@/hooks/useProjetos'
import { useDebounce } from '@/hooks/useDebounce'
import {
  PROJETO_TIPO_LABEL,
  PROJETO_STATUS_LABEL,
  PROJETO_STATUS_VARIANT,
  formatDate,
} from '@/lib/formatters'

export function ProjetosList() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; nome: string; total_analises: number } | null>(null)

  const debouncedSearch = useDebounce(search, 400)
  const { data, isLoading } = useProjetos(debouncedSearch)
  const projetos = data?.items ?? []
  const total = data?.total ?? 0
  const deleteMutation = useDeleteProjeto()

  function handleDelete() {
    if (!deleteTarget) return
    deleteMutation.mutate(
      { id: deleteTarget.id, cascade: deleteTarget.total_analises > 0 },
      { onSettled: () => setDeleteTarget(null) }
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-(--color-text)">Projetos</h1>
        <Button onClick={() => navigate('/projetos/novo')}>
          <Plus size={16} className="mr-1.5" />
          Novo projeto
        </Button>
      </div>

      <div className="relative max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-(--color-text-muted)" />
        <Input
          placeholder="Buscar por nome, município ou UF..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-9"
        />
      </div>

      {isLoading ? (
        <Card className="p-0">
          <div className="space-y-0 divide-y divide-(--color-border)">
            <div className="flex items-center gap-4 px-3 py-3">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-4 w-20" />
              <Skeleton className="h-5 w-24 rounded-full" />
              <Skeleton className="h-4 w-28" />
              <Skeleton className="h-4 w-12" />
              <Skeleton className="h-4 w-24" />
              <Skeleton className="ml-auto h-4 w-20" />
            </div>
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 px-3 py-3">
                <Skeleton className="h-4 w-36" />
                <Skeleton className="h-4 w-16" />
                <Skeleton className="h-5 w-20 rounded-full" />
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-4 w-8" />
                <Skeleton className="h-4 w-20" />
                <Skeleton className="ml-auto h-6 w-24" />
              </div>
            ))}
          </div>
        </Card>
      ) : projetos.length === 0 && !search ? (
        <EmptyState
          title="Nenhum projeto cadastrado"
          description="Crie o primeiro projeto para organizar análises e o contexto do mapa no MineralRadar."
          action={
            <Button onClick={() => navigate('/projetos/novo')}>
              <Plus size={16} className="mr-1.5" />
              Novo projeto
            </Button>
          }
        />
      ) : (
        <>
          <Card className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Nome</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Localização</TableHead>
                  <TableHead className="text-center">Análises</TableHead>
                  <TableHead>Atualização</TableHead>
                  <TableHead className="text-right">Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {projetos.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="py-8 text-center text-(--color-text-muted)">
                      Nenhum projeto encontrado para &quot;{debouncedSearch}&quot;
                    </TableCell>
                  </TableRow>
                ) : (
                  projetos.map((projeto) => (
                    <TableRow key={projeto._id} className="cursor-pointer" onClick={() => navigate(`/projetos/${projeto._id}`)}>
                      <TableCell className="font-medium">{projeto.nome}</TableCell>
                      <TableCell>{PROJETO_TIPO_LABEL[projeto.tipo] ?? projeto.tipo}</TableCell>
                      <TableCell>
                        <Badge variant={PROJETO_STATUS_VARIANT[projeto.status]}>
                          {PROJETO_STATUS_LABEL[projeto.status] ?? projeto.status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {projeto.municipio && projeto.uf ? `${projeto.municipio}/${projeto.uf}` : '—'}
                      </TableCell>
                      <TableCell className="text-center">{projeto.total_analises}</TableCell>
                      <TableCell>{formatDate(projeto.updated_at)}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                          <Link to={`/projetos/${projeto._id}`}>
                            <Button variant="ghost" size="sm" title="Ver detalhes">
                              <Eye size={14} />
                            </Button>
                          </Link>
                          <Link to={`/projetos/novo?edit=${projeto._id}`}>
                            <Button variant="ghost" size="sm" title="Editar">
                              <Pencil size={14} />
                            </Button>
                          </Link>
                          <Button
                            variant="ghost"
                            size="sm"
                            title="Excluir"
                            onClick={() => setDeleteTarget({ id: projeto._id, nome: projeto.nome, total_analises: projeto.total_analises ?? 0 })}
                          >
                            <Trash2 size={14} className="text-red-500" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </Card>

          <p className="text-xs text-(--color-text-muted)">
            {projetos.length} de {total} projeto{total !== 1 ? 's' : ''}
          </p>
        </>
      )}

      <Dialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Excluir projeto"
      >
        <p className="text-sm text-(--color-text)">
          Tem certeza que deseja excluir <strong>{deleteTarget?.nome}</strong>? Essa ação não pode ser desfeita.
        </p>
        {deleteTarget && deleteTarget.total_analises > 0 && (
          <p className="mt-2 text-sm text-amber-500">
            Este projeto possui <strong>{deleteTarget.total_analises} análise{deleteTarget.total_analises !== 1 ? 's' : ''}</strong> vinculada{deleteTarget.total_analises !== 1 ? 's' : ''} que também serão excluídas.
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
            Cancelar
          </Button>
          <Button
            onClick={handleDelete}
            disabled={deleteMutation.isPending}
            className="bg-red-600 text-white hover:bg-red-700"
          >
            {deleteMutation.isPending ? 'Excluindo...' : 'Excluir'}
          </Button>
        </div>
      </Dialog>
    </div>
  )
}
