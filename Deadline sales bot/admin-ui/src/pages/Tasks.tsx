import { useState } from 'react'
import { api } from '../api/client'
import { ScheduledActionItem } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useDrawer } from '../components/DrawerContext'
import { CHANNEL_META, fmtTime, fmtAgo } from '../lib'
import { HintBar } from '../components/HintBar'
import { Help } from '../components/Help'

/* Задачи. «Мой день» = CRM-доска (как amoCRM): у каждого активного лида должна
   быть следующая задача; лид без задачи выводится отдельно. Приоритет по
   температуре+стадии. Видно, что бот делает сам, а что — администратор.
   «Все задачи» = полный список с фильтрами. */

const TYPE_LABELS: Record<string, string> = {
  followup_message: '📨 Написать лиду', warming_touch: '🔥 Прогрев',
  operator_callback: '👤 Связаться / сделать', escalation: '🚨 Эскалация',
}
const TEMP: Record<string, { e: string; c: string }> = {
  ready: { e: '✅', c: '#3bb4a0' }, hot: { e: '🔥', c: '#e0524f' },
  warm: { e: '🌤', c: '#c9a23b' }, cold: { e: '❄️', c: '#5b9bd5' },
}

type BoardTask = {
  id: string; who: 'bot' | 'human'; can_bot: boolean; action_type: string
  text: string; due_at: string | null; conversation_id: string | null
  name: string; stage: string | null; stage_label: string
  temperature: string | null; channel: string; wa_autonomous: boolean
}
type NoTaskLead = {
  conversation_id: string; name: string; stage: string | null; stage_label: string
  temperature: string | null; channel: string; last_message_at: string | null
  next_action: string; bot_can: boolean; wa_autonomous: boolean
  mode: string | null; kind: string | null; draft: string; reason: string; analyzed: boolean
}
// Режимы умного шага (см. services/next_action.py).
const MODE: Record<string, { e: string; t: string; c?: string }> = {
  bot_auto: { e: '🤖', t: 'бот сам' },
  needs_approval: { e: '⏳', t: 'на одобрение', c: 'var(--accent)' },
  human: { e: '👤', t: 'за тобой' },
  reengage: { e: '🔁', t: 'дожать' },
  wait: { e: '⏸', t: 'ждём лида' },
  unclear: { e: '🆘', t: 'нужна помощь', c: '#e0524f' },
}
// Лид как единица умного задачника (зона = что с ним делать прямо сейчас).
type Lead = {
  conversation_id: string; name: string; stage: string | null; stage_label: string
  temperature: string | null; channel: string; last_message_at: string | null
  silent_hours: number; mode: string | null; kind: string | null; label: string
  draft: string; reason: string; wa_autonomous: boolean; bot_capable: boolean
  bot_status: string; bot_status_label: string
  has_human_task: boolean; task_id: string | null; task_due: string | null; task_text: string | null
  bot_next_action_type: string | null; bot_next_due: string | null; bot_next_text: string | null
}
type ZoneId = 'approve_now' | 'your_turn' | 'bot_leading' | 'stuck' | 'waiting'
// Упавшая bot-задача (дожим/напоминание не доставлено) — сигнал «нужен человек».
type FailedItem = {
  id: string; conversation_id: string | null; name: string; channel: string
  text: string; action_type: string; stage_label: string; temperature: string | null
  attempts: number
}
type Board = {
  summary: { overdue: number; today: number; no_task: number; bot: number; human: number
    approve_now: number; your_turn: number; bot_leading: number; stuck: number
    delivery_failed: number }
  buckets: Record<'overdue' | 'today' | 'tomorrow' | 'week' | 'later', BoardTask[]>
  no_task_leads: NoTaskLead[]
  zones: { approve_now: Lead[]; your_turn: Lead[]; bot_leading: Lead[]; waiting: Lead[] }
  stuck: Lead[]
  delivery_failed: FailedItem[]
}

const StageChip = ({ s }: { s: string }) =>
  s ? <span className="chip" style={{ fontSize: 10.5 }}>{s}</span> : null
const TempDot = ({ t }: { t: string | null }) => {
  const m = TEMP[(t || '').toLowerCase()]
  return m ? <span title={t || ''} style={{ fontSize: 12 }}>{m.e}</span> : null
}

export function Tasks() {
  const [tab, setTab] = useState<'day' | 'all'>('day')
  const [toast, setToast] = useState<string | null>(null)
  const showToast = (t: string) => { setToast(t); setTimeout(() => setToast(null), 4000) }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Задачи</h1>
        <div style={{ display: 'flex', gap: 4, background: 'var(--panel)', borderRadius: 8, padding: 3 }}>
          <button className={`btn sm ${tab === 'day' ? 'primary' : 'ghost'}`} onClick={() => setTab('day')}>Приоритет сегодня</button>
          <button className={`btn sm ${tab === 'all' ? 'primary' : 'ghost'}`} onClick={() => setTab('all')}>Все задачи</button>
        </div>
      </div>
      <HintBar id="tasks" icon="⏰">
        Задачи по дням: <b>просрочено</b> и <b>сегодня</b> — в первую очередь. <b>«Без задачи»</b>
        (бледно-красным) — клиенты без следующего шага, их легко потерять: назначь задачу или
        передай боту. 🤖 — бот сделает сам, 👤 — за тобой. «Будущее» свёрнуто внизу.
      </HintBar>
      {tab === 'day' ? <CrmBoard showToast={showToast} /> : <AllTasks showToast={showToast} />}
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}

/* ---------- CRM-доска ---------- */

function CrmBoard({ showToast }: { showToast: (t: string) => void }) {
  const [board, setBoard] = useState<Board | null>(null)
  const [busy, setBusy] = useState('')
  // Фильтр-фокус: клик по счётчику-зоне вверху → показать только эту зону.
  const [filterWho, setFilterWho] = useState<'all' | 'bot' | 'human' | 'approve'>('all')
  const [daysOpen, setDaysOpen] = useState(false)  // будущие дни (завтра/неделя/позже) свёрнуты
  const [stageFilter, setStageFilter] = useState<string | null>(null)  // фильтр по СТАТУСУ (стадии воронки)
  const [rsId, setRsId] = useState('')    // id задачи с открытым пикером переноса
  const [rsVal, setRsVal] = useState('')  // значение datetime-local
  const [ntConvId, setNtConvId] = useState('')  // диалог с открытой формой «создать задачу»
  const [ntVal, setNtVal] = useState('')        // дата/время новой задачи
  const [ntText, setNtText] = useState('')      // текст новой задачи
  const { openConversation } = useDrawer()

  const load = async () => {
    try { setBoard(await api.get<Board>('/task-board')) } catch { /* */ }
  }
  usePolling(load, 20000)

  const act = async (fn: () => Promise<any>, ok: string) => {
    setBusy('1')
    try { await fn(); showToast(ok); await load() }
    catch (e: any) { showToast(`Ошибка: ${e?.detail ?? e?.message ?? 'ошибка'}`) }
    finally { setBusy('') }
  }
  const done = (id: string) => act(() => api.post(`/scheduled-actions/${id}/done`), '✅ Сделано')
  const cancel = (id: string) => act(() => api.post(`/scheduled-actions/${id}/cancel`), 'Отменено')
  const botLead = (id: string) => act(() => api.post(`/conversations/${id}/wa-autonomous`, { on: true }), '🤖 Бот ведёт диалог')
  const sweep = () => act(async () => {
    const r = await api.post<any>('/cron/sweep'); showToast(`Крон: бот отправил ${r.followups?.sent ?? 0}`)
  }, 'Крон прогнан')
  const generate = () => act(async () => {
    const r = await api.post<any>('/task-board/generate', { limit: 10 })
    showToast(`🤖 Разобрал ${r.processed ?? 0} лидов`)
  }, 'Готово')
  // Перенос задачи на ПРОИЗВОЛЬНУЮ дату/время (пикер). Кейс владельца: «написать
  // сегодня» → клиент попросил «через неделю» → переносим. Бэкенд /reschedule берёт ISO.
  const rescheduleTo = (id: string, iso: string) => act(async () => {
    await api.post(`/scheduled-actions/${id}/reschedule`, { due_at: iso })
    setRsId(''); setRsVal('')
  }, '↪ Перенесено')
  // Создать задачу человеку с датой (использует существующий POST /tasks, executor=human).
  const createTask = (convId: string, iso: string, text: string) => act(async () => {
    await api.post('/tasks', { conversation_id: convId, text, due_at: iso, executor: 'human' })
    setNtConvId(''); setNtVal(''); setNtText('')
  }, '✅ Задача создана')
  const markLostTask = (convId: string) =>
    act(() => api.post(`/conversations/${convId}/stage`, { to_stage: 'lost', lost_reason: 'delayed' }), '✗ Не сложилось')
  // Одобрить готовый черновик бота (зона «Одобри сейчас»): отправить / отклонить.
  const approveDraft = (convId: string) => act(() => api.post(`/conversations/${convId}/wa-draft`, { action: 'send' }), '✅ Ответ отправлен')
  const rejectDraft = (convId: string) => act(() => api.post(`/conversations/${convId}/wa-draft`, { action: 'reject' }), '🚫 Черновик отклонён')
  // Вернуть автопилотного лида на ручное одобрение (зона «Бот ведёт»).
  const toApproval = (convId: string) => act(() => api.post(`/conversations/${convId}/wa-autonomous`, { on: false }), '⏸ Вернул на одобрение')

  // Инлайн-пикер переноса задачи на дату/время (раскрывается кнопкой «↪ Перенести»).
  const Reschedule = ({ taskId }: { taskId: string }) =>
    rsId === taskId ? (
      <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }} onClick={e => e.stopPropagation()}>
        <input type="datetime-local" value={rsVal} autoFocus onChange={e => setRsVal(e.target.value)} style={{ fontSize: 11 }} />
        <button className="btn sm primary" disabled={!rsVal || !!busy}
                onClick={() => rescheduleTo(taskId, new Date(rsVal).toISOString())}>OK</button>
        <button className="btn sm ghost" onClick={() => { setRsId(''); setRsVal('') }}>✕</button>
      </span>
    ) : (
      <button className="btn sm ghost" disabled={!!busy} title="Перенести задачу на дату/время"
              onClick={() => { setRsId(taskId); setRsVal('') }}>↪ Перенести</button>
    )

  // Создать задачу человеку с датой прямо из доски (без внутр. useState — стабильно).
  const NewTask = ({ convId }: { convId: string }) =>
    ntConvId === convId ? (
      <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center', flexWrap: 'wrap' }} onClick={e => e.stopPropagation()}>
        <input type="text" value={ntText} placeholder="что сделать…" autoFocus
               onChange={e => setNtText(e.target.value)} style={{ fontSize: 11, width: 150 }} />
        <input type="datetime-local" value={ntVal} onChange={e => setNtVal(e.target.value)} style={{ fontSize: 11 }} />
        <button className="btn sm primary" disabled={!ntVal || !ntText.trim() || !!busy}
                onClick={() => createTask(convId, new Date(ntVal).toISOString(), ntText.trim())}>OK</button>
        <button className="btn sm ghost" onClick={() => { setNtConvId(''); setNtVal(''); setNtText('') }}>✕</button>
      </span>
    ) : (
      <button className="btn sm ghost" disabled={!!busy} title="Создать задачу человеку с датой"
              onClick={() => { setNtConvId(convId); setNtVal(''); setNtText('') }}>➕ Задача</button>
    )

  if (!board) return <div className="empty"><span className="spin" /> Загрузка…</div>

  // Одна карточка лида. Кнопки зависят от зоны.
  const LeadCard = (l: Lead, zone: ZoneId) => (
    <div className={`conv-row${l.wa_autonomous ? ' autonomous' : ''}`} key={l.conversation_id} style={{ cursor: 'default' }}>
      <div className="c-main" style={{ cursor: 'pointer' }} onClick={() => openConversation(l.conversation_id)}>
        <div className="c-name" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <TempDot t={l.temperature} />{l.name}
          <StageChip s={l.stage_label} />
          <span className="chip" style={{ fontSize: 10 }} title="Статус автоматизации по лиду">{l.bot_status_label}</span>
          <span className="faint" style={{ fontWeight: 400 }}>{CHANNEL_META[l.channel]?.icon}</span>
        </div>
        <div className="c-preview">
          {zone === 'approve_now'
            ? (l.draft
                ? <i>✍️ «{l.draft.slice(0, 130)}{l.draft.length > 130 ? '…' : ''}»</i>
                : <i>🤖 бот подготовил ответ — нажми ✅ или открой</i>)
            : zone === 'bot_leading'
              ? <>🤖 ведёт сам{l.last_message_at ? ` · последнее ${fmtAgo(l.last_message_at)} назад` : ''}
                  {l.bot_next_due && <div className="faint" style={{ fontSize: 10.5, marginTop: 2 }}>
                    📨 напишет {fmtTime(l.bot_next_due)}{l.bot_next_text ? `: «${l.bot_next_text.slice(0, 60)}${l.bot_next_text.length > 60 ? '…' : ''}»` : ''}
                  </div>}</>
              : (l.label || l.task_text || (l.last_message_at ? `молчит ${Math.round(l.silent_hours)}ч` : '—'))}
        </div>
        {l.reason && <div className="faint" style={{ fontSize: 11 }}>{l.reason}</div>}
      </div>
      <div className="c-meta">
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {zone === 'approve_now' && <>
            <button className="btn sm primary" disabled={!!busy} title="Отправить ответ бота лиду" onClick={() => approveDraft(l.conversation_id)}>✅ Одобрить</button>
            <button className="btn sm ghost" disabled={!!busy} title="Изменить перед отправкой" onClick={() => openConversation(l.conversation_id)}>✏️</button>
            <button className="btn sm ghost" disabled={!!busy} title="Отклонить черновик" onClick={() => rejectDraft(l.conversation_id)}>🚫</button>
          </>}
          {zone === 'your_turn' && <>
            {l.task_id && <button className="btn sm" disabled={!!busy} onClick={() => done(l.task_id!)}>✓ Сделано</button>}
            {l.task_id && <Reschedule taskId={l.task_id} />}
            {l.bot_capable && !l.wa_autonomous && <button className="btn sm" disabled={!!busy} onClick={() => botLead(l.conversation_id)}>🤖 Боту</button>}
            <NewTask convId={l.conversation_id} />
            <button className="btn sm ghost" onClick={() => openConversation(l.conversation_id)}>Открыть</button>
          </>}
          {zone === 'bot_leading' && <>
            <button className="btn sm ghost" disabled={!!busy} title="Вернуть на ручное одобрение" onClick={() => toApproval(l.conversation_id)}>⏸ На одобрение</button>
            <NewTask convId={l.conversation_id} />
            <button className="btn sm ghost" onClick={() => openConversation(l.conversation_id)}>Открыть</button>
          </>}
          {(zone === 'waiting' || zone === 'stuck') && <>
            {l.bot_capable && !l.wa_autonomous && <button className="btn sm" disabled={!!busy} onClick={() => botLead(l.conversation_id)}>🤖 Боту</button>}
            <NewTask convId={l.conversation_id} />
            <button className="btn sm ghost" disabled={!!busy} title="В «Не сложилось»" onClick={() => markLostTask(l.conversation_id)}>✗</button>
            <button className="btn sm ghost" onClick={() => openConversation(l.conversation_id)}>Открыть</button>
          </>}
        </div>
      </div>
    </div>
  )

  const Zone = ({ id, title, hint, items, color, action }: {
    id: ZoneId; title: string; hint?: string; items: Lead[]; color?: string; action?: JSX.Element
  }) => items.length === 0 ? null : (
    <div className="card" style={{ padding: 12, borderColor: color }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13 }}>{title} ({items.length})</b>
        <span style={{ flex: 1 }} />{action}
      </div>
      {hint && <div className="faint" style={{ fontSize: 11.5, margin: '4px 0 8px' }}>{hint}</div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginTop: hint ? 0 : 8 }}>
        {items.map(l => LeadCard(l, id))}
      </div>
    </div>
  )

  // Карточка ЗАДАЧИ (из buckets по дням): что сделать + срок. Бот-задачи бот сделает сам.
  const TaskCard = (t: BoardTask) => {
    const overdue = !!t.due_at && new Date(t.due_at).getTime() < Date.now()
    const isBot = t.who === 'bot'
    return (
      <div className={`conv-row${t.wa_autonomous ? ' autonomous' : ''}`} key={t.id}
           style={overdue ? { borderLeft: '3px solid var(--danger)' } : undefined}>
        <div className="c-main" style={{ cursor: t.conversation_id ? 'pointer' : 'default' }}
             onClick={() => t.conversation_id && openConversation(t.conversation_id)}>
          <div className="c-name" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <TempDot t={t.temperature} />{t.name}
            <span className="faint" style={{ fontWeight: 400 }}>· {t.text || TYPE_LABELS[t.action_type] || 'задача'}</span>
            <StageChip s={t.stage_label} />
            <span className="faint" style={{ fontWeight: 400 }}>{CHANNEL_META[t.channel]?.icon}</span>
          </div>
          <div className="c-preview" style={overdue ? { color: 'var(--danger)' } : undefined}>
            {isBot ? '🤖 бот сделает сам' : ''}{t.due_at ? `${isBot ? ' · ' : ''}${fmtTime(t.due_at)}` : ''}
          </div>
        </div>
        <div className="c-meta">
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            {!isBot && <button className="btn sm" disabled={!!busy} onClick={() => done(t.id)}>✓ Сделано</button>}
            {!isBot && <Reschedule taskId={t.id} />}
            {t.conversation_id && <button className="btn sm ghost" onClick={() => openConversation(t.conversation_id!)}>Открыть</button>}
          </div>
        </div>
      </div>
    )
  }

  // Карточка КЛИЕНТА БЕЗ ЗАДАЧИ (бледно-красным, мягко) — назначь шаг или передай боту.
  const NoTaskCard = (l: NoTaskLead) => (
    <div className="conv-row" key={l.conversation_id}
         style={{ borderLeft: '3px solid var(--danger)', background: 'rgba(224,82,79,0.07)' }}>
      <div className="c-main" style={{ cursor: 'pointer' }} onClick={() => openConversation(l.conversation_id)}>
        <div className="c-name" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <TempDot t={l.temperature} />{l.name}
          <StageChip s={l.stage_label} />
          <span className="faint" style={{ fontWeight: 400 }}>{CHANNEL_META[l.channel]?.icon}</span>
        </div>
        <div className="c-preview" style={{ color: 'var(--danger)' }}>
          нет следующего шага{l.last_message_at ? ` · молчит ${fmtAgo(l.last_message_at)}` : ''}
        </div>
      </div>
      <div className="c-meta">
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <NewTask convId={l.conversation_id} />
          {l.bot_can && <button className="btn sm" disabled={!!busy} onClick={() => botLead(l.conversation_id)}>🤖 Боту</button>}
          <button className="btn sm ghost" onClick={() => openConversation(l.conversation_id)}>Открыть</button>
        </div>
      </div>
    </div>
  )

  const sm = board.summary
  const z = board.zones
  const b = board.buckets
  // Стадии для фильтра по статусу — из всех задач по дням.
  const stageCounts = new Map<string, { label: string; n: number }>()
  for (const t of [...b.overdue, ...b.today, ...b.tomorrow, ...b.week, ...b.later]) {
    if (!t.stage) continue
    const e = stageCounts.get(t.stage) || { label: t.stage_label || t.stage, n: 0 }
    e.n++; stageCounts.set(t.stage, e)
  }
  const stageList = [...stageCounts.entries()].sort((a, c) => c[1].n - a[1].n)
  const sStage = (st: string | null) => !stageFilter || st === stageFilter
  const sWho = (who: string, wa: boolean) =>
    (filterWho === 'all' || filterWho === 'approve') ? true
      : filterWho === 'bot' ? (who === 'bot' || wa)
        : (who === 'human' && !wa)
  const fT = (items: BoardTask[]) =>
    filterWho === 'approve' ? [] : items.filter(t => sStage(t.stage) && sWho(t.who, t.wa_autonomous))
  const approveIds = new Set(z.approve_now.map(l => l.conversation_id))
  const fApprove = filterWho === 'bot' ? [] : z.approve_now.filter(l => sStage(l.stage))
  const fNoTask = (filterWho === 'approve' || filterWho === 'bot') ? []
    : board.no_task_leads.filter(l => sStage(l.stage) && !approveIds.has(l.conversation_id))

  const overdue = fT(b.overdue), today = fT(b.today)
  const tomorrow = fT(b.tomorrow), week = fT(b.week), later = fT(b.later)
  const futureN = tomorrow.length + week.length + later.length
  const nothing = overdue.length + fApprove.length + today.length + fNoTask.length + futureN === 0

  const whoChip = (id: 'all' | 'bot' | 'human' | 'approve', label: string) => (
    <span className="chip" style={{ cursor: 'pointer', boxShadow: filterWho === id ? '0 0 0 2px var(--accent)' : 'none' }}
          onClick={() => setFilterWho(id)}>{label}</span>
  )
  const Head = ({ title, n, color, hint, action }: { title: string; n: number; color?: string; hint?: string; action?: JSX.Element }) => (
    <div style={{ margin: '2px 0 8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13, color }}>{title} · {n}</b><span style={{ flex: 1 }} />{action}
      </div>
      {hint && <div className="faint" style={{ fontSize: 11.5, marginTop: 2 }}>{hint}</div>}
    </div>
  )
  const colS: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 7 }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="faint" style={{ fontSize: 11.5 }}>кто ведёт:</span>
        {whoChip('all', 'Все')}
        {whoChip('bot', `🤖 Бот ${sm.bot}`)}
        {whoChip('human', '👤 Менеджер')}
        {whoChip('approve', `⏳ Одобрить ${sm.approve_now}`)}
        <span style={{ flex: 1 }} />
        <button className="btn sm" onClick={sweep} disabled={!!busy}>↻ Обновить</button>
        <Help title="Обновить" text="Ручной запуск проверки: бот сразу дожмёт молчунов, разошлёт напоминания и подтянет переписки. Обычно делает это сам каждые ~10 минут." />
      </div>

      {(stageList.length > 1 || stageFilter) && (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="faint" style={{ fontSize: 11.5, marginRight: 2 }}>стадия:</span>
          {stageList.map(([st, info]) => (
            <span key={st} className="chip"
                  style={{ cursor: 'pointer', fontSize: 11.5, boxShadow: stageFilter === st ? '0 0 0 2px var(--accent)' : 'none' }}
                  onClick={() => setStageFilter(stageFilter === st ? null : st)}>{info.label} {info.n}</span>
          ))}
          {stageFilter && <button className="btn sm ghost" onClick={() => setStageFilter(null)}>✕ статус</button>}
        </div>
      )}

      {board.delivery_failed.length > 0 && (
        <div>
          <Head title="📵 Доставка не удалась" n={board.delivery_failed.length} color="var(--danger)"
                hint="Авто-сообщения бота этим лидам не дошли. Открой и ответь вручную." />
          <div style={colS}>
            {board.delivery_failed.map(f => (
              <div className="conv-row" key={f.id}>
                <div className="c-main" style={{ cursor: f.conversation_id ? 'pointer' : 'default' }}
                     onClick={() => f.conversation_id && openConversation(f.conversation_id)}>
                  <div className="c-name" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <TempDot t={f.temperature} />{f.name}<StageChip s={f.stage_label} />
                    <span className="faint" style={{ fontWeight: 400 }}>{CHANNEL_META[f.channel]?.icon}</span>
                  </div>
                  <div className="c-preview"><i>не доставлено ({f.attempts} попыт.): «{(f.text || '').slice(0, 90)}»</i></div>
                </div>
                <div className="c-meta">{f.conversation_id && <button className="btn sm ghost" onClick={() => openConversation(f.conversation_id!)}>Открыть</button>}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {overdue.length > 0 && (
        <div>
          <Head title="⚠️ Просрочено" n={overdue.length} color="var(--danger)"
                hint="Срок прошёл, а шаг не сделан — в первую очередь." />
          <div style={colS}>{overdue.map(TaskCard)}</div>
        </div>
      )}

      {(fApprove.length + today.length) > 0 && (
        <div>
          <Head title="Сегодня" n={fApprove.length + today.length}
                hint="Что нужно сделать сегодня. ⏳ — бот подготовил ответ, нужно твоё «ОК»." />
          <div style={colS}>
            {fApprove.map(l => LeadCard(l, 'approve_now'))}
            {today.map(TaskCard)}
          </div>
        </div>
      )}

      {fNoTask.length > 0 && (
        <div>
          <Head title="🏷 Без задачи" n={fNoTask.length} color="var(--danger)"
                hint="По этим клиентам нет следующего шага — назначь задачу или передай боту, чтобы не потерять."
                action={<button className="btn sm primary" disabled={!!busy} onClick={generate} title="Бот прочитает диалоги без шага и предложит, что делать дальше">🤖 Разобрать ботом</button>} />
          <div style={colS}>{fNoTask.map(NoTaskCard)}</div>
        </div>
      )}

      {futureN > 0 && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }} onClick={() => setDaysOpen(v => !v)}>
            <b style={{ fontSize: 13 }}>{daysOpen ? '▾' : '▸'} Будущее · {futureN}</b>
            <span className="faint" style={{ fontSize: 11.5 }}>завтра, эта неделя и дальше — не срочно</span>
          </div>
          {daysOpen && (
            <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 14 }}>
              {tomorrow.length > 0 && <div><Head title="Завтра" n={tomorrow.length} /><div style={colS}>{tomorrow.map(TaskCard)}</div></div>}
              {week.length > 0 && <div><Head title="Эта неделя" n={week.length} /><div style={colS}>{week.map(TaskCard)}</div></div>}
              {later.length > 0 && <div><Head title="Позже" n={later.length} /><div style={colS}>{later.map(TaskCard)}</div></div>}
            </div>
          )}
        </div>
      )}

      {!stageFilter && filterWho === 'all' && <SleepingPanel showToast={showToast} />}
      {nothing && <div className="empty">Всё под контролем — на сегодня задач, требующих тебя, нет 🎉</div>}
    </div>
  )
}

/* ---------- Все задачи ---------- */

function AllTasks({ showToast }: { showToast: (t: string) => void }) {
  const [status, setStatus] = useState('pending')
  const [executor, setExecutor] = useState<'all' | 'human' | 'bot'>('all')
  const [items, setItems] = useState<ScheduledActionItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const { openConversation } = useDrawer()

  const load = async () => {
    try {
      const r = await api.get<{ items: ScheduledActionItem[] }>(`/scheduled-actions?status=${status}`)
      setItems(r.items); setLoaded(true)
    } catch { /* ignore */ }
  }
  usePolling(load, 30000, [status])

  const filtered = executor === 'all' ? items : items.filter(a => a.executor === executor)

  const cancel = async (id: string) => {
    setBusy(true)
    try { await api.post(`/scheduled-actions/${id}/cancel`); showToast('Отменено'); await load() }
    catch (e: any) { showToast(`Ошибка: ${e.message}`) }
    finally { setBusy(false) }
  }

  return (
    <>
      <div className="filters">
        <select value={status} onChange={e => setStatus(e.target.value)}>
          <option value="pending">Ожидают</option>
          <option value="done">Выполнены</option>
          <option value="failed">Ошибки</option>
          <option value="cancelled">Отменены</option>
        </select>
        <select value={executor} onChange={e => setExecutor(e.target.value as 'all' | 'human' | 'bot')}>
          <option value="all">Все исполнители</option>
          <option value="human">👤 Мои</option>
          <option value="bot">🤖 Бот</option>
        </select>
        <span className="sub" style={{ alignSelf: 'center' }}>{loaded ? `${filtered.length} шт.` : '…'}</span>
      </div>
      <div className="card" style={{ padding: 0, overflow: 'auto' }}>
        <table className="tbl">
          <thead>
            <tr><th>Когда</th><th>Тип</th><th>Лид</th><th>Канал</th><th>Текст</th><th>Кто</th><th></th></tr>
          </thead>
          <tbody>
            {filtered.map(a => (
              <tr key={a.id}>
                <td className="mono" style={{ whiteSpace: 'nowrap' }}>{fmtTime(a.due_at)}</td>
                <td>{TYPE_LABELS[a.action_type] ?? a.action_type}</td>
                <td>
                  {a.conversation_id ? (
                    <a style={{ color: 'var(--accent)', cursor: 'pointer' }}
                       onClick={() => openConversation(a.conversation_id!)}>
                      {a.customer.name || a.customer.email || 'лид'}
                    </a>
                  ) : (a.customer.name || a.customer.email || '—')}
                </td>
                <td>{CHANNEL_META[a.channel]?.icon} {CHANNEL_META[a.channel]?.label ?? a.channel}</td>
                <td className="muted" style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {a.payload?.text ?? '—'}
                </td>
                <td><span className="chip">{a.executor === 'bot' ? '🤖 бот' : '👤 менеджер'}</span></td>
                <td>
                  {a.status === 'pending' && (
                    <button className="btn sm danger" onClick={() => cancel(a.id)} disabled={busy}>Отменить</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {loaded && filtered.length === 0 && <div className="empty">Пусто</div>}
      </div>
    </>
  )
}

/* ---------- Массовый дожим спящих (под контролем) ---------- */

function SleepingPanel({ showToast }: { showToast: (t: string) => void }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<{ items: any[]; count: number; ready: number } | null>(null)
  const [busy, setBusy] = useState('')
  const [massOpen, setMassOpen] = useState(false)
  const [massText, setMassText] = useState('')
  const { openConversation } = useDrawer()

  const load = async () => { try { setData(await api.get('/whatsapp/sleeping?hours=24')) } catch { /* */ } }
  const toggle = () => { const n = !open; setOpen(n); if (n && !data) void load() }

  const pollDone = async (url: string) => {
    for (let i = 0; i < 45; i++) {
      await new Promise(r => setTimeout(r, 4000))
      try { const s = await api.get<any>(url); if (!s.running) return s } catch { /* */ }
    }
    return null
  }
  const prepare = async () => {
    setBusy('prep')
    try {
      await api.post('/whatsapp/prepare-drafts', {})
      showToast('🤖 Бот готовит дожим спящим…')
      const s = await pollDone('/whatsapp/drafts-status')
      if (s) showToast(`✅ Подготовлено черновиков: ${s.prepared ?? 0}`)
      await load()
    } catch (e: any) { showToast(`Ошибка: ${e?.detail ?? e?.message ?? 'ошибка'}`) }
    finally { setBusy('') }
  }
  const sendAll = async () => {
    setBusy('send')
    try {
      const r = await api.post<any>('/whatsapp/send-sleeping', { hours: 24 })
      if (!r.started) { showToast(r.reason || 'нет готовых черновиков'); setBusy(''); return }
      showToast(`📤 Отправляю ${r.total} (с паузами анти-бан)…`)
      const s = await pollDone('/whatsapp/send-status')
      if (s) showToast(`✅ Отправлено: ${s.sent ?? 0} · пропущено ${s.skipped ?? 0}`)
      await load()
    } catch (e: any) { showToast(`Ошибка: ${e?.detail ?? e?.message ?? 'ошибка'}`) }
    finally { setBusy('') }
  }
  const sendOne = async (id: string) => {
    setBusy(id)
    try { await api.post(`/conversations/${id}/wa-draft`, { action: 'send' }); showToast('✅ Отправлено'); await load() }
    catch (e: any) { showToast(`Ошибка: ${e?.detail ?? e?.message ?? 'ошибка'}`) }
    finally { setBusy('') }
  }
  // В «Проигран» (меняет стадию → уходит из дожима). suggest=true → причина hard_stop (отказ/ошибся).
  const markLost = async (id: string, suggest: boolean) => {
    setBusy(id)
    try { await api.post(`/conversations/${id}/stage`, { to_stage: 'lost', lost_reason: suggest ? 'hard_stop' : 'delayed' }); showToast('✗ Не сложилось'); await load() }
    catch (e: any) { showToast(`Ошибка: ${e?.detail ?? e?.message ?? 'ошибка'}`) }
    finally { setBusy('') }
  }
  // Убрать из спящих (не дожимать) — стадию НЕ меняет, вернётся если лид напишет.
  const dismiss = async (id: string) => {
    setBusy(id)
    try { await api.post(`/whatsapp/sleeping/${id}/dismiss`, {}); showToast('🚫 Убрано из спящих'); await load() }
    catch (e: any) { showToast(`Ошибка: ${e?.detail ?? e?.message ?? 'ошибка'}`) }
    finally { setBusy('') }
  }
  // Массовый пинок: один текст всем видимым молчунам (с подстановкой имени), троттл на бэке.
  const sendMass = async () => {
    const ids = (data?.items || []).map((i: any) => i.conversation_id)
    if (!massText.trim() || ids.length === 0) { showToast('Напиши текст (и нужны молчуны)'); return }
    if (!window.confirm(`Отправить ОДИН текст ${ids.length} молчунам? (по одному, с паузами 5–7 сек; имя подставится)`)) return
    setBusy('mass')
    try {
      const r = await api.post<any>('/whatsapp/mass-nudge', { text: massText, conversation_ids: ids })
      if (r.already_running) { showToast('Отправка уже идёт'); setBusy(''); return }
      showToast(`📤 Массовый пинок ${r.total} (с паузами анти-бан)…`)
      const s = await pollDone('/whatsapp/send-status')
      if (s) showToast(`✅ Отправлено: ${s.sent ?? 0} · пропущено ${s.skipped ?? 0}`)
      setMassOpen(false); setMassText(''); await load()
    } catch (e: any) { showToast(`Ошибка: ${e?.detail ?? e?.message ?? 'ошибка'}`) }
    finally { setBusy('') }
  }

  const items = data?.items || []
  return (
    <div className="card" style={{ padding: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <button className="btn sm ghost" onClick={toggle} style={{ minWidth: 26 }}>{open ? '▾' : '▸'}</button>
        <b style={{ fontSize: 13, cursor: 'pointer' }} onClick={toggle}>💤 Спящие лиды{data ? ` (${data.count})` : ''}</b>
        {data && data.ready > 0 && <span className="chip accent">{data.ready} готовы</span>}
        <span style={{ flex: 1 }} />
        {open && <button className="btn sm" onClick={prepare} disabled={!!busy}>{busy === 'prep' ? '…' : '🤖 Подготовить дожим'}</button>}
        {open && data && data.ready > 0 && <button className="btn sm primary" onClick={sendAll} disabled={!!busy}>{busy === 'send' ? '…' : `✅ Отправить всем готовым (${data.ready})`}</button>}
        {open && data && data.count > 0 && <button className="btn sm" onClick={() => setMassOpen(v => !v)} disabled={!!busy}>📣 Массовый пинок</button>}
      </div>
      {open && (
        <>
          <div className="faint" style={{ fontSize: 11.5, margin: '6px 0 8px', lineHeight: 1.5 }}>
            <b>Спящие</b> = активные лиды, молчащие дольше суток (мы написали последними).
            «🤖 Подготовить дожим» — бот напишет черновик каждому (ПОД КОНТРОЛЕМ — само не уходит);
            проверь и отправь «✅» точечно или «всем готовым» (с паузами 5–7 сек — не забанит).
            <b> «⚠️»</b> — похоже не лид (извинился/отказ/ошибся): жми «✗ Не сложилось». «🚫» — просто убрать из дожима.
          </div>
          {massOpen && (
            <div style={{ border: '1px solid var(--accent-border)', borderRadius: 8, padding: 10, marginBottom: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
              <b style={{ fontSize: 12.5 }}>📣 Массовый пинок — один текст всем молчунам ({data?.items.length ?? 0})</b>
              <textarea value={massText} onChange={e => setMassText(e.target.value)} rows={3}
                placeholder="Напиши сообщение. {name} подставит имя лида. Напр.: «{name}, добрый день! Подскажите, ваш вопрос ещё актуален?»"
                style={{ fontSize: 12.5, padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)', resize: 'vertical' }} />
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <button className="btn sm primary" onClick={sendMass} disabled={!!busy || !massText.trim()}>{busy === 'mass' ? '…' : `📤 Отправить всем (${data?.items.length ?? 0})`}</button>
                <span className="faint" style={{ fontSize: 11 }}>⚠️ По одному, с паузами 5–7 сек (анти-бан). Имя подставится. Лучше короткий нейтральный вопрос — не рассылка-спам.</span>
              </div>
            </div>
          )}
          {!data && <div className="faint" style={{ fontSize: 12 }}><span className="spin" /> загрузка…</div>}
          {data && items.length === 0 && <div className="faint" style={{ fontSize: 12 }}>спящих нет 🎉</div>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            {items.map((l: any) => (
              <div className="conv-row" key={l.conversation_id} style={{ cursor: 'default' }}>
                <div className="c-main" style={{ cursor: 'pointer' }} onClick={() => openConversation(l.conversation_id)}>
                  <div className="c-name" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <TempDot t={l.temperature} />{l.name}
                    <StageChip s={l.stage_label} />
                    {l.hours_silent != null && <span className="faint" style={{ fontSize: 11 }}>молчит {l.hours_silent}ч</span>}
                    {l.suggest_lost && <span className="chip warn">⚠️ {l.lost_hint || 'похоже не лид'}</span>}
                  </div>
                  {l.draft
                    ? <div className="faint" style={{ fontSize: 11.5, marginTop: 2, fontStyle: 'italic' }}>✍️ «{l.draft.slice(0, 120)}{l.draft.length > 120 ? '…' : ''}»</div>
                    : <div className="faint" style={{ fontSize: 11.5, marginTop: 2 }}>нет черновика — нажми «Подготовить»</div>}
                </div>
                <div className="c-meta">
                  <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    {l.has_draft && !l.suggest_lost && <button className="btn sm primary" disabled={!!busy} onClick={() => sendOne(l.conversation_id)}>✅ Отправить</button>}
                    <button className="btn sm" disabled={!!busy} style={l.suggest_lost ? { color: '#e0524f', borderColor: '#e0524f' } : undefined} onClick={() => markLost(l.conversation_id, !!l.suggest_lost)}>✗ Не сложилось</button>
                    <button className="btn sm ghost" disabled={!!busy} title="Убрать из спящих (не дожимать)" onClick={() => dismiss(l.conversation_id)}>🚫</button>
                    <button className="btn sm ghost" onClick={() => openConversation(l.conversation_id)}>Открыть</button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
