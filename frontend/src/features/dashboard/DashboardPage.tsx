import { Card } from '@/components/ui'

export function DashboardPage() {
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Card>
        <p className="text-sm text-(--color-text-muted)">Obras ativas</p>
        <p className="mt-2 text-2xl font-semibold text-(--color-text)">--</p>
      </Card>
      <Card>
        <p className="text-sm text-(--color-text-muted)">Estudos em andamento</p>
        <p className="mt-2 text-2xl font-semibold text-(--color-text)">--</p>
      </Card>
      <Card>
        <p className="text-sm text-(--color-text-muted)">Fornecedores avaliados</p>
        <p className="mt-2 text-2xl font-semibold text-(--color-text)">--</p>
      </Card>
    </div>
  )
}
