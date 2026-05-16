/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string
  readonly VITE_AUTH_MOCK: string
  readonly VITE_AZURE_CLIENT_ID: string
  readonly VITE_AZURE_TENANT_ID: string
  readonly VITE_AZURE_API_SCOPE: string
  readonly VITE_AZURE_MAPS_KEY: string
  readonly VITE_MAPLIBRE_STYLE: string
  readonly VITE_GOOGLE_MAPS_KEY: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
