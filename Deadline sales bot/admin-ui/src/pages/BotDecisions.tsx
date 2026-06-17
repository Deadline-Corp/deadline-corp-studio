import { useState } from 'react'
import { api } from '../api/client'
import { BotDecisionItem } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useDrawer } from '../components/DrawerContext'
import { fmtTime } from '../lib'
import { HintBar } from '../components/HintBar'
import { DECISION_META } from '../components/ConversationDrawer'

/* Журнал РЕШЕНИЙ БОТА — отдельный от системных логов. Что бот решил и ПОЧЕМУ,
   простым языком, по всем лидам. Прозрачность логики → доверие + тюнинг правил. */

export function BotDecisions() {
  const { openConversation } = useDrawer()
  const [items, setItems] = useState<BotDecisionItem[]>([])
  const [type, setType] = useState('')
  const [nextBefore, setNextBefore] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  const qs = (extra?: Record<string, string>) => {
    const p = new URLSearchParams()
    p.set('limit', '60')
    Object.entries(extra || {}).forEach(([k, v]) => p.set(k, v))
    return p.toString()
  }

  const load = async () => {
    try {
      const r = await api.get<{ items: BotDecisionItem[]; next_before: string | null }>(`/bot-decisions?${qs()}`)
      // фильтр по типу — на клиенте (лента небольшая); сервер отдаёт всё
      setItems(type ? r.items.filter(i => i.decision_type === type) : r.items)
      setNextBefore(r.next_before)
      setLoaded(true)
    } catch { /* 401 редиректит сам */ }
  }
  const loadMore = async () => {
    if (!nextBefore) return
    try {
      const r = await api.get<{ items: BotDecisionItem[]; next_before: string | null }>(`/bot-decisions?${qs({ before: nextBefore })}`)
      const more = type ? r.items.filter(i => i.decision_type === type) : r.items
      setItems(prev => [...prev, ...more]); setNextBefore(r.next_before)
    } catch { /* ignore */ }
  }

  usePolling(load, 15000, [type])

  return (
    <div className="page">
      <div className="page-head">
        <h1>🤖 Решения бота</h1>
        <span className="sub">{loaded ? 'что и почему делал бот' : '…'}</span>
      </div>

      <HintBar id="bot-decisions" icon="🤖">
        Отдельный от системных логов журнал — <b>какие решения принимал бот и ПОЧЕМУ</b>:
        двигал стадию, предлагал/бронировал созвон, дожимал молчуна, передавал оператору,
        возвращал проигранного. Видно логику → можно доверять и точечно подстраивать правила
        в «Мозге». Кликните строку — откроется карточка лида. Хранится 60 дней.
      </HintBar>

      <div className="filters">
        <select value={type} onChange={e => setType(e.target.value)}>
          <option value="">Все решения</option>
          {Object.entries(DECISION_META).map(([k, v]) =>
            <option key={k} value={k}>{v.icon} {v.label}</option>)}
        </select>
      </div>

      <div className="log-list">
        {loaded && items.length === 0 && (
          <div className="empty">
            Решений пока нет — бот запишет сюда каждое решение (стадия/созвон/дожим/передача),
            как только начнёт вести лидов.
          </div>
        )}
        {items.map(d => {
          const m = DECISION_META[d.decision_type] || { icon: '•', label: d.decision_type }
          const open = expanded === d.id
          return (
            <div key={d.id} className="log-row">
              <div className="log-main" onClick={() => setExpanded(open ? null : d.id)}>
                <span className="faint mono log-time">{d.at ? fmtTime(d.at) : ''}</span>
                <span className="chip" title={m.label}>{m.icon} {m.label}</span>
                <span className="log-summary">{d.reason}</span>
                <span className="faint log-actor">{d.actor}</span>
              </div>
              {open && (
                <div className="log-detail">
                  {d.detail && <pre>{JSON.stringify(d.detail, null, 2)}</pre>}
                  {d.conversation_id && (
                    <button className="btn sm" onClick={() => openConversation(d.conversation_id!)}>
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
