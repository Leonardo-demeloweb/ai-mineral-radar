import { Link, useLocation } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'

const labels: Record<string, string> = {
  workspace: 'Workspace',
  projetos: 'Projetos',
  novo: 'Novo Projeto',
  dashboard: 'Dashboard',
  analises: 'Análises',
}

export function Breadcrumb() {
  const location = useLocation()
  const segments = location.pathname.split('/').filter(Boolean)

  if (segments.length <= 1) return null

  return (
    <nav className="flex items-center gap-1 text-sm text-(--color-text-muted)">
      {segments.map((segment, idx) => {
        const path = '/' + segments.slice(0, idx + 1).join('/')
        const label = labels[segment] ?? segment
        const isLast = idx === segments.length - 1

        return (
          <span key={path} className="flex items-center gap-1">
            {idx > 0 && <ChevronRight size={12} />}
            {isLast ? (
              <span className="font-medium text-(--color-text)">{label}</span>
            ) : (
              <Link to={path} className="hover:text-(--color-text)">
                {label}
              </Link>
            )}
          </span>
        )
      })}
    </nav>
  )
}
