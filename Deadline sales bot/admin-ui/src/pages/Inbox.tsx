import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { ConvSummary } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useDrawer } from '../components/DrawerContext'
import { CHANNEL_META, STAGES, TEMP_META, stageLabel, fmtAgo, initials } from '../lib'

/* Единый inbox: все переписки всех каналов, фильтры, клик → drawer. */

export function Inbox() {
  const [params, setParams] = useSearchParams()
  const [items, setItems] = useState<ConvSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loaded, setLoaded] = useState(false)
  const { openConversation } = useDrawer()

  const channel = params.get('channel') ?? ''
  const stage = params.get('stage') ?? ''
  const temperature = params.get('temperature') ?? ''
  const q = params.get('q') ?? ''

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value); else next.delete(key)
    setParams(next, { replace: true })
  }

  const load = async () => {
    const qs = new URLSearchParams()
    if (channel) qs.set('channel', channel)
    if (stage) qs.set('stage', stage)
    if (temperature) qs.set('temperature', temperature)
    if (q) qs.set('q', q)
    qs.set('limit', '60')
    try {
      const r = await api.get<{ total: number; items: ConvSummary[] }>(`/conversations?${qs}`)
      setItems(r.items)
      setTotal(r.total)
      setLoaded(true)
    } catch { /* 401 редиректит сам */ }
  }

  usePolling(load, 10000, [channel, stage, temperature, q])

  return (
    <div className="page">
      <div className="page-head">
        <h1>Переписки</h1>
        <span className="sub">{loaded ? `${total} диалогов` : '…'}</span>
      </div>

      <div className="filters">
        <select value={channel} onChange={e => setFilter('channel', e.target.value)}>
          <option value="">Все каналы</option>
          {Object.entries(CHANNEL_META).map(([id, m]) => (
            <option key={id} value={id}>{m.icon} {m.label}</option>
          ))}
        </select>
        <select value={stage} onChange={e => setFilter('stage', e.target.value)}>
          <option value="">Все стадии</option>
          {STAGES.map(s => <option key={s.stage} value={s.stage}>{s.label}</option>)}
        </select>
        <select value={temperature} onChange={e => setFilter('temperature', e.target.value)}>
          <option value="">Любая температура</option>
          {Object.keys(TEMP_META).map(t => <option key={t} value={t}>{TEMP_META[t].label}</option>)}
        </select>
        <input
          placeholder="Поиск: имя / email / телефон"
          defaultValue={q}
          onKeyDown={e => { if (e.key === 'Enter') setFilter('q', (e.target as HTMLInputElement).value) }}
          style={{ width: 220 }}
        />
      </div>

      <div className="inbox-list">
        {loaded && items.length === 0 && <div className="empty">Диалогов нет</div>}
        {items.map(c => {
          const ch = CHANNEL_META[c.channel]
          const temp = TEMP_META[c.customer.lead_temperature]
          return (
            <div className="conv-row" key={c.id} onClick={() => openConversation(c.id)}>
              <div className="avatar">{initials(c.customer.name)}</div>
              <div className="c-main">
                <div className="c-name">
                  {c.customer.name || 'Без имени'}
                  <span className="faint" style={{ fontWeight: 400, fontSize: 12 }}>{ch?.icon}</span>
                  {c.operator_takeover && <span className="chip ok">👤</span>}
                </div>
                <div className="c-preview">{c.preview || '—'}</div>
              </div>
              <div className="c-meta">
                <div style={{ display: 'flex', gap: 5 }}>
                  <span className="chip accent">{stageLabel(c.lead_stage)}</span>
                  {temp && <span className={`chip ${temp.cls}`}>{temp.label}</span>}
                </div>
                <span className="c-time">{fmtAgo(c.last_message_at)} назад</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
