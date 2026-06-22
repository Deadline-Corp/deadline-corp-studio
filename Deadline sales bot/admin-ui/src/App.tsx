import { lazy, Suspense } from 'react'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { getToken } from './api/client'
import { Layout } from './components/Layout'
import { Login } from './pages/Login'
import { Inbox } from './pages/Inbox'
import { Funnel } from './pages/Funnel'
import { Brain } from './pages/Brain'
import { Tasks } from './pages/Tasks'
import { Settings } from './pages/Settings'
import { Channels } from './pages/Channels'
import { Logs } from './pages/Logs'
import { BotDecisions } from './pages/BotDecisions'
import { Automations } from './pages/Automations'
import { Analytics } from './pages/Analytics'
import { Onboarding } from './pages/Onboarding'
// Тяжёлые библиотеки (React Flow в Канвасе, FullCalendar в Календаре) — отдельными
// чанками через lazy: не грузим их на каждом заходе (вкл. /login) → быстрее первый
// экран, особенно с телефона.
const Canvas = lazy(() => import('./pages/Canvas').then(m => ({ default: m.Canvas })))
const Calendar = lazy(() => import('./pages/Calendar').then(m => ({ default: m.Calendar })))
const RouteFallback = () => <div style={{ padding: 24, color: 'var(--text-faint)' }}>Загрузка…</div>

/* HashRouter: SPA живёт под /admin/ui/ внутри FastAPI StaticFiles — hash-роуты
   не требуют server-side fallback на index.html для глубоких ссылок. */

function RequireAuth({ children }: { children: JSX.Element }) {
  if (!getToken()) return <Navigate to="/login" replace />
  return children
}

export function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/onboarding" element={<RequireAuth><Onboarding /></RequireAuth>} />
        <Route element={<RequireAuth><Layout /></RequireAuth>}>
          <Route path="/" element={<Suspense fallback={<RouteFallback />}><Canvas /></Suspense>} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/funnel" element={<Funnel />} />
          <Route path="/brain" element={<Brain />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/calendar" element={<Suspense fallback={<RouteFallback />}><Calendar /></Suspense>} />
          <Route path="/automations" element={<Automations />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/channels" element={<Channels />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/bot-decisions" element={<BotDecisions />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  )
}
