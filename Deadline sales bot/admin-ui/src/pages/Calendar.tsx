import { useState } from 'react'
import { api, getToken } from '../api/client'
import { TodayView } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useDrawer } from '../components/DrawerContext'
import { CHANNEL_META, fmtTime } from '../lib'
import { HintBar } from '../components/HintBar'

/* Календарь v2 — «повестка» (agenda): события сгруппированы по дням сверху вниз,
   каждое во всю ширину (текст не режется), просроченные задачи закреплены сверху.
   Источник — /today (созвоны + задачи). Клик по событию открывает карточку лида
   (там перенос/отмена созвона). Кнопка «Подписаться» — ICS-фид в телефон. */

type Ev = {
  kind: 'call' | 'bot' | 'task'
  time: string | null
  title: string
  detail: string
  conv: string | null
  ch?: string
}

const KIND_META: Record<Ev['kind'], { icon: string; tone: string }> = {
  call: { icon: '📞', tone: 'var(--accent)' },
  bot: { icon: '🤖', tone: '#3bb4a0' },
  task: { icon: '📋', tone: '#c9a23b' },
}
const DOWS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб']

function dayKey(d: Date) {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}
function relLabel(d: Date, now: Date) {
  const diff = Math.round((+new Date(d.getFullYear(), d.getMonth(), d.getDate())
    - +new Date(now.getFullYear(), now.getMonth(), now.getDate())) / 864e5)
  const base = `${DOWS[d.getDay()]} ${d.getDate()}.${String(d.getMonth() + 1).padStart(2, '0')}`
  if (diff === 0) return `Сегодня · ${base}`
  if (diff === 1) return `Завтра · ${base}`
  return base
}

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

  const now = new Date()
  const todayStart = +new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const overdue: Ev[] = []
  const byDay = new Map<string, { date: Date; items: Ev[] }>()

  const place = (iso: string | null, ev: Ev) => {
    if (!iso) return
    const d = new Date(iso)
    const dStart = +new Date(d.getFullYear(), d.getMonth(), d.getDate())
    if (dStart < todayStart) { overdue.push({ ...ev, time: iso }); return }
    if (dStart > todayStart + 13 * 864e5) return // окно 14 дней
    const k = dayKey(d)
    if (!byDay.has(k)) byDay.set(k, { date: d, items: [] })
    byDay.get(k)!.items.push({ ...ev, time: iso })
  }

  if (view) {
    view.calls.forEach(c => place(c.call_at, {
      kind: 'call',
      title: c.customer.name || c.customer.email || 'Лид',
      detail: c.medium ? `созвон · ${c.medium}` : 'созвон',
      conv: c.conversation_id, ch: c.channel,
    }))
    const asTask = (t: any): Ev => ({
      kind: t.executor === 'bot' ? 'bot' : 'task',
      title: t.customer.name || 'Лид',
      detail: (t.text || '').replace(/\s+/g, ' ').trim(),
      conv: t.conversation_id, ch: t.channel,
    })
    view.overdue.forEach(t => place(t.due_at, asTask(t)))
    view.today.forEach(t => place(t.due_at, asTask(t)))
    view.upcoming.forEach(t => place(t.due_at, asTask(t)))
  }

  const days = [...byDay.values()].sort((a, b) => +a.date - +b.date)
  days.forEach(d => d.items.sort((a, b) => (a.time || '').localeCompare(b.time || '')))
  overdue.sort((a, b) => (a.time || '').localeCompare(b.time || ''))

  const callCount = (view?.calls.length) || 0
  const taskCount = days.reduce((n, d) => n + d.items.filter(i => i.kind !== 'call').length, 0)
  const isEmpty = view && !overdue.length && !days.length

  const Row = (it: Ev, k: number) => {
    const m = KIND_META[it.kind]
    return (
      <div key={k}
           onClick={() => it.conv && openConversation(it.conv)}
           style={{
             display: 'flex', alignItems: 'flex-start', gap: 10,
             padding: '9px 11px', borderRadius: 9,
             cursor: it.conv ? 'pointer' : 'default',
             background: 'var(--panel)',
             border: '1px solid var(--border)',
             borderLeft: `3px solid ${m.tone}`,
           }}>
        <div style={{
          minWidth: 46, fontSize: 13, fontWeight: 700, color: 'var(--text)',
          fontVariantNumeric: 'tabular-nums', paddingTop: 1,
        }}>{fmtTime(it.time) || '—'}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
            <span style={{ marginRight: 6 }}>{m.icon}</span>{it.title}
            {it.ch && <span style={{ marginLeft: 6 }}>{CHANNEL_META[it.ch]?.icon ?? ''}</span>}
          </div>
          {it.detail && (
            <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 2, lineHeight: 1.4 }}>
              {it.detail.length > 140 ? it.detail.slice(0, 140) + '…' : it.detail}
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Календарь</h1>
        <span className="sub">созвоны и дедлайны на 2 недели · бот сам бронирует время и напоминает за день и за час</span>
      </div>
      <HintBar id="calendar" icon="📅">
        Повестка по дням: созвоны, которые бот назначил с лидами, и задачи с дедлайном.
        Клик по событию открывает карточку лида — там можно <b>перенести/отменить созвон</b> (📞 Созвон).
        Кнопка <b>«📲 Подписаться»</b> добавит созвоны в твой телефон/Google-календарь (обновляются сами).
      </HintBar>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
        <button className="btn sm primary" onClick={copySubscribe}>
          {copied ? '✅ Ссылка скопирована' : '📲 Подписаться в телефоне'}
        </button>
        {view && (
          <span className="faint" style={{ fontSize: 12.5 }}>
            📞 {callCount} {callCount === 1 ? 'созвон' : 'созвонов'} · 📋 {taskCount} {taskCount === 1 ? 'задача' : 'задач'} на 2 недели
            {overdue.length > 0 && <span style={{ color: '#e0524f', fontWeight: 600 }}> · ⚠️ {overdue.length} просрочено</span>}
          </span>
        )}
      </div>

      {overdue.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: '#e0524f', marginBottom: 8 }}>
            ⚠️ Просрочено ({overdue.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            {overdue.map(Row)}
          </div>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        {days.map((d, di) => (
          <div key={di}>
            <div style={{
              fontSize: 12.5, fontWeight: 700, marginBottom: 8,
              color: di === 0 && dayKey(d.date) === dayKey(now) ? 'var(--accent)' : 'var(--text-dim)',
            }}>
              {relLabel(d.date, now)}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              {d.items.map(Row)}
            </div>
          </div>
        ))}
      </div>

      {isEmpty && (
        <div className="empty">Пока пусто — назначенные ботом созвоны и задачи появятся здесь сами</div>
      )}
    </div>
  )
}
