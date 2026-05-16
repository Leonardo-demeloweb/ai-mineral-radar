import {
  EventType,
  PublicClientApplication,
  type Configuration,
  type EventMessage,
} from '@azure/msal-browser'

const clientId = (import.meta.env.VITE_AZURE_CLIENT_ID as string | undefined)?.trim() ?? ''
const tenantId = (import.meta.env.VITE_AZURE_TENANT_ID as string | undefined)?.trim() ?? ''
const apiScope =
  (import.meta.env.VITE_AZURE_API_SCOPE as string | undefined)?.trim() ||
  'api://supplyradar/.default'

export const isAuthMock =
  (import.meta.env.VITE_AUTH_MOCK as string | undefined)?.toLowerCase() !== 'false'

export const isAzureConfigured = Boolean(clientId && tenantId)

const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: window.location.origin,
    postLogoutRedirectUri: window.location.origin,
    navigateToLoginRequestUrl: true,
  },
  cache: {
    cacheLocation: 'sessionStorage',
    storeAuthStateInCookie: false,
  },
}

export const msalInstance = new PublicClientApplication(msalConfig)

msalInstance.addEventCallback((event: EventMessage) => {
  if (event.eventType === EventType.LOGIN_FAILURE && event.error) {
    console.error('[MSAL] login failure', event.error)
  }
})

export const loginRequest = {
  scopes: [apiScope],
}

export async function initializeMsal(): Promise<void> {
  if (isAuthMock || !isAzureConfigured) return
  await msalInstance.initialize()
  await msalInstance.handleRedirectPromise()
}
