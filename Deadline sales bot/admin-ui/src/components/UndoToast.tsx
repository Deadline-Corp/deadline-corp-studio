import { useEffect, useRef, useState } from 'react'
import { onLeadDismissed, DismissedLead } from '../lib'
import { restoreLead } from '../api/leads'

/* Глобальный тост «Убрано в архив · Вернуть» — ловит событие из любого вью
   (Воронка / Переписки / карточка лида). Авто-скрытие ~6 с. Один на всё приложение. */
export function UndoToast() {
  const [item, setItem] = useState<DismissedLead | null>(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => onLeadDismissed(d => {
    setItem(d)
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setItem(null), 6000)
  }), [])

  if (!item) return null

  const undo = async () => {
    setBusy(true)
    try { await restoreLead(item.id, item.stage) } catch { /* всё равно скрываем */ }
    finally { setBusy(false); setItem(null) }
  }

  return (
    <div className="toast undo-toast">
      <span>🗑 Убрано в архив: <b>{item.label}</b></span>
      <button className="btn sm" onClick={undo} disabled={busy}>↩ Вернуть</button>
    </div>
  )
}
