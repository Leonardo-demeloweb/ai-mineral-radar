import { create } from 'zustand'

interface FavoritesState {
  /** Map of normalized fornecedor ID → tipo_fonte for items saved to the active study */
  saved: Record<string, string>

  /** Replace the full set — only includes fornecedores with favorito=true */
  syncFromStudy: (fornecedores: Array<{ id: string; tipo_fonte: string; favorito?: boolean }>) => void

  /** Optimistically mark an ID as saved */
  markSaved: (id: string, tipoFonte: string) => void

  /** Optimistically mark an ID as removed */
  markRemoved: (id: string) => void

  /** Reset on study change */
  reset: () => void
}

export const useFavoritesStore = create<FavoritesState>((set) => ({
  saved: {},

  syncFromStudy: (fornecedores) =>
    set({
      saved: Object.fromEntries(
        fornecedores
          .filter((f) => f.favorito)
          .map((f) => [f.id, f.tipo_fonte]),
      ),
    }),

  markSaved: (id, tipoFonte) =>
    set((s) => ({ saved: { ...s.saved, [id]: tipoFonte } })),

  markRemoved: (id) =>
    set((s) => {
      const next = { ...s.saved }
      delete next[id]
      return { saved: next }
    }),

  reset: () => set({ saved: {} }),
}))
