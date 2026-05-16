import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

interface DrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  children: ReactNode
  side?: 'left' | 'right'
}

const sideStyles = {
  left: 'left-0 top-0 h-full w-[420px] animate-slide-in-left',
  right: 'right-0 top-0 h-full w-[420px] animate-slide-in-right',
}

export function Drawer({
  open,
  onOpenChange,
  title,
  children,
  side = 'right',
}: DrawerProps) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 bg-zinc-900/40" />
        <DialogPrimitive.Content
          className={cn(
            'fixed bg-(--color-surface) p-5 shadow-xl',
            sideStyles[side],
          )}
        >
          <div className="mb-4 flex items-center justify-between">
            <DialogPrimitive.Title className="text-base font-semibold text-(--color-text)">
              {title}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close className="rounded p-1 text-(--color-text-muted) hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-(--color-text)">
              <X size={16} />
            </DialogPrimitive.Close>
          </div>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
