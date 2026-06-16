import { useCallback, useMemo, useRef, useState } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import ruLocale from '@fullcalendar/core/locales/ru'
import { api, getToken } from '../api/client'
import { TodayView } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useDrawer } from '../components/DrawerContext'
import { HintBar } from '../components/HintBar'

/* Календарь v3 — FullCalendar (как Google Calendar): виды месяц/неделя/3 дня/день,
   перетаскивание событий мышью → перенос бронируется на бэке (созвон и задача),
   обновляется везде. Клик по событию открывает карточку лида. Источник — /today. */

const COLORS = {
  call: '#7c6cff',   // созвон — акцент
  bot: '#3bb4a0',    // задача бота
  task: '#c9a23b',   // задача человека
  overdue: '#e0524f', // просрочено
}

function addMin(iso: string, min: number) {
  return new Date(new Date(iso).getTime() + min * 60000).toISOString()
}

export function Calendar() {
  const [view, setView] = useState<TodayView | null>(null)
  const [copied, setCopied] = useState(false)
  const [note, setNote] = useState('')
  const { openConversation } = useDrawer()
  const calRef = useRef<FullCalendar | null>(null)

  const subscribeUrl = `${location.origin}/calendar.ics?token=${encodeURIComponent(getToken() || '')}`
  const copySubscribe = () => {
    navigator.clipboard?.writeText(subscribeUrl)
    setCopied(true); setTimeout(() => setCopied(false), 3000)
  }

  const refresh = useCallback(async () => {
    try { setView(await api.get<TodayView>('/today')) } catch { /* */ }
  }, [])
  usePolling(refresh, 30000)

  const now = Date.now()
  const events = useMemo(() => {
    if (!view) return []
    const evs: any[] = []
    view.calls.forEach(c => {
      if (!c.call_at) return
      evs.push({
        id: 'call-' + c.conversation_id,
        title: `📞 ${c.customer.name || c.customer.email || 'Лид'}${c.medium ? ' · ' + c.medium : ''}`,
        start: c.call_at, end: addMin(c.call_at, 30),
        backgroundColor: COLORS.call, borderColor: COLORS.call,
        extendedProps: { kind: 'call', conv: c.conversation_id },
      })
    })
    const tasks = [...view.overdue, ...view.today, ...view.upcoming]
    tasks.forEach(t => {
      if (!t.due_at) return
      const isBot = t.executor === 'bot'
      const overdue = new Date(t.due_at).getTime() < now
      const col = overdue ? COLORS.overdue : (isBot ? COLORS.bot : COLORS.task)
      evs.push({
        id: 'task-' + t.id,
        title: `${isBot ? '🤖' : '📋'} ${t.customer.name || 'Лид'}: ${(t.text || '').replace(/\s+/g, ' ').trim().slice(0, 44)}`,
        start: t.due_at, end: addMin(t.due_at, 30),
        backgroundColor: col, borderColor: col,
        extendedProps: { kind: isBot ? 'bot' : 'task', conv: t.conversation_id, actionId: t.id },
      })
    })
    return evs
  }, [view, now])

  // Перетащил/растянул событие → переносим на бэке. Ошибка → откат на место.
  const onMove = async (info: any) => {
    const p = info.event.extendedProps
    const startISO: string | undefined = info.event.start?.toISOString()
    if (!startISO) { info.revert(); return }
    try {
      if (p.kind === 'call') {
        if (!p.conv) { info.revert(); return }
        await api.post(`/conversations/${p.conv}/call`, { action: 'reschedule', time: startISO })
        setNote('📞 Созвон перенесён — напоминания обновлены')
      } else {
        await api.post(`/scheduled-actions/${p.actionId}/reschedule`, { due_at: startISO })
        setNote('📋 Задача перенесена')
      }
      setTimeout(() => setNote(''), 3000)
      await refresh()
    } catch (e: any) {
      info.revert()
      setNote(`Не удалось перенести: ${e?.detail ?? e?.message ?? 'ошибка'}`)
      setTimeout(() => setNote(''), 4000)
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Календарь</h1>
        <span className="sub">созвоны и задачи · перетащи событие мышью, чтобы перенести — изменится везде</span>
      </div>
      <HintBar id="calendar" icon="📅">
        Как Google-календарь: переключай <b>месяц / неделю / 3 дня / день</b> справа сверху.
        <b> Перетащи событие</b> на другое время — созвон/задача перенесётся и напоминания обновятся.
        Клик по событию — карточка лида. <b>«📲 Подписаться»</b> — те же события в твоём телефоне.
      </HintBar>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
        <button className="btn sm primary" onClick={copySubscribe}>
          {copied ? '✅ Ссылка скопирована' : '📲 Подписаться в телефоне'}
        </button>
        <span style={{ fontSize: 11.5, display: 'inline-flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ color: COLORS.call }}>● созвон</span>
          <span style={{ color: COLORS.bot }}>● задача бота</span>
          <span style={{ color: COLORS.task }}>● задача человека</span>
          <span style={{ color: COLORS.overdue }}>● просрочено</span>
        </span>
        {note && <span className="chip accent" style={{ marginLeft: 'auto' }}>{note}</span>}
      </div>

      <div className="card" style={{ padding: 12 }}>
        <FullCalendar
          ref={calRef as any}
          plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
          initialView="timeGridWeek"
          locale={ruLocale}
          firstDay={1}
          nowIndicator
          headerToolbar={{
            left: 'prev,next today',
            center: 'title',
            right: 'dayGridMonth,timeGridWeek,timeGridThreeDay,timeGridDay',
          }}
          views={{
            timeGridThreeDay: { type: 'timeGrid', duration: { days: 3 }, buttonText: '3 дня' },
          }}
          buttonText={{ today: 'сегодня', month: 'месяц', week: 'неделя', day: 'день' }}
          events={events}
          editable
          eventStartEditable
          eventDurationEditable={false}
          eventDrop={onMove}
          eventResize={onMove}
          eventClick={(info) => { const c = info.event.extendedProps.conv; if (c) openConversation(c) }}
          slotMinTime="07:00:00"
          slotMaxTime="22:00:00"
          allDaySlot={false}
          expandRows
          height="calc(100vh - 230px)"
          dayMaxEvents
          eventTimeFormat={{ hour: '2-digit', minute: '2-digit', hour12: false }}
        />
      </div>
    </div>
  )
}
