import { useState, useEffect } from 'react'
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
  call_booked: '📞 Созвон', call_reminder: '⏰ Напоминание о созвоне',
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
  deal_value: number | null
}
type NoTaskLead = {
  conversation_id: string; name: string; stage: string | null; stage_label: string
  temperature: string | null; channel: string; last_message_at: string | null
  next_action: string; bot_can: boolean; wa_autonomous: boolean
  mode: string | null; kind: string | null; draft: string; reason: string; analyzed: boolean
  deal_value: number | null
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
  deal_value: number | null
}
type ZoneId = 'approve_now' | 'your_turn' | 'bot_leading' | 'stuck' | 'waiting'
// Вид доски: «Всё» / одна категория (секция) / срез «кто ведёт». Кликается вверху.
type ViewKey = 'all' | 'overdue' | 'approve' | 'today' | 'notask' | 'stuck' | 'delivery' | 'bot' | 'human'
// Упавшая bot-задача (дожим/напоминание не доставлено) — сигнал «нужен человек».
type FailedItem = {
  id: string; conversation_id: string | null; name: string; channel: string
  text: string; action_type: string; stage_label: string; temperature: string | null
  attempts: number; wa_autonomous?: boolean; fail_count?: number
}
type Board = {
  summary: { overdue: number; today: number; no_task: number; bot: number; human: number
    approve_now: number; your_turn: number; bot_leading: number; stuck: number
    delivery_failed: number; done_7d?: number }
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
// Вес сделки: ₸150К / ₸1.2М. Крупные (≥500К) — зелёным (трогать вручную), мелкие серым.
const DealChip = ({ v }: { v: number | null }) => {
  if (!v || v <= 0) return null
  const s = v >= 1e6 ? `₸${(v / 1e6).toFixed(1)}М` : v >= 1e3 ? `₸${Math.round(v / 1e3)}К` : `₸${v}`
  return <span className="chip" title="Сумма сделки"
    style={{ fontSize: 10, color: v >= 5e5 ? '#3bb4a0' : undefined, fontWeight: 600 }}>{s}</span>
}
// «через 2ч 15м» для будущего времени (контекст «когда бот напишет»). Прошлое → ''.
const fmtCountdown = (iso: string | null): string => {
  if (!iso) return ''
  const ms = new Date(iso).getTime() - Date.now()
  if (ms <= 0) return ''
  const h = Math.floor(ms / 3.6e6), m = Math.floor((ms % 3.6e6) / 6e4)
  return h >= 24 ? `через ${Math.round(h / 24)}д` : h > 0 ? `через ${h}ч${m ? ` ${m}м` : ''}` : `через ${m}м`
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
          <button className={`btn sm ${tab === 'day' ? 'primary' : 'ghost'}`} onClick={() => setTab('day')}>Доска</button>
          <button className={`btn sm ${tab === 'all' ? 'primary' : 'ghost'}`} onClick={() => setTab('all')}>Все задачи (список)</button>
        </div>
      </div>
      <HintBar id="tasks" icon="⏰">
        Кнопки сверху — фильтр: нажми <b>⏳ Одобрить</b>, <b>🆘 Бот завис</b> или любую — увидишь
        только её, остальное скроется. <b>«Всё»</b> — общий вид по срочности. Зелёная рамка слева
        у карточки = этот диалог ведёт бот.
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
  // Один кликабельный фильтр вверху: «Всё» или одна категория/срез — показывается ТОЛЬКО
  // выбранное, остальные секции скрыты (не нужно крутить вниз).
  const [view, setView] = useState<ViewKey>('all')
  const [daysOpen, setDaysOpen] = useState(false)  // будущие дни (завтра/неделя/позже) свёрнуты
  const [stageFilter, setStageFilter] = useState<string | null>(null)  // фильтр по СТАТУСУ (стадии воронки)
  const [rsId, setRsId] = useState('')    // id задачи с открытым пикером переноса
  const [rsVal, setRsVal] = useState('')  // значение datetime-local
  const [ntConvId, setNtConvId] = useState('')  // диалог с открытой формой «создать задачу»
  const [ntVal, setNtVal] = useState('')        // дата/время новой задачи
  const [ntText, setNtText] = useState('')      // текст новой задачи
  const [stkId, setStkId] = useState('')        // диалог с открытым полем «объясни боту»
  const [stkVal, setStkVal] = useState('')      // текст подсказки боту
  const [openDraft, setOpenDraft] = useState('') // диалог с раскрытым полным черновиком
  const [lastLoad, setLastLoad] = useState(0)    // когда последний раз обновили доску (мс)
  const [, setTick] = useState(0)                // тик раз в 5с — освежить метку «обновлено N назад»
  const { openConversation } = useDrawer()

  const load = async () => {
    try { setBoard(await api.get<Board>('/task-board')); setLastLoad(Date.now()) } catch { /* */ }
  }
  usePolling(load, 20000)
  useEffect(() => { const t = setInterval(() => setTick(x => x + 1), 5000); return () => clearInterval(t) }, [])

  // fn может вернуть строку — она станет текстом тоста (иначе берётся ok). Так у
  // действий со счётчиком (крон/разбор) ровно ОДИН тост, а не два подряд.
  const act = async (fn: () => Promise<any>, ok: string) => {
    setBusy('1')
    try { const r = await fn(); showToast(typeof r === 'string' ? r : ok); await load() }
    catch (e: any) { showToast(`Ошибка: ${e?.detail ?? e?.message ?? 'ошибка'}`) }
    finally { setBusy('') }
  }
  const done = (id: string) => act(() => api.post(`/scheduled-actions/${id}/done`), '✅ Сделано')
  const cancel = (id: string) => act(() => api.post(`/scheduled-actions/${id}/cancel`), 'Отменено')
  // 🤖 Боту = передать диалог боту. На WhatsApp (WAHA, неофициальный) это может СРАЗУ
  // отправить готовый черновик лиду — поэтому подтверждаем перед включением автопилота.
  const botLead = (id: string) => {
    if (!window.confirm('Передать лида боту? Бот будет вести диалог сам; если для него уже готов черновик ответа — он отправит его лиду сейчас.')) return
    act(() => api.post(`/conversations/${id}/wa-autonomous`, { on: true }), '🤖 Бот ведёт диалог')
  }
  const sweep = () => act(async () => {
    const r = await api.post<any>('/cron/sweep'); const n = r.followups?.sent ?? 0
    return n > 0 ? `↻ Готово · бот разослал ${n} сообщений (ботоведомым) и навёл порядок`
      : '↻ Готово · бот навёл порядок; рассылать сейчас было нечего'
  }, 'Готово')
  const generate = () => act(async () => {
    const r = await api.post<any>('/task-board/generate', { limit: 10 })
    return `🤖 Разобрал ${r.processed ?? 0} лидов`
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
  // «Объясни боту» по зависшему лиду: подсказка менеджера → бот ПЕРЕразбирает с её
  // учётом (лиду ничего не шлёт — готовит шаг/черновик). Без текста — просто переразбор.
  const rethink = (convId: string, hint: string) => act(async () => {
    const r = await api.post<any>(`/conversations/${convId}/rethink`, { hint: hint.trim() || undefined })
    setStkId(''); setStkVal('')
    return r.rethought ? `🤖 Бот разобрал: ${r.label || r.mode || 'готово'}` : (r.reason || 'бот не стал разбирать')
  }, '🤖 Переразобрал')

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
          <DealChip v={l.deal_value} />
          <span className="chip" style={{ fontSize: 10 }} title="Статус автоматизации по лиду">{l.bot_status_label}</span>
          <span className="faint" style={{ fontWeight: 400 }}>{CHANNEL_META[l.channel]?.icon}</span>
        </div>
        <div className="c-preview">
          {zone === 'approve_now'
            ? (l.draft
                ? <i onClick={e => { e.stopPropagation(); setOpenDraft(openDraft === l.conversation_id ? '' : l.conversation_id) }}
                     style={{ cursor: 'pointer' }} title={openDraft === l.conversation_id ? 'Свернуть' : 'Показать целиком'}>
                    ✍️ «{openDraft === l.conversation_id ? l.draft : l.draft.slice(0, 130) + (l.draft.length > 130 ? '…' : '')}»
                    {l.draft.length > 130 && <span className="faint" style={{ fontSize: 10 }}> {openDraft === l.conversation_id ? '▴ свернуть' : '▾ целиком'}</span>}
                  </i>
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
    const overdueMs = t.due_at ? Date.now() - new Date(t.due_at).getTime() : 0
    const overdue = overdueMs > 0
    const overdueDays = overdue ? Math.floor(overdueMs / 86400000) : 0
    const isBot = t.who === 'bot'
    return (
      <div className={`conv-row${t.wa_autonomous ? ' autonomous' : ''}`} key={t.id}
           style={!t.wa_autonomous && overdue ? { boxShadow: 'inset 3px 0 0 var(--danger)' } : undefined}>
        <div className="c-main" style={{ cursor: t.conversation_id ? 'pointer' : 'default' }}
             onClick={() => t.conversation_id && openConversation(t.conversation_id)}>
          <div className="c-name" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <TempDot t={t.temperature} />{t.name}
            <span className="faint" style={{ fontWeight: 400 }}>· {t.text || TYPE_LABELS[t.action_type] || 'задача'}</span>
            <StageChip s={t.stage_label} />
            <DealChip v={t.deal_value} />
            <span className="faint" style={{ fontWeight: 400 }}>{CHANNEL_META[t.channel]?.icon}</span>
          </div>
          <div className="c-preview" style={overdue ? { color: 'var(--danger)' } : undefined}>
            {overdue && <b>🔴 просрочено {overdueDays > 0 ? `${overdueDays} дн` : 'сегодня'} · </b>}
            {isBot ? '🤖 бот сделает сам' : ''}{t.due_at ? `${isBot ? ' · ' : ''}${fmtTime(t.due_at)}` : ''}
            {isBot && !overdue && fmtCountdown(t.due_at) && <span className="faint" style={{ fontSize: 10.5 }}> ({fmtCountdown(t.due_at)})</span>}
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
    <div className={`conv-row${l.wa_autonomous ? ' autonomous' : ''}`} key={l.conversation_id}
         style={l.wa_autonomous ? undefined : { boxShadow: 'inset 3px 0 0 var(--danger)', background: 'rgba(224,82,79,0.07)' }}>
      <div className="c-main" style={{ cursor: 'pointer' }} onClick={() => openConversation(l.conversation_id)}>
        <div className="c-name" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <TempDot t={l.temperature} />{l.name}
          <StageChip s={l.stage_label} />
          <DealChip v={l.deal_value} />
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

  // Карточка «ЗАТЫК»: бот не разобрался (молчит >2 суток, шага нет). Человек может
  // ОБЪЯСНИТЬ боту, что делать — бот переразберёт с подсказкой и даст готовый шаг.
  const StuckCard = (l: Lead) => {
    const open = stkId === l.conversation_id
    return (
      <div className={`conv-row${l.wa_autonomous ? ' autonomous' : ''}`} key={l.conversation_id}
           style={l.wa_autonomous ? undefined : { boxShadow: 'inset 3px 0 0 #c9a23b', background: 'rgba(201,162,59,0.07)' }}>
        <div className="c-main" style={{ cursor: 'pointer' }} onClick={() => openConversation(l.conversation_id)}>
          <div className="c-name" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <TempDot t={l.temperature} />{l.name}
            <StageChip s={l.stage_label} />
            <DealChip v={l.deal_value} />
            <span className="faint" style={{ fontWeight: 400 }}>{CHANNEL_META[l.channel]?.icon}</span>
          </div>
          <div className="c-preview" style={{ color: '#c9a23b' }}>
            бот не разобрался{l.last_message_at ? ` · молчит ${fmtAgo(l.last_message_at)}` : ''} — объясни ему, что делать
          </div>
        </div>
        <div className="c-meta" style={{ width: '100%' }}>
          {open ? (
            <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap', width: '100%', justifyContent: 'flex-end' }}
                 onClick={e => e.stopPropagation()}>
              <input type="text" value={stkVal} autoFocus placeholder="напр.: это ресторан, предложи созвон в четверг"
                     onChange={e => setStkVal(e.target.value)} style={{ fontSize: 11, flex: 1, minWidth: 180 }} />
              <button className="btn sm primary" disabled={!!busy}
                      onClick={() => rethink(l.conversation_id, stkVal)}>🤖 Переразобрать</button>
              <button className="btn sm ghost" onClick={() => { setStkId(''); setStkVal('') }}>✕</button>
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              <button className="btn sm primary" disabled={!!busy} title="Написать боту, что делать — он переразберёт"
                      onClick={() => { setStkId(l.conversation_id); setStkVal('') }}>💬 Объяснить боту</button>
              <NewTask convId={l.conversation_id} />
              <button className="btn sm ghost" disabled={!!busy} title="В «Не сложилось»" onClick={() => markLostTask(l.conversation_id)}>✗</button>
              <button className="btn sm ghost" onClick={() => openConversation(l.conversation_id)}>Открыть</button>
            </div>
          )}
        </div>
      </div>
    )
  }

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
  // «кто ведёт»-срез: бот = автопилот ИЛИ шаг назначен боту. Применяется ТОЛЬКО в видах
  // «🤖 Бот»/«👤 Я веду»; в «Всё» и категориях — показываем всех.
  const isBot = (it: { who?: string; wa_autonomous?: boolean }) => it.wa_autonomous === true || it.who === 'bot'
  const whoOk = (it: { who?: string; wa_autonomous?: boolean }) =>
    view === 'bot' ? isBot(it) : view === 'human' ? !isBot(it) : true
  const pass = (it: { who?: string; wa_autonomous?: boolean; stage?: string | null }) =>
    whoOk(it) && sStage(it.stage ?? null)
  // Категория видна, если выбран вид «Всё/Бот/Я веду» (показываем все секции) ИЛИ выбрана
  // ИМЕННО эта категория (показываем только её — остальные прячем).
  const showCat = (c: string) => view === 'all' || view === 'bot' || view === 'human' || view === c
  const approveIds = new Set(z.approve_now.map(l => l.conversation_id))
  const stuckIds = new Set(board.stuck.map(l => l.conversation_id))
  const fT = (items: BoardTask[]) => items.filter(pass)
  const fApprove = z.approve_now.filter(pass)
  // «Без задачи» = активные лиды без следующего шага, которыми НЕ занят бот (это сироты,
  // нужен ЧЕЛОВЕК). Бот-ведомые сюда НЕ попадают (кроме фильтра «🤖 Бот») — их «ведёт бот»,
  // это не «без задачи для тебя». «Затыки» тоже исключены (отдельный блок).
  const fNoTask = board.no_task_leads.filter(l =>
    !approveIds.has(l.conversation_id) && !stuckIds.has(l.conversation_id) && pass(l)
    && (view === 'bot' || !isBot(l)))
  const fStuck = board.stuck.filter(pass)
  // Счётчик «Без задачи» = только сироты для человека (без бот-ведомых/одобрить/затыков),
  // чтобы число на кнопке совпадало с тем, что в секции.
  const noTaskN = board.no_task_leads.filter(l =>
    !isBot(l) && !approveIds.has(l.conversation_id) && !stuckIds.has(l.conversation_id)).length

  const overdue = fT(b.overdue), today = fT(b.today)
  const tomorrow = fT(b.tomorrow), week = fT(b.week), later = fT(b.later)
  const futureN = tomorrow.length + week.length + later.length
  // Сколько реально видно при текущем виде (для пустого состояния).
  const shownN = (showCat('overdue') ? overdue.length : 0) + (showCat('approve') ? fApprove.length : 0)
    + (showCat('today') ? today.length : 0) + (showCat('notask') ? fNoTask.length : 0)
    + (showCat('stuck') ? fStuck.length : 0) + (showCat('delivery') ? board.delivery_failed.length : 0)
    + (showCat('future') ? futureN : 0)
  const nothing = shownN === 0
  const staleSec = lastLoad ? Math.round((Date.now() - lastLoad) / 1000) : 0

  // Кликабельная кнопка-категория вверху. Активная подсвечена. Клик → показать ТОЛЬКО её.
  const Chip = ({ id, label, color, hint }: { id: ViewKey; label: string; color?: string; hint?: string }) => (
    <button className={`btn sm ${view === id ? 'primary' : 'ghost'}`} title={hint}
            style={color && view !== id ? { color } : undefined}
            onClick={() => setView(id)}>{label}</button>
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
      {/* ОДНА кликабельная панель: «Всё» + категории + срез «кто ведёт». Клик → видна
          ТОЛЬКО выбранная категория, остальные секции скрыты (не нужно крутить вниз). */}
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        <Chip id="all" label="Всё" hint="Все задачи и лиды по порядку срочности." />
        <Chip id="overdue" label={`⚠️ Просрочено ${sm.overdue}`} color="var(--danger)"
              hint="Срок шага прошёл, а он не сделан — это в первую очередь." />
        <Chip id="approve" label={`⏳ Одобрить ${sm.approve_now}`} color="var(--accent)"
              hint="Бот подготовил ответы лидам — проверь и нажми «Одобрить» (или поправь перед отправкой)." />
        <Chip id="today" label={`☀️ Сегодня ${sm.today}`} hint="Задачи, у которых срок — сегодня." />
        <Chip id="notask" label={`🏷 Без задачи ${noTaskN}`} color="#b6791f"
              hint="Активные лиды, по которым НЕТ следующего шага и которыми НЕ занят бот — назначь задачу или передай боту, чтобы не потерять." />
        <Chip id="stuck" label={`🆘 Бот завис ${sm.stuck}`} color="#c9a23b"
              hint="Бот не разобрался сам (молчат >2 суток) — объясни ему, что делать, или возьми на себя." />
        {board.delivery_failed.length > 0 && <Chip id="delivery" label={`📵 Не дошло ${board.delivery_failed.length}`} color="var(--danger)"
              hint="Авто-сообщения бота не доставились лиду — проверь / напиши вручную." />}
        <span style={{ width: 1, alignSelf: 'stretch', minHeight: 20, background: 'var(--border)', margin: '0 3px' }} />
        <Chip id="bot" label={`🤖 Бот ${sm.bot}`} hint="Диалоги, которые бот ведёт сам (зелёная рамка). Зайди — увидишь его план." />
        <Chip id="human" label={`👤 Я веду ${sm.your_turn + sm.approve_now}`}
              hint="Лиды, которыми занимаешься ты (бот их не ведёт автономно)." />
        <span style={{ flex: 1 }} />
        {lastLoad > 0 && (
          <span className="faint" style={{ fontSize: 10.5, color: staleSec > 60 ? '#c9a23b' : undefined }}
                title="Когда доска последний раз подтянула данные (сама раз в ~20 сек)">
            {staleSec > 60 ? `⚠️ обновлено ${staleSec >= 120 ? Math.round(staleSec / 60) + ' мин' : staleSec + ' сек'} назад` : `обновлено ${staleSec} сек назад`}
          </span>
        )}
        <button className="btn sm" onClick={sweep} disabled={!!busy}
                title="Запустить работу бота прямо сейчас (он и так делает это сам каждые ~10 мин).">
          {busy ? '↻ Бот работает…' : '↻ Бот: проверить'}
        </button>
        <Help title="Бот: проверить сейчас" text="Запускает работу бота прямо сейчас (он и так делает это сам каждые ~10 минут — нажимай, только если не хочешь ждать). Делает два дела: 1) НАВОДИТ ПОРЯДОК во всей панели — склеивает раздвоенные карточки, убирает дубли и фантомы, чинит рассинхроны (по ВСЕМ лидам); 2) ШЛЁТ авто-сообщения (дожимы молчунам, напоминания о созвоне) — ТОЛЬКО лидам, которых ведёт сам бот (зелёная рамка). Лидам на ручном ведении бот по нажатию НЕ пишет — только готовит черновик на твоё одобрение." />
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

      {showCat('delivery') && board.delivery_failed.length > 0 && (
        <div>
          <Head title="📵 Доставка не удалась" n={board.delivery_failed.length} color="var(--danger)"
                hint="Бот пытался сам отправить лиду авто-сообщение (напоминание о созвоне / дожим), но оно НЕ доставилось: номер недоступен/заблокирован ИЛИ был временный сбой связи. Лид мог не получить → открой и проверь / напиши вручную. Старые такие записи бот сам убирает через 2 недели." />
          <div style={colS}>
            {board.delivery_failed.map(f => (
              <div className={`conv-row${f.wa_autonomous ? ' autonomous' : ''}`} key={f.id}>
                <div className="c-main" style={{ cursor: f.conversation_id ? 'pointer' : 'default' }}
                     onClick={() => f.conversation_id && openConversation(f.conversation_id)}>
                  <div className="c-name" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <TempDot t={f.temperature} />{f.name}<StageChip s={f.stage_label} />
                    {(f.fail_count ?? 1) > 1 && <span className="chip" style={{ fontSize: 10, color: 'var(--danger)' }}>×{f.fail_count} не дошло</span>}
                    <span className="faint" style={{ fontWeight: 400 }}>{CHANNEL_META[f.channel]?.icon}</span>
                  </div>
                  <div className="c-preview"><i>не доставлено: «{(f.text || '').slice(0, 90)}»</i></div>
                </div>
                <div className="c-meta">{f.conversation_id && <button className="btn sm ghost" onClick={() => openConversation(f.conversation_id!)}>Открыть</button>}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {showCat('overdue') && overdue.length > 0 && (
        <div>
          <Head title="⚠️ Просрочено" n={overdue.length} color="var(--danger)"
                hint="Срок прошёл, а шаг не сделан — в первую очередь." />
          <div style={colS}>{overdue.map(TaskCard)}</div>
        </div>
      )}

      {showCat('approve') && fApprove.length > 0 && (
        <div>
          <Head title="⏳ Одобрить" n={fApprove.length} color="var(--accent)"
                hint="Бот подготовил ответы лидам — проверь и нажми «✅ Одобрить» (или поправь)." />
          <div style={colS}>{fApprove.map(l => LeadCard(l, 'approve_now'))}</div>
        </div>
      )}

      {showCat('today') && today.length > 0 && (
        <div>
          <Head title="☀️ Сегодня" n={today.length} hint="Задачи на сегодня." />
          <div style={colS}>{today.map(TaskCard)}</div>
        </div>
      )}

      {showCat('notask') && fNoTask.length > 0 && (
        <div>
          <Head title={view === 'bot' ? '🤖 Бот ведёт сам' : '🏷 Без задачи'} n={fNoTask.length}
                color={view === 'bot' ? '#3bb4a0' : 'var(--danger)'}
                hint={view === 'bot'
                  ? 'Эти диалоги бот ведёт сам (зелёная рамка). Зайди в карточку — увидишь его план и дату следующего сообщения.'
                  : 'По этим клиентам нет следующего шага — назначь задачу или передай боту, чтобы не потерять.'}
                action={view !== 'bot' ? <button className="btn sm primary" disabled={!!busy} onClick={generate} title="Бот прочитает диалоги без шага и предложит, что делать дальше">🤖 Разобрать ботом</button> : undefined} />
          <div style={colS}>{fNoTask.map(NoTaskCard)}</div>
          {sm.no_task > board.no_task_leads.length && (
            <div className="faint" style={{ fontSize: 11.5, marginTop: 6, color: 'var(--danger)' }}>
              + ещё {sm.no_task - board.no_task_leads.length} лидов без задачи не показаны — разбери текущие или подними лимит на бэке
            </div>
          )}
        </div>
      )}

      {showCat('stuck') && fStuck.length > 0 && (
        <div>
          <Head title="🆘 Бот завис — помоги" n={fStuck.length} color="#c9a23b"
                hint="Бот не разобрался сам (молчат >2 суток). Объясни боту, что делать — он переразберёт диалог с твоей подсказкой и подготовит шаг. Или поставь задачу / закрой." />
          <div style={colS}>{fStuck.map(StuckCard)}</div>
        </div>
      )}

      {showCat('future') && futureN > 0 && (
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

      {!stageFilter && view === 'all' && <SleepingPanel showToast={showToast} />}
      {nothing && <div className="empty">{
        view === 'all' ? 'Всё под контролем — задач, требующих тебя, нет 🎉'
          : view === 'human' ? '👤 В режиме «Я веду» сейчас пусто — нажми «Всё».'
            : view === 'bot' ? '🤖 Под ботом сейчас нет активных диалогов.'
              : 'В этой категории сейчас пусто — нажми «Всё», чтобы увидеть остальное.'
      }</div>}
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
              <div className={`conv-row${l.wa_autonomous ? ' autonomous' : ''}`} key={l.conversation_id} style={{ cursor: 'default' }}>
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
