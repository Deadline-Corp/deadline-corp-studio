import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken, clearToken } from '../api/client'

export function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [token, setTok] = useState('')
  const [byToken, setByToken] = useState(false)  // фолбэк: вход по admin-токену
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  const proceed = async () => {
    const me = await api.get<{ onboarding_done: boolean; role: string }>('/me')
    // Онбординг — только владельцу; менеджер сразу в работу.
    navigate(me.role === 'owner' && !me.onboarding_done ? '/onboarding' : '/')
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setErr('')
    try {
      if (byToken) {
        if (!token.trim()) { setBusy(false); return }
        setToken(token.trim())
        await proceed()
      } else {
        if (!username.trim() || !password) { setBusy(false); return }
        const r = await api.post<{ token: string }>('/login', { username: username.trim(), password })
        setToken(r.token)
        await proceed()
      }
    } catch (e: any) {
      clearToken()
      setErr(
        e.status === 401 ? 'Неверный логин или пароль.'
          : e.status === 503
            ? (byToken ? 'Панель выключена на сервере (нет токена в env).'
              : 'Вход по логину не настроен — войдите по токену.')
            : 'Не удалось войти.',
      )
    } finally { setBusy(false) }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className="logo" style={{ width: 34, height: 34, borderRadius: 9, background: 'linear-gradient(135deg, var(--accent), #4938d8)', display: 'grid', placeItems: 'center', fontWeight: 800, color: '#fff' }}>D</div>
          <h1>Панель управления</h1>
        </div>
        {byToken ? (
          <>
            <p className="muted" style={{ margin: 0, fontSize: 13 }}>Вход по admin-токену.</p>
            <input type="password" placeholder="Токен" value={token} onChange={e => setTok(e.target.value)} autoFocus />
          </>
        ) : (
          <>
            <p className="muted" style={{ margin: 0, fontSize: 13 }}>Введите логин и пароль.</p>
            <input type="text" placeholder="Логин" value={username} autoComplete="username"
                   onChange={e => setUsername(e.target.value)} autoFocus />
            <input type="password" placeholder="Пароль" value={password} autoComplete="current-password"
                   onChange={e => setPassword(e.target.value)} />
          </>
        )}
        {err && <div className="err">{err}</div>}
        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? <span className="spin" /> : 'Войти'}
        </button>
        <button type="button" className="btn ghost" style={{ fontSize: 12, marginTop: 2 }}
                onClick={() => { setByToken(v => !v); setErr('') }}>
          {byToken ? '← Вход по логину и паролю' : 'Войти по токену'}
        </button>
      </form>
    </div>
  )
}
