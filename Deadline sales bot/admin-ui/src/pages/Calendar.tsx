import { useRef, useState } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import ruLocale from '@fullcalendar/core/locales/ru'
import { api, getToken } from '../api/client'
import { useDrawer } from '../components/DrawerContext'
import { HintBar } from '../components/HintBar'
import { getBizTzOffset } from '../lib'

/* Календарь v3 — FullCalendar (как Google Calendar): виды месяц/неделя/3 дня/день,
   перетаскивание событий мышью → перенос бронируется на бэке (созвон и задача).
   События подгружаются за видимый диапазон через /calendar-events. Клик — карточка. */

const COLORS: Record<string, string> = {
  call: '#7c6cff', bot: '#3bb4a0', task: '#c9a23b', reminder: '#6b7280', overdue: '#e0524f',
}
// Фильтры показа событий по типу (можно скрыть шум — оставить только реальные созвоны).
const KINDS: { k: string; label: string }[] = [
  { k: 'call', label: '📞 Созвоны' },
  { k: 'task', label: '📋 Задачи менеджера' },
  { k: 'bot', label: '🤖 Задачи бота' },
  { k: 'reminder', label: '⏰ Напоминания' },
]

type ApiEvent = {
  id: string; kind: 'call' | 'bot' | 'task' | 'reminder'; title: string
  start: string; conversation_id: string | null; action_id: string | null
}

function addMin(iso: string, min: number) {
  return new Date(new Date(iso).getTime() + min * 60000).toISOString()
}

// Рендерим в поясе БИЗНЕСА независимо от пояса браузера: FullCalendar с timeZone="UTC"
// показывает время как есть, поэтому кормим его «настенным» временем бизнеса (реальный UTC +
// смещение, помеченный Z), а на перетаскивании конвертируем обратно в реальный UTC.
const _calOff = () => getBizTzOffset() * 3600_000
const toBizWall = (utcIso: string) => new Date(new Date(utcIso).getTime() + _calOff()).toISOString()
const fromBizWall = (d: Date) => new Date(d.getTime() - _calOff()).toISOString()

export function Calendar() {
  const [copied, setCopied] = useState(false)
  const [note, setNote] = useState('')
  const [showSub, setShowSub] = useState(false)  // попап-инструкция «Подписаться в телефоне»
  const { openConversation } = useDrawer()
  const calRef = useRef<FullCalendar | null>(null)

  const [visible, setVisible] = useState<Record<string, boolean>>(() => {
    try { const s = localStorage.getItem('cal_filters'); if (s) return JSON.parse(s) } catch { /* */ }
    // Календарь = ТОЛЬКО события для человека: созвоны + напоминания перед ними.
    // Обычные задачи (написать/дожать) сюда не сваливаем — они в Задачнике (просьба владельца).
    return { call: true, reminder: true, task: false, bot: false }
  })
  const visibleRef = useRef(visible)
  visibleRef.current = visible
  const toggle = (k: string) => setVisible(v => {
    const nv = { ...v, [k]: !v[k] }
    try { localStorage.setItem('cal_filters', JSON.stringify(nv)) } catch { /* */ }
    setTimeout(() => calRef.current?.getApi().refetchEvents(), 0)
    return nv
  })

  const subscribeUrl = `${location.origin}/calendar.ics?token=${encodeURIComponent(getToken() || '')}`
  const copySubscribe = () => {
    navigator.clipboard?.writeText(subscribeUrl)
    setCopied(true); setTimeout(() => setCopied(false), 3000)
  }

  // Источник событий — подгружаем за видимый период (FullCalendar зовёт при смене вида/даты).
  const fetchEvents = async (info: { startStr: string; endStr: string }) => {
    const r = await api.get<{ events: ApiEvent[] }>(
      `/calendar-events?start=${encodeURIComponent(info.startStr)}&end=${encodeURIComponent(info.endStr)}`,
    )
    const now = Date.now()
    return (r.events || []).filter(e => visibleRef.current[e.kind] !== false).map(e => {
      const overdue = e.kind !== 'call' && new Date(e.start).getTime() < now
      const col = overdue ? COLORS.overdue : COLORS[e.kind]
      const startWall = toBizWall(e.start)
      return {
        id: e.id, title: e.title, start: startWall, end: addMin(startWall, 30),
        backgroundColor: col, borderColor: col,
        extendedProps: { kind: e.kind, conv: e.conversation_id, actionId: e.action_id },
      }
    })
  }

  // Перетащил событие → переносим на бэке. Ошибка → откат на место.
  const onMove = async (mv: any) => {
    const p = mv.event.extendedProps
    const startISO: string | undefined = mv.event.start ? fromBizWall(mv.event.start) : undefined
    if (!startISO) { mv.revert(); return }
    try {
      if (p.kind === 'call') {
        if (!p.conv) { mv.revert(); return }
        await api.post(`/conversations/${p.conv}/call`, { action: 'reschedule', time: startISO })
        setNote('📞 Созвон перенесён — напоминания обновлены')
      } else {
        await api.post(`/scheduled-actions/${p.actionId}/reschedule`, { due_at: startISO })
        setNote('📋 Задача перенесена')
      }
      setTimeout(() => setNote(''), 3000)
      calRef.current?.getApi().refetchEvents()
    } catch (e: any) {
      mv.revert()
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
        <b> Перетащи событие</b> на другое время — созвон/задача перенесётся, напоминания обновятся.
        Клик по событию — карточка лида. <b>«📲 Подписаться»</b> — те же события в твоём телефоне.
      </HintBar>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
        <button className="btn sm primary" onClick={() => setShowSub(true)}>
          📲 Подписаться в телефоне
        </button>
        <span style={{ fontSize: 11.5, display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          {KINDS.map(({ k, label }) => {
            const on = visible[k] !== false
            return (
              <button key={k} onClick={() => toggle(k)} title={on ? 'скрыть' : 'показать'}
                style={{
                  cursor: 'pointer', fontSize: 11.5, padding: '3px 9px', borderRadius: 20,
                  border: `1px solid ${on ? COLORS[k] : 'var(--border)'}`,
                  background: on ? COLORS[k] + '22' : 'transparent',
                  color: on ? 'var(--text)' : 'var(--text-faint)',
                  opacity: on ? 1 : 0.55, textDecoration: on ? 'none' : 'line-through',
                }}>
                {label}
              </button>
            )
          })}
        </span>
        {note && <span className="chip accent" style={{ marginLeft: 'auto' }}>{note}</span>}
      </div>

      <div className="card" style={{ padding: 12 }}>
        <FullCalendar
          ref={calRef as any}
          plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
          initialView="timeGridWeek"
          locale={ruLocale}
          timeZone="UTC"
          firstDay={1}
          nowIndicator
          headerToolbar={{
            left: 'prev,next today',
            center: 'title',
            right: 'dayGridMonth,timeGridWeek,timeGridThreeDay,timeGridDay',
          }}
          views={{ timeGridThreeDay: { type: 'timeGrid', duration: { days: 3 }, buttonText: '3 дня' } }}
          buttonText={{ today: 'сегодня', month: 'месяц', week: 'неделя', day: 'день' }}
          events={fetchEvents}
          editable
          eventStartEditable
          eventDurationEditable={false}
          eventDrop={onMove}
          eventClick={(info) => { const c = info.event.extendedProps.conv; if (c) openConversation(c) }}
          slotMinTime="07:00:00"
          slotMaxTime="22:00:00"
          allDaySlot={false}
          expandRows
          height="calc(100vh - 230px)"
          dayMaxEvents
          eventTimeFormat={{ hour: '2-digit', minute: '2-digit', hour12: false }}
          slotLabelFormat={{ hour: '2-digit', minute: '2-digit', hour12: false }}
          slotLabelInterval="01:00:00"
          slotDuration="01:00:00"
        />
      </div>

      {showSub && (
        <>
          <div className="drawer-overlay" onClick={() => setShowSub(false)} />
          <div className="card" style={{
            position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
            zIndex: 50, width: 'min(560px, 94vw)', maxHeight: '90vh', overflowY: 'auto',
            display: 'flex', flexDirection: 'column', gap: 14,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <h2 style={{ margin: 0, fontSize: 17 }}>📲 Календарь в телефоне</h2>
              <button className="btn sm ghost" onClick={() => setShowSub(false)}>✕</button>
            </div>

            <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: 'var(--text-faint)' }}>
              Добавь ссылку <b>один раз</b> — и все события из панели (📞 созвоны,
              📋 задачи, ⏰ напоминания) появятся в календаре телефона и будут
              обновляться сами. Перенёс созвон в панели — он переедет и в телефоне.
              Это <b>только просмотр</b>: создаёшь и двигаешь события здесь, в панели.
            </p>

            {/* Ссылка-подписка + копирование */}
            <div style={{ display: 'flex', gap: 8, alignItems: 'stretch' }}>
              <input readOnly value={subscribeUrl} onFocus={e => e.currentTarget.select()}
                style={{
                  flex: 1, fontSize: 12, fontFamily: 'monospace', padding: '8px 10px',
                  borderRadius: 8, border: '1px solid var(--border)',
                  background: 'var(--bg)', color: 'var(--text)', minWidth: 0,
                }} />
              <button className="btn sm primary" style={{ whiteSpace: 'nowrap' }} onClick={copySubscribe}>
                {copied ? '✅ Скопировано' : 'Копировать'}
              </button>
            </div>

            {/* iPhone */}
            <div>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>🍏 iPhone / iPad (Apple Календарь)</div>
              <ol style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7, color: 'var(--text)' }}>
                <li>Нажми <b>«Копировать»</b> выше</li>
                <li><b>Настройки</b> → <b>Календарь</b> → <b>Учётные записи</b></li>
                <li><b>Новая учётная запись</b> → <b>Другое</b></li>
                <li><b>Подписной календарь</b></li>
                <li>Вставь ссылку → <b>Далее</b> → <b>Сохранить</b></li>
              </ol>
            </div>

            {/* Android / Google */}
            <div>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>🤖 Android / Google Календарь</div>
              <p style={{ margin: '0 0 4px', fontSize: 11.5, color: 'var(--text-faint)' }}>
                Google не даёт добавить ссылку с телефона — нужен компьютер (один раз):
              </p>
              <ol style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7, color: 'var(--text)' }}>
                <li>На компьютере открой <b>calendar.google.com</b></li>
                <li>Слева <b>«Другие календари»</b> → <b>«＋»</b> → <b>«Подписаться по URL»</b></li>
                <li>Вставь ссылку → <b>«Добавить календарь»</b></li>
                <li>В Google Календаре на телефоне он появится сам</li>
              </ol>
            </div>

            <div style={{ fontSize: 11.5, color: 'var(--text-faint)', lineHeight: 1.5,
              borderTop: '1px solid var(--border)', paddingTop: 10 }}>
              🔒 В ссылке твой секретный ключ — <b>никому не пересылай</b> (у кого ссылка,
              тот видит твой календарь).<br />
              ⏱ Телефон тянет обновления <b>раз в несколько часов</b> (это поведение iOS/Google,
              не панели) — мгновенной синхронизации тут не бывает.
            </div>

            <button className="btn primary" onClick={() => setShowSub(false)}>Готово</button>
          </div>
        </>
      )}
    </div>
  )
}
