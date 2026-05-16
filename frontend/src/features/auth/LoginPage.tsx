import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card } from '@/components/ui'
import { isAuthMock, isAzureConfigured } from '@/auth/authConfig'
import { useAuth } from '@/auth/useAuth'

export function LoginPage() {
  const { isAuthenticated, login, azureConfigured } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (isAuthenticated) {
      navigate('/workspace', { replace: true })
    }
  }, [isAuthenticated, navigate])

  const configHint = !isAuthMock && !azureConfigured

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50 p-6">
      <Card className="w-full max-w-md space-y-4">
        <h1 className="text-xl font-semibold text-zinc-900">Entrar no MineralRadar</h1>
        {configHint ? (
          <p className="text-sm text-amber-700">
            Azure AD não está configurado no frontend (VITE_AZURE_CLIENT_ID e
            VITE_AZURE_TENANT_ID). Defina VITE_AUTH_MOCK=true para desenvolvimento
            com dev-token, ou preencha as variáveis Azure.
          </p>
        ) : isAuthMock ? (
          <p className="text-sm text-zinc-600">
            Modo desenvolvimento (VITE_AUTH_MOCK). A API aceita Bearer dev-token
            se AZURE_AD_ALLOW_DEV_BEARER=true no backend.
          </p>
        ) : (
          <p className="text-sm text-zinc-600">
            Use sua conta corporativa Microsoft (Andrade Gutierrez).
          </p>
        )}
        <Button
          className="w-full"
          onClick={() => {
            if (isAuthMock || !azureConfigured) {
              navigate('/workspace')
              return
            }
            login()
          }}
        >
          Entrar com Microsoft
        </Button>
      </Card>
    </div>
  )
}
