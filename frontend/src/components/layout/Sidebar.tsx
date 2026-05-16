import { Link, useLocation } from 'react-router-dom'
import { LayoutDashboard, Map, Pickaxe, PanelLeftClose, PanelLeft, Radar } from 'lucide-react'
import { cn } from '@/lib/cn'
import { useUiStore } from '@/stores/uiStore'

/* Collapsed = icon rail only. Expanded = icon rail + labels. */
const W_COLLAPSED = 56   // px
const W_EXPANDED  = 200  // px
const ICON_RAIL   = W_COLLAPSED  // icon zone always equals collapsed width

const navItems = [
  { to: '/workspace', label: 'Workspace', icon: Map },
  { to: '/projetos',  label: 'Projetos',  icon: Pickaxe },
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
]

export function Sidebar() {
  const location  = useLocation()
  const { sidebarOpen, toggleSidebar } = useUiStore()

  return (
    <aside
      className="flex shrink-0 flex-col overflow-hidden border-r border-(--color-border) bg-(--color-surface) transition-[width] duration-300 ease-out"
      style={{ width: sidebarOpen ? W_EXPANDED : W_COLLAPSED }}
    >
      {/*
        Inner wrapper is always W_EXPANDED wide.
        The aside clips it via overflow-hidden.
      */}
      <div style={{ width: W_EXPANDED }} className="flex flex-1 flex-col">

        {/* Brand */}
        <div className="flex h-14 shrink-0 items-center border-b border-(--color-border)">
          <div style={{ width: ICON_RAIL }} className="flex shrink-0 items-center justify-center">
            <Radar size={20} className="text-(--color-primary)" />
          </div>
          <span className="whitespace-nowrap text-sm font-semibold text-(--color-text)">
            MineralRadar
          </span>
        </div>

        {/* Nav links */}
        <nav className="flex flex-1 flex-col gap-0.5 py-2 px-2">
          {navItems.map((item) => {
            const Icon = item.icon
            const isActive = location.pathname.startsWith(item.to)

            return (
              <Link
                key={item.to}
                to={item.to}
                title={item.label}
                className={cn(
                  'flex h-9 items-center rounded-md text-sm',
                  isActive
                    ? 'bg-(--color-primary) text-white'
                    : 'text-(--color-text-muted) hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-(--color-text)',
                )}
              >
                {/* Icon zone: ICON_RAIL minus nav's px-2 (8px each side = 16px total) */}
                <div
                  style={{ width: ICON_RAIL - 16, minWidth: ICON_RAIL - 16 }}
                  className="flex items-center justify-center"
                >
                  <Icon size={18} />
                </div>
                <span className="whitespace-nowrap pl-2">{item.label}</span>
              </Link>
            )
          })}
        </nav>

        {/* Toggle */}
        <div className="shrink-0 border-t border-(--color-border) py-2 px-2">
          <button
            onClick={toggleSidebar}
            title={sidebarOpen ? 'Recolher' : 'Expandir'}
            className="flex h-9 w-full items-center rounded-md text-sm text-(--color-text-muted) hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-(--color-text)"
          >
            <div
              style={{ width: ICON_RAIL - 16, minWidth: ICON_RAIL - 16 }}
              className="flex items-center justify-center"
            >
              {sidebarOpen ? <PanelLeftClose size={18} /> : <PanelLeft size={18} />}
            </div>
            <span className="whitespace-nowrap pl-2">Recolher</span>
          </button>
        </div>

      </div>
    </aside>
  )
}
