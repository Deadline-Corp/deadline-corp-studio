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
}
type ZoneId = 'approve_now' | 'your_turn' | 'bot_leading' | 'stuck' | 'waiting'
type Board = {
  summary: { overdue: number; today: number; no_task: number; bot: number; human: number
    approve_now: number; your_turn: number; bot_leading: number; stuck: number }
  buckets: Record<'overdue' | 'today' | 'tomorrow' | 'week' | 'later', BoardTask[]>
  no_task_leads: NoTaskLead[]
  zones: { approve_now: Lead[]; your_turn: Lead[]; bot_leading: Lead[]; waiting: Lead[] }
  stuck: Lead[]
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
          <button className={`btn sm ${tab === 'day' ? 'primary' : 'ghost'}`} onClick={() => setTab('day')}>Мой день</button>
          <button className={`btn sm ${tab === 'all' ? 'primary' : 'ghost'}`} onClick={() => setTab('all')}>Все задачи</button>
        </div>
      </div>
      <HintBar id="tasks" icon="⏰">
        Доска как в CRM: у каждого активного лида — следующая задача. Сверху <b>«Лиды без
        задачи»</b> (их легко забыть) — реши, ведёт ли их бот сам или ты. Ниже задачи по
        срочности и приоритету (🔥 горячие выше). 🤖 — бот сделает сам, 👤 — за тобой.
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
  const [filter, setFilter] = useState<ZoneId | null>(null)
  const [waitOpen, setWaitOpen] = useState(false)  // зона «Ждём лида» свёрнута по умолчанию
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
  // Перенос задачи на N дней вперёд (на 10:00). Бэкенд /reschedule меняет due_at.
  const reschedule = (id: string, days: number) => act(async () => {
    const d = new Date(); d.setDate(d.getDate() + days); d.setHours(10, 0, 0, 0)
    await api.post(`/scheduled-actions/${id}/reschedule`, { due_at: d.toISOString() })
  }, days === 1 ? 'Перенесено на завтра' : `Перенесено на +${days}д`)
  const markLostTask = (convId: string) =>
    act(() => api.post(`/conversations/${convId}/stage`, { to_stage: 'lost', lost_reason: 'delayed' }), '✗ Не сложилось')
  // Одобрить готовый черновик бота (зона «Одобри сейчас»): отправить / отклонить.
  const approveDraft = (convId: string) => act(() => api.post(`/conversations/${convId}/wa-draft`, { action: 'send' }), '✅ Ответ отправлен')
  const rejectDraft = (convId: string) => act(() => api.post(`/conversations/${convId}/wa-draft`, { action: 'reject' }), '🚫 Черновик отклонён')
  // Вернуть автопилотного лида на ручное одобрение (зона «Бот ведёт»).
  const toApproval = (convId: string) => act(() => api.post(`/conversations/${convId}/wa-autonomous`, { on: false }), '⏸ Вернул на одобрение')

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
              ? <>🤖 ведёт сам{l.last_message_at ? ` · последнее ${fmtAgo(l.last_message_at)} назад` : ''}</>
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
            {l.bot_capable && !l.wa_autonomous && <button className="btn sm" disabled={!!busy} onClick={() => botLead(l.conversation_id)}>🤖 Боту</button>}
            <button className="btn sm ghost" onClick={() => openConversation(l.conversation_id)}>Открыть</button>
          </>}
          {zone === 'bot_leading' && <>
            <button className="btn sm ghost" disabled={!!busy} title="Вернуть на ручное одобрение" onClick={() => toApproval(l.conversation_id)}>⏸ На одобрение</button>
            <button className="btn sm ghost" onClick={() => openConversation(l.conversation_id)}>Открыть</button>
          </>}
          {(zone === 'waiting' || zone === 'stuck') && <>
            {l.bot_capable && !l.wa_autonomous && <button className="btn sm" disabled={!!busy} onClick={() => botLead(l.conversation_id)}>🤖 Боту</button>}
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

  const sm = board.summary
  const z = board.zones
  const ZChip = ({ id, label, cls }: { id: ZoneId; label: string; cls?: string }) => (
    <span className={`chip ${cls ?? ''}`} style={{ cursor: 'pointer', boxShadow: filter === id ? '0 0 0 2px var(--accent)' : 'none' }}
          onClick={() => setFilter(filter === id ? null : id)}>{label}</span>
  )
  const empty = sm.approve_now + sm.your_turn + sm.bot_leading + sm.stuck + z.waiting.length === 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <ZChip id="approve_now" cls="warn" label={`⏳ Одобри ${sm.approve_now}`} />
        <ZChip id="your_turn" label={`👤 Твой ход ${sm.your_turn}`} />
        <ZChip id="bot_leading" cls="ok" label={`🤖 Бот ведёт ${sm.bot_leading}`} />
        <ZChip id="stuck" cls="danger" label={`⚠️ Затык ${sm.stuck}`} />
        {filter && <button className="btn sm ghost" onClick={() => setFilter(null)}>✕ показать всё</button>}
        <span style={{ flex: 1 }} />
        <button className="btn sm" onClick={sweep} disabled={!!busy}>▶ Проверить сейчас</button>
        <Help title="Проверить сейчас" text="Бот сам каждые ~10 минут: дожимает молчунов, шлёт напоминания, чинит рассинхроны. Кнопка запускает проверку немедленно." />
      </div>

      {(!filter || filter === 'stuck') &&
        <Zone id="stuck" color="var(--danger)" items={board.stuck}
              title="⚠️ Затыки — лиды без движения и без задачи"
              hint="Ими никто не занимается: ни бот, ни задача, давно молчат. Реши: передать боту, написать самому или закрыть." />}
      {(!filter || filter === 'approve_now') &&
        <Zone id="approve_now" color="var(--accent-border)" items={z.approve_now}
              title="⏳ Одобри сейчас" hint="Бот подготовил ответ и ждёт твоё «ОК» — один клик ✅." />}
      {(!filter || filter === 'your_turn') &&
        <Zone id="your_turn" items={z.your_turn}
              title="👤 Твой ход" hint="Нужен человек: позвонить, выставить КП, ответить на сложное."
              action={<button className="btn sm primary" disabled={!!busy} onClick={generate} title="Бот прочитает диалоги без шага и предложит, что делать">🤖 Разобрать новых</button>} />}
      {(!filter || filter === 'bot_leading') &&
        <Zone id="bot_leading" items={z.bot_leading}
              title="🤖 Бот ведёт сам" hint="Автопилот — делать ничего не надо, видно статус. Можно вернуть на одобрение." />}

      {!filter && z.waiting.length > 0 && (
        <div className="card" style={{ padding: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }} onClick={() => setWaitOpen(v => !v)}>
            <b style={{ fontSize: 13 }}>{waitOpen ? '▾' : '▸'} ⏸ Ждём лида ({z.waiting.length})</b>
            <span className="faint" style={{ fontSize: 11.5 }}>мяч у лида / бот дожмёт по каденции — не срочно</span>
          </div>
          {waitOpen && <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginTop: 8 }}>{z.waiting.map(l => LeadCard(l, 'waiting'))}</div>}
        </div>
      )}

      {!filter && <SleepingPanel showToast={showToast} />}

      {empty && <div className="empty">Всё под контролем — лидов, требующих внимания, нет 🎉</div>}
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
