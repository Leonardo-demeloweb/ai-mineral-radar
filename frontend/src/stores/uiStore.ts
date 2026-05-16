import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type ThemeMode = 'light' | 'dark'
type ModalType = 'jazida-detail' | 'empresa-detail' | null

interface UiState {
  sidebarOpen: boolean
  theme: ThemeMode
  chatPanelWidth: number
  activeModal: ModalType
  modalData: unknown
  activeProjetoId: string | null
  mapTopics: string[]

  toggleSidebar: () => void
  setTheme: (theme: ThemeMode) => void
  setChatPanelWidth: (width: number) => void
  openModal: (type: NonNullable<ModalType>, data?: unknown) => void
  closeModal: () => void
  setActiveProjetoId: (id: string | null) => void
  setMapTopics: (topics: string[]) => void
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      theme: 'dark',
      chatPanelWidth: 40,
      activeModal: null,
      modalData: null,
      activeProjetoId: null,
      mapTopics: [],

      toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
      setTheme: (theme) => set({ theme }),
      setChatPanelWidth: (chatPanelWidth) => set({ chatPanelWidth }),
      openModal: (activeModal, modalData) => set({ activeModal, modalData: modalData ?? null }),
      closeModal: () => set({ activeModal: null, modalData: null }),
      setActiveProjetoId: (activeProjetoId) => set({ activeProjetoId }),
      setMapTopics: (mapTopics) => set({ mapTopics }),
    }),
    {
      name: 'mineralradar-ui',
      partialize: (state) => ({ theme: state.theme, sidebarOpen: state.sidebarOpen }),
    }
  )
)
