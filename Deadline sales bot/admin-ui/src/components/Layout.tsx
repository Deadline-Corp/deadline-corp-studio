import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { api, clearToken } from '../api/client'
import { Overview } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { DrawerProvider } from './DrawerContext'
import { createContext, useContext } from 'react'

/* Постоянный сайдбар + общий Overview-контекст (бейджи живут на поллинге 30с). */

const OverviewCtx = createContext<Overview | null>(null)
export function useOverview() {
  return useContext(OverviewCtx)
}

const NAV = [
  { to: '/', icon: '🕸', label: 'Канвас', end: true },
  { to: '/funnel', icon: '📊', label: 'Воронка' },
  { to: '/inbox', icon: '💬', label: 'Переписки' },
  { to: '/brain', icon: '🧠', label: 'Мозг' },
  { to: '/tasks', icon: '⏰', label: 'Задачи' },
  { to: '/settings', icon: '⚙️', label: 'Настройки' },
]

export function Layout() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const navigate = useNavigate()

  usePolling(async () => {
    try { setOverview(await api.get<Overview>('/overview')) } catch { /* 401 редиректит сам */ }
  }, 30000)

  const logout = () => {
    clearToken()
    navigate('/login')
  }

  const badge = (to: string): number | null => {
    if (!overview) return null
    if (to === '/inbox') return overview.inbox.open || null
    if (to === '/tasks') return overview.tasks.scheduled_pending || null
    return null
  }

  return (
    <OverviewCtx.Provider value={overview}>
      <DrawerProvider>
        <div className="layout">
          <aside className="sidebar">
            <div className="brand">
              <div className="logo">D</div>
              <span>DEADLINE</span>
            </div>
            {NAV.map(n => (
              <NavLink key={n.to} to={n.to} end={n.end as any}
                className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
                <span className="nav-ico">{n.icon}</span>
                {n.label}
                {badge(n.to) != null && <span className="nav-badge">{badge(n.to)}</span>}
              </NavLink>
            ))}
            <div className="foot">
              <div>{overview ? `${overview.bot.display_name} · v${overview.bot.version}` : '…'}</div>
              <div className="mono" style={{ fontSize: 10.5, margin: '3px 0' }}>
                {overview?.bot.model.split('/').pop()}
              </div>
              <button onClick={logout}>Выйти</button>
            </div>
          </aside>
          <main className="main">
            <Outlet />
          </main>
        </div>
      </DrawerProvider>
    </OverviewCtx.Provider>
  )
}
