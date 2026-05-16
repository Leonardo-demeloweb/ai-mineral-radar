import { create } from 'zustand'

interface ChatState {
  activeConversationId: string | null
  projetoId: string | null
  analiseId: string | null
  lastToolCallResults: Record<string, unknown>
  suggestedQueries: string[]

  setContext: (projetoId: string | null, analiseId?: string | null) => void
  setToolCallResult: (toolName: string, data: unknown) => void
  clearConversation: () => void
}

export const useChatStore = create<ChatState>((set) => ({
  activeConversationId: null,
  projetoId: null,
  analiseId: null,
  lastToolCallResults: {},
  suggestedQueries: [
    'Jazidas de lítio próximas ao projeto',
    'Empresas de transporte de minérios em MG',
    'Detalhes do processo 832.145/2018',
    'Empresas do sócio João da Silva',
  ],

  setContext: (projetoId, analiseId) =>
    set({ projetoId: projetoId ?? null, analiseId: analiseId ?? null }),

  setToolCallResult: (toolName, data) =>
    set((state) => ({
      lastToolCallResults: { ...state.lastToolCallResults, [toolName]: data },
    })),

  clearConversation: () =>
    set({
      activeConversationId: null,
      lastToolCallResults: {},
    }),
}))
