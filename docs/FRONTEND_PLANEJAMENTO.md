# MineralRadar 2.0 — Planejamento Frontend

## Sumário

1. [Visão Geral](#1-visão-geral)
2. [Stack Tecnológica](#2-stack-tecnológica)
3. [Estrutura de Diretórios](#3-estrutura-de-diretórios)
4. [Roteamento e Páginas](#4-roteamento-e-páginas)
5. [Layout e Design System](#5-layout-e-design-system)
6. [Mapa Interativo](#6-mapa-interativo)
   - [Azure Maps — Routing, Geocoding e Serviços Geoespaciais](#azure-maps--routing-geocoding-e-serviços-geoespaciais)
7. [Chat com IA (Agentic RAG)](#7-chat-com-ia-agentic-rag)
8. [Gerenciamento de Estado](#8-gerenciamento-de-estado)
9. [Integração com Backend](#9-integração-com-backend)
10. [Autenticação (Azure AD)](#10-autenticação-azure-ad)
11. [Páginas Detalhadas](#11-páginas-detalhadas)
12. [Performance e Otimizações](#12-performance-e-otimizações)
13. [Cronograma de Implementação](#13-cronograma-de-implementação)
14. [Decisões Arquiteturais](#14-decisões-arquiteturais)

---

## 1. Visão Geral

### Objetivo

Construir uma SPA moderna para inteligência mineral, centrada em **duas experiências principais**:

1. **Chat conversacional com IA** — o usuário pergunta em linguagem natural e o agente busca, filtra e apresenta dados de jazidas/empresas
2. **Mapa interativo** — visualização geoespacial dos resultados, polígonos, rotas, overlayers KML

### Público-Alvo

| Persona | Uso Principal |
|---------|---------------|
| Engenheiro de Suprimentos | Buscar jazidas e fornecedores próximos à obra |
| Comprador | Comparar fornecedores, analisar custo logístico |
| Analista de Compliance | Due diligence: verificar CNPJ, sócios, situação cadastral |
| Gestor de Projetos | Acompanhar estudos, aprovar fornecedores |

### Princípios de UX

- **Chat-first**: A interação primária é conversacional — o mapa reage ao chat
- **Map-aware**: Respostas do agente sempre que possível incluem representação geográfica
- **Progressive disclosure**: Resultados resumidos no chat, detalhes em modais/drawers
- **Responsive**: Desktop-first (foco profissional), mas funcional em tablet

---

## 2. Stack Tecnológica

### Core

| Camada | Tecnologia | Versão | Justificativa |
|--------|------------|--------|---------------|
| Framework | **React** | 19+ | Ecosystem, concurrent features |
| Build | **Vite** | 6+ | HMR rápido, ESBuild, tree-shaking |
| Linguagem | **TypeScript** | 5.x | Type safety, DX, refactoring |
| Routing | **React Router** | 7 | Nested routes, lazy loading, loaders |

### UI

| Camada | Tecnologia | Justificativa |
|--------|------------|---------------|
| Primitivos | **Radix UI** | Acessibilidade (WAI-ARIA), headless |
| Estilização | **Tailwind CSS 4** | Utility-first, design tokens, dark mode |
| Ícones | **Lucide React** | Tree-shakeable, consistente |
| Animações | **Framer Motion** | Transições, layout animations |

### Mapa

| Camada | Tecnologia | Justificativa |
|--------|------------|---------------|
| Renderização | **MapLibre GL JS** | Open-source, WebGL, gratuito |
| React wrapper | **react-map-gl** (v7) | Hooks, controle declarativo |
| Tiles | **OpenStreetMap** (raster) ou **MapTiler** (vector) | Gratuito / free tier |
| Overlayers 3D | **Deck.gl** (opcional, fase futura) | Heatmaps, milhões de pontos |
| Rotas | **Azure Maps Route Service** | Truck routing nativo, ~10x mais barato que Google, mesmo tenant Azure |
| Geocoding | **Azure Maps Search** | Fuzzy search, reverse geocoding, batch, mesmo subscription key |
| Street View | **Google Street View API** | Único provedor de imagens de rua (Azure Maps não tem equivalente) |
| Isócronas | **Azure Maps Route Range** (fase futura) | Polígonos de alcance por tempo/distância para caminhão |

### Estado e Data Fetching

| Camada | Tecnologia | Justificativa |
|--------|------------|---------------|
| Estado global | **Zustand** | Simples, sem boilerplate, middleware |
| Server state | **TanStack Query** (v5) | Cache, revalidation, optimistic updates |
| Chat streaming | **Vercel AI SDK** (`ai` + `@ai-sdk/react`) | `useChat`, streaming SSE nativo, tool calls |

### HTTP e Auth

| Camada | Tecnologia | Justificativa |
|--------|------------|---------------|
| HTTP Client | **ky** | Lightweight, retry, hooks, TypeScript |
| Auth | **MSAL React** (`@azure/msal-react`) | Azure AD SSO corporativo |

### Qualidade

| Camada | Tecnologia | Justificativa |
|--------|------------|---------------|
| Lint | **ESLint** (flat config) + **Prettier** | Consistência de código |
| Test | **Vitest** + **Testing Library** | Rápido, compatível com Vite |
| E2E | **Playwright** | Cross-browser, paralelo |

---

## 3. Estrutura de Diretórios

```
frontend/
├── index.html
├── package.json
├── tsconfig.json
├── tailwind.config.ts
├── vite.config.ts
├── public/
│   ├── favicon.svg
│   └── og-image.png
├── src/
│   ├── main.tsx                     # Entry point
│   ├── App.tsx                      # Router setup
│   ├── vite-env.d.ts
│   │
│   ├── assets/                      # Imagens, SVGs estáticos
│   │   └── logo.svg
│   │
│   ├── auth/                        # Azure AD (MSAL)
│   │   ├── AuthProvider.tsx         # MsalProvider wrapper
│   │   ├── authConfig.ts           # Client IDs, scopes, redirect URIs
│   │   ├── useAuth.ts              # Hook: login, logout, token
│   │   └── ProtectedRoute.tsx      # Guard component
│   │
│   ├── components/                  # Componentes reutilizáveis (UI pura)
│   │   ├── ui/                     # Design system (Radix + Tailwind)
│   │   │   ├── Button.tsx
│   │   │   ├── Dialog.tsx
│   │   │   ├── Drawer.tsx
│   │   │   ├── DropdownMenu.tsx
│   │   │   ├── Input.tsx
│   │   │   ├── Select.tsx
│   │   │   ├── Badge.tsx
│   │   │   ├── Card.tsx
│   │   │   ├── Table.tsx
│   │   │   ├── Tabs.tsx
│   │   │   ├── Toast.tsx
│   │   │   ├── Tooltip.tsx
│   │   │   ├── Skeleton.tsx
│   │   │   └── index.ts
│   │   │
│   │   ├── layout/                 # Layout shell
│   │   │   ├── AppLayout.tsx       # Sidebar + main area
│   │   │   ├── Sidebar.tsx         # Navegação lateral
│   │   │   ├── Header.tsx          # Top bar: user, theme, breadcrumb
│   │   │   ├── ThemeToggle.tsx     # Light/dark mode
│   │   │   └── Breadcrumb.tsx
│   │   │
│   │   ├── map/                    # Mapa (MapLibre)
│   │   │   ├── MapContainer.tsx    # Wrapper react-map-gl
│   │   │   ├── MapMarkers.tsx      # Cluster de marcadores
│   │   │   ├── MapPolygons.tsx     # Polígonos de jazidas
│   │   │   ├── MapRoutes.tsx       # Linhas de rota Azure Maps Route
│   │   │   ├── MapKmlOverlay.tsx   # KML/GeoJSON importados
│   │   │   ├── MapControls.tsx     # Zoom, layers, fullscreen
│   │   │   ├── MapPopup.tsx        # Info popup ao clicar
│   │   │   ├── MapLegend.tsx       # Legenda de camadas
│   │   │   └── useMapViewport.ts   # Hook: viewport state
│   │   │
│   │   ├── chat/                   # Interface de Chat com IA
│   │   │   ├── ChatPanel.tsx       # Container principal
│   │   │   ├── ChatInput.tsx       # Input + botão de envio
│   │   │   ├── ChatMessages.tsx    # Lista de mensagens (scroll)
│   │   │   ├── MessageBubble.tsx   # Bubble individual (user/assistant)
│   │   │   ├── ToolCallCard.tsx    # Cartão visual de tool call
│   │   │   ├── ResultsTable.tsx    # Tabela de resultados inline
│   │   │   ├── StreamingDots.tsx   # Indicador de typing
│   │   │   └── ChatSuggestions.tsx # Chips de sugestão
│   │   │
│   │   └── shared/                 # Componentes comuns ao domínio
│   │       ├── JazidaCard.tsx      # Card de jazida (resumo)
│   │       ├── EmpresaCard.tsx     # Card de empresa (resumo)
│   │       ├── FornecedorBadge.tsx # Badge ANM / CNPJ / Manual
│   │       ├── DistanceBadge.tsx   # Badge "12.4 km"
│   │       ├── StatusBadge.tsx     # Status ativo/inativo
│   │       ├── CnpjFormatter.tsx   # Formatação 00.000.000/0000-00
│   │       ├── EmptyState.tsx      # Placeholder vazio
│   │       └── ErrorBoundary.tsx   # Error boundary genérico
│   │
│   ├── features/                   # Feature modules (pages + lógica)
│   │   ├── obras/
│   │   │   ├── ObrasList.tsx       # Página: listagem de obras
│   │   │   ├── ObraDetail.tsx      # Página: detalhe da obra + estudos
│   │   │   ├── ObraForm.tsx        # Form criar/editar obra
│   │   │   ├── useObras.ts         # Hook TanStack Query
│   │   │   └── obras.types.ts      # Tipos TS da feature
│   │   │
│   │   ├── estudos/
│   │   │   ├── EstudoDetail.tsx    # Página: detalhe do estudo
│   │   │   ├── EstudoForm.tsx      # Form criar/editar estudo
│   │   │   ├── FornecedoresList.tsx # Lista fornecedores selecionados
│   │   │   ├── FornecedorDetail.tsx # Drawer/modal com detalhe
│   │   │   ├── KmlUpload.tsx       # Upload KML/GeoJSON
│   │   │   ├── useEstudos.ts       # Hook TanStack Query
│   │   │   └── estudos.types.ts    # Tipos TS da feature
│   │   │
│   │   ├── workspace/              # Tela principal: Chat + Mapa
│   │   │   ├── WorkspacePage.tsx   # Layout split: chat | mapa
│   │   │   ├── useWorkspace.ts     # Hook: coordenação chat ↔ mapa
│   │   │   └── workspace.types.ts
│   │   │
│   │   ├── detalhes/               # Modais de detalhe
│   │   │   ├── JazidaDetailModal.tsx  # Detalhe completo jazida
│   │   │   ├── EmpresaDetailModal.tsx # Detalhe completo empresa
│   │   │   ├── SociosTable.tsx        # Tabela de sócios
│   │   │   ├── StreetView.tsx         # Embed Street View
│   │   │   └── ProcessoTimeline.tsx   # Timeline de eventos ANM
│   │   │
│   │   └── dashboard/
│   │       ├── DashboardPage.tsx   # Métricas e resumos
│   │       └── StatCard.tsx        # Card de métrica
│   │
│   ├── hooks/                      # Hooks globais
│   │   ├── useAzureRoute.ts       # Azure Maps Route (truck routing)
│   │   ├── useAzureGeocode.ts     # Azure Maps Search (fuzzy + reverse)
│   │   ├── useDebounce.ts
│   │   ├── useMediaQuery.ts
│   │   ├── useLocalStorage.ts
│   │   └── useGeolocation.ts
│   │
│   ├── lib/                        # Utilitários e clients
│   │   ├── api.ts                  # ky instance configurado
│   │   ├── constants.ts            # Env vars, URLs
│   │   ├── formatters.ts           # CNPJ, telefone, moeda, data
│   │   ├── geo.ts                  # Helpers GeoJSON
│   │   └── cn.ts                   # clsx + tailwind-merge
│   │
│   ├── stores/                     # Zustand stores
│   │   ├── mapStore.ts             # Viewport, layers, selected features
│   │   ├── chatStore.ts            # Conversas, contexto
│   │   └── uiStore.ts             # Sidebar open, theme, modals
│   │
│   └── types/                      # Tipos globais
│       ├── api.ts                  # DTOs do backend (espelhados)
│       ├── geojson.ts              # GeoJSON tipado
│       └── chat.ts                 # Mensagens, tool calls, ações
│
├── tests/
│   ├── setup.ts
│   ├── components/
│   └── features/
│
└── e2e/
    ├── playwright.config.ts
    └── specs/
```

---

## 4. Roteamento e Páginas

### Mapa de Rotas

```
/                           → Redirect para /workspace ou /obras
├── /login                  → Página de login (Azure AD)
├── /workspace              → ★ Tela principal: Chat + Mapa (split view)
├── /obras                  → Lista de obras
│   ├── /obras/nova         → Criar obra
│   └── /obras/:id          → Detalhe da obra
│       ├── /obras/:id/estudos/novo   → Criar estudo
│       └── /obras/:id/estudos/:eid   → Detalhe do estudo
├── /dashboard              → Dashboard com métricas
└── /configuracoes          → Configurações do usuário (futuro)
```

### Definição em React Router

```tsx
// src/App.tsx
const router = createBrowserRouter([
  {
    element: <ProtectedRoute />,       // Guard Azure AD
    children: [
      {
        element: <AppLayout />,         // Sidebar + Header
        children: [
          { index: true, element: <Navigate to="/workspace" /> },
          { path: "workspace", element: <WorkspacePage /> },
          { path: "obras", element: <ObrasList /> },
          { path: "obras/nova", element: <ObraForm /> },
          { path: "obras/:id", element: <ObraDetail /> },
          { path: "obras/:id/estudos/novo", element: <EstudoForm /> },
          { path: "obras/:id/estudos/:eid", element: <EstudoDetail /> },
          { path: "dashboard", element: <DashboardPage /> },
        ],
      },
    ],
  },
  { path: "login", element: <LoginPage /> },
]);
```

### Navegação Sidebar

```
┌──────────────────────────┐
│  🔍 MineralRadar          │
│  ─────────────────────── │
│  💬 Workspace             │  ← Chat + Mapa (tela principal)
│  🏗️ Obras                │  ← CRUD obras
│  📊 Dashboard             │  ← Métricas
│  ─────────────────────── │
│  ⚙️ Configurações        │  (futuro)
│  🚪 Sair                  │
└──────────────────────────┘
```

---

## 5. Layout e Design System

### Layout Principal (AppLayout)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  [Sidebar]  │                    [Main Content Area]                         │
│             │  ┌─────────────────────────────────────────────────────────┐  │
│  Logo       │  │ Header: Breadcrumb • User Avatar • Theme Toggle         │  │
│  ────────── │  ├─────────────────────────────────────────────────────────┤  │
│  💬 Work.   │  │                                                         │  │
│  🏗️ Obras   │  │             <Outlet /> (Router content)                 │  │
│  📊 Dash.   │  │                                                         │  │
│             │  │                                                         │  │
│             │  │                                                         │  │
│  ────────── │  │                                                         │  │
│  ⚙️ Config  │  │                                                         │  │
│  🚪 Sair    │  └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Layout Workspace (Chat + Mapa — Split View)

```
┌───────────────────────────────────────────────────────────────────────┐
│  [Chat Panel — 40%]          │  [Mapa — 60%]                          │
│  ┌─────────────────────────┐ │  ┌──────────────────────────────────┐  │
│  │ ┌─────────────────────┐ │ │  │                                  │  │
│  │ │ "Encontre jazidas   │ │ │  │       🗺️ MapLibre GL JS           │  │
│  │ │  de areia em 50km   │ │ │  │                                  │  │
│  │ │  de Guarulhos"      │ │ │  │  📍 📍 📍 marcadores              │  │
│  │ └─────────────────────┘ │ │  │  ▓▓▓▓ polígonos jazidas          │  │
│  │ ┌─────────────────────┐ │ │  │  ─── rotas Azure Maps (truck)   │  │
│  │ │ 🤖 Encontrei 23     │ │ │  │                                  │  │
│  │ │ jazidas...           │ │ │  │  ┌──────────┐                   │  │
│  │ │ ┌─────────────────┐ │ │ │  │  │ Legenda  │                   │  │
│  │ │ │ 📋 Tabela inline│ │ │ │  │  │ Controls │                   │  │
│  │ │ │  Processo  Subs  │ │ │ │  │  └──────────┘                   │  │
│  │ │ │  832.145   Areia │ │ │ │  │                                  │  │
│  │ │ └─────────────────┘ │ │ │  └──────────────────────────────────┘  │
│  │ └─────────────────────┘ │ │                                        │
│  │ ┌─────────────────────┐ │ │  [Drag handle para resize]            │
│  │ │ 💬 input message... │ │ │                                        │
│  │ └─────────────────────┘ │ │                                        │
│  └─────────────────────────┘ │                                        │
└───────────────────────────────────────────────────────────────────────┘
```

**Comportamentos**:
- **Resizable**: drag handle entre painel de chat e mapa
- **Collapsible**: botão para colapsar chat (mapa fullscreen) ou mapa (chat fullscreen)
- **Responsive**: em telas < 1024px, usar tabs (Chat | Mapa) ao invés de split

### Tema

| Token | Light | Dark |
|-------|-------|------|
| Background | `#FAFAFA` | `#0A0A0A` |
| Surface | `#FFFFFF` | `#171717` |
| Border | `#E5E5E5` | `#2D2D2D` |
| Primary | `#2563EB` (blue-600) | `#3B82F6` (blue-500) |
| Text primary | `#171717` | `#FAFAFA` |
| Text muted | `#737373` | `#A3A3A3` |

Persistência: `localStorage` + `prefers-color-scheme` como fallback.

---

## 6. Mapa Interativo

### Camadas (Layers)

| # | Layer | Fonte | Cor Default | Toggle |
|---|-------|-------|-------------|--------|
| 1 | **Pontos de jazidas** | Tool call `buscar_jazidas` | 🟢 Verde | ✅ |
| 2 | **Polígonos de jazidas** | Tool call `detalhes_processo` | 🟢 Verde (fill 20%) | ✅ |
| 3 | **Pontos de empresas** | Tool call `buscar_empresas` | 🔵 Azul | ✅ |
| 4 | **Limites municipais** | `ibge_municipio_v001` | ⚪ Cinza (stroke) | ✅ |
| 5 | **Rotas Azure Maps** | Azure Maps Route Service (truck) | 🟡 Amarelo (line) | ✅ |
| 6 | **Overlays KML** | Upload do usuário | 🟠 Laranja (customizável) | ✅ |
| 7 | **Raio de busca** | Parâmetro do chat | ⚪ Cinza (circle, dashed) | ✅ |
| 8 | **Ponto da obra** | Coordenadas da obra | 🔴 Vermelho | ✅ |
| 9 | **Isócrona (futuro)** | Azure Maps Route Range | 🟣 Roxo (fill 15%) | ✅ |

### Interações

```
Marcador (ponto)
  → Hover:  Tooltip com nome/processo
  → Click:  Popup com resumo + botão "Ver detalhes"
  → "Ver detalhes": abre modal JazidaDetailModal ou EmpresaDetailModal

Polígono
  → Hover:  Highlight (opacity +20%)
  → Click:  Popup com informações da área

Cluster (zoom out)
  → Click:  Zoom in para desagrupar
  → Badge:  Número de pontos no cluster
```

### Viewport Control via Chat

Quando o agente responde com dados geolocalizados, o frontend deve:

1. Receber o array de pontos no response
2. Calcular bounding box (`LngLatBounds`)
3. Animar `flyTo` para o bbox com padding
4. Adicionar marcadores/polígonos ao layer correspondente

```typescript
// Exemplo: chat responde com jazidas
const handleToolCallResult = (toolName: string, data: ToolCallData) => {
  if (toolName === 'buscar_jazidas' && data.mapa?.pontos) {
    mapStore.setJazidaMarkers(data.mapa.pontos);
    mapStore.fitBounds(calculateBBox(data.mapa.pontos));
  }
  if (toolName === 'buscar_empresas' && data.mapa?.pontos) {
    mapStore.setEmpresaMarkers(data.mapa.pontos);
    mapStore.fitBounds(calculateBBox(data.mapa.pontos));
  }
};
```

### Azure Maps — Routing, Geocoding e Serviços Geoespaciais

#### Justificativa da Escolha

| Critério | Azure Maps | Google Maps | OSRM |
|----------|-----------|-------------|------|
| **Custo routing** | $0.50/1K requests | $5.00/1K requests | Gratuito (self-hosted) |
| **Truck routing** | ✅ Nativo (dimensões, peso, carga perigosa) | ❌ Não tem | ❌ Não tem |
| **Integração Azure** | ✅ Mesmo tenant/subscription | ❌ GCP separado | N/A (infra própria) |
| **Auth** | Subscription key ou Azure AD (já temos) | API key separada | N/A |
| **Geocoding Brasil** | ✅ Bom (TomTom data) | ✅ Excelente | N/A |
| **SLA** | 99.9% (Azure) | 99.9% | Depende do self-host |
| **Isócronas** | ✅ Route Range API | ✅ Distance Matrix (caro) | ✅ via plugin |

**Decisão**: Azure Maps para routing + geocoding. MapLibre continua como renderizador de mapa (tiles OSM/MapTiler). Google Maps **apenas** para Street View (sem equivalente Azure).

#### APIs Consumidas

**1. Route Directions (Truck)**

```
POST https://atlas.microsoft.com/route/directions/json
  ?api-version=1.0
  &subscription-key={key}
  &travelMode=truck
  &vehicleWeight=40000         // 40 toneladas (caminhão de minério)
  &vehicleHeight=4.5           // metros
  &vehicleWidth=2.6
  &vehicleLength=18.75         // carreta
  &vehicleAxleWeight=12000     // peso por eixo (kg)
  &vehicleLoadType=otherHazmatGeneral  // carga perigosa (opcional)
  &avoid=tollRoads             // evitar pedágios (opcional)
  &routeRepresentation=polyline
  &query={originLat},{originLon}:{destLat},{destLon}
```

**Response relevante**:
- `routes[0].summary.lengthInMeters` — distância total
- `routes[0].summary.travelTimeInSeconds` — tempo estimado
- `routes[0].legs[0].points[]` — polyline para renderizar no MapLibre
- `routes[0].summary.trafficDelayInSeconds` — atraso por tráfego

**2. Route Range (Isócrona)**

```
POST https://atlas.microsoft.com/route/range/json
  ?api-version=1.0
  &subscription-key={key}
  &travelMode=truck
  &timeBudgetInSec=3600        // 1 hora de viagem
  &query={lat},{lon}
```

Retorna um polígono GeoJSON com a área alcançável em X minutos/km — útil para:
- "Quais jazidas estão a até 2h de caminhão da obra?"
- Visualização de cobertura logística no mapa

**3. Search (Geocoding)**

```
GET https://atlas.microsoft.com/search/fuzzy/json
  ?api-version=1.0
  &subscription-key={key}
  &query=Guarulhos, SP
  &countrySet=BR
  &language=pt-BR
  &limit=5
```

Substitui Nominatim (OSM). Vantagens: fuzzy matching, POI search, batch geocoding.

**4. Search Reverse (Reverse Geocoding)**

```
GET https://atlas.microsoft.com/search/address/reverse/json
  ?api-version=1.0
  &subscription-key={key}
  &query={lat},{lon}
```

Usado quando o usuário clica no mapa → resolve coordenada para endereço.

#### Arquitetura de Integração

```
MapLibre GL JS (renderização)              Azure Maps REST (serviços)
┌──────────────────────────┐              ┌──────────────────────────┐
│  Tiles: OSM / MapTiler   │              │  Route Directions (truck)│
│  Layers: GeoJSON sources │◄─polyline───│  Route Range (isócrona)  │
│  Markers: react-map-gl   │              │  Search (geocoding)      │
│  Controls: zoom, legend  │              │  Search Reverse          │
└──────────────────────────┘              └──────────────────────────┘
         ▲                                          ▲
         │ render                                    │ fetch
         │                                           │
    ┌────┴──────────────────────────────────────────┴────┐
    │            Frontend (React)                         │
    │  useAzureRoute() → fetch Route API → setRouteLayer │
    │  useAzureGeocode() → fetch Search → setMarker      │
    └────────────────────────────────────────────────────┘
```

#### Componente MapRoutes.tsx

```typescript
// components/map/MapRoutes.tsx
import { Source, Layer } from 'react-map-gl/maplibre';
import { useMapStore } from '@/stores/mapStore';

const ROUTE_LAYER_STYLE: LayerProps = {
  id: 'azure-route-line',
  type: 'line',
  paint: {
    'line-color': '#EAB308',  // amarelo
    'line-width': 4,
    'line-opacity': 0.8,
  },
};

export function MapRoutes() {
  const routeLines = useMapStore((s) => s.routeLines);

  if (!routeLines.length) return null;

  const geojson: GeoJSON.FeatureCollection = {
    type: 'FeatureCollection',
    features: routeLines.map((route) => ({
      type: 'Feature',
      properties: {
        distance_km: route.distance_km,
        duration_min: route.duration_min,
        label: route.label,
      },
      geometry: {
        type: 'LineString',
        coordinates: route.points.map((p) => [p.lon, p.lat]),
      },
    })),
  };

  return (
    <Source id="route-source" type="geojson" data={geojson}>
      <Layer {...ROUTE_LAYER_STYLE} />
    </Source>
  );
}
```

#### Hook useAzureRoute

```typescript
// hooks/useAzureRoute.ts
const AZURE_MAPS_URL = 'https://atlas.microsoft.com/route/directions/json';

interface RouteParams {
  origin: { lat: number; lon: number };
  destination: { lat: number; lon: number };
  travelMode?: 'car' | 'truck';
  vehicleWeight?: number;       // kg (default 40000 para caminhão de minério)
  avoidTolls?: boolean;
}

interface RouteResult {
  distance_km: number;
  duration_min: number;
  points: { lat: number; lon: number }[];
  traffic_delay_min: number;
}

export function useAzureRoute() {
  const subscriptionKey = import.meta.env.VITE_AZURE_MAPS_KEY;

  async function getRoute(params: RouteParams): Promise<RouteResult> {
    const { origin, destination, travelMode = 'truck', vehicleWeight = 40000 } = params;

    const query = `${origin.lat},${origin.lon}:${destination.lat},${destination.lon}`;
    const url = new URL(AZURE_MAPS_URL);
    url.searchParams.set('api-version', '1.0');
    url.searchParams.set('subscription-key', subscriptionKey);
    url.searchParams.set('travelMode', travelMode);
    url.searchParams.set('routeRepresentation', 'polyline');
    url.searchParams.set('query', query);

    if (travelMode === 'truck') {
      url.searchParams.set('vehicleWeight', String(vehicleWeight));
      url.searchParams.set('vehicleHeight', '4.5');
      url.searchParams.set('vehicleWidth', '2.6');
      url.searchParams.set('vehicleLength', '18.75');
    }

    if (params.avoidTolls) {
      url.searchParams.set('avoid', 'tollRoads');
    }

    const res = await fetch(url.toString());
    const data = await res.json();
    const route = data.routes[0];

    return {
      distance_km: route.summary.lengthInMeters / 1000,
      duration_min: Math.round(route.summary.travelTimeInSeconds / 60),
      points: route.legs[0].points.map((p: any) => ({ lat: p.latitude, lon: p.longitude })),
      traffic_delay_min: Math.round((route.summary.trafficDelayInSeconds || 0) / 60),
    };
  }

  return { getRoute };
}
```

#### Fluxo Chat → Rota no Mapa

```
1. Usuário: "Qual a rota da obra até a jazida 832.145/2018?"
2. LangGraph → resolve coordenadas da obra e jazida via OpenSearch
3. Backend retorna: { origin: {lat, lon}, destination: {lat, lon}, ... }
4. Frontend chama useAzureRoute().getRoute(origin, destination, 'truck')
5. Azure Maps retorna polyline + distância + tempo
6. Frontend renderiza rota no MapLibre + info no chat:
   "📍 Rota de caminhão: 47.3 km • ~58 min • via BR-381"
```

#### Estimativa de Custo Azure Maps

| Serviço | Preço (S1 tier) | Volume mensal estimado | Custo/mês |
|---------|----------------|----------------------|-----------|
| Route Directions | $0.50 / 1K requests | ~5K routes | ~$2.50 |
| Route Range | $0.50 / 1K requests | ~1K isócronas | ~$0.50 |
| Search (geocoding) | $0.50 / 1K requests | ~10K buscas | ~$5.00 |
| Render (tiles) | Não usado (MapLibre + OSM) | 0 | $0.00 |
| **Total estimado** | | | **~$8.00/mês** |

*Comparativo Google Maps: Route Directions ($5/1K) + Geocoding ($5/1K) = ~$80/mês para mesmo volume.*

---

## 7. Chat com IA (Agentic RAG)

### Arquitetura de Comunicação

```
┌──────────────┐    SSE Stream     ┌──────────────┐    LangGraph    ┌────────────┐
│   Frontend   │ ◄──────────────── │   FastAPI     │ ──────────────► │  LangGraph  │
│  (useChat)   │ ──────────────── ▶│   /api/chat   │                │ Orchestrator│
│              │    POST message    │              │                │            │
└──────────────┘                   └──────────────┘                └────────────┘
                                                                        │
                                                          ┌─────────────┼──────────────┐
                                                          ▼             ▼              ▼
                                                    MCP Jazidas   MCP Empresas   OpenSearch MCP
                                                    (:8010)       (:8011)        (nativo)
```

### Vercel AI SDK — useChat

```typescript
// features/workspace/WorkspacePage.tsx
import { useChat } from '@ai-sdk/react';

const { messages, input, handleInputChange, handleSubmit, isLoading } = useChat({
  api: '/api/v1/chat',
  headers: { Authorization: `Bearer ${token}` },
  body: {
    obra_id: currentObraId,       // Contexto da obra ativa
    estudo_id: currentEstudoId,   // Contexto do estudo (opcional)
  },
  onToolCall: async ({ toolCall }) => {
    // Interceptar tool calls para atualizar mapa
    handleToolCallResult(toolCall.toolName, toolCall.args);
  },
});
```

### Tipos de Mensagem na UI

| Tipo | Renderização |
|------|-------------|
| **user** | Bubble alinhada à direita, cor primária |
| **assistant** (texto) | Bubble à esquerda, markdown renderizado |
| **assistant** (tool_call) | Card visual com ícone da tool + resumo dos resultados |
| **assistant** (mapa) | Atualização automática do mapa (sem bubble extra) |
| **system** | Mensagem cinza centralizada (ex: "Estudo carregado") |

### Tool Call Cards

Quando o agente executa uma tool (ex: `buscar_jazidas`), o frontend renderiza um card expandível:

```
┌──────────────────────────────────────────────────────┐
│ 🔍 buscar_jazidas                              ✅ OK │
│ ────────────────────────────────────────────────────  │
│ Substância: Areia  •  Raio: 50km  •  23 resultados   │
│ ┌──────────────────────────────────────────────────┐ │
│ │ # │ Processo      │ Substância │ Dist.  │ Fase   │ │
│ │ 1 │ 832.145/2018 │ Areia      │ 5.2 km │ Lavra  │ │
│ │ 2 │ 832.200/2020 │ Areia      │ 8.1 km │ Pesq.  │ │
│ │ ... │             │            │        │        │ │
│ └──────────────────────────────────────────────────┘ │
│                                    [📍 Ver no mapa]  │
└──────────────────────────────────────────────────────┘
```

### Sugestões Iniciais

Ao abrir o workspace sem conversa ativa:

```
┌─────────────────────────────────────────┐
│  💬 Como posso ajudar?                   │
│                                          │
│  Sugestões:                              │
│  ┌───────────────────────────────────┐  │
│  │ 🏗️ "Jazidas de areia perto da     │  │
│  │     minha obra"                   │  │
│  ├───────────────────────────────────┤  │
│  │ 🏢 "Empresas de transporte de     │  │
│  │     minérios em MG"              │  │
│  ├───────────────────────────────────┤  │
│  │ 📋 "Detalhes do processo          │  │
│  │     832.145/2018"                │  │
│  ├───────────────────────────────────┤  │
│  │ 👤 "Empresas do sócio João       │  │
│  │     da Silva"                    │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

---

## 8. Gerenciamento de Estado

### Zustand Stores

#### `mapStore.ts`

```typescript
interface MapState {
  // Viewport
  viewport: { lng: number; lat: number; zoom: number };
  setViewport: (v: Partial<MapState['viewport']>) => void;
  flyTo: (lng: number, lat: number, zoom?: number) => void;
  fitBounds: (bbox: [number, number, number, number]) => void;

  // Layers & data
  jazidaMarkers: MapaPonto[];
  empresaMarkers: MapaPontoEmpresa[];
  polygons: GeoJSONFeature[];
  kmlOverlays: KmlOverlay[];
  routeLines: RouteLine[];             // Azure Maps Route polylines
  searchRadius: { center: [number, number]; km: number } | null;

  // Layer visibility
  layerVisibility: Record<LayerId, boolean>;
  toggleLayer: (id: LayerId) => void;

  // Selection
  selectedFeatureId: string | null;
  selectFeature: (id: string | null) => void;

  // Setters
  setJazidaMarkers: (m: MapaPonto[]) => void;
  setEmpresaMarkers: (m: MapaPontoEmpresa[]) => void;
  addPolygons: (p: GeoJSONFeature[]) => void;
  clearAll: () => void;
}
```

#### `chatStore.ts`

```typescript
interface ChatState {
  // Context
  activeConversationId: string | null;
  obraId: string | null;
  estudoId: string | null;

  // State
  lastToolCallResults: Record<string, unknown>;
  suggestedQueries: string[];

  // Actions
  setContext: (obraId: string, estudoId?: string) => void;
  setToolCallResult: (toolName: string, data: unknown) => void;
  clearConversation: () => void;
}
```

#### `uiStore.ts`

```typescript
interface UiState {
  sidebarOpen: boolean;
  theme: 'light' | 'dark' | 'system';
  chatPanelWidth: number;          // % do workspace
  activeModal: ModalType | null;
  modalData: unknown;

  toggleSidebar: () => void;
  setTheme: (t: UiState['theme']) => void;
  setChatPanelWidth: (w: number) => void;
  openModal: (type: ModalType, data?: unknown) => void;
  closeModal: () => void;
}
```

### TanStack Query — Keys & Hooks

```typescript
// hooks pattern
export const obraKeys = {
  all: ['obras'] as const,
  detail: (id: string) => ['obras', id] as const,
  estudos: (id: string) => ['obras', id, 'estudos'] as const,
};

export function useObras() {
  return useQuery({
    queryKey: obraKeys.all,
    queryFn: () => api.get('obras').json<ObraListResponse[]>(),
  });
}

export function useObra(id: string) {
  return useQuery({
    queryKey: obraKeys.detail(id),
    queryFn: () => api.get(`obras/${id}`).json<ObraResponse>(),
    enabled: !!id,
  });
}
```

---

## 9. Integração com Backend

### Endpoints Consumidos

#### FastAPI REST (CRUD)

| Método | Endpoint | Feature | Hook |
|--------|----------|---------|------|
| `GET` | `/api/v1/obras` | Listagem de obras | `useObras()` |
| `POST` | `/api/v1/obras` | Criar obra | `useCreateObra()` |
| `GET` | `/api/v1/obras/:id` | Detalhe obra | `useObra(id)` |
| `PATCH` | `/api/v1/obras/:id` | Atualizar obra | `useUpdateObra()` |
| `DELETE` | `/api/v1/obras/:id` | Remover obra | `useDeleteObra()` |
| `GET` | `/api/v1/estudos?obra_id=X` | Estudos da obra | `useEstudos(obraId)` |
| `POST` | `/api/v1/estudos` | Criar estudo | `useCreateEstudo()` |
| `GET` | `/api/v1/estudos/:id` | Detalhe estudo | `useEstudo(id)` |
| `PATCH` | `/api/v1/estudos/:id` | Atualizar estudo | `useUpdateEstudo()` |
| `POST` | `/api/v1/estudos/:id/fornecedores` | Adicionar fornecedor | `useAddFornecedor()` |

#### Chat Streaming (SSE)

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `POST` | `/api/v1/chat` | Envia mensagem, recebe stream SSE |

O endpoint de chat utiliza o **Vercel AI SDK protocol** — o backend (FastAPI) formata a resposta no padrão esperado pelo `useChat`:

```
data: {"role":"assistant","content":"Encontrei 23 jazidas..."}

data: {"role":"assistant","tool_calls":[{"id":"tc_1","type":"function","function":{"name":"buscar_jazidas","arguments":"{...}"}}]}

data: {"role":"tool","tool_call_id":"tc_1","content":"{\"total\":23,\"resultados\":[...],\"mapa\":{...}}"}
```

### Cliente HTTP (ky)

```typescript
// lib/api.ts
import ky from 'ky';
import { msalInstance } from '@/auth/authConfig';

export const api = ky.create({
  prefixUrl: import.meta.env.VITE_API_URL,
  hooks: {
    beforeRequest: [
      async (request) => {
        const accounts = msalInstance.getAllAccounts();
        if (accounts.length > 0) {
          const token = await msalInstance.acquireTokenSilent({
            scopes: ['api://supplyradar/.default'],
            account: accounts[0],
          });
          request.headers.set('Authorization', `Bearer ${token.accessToken}`);
        }
      },
    ],
  },
  retry: { limit: 2, methods: ['get'] },
  timeout: 30_000,
});
```

---

## 10. Autenticação (Azure AD)

### Fluxo

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Usuário     │ ──► │  /login       │ ──► │  Azure AD    │
│  (browser)   │     │  (MSAL popup) │     │  SSO         │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                                                  │ access_token + id_token
                                                  ▼
                            ┌──────────────────────────────┐
                            │  Frontend armazena tokens     │
                            │  MSAL cache (sessionStorage)  │
                            │  → Injeta Bearer em requests  │
                            └──────────────────────────────┘
```

### Configuração MSAL

```typescript
// auth/authConfig.ts
import { PublicClientApplication, Configuration } from '@azure/msal-browser';

const msalConfig: Configuration = {
  auth: {
    clientId: import.meta.env.VITE_AZURE_CLIENT_ID,
    authority: `https://login.microsoftonline.com/${import.meta.env.VITE_AZURE_TENANT_ID}`,
    redirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: 'sessionStorage',
    storeAuthStateInCookie: false,
  },
};

export const msalInstance = new PublicClientApplication(msalConfig);

export const loginRequest = {
  scopes: ['api://supplyradar/.default'],
};
```

### Protected Route

```typescript
// auth/ProtectedRoute.tsx
import { useIsAuthenticated } from '@azure/msal-react';
import { Navigate, Outlet } from 'react-router-dom';

export function ProtectedRoute() {
  const isAuthenticated = useIsAuthenticated();
  return isAuthenticated ? <Outlet /> : <Navigate to="/login" />;
}
```

---

## 11. Páginas Detalhadas

### 11.1 Workspace (Chat + Mapa)

**A tela principal do sistema.** Split-view entre Chat à esquerda e Mapa à direita.

**Estado inicial**: Chat com sugestões, mapa centralizado no Brasil.

**Fluxo principal**:
1. Usuário digita "Encontre jazidas de areia em 50km de Guarulhos"
2. Frontend envia via `useChat` → SSE stream
3. Agente (LangGraph) executa tools: `SubstanciaResolver` → `buscar_jazidas`
4. Stream retorna texto + tool_call com resultados
5. Frontend renderiza:
   - Mensagem de texto no chat
   - ToolCallCard com tabela inline de resultados
   - Marcadores + polígonos no mapa
   - FlyTo para o bounding box dos resultados

**Contexto de obra**: O workspace pode estar vinculado a uma obra. Nesse caso:
- Ponto da obra aparece fixo no mapa (📍 vermelho)
- Raio de busca padrão da obra é usado como sugestão
- Fornecedores podem ser adicionados ao estudo ativo

### 11.2 Obras (CRUD)

**Lista de obras**:
- Cards ou tabela com nome, tipo, status, município, UF, total de estudos
- Filtros: status, tipo, busca por nome
- Ação: criar nova obra

**Detalhe da obra**:
- Dados da obra (header)
- Mapa com localização
- Lista de estudos vinculados (sub-tabela)
- Botão "Abrir no Workspace" → navega para `/workspace` com contexto

### 11.3 Estudos

**Detalhe do estudo**:
- Header: título, categoria, status, data
- Filtros de busca salvos
- Lista de fornecedores selecionados (table sortável)
  - Colunas: Nome, Tipo (ANM/CNPJ), Distância, Status (aprovado/pendente/reprovado), Favorito
  - Ações: aprovar, reprovar, favoritar, editar notas, remover
- Mapa com todos os fornecedores + KMLs + rotas Azure Maps (truck)
- Abas: Fornecedores | Mapa | Arquivos KML | Histórico de Chat

### 11.4 Detalhes (Modais)

**JazidaDetailModal** (aberto ao clicar em jazida no chat/mapa/estudo):

```
┌──────────────────────────────────────────────────────┐
│ ✕                                                     │
│  JAZIDA: 832.145/2018                                 │
│  ──────────────────────────────────────────────────── │
│  📋 Dados do Processo                                 │
│  │ Fase: Concessão de Lavra  •  Ativo: ✅             │
│  │ Área: 12.5 ha  •  UF: SP  •  Município: Guarulhos │
│  │                                                    │
│  │ 👤 Titular: Mineração Porto Areia Ltda             │
│  │    CNPJ: 12.345.678/0001-99                        │
│  │    Tel: (11) 3333-4444                             │
│  │                                                    │
│  │ ⚗️ Substâncias:                                    │
│  │    Areia (vigente desde 2015) • Cascalho (vigente) │
│  │                                                    │
│  │ 📅 Últimos eventos:                                │
│  │    2024-01: Publicação no DOU                      │
│  │    2023-06: Vistoria realizada                     │
│  ──────────────────────────────────────────────────── │
│  🗺️ Mapa do polígono    │  📸 Street View            │
│  [MapLibre embed]        │  [Google SV embed]         │
│  ──────────────────────────────────────────────────── │
│  [➕ Adicionar ao Estudo]  [🔗 Link direto ANM]       │
└──────────────────────────────────────────────────────┘
```

**EmpresaDetailModal**:

```
┌──────────────────────────────────────────────────────┐
│ ✕                                                     │
│  EMPRESA: AREIAS GUARULHOS LTDA                       │
│  CNPJ: 12.345.678/0001-99                            │
│  ──────────────────────────────────────────────────── │
│  📋 Dados Cadastrais                                  │
│  │ Razão Social: AREIAS GUARULHOS LTDA EPP            │
│  │ Nome Fantasia: AREIAS GUARULHOS                    │
│  │ Situação: ✅ Ativa  •  Desde: 10/01/2010          │
│  │ Natureza: Soc. Empresária Limitada                 │
│  │ Capital Social: R$ 500.000,00                      │
│  │                                                    │
│  │ 🏭 CNAEs:                                          │
│  │   Principal: 0810-0/06 — Extração de areia         │
│  │   Secundários: 4930-2/01, 4663-0/00               │
│  │                                                    │
│  │ 📍 Endereço: Rod. Campinas-Mogi, km 10            │
│  │ 📞 (19) 3333-4444  •  contato@areias.com.br       │
│  │                                                    │
│  │ 👥 Sócios:                                         │
│  │   João da Silva — Sócio-Administrador — 60%       │
│  │   Maria Santos  — Sócia              — 40%       │
│  │                                                    │
│  │ ⛏️ Processos ANM vinculados:                       │
│  │   832.145/2018 — Areia — Concessão de Lavra        │
│  │   832.200/2020 — Cascalho — Licenciamento          │
│  ──────────────────────────────────────────────────── │
│  [➕ Adicionar ao Estudo]                              │
└──────────────────────────────────────────────────────┘
```

---

## 12. Performance e Otimizações

### Code Splitting

```typescript
// Lazy load de features pesadas
const WorkspacePage = lazy(() => import('@/features/workspace/WorkspacePage'));
const DashboardPage = lazy(() => import('@/features/dashboard/DashboardPage'));
const ObraDetail = lazy(() => import('@/features/obras/ObraDetail'));
```

### Mapa

- **Clustering**: agrupar marcadores com `supercluster` quando > 100 pontos
- **Simplification**: simplificar polígonos grandes com `@turf/simplify` antes de renderizar
- **Tile caching**: usar Service Worker para cache de tiles OSM offline
- **Viewport-based loading**: só carregar features visíveis no viewport

### Chat

- **Virtualized scroll**: usar `@tanstack/react-virtual` para conversas longas
- **Throttled map updates**: debounce 300ms em atualizações de mapa vindas do stream
- **Optimistic UI**: mostrar mensagem do user imediatamente antes do ACK

### Bundle

- **Target**: < 300KB gzipped (initial load)
- **MapLibre**: ~200KB gzipped — carregado lazy com prefetch
- **Tree shaking**: Radix UI e Lucide icons são tree-shakeable

---

## 13. Cronograma de Implementação

### Fase 1 — Fundação (Semana 1)

| # | Tarefa | Estimativa |
|---|--------|------------|
| 1.1 | `npm create vite` + TS + Tailwind + ESLint + Prettier | 2h |
| 1.2 | Configurar Radix UI + cn helper (clsx + twMerge) | 1h |
| 1.3 | Design system: Button, Input, Card, Badge, Dialog, Drawer | 4h |
| 1.4 | AppLayout: Sidebar, Header, ThemeToggle, Breadcrumb | 4h |
| 1.5 | React Router: rotas, ProtectedRoute (stub sem Azure AD) | 2h |
| 1.6 | Zustand stores: mapStore, chatStore, uiStore | 2h |
| 1.7 | `ky` API client configurado | 1h |
| **Total** | | **~16h** |

### Fase 2 — Mapa Base (Semana 2)

| # | Tarefa | Estimativa |
|---|--------|------------|
| 2.1 | MapLibre + react-map-gl: container, controles | 4h |
| 2.2 | MapMarkers com clustering (supercluster) | 4h |
| 2.3 | MapPolygons (GeoJSON source + fill/line layers) | 3h |
| 2.4 | MapPopup + MapLegend | 2h |
| 2.5 | Integração mapStore ↔ MapContainer | 2h |
| 2.6 | FlyTo e fitBounds via store | 1h |
| **Total** | | **~16h** |

### Fase 3 — Chat com IA (Semana 3)

| # | Tarefa | Estimativa |
|---|--------|------------|
| 3.1 | Vercel AI SDK: useChat integrado ao backend | 4h |
| 3.2 | ChatPanel, ChatInput, ChatMessages, MessageBubble | 4h |
| 3.3 | ToolCallCard: renderização visual de tool calls | 4h |
| 3.4 | ResultsTable inline (jazidas, empresas) | 3h |
| 3.5 | Chat → Mapa: onToolCall → mapStore updates | 2h |
| 3.6 | ChatSuggestions, StreamingDots | 1h |
| **Total** | | **~18h** |

### Fase 4 — Obras e Estudos (Semana 4)

| # | Tarefa | Estimativa |
|---|--------|------------|
| 4.1 | TanStack Query hooks: useObras, useObra, CRUD | 3h |
| 4.2 | ObrasList: table + filtros + criar | 4h |
| 4.3 | ObraDetail: header + mapa + estudos | 4h |
| 4.4 | EstudoForm: criar/editar | 3h |
| 4.5 | EstudoDetail: fornecedores + mapa + KML | 6h |
| 4.6 | AddFornecedor: do chat/mapa para o estudo | 2h |
| **Total** | | **~22h** |

### Fase 5 — Detalhes e Integração (Semana 5)

| # | Tarefa | Estimativa |
|---|--------|------------|
| 5.1 | JazidaDetailModal completo | 4h |
| 5.2 | EmpresaDetailModal completo | 4h |
| 5.3 | Street View embed | 2h |
| 5.4 | KML Upload + overlay rendering | 4h |
| 5.5 | Azure Maps Route: useAzureRoute hook + MapRoutes + truck params | 4h |
| 5.6 | Azure Maps Search: useAzureGeocode hook (fuzzy + reverse) | 2h |
| **Total** | | **~20h** |

### Fase 6 — Auth e Polish (Semana 6)

| # | Tarefa | Estimativa |
|---|--------|------------|
| 6.1 | MSAL React: AuthProvider, login, token inject | 4h |
| 6.2 | Dashboard: métricas, cards, mini-charts | 4h |
| 6.3 | Responsive: tablet breakpoint, chat/mapa tabs | 3h |
| 6.4 | Animações: Framer Motion em modais, transições | 2h |
| 6.5 | Error boundaries, loading states, empty states | 2h |
| 6.6 | Vitest: testes unitários críticos | 3h |
| **Total** | | **~18h** |

### Resumo

| Fase | Semana | Horas | Entrega |
|------|--------|-------|---------|
| 1 — Fundação | S1 | 16h | Projeto scaffolded, design system, layout |
| 2 — Mapa | S2 | 16h | Mapa interativo com layers |
| 3 — Chat IA | S3 | 18h | Chat streaming + tool call rendering |
| 4 — CRUD | S4 | 22h | Obras + Estudos funcional |
| 5 — Detalhes + Azure Maps | S5 | 20h | Modais, KML, Azure Maps Route/Search, Street View |
| 6 — Auth & Polish | S6 | 18h | Azure AD, dashboard, testes, responsive |
| **Total** | **6 sem** | **~110h** | **MVP Completo** |

---

## 14. Decisões Arquiteturais

### ADR-01: React + Vite (não Next.js)

**Contexto**: O sistema é uma SPA corporativa (não precisa de SEO) com comunicação via SSE streaming.

**Decisão**: React + Vite + React Router (SPA pura).

**Alternativa rejeitada**: Next.js — adiciona complexidade de server components, routing conventions, e deploy (requer Node.js server). A SPA estática é mais simples para deploy como container Azure / static site.

### ADR-02: Vercel AI SDK para Chat

**Contexto**: O chat precisa de streaming SSE, rendering de tool calls, e state management de conversas.

**Decisão**: Usar `@ai-sdk/react` (`useChat` hook) que abstrai SSE, tool calls, e streaming.

**Benefício**: O backend (FastAPI) implementa o protocolo Vercel AI (text stream protocol), e o frontend "grátis" ganha streaming + tool call handling.

### ADR-03: Zustand + TanStack Query (não Redux)

**Decisão**: Zustand para estado UI (mapa, chat, sidebar) + TanStack Query para server state (obras, estudos).

**Justificativa**: Zustand é ~1KB, zero boilerplate. TanStack Query resolve cache, refetch, optimistic updates para CRUD. Redux seria overkill para este tamanho de aplicação.

### ADR-04: MapLibre GL JS (não Leaflet, não Google Maps)

**Decisão**: MapLibre GL JS com tiles OSM.

**Justificativa**:
- **Gratuito** (sem API key para tiles básicos OSM)
- **WebGL** — renderiza milhares de polígonos sem perda de performance
- **Compatível com deck.gl** para futuro 3D/heatmaps
- Leaflet é rasterizado (lento com muitos polígonos); Google Maps tem custo e restrições de licença

### ADR-05: ky (não axios)

**Decisão**: `ky` como HTTP client.

**Justificativa**: Mais leve que axios (~3KB vs ~13KB), API moderna baseada em fetch, suporta hooks nativos, TypeScript first-class. Para este projeto não precisamos de XMLHttpRequest features (upload progress no axios).

### ADR-06: Modo Split View Resizable

**Decisão**: Workspace em split view (chat | mapa) com drag handle resizable.

**Alternativa considerada**: Tabs (chat OU mapa). Rejeitada porque o valor principal do sistema é a interação simultânea chat + mapa — o usuário precisa ver os resultados no mapa enquanto conversa.

### ADR-07: Azure Maps Route Service (não OSRM, não Google Directions)

**Contexto**: O sistema precisa calcular rotas reais entre obras e jazidas/fornecedores, incluindo transporte de material mineral em caminhões pesados.

**Decisão**: Azure Maps Route Service como provedor de routing, geocoding e isócronas.

**Justificativa**:
1. **Truck routing nativo** — único provedor (além de HERE) que suporta `travelMode=truck` com dimensões do veículo (peso, altura, largura, comprimento, peso por eixo) e tipo de carga (perigosa). Essencial para logística mineral com carretas de 40+ toneladas
2. **Mesmo tenant Azure** — a infra já roda em Azure (App Services, OpenSearch, Redis). Subscription key no mesmo tenant elimina gestão de credenciais separadas
3. **Pricing ~10x menor que Google** — Azure Route: $0.50/1K requests vs Google Directions: $5.00/1K requests
4. **Geocoding integrado** — Azure Maps Search (fuzzy + reverse + batch) no mesmo subscription key, sem precisar de Nominatim (menos infra) ou Google Geocoding (caro)
5. **Route Range (isócronas)** — API nativa para polígonos de alcance ("quais jazidas estão a 2h de caminhão?"), útil para análise logística

**Alternativas rejeitadas**:
- **OSRM** — gratuito, mas requer self-hosting (Kubernetes, 50GB+ RAM para América do Sul), sem truck routing, sem SLA
- **Google Directions** — excelente qualidade, mas 10x mais caro, sem truck routing, API key em ecossistema GCP separado
- **HERE Routing** — tem truck routing, mas pricing menos favorável e sem integração Azure nativa

**Nota**: Google Street View continua sendo usado para imagens de rua (Azure Maps não tem equivalente).

---

## Apêndice A: Variáveis de Ambiente

```env
# .env.local (desenvolvimento)
VITE_API_URL=http://localhost:8000/api/v1
VITE_AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
VITE_AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
VITE_AZURE_MAPS_KEY=xxxxxxxx                               # Azure Maps subscription key (routing, geocoding)
VITE_MAPLIBRE_STYLE=https://basemaps.cartocdn.com/gl/positron-gl-style/style.json
VITE_GOOGLE_MAPS_KEY=AIza...                               # Apenas para Street View embed
```

## Apêndice B: Dependências Principais (package.json)

```json
{
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.0.0",
    "@radix-ui/react-dialog": "^1.1.0",
    "@radix-ui/react-dropdown-menu": "^2.1.0",
    "@radix-ui/react-tabs": "^1.1.0",
    "@radix-ui/react-tooltip": "^1.1.0",
    "tailwindcss": "^4.0.0",
    "maplibre-gl": "^5.0.0",
    "react-map-gl": "^7.1.0",
    "@ai-sdk/react": "^1.0.0",
    "ai": "^4.0.0",
    "zustand": "^5.0.0",
    "@tanstack/react-query": "^5.0.0",
    "ky": "^1.7.0",
    "@azure/msal-browser": "^4.0.0",
    "@azure/msal-react": "^3.0.0",
    "lucide-react": "^0.460.0",
    "framer-motion": "^11.0.0",
    "clsx": "^2.1.0",
    "tailwind-merge": "^2.6.0",
    "date-fns": "^4.0.0",
    "supercluster": "^8.0.0"
  },
  "devDependencies": {
    "typescript": "^5.7.0",
    "vite": "^6.0.0",
    "@vitejs/plugin-react-swc": "^4.0.0",
    "eslint": "^9.0.0",
    "prettier": "^3.4.0",
    "vitest": "^2.0.0",
    "@testing-library/react": "^16.0.0",
    "playwright": "^1.49.0"
  }
}
```

## Apêndice C: Referência Rápida — Tipos TS Espelhados do Backend

```typescript
// types/api.ts — DTOs espelhados das Pydantic models do backend

// Obras
interface Obra {
  _id: string;
  nome: string;
  tipo: 'rodovia' | 'ferrovia' | 'edificacao' | 'barragem' | 'ponte' | 'outro';
  status: 'planejamento' | 'em_andamento' | 'pausada' | 'concluida' | 'cancelada';
  localizacao?: { lat: number; lon: number };
  municipio?: string;
  uf?: string;
  raio_busca_km: number;
  total_estudos: number;
  created_at: string;
  updated_at: string;
}

// Estudos
interface Estudo {
  _id: string;
  titulo: string;
  obra_id: string;
  categoria: 'material_mineracao' | 'produto_comercial' | 'servico' | 'hibrido';
  termo_busca: string;
  status: 'rascunho' | 'em_analise' | 'concluido' | 'arquivado';
  fornecedores: Fornecedor[];
  total_fornecedores: number;
  created_at: string;
  updated_at: string;
}

// Fornecedor genérico (ANM ou CNPJ)
interface Fornecedor {
  id: string;
  tipo_fonte: 'anm' | 'cnpj' | 'manual';
  nome: string;
  localizacao?: { lat: number; lon: number };
  municipio?: string;
  uf?: string;
  distancia_km?: number;
  favorito: boolean;
  aprovado?: boolean;
  // ANM
  processo_anm?: string;
  substancia?: string;
  fase?: string;
  // CNPJ
  cnpj?: string;
  cnae_principal?: string;
}

// Mapa pontos (das tools MCP)
interface MapaPonto {
  lat: number;
  lon: number;
  label: string;
  id: string;
}

interface MapaPontoEmpresa {
  lat: number;
  lon: number;
  label: string;
  cnpj_basico: string;
  distancia_km?: number;
}

// Rota Azure Maps (truck routing)
interface RouteLine {
  id: string;
  label: string;                  // "Obra → Jazida 832.145/2018"
  origin: { lat: number; lon: number };
  destination: { lat: number; lon: number };
  points: { lat: number; lon: number }[];  // polyline do Azure Maps
  distance_km: number;
  duration_min: number;
  traffic_delay_min: number;
  travel_mode: 'car' | 'truck';
}
```
