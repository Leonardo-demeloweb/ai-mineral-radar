import { Link, useLocation } from 'react-router-dom'
import { Radar } from 'lucide-react'
import { Breadcrumb } from '@/components/layout/Breadcrumb'
import { ThemeToggle } from '@/components/layout/ThemeToggle'

/** Rótulo curto para rotas de um só segmento (breadcrumb completo fica oculto). */
const TOP_ROUTE_LABELS: Record<string, string> = {
  workspace: 'Workspace',
  projetos: 'Projetos',
  dashboard: 'Dashboard',
}

export function Header() {
  const { pathname } = useLocation()
  const segments = pathname.split('/').filter(Boolean)
  const showBreadcrumbTrail = segments.length > 1

  const centerContent = showBreadcrumbTrail ? (
    <Breadcrumb />
  ) : (
    <span className="truncate text-center text-sm font-medium text-(--color-text-muted)">
      {segments[0] ? TOP_ROUTE_LABELS[segments[0]] ?? segments[0] : 'Início'}
    </span>
  )

  return (
    <header className="grid h-14 shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3 border-b border-(--color-border) bg-(--color-surface) px-4 sm:px-6">
      <div className="flex min-w-0 items-center justify-self-start">
        <Link
          to="/workspace"
          className="flex min-w-0 max-w-full items-center gap-2 rounded-md py-1 text-(--color-text) transition-colors hover:text-(--color-primary)"
        >
          <Radar size={20} className="shrink-0 text-(--color-primary)" />
          <span className="truncate text-sm font-semibold tracking-tight">MineralRadar</span>
        </Link>
      </div>

      <div className="flex min-w-0 max-w-[min(560px,calc(100vw-14rem))] justify-center justify-self-center overflow-x-auto px-2 sm:max-w-[min(560px,calc(100vw-18rem))]">
        {centerContent}
      </div>

      <div className="flex min-w-0 items-center justify-self-end">
        <ThemeToggle />
      </div>
    </header>
  )
}
