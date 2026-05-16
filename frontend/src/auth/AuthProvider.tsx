import { MsalProvider } from '@azure/msal-react'
import { useEffect, useState, type PropsWithChildren } from 'react'
import {
  initializeMsal,
  isAuthMock,
  isAzureConfigured,
  msalInstance,
} from '@/auth/authConfig'

export function AuthProvider({ children }: PropsWithChildren) {
  const [ready, setReady] = useState(isAuthMock || !isAzureConfigured)

  useEffect(() => {
    if (isAuthMock || !isAzureConfigured) {
      setReady(true)
      return
    }
    let cancelled = false
    initializeMsal()
      .catch((err) => {
        console.error('[MSAL] initialize failed', err)
      })
      .finally(() => {
        if (!cancelled) setReady(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-sm text-zinc-400">
        A concluir login…
      </div>
    )
  }

  if (isAuthMock || !isAzureConfigured) {
    return children
  }

  return <MsalProvider instance={msalInstance}>{children}</MsalProvider>
}
