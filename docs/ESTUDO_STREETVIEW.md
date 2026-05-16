# Estudo — Street View no MineralRadar
**Data:** Abril 2026 | **Status:** Proposta para discussão

---

## 1. Contexto e Objetivo

O MineralRadar atualmente exibe jazidas minerais e empresas fornecedoras em um mapa interativo (MapLibre GL JS) com camada padrão (CARTO Voyager) e satélite (ESRI World Imagery). O usuário consegue ver **onde** um ativo está no mapa, mas não consegue visualizar **como é o entorno físico** daquele local — essencial para decisões de campo como:

- Verificar condições de acesso viário a uma jazida
- Avaliar a estrutura física aparente de um fornecedor antes de uma visita
- Conferir se o endereço geocodificado corresponde visualmente ao local esperado
- Identificar o tipo de uso do solo ao redor de uma obra

**Objetivo:** Avaliar as opções de Street View embutida no sistema (sem abrir nova aba), com análise de custo, usabilidade e implementação.

---

## 2. Opções Disponíveis

### 2.1 Google Street View

| Item | Detalhe |
|------|---------|
| Cobertura BR | Excelente — cidades médias e grandes, rodovias, algumas zonas rurais |
| Qualidade | Alta definição, 360°, atualização periódica |
| API para embed | Maps JavaScript API (`google.maps.StreetViewPanorama`) |
| Alternativa mais simples | Static Street View API (imagem estática PNG) |
| Custo Static | **US$ 7,00 / 1.000 requisições** (0,007/req) |
| Custo Dynamic (JS) | Incluído na cota gratuita de US$ 200/mês da Google |
| Gratuidade mensal | 28.571 imagens estáticas/mês ou uso JS dentro de US$ 200 |

**Como embute no sistema:** Dentro de um painel lateral ou drawer no `WorkspacePage`, usando a API JavaScript (sem iframe), passando `lat/lon` do ponto selecionado no mapa.

**Limitações:**
- Requer chave Google Maps (custo separado do Azure Maps já usado)
- Imagens rurais muitas vezes inexistentes
- Termos de uso proíbem extração/armazenamento de imagens

---

### 2.2 Mapillary (Meta / OpenStreetMap)

| Item | Detalhe |
|------|---------|
| Cobertura BR | Boa em cidades grandes e médias; pior que Google em zonas rurais |
| Qualidade | Variável (fotos colaborativas), geralmente boa |
| API para embed | `mapillary-js` SDK — JavaScript nativo, sem iframe |
| Integração MapLibre | Plugin oficial `maplibre-gl-mapillary` disponível |
| Custo | **Gratuito** para uso não-comercial; comercial tem tier |
| Gratuidade | 500 mil requisições/mês no plano gratuito |
| Licença | CC BY-SA (imagens abertas) |

**Como embute no sistema:** O `mapillary-js` abre um viewer 360° dentro de qualquer `<div>`, totalmente controlável via JavaScript. Pode sincronizar com MapLibre (mover câmera = mover mapa e vice-versa).

**Limitações:**
- Cobertura inferior ao Google em regiões periféricas
- Qualidade inconsistente (dependente de contribuidores)
- Uso comercial requer avaliação do contrato com Meta

---

### 2.3 Link Externo — Google Maps (nova aba)

| Item | Detalhe |
|------|---------|
| Cobertura BR | Excelente (igual ao Google Street View) |
| Custo de API | **R$ 0** — nenhuma chave necessária |
| Implementação | `<a href="...">` ou `window.open(...)` — 10 minutos |
| Dependência nova | Nenhuma |
| Experiência | Abandona o sistema — abre o Google Maps no browser |

**Como funciona:** Gerar a URL do Google Maps Street View a partir do `lat/lon` do pin:

```
https://www.google.com/maps?q=&layer=c&cbll={lat},{lon}
```

Exemplo para uma empresa em São Paulo (lat -23.55, lon -46.63):
```
https://www.google.com/maps?q=&layer=c&cbll=-23.55,-46.63
```

**Prós:**
- ✅ Custo zero — nenhuma API, nenhuma chave
- ✅ Implementação em menos de 1 hora
- ✅ Cobertura idêntica ao Google Street View completo
- ✅ Zero dependência nova no projeto
- ✅ Manutenção zero — nunca vai quebrar
- ✅ Experiência familiar — o usuário já conhece o Google Maps

**Contras:**
- ❌ Abandona o sistema — o usuário sai do MineralRadar
- ❌ Sem sincronização com o mapa interno (câmera, marcadores)
- ❌ Experiência fragmentada — duas abas abertas ao mesmo tempo
- ❌ No mobile, pode abrir o app Google Maps em vez do browser
- ❌ Sem controle sobre o que o usuário faz após abrir (pode não voltar)
- ❌ Não é possível customizar interface, adicionar contexto ou integrar dados do sistema

---

### 2.4 Azure Maps (já utilizado no projeto)

| Item | Detalhe |
|------|---------|
| Street View equivalente | **Não existe** — Azure Maps não oferece street-level imagery |
| Alternativa | Satélite de alta resolução (já implementado via ESRI) |

❌ **Descartado** para este caso de uso.

---

### 2.4 Bing Maps Streetside (Microsoft)

| Item | Detalhe |
|------|---------|
| Cobertura BR | Limitada — concentrada em grandes metrópoles |
| API | Disponível via Bing Maps REST e Maps SDK |
| Custo | Gratuito até 125.000 transações/ano (≈ 342/dia) |
| Integração | Requer Bing Maps Key separada |

**Limitações:**
- Cobertura muito inferior ao Google no Brasil
- Menos atualizações recentes
- SDK menos moderno

❌ **Não recomendado** para uso prioritário no contexto brasileiro.

---

## 3. Comparativo de Opções

| Critério | Link Google Maps (nova aba) | Google Street View (embed) | Mapillary (embed) | Bing Streetside |
|----------|-----------------------------|---------------------------|-------------------|-----------------|
| Cobertura BR | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| Qualidade de imagem | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| Custo | **R$ 0** | Pago (US$) | Gratuito | Gratuito (limitado) |
| Esforço de implementação | **~1h** | 2–3 dias | 3–5 dias | 3–4 dias |
| Fica dentro do sistema | ❌ | ✅ | ✅ | ✅ |
| Sincronização com mapa | ❌ | Manual | Nativa (plugin) | Manual |
| Requer chave API | ❌ | ✅ (Google) | ✅ (Mapillary) | ✅ (Bing) |
| Manutenção futura | Nenhuma | Baixa | Baixa | Baixa |
| Experiência do usuário | Fragmentada | Integrada | Integrada | Integrada |

---

## 4. Recomendação

### Opção 0 — Link externo (nova aba) — Recomendação do caminho mais simples

Se o objetivo for **entregar rapidamente com custo zero**, o link externo é uma opção válida para uma primeira versão. Basta adicionar um botão "📷 Ver no Google Maps" no popup do pin que abre:

```
https://www.google.com/maps?q=&layer=c&cbll={lat},{lon}
```

**Quando faz sentido:** MVP, validação rápida, equipes pequenas, orçamento zero.
**Quando não faz sentido:** produto consolidado onde retenção e experiência integrada importam.

---

### Estratégia Recomendada: Mapillary Embed → Google Embed

Para quem quer a experiência integrada (sem nova aba), a abordagem recomendada é **Mapillary como primário** (gratuito) com evolução para **Google Street View** conforme a necessidade de cobertura aumenta.

```
Usuário clica no pin → sistema tenta Google Street View
  ├── Imagem disponível → abre painel Street View Google
  └── Sem imagem Google → tenta Mapillary
        ├── Disponível → abre painel Mapillary
        └── Sem imagem → exibe mensagem "Imagem de rua não disponível para este local"
```

Para **fase inicial**, pode-se começar apenas com **Mapillary** (gratuito, boa cobertura em cidades) para validar a UX sem custo, e depois adicionar Google conforme necessidade.

---

## 5. Proposta de Usabilidade (UX)

### 5.1 Fluxo de ativação

O Street View não deve ser exibido por padrão — ocupa espaço e a maioria dos acessos ao mapa não precisará dele. A proposta é um **painel lateral deslizante (drawer)** ativado por demanda:

```
[Mapa] → Usuário clica num pin (empresa ou jazida)
       → Popup do pin exibe botão "Ver Street View 📷"
       → Clique abre um painel inferior (split-view vertical)
         ├── Superior: mapa atual (reduzido)
         └── Inferior: viewer 360° sincronizado
       → Botão "Fechar" retorna ao mapa em tela cheia
```

### 5.2 Layout sugerido — Split View Vertical

```
┌─────────────────────────────────────────────────────────┐
│  Chat (360px)  │  Mapa (flex)                           │
│                │  ┌──────────────────────────────────┐  │
│                │  │  MAPA (50% altura)               │  │
│                │  │  Pin selecionado visível          │  │
│                │  ├──────────────────────────────────┤  │
│                │  │  STREET VIEW (50% altura)         │  │
│                │  │  [← ↑ → controles 360°]          │  │
│                │  │  [📍 Lat/Lon | 🔗 Compartilhar]  │  │
│                │  └──────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 5.3 Interações

- **Sincronização mapa↔street view:** Mover a câmera no Street View rotaciona um indicador direcional (seta azul) no marker do mapa, mostrando para onde a câmera está olhando.
- **Clique no mapa em modo Street View:** Atualiza o viewer para as coordenadas clicadas (sem fechar o painel).
- **Controles:** Botão de fullscreen no painel do Street View para ocupar toda a área do mapa quando necessário.
- **Contexto:** Exibir nome da empresa/jazida e endereço no topo do painel Street View.

---

## 6. Arquitetura de Implementação

### 6.1 Componentes a criar

```
frontend/src/components/map/
├── MapContainer.tsx         ← já existe
├── MapLayerControl.tsx      ← já existe
├── MapStyleToggle.tsx       ← já existe
└── StreetViewPanel.tsx      ← NOVO
    └── StreetViewViewer.tsx ← NOVO (sub-componente do viewer)
```

### 6.2 Store — extensão do mapStore

```typescript
// Adicionar ao mapStore.ts
interface StreetViewState {
  isOpen: boolean
  lat: number | null
  lon: number | null
  label: string | null
}

// Actions
openStreetView: (lat: number, lon: number, label: string) => void
closeStreetView: () => void
```

### 6.3 StreetViewPanel.tsx — esboço de implementação com Mapillary

```typescript
import { useEffect, useRef } from 'react'
import { Viewer } from 'mapillary-js'
import 'mapillary-js/dist/mapillary.css'
import { useMapStore } from '@/stores/mapStore'

export function StreetViewPanel() {
  const { streetView, closeStreetView } = useMapStore()
  const viewerRef = useRef<Viewer | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current || !streetView.lat) return

    viewerRef.current = new Viewer({
      accessToken: import.meta.env.VITE_MAPILLARY_TOKEN,
      container: containerRef.current,
    })

    // Navegar para as coordenadas do pin selecionado
    viewerRef.current.moveTo(
      // Busca a imagem mais próxima das coordenadas via API Mapillary
      await fetchNearestMapillaryImage(streetView.lat, streetView.lon)
    )

    return () => viewerRef.current?.remove()
  }, [streetView.lat, streetView.lon])

  if (!streetView.isOpen) return null

  return (
    <div className="relative h-full w-full border-t border-zinc-700">
      <div className="absolute top-2 left-3 z-10 text-xs text-white bg-black/50 px-2 py-1 rounded">
        {streetView.label}
      </div>
      <button
        onClick={closeStreetView}
        className="absolute top-2 right-3 z-10 text-white bg-black/50 px-2 py-1 rounded text-xs"
      >
        ✕ Fechar
      </button>
      <div ref={containerRef} className="h-full w-full" />
    </div>
  )
}
```

### 6.4 Integração no WorkspacePage

```typescript
// Área do mapa passa a ser split-view quando streetView.isOpen
<div className={`flex flex-col ${streetView.isOpen ? 'h-full' : ''}`}>
  <div className={streetView.isOpen ? 'h-1/2' : 'h-full'}>
    <MapContainer />
  </div>
  {streetView.isOpen && (
    <div className="h-1/2">
      <StreetViewPanel />
    </div>
  )}
</div>
```

### 6.5 Botão no popup do pin (MapContainer)

```typescript
// Dentro do popup gerado ao clicar no marker
const popupContent = `
  <div>
    <strong>${label}</strong><br/>
    <button onclick="window.__openStreetView(${lat}, ${lon}, '${label}')"
      class="mt-2 text-xs bg-blue-600 text-white px-2 py-1 rounded">
      📷 Ver Street View
    </button>
  </div>
`
// window.__openStreetView conecta ao Zustand via bridge
```

---

## 7. Análise de Custos

### Cenário A — Mapillary (Gratuito)

| Item | Valor |
|------|-------|
| Custo de API | R$ 0 |
| Cobertura esperada | ~60–70% dos pontos em cidades médias/grandes |
| Cobertura em áreas rurais (jazidas) | ~20–30% |
| Dependência nova | `mapillary-js` (~220KB gzip) |
| Tempo estimado de implementação | 3–5 dias |

### Cenário B — Google Street View Static

| Item | Valor |
|------|-------|
| Custo por imagem carregada | US$ 0,007 (R$ ~0,04) |
| Volume estimado (100 usuários, 10 visualizações/dia) | 1.000 req/dia |
| Custo mensal estimado | US$ 7/mês (R$ ~40/mês) |
| Cobertura esperada | ~90% dos pontos |
| Dependência nova | Chave Google Maps API |
| Tempo estimado de implementação | 2–3 dias |

### Cenário C — Google Street View Dynamic (JS SDK)

| Item | Valor |
|------|-------|
| Cota gratuita mensal Google | US$ 200/mês |
| Sessões inclusas na cota | ≈ 28.000 sessões/mês |
| Custo acima da cota | US$ 14 / 1.000 sessões |
| Dependência nova | Chave Google Maps API + script JS |
| Tempo estimado de implementação | 3–4 dias |

### Recomendação de custo

Para o estágio atual do produto (validação):
> **Fase 1:** Mapillary (R$ 0) — implementar e validar se a equipe usa o recurso.
> **Fase 2:** Migrar para Google Street View Static se a cobertura do Mapillary for insuficiente. Custo mensal inferior a R$ 50 para o volume atual.

---

## 8. Riscos e Mitigações

| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| Cobertura insuficiente em zonas rurais (jazidas) | Alto | Exibir satélite ESRI como fallback em vez de Street View |
| Carregamento lento do viewer 360° | Médio | Lazy load do componente + skeleton loading |
| Chave Google Maps exposta no frontend | Alto | Restringir a chave por domínio no console Google Cloud |
| Custo Google excedendo estimativa | Médio | Implementar cache de imagens estáticas por coordenada no Redis |
| Termos Mapillary para uso comercial | Médio | Validar contrato com Meta antes de lançar em produção |
| Bundle size aumentando (mapillary-js) | Baixo | Code splitting — importar só quando painel for aberto |

---

## 9. Dependências e Configuração

### 9.1 Mapillary

```bash
npm install mapillary-js
```

```env
# .env
VITE_MAPILLARY_TOKEN=seu_token_aqui
```

Token gratuito em: [mapillary.com/developer](https://www.mapillary.com/developer)

### 9.2 Google Street View Static

```env
# .env
VITE_GOOGLE_MAPS_KEY=sua_chave_aqui
```

Chave criada em: [console.cloud.google.com](https://console.cloud.google.com)
APIs necessárias: `Street View Static API` e/ou `Maps JavaScript API`

---

## 10. Decisão Sugerida

| Etapa | Ação | Custo | Esforço |
|-------|------|-------|---------|
| **Imediato (MVP)** | Botão "Ver no Google Maps" → nova aba | R$ 0 | ~1h |
| **Curto prazo** | Implementar painel embed com **Mapillary** (sem sair do sistema) | R$ 0 | 3–5 dias |
| **Médio prazo** | Avaliar cobertura real nos endereços mais acessados (jazidas vs empresas) | — | — |
| **Longo prazo** | Se cobertura Mapillary < 70%, adicionar **Google Street View Static** com cache Redis | R$ 40–50/mês | 2–3 dias |

> **Nota para o time:** A opção de link externo (nova aba) entrega 100% da cobertura do Google Maps em menos de 1 hora sem nenhum custo de API. A contrapartida é a experiência fragmentada — o usuário sai do sistema. A decisão entre "link externo" e "embed" é essencialmente uma decisão de produto, não técnica.

A implementação técnica está mapeada e o projeto já tem toda a infraestrutura necessária (Zustand stores, MapLibre, Redis para cache). Estimativa total: **3–5 dias** para a Fase 1 completa com Mapillary.
