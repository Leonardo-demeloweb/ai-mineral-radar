import { Moon, Sun } from 'lucide-react'
import { useEffect } from 'react'
import { useUiStore } from '@/stores/uiStore'

export function ThemeToggle() {
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  function toggle() {
    setTheme(theme === 'dark' ? 'light' : 'dark')
  }

  return (
    <button
      onClick={toggle}
      className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-(--color-text-muted) transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-(--color-text)"
      title={theme === 'dark' ? 'Mudar para modo claro' : 'Mudar para modo escuro'}
    >
      {theme === 'dark' ? <Moon size={16} /> : <Sun size={16} />}
      <span className="hidden sm:inline">{theme === 'dark' ? 'Dark' : 'Light'}</span>
    </button>
  )
}
