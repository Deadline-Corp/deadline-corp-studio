import { useState } from 'react'
import { api } from '../api/client'
import { ScheduledActionItem, TodayView, TodayItem } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useDrawer } from '../components/DrawerContext'
import { CHANNEL_META, fmtTime, fmtAgo } from '../lib'

/* Задачи: вкладка «Мой день» (просрочено → сегодня → ближайшее + созвоны —
   паттерн HubSpot/Kommo) и «Все задачи» (полный список). */

const TYPE_LABELS: Record<string, string> = {
  followup_message: '📨 Написать лиду',
  warming_touch: '🔥 Прогрев',
  operator_callback: '👤 Связаться / сделать',
  escalation: '🚨 Эскалация',
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
      {tab === 'day' ? <MyDay showToast={showToast} /> : <AllTasks showToast={showToast} />}
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}

/* ---------- Мой день ---------- */

function MyDay({ showToast }: { showToast: (t: string) => void }) {
  const [view, setView] = useState<TodayView | null>(null)
  const [busy, setBusy] = useState(false)
  const { openConversation } = useDrawer()

  const load = async () => {
    try { setView(await api.get<TodayView>('/today')) } catch { /* ignore */ }
  }
  usePolling(load, 20000)

  const done = async (id: string) => {
    setBusy(true)
    try { await api.post(`/scheduled-actions/${id}/done`); showToast('✅ Сделано'); await load() }
    catch (e: any) { showToast(`Ошибка: ${e.message}`) }
    finally { setBusy(false) }
  }
  const cancel = async (id: string) => {
    setBusy(true)
    try { await api.post(`/scheduled-actions/${id}/cancel`); showToast('Отменено'); await load() }
    catch (e: any) { showToast(`Ошибка: ${e.message}`) }
    finally { setBusy(false) }
  }

  const sweep = async () => {
    setBusy(true)
    showToast('Прогоняю крон…')
    try {
      const r = await api.post<any>('/cron/sweep')
      showToast(`Готово: бот отправил ${r.followups?.sent ?? 0}`)
      await load()
    } catch (e: any) { showToast(`Ошибка: ${e.message}`) }
    finally { setBusy(false) }
  }

  const renderItem = (it: TodayItem, zone: 'overdue' | 'today' | 'upcoming') => (
    <div className="conv-row" key={it.id} style={{ cursor: 'default' }}>
      <span style={{ fontSize: 18 }}>{it.executor === 'bot' ? '🤖' : '👤'}</span>
      <div className="c-main" style={{ cursor: it.conversation_id ? 'pointer' : 'default' }}
           onClick={() => it.conversation_id && openConversation(it.conversation_id)}>
        <div className="c-name">
          {it.customer.name || it.customer.email || 'Лид'}
          <span className="chip">{TYPE_LABELS[it.action_type] ?? it.action_type}</span>
          <span className="faint" style={{ fontWeight: 400 }}>{CHANNEL_META[it.channel]?.icon}</span>
        </div>
        <div className="c-preview">{it.text || '—'}</div>
      </div>
      <div className="c-meta">
        <span className={`chip ${zone === 'overdue' ? 'danger' : zone === 'today' ? 'warn' : ''}`}>
          {zone === 'overdue' ? `просрочено ${fmtAgo(it.due_at)}` : fmtTime(it.due_at)}
        </span>
        <div style={{ display: 'flex', gap: 5 }}>
          {it.executor === 'human' && <button className="btn sm" onClick={() => done(it.id)} disabled={busy}>✓ Сделано</button>}
          <button className="btn sm ghost" onClick={() => cancel(it.id)} disabled={busy}>✕</button>
        </div>
      </div>
    </div>
  )

  if (!view) return <div className="empty"><span className="spin" /> Загрузка…</div>

  const Section = ({ title, items, zone, cls }: { title: string; items: TodayItem[]; zone: any; cls?: string }) => (
    items.length === 0 ? null : (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <b style={{ fontSize: 13 }} className={cls}>{title} ({items.length})</b>
        {items.map(it => renderItem(it, zone))}
      </div>
    )
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span className="muted" style={{ fontSize: 13, flex: 1 }}>
          Бот выполняет 🤖-задачи сам по крону; 👤-задачи закрываете вы. Поставить задачу — из карточки лида (кнопка «📋 Задача»).
        </span>
        <button className="btn sm" onClick={sweep} disabled={busy}>▶ Прогнать крон</button>
      </div>

      {view.calls.length > 0 && (
        <div className="card" style={{ padding: 12 }}>
          <b style={{ fontSize: 13 }}>📞 Созвоны на неделе ({view.calls.length})</b>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
            {view.calls.map((c, i) => (
              <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 13, cursor: 'pointer' }}
                   onClick={() => openConversation(c.conversation_id)}>
                <span className="chip accent">{c.call_at ? fmtTime(c.call_at) : 'время не зафиксировано'}</span>
                <span>{c.customer.name || c.customer.email || 'Лид'}</span>
                {c.medium && <span className="chip">{c.medium}</span>}
                <span className="faint">{CHANNEL_META[c.channel]?.icon}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <Section title="🔴 Просрочено" items={view.overdue} zone="overdue" />
      <Section title="🟡 Сегодня" items={view.today} zone="today" />
      <Section title="📅 Ближайшие 7 дней" items={view.upcoming} zone="upcoming" />

      {view.overdue.length + view.today.length + view.upcoming.length === 0 && (
        <div className="empty">На сегодня задач нет — всё чисто 🎉</div>
      )}
    </div>
  )
}

/* ---------- Все задачи ---------- */

function AllTasks({ showToast }: { showToast: (t: string) => void }) {
  const [status, setStatus] = useState('pending')
  const [items, setItems] = useState<ScheduledActionItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const { openConversation } = useDrawer()

  const load = async () => {
    try {
      const r = await api.get<{ items: ScheduledActionItem[] }>(`/scheduled-actions?status=${status}`)
      setItems(r.items)
      setLoaded(true)
    } catch { /* ignore */ }
  }
  usePolling(load, 30000, [status])

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
        <span className="sub" style={{ alignSelf: 'center' }}>{loaded ? `${items.length} шт.` : '…'}</span>
      </div>
      <div className="card" style={{ padding: 0, overflow: 'auto' }}>
        <table className="tbl">
          <thead>
            <tr><th>Когда</th><th>Тип</th><th>Лид</th><th>Канал</th><th>Текст</th><th>Кто</th><th></th></tr>
          </thead>
          <tbody>
            {items.map(a => (
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
        {loaded && items.length === 0 && <div className="empty">Пусто</div>}
      </div>
    </>
  )
}
