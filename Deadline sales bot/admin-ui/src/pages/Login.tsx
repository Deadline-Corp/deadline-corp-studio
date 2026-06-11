import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken, clearToken } from '../api/client'

export function Login() {
  const [token, setTok] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!token.trim() || busy) return
    setBusy(true)
    setErr('')
    setToken(token.trim())
    try {
      await api.get('/me')
      navigate('/')
    } catch (e: any) {
      clearToken()
      setErr(e.status === 503
        ? 'Панель выключена на сервере (нет ADMIN_UI_TOKEN в env).'
        : 'Неверный токен.')
    } finally { setBusy(false) }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className="logo" style={{ width: 34, height: 34, borderRadius: 9, background: 'linear-gradient(135deg, var(--accent), #4938d8)', display: 'grid', placeItems: 'center', fontWeight: 800, color: '#fff' }}>D</div>
          <h1>Панель управления</h1>
        </div>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Введите admin-токен (ADMIN_UI_TOKEN из Railway).
        </p>
        <input
          type="password"
          placeholder="Токен"
          value={token}
          onChange={e => setTok(e.target.value)}
          autoFocus
        />
        {err && <div className="err">{err}</div>}
        <button className="btn primary" type="submit" disabled={busy || !token.trim()}>
          {busy ? <span className="spin" /> : 'Войти'}
        </button>
      </form>
    </div>
  )
}
