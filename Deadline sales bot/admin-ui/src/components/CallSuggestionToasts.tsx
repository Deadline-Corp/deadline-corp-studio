import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

/* Всплывающие уведомления о договорённостях о созвоне (бот распознал в переписке).
   Дублируют то, что в карточке. «✅ Создать» — создаёт событие (и убирает везде).
   «✕» — скрывает ТОЛЬКО всплывашку (в карточке остаётся ждать подтверждения). */

type Sugg = { id: string; name: string; when_human?: string; at?: string }

export function CallSuggestionToasts() {
  const [items, setItems] = useState<Sugg[]>([])
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState('')
  const nav = useNavigate()

  const load = async () => {
    try { const r = await api.get<{ items: Sugg[] }>('/whatsapp/pending-suggestions'); setItems(r.items || []) }
    catch { /* ignore */ }
  }
  useEffect(() => { void load(); const t = setInterval(() => void load(), 30000); return () => clearInterval(t) }, [])

  const confirm = async (id: string) => {
    setBusy(id)
    try { await api.post(`/conversations/${id}/call-suggestion`, { action: 'confirm' }); await load() }
    catch { /* ignore */ }
    finally { setBusy('') }
  }

  const visible = items.filter(i => !hidden.has(i.id))
  if (!visible.length) return null
  return (
    <div style={{ position: 'fixed', right: 16, bottom: 16, zIndex: 9999, display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 340 }}>
      {visible.map(i => (
        <div key={i.id} style={{ background: 'var(--panel)', border: '1px solid var(--accent-border)', borderRadius: 10, padding: '10px 12px', boxShadow: '0 6px 24px rgba(0,0,0,.35)' }}>
          <div style={{ fontSize: 12.5 }}><b>📅 Договорённость о созвоне</b></div>
          <div style={{ fontSize: 12.5, margin: '3px 0' }}>{i.name} — <b>{i.when_human}</b></div>
          <div style={{ display: 'flex', gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
            <button className="btn sm primary" disabled={busy === i.id} onClick={() => confirm(i.id)}>✅ Создать</button>
            <button className="btn sm" onClick={() => nav(`/inbox?open=${i.id}`)}>Открыть</button>
            <button className="btn sm ghost" title="Скрыть (останется в карточке)" onClick={() => setHidden(s => new Set([...s, i.id]))}>✕</button>
          </div>
        </div>
      ))}
    </div>
  )
}
