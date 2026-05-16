import * as React from 'react'
import { cn } from '@/lib/cn'

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => {
  return (
    <input
      ref={ref}
      className={cn(
        'h-10 w-full rounded-md border border-(--color-border) bg-(--color-surface) px-3 text-sm text-(--color-text) outline-none transition-colors placeholder:text-(--color-text-muted) focus:border-zinc-500 dark:focus:border-zinc-400',
        className,
      )}
      {...props}
    />
  )
})

Input.displayName = 'Input'
