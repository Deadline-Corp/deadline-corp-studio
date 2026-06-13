import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { getToken } from './api/client'
import { Layout } from './components/Layout'
import { Login } from './pages/Login'
import { Canvas } from './pages/Canvas'
import { Inbox } from './pages/Inbox'
import { Funnel } from './pages/Funnel'
import { Brain } from './pages/Brain'
import { Tasks } from './pages/Tasks'
import { Settings } from './pages/Settings'
import { Channels } from './pages/Channels'
import { Automations } from './pages/Automations'
import { Analytics } from './pages/Analytics'
import { Onboarding } from './pages/Onboarding'
import { Calendar } from './pages/Calendar'

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
          <Route path="/" element={<Canvas />} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/funnel" element={<Funnel />} />
          <Route path="/brain" element={<Brain />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/calendar" element={<Calendar />} />
          <Route path="/automations" element={<Automations />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/channels" element={<Channels />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  )
}
