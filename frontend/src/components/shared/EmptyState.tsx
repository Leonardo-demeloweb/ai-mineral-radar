import type { ReactNode } from 'react'

interface EmptyStateProps {
  title: string
  description?: string
  action?: ReactNode
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="rounded-lg border border-dashed border-(--color-border) bg-(--color-surface) p-8 text-center">
      <p className="text-base font-medium text-(--color-text)">{title}</p>
      {description ? <p className="mt-2 text-sm text-(--color-text-muted)">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  )
}
