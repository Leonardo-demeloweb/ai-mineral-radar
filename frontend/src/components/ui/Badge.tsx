import type { HTMLAttributes } from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/cn'

const badgeVariants = cva(
  'inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium',
  {
    variants: {
      variant: {
        default: 'bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300',
        success: 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400',
        warning: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400',
        danger: 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400',
        info: 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

export type BadgeProps = HTMLAttributes<HTMLSpanElement> &
  VariantProps<typeof badgeVariants>

export function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <span
      className={cn(badgeVariants({ variant, className }))}
      {...props}
    />
  )
}
