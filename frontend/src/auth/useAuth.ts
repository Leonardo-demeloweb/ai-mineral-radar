import {
  EventType,
  type AccountInfo,
  type EventMessage,
} from '@azure/msal-browser'
import { useCallback, useEffect, useState } from 'react'
import {
  isAuthMock,
  isAzureConfigured,
  loginRequest,
  msalInstance,
} from '@/auth/authConfig'

function readAccounts(): AccountInfo[] {
  if (!isAzureConfigured) return []
  return msalInstance.getAllAccounts()
}

export function useAuth() {
  const [accounts, setAccounts] = useState<AccountInfo[]>(readAccounts)

  useEffect(() => {
    if (!isAzureConfigured) return

    const sync = () => setAccounts(readAccounts())
    const callbackId = msalInstance.addEventCallback((event: EventMessage) => {
      if (
        event.eventType === EventType.LOGIN_SUCCESS ||
        event.eventType === EventType.ACQUIRE_TOKEN_SUCCESS ||
        event.eventType === EventType.SSO_SILENT_SUCCESS
      ) {
        sync()
      }
      if (event.eventType === EventType.LOGOUT_SUCCESS) {
        setAccounts([])
      }
    })

    sync()
    return () => {
      if (callbackId) msalInstance.removeEventCallback(callbackId)
    }
  }, [])

  const isAuthenticated =
    isAuthMock || !isAzureConfigured || accounts.length > 0

  const login = useCallback(() => {
    if (isAuthMock || !isAzureConfigured) return
    void msalInstance.loginRedirect(loginRequest)
  }, [])

  const logout = useCallback(() => {
    if (isAuthMock || !isAzureConfigured) return
    const account = accounts[0]
    void msalInstance.logoutRedirect(account ? { account } : undefined)
  }, [accounts])

  return {
    isAuthenticated,
    login,
    logout,
    account: accounts[0] ?? null,
    isMock: isAuthMock,
    azureConfigured: isAzureConfigured,
  }
}
