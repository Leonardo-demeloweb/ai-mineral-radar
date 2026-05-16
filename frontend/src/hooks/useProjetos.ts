import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Projeto } from '@/types/api'

export type ProjetoInput = Pick<
  Projeto,
  'nome' | 'tipo' | 'status' | 'municipio' | 'uf' | 'raio_busca_km' | 'endereco' | 'localizacao'
>

const PROJETOS_KEY = ['projetos'] as const

interface PaginatedProjetos {
  items: Projeto[]
  total: number
  page: number
  page_size: number
  pages: number
}

async function fetchProjetos(search?: string): Promise<PaginatedProjetos> {
  const params: Record<string, string> = { page_size: '50' }
  if (search?.trim()) params.search = search.trim()
  return api.get('projetos', { searchParams: params }).json<PaginatedProjetos>()
}

async function fetchProjeto(id: string): Promise<Projeto> {
  return api.get(`projetos/${id}`).json<Projeto>()
}

async function createProjeto(input: ProjetoInput): Promise<Projeto> {
  return api.post('projetos', { json: input }).json<Projeto>()
}

async function updateProjeto(id: string, input: Partial<ProjetoInput>): Promise<Projeto> {
  return api.put(`projetos/${id}`, { json: input }).json<Projeto>()
}

async function deleteProjeto({ id, cascade }: { id: string; cascade?: boolean }): Promise<void> {
  const url = cascade ? `projetos/${id}?cascade=true` : `projetos/${id}`
  await api.delete(url)
}

export function useProjetos(search?: string) {
  return useQuery({
    queryKey: [...PROJETOS_KEY, search ?? ''],
    queryFn: () => fetchProjetos(search),
  })
}

export function useProjeto(id: string | undefined) {
  return useQuery({
    queryKey: [...PROJETOS_KEY, id],
    queryFn: () => fetchProjeto(id!),
    enabled: !!id,
  })
}

export function useCreateProjeto() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: createProjeto,
    onSuccess: () => qc.invalidateQueries({ queryKey: PROJETOS_KEY }),
  })
}

export function useUpdateProjeto() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<ProjetoInput> }) =>
      updateProjeto(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: PROJETOS_KEY }),
  })
}

export function useDeleteProjeto() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, cascade }: { id: string; cascade?: boolean }) =>
      deleteProjeto({ id, cascade }),
    onSuccess: () => qc.invalidateQueries({ queryKey: PROJETOS_KEY }),
  })
}
