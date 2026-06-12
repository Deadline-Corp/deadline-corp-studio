import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { api, clearToken } from '../api/client'
import { Overview } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { DrawerProvider } from './DrawerContext'
import { OverviewCtx } from '../overviewContext'
import { Tour } from './Tour'
import { useEffect } from 'react'

/* Постоянный сайдбар + общий Overview-контекст (бейджи живут на поллинге 30с). */

const NAV = [
  { to: '/', icon: '🕸', label: 'Канвас', end: true },
  { to: '/funnel', icon: '📊', label: 'Воронка' },
  { to: '/inbox', icon: '💬', label: 'Переписки' },
  { to: '/tasks', icon: '⏰', label: 'Задачи' },
  { to: '/automations', icon: '⚡', label: 'Автоматизации' },
  { to: '/analytics', icon: '📈', label: 'Аналитика' },
  { to: '/brain', icon: '🧠', label: 'Мозг' },
  { to: '/channels', icon: '🔌', label: 'Каналы' },
  { to: '/settings', icon: '⚙️', label: 'Настройки' },
]

export function Layout() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [brand, setBrand] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    void api.get<{ display_name: string }>('/me').then(r => setBrand(r.display_name)).catch(() => { /* */ })
  }, [])

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
              <div className="logo">{(brand || 'D')[0].toUpperCase()}</div>
              <span style={{ fontSize: brand && brand.length > 12 ? 12.5 : 15 }}>
                {(brand || 'DEADLINE').toUpperCase()}
              </span>
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
          <Tour />
        </div>
      </DrawerProvider>
    </OverviewCtx.Provider>
  )
}
