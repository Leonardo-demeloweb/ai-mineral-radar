import { useEffect, useRef } from 'react'
import maplibregl, { LngLatBounds } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useMapStore } from '@/stores/mapStore'
import { useFavoritesStore } from '@/stores/favoritesStore'
import { useChatStore } from '@/stores/chatStore'
import { useUiStore } from '@/stores/uiStore'
import { useProjeto } from '@/hooks/useProjetos'
import { useAddFornecedor, useRemoveFornecedor, type AddFornecedorInput } from '@/hooks/useAnalises'
import { fetchCarPoligono, fetchCprmPoligono, fetchJazidaPoligono } from '@/lib/api'
import { PROJETO_TIPO_LABEL, PROJETO_STATUS_LABEL } from '@/lib/formatters'
import type { StyleSpecification } from 'maplibre-gl'
import type { MapaPonto, MapaPontoEmpresa, GeoJSONFeature, AddressPin } from '@/types/geojson'
import type { Projeto } from '@/types/api'

const RESIZE_DEBOUNCE_MS = 60

type MarkerEntry = { id: string; marker: maplibregl.Marker; lat: number; lon: number }

/** Strips non-digit characters so CNPJ/Processo comparisons are format-agnostic.
 *  "12.345.678/0001-90" === "12345678000190" and "800.335/1992" === "8003351992". */
function normalizeId(raw: string | undefined | null): string {
  return (raw ?? '').replace(/\D/g, '')
}

/** CPRM GeoBank sample id — must match ChatShell result cards. */
function normalizeAmostraId(raw: string | undefined | null): string {
  return (raw ?? '').trim().toUpperCase().replace(/\s+/g, '')
}

const RADIUS_SOURCE = 'obra-search-radius'
const RADIUS_FILL_LAYER = 'obra-search-radius-fill'
const RADIUS_OUTLINE_LAYER = 'obra-search-radius-outline'

/** Colors that contrast well against each base style. */
const RADIUS_COLORS: Record<string, { fill: string; line: string; fillOpacity: number }> = {
  map:       { fill: '#16a34a', line: '#16a34a', fillOpacity: 0.07 },
  satellite: { fill: '#facc15', line: '#facc15', fillOpacity: 0.10 },
}

const JAZIDA_POLY_SOURCE = 'jazida-polygons-src'
const JAZIDA_POLY_FILL   = 'jazida-polygons-fill'
const JAZIDA_POLY_LINE   = 'jazida-polygons-line'

const MUNICIPIO_POLY_SOURCE = 'municipio-borders-src'
const MUNICIPIO_POLY_FILL   = 'municipio-borders-fill'
const MUNICIPIO_POLY_LINE   = 'municipio-borders-line'

const CHAT_CONTEXT_SOURCE = 'chat-context-geo-src'
const CHAT_CONTEXT_FILL   = 'chat-context-fill'
const CHAT_CONTEXT_LINE   = 'chat-context-line'

const ROUTE_SOURCE      = 'route-lines-src'
const ROUTE_LINE_LAYER  = 'route-lines-layer'
const ROUTE_ARROW_LAYER = 'route-arrows-layer'

const ROUTE_GAP_SOURCE  = 'route-gap-src'
const ROUTE_GAP_LAYER   = 'route-gap-layer'

const ROUTE_GAP_THRESHOLD_KM = 0.1

const ISO_SOURCE     = 'isochrone-src'
const ISO_FILL_LAYER = 'isochrone-fill'
const ISO_LINE_LAYER = 'isochrone-line'

const CPRM_WMS_BASE = 'https://geoservicos.sgb.gov.br/geoserver/wms'
const CPRM_SOURCE   = 'cprm-geo-src'
const CPRM_LAYER    = 'cprm-geo-layer'

const JAZIDA_COLORS: Record<string, { fill: string; line: string; fillOpacity: number }> = {
  map:       { fill: '#10b981', line: '#059669', fillOpacity: 0.15 },
  satellite: { fill: '#34d399', line: '#34d399', fillOpacity: 0.20 },
}

const ROUTE_COLORS: Record<string, { line: string; arrow: string; gap: string }> = {
  map:       { line: '#16a34a', arrow: '#15803d', gap: '#9ca3af' },
  satellite: { line: '#facc15', arrow: '#ca8a04', gap: '#e5e7eb' },
}

/**
 * Paleta cíclica de cores para múltiplas rotas comparadas no mesmo turno
 * (ex.: comparação de portos). Quando 2+ rotas compartilham segmentos
 * (Aratu e Salvador divergem só no fim), usar cores distintas + line-offset
 * evita que apareçam empilhadas como uma única linha.
 *
 * 6 matizes — passa a girar o índice se houver mais que 6 rotas no turno.
 */
const ROUTE_PALETTE_MAP: string[] = [
  '#16a34a', // verde (default)
  '#f97316', // laranja
  '#16a34a', // verde
  '#db2777', // magenta
  '#0891b2', // ciano
  '#a855f7', // roxo
]
const ROUTE_PALETTE_SAT: string[] = [
  '#facc15', // amarelo (default)
  '#fb923c', // laranja claro
  '#4ade80', // verde claro
  '#f472b6', // rosa
  '#22d3ee', // ciano claro
  '#c084fc', // roxo claro
]
/** Offsets perpendiculares (em px) para separar linhas sobrepostas. */
const ROUTE_OFFSETS_PX: number[] = [0, 4, -4, 8, -8, 12]

const ISO_COLORS: Record<string, { fill: string; line: string; fillOpacity: number }> = {
  map:       { fill: '#8b5cf6', line: '#7c3aed', fillOpacity: 0.12 },
  satellite: { fill: '#c4b5fd', line: '#a78bfa', fillOpacity: 0.18 },
}

const ADDRESS_PIN_COLORS: Record<string, { fill: string; ring: string }> = {
  map:       { fill: '#f59e0b', ring: '#b45309' },
  satellite: { fill: '#fbbf24', ring: '#fbbf24' },
}

/**
 * 32x40 pinpoint SVG, fully self-contained (no overflow), used as the HTML
 * marker for arbitrary addresses plotted via plotar_endereco.
 * Same anchor/offset convention as the obra marker (-16, -40, top-left).
 */
function makeAddressPinEl(color: string, ring: string, title: string): HTMLDivElement {
  const el = document.createElement('div')
  el.style.cssText = 'cursor:pointer;width:32px;height:40px;display:block;line-height:0'
  el.title = title
  el.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" style="display:block" width="32" height="40" viewBox="0 0 32 40" fill="none">
    <path d="M16 0C7.16 0 0 7.16 0 16c0 12 16 24 16 24s16-12 16-24C32 7.16 24.84 0 16 0z" fill="${color}" stroke="${ring}" stroke-width="1"/>
    <circle cx="16" cy="15" r="6" fill="white"/>
    <circle cx="16" cy="15" r="3" fill="${color}"/>
  </svg>`
  return el
}

function streetViewUrl(lat: number, lon: number): string {
  return `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat},${lon}`
}

/**
 * Round 22x22 button anchored absolutely (top:2px;right:2px-ish). Caller
 * passes `right` to stack icons next to the favorite star.
 *
 * Style: amber background (#f59e0b), white SVG of a person silhouette,
 * tooltip "Abrir no Street View". Clicking opens a new tab — no JS needed.
 */
function makeStreetViewBtn(lat: number, lon: number, right: number): string {
  return `<a href="${streetViewUrl(lat, lon)}" target="_blank" rel="noopener noreferrer"
    title="Abrir no Street View"
    style="position:absolute;top:2px;right:${right}px;display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;background:#f59e0b;color:white;text-decoration:none;line-height:1;box-shadow:0 1px 2px rgba(0,0,0,.2);transition:transform .15s ease;z-index:1"
    onmouseover="this.style.transform='scale(1.15)'" onmouseout="this.style.transform='scale(1)'">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="7" r="3"/>
      <path d="M5 21v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2"/>
    </svg>
  </a>`
}

/**
 * Round 22x22 button that triggers a chat message asking the agent to
 * compute a route from the active obra to this point. Click is captured
 * by event delegation (`.route-calc-btn`) and forwarded as a CustomEvent
 * to ChatShell, which calls sendMessage().
 */
function makeRouteCalcBtn(label: string, lat: number, lon: number, right: number): string {
  // Use data-* attributes for the click handler. label may contain quotes
  // so we encode it.
  const encodedLabel = encodeURIComponent(label)
  return `<button class="route-calc-btn"
    data-route-label="${encodedLabel}"
    data-route-lat="${lat}"
    data-route-lon="${lon}"
    title="Calcular rota do pino do projeto até aqui"
    style="position:absolute;top:2px;right:${right}px;display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;background:#16a34a;color:white;border:none;cursor:pointer;line-height:1;box-shadow:0 1px 2px rgba(0,0,0,.2);transition:transform .15s ease;z-index:1;padding:0"
    onmouseover="this.style.transform='scale(1.15)'" onmouseout="this.style.transform='scale(1)'">
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="3 11 13 11 13 5 21 12 13 19 13 13 3 13"/>
    </svg>
  </button>`
}

/**
 * Top-right action bar for jazida/empresa popups.
 *
 * Layout, right → left (each icon is 22px wide, ~4px gap):
 *   [star?(right:2px)] [streetView(right:28px)] [routeCalc(right:54px)]
 *
 * The favorite star is hard-positioned at right:2px by makeStarSvg, so we
 * shift the other icons further left. paddingRightPx tells the caller how
 * much room to reserve in the text body so it doesn't run under the icons.
 */
function makePopupActions(opts: {
  lat: number
  lon: number
  routeLabel: string
  showStar: boolean
  starId: string | null
}): { html: string; paddingRightPx: number } {
  const parts: string[] = []
  // Star (rightmost) — already positioned at right:2px internally
  if (opts.showStar && opts.starId) {
    parts.push(makeStarSvg(opts.starId))
  }
  // Street View — sits to the left of the star (or rightmost if no star)
  const svRight = opts.showStar && opts.starId ? 28 : 2
  parts.push(makeStreetViewBtn(opts.lat, opts.lon, svRight))
  // Route calc — sits to the left of street view
  const routeRight = svRight + 26
  parts.push(makeRouteCalcBtn(opts.routeLabel, opts.lat, opts.lon, routeRight))

  // Reserve room for: route(22) + svGap(4) + sv(22) + starGap(4) + star(22) + edge(2)
  const paddingRightPx = routeRight + 22 + 4
  return { html: parts.join(''), paddingRightPx }
}

/**
 * Mapeamento de chaves conhecidas em `pin.detalhes` para ícone + label de
 * exibição no popup. Mantém os mesmos emojis usados em makeJazidaPopupHtml
 * para consistência visual (📐 área, ⚙️ fase, 🏢 titular/CNPJ, etc.).
 */
const DETAIL_RENDERERS: Record<
  string,
  { icon: string; format?: (v: unknown) => string }
> = {
  processo:    { icon: '🆔', format: (v) => `Processo ${String(v)}` },
  substancia:  { icon: '⛏️' },
  area_ha:     { icon: '📐', format: (v) => `${v} ha` },
  fase:        { icon: '⚙️' },
  municipio:   { icon: '📍' },
  titulares:   {
    icon: '🏢',
    format: (v) => Array.isArray(v) ? v.join(', ') : String(v),
  },
  cnpj:        { icon: '🏢' },
  telefone:    { icon: '📞' },
  email:       { icon: '✉️' },
  distancia_km:{ icon: '📏', format: (v) => `${v} km` },
  observacao:  { icon: '📝' },
}

/**
 * Converte uma chave snake_case em rótulo legível para chaves não
 * reconhecidas em DETAIL_RENDERERS (ex.: "tipo_uso" → "Tipo Uso").
 */
function humanizeDetailKey(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

function renderDetailRow(key: string, value: unknown): string | null {
  if (value == null || value === '') return null
  const renderer = DETAIL_RENDERERS[key]
  if (renderer) {
    const text = renderer.format ? renderer.format(value) : String(value)
    if (!text) return null
    return `${renderer.icon} ${text}`
  }
  // Chave desconhecida → linha genérica "Rótulo: valor".
  const display = Array.isArray(value) ? value.join(', ') : String(value)
  if (!display) return null
  return `<span style="color:#475569"><strong>${humanizeDetailKey(key)}:</strong> ${display}</span>`
}

function buildAddressPinPopup(pin: AddressPin): string {
  const rows: string[] = []

  const consultado = pin.endereco_consultado
  const resolvido = pin.endereco_resolvido
  if (resolvido) rows.push(`🏠 ${resolvido}`)
  if (consultado && consultado !== resolvido) {
    rows.push(`<span style="color:#64748b;font-size:11px">Consulta: ${consultado}</span>`)
  }

  // Detalhes enriquecidos (jazida/empresa) — vêm via parâmetro `detalhes`
  // do tool plotar_endereco. Renderizamos as chaves conhecidas primeiro na
  // ordem definida em DETAIL_ORDER e o restante depois.
  const detalhes = pin.detalhes ?? {}
  const DETAIL_ORDER = [
    'processo', 'substancia', 'fase', 'area_ha',
    'municipio', 'titulares', 'cnpj',
    'telefone', 'email', 'distancia_km', 'observacao',
  ]
  const seen = new Set<string>()
  for (const key of DETAIL_ORDER) {
    if (key in detalhes) {
      const html = renderDetailRow(key, detalhes[key])
      if (html) rows.push(html)
      seen.add(key)
    }
  }
  for (const [key, value] of Object.entries(detalhes)) {
    if (seen.has(key)) continue
    const html = renderDetailRow(key, value)
    if (html) rows.push(html)
  }

  rows.push(`<span style="color:#64748b;font-size:11px">📍 ${pin.lat.toFixed(5)}, ${pin.lon.toFixed(5)}</span>`)
  if (pin.fonte === 'geocodificado') {
    rows.push(`<span style="color:#64748b;font-size:10px">Geocodificado via Azure Maps</span>`)
  }

  const actions = makePopupActions({
    lat: pin.lat,
    lon: pin.lon,
    routeLabel: pin.label,
    showStar: false,
    starId: null,
  })

  // Se o pin carrega o número do processo ANM, oferecemos o mesmo botão
  // "Ver polígono da jazida" disponível nos popups de jazida normal.
  const processoId = typeof detalhes.processo === 'string' ? detalhes.processo : ''
  const polygonBtn = processoId ? makePolygonToggleBtn(processoId, 'anm') : ''

  return `<div style="font-size:12px;line-height:1.5;max-width:280px;position:relative">
    ${actions.html}
    <div style="padding-right:${actions.paddingRightPx}px">
      <strong style="font-size:13px;display:block;margin-bottom:4px;color:#b45309">📌 ${pin.label}</strong>
      ${rows.map((r) => `<div>${r}</div>`).join('')}
    </div>
    ${polygonBtn}
  </div>`
}

const MUNICIPIO_COLORS: Record<string, { fill: string; line: string; fillOpacity: number }> = {
  map:       { fill: '#22c55e', line: '#16a34a', fillOpacity: 0.05 },
  satellite: { fill: '#4ade80', line: '#4ade80', fillOpacity: 0.08 },
}

const CHAT_CONTEXT_COLORS: Record<
  string,
  { fill: string; linePorto: string; lineRail: string; fillOpacity: number }
> = {
  map:       { fill: '#3b82f6', linePorto: '#1d4ed8', lineRail: '#b45309', fillOpacity: 0.22 },
  satellite: { fill: '#60a5fa', linePorto: '#93c5fd', lineRail: '#fdba74', fillOpacity: 0.28 },
}

/** Generates a GeoJSON Polygon approximating a circle on the Earth's surface. */
function makeCircleGeoJSON(
  centerLng: number,
  centerLat: number,
  radiusKm: number,
  numPoints = 64,
): GeoJSON.Feature<GeoJSON.Polygon> {
  const R = 6371
  const latRad = (centerLat * Math.PI) / 180
  const coords: [number, number][] = []

  for (let i = 0; i <= numPoints; i++) {
    const angle = (i / numPoints) * 2 * Math.PI
    const dx = radiusKm * Math.cos(angle)
    const dy = radiusKm * Math.sin(angle)
    const newLat = centerLat + (dy / R) * (180 / Math.PI)
    const newLng = centerLng + (dx / R) * (180 / Math.PI) / Math.cos(latRad)
    coords.push([newLng, newLat])
  }

  return {
    type: 'Feature',
    properties: {},
    geometry: { type: 'Polygon', coordinates: [coords] },
  }
}

function toFeatureCollection(features: GeoJSONFeature[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: features as unknown as GeoJSON.Feature[],
  }
}

/* ── Estilos de base ──────────────────────────────────────────────── */

const STYLE_MAP: string =
  (import.meta.env.VITE_MAPLIBRE_STYLE as string | undefined) ||
  'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json'

// Satélite: ESRI World Imagery — gratuito, sem chave
const STYLE_SATELLITE: StyleSpecification = {
  version: 8,
  glyphs: 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf',
  sources: {
    satellite: {
      type: 'raster',
      tiles: [
        'https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      ],
      tileSize: 256,
      maxzoom: 19,
      attribution: '© Esri, Maxar, Earthstar Geographics',
    },
    'satellite-labels': {
      type: 'raster',
      tiles: [
        'https://services.arcgisonline.com/arcgis/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
      ],
      tileSize: 256,
      maxzoom: 19,
    },
  },
  layers: [
    { id: 'satellite-layer', type: 'raster', source: 'satellite' },
    { id: 'satellite-labels-layer', type: 'raster', source: 'satellite-labels' },
  ],
}

function getStyleFor(base: 'map' | 'satellite'): string | StyleSpecification {
  return base === 'satellite' ? STYLE_SATELLITE : STYLE_MAP
}

/* ── Componente ───────────────────────────────────────────────────── */

interface Props {
  height?: number | string
}

/** Build a small colored pin SVG for supplier/jazida markers. */
function makeMarkerSvg(color: string): string {
  // display:block is critical — SVG is inline by default which creates a
  // descender gap inside the container, misaligning the anchor point used
  // by MapLibre (translate(-50%,-100%)) from the visual pin tip.
  return `<svg xmlns="http://www.w3.org/2000/svg" style="display:block" width="22" height="28" viewBox="0 0 22 28" fill="none">
    <path d="M11 0C4.92 0 0 4.92 0 11c0 8.25 11 17 11 17s11-8.75 11-17C22 4.92 17.08 0 11 0z" fill="${color}"/>
    <circle cx="11" cy="11" r="5" fill="white"/>
  </svg>`
}

function makeStarSvg(id: string): string {
  return `<button class="fav-star-btn" data-fav-id="${id}" style="background:none;border:none;cursor:pointer;padding:3px;position:absolute;top:2px;right:2px;z-index:1;transition:transform .15s ease" title="Salvar fornecedor"
    onmouseover="this.style.transform='scale(1.2)'" onmouseout="this.style.transform='scale(1)'">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
    </svg>
  </button>`
}

function makeEmpresaPopupHtml(p: MapaPontoEmpresa, showStar: boolean): string {
  const nome = p.nome_fantasia || p.razao_social || p.label
  const id = normalizeId(p.cnpj_completo)
  const rows: string[] = []
  if (p.cnpj_completo)  rows.push(`<span style="color:#64748b">CNPJ: ${p.cnpj_completo}</span>`)
  if (p.municipio)      rows.push(`📍 ${p.municipio}${p.uf ? `/${p.uf}` : ''}`)
  if (p.porte)          rows.push(`🏢 ${p.porte}`)
  if (p.capital_social) rows.push(`💰 R$ ${p.capital_social.toLocaleString('pt-BR', { minimumFractionDigits: 2 })}`)
  if (p.telefone)       rows.push(`📞 ${p.telefone}`)
  if (p.email)          rows.push(`✉️ ${p.email}`)
  if (p.endereco)       rows.push(`🏠 ${p.endereco}`)
  if (p.cnae_descricao) rows.push(`<span style="color:#64748b;font-size:10px">${p.cnae_descricao}</span>`)
  if (p.distancia_km)   rows.push(`<span style="color:#64748b">📏 ${p.distancia_km} km</span>`)

  const routeLabel = p.cnpj_completo
    ? `${nome} (CNPJ ${p.cnpj_completo})`
    : nome
  const actions = makePopupActions({
    lat: p.lat, lon: p.lon, routeLabel, showStar, starId: id || null,
  })

  return `<div style="font-size:12px;line-height:1.5;position:relative;padding-right:${actions.paddingRightPx}px">
    ${actions.html}
    <strong style="font-size:13px;display:block;margin-bottom:4px">${nome}</strong>
    ${rows.map((r) => `<div>${r}</div>`).join('')}
  </div>`
}

function makeEmpresaGroupPopupHtml(items: MapaPontoEmpresa[], showStar: boolean): string {
  const cards = items.map((p) => {
    const nome = p.nome_fantasia || p.razao_social || p.label
    const id = normalizeId(p.cnpj_completo)
    const detail: string[] = []
    if (p.cnpj_completo) detail.push(p.cnpj_completo)
    if (p.municipio) detail.push(`📍 ${p.municipio}${p.uf ? `/${p.uf}` : ''}`)
    if (p.telefone) detail.push(`📞 ${p.telefone}`)
    if (p.email) detail.push(`✉️ ${p.email}`)
    if (p.endereco) detail.push(`🏠 ${p.endereco}`)

    const routeLabel = p.cnpj_completo ? `${nome} (CNPJ ${p.cnpj_completo})` : nome
    const actions = makePopupActions({
      lat: p.lat, lon: p.lon, routeLabel, showStar, starId: id || null,
    })

    return `<div style="padding:6px 0;border-bottom:1px solid #e2e8f0;position:relative;padding-right:${actions.paddingRightPx}px">
      ${actions.html}
      <strong style="font-size:12px">${nome}</strong>
      ${detail.map(d => `<div style="font-size:11px;color:#475569">${d}</div>`).join('')}
    </div>`
  })

  return `<div style="font-size:12px;max-height:300px;overflow-y:auto">
    <div style="font-weight:600;margin-bottom:4px;color:#334155">${items.length} empresas neste local</div>
    ${cards.join('')}
  </div>`
}

function makeClusterEl(count: number, color: string): HTMLDivElement {
  // Single 38x38 SVG contains BOTH the pin and the badge entirely — no overflow.
  // Pin path is offset by (8,10) so its tip is at (19, 38) = bottom-center of container.
  // Badge is drawn at top-right corner inside the SVG.
  // No nested HTML, no overflow:visible, no stacking contexts → zero drift on zoom.
  const el = document.createElement('div')
  el.style.cssText = 'width:38px;height:38px;display:block;line-height:0;cursor:pointer'
  el.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg"
    style="display:block"
    width="38" height="38" viewBox="0 0 38 38" fill="none">
    <g transform="translate(8,10)">
      <path d="M11 0C4.92 0 0 4.92 0 11c0 8.25 11 17 11 17s11-8.75 11-17C22 4.92 17.08 0 11 0z" fill="${color}"/>
      <circle cx="11" cy="11" r="5" fill="white"/>
    </g>
    <circle cx="30" cy="9" r="8" fill="#166534" stroke="white" stroke-width="1.5"/>
    <text x="30" y="12.5" text-anchor="middle"
      font-size="10" font-weight="bold" fill="white"
      font-family="system-ui,-apple-system,sans-serif">${count}</text>
  </svg>`
  return el
}

type CoordGroup<T> = { key: string; lat: number; lon: number; items: T[] }

function groupByCoord<T extends { lat: number; lon: number }>(items: T[]): CoordGroup<T>[] {
  const map = new Map<string, CoordGroup<T>>()
  for (const item of items) {
    const k = `${item.lat.toFixed(4)},${item.lon.toFixed(4)}`
    let g = map.get(k)
    if (!g) { g = { key: k, lat: item.lat, lon: item.lon, items: [] }; map.set(k, g) }
    g.items.push(item)
  }
  return [...map.values()]
}


function makePolygonToggleBtn(
  processoId: string,
  polygonFetch: 'anm' | 'cprm' | 'car' | 'none' = 'anm',
): string {
  if (polygonFetch === 'none') return ''
  // For CAR, use the raw cod_car as-is (no digit-normalization)
  const nid = polygonFetch === 'car' ? processoId : normalizeId(processoId)
  if (!nid) return ''

  const state = useMapStore.getState()
  const active = state.activeJazidaPolygonIds.has(nid)
  const loading = state.jazidaPolygonLoading.has(nid)

  const label = loading ? 'Carregando…' : active ? 'Ocultar area' : 'Ver area'
  const bg = loading ? '#64748b' : active ? '#059669' : '#16a34a'
  // data-polygon-id  → normalised (digits only) — used as cache key
  // data-raw-processo → original format (e.g. "813.866/1974") — sent to API
  // data-polygon-fetch → anm (jazida ANM) | cprm (ocorrência mr_cprm_v001)
  return `<button class="polygon-toggle-btn"
    data-polygon-id="${nid}"
    data-polygon-fetch="${polygonFetch}"
    data-raw-processo="${processoId}"
    style="display:block;width:100%;margin-top:8px;padding:6px 10px;font-size:12px;font-weight:600;
    color:#fff;background:${bg};border:none;border-radius:5px;cursor:pointer;
    transition:background .15s,opacity .15s;text-align:center;letter-spacing:0.01em"
    onmouseover="this.style.opacity='0.85'" onmouseout="this.style.opacity='1'">${label}</button>`
}

function makeObraPopupHtml(obra: Projeto): string {
  const { lat, lon } = obra.localizacao!
  const rows: string[] = []
  const tipoLabel   = PROJETO_TIPO_LABEL[obra.tipo]   ?? obra.tipo
  const statusLabel = PROJETO_STATUS_LABEL[obra.status] ?? obra.status
  rows.push(`⚙️ ${tipoLabel}`)
  rows.push(`<span style="color:#16a34a;font-weight:600">${statusLabel}</span>`)
  if (obra.municipio) rows.push(`📍 ${obra.municipio}${obra.uf ? `/${obra.uf}` : ''}`)
  if (obra.endereco)  rows.push(`🏠 ${obra.endereco}`)
  rows.push(`📏 Raio de busca: ${obra.raio_busca_km} km`)
  if (obra.total_analises > 0)
    rows.push(`📋 ${obra.total_analises} análise${obra.total_analises > 1 ? 's' : ''}`)

  const actions = makePopupActions({
    lat, lon,
    routeLabel: obra.nome,
    showStar: false,
    starId: null,
  })

  return `<div style="font-size:12px;line-height:1.5;position:relative;max-width:280px;word-break:break-word">
    ${actions.html}
    <div style="padding-right:${actions.paddingRightPx}px">
      <strong style="font-size:13px;display:block;margin-bottom:4px;color:#16a34a">🏗️ ${obra.nome}</strong>
      ${rows.map((r) => `<div>${r}</div>`).join('')}
    </div>
  </div>`
}

function makeJazidaPopupHtml(p: MapaPonto, showStar: boolean): string {
  const isGeo          = p.tipo === 'geoquimica'
  const isCprm         = p.tipo === 'ocorrencia_mineral'
  const isAfloramento  = p.tipo === 'afloramento'
  const isCar          = p.tipo === 'car'

  const subst = isGeo
    ? (p.analitos?.slice(0, 8).join(', ') ?? p.substancia ?? 'Amostra geoquímica')
    : (p.substancias?.join(', ') ?? p.label)

  const muns  = p.municipios?.join(', ') ?? ''
  const ufs   = p.uf?.join('/') ?? ''
  const local = [muns, ufs].filter(Boolean).join('/')

  const id = isGeo ? normalizeAmostraId(p.id) : normalizeId(p.id)

  // ── CAR rural property popup ─────────────────────────────────────────────
  if (isCar) {
    const nome   = p.nome || p.municipios?.[0] || p.id || 'Imóvel Rural'
    const tipo   = p.substancias?.[0] || 'Imóvel Rural'
    const rows: string[] = []
    if (p.status)
      rows.push(`<span style="color:#16a34a;font-weight:600">${p.status}</span>`)
    if (local)           rows.push(`📍 ${local}`)
    if (p.area_ha)       rows.push(`📐 ${p.area_ha} ha`)
    if (p.distancia_km)  rows.push(`<span style="color:#64748b">📏 ${p.distancia_km} km</span>`)

    const actions = makePopupActions({
      lat: p.lat, lon: p.lon, routeLabel: nome, showStar: false, starId: null,
    })

    return `<div style="font-size:12px;line-height:1.5;position:relative;max-width:300px;word-break:break-word">
      ${actions.html}
      <div style="padding-right:${actions.paddingRightPx}px">
        <strong style="font-size:13px;display:block;margin-bottom:2px">${nome}</strong>
        <div style="color:#15803d;margin-bottom:4px">${tipo} · CAR</div>
        ${rows.map((r) => `<div>${r}</div>`).join('')}
      </div>
      ${makePolygonToggleBtn(p.id, 'car')}
    </div>`
  }

  // ── Afloramento geológico popup ──────────────────────────────────────────
  if (isAfloramento) {
    const rochas = p.substancias?.[0] || p.label || 'Afloramento'
    const rows: string[] = []
    if (p.substancia)    rows.push(`🪨 ${p.substancia}`)
    if (local)           rows.push(`📍 ${local}`)
    if (p.projeto)       rows.push(`📋 ${p.projeto}`)
    if (p.descricao)     rows.push(`<span style="color:#64748b">${p.descricao.slice(0, 200)}</span>`)
    if (p.distancia_km)  rows.push(`<span style="color:#64748b">📏 ${p.distancia_km} km</span>`)

    const actions = makePopupActions({
      lat: p.lat, lon: p.lon, routeLabel: rochas, showStar: false, starId: null,
    })

    return `<div style="font-size:12px;line-height:1.5;position:relative;max-width:300px;word-break:break-word">
      ${actions.html}
      <div style="padding-right:${actions.paddingRightPx}px">
        <strong style="font-size:13px;display:block;margin-bottom:2px">${rochas}</strong>
        <div style="color:#0ea5e9;margin-bottom:4px">Afloramento Geológico · CPRM</div>
        ${rows.map((r) => `<div>${r}</div>`).join('')}
      </div>
    </div>`
  }

  // ── CPRM occurrence popup ────────────────────────────────────────────────
  if (isCprm) {
    const title = p.nome || p.id || 'Ocorrência'
    const rows: string[] = []
    if (p.importancia)       rows.push(`⭐ ${p.importancia}`)
    if (p.status_economico)  rows.push(`⛏️ ${p.status_economico}`)
    if (local)               rows.push(`📍 ${local}`)
    if (p.projeto)           rows.push(`📋 ${p.projeto}`)
    if (p.provincia)         rows.push(`🗺️ ${p.provincia}`)
    if (p.descricao)         rows.push(`<span style="color:#64748b">${p.descricao.slice(0, 200)}</span>`)
    if (p.distancia_km)      rows.push(`<span style="color:#64748b">📏 ${p.distancia_km} km</span>`)

    const routeLabel = p.nome || p.id || 'ocorrência'
    const actions = makePopupActions({
      lat: p.lat, lon: p.lon, routeLabel, showStar: false, starId: null,
    })

    return `<div style="font-size:12px;line-height:1.5;position:relative;max-width:300px;word-break:break-word">
      ${actions.html}
      <div style="padding-right:${actions.paddingRightPx}px">
        <strong style="font-size:13px;display:block;margin-bottom:2px">${title}</strong>
        <div style="color:#7c3aed;margin-bottom:4px">${subst || 'Ocorrência Mineral'}</div>
        ${rows.map((r) => `<div>${r}</div>`).join('')}
      </div>
      ${makePolygonToggleBtn(p.id, 'cprm')}
    </div>`
  }

  // ── Jazida / geoquímica popup ────────────────────────────────────────────
  const titular = p.titulares?.[0] ?? ''
  const cnpjs = (p.cnpj_titulares ?? []).filter(Boolean)
  const rows: string[] = []
  if (isGeo && p.projeto) rows.push(`📋 ${p.projeto}`)
  if (isGeo && p.substancia) rows.push(`🪨 ${p.substancia}`)
  if (p.fase)          rows.push(`⚙️ ${p.fase}`)
  if (local)           rows.push(`📍 ${local}`)
  if (titular)         rows.push(`🏢 ${titular}`)
  if (cnpjs.length)    rows.push(`🆔 ${cnpjs.join(', ')}`)
  if (p.endereco)      rows.push(`🏠 ${p.endereco}`)
  if (p.telefone)      rows.push(`📞 ${p.telefone}`)
  if (p.email)         rows.push(`✉️ ${p.email}`)
  if (p.area_ha)       rows.push(`📐 ${p.area_ha} ha`)
  if (p.distancia_km)  rows.push(`<span style="color:#64748b">📏 ${p.distancia_km} km</span>`)

  const routeLabel = isGeo ? (p.id || p.label) : (p.id ? `processo ${p.id}` : (p.label || 'jazida'))
  const actions = makePopupActions({
    lat: p.lat, lon: p.lon, routeLabel, showStar: showStar && !isGeo, starId: id || null,
  })

  // Action icons are absolutely positioned at the top-right; text content
  // gets padding-right to avoid overlap. The polygon toggle lives OUTSIDE
  // the padded wrapper so it always spans the full card width.
  return `<div style="font-size:12px;line-height:1.5;position:relative;max-width:300px;word-break:break-word">
    ${actions.html}
    <div style="padding-right:${actions.paddingRightPx}px">
      <strong style="font-size:13px;display:block;margin-bottom:2px">${p.id}</strong>
      <div style="color:#059669;margin-bottom:4px">${subst}</div>
      ${rows.map((r) => `<div>${r}</div>`).join('')}
    </div>
    ${!isGeo ? makePolygonToggleBtn(p.id, p.polygonFetch ?? 'anm') : ''}
  </div>`
}


export function MapContainer({ height = 460 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  /** Evita setStyle redundante no 1º mount (apagava camadas WMS/custom). */
  const baseStyleBootstrappedRef = useRef(false)
  const obraMarkerRef = useRef<maplibregl.Marker | null>(null)
  const obraOnMapRef = useRef(false)
  const activeObraRef = useRef<typeof activeObra>(undefined)
  const empresaMarkersRef = useRef<MarkerEntry[]>([])
  const jazidaMarkersRef  = useRef<MarkerEntry[]>([])
  const addressPinMarkersRef = useRef<MarkerEntry[]>([])
  const fornecedorDataRef = useRef(new Map<string, AddFornecedorInput>())

  const viewport          = useMapStore((s) => s.viewport)
  const setViewport       = useMapStore((s) => s.setViewport)
  const baseStyle         = useMapStore((s) => s.baseStyle)
  const flyTo             = useMapStore((s) => s.flyTo)
  const selectFeature     = useMapStore((s) => s.selectFeature)
  const selectedFeatureId = useMapStore((s) => s.selectedFeatureId)

  const markersByTopic    = useMapStore((s) => s.markersByTopic)
  const activeTopicFilter = useMapStore((s) => s.activeTopicFilter)

  // Per-layer visibility selectors — each triggers only its own effect
  const obraPointVisible       = useMapStore((s) => s.layerVisibility['obra-point'])
  const empresaVisible         = useMapStore((s) => s.layerVisibility['empresa-points'])
  const jazidaVisible          = useMapStore((s) => s.layerVisibility['jazida-points'])
  const jazidaPolyVisible      = useMapStore((s) => s.layerVisibility['jazida-polygons'])
  const municipioBorderVisible = useMapStore((s) => s.layerVisibility['municipio-borders'])
  const chatContextVisible     = useMapStore((s) => s.layerVisibility['chat-context'])
  const searchRadiusVisible    = useMapStore((s) => s.layerVisibility['search-radius'])

  const municipioPolygons      = useMapStore((s) => s.municipioPolygons)
  const chatGeometries         = useMapStore((s) => s.chatGeometries)
  const activeJazidaPolygonIds = useMapStore((s) => s.activeJazidaPolygonIds)
  const jazidaPolygonCache     = useMapStore((s) => s.jazidaPolygonCache)
  const routeLines             = useMapStore((s) => s.routeLines)
  const isochroneFeature       = useMapStore((s) => s.isochroneFeature)
  const addressPins            = useMapStore((s) => s.addressPins)
  const routesVisible          = useMapStore((s) => s.layerVisibility['routes'])
  const isochroneVisible       = useMapStore((s) => s.layerVisibility['isochrone'])
  const addressPinsVisible     = useMapStore((s) => s.layerVisibility['address-pins'])
  const cprmVisible            = useMapStore((s) => s.layerVisibility['cprm-geologico'])


  const activeProjetoId = useUiStore((s) => s.activeProjetoId)
  const { data: activeObra } = useProjeto(activeProjetoId ?? undefined)
  const savedFavorites = useFavoritesStore((s) => s.saved)

  const addMut = useAddFornecedor()
  const removeMut = useRemoveFornecedor()
  const addMutRef = useRef(addMut)
  const removeMutRef = useRef(removeMut)
  addMutRef.current = addMut
  removeMutRef.current = removeMut

  // Keep ref in sync so style-swap handler can read current obra without stale closure
  activeObraRef.current = activeObra

  /* Inicializa o mapa uma única vez */
  useEffect(() => {
    const el = containerRef.current
    if (!el || mapRef.current) return

    const map = new maplibregl.Map({
      container: el,
      style: getStyleFor(baseStyle),
      center: [viewport.lng, viewport.lat],
      zoom: viewport.zoom,
      attributionControl: false,
      fadeDuration: 0,
    })

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')

    map.once('load', () => map.resize())

    map.on('moveend', () => {
      const center = map.getCenter()
      setViewport({ lng: center.lng, lat: center.lat, zoom: map.getZoom() })
    })

    mapRef.current = map

    let timer: ReturnType<typeof setTimeout>
    const ro = new ResizeObserver(() => {
      clearTimeout(timer)
      timer = setTimeout(() => { mapRef.current?.resize() }, RESIZE_DEBOUNCE_MS)
    })
    ro.observe(el)

    return () => {
      clearTimeout(timer)
      ro.disconnect()
      map.remove()
      mapRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* Troca o style preservando viewport */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (!baseStyleBootstrappedRef.current) {
      baseStyleBootstrappedRef.current = true
      return
    }
    const style = getStyleFor(baseStyle)
    map.setStyle(style, { diff: false })
    map.once('styledata', () => {
      map.resize()
      // Re-draw radius circle after style reload with correct colors for new style
      const obra = activeObraRef.current
      if (!obra?.localizacao) return
      const { lat, lon } = obra.localizacao
      const colors = RADIUS_COLORS[baseStyle] ?? RADIUS_COLORS.map
      if (!map.getSource(RADIUS_SOURCE)) {
        map.addSource(RADIUS_SOURCE, { type: 'geojson', data: makeCircleGeoJSON(lon, lat, obra.raio_busca_km) })
        map.addLayer({ id: RADIUS_FILL_LAYER, type: 'fill', source: RADIUS_SOURCE, paint: { 'fill-color': colors.fill, 'fill-opacity': colors.fillOpacity } })
        map.addLayer({ id: RADIUS_OUTLINE_LAYER, type: 'line', source: RADIUS_SOURCE, paint: { 'line-color': colors.line, 'line-width': 1.5, 'line-dasharray': [4, 3], 'line-opacity': 0.7 } })
      }
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseStyle])

  /* Sync store → map (flyTo) */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const c = map.getCenter()
    const same =
      Math.abs(c.lng - viewport.lng) < 0.0001 &&
      Math.abs(c.lat - viewport.lat) < 0.0001 &&
      Math.abs(map.getZoom() - viewport.zoom) < 0.01
    if (!same) map.flyTo({ center: [viewport.lng, viewport.lat], zoom: viewport.zoom })
  }, [viewport])

  /* ── Obra pin ─────────────────────────────────────────────── */
  useEffect(() => {
    // Remove previous marker
    if (obraMarkerRef.current) {
      obraMarkerRef.current.remove()
      obraMarkerRef.current = null
      obraOnMapRef.current = false
    }

    if (!activeObra?.localizacao) return

    const { lat, lon } = activeObra.localizacao

    function addMarker() {
      const map = mapRef.current
      if (!map) return

      const el = document.createElement('div')
      el.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" style="display:block" width="32" height="40" viewBox="0 0 32 40" fill="none">
        <path d="M16 0C7.16 0 0 7.16 0 16c0 12 16 24 16 24s16-12 16-24C32 7.16 24.84 0 16 0z" fill="#16a34a"/>
        <circle cx="16" cy="15" r="7" fill="white"/>
        <circle cx="16" cy="15" r="4" fill="#16a34a"/>
      </svg>`
      el.style.cssText = 'cursor:pointer;width:32px;height:40px;display:block;line-height:0'
      el.title = activeObra!.nome

      const popup = new maplibregl.Popup({
        offset: [0, -40],
        closeButton: true,
        closeOnClick: false,
        maxWidth: '300px',
      }).setHTML(makeObraPopupHtml(activeObra!))

      const marker = new maplibregl.Marker({
        element: el,
        anchor: 'top-left',
        offset: [-16, -40],
        pitchAlignment: 'viewport',
        rotationAlignment: 'viewport',
      })
        .setLngLat([lon, lat])
        .setPopup(popup)

      obraMarkerRef.current = marker

      // Only add to map if layer is currently visible
      if (useMapStore.getState().layerVisibility['obra-point']) {
        marker.addTo(map)
        obraOnMapRef.current = true
      }

      flyTo(lon, lat, 14)
    }

    const map = mapRef.current
    if (!map) return

    // If map is already loaded, add immediately; otherwise wait for load
    if (map.loaded()) {
      addMarker()
    } else {
      map.once('load', addMarker)
    }

    return () => {
      obraMarkerRef.current?.remove()
      obraMarkerRef.current = null
      obraOnMapRef.current = false
    }
  }, [activeObra, flyTo])

  /* ── Search-radius circle ──────────────────────────────────── */
  useEffect(() => {
    function removeRadius(map: maplibregl.Map) {
      if (map.getLayer(RADIUS_FILL_LAYER)) map.removeLayer(RADIUS_FILL_LAYER)
      if (map.getLayer(RADIUS_OUTLINE_LAYER)) map.removeLayer(RADIUS_OUTLINE_LAYER)
      if (map.getSource(RADIUS_SOURCE)) map.removeSource(RADIUS_SOURCE)
    }

    function addRadius() {
      const map = mapRef.current
      if (!map || !activeObra?.localizacao) return

      const { lat, lon } = activeObra.localizacao
      const km = activeObra.raio_busca_km
      const colors = RADIUS_COLORS[baseStyle] ?? RADIUS_COLORS.map

      removeRadius(map)

      map.addSource(RADIUS_SOURCE, { type: 'geojson', data: makeCircleGeoJSON(lon, lat, km) })

      const radiusVisible = useMapStore.getState().layerVisibility['search-radius']
      const layoutVis = radiusVisible ? 'visible' : 'none'

      map.addLayer({
        id: RADIUS_FILL_LAYER,
        type: 'fill',
        source: RADIUS_SOURCE,
        layout: { visibility: layoutVis },
        paint: { 'fill-color': colors.fill, 'fill-opacity': colors.fillOpacity },
      })

      map.addLayer({
        id: RADIUS_OUTLINE_LAYER,
        type: 'line',
        source: RADIUS_SOURCE,
        layout: { visibility: layoutVis },
        paint: {
          'line-color': colors.line,
          'line-width': 1.5,
          'line-dasharray': [4, 3],
          'line-opacity': 0.7,
        },
      })
    }

    const map = mapRef.current
    if (!map) return

    if (!activeObra?.localizacao) {
      if (map.isStyleLoaded()) removeRadius(map)
      return
    }

    if (map.isStyleLoaded()) {
      addRadius()
    } else {
      map.once('load', addRadius)
    }

    return () => {
      const m = mapRef.current
      if (m?.isStyleLoaded()) removeRadius(m)
    }
  }, [activeObra, baseStyle])

  /* ── Layer visibility: obra-point ────────────────────────────── */
  useEffect(() => {
    const marker = obraMarkerRef.current
    const map = mapRef.current
    if (!marker || !map) return

    if (obraPointVisible && !obraOnMapRef.current) {
      marker.addTo(map)
      obraOnMapRef.current = true
    } else if (!obraPointVisible && obraOnMapRef.current) {
      marker.remove()
      obraOnMapRef.current = false
    }
  }, [obraPointVisible])

  /* ── Layer visibility: empresa-points ───────────────────────── */
  useEffect(() => {
    empresaMarkersRef.current.forEach(({ marker }) => {
      marker.getElement().style.display = empresaVisible ? '' : 'none'
    })
  }, [empresaVisible])

  /* ── Layer visibility: jazida-points ────────────────────────── */
  useEffect(() => {
    jazidaMarkersRef.current.forEach(({ marker }) => {
      marker.getElement().style.display = jazidaVisible ? '' : 'none'
    })
  }, [jazidaVisible])

  /* ── Visual ring on jazida pins with active polygon ─────────── */
  useEffect(() => {
    jazidaMarkersRef.current.forEach(({ id, marker }) => {
      const el = marker.getElement()
      if (activeJazidaPolygonIds.has(id)) {
        el.style.filter = 'drop-shadow(0 0 4px #059669) drop-shadow(0 0 8px #059669)'
      } else {
        el.style.filter = ''
      }
    })
  }, [activeJazidaPolygonIds])

  /* ── Layer visibility: search-radius ────────────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map?.isStyleLoaded()) return
    const vis = searchRadiusVisible ? 'visible' : 'none'
    if (map.getLayer(RADIUS_FILL_LAYER)) map.setLayoutProperty(RADIUS_FILL_LAYER, 'visibility', vis)
    if (map.getLayer(RADIUS_OUTLINE_LAYER)) map.setLayoutProperty(RADIUS_OUTLINE_LAYER, 'visibility', vis)
  }, [searchRadiusVisible])

  /* ── Jazida polygon layer (on-demand — reads from jazidaPolygonCache) ── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    function removeLayers(m: maplibregl.Map) {
      if (m.getLayer(JAZIDA_POLY_FILL)) m.removeLayer(JAZIDA_POLY_FILL)
      if (m.getLayer(JAZIDA_POLY_LINE)) m.removeLayer(JAZIDA_POLY_LINE)
      if (m.getSource(JAZIDA_POLY_SOURCE)) m.removeSource(JAZIDA_POLY_SOURCE)
    }

    function addLayers() {
      const m = mapRef.current
      if (!m) return

      // Collect all features for active processo IDs that are already cached
      const activeFeatures: GeoJSONFeature[] = []
      for (const pid of activeJazidaPolygonIds) {
        const features = jazidaPolygonCache[pid]
        if (features?.length) activeFeatures.push(...features)
      }

      removeLayers(m)
      if (activeFeatures.length === 0) return

      const colors = JAZIDA_COLORS[baseStyle] ?? JAZIDA_COLORS.map
      const vis = useMapStore.getState().layerVisibility['jazida-polygons'] ? 'visible' : 'none'

      m.addSource(JAZIDA_POLY_SOURCE, {
        type: 'geojson',
        data: toFeatureCollection(activeFeatures),
      })
      m.addLayer({
        id: JAZIDA_POLY_FILL,
        type: 'fill',
        source: JAZIDA_POLY_SOURCE,
        layout: { visibility: vis },
        paint: { 'fill-color': colors.fill, 'fill-opacity': colors.fillOpacity },
      })
      m.addLayer({
        id: JAZIDA_POLY_LINE,
        type: 'line',
        source: JAZIDA_POLY_SOURCE,
        layout: { visibility: vis },
        paint: { 'line-color': colors.line, 'line-width': 1.5, 'line-opacity': 0.8 },
      })
    }

    if (activeJazidaPolygonIds.size === 0) {
      if (map.isStyleLoaded()) removeLayers(map)
      return
    }

    if (map.isStyleLoaded()) {
      addLayers()
    } else {
      map.once('load', addLayers)
    }

    return () => {
      const m = mapRef.current
      if (m?.isStyleLoaded()) removeLayers(m)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jazidaPolygonCache, activeJazidaPolygonIds, baseStyle])

  /* ── Jazida polygon cursor (no popup — pin popup already shows full detail) ── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    function onEnter() { map!.getCanvas().style.cursor = 'pointer' }
    function onLeave() { map!.getCanvas().style.cursor = '' }

    map.on('mouseenter', JAZIDA_POLY_FILL, onEnter)
    map.on('mouseleave', JAZIDA_POLY_FILL, onLeave)

    return () => {
      map.off('mouseenter', JAZIDA_POLY_FILL, onEnter)
      map.off('mouseleave', JAZIDA_POLY_FILL, onLeave)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jazidaPolygonCache])

  /* ── Layer visibility: jazida-polygons ──────────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map?.isStyleLoaded()) return
    const vis = jazidaPolyVisible ? 'visible' : 'none'
    if (map.getLayer(JAZIDA_POLY_FILL)) map.setLayoutProperty(JAZIDA_POLY_FILL, 'visibility', vis)
    if (map.getLayer(JAZIDA_POLY_LINE)) map.setLayoutProperty(JAZIDA_POLY_LINE, 'visibility', vis)
  }, [jazidaPolyVisible])

  /* ── Municipio border layer ──────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    function removeLayers(m: maplibregl.Map) {
      if (m.getLayer(MUNICIPIO_POLY_FILL)) m.removeLayer(MUNICIPIO_POLY_FILL)
      if (m.getLayer(MUNICIPIO_POLY_LINE)) m.removeLayer(MUNICIPIO_POLY_LINE)
      if (m.getSource(MUNICIPIO_POLY_SOURCE)) m.removeSource(MUNICIPIO_POLY_SOURCE)
    }

    function addLayers() {
      const m = mapRef.current
      if (!m || municipioPolygons.length === 0) return
      removeLayers(m)

      const colors = MUNICIPIO_COLORS[baseStyle] ?? MUNICIPIO_COLORS.map
      const vis = useMapStore.getState().layerVisibility['municipio-borders'] ? 'visible' : 'none'

      m.addSource(MUNICIPIO_POLY_SOURCE, {
        type: 'geojson',
        data: toFeatureCollection(municipioPolygons),
      })
      m.addLayer({
        id: MUNICIPIO_POLY_FILL,
        type: 'fill',
        source: MUNICIPIO_POLY_SOURCE,
        layout: { visibility: vis },
        paint: { 'fill-color': colors.fill, 'fill-opacity': colors.fillOpacity },
      })
      m.addLayer({
        id: MUNICIPIO_POLY_LINE,
        type: 'line',
        source: MUNICIPIO_POLY_SOURCE,
        layout: { visibility: vis },
        paint: {
          'line-color': colors.line,
          'line-width': 2,
          'line-dasharray': [4, 3],
          'line-opacity': 0.7,
        },
      })
    }

    if (municipioPolygons.length === 0) {
      if (map.isStyleLoaded()) removeLayers(map)
      return
    }

    if (map.isStyleLoaded()) {
      addLayers()
    } else {
      map.once('load', addLayers)
    }

    return () => {
      const m = mapRef.current
      if (m?.isStyleLoaded()) removeLayers(m)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [municipioPolygons, baseStyle])

  /* ── Municipio border click → popup ───────────────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: true, maxWidth: '220px' })

    function onClick(e: maplibregl.MapMouseEvent) {
      const features = map!.queryRenderedFeatures(e.point, { layers: [MUNICIPIO_POLY_FILL] })
      if (!features.length) return
      const props = features[0].properties
      const rows: string[] = []
      if (props.nome) rows.push(`<strong>${props.nome}</strong>`)
      if (props.uf) rows.push(props.uf)
      if (props.id_ibge) rows.push(`<span style="color:#64748b;font-size:11px">IBGE: ${props.id_ibge}</span>`)

      popup
        .setLngLat(e.lngLat)
        .setHTML(`<div style="font-size:12px;line-height:1.5">${rows.join('<br>')}</div>`)
        .addTo(map!)
    }

    function onEnter() { map!.getCanvas().style.cursor = 'pointer' }
    function onLeave() { map!.getCanvas().style.cursor = '' }

    map.on('click', MUNICIPIO_POLY_FILL, onClick)
    map.on('mouseenter', MUNICIPIO_POLY_FILL, onEnter)
    map.on('mouseleave', MUNICIPIO_POLY_FILL, onLeave)

    return () => {
      popup.remove()
      map.off('click', MUNICIPIO_POLY_FILL, onClick)
      map.off('mouseenter', MUNICIPIO_POLY_FILL, onEnter)
      map.off('mouseleave', MUNICIPIO_POLY_FILL, onLeave)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [municipioPolygons])

  /* ── Layer visibility: municipio-borders ───────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map?.isStyleLoaded()) return
    const vis = municipioBorderVisible ? 'visible' : 'none'
    if (map.getLayer(MUNICIPIO_POLY_FILL)) map.setLayoutProperty(MUNICIPIO_POLY_FILL, 'visibility', vis)
    if (map.getLayer(MUNICIPIO_POLY_LINE)) map.setLayoutProperty(MUNICIPIO_POLY_LINE, 'visibility', vis)
  }, [municipioBorderVisible])

  /* ── Chat context: portos + ferrovias (SSE ``context_geometry``) ───────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    function removeLayers(m: maplibregl.Map) {
      if (m.getLayer(CHAT_CONTEXT_FILL)) m.removeLayer(CHAT_CONTEXT_FILL)
      if (m.getLayer(CHAT_CONTEXT_LINE)) m.removeLayer(CHAT_CONTEXT_LINE)
      if (m.getSource(CHAT_CONTEXT_SOURCE)) m.removeSource(CHAT_CONTEXT_SOURCE)
    }

    function addLayers() {
      const m = mapRef.current
      if (!m || chatGeometries.length === 0) return
      removeLayers(m)

      const colors = CHAT_CONTEXT_COLORS[baseStyle] ?? CHAT_CONTEXT_COLORS.map
      const vis = useMapStore.getState().layerVisibility['chat-context'] ? 'visible' : 'none'

      m.addSource(CHAT_CONTEXT_SOURCE, {
        type: 'geojson',
        data: toFeatureCollection(chatGeometries),
      })
      m.addLayer({
        id: CHAT_CONTEXT_FILL,
        type: 'fill',
        source: CHAT_CONTEXT_SOURCE,
        filter: ['match', ['geometry-type'], ['Polygon', 'MultiPolygon'], true, false],
        layout: { visibility: vis },
        paint: {
          'fill-color': colors.fill,
          'fill-opacity': colors.fillOpacity,
        },
      })
      m.addLayer({
        id: CHAT_CONTEXT_LINE,
        type: 'line',
        source: CHAT_CONTEXT_SOURCE,
        layout: { visibility: vis },
        paint: {
          'line-color': [
            'case',
            ['==', ['get', 'camada'], 'porto'],
            colors.linePorto,
            colors.lineRail,
          ],
          'line-width': ['case', ['==', ['get', 'camada'], 'porto'], 2, 3],
          'line-opacity': 0.92,
        },
      })
    }

    if (chatGeometries.length === 0) {
      if (map.isStyleLoaded()) removeLayers(map)
      return
    }

    if (map.isStyleLoaded()) {
      addLayers()
    } else {
      map.once('load', addLayers)
    }

    return () => {
      const m = mapRef.current
      if (m?.isStyleLoaded()) removeLayers(m)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatGeometries, baseStyle])

  useEffect(() => {
    const map = mapRef.current
    if (!map?.isStyleLoaded()) return
    const vis = chatContextVisible ? 'visible' : 'none'
    if (map.getLayer(CHAT_CONTEXT_FILL)) map.setLayoutProperty(CHAT_CONTEXT_FILL, 'visibility', vis)
    if (map.getLayer(CHAT_CONTEXT_LINE)) map.setLayoutProperty(CHAT_CONTEXT_LINE, 'visibility', vis)
  }, [chatContextVisible])

  /* ── Route lines layer ───────────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    function removeLayers(m: maplibregl.Map) {
      if (m.getLayer(ROUTE_ARROW_LAYER)) m.removeLayer(ROUTE_ARROW_LAYER)
      if (m.getLayer(ROUTE_LINE_LAYER))  m.removeLayer(ROUTE_LINE_LAYER)
      if (m.getSource(ROUTE_SOURCE))     m.removeSource(ROUTE_SOURCE)
      if (m.getLayer(ROUTE_GAP_LAYER))   m.removeLayer(ROUTE_GAP_LAYER)
      if (m.getSource(ROUTE_GAP_SOURCE)) m.removeSource(ROUTE_GAP_SOURCE)
    }

    function addLayers() {
      const m = mapRef.current
      if (!m || routeLines.length === 0) return
      removeLayers(m)

      const colors = ROUTE_COLORS[baseStyle] ?? ROUTE_COLORS.map
      const palette = baseStyle === 'satellite' ? ROUTE_PALETTE_SAT : ROUTE_PALETTE_MAP
      const vis = useMapStore.getState().layerVisibility['routes'] ? 'visible' : 'none'

      const features = routeLines.map((r, i) => {
        const colorIdx  = i % palette.length
        const offsetIdx = i % ROUTE_OFFSETS_PX.length
        return {
          type: 'Feature' as const,
          properties: {
            id: r.id,
            label: r.label,
            distance_km: r.distance_km,
            duration_min: r.duration_min,
            travel_mode: r.travel_mode,
            gap_origin_km: r.gap_origin_km ?? 0,
            gap_destination_km: r.gap_destination_km ?? 0,
            partial_access: r.partial_access ?? false,
            color_idx: colorIdx,
            color_hex: palette[colorIdx],
            offset_px: ROUTE_OFFSETS_PX[offsetIdx],
            origin_address: r.origin?.endereco_resolvido ?? null,
            destination_address:
              r.destination?.endereco_consultado
              ?? r.destination?.endereco_resolvido
              ?? null,
          },
          geometry: {
            type: 'LineString' as const,
            coordinates: r.points.map((p) => [p.lon, p.lat] as [number, number]),
          },
        }
      })

      // Build dashed off-road segments (origin gap + destination gap) only
      // when above threshold. Each gap is a 2-point LineString from the
      // requested origin/destination to the corresponding polyline endpoint.
      const gapFeatures: GeoJSON.Feature[] = []
      for (const r of routeLines) {
        if (!r.points.length) continue
        const first = r.points[0]
        const last  = r.points[r.points.length - 1]

        if ((r.gap_origin_km ?? 0) > ROUTE_GAP_THRESHOLD_KM) {
          gapFeatures.push({
            type: 'Feature',
            properties: {
              kind: 'origin',
              gap_km: r.gap_origin_km,
              route_id: r.id,
            },
            geometry: {
              type: 'LineString',
              coordinates: [
                [r.origin.lon, r.origin.lat],
                [first.lon, first.lat],
              ],
            },
          })
        }

        if ((r.gap_destination_km ?? 0) > ROUTE_GAP_THRESHOLD_KM) {
          gapFeatures.push({
            type: 'Feature',
            properties: {
              kind: 'destination',
              gap_km: r.gap_destination_km,
              route_id: r.id,
            },
            geometry: {
              type: 'LineString',
              coordinates: [
                [last.lon, last.lat],
                [r.destination.lon, r.destination.lat],
              ],
            },
          })
        }
      }

      m.addSource(ROUTE_SOURCE, {
        type: 'geojson',
        data: { type: 'FeatureCollection', features },
      })
      m.addLayer({
        id: ROUTE_LINE_LAYER,
        type: 'line',
        source: ROUTE_SOURCE,
        layout: { 'line-cap': 'round', 'line-join': 'round', visibility: vis },
        paint: {
          // 1 rota → cor padrão; 2+ rotas → cor por feature.properties.color_hex
          'line-color': routeLines.length <= 1
            ? colors.line
            : ['get', 'color_hex'],
          'line-width': 4,
          'line-opacity': 0.85,
          // Separa linhas sobrepostas perpendicularmente. Sem isso, rotas
          // que compartilham trecho rodoviário (ex.: Aratu e Salvador
          // saindo de Sento Sé) ficam empilhadas em pixels idênticos.
          'line-offset': routeLines.length <= 1
            ? 0
            : ['get', 'offset_px'],
        },
      })
      m.addLayer({
        id: ROUTE_ARROW_LAYER,
        type: 'symbol',
        source: ROUTE_SOURCE,
        layout: {
          'symbol-placement': 'line',
          'symbol-spacing': 80,
          'icon-image': 'border-arrow-white-2',
          'icon-rotate': 90,
          'icon-allow-overlap': true,
          visibility: vis,
        },
      })

      if (gapFeatures.length > 0) {
        m.addSource(ROUTE_GAP_SOURCE, {
          type: 'geojson',
          data: { type: 'FeatureCollection', features: gapFeatures },
        })
        m.addLayer({
          id: ROUTE_GAP_LAYER,
          type: 'line',
          source: ROUTE_GAP_SOURCE,
          layout: { 'line-cap': 'round', 'line-join': 'round', visibility: vis },
          paint: {
            'line-color': colors.gap,
            'line-width': 2.5,
            'line-opacity': 0.9,
            'line-dasharray': [2, 2],
          },
        })
      }

      // Fit map to route bounds (route polyline + dashed gaps so the
      // requested destination is always in view).
      const allCoords = [
        ...features.flatMap((f) => f.geometry.coordinates),
        ...gapFeatures.flatMap((f) =>
          f.geometry.type === 'LineString' ? f.geometry.coordinates : [],
        ),
      ] as [number, number][]
      if (allCoords.length > 0) {
        const bounds = allCoords.reduce(
          (b, c) => b.extend(c),
          new LngLatBounds(allCoords[0], allCoords[0]),
        )
        m.fitBounds(bounds, { padding: 80, maxZoom: 14 })
      }
    }

    if (routeLines.length === 0) {
      if (map.isStyleLoaded()) removeLayers(map)
      return
    }

    if (map.isStyleLoaded()) {
      addLayers()
    } else {
      map.once('load', addLayers)
    }

    return () => {
      const m = mapRef.current
      if (m?.isStyleLoaded()) removeLayers(m)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeLines, baseStyle])

  /* ── Route line popup on click ──────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    const popup    = new maplibregl.Popup({ closeButton: true, closeOnClick: true, maxWidth: '220px' })
    const gapPopup = new maplibregl.Popup({ closeButton: true, closeOnClick: true, maxWidth: '240px' })

    function onClick(e: maplibregl.MapMouseEvent) {
      const features = map!.queryRenderedFeatures(e.point, { layers: [ROUTE_LINE_LAYER] })
      if (!features.length) return
      const p = features[0].properties
      const rows: string[] = []

      const colorHex = String(p.color_hex || '')
      const swatch = colorHex
        ? `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${colorHex};margin-right:6px;vertical-align:middle"></span>`
        : ''
      if (p.label) rows.push(`${swatch}<strong>${p.label}</strong>`)

      if (p.destination_address) rows.push(`🎯 ${p.destination_address}`)
      if (p.distance_km)  rows.push(`📏 ${p.distance_km} km`)
      if (p.duration_min) rows.push(`⏱ ~${Math.round(p.duration_min)} min`)
      if (p.travel_mode)  rows.push(`🚛 ${p.travel_mode}`)

      const gapO = Number(p.gap_origin_km) || 0
      const gapD = Number(p.gap_destination_km) || 0
      if (gapO > ROUTE_GAP_THRESHOLD_KM || gapD > ROUTE_GAP_THRESHOLD_KM) {
        const parts: string[] = []
        if (gapO > ROUTE_GAP_THRESHOLD_KM) parts.push(`${(gapO * 1000).toFixed(0)} m na origem`)
        if (gapD > ROUTE_GAP_THRESHOLD_KM) parts.push(`${(gapD * 1000).toFixed(0)} m no destino`)
        rows.push(
          `<span style="color:#9ca3af">⚠️ Acesso parcial: ${parts.join(' + ')} fora da malha viária mapeada.</span>`,
        )
      }
      popup
        .setLngLat(e.lngLat)
        .setHTML(`<div style="font-size:12px;line-height:1.6">${rows.join('<br>')}</div>`)
        .addTo(map!)
    }

    function onGapClick(e: maplibregl.MapMouseEvent) {
      const features = map!.queryRenderedFeatures(e.point, { layers: [ROUTE_GAP_LAYER] })
      if (!features.length) return
      const p = features[0].properties
      const kind = p.kind === 'origin' ? 'origem' : 'destino'
      const meters = ((Number(p.gap_km) || 0) * 1000).toFixed(0)
      const html = `
        <div style="font-size:12px;line-height:1.5">
          <strong>Trecho off-road (${kind})</strong><br>
          ${meters} m sem cobertura na malha viária do roteador.<br>
          <span style="color:#6b7280">Acesso provável por estrada vicinal não cadastrada, trilha rural ou via sazonal.</span>
        </div>`
      gapPopup.setLngLat(e.lngLat).setHTML(html).addTo(map!)
    }

    function onEnter() { map!.getCanvas().style.cursor = 'pointer' }
    function onLeave() { map!.getCanvas().style.cursor = '' }

    map.on('click', ROUTE_LINE_LAYER, onClick)
    map.on('mouseenter', ROUTE_LINE_LAYER, onEnter)
    map.on('mouseleave', ROUTE_LINE_LAYER, onLeave)

    map.on('click', ROUTE_GAP_LAYER, onGapClick)
    map.on('mouseenter', ROUTE_GAP_LAYER, onEnter)
    map.on('mouseleave', ROUTE_GAP_LAYER, onLeave)

    return () => {
      popup.remove()
      gapPopup.remove()
      map.off('click', ROUTE_LINE_LAYER, onClick)
      map.off('mouseenter', ROUTE_LINE_LAYER, onEnter)
      map.off('mouseleave', ROUTE_LINE_LAYER, onLeave)
      map.off('click', ROUTE_GAP_LAYER, onGapClick)
      map.off('mouseenter', ROUTE_GAP_LAYER, onEnter)
      map.off('mouseleave', ROUTE_GAP_LAYER, onLeave)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeLines])

  /* ── Layer visibility: routes ───────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map?.isStyleLoaded()) return
    const vis = routesVisible ? 'visible' : 'none'
    if (map.getLayer(ROUTE_LINE_LAYER))  map.setLayoutProperty(ROUTE_LINE_LAYER, 'visibility', vis)
    if (map.getLayer(ROUTE_ARROW_LAYER)) map.setLayoutProperty(ROUTE_ARROW_LAYER, 'visibility', vis)
    if (map.getLayer(ROUTE_GAP_LAYER))   map.setLayoutProperty(ROUTE_GAP_LAYER, 'visibility', vis)
  }, [routesVisible])

  /* ── Isochrone layer ─────────────────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    function removeLayers(m: maplibregl.Map) {
      if (m.getLayer(ISO_LINE_LAYER)) m.removeLayer(ISO_LINE_LAYER)
      if (m.getLayer(ISO_FILL_LAYER)) m.removeLayer(ISO_FILL_LAYER)
      if (m.getSource(ISO_SOURCE))    m.removeSource(ISO_SOURCE)
    }

    function addLayers() {
      const m = mapRef.current
      if (!m || !isochroneFeature) return
      removeLayers(m)

      const colors = ISO_COLORS[baseStyle] ?? ISO_COLORS.map
      const vis = useMapStore.getState().layerVisibility['isochrone'] ? 'visible' : 'none'

      m.addSource(ISO_SOURCE, {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: [isochroneFeature as unknown as GeoJSON.Feature],
        },
      })
      m.addLayer({
        id: ISO_FILL_LAYER,
        type: 'fill',
        source: ISO_SOURCE,
        layout: { visibility: vis },
        paint: { 'fill-color': colors.fill, 'fill-opacity': colors.fillOpacity },
      })
      m.addLayer({
        id: ISO_LINE_LAYER,
        type: 'line',
        source: ISO_SOURCE,
        layout: { visibility: vis },
        paint: {
          'line-color': colors.line,
          'line-width': 2,
          'line-dasharray': [3, 2],
          'line-opacity': 0.8,
        },
      })

      // Fit map to isochrone bounds
      const coords = (isochroneFeature.geometry as { coordinates: [number, number][][] }).coordinates?.[0]
      if (coords?.length) {
        const bounds = coords.reduce(
          (b, c) => b.extend(c),
          new LngLatBounds(coords[0], coords[0]),
        )
        m.fitBounds(bounds, { padding: 60, maxZoom: 13 })
      }
    }

    if (!isochroneFeature) {
      if (map.isStyleLoaded()) removeLayers(map)
      return
    }

    if (map.isStyleLoaded()) {
      addLayers()
    } else {
      map.once('load', addLayers)
    }

    return () => {
      const m = mapRef.current
      if (m?.isStyleLoaded()) removeLayers(m)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isochroneFeature, baseStyle])

  /* ── Layer visibility: isochrone ────────────────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map?.isStyleLoaded()) return
    const vis = isochroneVisible ? 'visible' : 'none'
    if (map.getLayer(ISO_FILL_LAYER)) map.setLayoutProperty(ISO_FILL_LAYER, 'visibility', vis)
    if (map.getLayer(ISO_LINE_LAYER)) map.setLayoutProperty(ISO_LINE_LAYER, 'visibility', vis)
  }, [isochroneVisible])

  /* ── Address pins (plotar_endereco + endpoints de route_data) ── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    function clearMarkers() {
      addressPinMarkersRef.current.forEach((entry) => entry.marker.remove())
      addressPinMarkersRef.current = []
    }

    function renderPins() {
      const m = mapRef.current
      if (!m?.isStyleLoaded()) return
      clearMarkers()

      if (addressPins.length === 0) return

      const colors = ADDRESS_PIN_COLORS[baseStyle] ?? ADDRESS_PIN_COLORS.map
      const showOnMap = useMapStore.getState().layerVisibility['address-pins']

      for (const pin of addressPins) {
        const el = makeAddressPinEl(colors.fill, colors.ring, pin.label)

        const popup = new maplibregl.Popup({
          closeButton: true,
          closeOnClick: false,
          maxWidth: '260px',
          offset: 12,
        }).setHTML(buildAddressPinPopup(pin))

        const marker = new maplibregl.Marker({
          element: el,
          anchor: 'top-left',
          offset: [-16, -40],
          pitchAlignment: 'viewport',
          rotationAlignment: 'viewport',
        })
          .setLngLat([pin.lon, pin.lat])
          .setPopup(popup)

        if (showOnMap) marker.addTo(m)

        addressPinMarkersRef.current.push({
          id: pin.id,
          marker,
          lat: pin.lat,
          lon: pin.lon,
        })
      }

      const last = addressPins[addressPins.length - 1]
      m.flyTo({ center: [last.lon, last.lat], zoom: Math.max(m.getZoom(), 14) })
    }

    function schedulePins() {
      if (map.isStyleLoaded()) renderPins()
      else map.once('style.load', renderPins)
    }

    schedulePins()
    map.on('style.load', renderPins)

    return () => {
      map.off('style.load', renderPins)
      clearMarkers()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addressPins, baseStyle])

  /* ── Layer visibility: address-pins ──────────────────────────── */
  useEffect(() => {
    addressPinMarkersRef.current.forEach(({ marker }) => {
      marker.getElement().style.display = addressPinsVisible ? '' : 'none'
    })
  }, [addressPinsVisible])

  /* ── Empresa markers (grouped by coordinate) ────────────────── */
  const hasEstudo = !!useChatStore((s) => s.analiseId)

  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    // Build ID → topic lookup for fornecedorDataRef
    const idToTopic = new Map<string, string>()
    for (const [label, data] of Object.entries(markersByTopic)) {
      for (const e of data.empresa) { const n = normalizeId(e.cnpj_completo); if (n) idToTopic.set(n, label) }
      for (const j of data.jazida) { const n = normalizeId(j.id); if (n) idToTopic.set(n, label) }
    }

    const empresaMarkers: MapaPontoEmpresa[] =
      activeTopicFilter && markersByTopic[activeTopicFilter]
        ? markersByTopic[activeTopicFilter].empresa
        : Object.values(markersByTopic).flatMap((t) => t.empresa)

    function render(m: maplibregl.Map, raw: MapaPontoEmpresa[]) {
      empresaMarkersRef.current.forEach((e) => e.marker.remove())
      empresaMarkersRef.current = []

      if (raw.length === 0) return

      const groups = groupByCoord(raw)
      const bounds = new LngLatBounds()
      // Read visibility synchronously so newly created markers match current state
      const visibleNow = useMapStore.getState().layerVisibility['empresa-points']

      for (const g of groups) {
        let el: HTMLDivElement
        let popup: maplibregl.Popup
        const entryId = normalizeId(g.items[0].cnpj_completo) || g.key

        // Register fornecedor data for each item in the group
        for (const p of g.items) {
          const nid = normalizeId(p.cnpj_completo)
          if (nid) {
            fornecedorDataRef.current.set(nid, {
              id: nid,
              tipo_fonte: 'cnpj',
              nome: p.nome_fantasia || p.razao_social || p.label,
              favorito: true,
              cnpj: p.cnpj_completo || null,
              municipio: p.municipio || null,
              uf: p.uf || null,
              endereco: p.endereco || null,
              cnae_descricao: p.cnae_descricao || null,
              porte: p.porte || null,
              situacao_cadastral: p.situacao || null,
              contato_telefone: p.telefone || null,
              contato_email: p.email || null,
              distancia_km: p.distancia_km ?? null,
              localizacao: { lat: p.lat, lon: p.lon },
              topico: idToTopic.get(nid) ?? null,
            })
          }
        }

        // Pin tip offset depends on element size: 22x28 for single, 38x38 for cluster.
        let pinOffset: [number, number]
        if (g.items.length === 1) {
          el = document.createElement('div')
          el.innerHTML = makeMarkerSvg('#f59e0b')
          el.style.cssText = 'cursor:pointer;width:22px;height:28px;display:block;line-height:0'
          el.title = g.items[0].label
          popup = new maplibregl.Popup({ offset: 24, closeButton: true, closeOnClick: false, maxWidth: '280px' })
            .setHTML(makeEmpresaPopupHtml(g.items[0], hasEstudo))
          pinOffset = [-11, -28]
        } else {
          el = makeClusterEl(g.items.length, '#f59e0b')
          el.title = `${g.items.length} empresas`
          popup = new maplibregl.Popup({ offset: 24, closeButton: true, closeOnClick: false, maxWidth: '320px' })
            .setHTML(makeEmpresaGroupPopupHtml(g.items, hasEstudo))
          pinOffset = [-19, -38]
        }

        popup.on('open',  () => selectFeature(entryId))
        popup.on('close', () => { if (!suppressPopupClose.current) selectFeature(null) })

        const marker = new maplibregl.Marker({
          element: el,
          anchor: 'top-left',
          offset: pinOffset,
          pitchAlignment: 'viewport',
          rotationAlignment: 'viewport',
        })
          .setLngLat([g.lon, g.lat])
          .setPopup(popup)
          .addTo(m)

        el.addEventListener('click', (e) => {
          e.stopPropagation() // prevent _onMapClick (map-level) from double-toggling
          selectFeature(entryId)
          marker.togglePopup()
        })

        if (!visibleNow) el.style.display = 'none'

        bounds.extend([g.lon, g.lat])
        empresaMarkersRef.current.push({ id: entryId, marker, lat: g.lat, lon: g.lon })
      }

      if (!bounds.isEmpty()) {
        m.fitBounds(bounds, { padding: { top: 50, bottom: 50, left: 380, right: 50 }, maxZoom: 14 })
      }
    }

    try {
      render(map, empresaMarkers)
    } catch (err) {
      console.error('[map-render] empresa render error:', err)
    }

    return () => {
      empresaMarkersRef.current.forEach((e) => e.marker.remove())
      empresaMarkersRef.current = []
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [markersByTopic, activeTopicFilter, hasEstudo])

  /* ── Jazida markers (grouped by coordinate) ──────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    // Build ID → topic lookup for fornecedorDataRef
    const idToTopic = new Map<string, string>()
    for (const [label, data] of Object.entries(markersByTopic)) {
      for (const e of data.empresa) { const n = normalizeId(e.cnpj_completo); if (n) idToTopic.set(n, label) }
      for (const j of data.jazida) {
        const n = j.tipo === 'geoquimica' ? normalizeAmostraId(j.id) : normalizeId(j.id)
        if (n) idToTopic.set(n, label)
      }
    }

    const jazidaMarkers: MapaPonto[] =
      activeTopicFilter && markersByTopic[activeTopicFilter]
        ? markersByTopic[activeTopicFilter].jazida
        : Object.values(markersByTopic).flatMap((t) => t.jazida)

    function render(m: maplibregl.Map, raw: MapaPonto[]) {
      jazidaMarkersRef.current.forEach((e) => e.marker.remove())
      jazidaMarkersRef.current = []

      if (raw.length === 0) return

      const groups = groupByCoord(raw)
      const bounds = new LngLatBounds()
      // Read visibility synchronously so newly created markers match current state
      const visibleNow = useMapStore.getState().layerVisibility['jazida-points']

      for (const g of groups) {
        let el: HTMLDivElement
        let popup: maplibregl.Popup
        const head = g.items[0]
        const isGeoGroup = head.tipo === 'geoquimica'
        const isCarGroup = head.tipo === 'car'
        const entryId = isGeoGroup
          ? normalizeAmostraId(head.id)
          : isCarGroup
            ? (head.id || g.key)
            : (normalizeId(head.id) || g.key)

        // Register fornecedor data for each item in the group
        for (const p of g.items) {
          const nid = p.tipo === 'geoquimica'
            ? normalizeAmostraId(p.id)
            : p.tipo === 'car'
              ? p.id
              : normalizeId(p.id)
          if (nid) {
            fornecedorDataRef.current.set(nid, {
              id: nid,
              tipo_fonte: 'anm',
              nome: p.label,
              favorito: true,
              processo_anm: p.id || null,
              substancia: p.substancias?.join(', ') || null,
              fase: p.fase || null,
              municipio: p.municipios?.[0] || null,
              uf: p.uf?.[0] || null,
              endereco: p.endereco || null,
              contato_telefone: p.telefone || null,
              contato_email: p.email || null,
              distancia_km: p.distancia_km ?? null,
              localizacao: { lat: p.lat, lon: p.lon },
              topico: idToTopic.get(nid) ?? null,
            })
          }
        }

        const groupPinColor = isGeoGroup ? '#f59e0b' : isCarGroup ? '#a16207' : '#10b981'

        let pinOffset: [number, number]
        if (g.items.length === 1) {
          el = document.createElement('div')
          el.innerHTML = makeMarkerSvg(groupPinColor)
          el.style.cssText = 'cursor:pointer;width:22px;height:28px;display:block;line-height:0'
          el.title = g.items[0].label
          popup = new maplibregl.Popup({ offset: 24, closeButton: true, closeOnClick: false, maxWidth: '260px' })
            .setHTML(makeJazidaPopupHtml(g.items[0], hasEstudo))
          pinOffset = [-11, -28]
        } else {
          el = makeClusterEl(g.items.length, groupPinColor)
          el.title = `${g.items.length} jazidas`
          popup = new maplibregl.Popup({ offset: 24, closeButton: true, closeOnClick: false, maxWidth: '320px' })
            .setHTML(`<div style="font-size:12px;max-height:300px;overflow-y:auto">
              <div style="font-weight:600;margin-bottom:4px">${g.items.length} jazidas neste local</div>
              ${g.items.map(p => {
                const nid = p.tipo === 'geoquimica' ? normalizeAmostraId(p.id) : normalizeId(p.id)
                const analitos = p.analitos?.join(', ') ?? p.substancias?.join(', ')
                return `<div style="padding:4px 0;border-bottom:1px solid #e2e8f0;position:relative">
                ${hasEstudo && nid && p.tipo !== 'geoquimica' ? makeStarSvg(nid) : ''}
                <div style="padding-right:${hasEstudo ? '22px' : '0'}">
                  <strong>${p.id || p.label}</strong>
                  ${analitos ? `<div style="color:#059669;font-size:11px">${analitos}</div>` : ''}
                </div>
                ${p.tipo === 'geoquimica' ? '' : makePolygonToggleBtn(p.id, p.polygonFetch ?? 'anm')}
              </div>`
              }).join('')}
            </div>`)
          pinOffset = [-19, -38]
        }

        popup.on('open',  () => selectFeature(entryId))
        popup.on('close', () => { if (!suppressPopupClose.current) selectFeature(null) })

        const marker = new maplibregl.Marker({
          element: el,
          anchor: 'top-left',
          offset: pinOffset,
          pitchAlignment: 'viewport',
          rotationAlignment: 'viewport',
        })
          .setLngLat([g.lon, g.lat])
          .setPopup(popup)
          .addTo(m)

        el.addEventListener('click', (e) => {
          e.stopPropagation() // prevent _onMapClick (map-level) from double-toggling
          selectFeature(entryId)
          marker.togglePopup()
        })

        if (!visibleNow) el.style.display = 'none'

        bounds.extend([g.lon, g.lat])
        jazidaMarkersRef.current.push({ id: entryId, marker, lat: g.lat, lon: g.lon })
      }

      if (!bounds.isEmpty()) {
        m.fitBounds(bounds, { padding: { top: 50, bottom: 50, left: 380, right: 50 }, maxZoom: 14 })
      }
    }

    try {
      render(map, jazidaMarkers)
    } catch (err) {
      console.error('[map-render] jazida render error:', err)
    }

    return () => {
      jazidaMarkersRef.current.forEach((e) => e.marker.remove())
      jazidaMarkersRef.current = []
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [markersByTopic, activeTopicFilter, hasEstudo])

  /* ── Manage popup visibility when selectedFeatureId changes ──────────
   * - Close every open popup that is NOT the selected one
   * - Open the selected one if not already open
   * suppressRef prevents the programmatic togglePopup() calls from
   * cascading back into selectFeature() via the popup 'close' event.
   */
  const suppressPopupClose = useRef(false)

  useEffect(() => {
    const allEntries = [
      ...empresaMarkersRef.current,
      ...jazidaMarkersRef.current,
    ]

    suppressPopupClose.current = true
    allEntries.forEach((e) => {
      if (e.id !== selectedFeatureId) {
        const p = e.marker.getPopup()
        if (p?.isOpen()) e.marker.togglePopup()
      }
    })
    suppressPopupClose.current = false

    if (!selectedFeatureId) return

    const entry = allEntries.find((e) => e.id === selectedFeatureId)
    if (!entry) return

    flyTo(entry.lon, entry.lat, 15)

    const popup = entry.marker.getPopup()
    if (popup && !popup.isOpen()) {
      entry.marker.togglePopup()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFeatureId])

  /* ── Event delegation for popup star clicks ──────────────────── */
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    function onClick(e: MouseEvent) {
      const btn = (e.target as HTMLElement).closest('.fav-star-btn') as HTMLButtonElement | null
      if (!btn) return
      e.stopPropagation()

      const favId = btn.dataset.favId
      if (!favId) return

      const analiseId = useChatStore.getState().analiseId
      if (!analiseId) return

      const store = useFavoritesStore.getState()
      const svg = btn.querySelector('svg')

      if (favId in store.saved) {
        const tipoFonte = store.saved[favId]
        store.markRemoved(favId)
        removeMutRef.current.mutate({ analiseId, fornecedorId: favId, tipoFonte })
        if (svg) { svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', '#94a3b8') }
        btn.title = 'Salvar fornecedor'
      } else {
        const data = fornecedorDataRef.current.get(favId)
        if (!data) return
        store.markSaved(favId, data.tipo_fonte)
        addMutRef.current.mutate({ analiseId, data })
        if (svg) { svg.setAttribute('fill', '#f59e0b'); svg.setAttribute('stroke', '#f59e0b') }
        btn.title = 'Remover dos favoritos'
      }
    }

    el.addEventListener('click', onClick, true)
    return () => el.removeEventListener('click', onClick, true)
  }, [])

  /* ── Event delegation for popup route-calc clicks ─────────────── */
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    function onClick(e: MouseEvent) {
      const btn = (e.target as HTMLElement).closest('.route-calc-btn') as HTMLButtonElement | null
      if (!btn) return
      e.stopPropagation()
      e.preventDefault()

      const rawLabel = btn.dataset.routeLabel
      const lat = parseFloat(btn.dataset.routeLat ?? '')
      const lon = parseFloat(btn.dataset.routeLon ?? '')
      if (!rawLabel || !Number.isFinite(lat) || !Number.isFinite(lon)) return

      const label = decodeURIComponent(rawLabel)
      // Build an unambiguous prompt that gives the LLM the destination
      // coordinates directly, so it doesn't waste a tool call on geocoding.
      // The active project pin is the implicit origin (system prompt rule).
      const text =
        `Calcule a rota a partir do pino do projeto ativo até ${label}, ` +
        `coordenadas de destino: ${lat.toFixed(6)}, ${lon.toFixed(6)}, modo caminhão.`

      window.dispatchEvent(new CustomEvent('chat:send', { detail: { text } }))
    }

    el.addEventListener('click', onClick, true)
    return () => el.removeEventListener('click', onClick, true)
  }, [])

  /* ── Event delegation for popup polygon toggle clicks (on-demand fetch) ── */
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    function onClick(e: MouseEvent) {
      const btn = (e.target as HTMLElement).closest('.polygon-toggle-btn') as HTMLButtonElement | null
      if (!btn) return
      e.stopPropagation()
      e.preventDefault()

      const processoId = btn.dataset.polygonId
      if (!processoId) return

      const store = useMapStore.getState()
      const wasActive = store.activeJazidaPolygonIds.has(processoId)

      // Toggle the active set immediately for instant visual feedback
      store.toggleJazidaPolygon(processoId)
      const nowActive = !wasActive

      btn.textContent = nowActive ? 'Ocultar area' : 'Ver area'
      btn.style.background = nowActive ? '#059669' : '#16a34a'

      if (!nowActive) return  // turning off — nothing to fetch

      // Mostrar polígono mesmo se o utilizador tiver desligado a camada no painel
      store.ensureJazidaPolygonsVisible()

      // Só saltamos o fetch se já houver geometria em cache (arrays vazios não contam)
      if (useMapStore.getState().jazidaPolygonCache[processoId]?.length) return

      // On-demand fetch: get polygon from backend (Redis L1 → OpenSearch L2)
      btn.textContent = 'Carregando…'
      btn.style.background = '#64748b'
      store.setJazidaPolygonLoading(processoId, true)

      const fetchKind = (btn.dataset.polygonFetch as 'anm' | 'cprm' | 'car' | 'none' | undefined) ?? 'anm'
      if (fetchKind === 'none') {
        useMapStore.getState().setJazidaPolygonLoading(processoId, false)
        useMapStore.getState().toggleJazidaPolygon(processoId)
        btn.textContent = 'Ver area'
        btn.style.background = '#16a34a'
        return
      }
      // For ANM, reconstruct original processo string: "8003351992" → "800.335/1992"
      // For CAR, rawProcesso IS the cod_car (stored verbatim in data-raw-processo)
      const rawId = btn.dataset.rawProcesso ?? processoId
      const fetchPromise =
        fetchKind === 'cprm' ? fetchCprmPoligono(rawId)
        : fetchKind === 'car' ? fetchCarPoligono(rawId)
        : fetchJazidaPoligono(rawId)

      fetchPromise
        .then((fc) => {
          useMapStore.getState().setJazidaPolygonLoading(processoId, false)
          const feats = fc.features ?? []
          if (feats.length === 0) {
            useMapStore.getState().toggleJazidaPolygon(processoId)
            btn.textContent = 'Sem geometria'
            btn.style.background = '#94a3b8'
            btn.disabled = true
            return
          }
          useMapStore.getState().setJazidaPolygonData(processoId, feats)
          btn.textContent = 'Ocultar area'
          btn.style.background = '#059669'
        })
        .catch(() => {
          // Polygon not available — turn off the toggle
          useMapStore.getState().toggleJazidaPolygon(processoId)
          useMapStore.getState().setJazidaPolygonLoading(processoId, false)
          btn.textContent = 'Sem geometria'
          btn.style.background = '#94a3b8'
          btn.disabled = true
        })
    }

    el.addEventListener('click', onClick, true)
    return () => el.removeEventListener('click', onClick, true)
  }, [])

  /* ── Sync popup star visuals when favorites change ─────────── */
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const stars = el.querySelectorAll<HTMLButtonElement>('.fav-star-btn')
    stars.forEach((btn) => {
      const id = btn.dataset.favId
      if (!id) return
      const svg = btn.querySelector('svg')
      if (!svg) return
      const isSaved = id in savedFavorites
      svg.setAttribute('fill', isSaved ? '#f59e0b' : 'none')
      svg.setAttribute('stroke', isSaved ? '#f59e0b' : '#94a3b8')
      btn.title = isSaved ? 'Remover dos favoritos' : 'Salvar fornecedor'
    })
  }, [savedFavorites])

  /* ── CPRM Mapa Geológico 1:1M (WMS raster) ──────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    function removeCprm(m: maplibregl.Map) {
      if (m.getLayer(CPRM_LAYER)) m.removeLayer(CPRM_LAYER)
      if (m.getSource(CPRM_SOURCE)) m.removeSource(CPRM_SOURCE)
    }

    function syncCprm(m: maplibregl.Map) {
      if (!m.isStyleLoaded()) return

      const enabled = useMapStore.getState().layerVisibility['cprm-geologico']
      removeCprm(m)
      if (!enabled) return

      m.addSource(CPRM_SOURCE, {
        type: 'raster',
        tiles: [
          `${CPRM_WMS_BASE}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap` +
          `&LAYERS=geosgb:litoestratigrafia_1m` +
          `&CRS=EPSG:3857&BBOX={bbox-epsg-3857}` +
          `&WIDTH=256&HEIGHT=256&FORMAT=image/png&TRANSPARENT=TRUE&STYLES=`,
        ],
        tileSize: 256,
        attribution: '© CPRM/SGB — Mapa Geológico 1:1.000.000',
      })

      // Acima do satélite/OSM; abaixo de polígonos de jazida (se existirem)
      const beforeId = m.getLayer(JAZIDA_POLY_FILL) ? JAZIDA_POLY_FILL : undefined
      m.addLayer(
        {
          id: CPRM_LAYER,
          type: 'raster',
          source: CPRM_SOURCE,
          layout: { visibility: 'visible' },
          paint: { 'raster-opacity': 0.72 },
        },
        beforeId,
      )
    }

    function onStyleLoad() {
      syncCprm(map)
    }

    map.on('style.load', onStyleLoad)
    if (map.isStyleLoaded()) syncCprm(map)

    return () => {
      map.off('style.load', onStyleLoad)
      if (map.isStyleLoaded()) removeCprm(map)
    }
  }, [baseStyle, cprmVisible])

  /* ── CPRM GetFeatureInfo on click ───────────────────────────── */
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: true, maxWidth: '280px' })

    async function onClick(e: maplibregl.MapMouseEvent) {
      if (!useMapStore.getState().layerVisibility['cprm-geologico']) return
      if (!map) return

      const canvas = map.getCanvas()
      const w = canvas.width  / window.devicePixelRatio
      const h = canvas.height / window.devicePixelRatio

      const bounds = map.getBounds()
      const minX = bounds.getWest()
      const minY = bounds.getSouth()
      const maxX = bounds.getEast()
      const maxY = bounds.getNorth()

      // WMS 1.3.0 CRS:84 axis order is lng,lat (same as EPSG:4326 but lon-first)
      const bbox = `${minX},${minY},${maxX},${maxY}`
      const i = Math.round(e.point.x)
      const j = Math.round(e.point.y)

      const url =
        `${CPRM_WMS_BASE}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo` +
        `&LAYERS=geosgb:litoestratigrafia_1m` +
        `&QUERY_LAYERS=geosgb:litoestratigrafia_1m` +
        `&INFO_FORMAT=application/json` +
        `&FEATURE_COUNT=1` +
        `&CRS=CRS:84&BBOX=${bbox}` +
        `&WIDTH=${Math.round(w)}&HEIGHT=${Math.round(h)}` +
        `&I=${i}&J=${j}`

      try {
        const res = await fetch(url)
        if (!res.ok) return
        const data = await res.json()
        const feat = data?.features?.[0]
        if (!feat) return

        const p = feat.properties ?? {}

        const unidade  = p.sigla_unidade ?? p.unidade ?? p.nome ?? p.sigla ?? ''
        const descricao = p.descricao ?? p.litodescricao ?? p.litologia ?? ''
        const era       = p.era ?? p.periodo ?? p.era_geologica ?? ''
        const tipo      = p.tipo_litodescricao ?? p.tipo ?? ''

        const rows: string[] = []
        if (unidade)  rows.push(`<strong style="font-size:13px">${unidade}</strong>`)
        if (era)      rows.push(`⏳ ${era}`)
        if (tipo)     rows.push(`🪨 ${tipo}`)
        if (descricao) rows.push(`<span style="color:#475569;font-size:11px">${descricao.slice(0, 200)}${descricao.length > 200 ? '…' : ''}</span>`)

        // Fallback: show all non-null props if we couldn't extract known fields
        if (rows.length === 0) {
          for (const [k, v] of Object.entries(p)) {
            if (v != null && String(v).trim()) {
              rows.push(`<span style="color:#475569"><strong>${k}:</strong> ${v}</span>`)
            }
          }
        }

        if (rows.length === 0) return

        popup
          .setLngLat(e.lngLat)
          .setHTML(
            `<div style="font-size:12px;line-height:1.5">
              <div style="font-size:10px;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.05em">
                Mapa Geológico CPRM 1:1M
              </div>
              ${rows.join('<br>')}
            </div>`,
          )
          .addTo(map!)
      } catch {
        // network or JSON error — silently ignore
      }
    }

    function onEnter() {
      if (useMapStore.getState().layerVisibility['cprm-geologico']) {
        map!.getCanvas().style.cursor = 'crosshair'
      }
    }
    function onLeave() { map!.getCanvas().style.cursor = '' }

    map.on('click', CPRM_LAYER, onClick)
    map.on('mouseenter', CPRM_LAYER, onEnter)
    map.on('mouseleave', CPRM_LAYER, onLeave)

    return () => {
      popup.remove()
      map.off('click', CPRM_LAYER, onClick)
      map.off('mouseenter', CPRM_LAYER, onEnter)
      map.off('mouseleave', CPRM_LAYER, onLeave)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const isFullHeight = height === '100%'

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height: typeof height === 'number' ? `${height}px` : height }}
      className={isFullHeight ? '' : 'rounded-lg'}
    />
  )
}
