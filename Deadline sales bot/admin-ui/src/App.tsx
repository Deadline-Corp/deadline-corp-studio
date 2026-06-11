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
        <Route element={<RequireAuth><Layout /></RequireAuth>}>
          <Route path="/" element={<Canvas />} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/funnel" element={<Funnel />} />
          <Route path="/brain" element={<Brain />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  )
}
