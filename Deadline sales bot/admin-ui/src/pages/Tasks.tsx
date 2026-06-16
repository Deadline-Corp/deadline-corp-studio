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
  temperature: string | null; channel: string
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
type Board = {
  summary: { overdue: number; today: number; no_task: number; bot: number; human: number }
  buckets: Record<'overdue' | 'today' | 'tomorrow' | 'week' | 'later', BoardTask[]>
  no_task_leads: NoTaskLead[]
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

  if (!board) return <div className="empty"><span className="spin" /> Загрузка…</div>

  const Task = (t: BoardTask) => (
    <div className="conv-row" key={t.id} style={{ cursor: 'default' }}>
      <span style={{ fontSize: 17 }} title={t.who === 'bot' ? 'Бот сделает сам' : 'За тобой'}>
        {t.who === 'bot' ? '🤖' : '👤'}
      </span>
      <div className="c-main" style={{ cursor: t.conversation_id ? 'pointer' : 'default' }}
           onClick={() => t.conversation_id && openConversation(t.conversation_id)}>
        <div className="c-name" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <TempDot t={t.temperature} />{t.name}
          <StageChip s={t.stage_label} />
          <span className="chip" style={{ fontSize: 10.5 }}>{TYPE_LABELS[t.action_type] ?? t.action_type}</span>
          <span className="faint" style={{ fontWeight: 400 }}>{CHANNEL_META[t.channel]?.icon}</span>
        </div>
        <div className="c-preview">{t.text || '—'}</div>
      </div>
      <div className="c-meta">
        <span className="chip">{t.due_at ? fmtTime(t.due_at) : '—'}</span>
        <div style={{ display: 'flex', gap: 5 }}>
          {t.who === 'human' && <button className="btn sm" onClick={() => done(t.id)} disabled={!!busy}>✓ Сделано</button>}
          <button className="btn sm ghost" title="Снять задачу" onClick={() => cancel(t.id)} disabled={!!busy}>✕</button>
        </div>
      </div>
    </div>
  )

  const Bucket = ({ title, items, cls }: { title: string; items: BoardTask[]; cls?: string }) =>
    items.length === 0 ? null : (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <b style={{ fontSize: 13 }} className={cls}>{title} ({items.length})</b>
        {items.map(Task)}
      </div>
    )

  const b = board.buckets
  const totalTasks = b.overdue.length + b.today.length + b.tomorrow.length + b.week.length + b.later.length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="chip danger">🔴 Просрочено {board.summary.overdue}</span>
        <span className="chip warn">🟡 Сегодня {board.summary.today}</span>
        <span className="chip">🆕 Без задачи {board.summary.no_task}</span>
        <span className="chip">🤖 {board.summary.bot} · 👤 {board.summary.human}</span>
        <span style={{ flex: 1 }} />
        <button className="btn sm" onClick={sweep} disabled={!!busy}>▶ Прогнать крон</button>
        <Help title="Прогнать крон" text="Бот сам проверяет задачи каждые ~10 минут. Кнопка запускает проверку прямо сейчас." />
      </div>

      <SleepingPanel showToast={showToast} />

      {board.no_task_leads.length > 0 && (
        <div className="card" style={{ padding: 12, borderColor: 'var(--accent-border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
            <b style={{ fontSize: 13 }}>🆕 Лиды без задачи ({board.no_task_leads.length})</b>
            <span style={{ flex: 1 }} />
            <button className="btn sm primary" onClick={generate} disabled={!!busy}>🤖 Разобрать (бот предложит шаг)</button>
          </div>
          <div className="faint" style={{ fontSize: 11.5, marginBottom: 8 }}>
            активные лиды без следующего шага. «Разобрать» — бот прочитает диалоги и предложит,
            что делать (🔁 дожать молчуна / ⏳ ответ на одобрение / 👤 за тобой / 🆘 не понял — помоги).
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            {board.no_task_leads.map(l => {
              const m = l.mode ? MODE[l.mode] : null
              return (
                <div className="conv-row" key={l.conversation_id} style={{ cursor: 'default' }}>
                  <div className="c-main" style={{ cursor: 'pointer' }} onClick={() => openConversation(l.conversation_id)}>
                    <div className="c-name" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <TempDot t={l.temperature} />{l.name}
                      <StageChip s={l.stage_label} />
                      {m && <span className="chip" style={{ fontSize: 10.5, color: m.c, borderColor: m.c }}>{m.e} {m.t}</span>}
                      <span className="faint" style={{ fontWeight: 400 }}>{CHANNEL_META[l.channel]?.icon}</span>
                    </div>
                    <div className="c-preview">→ {l.next_action}{l.last_message_at ? ` · ${fmtAgo(l.last_message_at)}` : ''}</div>
                    {l.draft && <div className="faint" style={{ fontSize: 11.5, marginTop: 2, fontStyle: 'italic' }}>✍️ «{l.draft.slice(0, 110)}{l.draft.length > 110 ? '…' : ''}»</div>}
                  </div>
                  <div className="c-meta">
                    <div style={{ display: 'flex', gap: 5 }}>
                      {l.mode === 'needs_approval' &&
                        <button className="btn sm primary" onClick={() => openConversation(l.conversation_id)} disabled={!!busy}>⏳ Одобрить</button>}
                      {(l.mode === 'reengage' || (!l.analyzed && l.bot_can)) && !l.wa_autonomous &&
                        <button className="btn sm" onClick={() => botLead(l.conversation_id)} disabled={!!busy}>🤖 Пусть бот</button>}
                      {l.wa_autonomous && <span className="chip accent" style={{ fontSize: 10.5 }}>🤖 ведёт</span>}
                      <button className="btn sm ghost" onClick={() => openConversation(l.conversation_id)}>Открыть</button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <Bucket title="🔴 Просрочено" items={b.overdue} cls="text-danger" />
      <Bucket title="🟡 Сегодня" items={b.today} />
      <Bucket title="📅 Завтра" items={b.tomorrow} />
      <Bucket title="🗓 На неделе" items={b.week} />
      <Bucket title="Позже" items={b.later} />

      {totalTasks === 0 && board.no_task_leads.length === 0 && (
        <div className="empty">Всё под контролем — задач нет и лидов без задачи нет 🎉</div>
      )}
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
                <td><span className="chip">{a.executor === 'bot' ? '🤖 бот' : '👤 человек'}</span></td>
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
      </div>
      {open && (
        <>
          <div className="faint" style={{ fontSize: 11.5, margin: '6px 0 8px' }}>
            Молчат больше суток. «Подготовить» — бот напишет дожим каждому (ПОД КОНТРОЛЕМ — само не уходит).
            Проверь и отправь точечно «✅ Отправить» или сразу «всем готовым».
          </div>
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
                  </div>
                  {l.draft
                    ? <div className="faint" style={{ fontSize: 11.5, marginTop: 2, fontStyle: 'italic' }}>✍️ «{l.draft.slice(0, 120)}{l.draft.length > 120 ? '…' : ''}»</div>
                    : <div className="faint" style={{ fontSize: 11.5, marginTop: 2 }}>нет черновика — нажми «Подготовить»</div>}
                </div>
                <div className="c-meta">
                  <div style={{ display: 'flex', gap: 5 }}>
                    {l.has_draft && <button className="btn sm primary" disabled={!!busy} onClick={() => sendOne(l.conversation_id)}>✅ Отправить</button>}
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
