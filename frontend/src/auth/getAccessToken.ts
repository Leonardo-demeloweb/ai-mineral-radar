import {
  InteractionRequiredAuthError,
  type AccountInfo,
} from '@azure/msal-browser'
import { isAuthMock, isAzureConfigured, loginRequest, msalInstance } from '@/auth/authConfig'

export async function getAccessToken(): Promise<string | null> {
  if (isAuthMock) return 'dev-token'

  if (!isAzureConfigured) return null

  const accounts = msalInstance.getAllAccounts()
  if (accounts.length === 0) return null

  const account: AccountInfo = accounts[0]
  try {
    const result = await msalInstance.acquireTokenSilent({
      ...loginRequest,
      account,
    })
    return result.accessToken
  } catch (err) {
    if (err instanceof InteractionRequiredAuthError) {
      await msalInstance.acquireTokenRedirect({ ...loginRequest, account })
      return null
    }
    throw err
  }
}
