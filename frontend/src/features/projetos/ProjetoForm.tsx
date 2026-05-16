import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Save, Search, MapPin, X, LocateFixed, Loader2 } from 'lucide-react'
import {
  Button,
  Card,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui'
import { useProjeto, useCreateProjeto, useUpdateProjeto, type ProjetoInput } from '@/hooks/useProjetos'
import { useAzureGeocode, type GeocodeResult } from '@/hooks/useAzureGeocode'
import { PROJETO_TIPO_LABEL, PROJETO_STATUS_LABEL } from '@/lib/formatters'
import { cn } from '@/lib/cn'
import type { Projeto } from '@/types/api'

const UF_OPTIONS = [
  'AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG',
  'PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO',
]

const EMPTY_FORM: ProjetoInput = {
  nome: '',
  tipo: 'mineracao',
  status: 'planejamento',
  municipio: '',
  uf: '',
  raio_busca_km: 100,
  endereco: '',
  localizacao: undefined,
}

export function ProjetoForm() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const editId = searchParams.get('edit')
  const isEdit = !!editId

  const { data: existing, isLoading: loadingProjeto } = useProjeto(editId ?? undefined)
  const createMutation = useCreateProjeto()
  const updateMutation = useUpdateProjeto()
  const { searchAddress, reverseGeocode, loading: geocodeLoading } = useAzureGeocode()

  const initialForm = useMemo<ProjetoInput>(() => {
    if (!existing) return EMPTY_FORM
    return {
      nome: existing.nome,
      tipo: existing.tipo,
      status: existing.status,
      municipio: existing.municipio ?? '',
      uf: existing.uf ?? '',
      raio_busca_km: existing.raio_busca_km,
      endereco: existing.endereco ?? '',
      localizacao: existing.localizacao,
    }
  }, [existing])

  const [form, setForm] = useState<ProjetoInput>(EMPTY_FORM)
  const [errors, setErrors] = useState<Partial<Record<keyof ProjetoInput, string>>>({})

  const [addressQuery, setAddressQuery] = useState('')
  const [suggestions, setSuggestions] = useState<GeocodeResult[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [latStr, setLatStr] = useState('')
  const [lonStr, setLonStr] = useState('')
  const [coordMode, setCoordMode] = useState(false)
  const suggestionsRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setForm(initialForm)
    if (existing?.endereco) setAddressQuery(existing.endereco)
    if (existing?.localizacao) {
      setLatStr(String(existing.localizacao.lat))
      setLonStr(String(existing.localizacao.lon))
    }
  }, [initialForm, existing])

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (suggestionsRef.current && !suggestionsRef.current.contains(e.target as Node)) {
        setShowSuggestions(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  function setField<K extends keyof ProjetoInput>(key: K, value: ProjetoInput[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
    if (errors[key]) setErrors((prev) => ({ ...prev, [key]: undefined }))
  }

  function applyGeoResult(result: GeocodeResult) {
    setForm((prev) => ({
      ...prev,
      endereco: result.label,
      localizacao: { lat: result.lat, lon: result.lon },
      municipio: result.municipio ?? prev.municipio,
      uf: result.uf ?? prev.uf,
    }))
    setAddressQuery(result.label)
    setLatStr(result.lat.toFixed(6))
    setLonStr(result.lon.toFixed(6))
    setSuggestions([])
    setShowSuggestions(false)
  }

  function clearLocation() {
    setForm((prev) => ({ ...prev, endereco: '', localizacao: undefined }))
    setAddressQuery('')
    setLatStr('')
    setLonStr('')
  }

  async function handleAddressSearch() {
    if (!addressQuery.trim()) return
    const results = await searchAddress(addressQuery)
    if (results.length === 1) {
      applyGeoResult(results[0])
    } else if (results.length > 1) {
      setSuggestions(results)
      setShowSuggestions(true)
    }
  }

  async function handleCoordSearch() {
    const lat = parseFloat(latStr)
    const lon = parseFloat(lonStr)
    if (isNaN(lat) || isNaN(lon)) {
      setErrors((prev) => ({ ...prev, localizacao: 'Coordenadas inválidas' }))
      return
    }
    const result = await reverseGeocode(lat, lon)
    if (result) {
      applyGeoResult(result)
    } else {
      setForm((prev) => ({
        ...prev,
        localizacao: { lat, lon },
        endereco: `${lat}, ${lon}`,
      }))
      setAddressQuery(`${lat}, ${lon}`)
    }
  }

  function validate(): boolean {
    const e: typeof errors = {}
    if (!form.nome.trim()) e.nome = 'Nome é obrigatório'
    if (!form.tipo) e.tipo = 'Tipo é obrigatório'
    if (!form.status) e.status = 'Status é obrigatório'
    if (!form.municipio?.trim()) e.municipio = 'Município é obrigatório'
    if (!form.uf) e.uf = 'UF é obrigatória'
    if (form.raio_busca_km < 1 || form.raio_busca_km > 500) e.raio_busca_km = 'Raio deve ser entre 1 e 500 km'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  function buildPayload(): Partial<ProjetoInput> {
    return {
      ...form,
      tipo: form.tipo || undefined,
      status: form.status || undefined,
      uf: form.uf || undefined,
      endereco: form.endereco?.trim() || undefined,
      municipio: form.municipio?.trim() || undefined,
      localizacao: form.localizacao ?? undefined,
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!validate()) return

    const payload = buildPayload()

    if (isEdit && editId) {
      updateMutation.mutate(
        { id: editId, data: payload },
        { onSuccess: () => navigate(`/projetos/${editId}`) },
      )
    } else {
      createMutation.mutate(payload as ProjetoInput, {
        onSuccess: (projeto: Projeto) => navigate(`/projetos/${projeto._id}`),
      })
    }
  }

  const isPending = createMutation.isPending || updateMutation.isPending
  const hasLocation = !!form.localizacao
  const mutationError = createMutation.error ?? updateMutation.error

  if (isEdit && loadingProjeto) {
    return (
      <div className="mx-auto max-w-2xl flex items-center justify-center py-20">
        <Loader2 size={24} className="animate-spin text-(--color-text-muted)" />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate(-1)}>
          <ArrowLeft size={16} />
        </Button>
        <h1 className="text-xl font-semibold text-(--color-text)">
          {isEdit ? 'Editar Projeto' : 'Novo Projeto'}
        </h1>
      </div>

      <Card>
        <form onSubmit={handleSubmit} className="space-y-5">
          <Field label="Nome do projeto" error={errors.nome}>
            <Input
              value={form.nome}
              onChange={(e) => setField('nome', e.target.value)}
              placeholder="Ex.: Mineração Serra Azul — Fase 2"
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Tipo" error={errors.tipo}>
              <Select key={`tipo-${form.tipo}`} value={form.tipo || undefined} onValueChange={(v) => setField('tipo', v as ProjetoInput['tipo'])}>
                <SelectTrigger><SelectValue placeholder="Selecione o tipo" /></SelectTrigger>
                <SelectContent>
                  {Object.entries(PROJETO_TIPO_LABEL).map(([value, label]) => (
                    <SelectItem key={value} value={value}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label="Status" error={errors.status}>
              <Select key={`status-${form.status}`} value={form.status || undefined} onValueChange={(v) => setField('status', v as ProjetoInput['status'])}>
                <SelectTrigger><SelectValue placeholder="Selecione o status" /></SelectTrigger>
                <SelectContent>
                  {Object.entries(PROJETO_STATUS_LABEL).map(([value, label]) => (
                    <SelectItem key={value} value={value}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>

          {/* ── Localização ──────────────────────────────────────── */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium text-(--color-text)">
                Localização do projeto
              </label>
              <button
                type="button"
                onClick={() => setCoordMode((v) => !v)}
                className="text-xs text-(--color-text-muted) hover:text-(--color-primary) transition-colors"
              >
                {coordMode ? 'Buscar por endereço' : 'Inserir coordenadas'}
              </button>
            </div>

            {hasLocation ? (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-300 dark:border-emerald-700 bg-emerald-50 dark:bg-emerald-950/30 px-3 py-2">
                <MapPin size={16} className="shrink-0 text-emerald-500" />
                <div className="flex-1 min-w-0">
                  <p className="truncate text-sm font-medium text-(--color-text)">
                    {form.endereco || `${form.localizacao!.lat.toFixed(5)}, ${form.localizacao!.lon.toFixed(5)}`}
                  </p>
                  <p className="text-xs text-(--color-text-muted)">
                    {form.localizacao!.lat.toFixed(6)}, {form.localizacao!.lon.toFixed(6)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={clearLocation}
                  title="Remover localização"
                  className="shrink-0 text-(--color-text-muted) hover:text-red-500 transition-colors"
                >
                  <X size={16} />
                </button>
              </div>
            ) : !coordMode ? (
              <div className="relative" ref={suggestionsRef}>
                <div className="flex gap-2">
                  <Input
                    value={addressQuery}
                    onChange={(e) => setAddressQuery(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddressSearch() }}}
                    placeholder="Ex.: Serra Azul, MG"
                    className="flex-1"
                  />
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={handleAddressSearch}
                    disabled={geocodeLoading || !addressQuery.trim()}
                    className="shrink-0"
                  >
                    {geocodeLoading
                      ? <Loader2 size={16} className="animate-spin" />
                      : <Search size={16} />
                    }
                  </Button>
                </div>

                {showSuggestions && suggestions.length > 0 && (
                  <div className="absolute z-20 mt-1 w-full rounded-lg border border-(--color-border) bg-(--color-surface) shadow-lg">
                    {suggestions.map((s, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => applyGeoResult(s)}
                        className={cn(
                          'flex w-full items-start gap-2 px-3 py-2.5 text-left text-sm transition-colors',
                          'hover:bg-zinc-100 dark:hover:bg-zinc-800',
                          i > 0 && 'border-t border-(--color-border)',
                        )}
                      >
                        <MapPin size={14} className="mt-0.5 shrink-0 text-(--color-text-muted)" />
                        <span className="text-(--color-text)">{s.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="flex gap-2">
                <Input
                  value={latStr}
                  onChange={(e) => setLatStr(e.target.value)}
                  placeholder="Latitude  Ex: -19.8912"
                  className="flex-1"
                />
                <Input
                  value={lonStr}
                  onChange={(e) => setLonStr(e.target.value)}
                  placeholder="Longitude  Ex: -43.8829"
                  className="flex-1"
                />
                <Button
                  type="button"
                  variant="secondary"
                  onClick={handleCoordSearch}
                  disabled={geocodeLoading || !latStr || !lonStr}
                  title="Buscar endereço pelas coordenadas"
                  className="shrink-0"
                >
                  {geocodeLoading
                    ? <Loader2 size={16} className="animate-spin" />
                    : <LocateFixed size={16} />
                  }
                </Button>
              </div>
            )}

            {errors.localizacao && (
              <p className="text-xs text-red-500">{errors.localizacao}</p>
            )}
          </div>

          {/* ── Município / UF / Raio ────────────────────────────── */}
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Município" error={errors.municipio} className="sm:col-span-1">
              <Input
                value={form.municipio}
                onChange={(e) => setField('municipio', e.target.value)}
                placeholder="Ex.: Belo Horizonte"
              />
            </Field>

            <Field label="UF" error={errors.uf}>
              <Select key={`uf-${form.uf}`} value={form.uf || undefined} onValueChange={(v) => setField('uf', v)}>
                <SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger>
                <SelectContent>
                  {UF_OPTIONS.map((uf) => (
                    <SelectItem key={uf} value={uf}>{uf}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label="Raio de busca (km)" error={errors.raio_busca_km}>
              <Input
                type="number"
                min={1}
                max={500}
                value={form.raio_busca_km}
                onChange={(e) => setField('raio_busca_km', Number(e.target.value))}
              />
            </Field>
          </div>

          {mutationError && (
            <p className="rounded-md bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-3 py-2 text-sm text-red-600 dark:text-red-400">
              Erro ao salvar: {(mutationError as Error).message}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => navigate(-1)}>
              Cancelar
            </Button>
            <Button type="submit" disabled={isPending}>
              <Save size={16} className="mr-1.5" />
              {isPending ? 'Salvando...' : isEdit ? 'Salvar alterações' : 'Criar projeto'}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  )
}

function Field({
  label,
  error,
  className,
  children,
}: {
  label: string
  error?: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={className}>
      <label className="mb-1.5 block text-sm font-medium text-(--color-text)">{label}</label>
      {children}
      {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
    </div>
  )
}
