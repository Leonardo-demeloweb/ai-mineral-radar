# Estudo de Implementação — Camada de Mapas no Frontend

> MineralRadar 2.0 · Março 2026

---

## 1. Inventário: O que já existe

### Backend — MCP Geo (7 tools, 100% implementadas)

| # | Tool | Fonte | Dados retornados | Cache Redis |
|---|------|-------|------------------|-------------|
| 1 | `buscar_municipio` | OpenSearch `ibge_municipio_v001` | nome, UF, região, centro geográfico, polígono GeoJSON (opt) | `geo:mun:{hash}` 24h |
| 2 | `municipio_por_coordenada` | OpenSearch (geo_shape contains) | município onde está o ponto, polígono (opt) | `geo:coord:{lat}:{lon}` 24h |
| 3 | `obter_poligono` | OpenSearch | GeoJSON Feature completo (Polygon/MultiPolygon) | `geo:poly:{id_ibge}` 7d |
| 4 | `municipios_em_raio` | OpenSearch (geo_distance) | lista de municípios + distância, polígonos (opt) | `geo:raio:{hash}` 1h |
| 5 | `calcular_rota` | Azure Maps Route Directions | distância km, duração, polyline, tráfego | `geo:rota:{hash}` 1h |
| 6 | `calcular_isocrona` | Azure Maps Route Range | GeoJSON Polygon da área alcançável | `geo:iso:{hash}` 1h |
| 7 | `geocodificar` | Azure Maps Search | coordenadas, endereço, confiança | `geo:gc:{hash}` 24h |

### Frontend — Infraestrutura preparada

| Arquivo | Estado | O que faz |
|---------|--------|-----------|
| `types/geojson.ts` | ✅ Completo | Tipos: `MapaPonto`, `MapaPontoEmpresa`, `GeoJSONFeature`, `RouteLine`, `KmlOverlay` |
| `stores/mapStore.ts` | ✅ Completo | Zustand store: viewport, markers (jazida/empresa), polígonos, rotas, raio, layers, seleção |
| `hooks/useAzureRoute.ts` | ✅ Completo | Hook para Azure Maps Route Directions direto do frontend (com abort) |
| `hooks/useAzureGeocode.ts` | ⚠️ Stub | Throw error — não implementado |
| `components/map/MapContainer.tsx` | ❌ Stub | `return null` |
| `features/workspace/WorkspacePage.tsx` | ❌ Placeholder | Dois cards estáticos |
| `.env.example` | ✅ Configurado | `VITE_MAPLIBRE_STYLE`, `VITE_AZURE_MAPS_KEY` |
| `package.json` | ⚠️ Parcial | `supercluster` já instalado (clustering), **MapLibre GL JS não instalado** |

### O que NÃO existe no frontend

- Biblioteca de mapas (MapLibre GL JS) — **não instalada**
- Renderização de tiles
- Layers (pontos, polígonos, rotas, raio)
- Popups/tooltips de features
- Controles de layer visibility
- Integração com MCP Geo via backend API
- Componente de cluster (supercluster está instalado mas não usado)

---

## 2. Arquitetura: Como o mapa se conecta ao backend

### Fluxo principal (via Chat/Agent — S10)

```
Usuário no Workspace
  ├─ ChatPanel: "Busque jazidas de areia em 100km de BH"
  │     └─ POST /api/v1/chat { message, obra_id }
  │           └─ LangGraph Agent
  │                 ├─ Tool: buscar_jazidas(substancia="areia", lat=-19.9, lon=-43.9, raio=100)
  │                 │     └─ Retorna: { resultados: [...], mapa: { pontos: [...] } }
  │                 ├─ Tool: municipios_em_raio(lat, lon, raio=100)
  │                 │     └─ Retorna: { municipios: [...com polígonos...] }
  │                 └─ Tool: calcular_rota(origem→destino)
  │                       └─ Retorna: { polyline: [...], distancia_km }
  │
  └─ MapContainer: recebe dados via mapStore (Zustand)
        ├─ Layer: jazida-points (pins azuis)
        ├─ Layer: empresa-points (pins verdes)
        ├─ Layer: municipio-borders (polígonos)
        ├─ Layer: search-radius (círculo 100km)
        ├─ Layer: routes (polyline da rota)
        └─ Layer: obra-point (pin da obra)
```

### Decisão: toda operação geo passa pelo Agent

Todas as operações geo — busca de jazidas, municípios, rotas, isócronas, geocodificação — trafegam pelo Agent (backend). O frontend **não chama Azure Maps diretamente**.

| Motivo | Detalhe |
|--------|---------|
| Keys protegidas | `AZURE_MAPS_KEY` fica apenas no backend — nunca exposta no browser |
| Cache Redis | Todas as respostas geo são cacheadas (1h–7d) — zero custo duplicado |
| Lógica centralizada | MCP Geo é o único responsável por geo — frontend só renderiza |
| Contexto do Agent | O Agent pode combinar geo + jazidas + empresas numa única resposta estruturada |

---

## 3. Stack técnica

| Camada | Tecnologia | Justificativa |
|--------|-----------|---------------|
| **Renderização** | MapLibre GL JS (latest) | Open-source, WebGL, tiles vetoriais, gratuito |
| **Tiles base** | CARTO Positron (`VITE_MAPLIBRE_STYLE`) | Já configurado no `.env.example`, estilo limpo |
| **React binding** | API imperativa via `useRef` (sem react-map-gl) | Mais flexível para layers dinâmicos controlados por Zustand |
| **Clustering** | `supercluster` (já instalado) | Agrupa pontos em zoom baixo |
| **State** | `mapStore.ts` (Zustand, já implementado) | Viewport, markers, layers, seleção |
| **Tipos** | `geojson.ts` (já completo) | `MapaPonto`, `RouteLine`, `GeoJSONFeature` |

---

## 4. Componentes a implementar

### 4.1 MapContainer — Componente principal

```
MapContainer
  ├─ MapLibre GL JS instance (ref)
  ├─ useEffect: sync viewport com mapStore
  ├─ useEffect: sync layers com mapStore data
  │     ├─ Source: jazida-points (GeoJSON FeatureCollection)
  │     ├─ Source: empresa-points (GeoJSON FeatureCollection)
  │     ├─ Source: municipio-borders (GeoJSON polygons)
  │     ├─ Source: routes (GeoJSON LineString)
  │     ├─ Source: search-radius (GeoJSON circle gerado manualmente)
  │     └─ Source: obra-point (single Feature)
  ├─ Popup: on click → detalhes do ponto (nome, distância, fase)
  ├─ Cluster layer: supercluster para zoom < 10
  └─ Controls: zoom, fullscreen, layer toggle
```

### 4.2 MapLayerControl — Painel de layers

```
MapLayerControl (sidebar ou overlay)
  ├─ Toggle: Jazidas (azul)
  ├─ Toggle: Empresas (verde)
  ├─ Toggle: Polígonos municipais
  ├─ Toggle: Rotas
  ├─ Toggle: Raio de busca
  ├─ Toggle: Isócrona
  └─ Toggle: Overlays KML
```

### 4.3 MapPopup — Detalhes de feature

```
MapPopup
  ├─ Jazida: ds_processo, substâncias, fase, distância, titular
  ├─ Empresa: razão social, CNPJ, CNAE, distância
  ├─ Município: nome, UF, população
  └─ Ações: "Ver detalhes", "Calcular rota", "Favoritar"
```

### 4.4 Hooks de integração

| Hook | Função | Chama |
|------|--------|-------|
| `useMapData` | Converte resultados do Agent → mapStore data | Parseia chat response → `setJazidaMarkers`, `setRouteLines` etc. |
| ~~`useAzureRoute`~~ | ~~Rota direta do frontend~~ | **Não usar** — rota vai via Agent |
| ~~`useAzureGeocode`~~ | ~~Geocode direto do frontend~~ | **Não usar** — geocode vai via Agent |

> `useAzureRoute` e `useAzureGeocode` existem no código mas **não serão usados** — podem ser removidos ou mantidos como dead code até decisão final.

---

## 5. Dados: o que o MCP Geo retorna vs o que o mapa precisa

### Jazidas (de `buscar_jazidas` / `buscar_fornecedores`)

```json
// MCP retorna (campo "mapa.pontos" via extract_mapa_pontos):
{
  "pontos": [
    {
      "lat": -19.9167, "lon": -43.9345,
      "processo": "832.145/2018",
      "substancia": "AREIA",
      "fase": "LAVRA",
      "tipo": "centroide"
    }
  ]
}

// MapStore precisa (MapaPonto):
// Conversão no useMapData: processo→id, substância+fase→label
{
  "lat": -19.9167, "lon": -43.9345,
  "label": "832.145/2018 — AREIA (LAVRA)",
  "id": "832.145/2018"
}
```

### Polígonos municipais (de `obter_poligono`)

```json
// MCP retorna:
{
  "feature": {
    "type": "Feature",
    "properties": { "nome": "Belo Horizonte", "uf": "MG" },
    "geometry": { "type": "MultiPolygon", "coordinates": [...] }
  }
}

// MapStore: direto no addPolygons — tipo GeoJSONFeature já é compatível
```

### Rotas (de `calcular_rota`)

```json
// MCP retorna:
{
  "distancia_km": 234.5,
  "duracao_min": 185,
  "polyline": [{"lat": -19.9, "lon": -43.9}, ...]
}

// MapStore precisa (RouteLine): conversão direta — tipos já batem
```

---

## 6. Plano de implementação por fases

### Fase 1 — Mapa base funcional (sem Agent)

| # | Tarefa | Dependência |
|---|--------|-------------|
| 1.1 | Instalar `maplibre-gl` + types | npm |
| 1.2 | Implementar `MapContainer` com tiles CARTO Positron | `VITE_MAPLIBRE_STYLE` |
| 1.3 | Sync viewport com `mapStore` | — |
| 1.4 | Renderizar `obra-point` (pin da obra ativa) | `useObra` → coords |
| 1.5 | Renderizar `search-radius` (círculo GeoJSON com raio_busca_km) | `obra.raio_busca_km` + `obra.localizacao` |
| 1.6 | Integrar no `WorkspacePage` (substituir placeholder) | — |
| 1.7 | `MapLayerControl` básico (toggles) | — |

**Resultado:** Mapa visível no Workspace com a obra como pin + raio de busca.

> **Nota:** `obra.localizacao` é nullable (`{lat, lon} | null`). Se a obra não tiver coordenadas, o mapa carrega centrado no Brasil (viewport default do mapStore: `{lng: -47.9, lat: -15.8, zoom: 4}`) sem pin/raio. A ação "Geocodificar endereço" (via Agent) poderá resolver isso no futuro.

### Fase 2 — Layers de dados (quando Agent estiver conectado — S10)

| # | Tarefa | Dependência |
|---|--------|-------------|
| 2.1 | Layer `jazida-points` com clustering (supercluster) | Chat → Agent → `buscar_jazidas` |
| 2.2 | Layer `empresa-points` com clustering | Chat → Agent → `buscar_empresas` |
| 2.3 | Layer `municipio-borders` (polígonos GeoJSON) | Chat → Agent → `obter_poligono` |
| 2.4 | Layer `routes` (polyline) | Chat → Agent → `calcular_rota` |
| 2.5 | Layer `isochrone` (polígono alcance) | Chat → Agent → `calcular_isocrona` |
| 2.6 | `MapPopup` com detalhes da feature clicada | — |
| 2.7 | Hook `useMapData` para converter response do Agent → mapStore | — |

**Resultado:** Mapa interativo mostrando jazidas, empresas, polígonos e rotas.

### Fase 3 — Features avançadas

| # | Tarefa | Dependência |
|---|--------|-------------|
| 3.1 | Layer `kml-overlays` (upload de KML pelo usuário) | Upload no EstudoDetail |
| 3.2 | Click no mapa → reverse geocode → mostrar município | Chat → Agent → `geocodificar` |
| 3.3 | FitBounds automático quando dados carregam | mapStore.fitBounds via ref imperativo |
| 3.4 | Exportar mapa como imagem (html2canvas) | — |

---

## 7. Dependências a instalar

```bash
npm install maplibre-gl
npm install -D @types/maplibre-gl
```

`supercluster` e `@types/supercluster` já estão instalados.

Não é necessário `react-map-gl` — a API imperativa do MapLibre via `useRef` é mais adequada para o padrão de layers dinâmicos com Zustand.

`useAzureRoute` e `useAzureGeocode` **não serão usados** — toda operação geo passa pelo Agent.

---

## 8. Configuração de ambiente

```env
# .env (frontend)
VITE_API_URL=http://localhost:8000/api/v1
VITE_MAPLIBRE_STYLE=https://basemaps.cartocdn.com/gl/positron-gl-style/style.json
VITE_MAPLIBRE_STYLE_DARK=https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json
# VITE_AZURE_MAPS_KEY — não necessário, key fica apenas no backend
```

Tiles CARTO Positron são **gratuitos e sem API key**. Alternativas:
- `https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json` (dark mode)
- `https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json` (colorido)
- OpenStreetMap raster (fallback sem WebGL)

---


## 9. Riscos e mitigações

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| Agent indisponível (MCPs offline) | Sem dados no mapa | Mapa base (tiles + obra pin) continua funcional |
| Volume de pontos alto (>1000 jazidas) | Performance WebGL | Clustering com supercluster (já instalado) |
| Polígonos pesados (MultiPolygon GeoJSON) | Transferência lenta | Cache Redis 7d no backend + retornar só quando solicitado (`incluir_poligono: true`) |
| Sem tema dark para tiles | Inconsistência visual | Alternar style URL baseado em `uiStore.theme` |
| Latência do Agent (~300ms) | Mapa demora para popular | Skeleton/loading state no MapContainer |
