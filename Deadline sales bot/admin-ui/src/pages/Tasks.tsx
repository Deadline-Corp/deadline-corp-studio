import { useState } from 'react'
import { api } from '../api/client'
import { ScheduledActionItem } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useDrawer } from '../components/DrawerContext'
import { CHANNEL_META, fmtTime } from '../lib'

/* Задачи: отложенные действия бота (followup/прогрев/напоминания) +
   ручной запуск крон-свипа. */

const TYPE_LABELS: Record<string, string> = {
  followup_message: '📨 Написать лиду',
  warming_touch: '🔥 Прогрев',
  operator_callback: '👤 Менеджеру связаться',
  escalation: '🚨 Эскалация',
}

export function Tasks() {
  const [status, setStatus] = useState('pending')
  const [items, setItems] = useState<ScheduledActionItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const { openConversation } = useDrawer()

  const showToast = (t: string) => { setToast(t); setTimeout(() => setToast(null), 4000) }

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
    try {
      await api.post(`/scheduled-actions/${id}/cancel`)
      showToast('Отменено')
      await load()
    } catch (e: any) { showToast(`Ошибка: ${e.message}`) }
    finally { setBusy(false) }
  }

  const sweep = async () => {
    setBusy(true)
    showToast('Прогоняю крон…')
    try {
      const r = await api.post<any>('/cron/sweep')
      const sent = r.followups?.sent ?? 0
      showToast(`Готово: followups sent=${sent}, sweep=${JSON.stringify(r.sweep).slice(0, 80)}`)
      await load()
    } catch (e: any) { showToast(`Ошибка: ${e.message}`) }
    finally { setBusy(false) }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Задачи</h1>
        <span className="sub">{loaded ? `${items.length} в статусе «${status}»` : '…'}</span>
        <div className="spacer" />
        <select value={status} onChange={e => setStatus(e.target.value)}>
          <option value="pending">Ожидают</option>
          <option value="done">Выполнены</option>
          <option value="failed">Ошибки</option>
          <option value="cancelled">Отменены</option>
        </select>
        <button className="btn" onClick={sweep} disabled={busy}>▶ Прогнать крон сейчас</button>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'auto' }}>
        <table className="tbl">
          <thead>
            <tr>
              <th>Когда</th><th>Тип</th><th>Лид</th><th>Канал</th><th>Текст</th><th>Кто</th><th></th>
            </tr>
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

      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
