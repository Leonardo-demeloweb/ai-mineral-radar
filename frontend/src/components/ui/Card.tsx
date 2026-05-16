import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'rounded-lg border border-(--color-border) bg-(--color-surface) p-4 shadow-sm',
        className,
      )}
      {...props}
    />
  )
}
