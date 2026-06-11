import { useState } from 'react'
import { api } from '../api/client'
import { ConvSummary } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useDrawer } from '../components/DrawerContext'
import { CHANNEL_META, STAGES, LOST_REASONS, TEMP_META, fmtAgo, initials } from '../lib'

/* Канбан-воронка: колонки = 8 стадий, drag → подтверждение → stage override
   (зеркалится в HubSpot через очередь). Клик по карточке → drawer переписки. */

interface PendingMove {
  conv: ConvSummary
  toStage: string
}

export function Funnel() {
  const [items, setItems] = useState<ConvSummary[]>([])
  const [loaded, setLoaded] = useState(false)
  const [dragOver, setDragOver] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingMove | null>(null)
  const [lostReason, setLostReason] = useState('delayed')
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const { openConversation } = useDrawer()

  const load = async () => {
    try {
      const r = await api.get<{ items: ConvSummary[] }>('/conversations?limit=200')
      setItems(r.items)
      setLoaded(true)
    } catch { /* ignore */ }
  }

  usePolling(load, 15000)

  const showToast = (t: string) => { setToast(t); setTimeout(() => setToast(null), 3000) }

  const confirmMove = async () => {
    if (!pending || busy) return
    setBusy(true)
    try {
      await api.post(`/conversations/${pending.conv.id}/stage`, {
        to_stage: pending.toStage,
        lost_reason: pending.toStage === 'lost' ? lostReason : undefined,
      })
      showToast(`✅ ${pending.conv.customer.name || 'Лид'} → ${STAGES.find(s => s.stage === pending.toStage)?.label}`)
      setPending(null)
      await load()
    } catch (e: any) {
      showToast(`Ошибка: ${e.message}`)
    } finally { setBusy(false) }
  }

  const byStage = (stage: string) => items.filter(c => c.lead_stage === stage)
  const known = new Set(STAGES.map(s => s.stage))
  const other = items.filter(c => !known.has(c.lead_stage))

  const renderCard = (c: ConvSummary) => {
    const ch = CHANNEL_META[c.channel]
    const temp = TEMP_META[c.customer.lead_temperature]
    return (
      <div
        key={c.id}
        className="kb-card"
        draggable
        onDragStart={e => e.dataTransfer.setData('text/conv', JSON.stringify({ id: c.id }))}
        onClick={() => openConversation(c.id)}
      >
        <div className="kc-name">
          <span style={{ color: 'var(--accent)', fontWeight: 700, fontSize: 11 }}>{initials(c.customer.name)}</span>
          {c.customer.name || 'Без имени'}
          <span className="faint" style={{ marginLeft: 'auto', fontWeight: 400 }}>{ch?.icon}</span>
        </div>
        <div className="kc-preview">{c.preview || '—'}</div>
        <div className="kc-meta">
          {temp && <span className={`chip ${temp.cls}`}>{temp.label}</span>}
          <span className="chip">скор {c.customer.lead_score}</span>
          <span className="chip">{fmtAgo(c.last_message_at)}</span>
          {c.operator_takeover && <span className="chip ok">👤</span>}
        </div>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Воронка</h1>
        <span className="sub">{loaded ? `${items.length} сделок · перетащите карточку, чтобы сменить стадию` : '…'}</span>
      </div>

      <div className="kanban">
        {STAGES.map(s => {
          const cards = byStage(s.stage)
          return (
            <div
              key={s.stage}
              className={`kb-col${dragOver === s.stage ? ' drag-over' : ''}`}
              onDragOver={e => { e.preventDefault(); setDragOver(s.stage) }}
              onDragLeave={() => setDragOver(d => (d === s.stage ? null : d))}
              onDrop={e => {
                e.preventDefault()
                setDragOver(null)
                try {
                  const { id } = JSON.parse(e.dataTransfer.getData('text/conv'))
                  const conv = items.find(c => c.id === id)
                  if (conv && conv.lead_stage !== s.stage) setPending({ conv, toStage: s.stage })
                } catch { /* чужой drag */ }
              }}
            >
              <div className="k-head">
                {s.label}
                <span className="k-count">{cards.length}</span>
              </div>
              <div className="k-body">
                {cards.map(renderCard)}
                {cards.length === 0 && <div className="empty" style={{ padding: '18px 0', fontSize: 12 }}>пусто</div>}
              </div>
            </div>
          )
        })}
        {other.length > 0 && (
          <div className="kb-col">
            <div className="k-head">Другое <span className="k-count">{other.length}</span></div>
            <div className="k-body">{other.map(renderCard)}</div>
          </div>
        )}
      </div>

      {pending && (
        <>
          <div className="drawer-overlay" onClick={() => setPending(null)} />
          <div className="card" style={{
            position: 'fixed', top: '40%', left: '50%', transform: 'translate(-50%, -50%)',
            zIndex: 50, width: 380, display: 'flex', flexDirection: 'column', gap: 12,
          }}>
            <b>Перевести «{pending.conv.customer.name || 'Без имени'}»?</b>
            <div className="muted" style={{ fontSize: 13 }}>
              {STAGES.find(s => s.stage === pending.conv.lead_stage)?.label ?? pending.conv.lead_stage}
              {' → '}
              {STAGES.find(s => s.stage === pending.toStage)?.label}
            </div>
            {pending.toStage === 'lost' && (
              <select value={lostReason} onChange={e => setLostReason(e.target.value)}>
                {LOST_REASONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
              </select>
            )}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setPending(null)}>Отмена</button>
              <button className="btn primary" onClick={confirmMove} disabled={busy}>
                {busy ? <span className="spin" /> : 'Перевести'}
              </button>
            </div>
          </div>
        </>
      )}

      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
