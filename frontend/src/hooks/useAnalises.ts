import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Analise, FiltrosBusca } from '@/types/api'

export type AnaliseInput = {
  projeto_id: string
  titulo: string
  categoria?: Analise['categoria']
  termo_busca?: string
  descricao?: string
  filtros?: FiltrosBusca
}

export type AnaliseUpdateInput = Partial<Omit<AnaliseInput, 'projeto_id'>>

const KEY = ['analises'] as const

interface PaginatedAnalises {
  items: Analise[]
  total: number
  page: number
  page_size: number
  pages: number
}

async function fetchAnalisesByProjeto(projetoId: string): Promise<Analise[]> {
  const res = await api
    .get('analises', { searchParams: { projeto_id: projetoId, page_size: '100' } })
    .json<PaginatedAnalises>()
  return res.items
}

async function fetchAnalise(id: string): Promise<Analise> {
  return api.get(`analises/${id}`).json<Analise>()
}

async function createAnalise(input: AnaliseInput): Promise<Analise> {
  return api.post('analises', { json: input }).json<Analise>()
}

async function updateAnalise(id: string, input: AnaliseUpdateInput): Promise<Analise> {
  return api.put(`analises/${id}`, { json: input }).json<Analise>()
}

async function deleteAnalise(id: string): Promise<void> {
  await api.delete(`analises/${id}`)
}

export function useAnalises(projetoId: string | undefined) {
  return useQuery({
    queryKey: [...KEY, projetoId],
    queryFn: () => fetchAnalisesByProjeto(projetoId!),
    enabled: !!projetoId,
  })
}

export function useAnalise(id: string | undefined) {
  return useQuery({
    queryKey: [...KEY, 'detail', id],
    queryFn: () => fetchAnalise(id!),
    enabled: !!id,
  })
}

export function useCreateAnalise() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: createAnalise,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: [...KEY, data.projeto_id] })
      qc.invalidateQueries({ queryKey: ['projetos'] })
    },
  })
}

export function useUpdateAnalise() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: AnaliseUpdateInput }) =>
      updateAnalise(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  })
}

export function useDeleteAnalise() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: deleteAnalise,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY })
      qc.invalidateQueries({ queryKey: ['projetos'] })
    },
  })
}

// ── Fornecedor operations ────────────────────────────────────────────────────

export interface AddFornecedorInput {
  id: string
  tipo_fonte: 'anm' | 'cnpj' | 'manual'
  nome: string
  favorito?: boolean
  localizacao?: { lat: number; lon: number } | null
  endereco?: string | null
  municipio?: string | null
  uf?: string | null
  // ANM
  processo_anm?: string | null
  substancia?: string | null
  fase?: string | null
  situacao?: string | null
  // CNPJ
  cnpj?: string | null
  cnae_principal?: string | null
  cnae_descricao?: string | null
  porte?: string | null
  situacao_cadastral?: string | null
  // Contato (auto-preenchido da fonte)
  contato_telefone?: string | null
  contato_email?: string | null
  distancia_km?: number | null
  notas?: string | null
  topico?: string | null
}

async function addFornecedor(analiseId: string, input: AddFornecedorInput): Promise<Analise> {
  console.log('[addFornecedor] payload:', { id: input.id, localizacao: input.localizacao, favorito: input.favorito })
  return api.post(`analises/${analiseId}/fornecedores`, { json: input }).json<Analise>()
}

async function removeFornecedor(analiseId: string, fornecedorId: string, tipoFonte: string): Promise<void> {
  await api.delete(`analises/${analiseId}/fornecedores/${fornecedorId}`, {
    searchParams: { tipo_fonte: tipoFonte },
  })
}

export function useAddFornecedor() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ analiseId, data }: { analiseId: string; data: AddFornecedorInput }) =>
      addFornecedor(analiseId, data),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: [...KEY, 'detail', vars.analiseId] })
      qc.invalidateQueries({ queryKey: KEY })
    },
  })
}

export function useRemoveFornecedor() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ analiseId, fornecedorId, tipoFonte }: { analiseId: string; fornecedorId: string; tipoFonte: string }) =>
      removeFornecedor(analiseId, fornecedorId, tipoFonte),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: [...KEY, 'detail', vars.analiseId] })
      qc.invalidateQueries({ queryKey: KEY })
    },
  })
}
