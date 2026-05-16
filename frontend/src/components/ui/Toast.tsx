import * as ToastPrimitive from '@radix-ui/react-toast'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'

export const ToastProvider = ToastPrimitive.Provider

export function ToastViewport({
  className,
  ...props
}: ToastPrimitive.ToastViewportProps) {
  return (
    <ToastPrimitive.Viewport
      className={cn(
        'fixed bottom-4 right-4 z-[100] flex w-full max-w-sm flex-col gap-2',
        className,
      )}
      {...props}
    />
  )
}

export function Toast({
  className,
  ...props
}: ToastPrimitive.ToastProps) {
  return (
    <ToastPrimitive.Root
      className={cn(
        'rounded-lg border border-(--color-border) bg-(--color-surface) p-4 shadow-lg',
        className,
      )}
      {...props}
    />
  )
}

export const ToastTitle = ({
  className,
  ...props
}: ToastPrimitive.ToastTitleProps) => (
  <ToastPrimitive.Title
    className={cn('text-sm font-semibold text-(--color-text)', className)}
    {...props}
  />
)

export const ToastDescription = ({
  className,
  ...props
}: ToastPrimitive.ToastDescriptionProps) => (
  <ToastPrimitive.Description
    className={cn('mt-1 text-sm text-(--color-text-muted)', className)}
    {...props}
  />
)

export const ToastClose = ({
  className,
  ...props
}: ToastPrimitive.ToastCloseProps) => (
  <ToastPrimitive.Close
    className={cn(
      'absolute right-2 top-2 rounded p-1 text-(--color-text-muted) hover:text-(--color-text)',
      className,
    )}
    {...props}
  >
    <X size={14} />
  </ToastPrimitive.Close>
)
