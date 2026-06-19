import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { ConvSummary } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useDrawer } from '../components/DrawerContext'
import { useStages, useStageLabel } from '../overviewContext'
import { HintBar } from '../components/HintBar'
import { CHANNEL_META, TEMP_META, fmtAgo, initials, onLeadsChanged, emitLeadDismissed } from '../lib'
import { dismissLead } from '../api/leads'

/* Единый inbox: все переписки всех каналов, фильтры, клик → drawer. */

export function Inbox() {
  const [params, setParams] = useSearchParams()
  const [items, setItems] = useState<ConvSummary[]>([])
  const [total, setTotal] = useState(0)
  const [loaded, setLoaded] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const { openConversation } = useDrawer()
  const stages = useStages()
  const stageLabel = useStageLabel()

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

  // Пометить «важный/мой лид» (жёлтая подсветка) — оптимистично, с откатом при ошибке.
  const togglePin = async (c: ConvSummary) => {
    const next = !c.pinned
    setItems(prev => prev.map(x => (x.id === c.id ? { ...x, pinned: next } : x)))
    try { await api.post(`/conversations/${c.id}/pin`, { pinned: next }) }
    catch { setItems(prev => prev.map(x => (x.id === c.id ? { ...x, pinned: !next } : x))) }
  }

  usePolling(load, 10000, [channel, stage, temperature, q])
  // Мгновенно перечитать список после любой мутации лида (своей или из карточки).
  useEffect(() => onLeadsChanged(load), [channel, stage, temperature, q])

  // Быстро «убрать» лид (спам/ненужный) → в архив, строка сразу исчезает, тост «Вернуть».
  const dismiss = async (c: ConvSummary) => {
    const prevStage = c.lead_stage
    const label = c.customer.display_name || c.customer.name || 'Лид'
    setItems(prev => prev.filter(x => x.id !== c.id))
    try {
      await dismissLead(c.id)
      emitLeadDismissed({ id: c.id, stage: prevStage, label })
    } catch { setToast('Не удалось убрать'); setTimeout(() => setToast(null), 3000); void load() }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Переписки</h1>
        <span className="sub">{loaded ? `${total} диалогов` : '…'}</span>
      </div>

      <HintBar id="inbox" icon="💬">
        Все переписки со всех каналов — в одном месте (цветной бейдж показывает, откуда лид).
        Кликните на диалог: можно <b>ответить самому</b> («Взять на себя» — бот замолчит и не будет мешать),
        сменить стадию, заполнить поля или пнуть молчуна.
      </HintBar>

      <div className="filters">
        <select value={channel} onChange={e => setFilter('channel', e.target.value)}>
          <option value="">Все каналы</option>
          {Object.entries(CHANNEL_META).map(([id, m]) => (
            <option key={id} value={id}>{m.icon} {m.label}</option>
          ))}
        </select>
        <select value={stage} onChange={e => setFilter('stage', e.target.value)}>
          <option value="">Все стадии</option>
          {stages.map(s => <option key={s.stage} value={s.stage}>{s.label}</option>)}
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
            <div
              className={`conv-row${c.pinned ? ' pinned' : ''}${c.wa_autonomous ? ' autonomous' : ''}`}
              key={c.id}
              onClick={() => openConversation(c.id)}
            >
              <button
                className={`pin-star${c.pinned ? ' on' : ''}`}
                title={c.pinned ? 'Важный лид — снять пометку' : 'Пометить как важный (чтобы не потерять)'}
                onClick={e => { e.stopPropagation(); togglePin(c) }}
              >★</button>
              <div className="avatar">{initials(c.customer.name)}</div>
              <div className="c-main">
                <div className="c-name">
                  {c.customer.display_name || c.customer.name || 'Без имени'}
                  <span className={`chip ${ch?.cls ?? ''}`} style={{ fontWeight: 500 }}>
                    {ch?.icon} {ch?.label ?? c.channel}
                  </span>
                  {c.wa_autonomous && <span className="chip bot-led">🤖 бот ведёт</span>}
                  {c.operator_takeover && <span className="chip ok">👤 оператор</span>}
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
              <button className="conv-x" title="Убрать в архив (не сложилось / спам)"
                      onClick={e => { e.stopPropagation(); void dismiss(c) }}>✕</button>
            </div>
          )
        })}
      </div>
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
