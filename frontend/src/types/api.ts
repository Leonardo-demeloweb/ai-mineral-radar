export interface Projeto {
  _id: string
  nome: string
  tipo: 'mineracao' | 'pesquisa_mineral' | 'licenciamento' | 'lavra' | 'beneficiamento' | 'infraestrutura' | 'logistica' | 'ambiental' | 'industrial' | 'outro'
  status: 'planejamento' | 'em_andamento' | 'pausado' | 'concluido' | 'cancelado'
  localizacao?: { lat: number; lon: number }
  endereco?: string
  municipio?: string
  uf?: string
  raio_busca_km: number
  total_analises: number
  created_at: string
  updated_at: string
}

export interface FiltrosCNPJ {
  cnaes?: string[]
  portes?: string[]
  situacoes_cadastrais?: string[]
  naturezas_juridicas?: string[]
  incluir_mei?: boolean
}

export interface FiltrosBusca {
  ufs?: string[]
  municipios?: string[]
  raio_km?: number
  centro_busca?: { lat: number; lon: number }
  texto_livre?: string
  filtros_cnpj?: FiltrosCNPJ
}

export interface Analise {
  _id: string
  titulo: string
  projeto_id: string
  categoria: 'material_mineracao' | 'produto_comercial' | 'servico' | 'hibrido'
  termo_busca: string
  status: 'rascunho' | 'em_analise' | 'concluida' | 'arquivada'
  filtros?: FiltrosBusca
  fornecedores?: Fornecedor[]
  total_fornecedores: number
  created_at: string
  updated_at: string
}

export interface Fornecedor {
  id: string
  tipo_fonte: 'anm' | 'cnpj' | 'manual'
  nome: string
  descricao?: string
  localizacao?: { lat: number; lon: number }
  endereco?: string
  municipio?: string
  uf?: string
  distancia_km?: number
  favorito: boolean
  aprovado?: boolean
  notas?: string
  contato_nome?: string
  contato_telefone?: string
  contato_email?: string
  adicionado_em?: string
  // ANM
  processo_anm?: string
  substancia?: string
  fase?: string
  situacao?: string
  // CNPJ
  cnpj?: string
  cnae_principal?: string
  cnae_descricao?: string
  porte?: string
  situacao_cadastral?: string
  // Topic label for map chip restoration
  topico?: string
}
