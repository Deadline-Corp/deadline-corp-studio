import { useState } from 'react'
import { api } from '../api/client'
import { ActivityLogItem, LogsResp } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useMe } from '../overviewContext'
import { useDrawer } from '../components/DrawerContext'
import { fmtTime } from '../lib'
import { HintBar } from '../components/HintBar'

/* Журнал активности системы: что/как/почему/кто. История работы приложения для
   быстрого поиска причин ошибок, изменений и событий (Настройки → расширенные). */

const CAT: Record<string, { label: string; icon: string }> = {
  config:  { label: 'конфигурация', icon: '⚙️' },
  channel: { label: 'каналы', icon: '🔌' },
  stage:   { label: 'стадии', icon: '📊' },
  bot:     { label: 'бот', icon: '🤖' },
  send:    { label: 'отправка', icon: '📤' },
  task:    { label: 'задачи', icon: '⏰' },
  auth:    { label: 'доступ', icon: '🔐' },
  error:   { label: 'ошибки', icon: '❗' },
  lead:    { label: 'лиды', icon: '🧑' },
  system:  { label: 'система', icon: '🖥' },
}
const LEVEL_CLS: Record<string, string> = { info: '', warn: 'warn', error: 'danger' }

export function Logs() {
  const me = useMe()
  const { openConversation } = useDrawer()
  const [items, setItems] = useState<ActivityLogItem[]>([])
  const [cat, setCat] = useState('')
  const [level, setLevel] = useState('')
  const [q, setQ] = useState('')
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [err24, setErr24] = useState(0)
  const [nextBefore, setNextBefore] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  const qs = (extra?: Record<string, string>) => {
    const p = new URLSearchParams()
    if (cat) p.set('category', cat)
    if (level) p.set('level', level)
    if (q.trim()) p.set('q', q.trim())
    p.set('limit', '80')
    Object.entries(extra || {}).forEach(([k, v]) => p.set(k, v))
    return p.toString()
  }

  const load = async () => {
    try {
      const r = await api.get<LogsResp>(`/logs?${qs()}`)
      setItems(r.items); setCounts(r.cat_counts_24h || {})
      setErr24(r.errors_24h || 0); setNextBefore(r.next_before); setLoaded(true)
    } catch { /* 401 редиректит сам */ }
  }
  const loadMore = async () => {
    if (!nextBefore) return
    try {
      const r = await api.get<LogsResp>(`/logs?${qs({ before: nextBefore })}`)
      setItems(prev => [...prev, ...r.items]); setNextBefore(r.next_before)
    } catch { /* ignore */ }
  }

  usePolling(load, 15000, [cat, level, q])

  if (me && me.role !== 'owner') {
    return <div className="page"><div className="empty">📋 Журнал доступен только владельцу.</div></div>
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Логи системы</h1>
        <span className="sub">{err24 > 0 ? `❗ ${err24} ошибок за сутки` : (loaded ? 'история работы' : '…')}</span>
      </div>

      <HintBar id="logs" icon="📋">
        История работы приложения — <b>что / как / почему / кто</b>. Видно смену настроек,
        решения бота, действия операторов, массовые отправки и ошибки. Кликните на строку —
        раскроются детали; если событие про лида — откроется его карточка. Хранится 30 дней.
      </HintBar>

      <div className="filters">
        <select value={cat} onChange={e => setCat(e.target.value)}>
          <option value="">Все категории</option>
          {Object.entries(CAT).map(([k, v]) =>
            <option key={k} value={k}>{v.icon} {v.label}{counts[k] ? ` (${counts[k]})` : ''}</option>)}
        </select>
        <select value={level} onChange={e => setLevel(e.target.value)}>
          <option value="">Любой уровень</option>
          <option value="info">инфо</option>
          <option value="warn">предупреждение</option>
          <option value="error">ошибка</option>
        </select>
        <input placeholder="🔎 Поиск по тексту события…" value={q}
               onChange={e => setQ(e.target.value)} style={{ flex: 1, minWidth: 180 }} />
      </div>

      <div className="log-list">
        {loaded && items.length === 0 && <div className="empty">Событий по фильтру нет</div>}
        {items.map(it => {
          const c = CAT[it.category] || { label: it.category, icon: '•' }
          const open = expanded === it.id
          return (
            <div key={it.id} className={`log-row lvl-${it.level}`}>
              <div className="log-main" onClick={() => setExpanded(open ? null : it.id)}>
                <span className="faint mono log-time">{it.at ? fmtTime(it.at) : ''}</span>
                <span className={`chip ${LEVEL_CLS[it.level] ?? ''}`}>{it.level}</span>
                <span className="chip" title={c.label}>{c.icon}</span>
                <span className="log-summary">{it.summary}</span>
                <span className="faint log-actor">{it.actor}</span>
              </div>
              {open && (
                <div className="log-detail">
                  {it.meta && <pre>{JSON.stringify(it.meta, null, 2)}</pre>}
                  {it.conversation_id && (
                    <button className="btn sm" onClick={() => openConversation(it.conversation_id!)}>
                      Открыть карточку лида →
                    </button>
                  )}
                </div>
              )}
            </div>
          )
        })}
        {nextBefore && (
          <button className="btn sm ghost" style={{ alignSelf: 'center', marginTop: 8 }} onClick={loadMore}>
            Показать ещё
          </button>
        )}
      </div>
    </div>
  )
}
