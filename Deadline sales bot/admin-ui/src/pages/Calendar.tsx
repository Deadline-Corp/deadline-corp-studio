import { useState } from 'react'
import { api, getToken } from '../api/client'
import { TodayView } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useDrawer } from '../components/DrawerContext'
import { CHANNEL_META, fmtTime } from '../lib'
import { HintBar } from '../components/HintBar'

/* Календарь v1: ближайшие 14 дней — созвоны (бот бронирует их сам и шлёт
   напоминания за день/час) + задачи с дедлайном. Клик — переписка лида.
   Слоты/Google-синк — следующая итерация (#15). */

export function Calendar() {
  const [view, setView] = useState<TodayView | null>(null)
  const [copied, setCopied] = useState(false)
  const { openConversation } = useDrawer()

  const subscribeUrl = `${location.origin}/calendar.ics?token=${encodeURIComponent(getToken() || '')}`
  const copySubscribe = () => {
    navigator.clipboard?.writeText(subscribeUrl)
    setCopied(true)
    setTimeout(() => setCopied(false), 3000)
  }

  usePolling(async () => {
    try { setView(await api.get<TodayView>('/today')) } catch { /* */ }
  }, 30000)

  const days: Array<{ date: Date; items: Array<{ kind: string; time: string | null; label: string; conv: string | null; ch?: string }> }> = []
  const now = new Date()
  for (let i = 0; i < 14; i++) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i)
    days.push({ date: d, items: [] })
  }
  const put = (iso: string | null, item: any) => {
    if (!iso) return
    const d = new Date(iso)
    const idx = Math.floor((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
      - new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()) / 864e5)
    if (idx >= 0 && idx < 14) days[idx].items.push({ ...item, time: iso })
  }
  if (view) {
    view.calls.forEach(c => put(c.call_at, {
      kind: 'call', label: `📞 ${c.customer.name || c.customer.email || 'Лид'}${c.medium ? ' · ' + c.medium : ''}`,
      conv: c.conversation_id, ch: c.channel,
    }))
    ;[...view.overdue, ...view.today, ...view.upcoming].forEach(t => put(t.due_at, {
      kind: t.executor === 'bot' ? 'bot' : 'task',
      label: `${t.executor === 'bot' ? '🤖' : '📋'} ${t.customer.name || 'лид'}: ${(t.text || '').slice(0, 50)}`,
      conv: t.conversation_id, ch: t.channel,
    }))
  }
  const dows = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб']

  return (
    <div className="page">
      <div className="page-head">
        <h1>Календарь</h1>
        <span className="sub">созвоны и дедлайны на 2 недели · бот сам бронирует время и напоминает за день и за час</span>
      </div>
      <HintBar id="calendar" icon="📅">
        Созвоны, которые бот назначил с лидами, и задачи с дедлайном. Клик по событию —
        откроется переписка. Бот предлагает лиду 2-3 свободных слота (не вываливая весь день),
        бронирует и шлёт напоминания обоим. Перенос — лид пишет боту, тот перебронирует.
      </HintBar>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
        <button className="btn sm primary" onClick={copySubscribe}>
          {copied ? '✅ Ссылка скопирована' : '📲 Подписаться в телефоне'}
        </button>
        <span className="faint" style={{ fontSize: 12 }}>
          вставьте ссылку в Google Календарь / iPhone «Подписка на календарь» — созвоны и
          дедлайны появятся в телефоне и будут обновляться сами
        </span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 8 }}>
        {days.map((d, i) => {
          const isToday = i === 0
          return (
            <div key={i} className="card" style={{
              padding: 10, minHeight: 96,
              borderColor: isToday ? 'var(--accent-border)' : undefined,
            }}>
              <div style={{ fontSize: 11.5, fontWeight: 700, color: isToday ? 'var(--accent)' : 'var(--text-faint)' }}>
                {dows[d.date.getDay()]} {d.date.getDate()}.{String(d.date.getMonth() + 1).padStart(2, '0')}
                {isToday && ' · сегодня'}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginTop: 6 }}>
                {d.items.sort((a, b) => (a.time || '').localeCompare(b.time || '')).map((it, k) => (
                  <div key={k}
                       onClick={() => it.conv && openConversation(it.conv)}
                       style={{
                         fontSize: 11, lineHeight: 1.35, padding: '4px 6px', borderRadius: 6,
                         cursor: it.conv ? 'pointer' : 'default',
                         background: it.kind === 'call' ? 'var(--accent-soft)' : 'var(--panel-2)',
                         border: it.kind === 'call' ? '1px solid var(--accent-border)' : '1px solid var(--border)',
                       }}>
                    <b>{fmtTime(it.time)}</b> {CHANNEL_META[it.ch || '']?.icon ?? ''}<br />{it.label}
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>
      {view && days.every(d => d.items.length === 0) && (
        <div className="empty">Пока пусто — назначенные ботом созвоны и задачи появятся здесь сами</div>
      )}
    </div>
  )
}
