import { Suspense } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { Header } from '@/components/layout/Header'
import { Sidebar } from '@/components/layout/Sidebar'
import { Skeleton } from '@/components/ui'

function PageSkeleton() {
  return (
    <div className="space-y-4 p-6">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-4 w-72" />
      <div className="grid gap-4 sm:grid-cols-3 pt-2">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
      <Skeleton className="h-64 w-full" />
    </div>
  )
}

export function AppLayout() {
  const location = useLocation()
  const isWorkspace = location.pathname.startsWith('/workspace')

  return (
    <div className="flex h-screen overflow-hidden bg-(--color-background)">
      <Sidebar />
      <main className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <section className={isWorkspace ? 'min-h-0 flex-1 overflow-hidden' : 'flex-1 overflow-auto p-6'}>
          <Suspense fallback={<PageSkeleton />}>
            <Outlet />
          </Suspense>
        </section>
      </main>
    </div>
  )
}
