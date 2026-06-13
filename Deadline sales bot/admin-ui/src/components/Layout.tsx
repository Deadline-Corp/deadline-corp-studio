import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { api, clearToken } from '../api/client'
import { Overview } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { DrawerProvider } from './DrawerContext'
import { OverviewCtx, MeCtx, Me } from '../overviewContext'
import { Tour } from './Tour'

/* Постоянный сайдбар + Overview/Me контексты. Менеджеру навигация урезана
   (Мозг/Автоматизации/Каналы/Настройки скрыты; бэкенд форсит то же 403-ми). */

const NAV = [
  { to: '/', icon: '🕸', label: 'Канвас', end: true },
  { to: '/funnel', icon: '📊', label: 'Воронка' },
  { to: '/inbox', icon: '💬', label: 'Переписки' },
  { to: '/tasks', icon: '⏰', label: 'Задачи' },
  { to: '/calendar', icon: '📅', label: 'Календарь' },
  { to: '/automations', icon: '⚡', label: 'Автоматизации', owner: true },
  { to: '/analytics', icon: '📈', label: 'Аналитика' },
  { to: '/brain', icon: '🧠', label: 'Мозг', owner: true },
  { to: '/channels', icon: '🔌', label: 'Каналы', owner: true },
  { to: '/settings', icon: '⚙️', label: 'Настройки', owner: true },
]
const OWNER_PATHS = NAV.filter(n => (n as any).owner).map(n => n.to)

export function Layout() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [me, setMe] = useState<Me | null>(null)
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    void api.get<Me>('/me').then(m => {
      setMe(m)
      // White-label: акцентный цвет клиента поверх дефолтного фиолетового.
      if (m.accent_color) {
        document.documentElement.style.setProperty('--accent', m.accent_color)
        document.documentElement.style.setProperty('--accent-border', m.accent_color + '99')
        document.documentElement.style.setProperty('--accent-soft', m.accent_color + '24')
      }
    }).catch(() => { /* 401 редиректит сам */ })
  }, [])

  // Менеджер на owner-странице (прямой URL) → мягко на канвас.
  useEffect(() => {
    if (me?.role === 'manager' && OWNER_PATHS.includes(location.pathname)) {
      navigate('/', { replace: true })
    }
  }, [me, location.pathname, navigate])

  usePolling(async () => {
    try { setOverview(await api.get<Overview>('/overview')) } catch { /* ignore */ }
  }, 30000)

  const [theme, setTheme] = useState(localStorage.getItem('deadline_theme') || 'dark')
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('deadline_theme', theme)
  }, [theme])

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

  const brand = me?.display_name || null
  const visibleNav = NAV.filter(n => !(n as any).owner || me?.role !== 'manager')

  return (
    <MeCtx.Provider value={me}>
      <OverviewCtx.Provider value={overview}>
        <DrawerProvider>
          <div className="layout">
            <aside className="sidebar">
              <div className="brand">
                {me?.logo_url
                  ? <img src={me.logo_url} alt="" style={{ width: 30, height: 30, borderRadius: 8, objectFit: 'cover' }} />
                  : <div className="logo">{(brand || 'D')[0].toUpperCase()}</div>}
                <span style={{ fontSize: brand && brand.length > 12 ? 12.5 : 15 }}>
                  {(brand || 'DEADLINE').toUpperCase()}
                </span>
              </div>
              {visibleNav.map(n => (
                <NavLink key={n.to} to={n.to} end={n.end as any} data-tour={`nav-${n.to}`}
                  className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
                  <span className="nav-ico">{n.icon}</span>
                  {n.label}
                  {badge(n.to) != null && <span className="nav-badge">{badge(n.to)}</span>}
                </NavLink>
              ))}
              <div className="foot">
                <div>
                  {me ? (me.role === 'manager' ? `👤 ${me.member_name}` : `${me.display_name}`) : '…'}
                </div>
                <div className="mono" style={{ fontSize: 10.5, margin: '3px 0' }}>
                  {overview?.bot.model.split('/').pop()}
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <button onClick={logout}>Выйти</button>
                  <button title="Светлая/тёмная тема"
                          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
                    {theme === 'dark' ? '☀️' : '🌙'}
                  </button>
                </div>
              </div>
            </aside>
            <main className="main">
              <Outlet />
            </main>
            <Tour />
          </div>
        </DrawerProvider>
      </OverviewCtx.Provider>
    </MeCtx.Provider>
  )
}
