import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { ConvSummary, StageDef } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useDrawer } from '../components/DrawerContext'
import { useStages } from '../overviewContext'
import { CHANNEL_META, LOST_REASONS, TEMP_META, fmtAgo, initials } from '../lib'

/* Канбан-воронка: стадии динамические (своя CRM — настраиваются тут же),
   drag → подтверждение → stage override (встроенные стадии зеркалятся в
   HubSpot). Клик по карточке → drawer переписки. */

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
  const [editorOpen, setEditorOpen] = useState(false)
  const { openConversation } = useDrawer()
  const stages = useStages()

  const load = async () => {
    try {
      const r = await api.get<{ items: ConvSummary[] }>('/conversations?limit=200')
      setItems(r.items)
      setLoaded(true)
    } catch { /* ignore */ }
  }

  usePolling(load, 10000)

  const showToast = (t: string) => { setToast(t); setTimeout(() => setToast(null), 3000) }

  const isLostStage = (key: string) =>
    key === 'lost' || stages.find(s => s.stage === key)?.kind === 'lost'

  const confirmMove = async () => {
    if (!pending || busy) return
    setBusy(true)
    try {
      await api.post(`/conversations/${pending.conv.id}/stage`, {
        to_stage: pending.toStage,
        lost_reason: isLostStage(pending.toStage) ? lostReason : undefined,
      })
      showToast(`✅ ${pending.conv.customer.name || 'Лид'} → ${stages.find(s => s.stage === pending.toStage)?.label}`)
      setPending(null)
      await load()
    } catch (e: any) {
      showToast(`Ошибка: ${e.message}`)
    } finally { setBusy(false) }
  }

  const byStage = (stage: string) => items.filter(c => c.lead_stage === stage)
  const known = new Set(stages.map(s => s.stage))
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
        <div className="spacer" />
        <button className="btn" onClick={() => setEditorOpen(true)}>⚙ Настроить стадии</button>
      </div>

      <div className="kanban">
        {stages.map(s => {
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
              {stages.find(s => s.stage === pending.conv.lead_stage)?.label ?? pending.conv.lead_stage}
              {' → '}
              {stages.find(s => s.stage === pending.toStage)?.label}
            </div>
            {isLostStage(pending.toStage) && (
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

      {editorOpen && <StageEditor onClose={() => setEditorOpen(false)} onSaved={() => { setEditorOpen(false); showToast('✅ Стадии сохранены — канвас/канбан обновятся за полминуты') }} />}

      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}

/* ---------- Редактор стадий (своя CRM) ---------- */

function StageEditor({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [items, setItems] = useState<StageDef[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    void api.get<{ items: StageDef[] }>('/funnel/stages').then(r => setItems(r.items)).catch(() => setErr('Не загрузилось'))
  }, [])

  const move = (i: number, d: -1 | 1) => {
    if (!items) return
    const j = i + d
    if (j < 0 || j >= items.length) return
    const next = [...items]
    ;[next[i], next[j]] = [next[j], next[i]]
    setItems(next)
  }

  const update = (i: number, patch: Partial<StageDef>) => {
    if (!items) return
    setItems(items.map((it, k) => (k === i ? { ...it, ...patch } : it)))
  }

  const remove = (i: number) => {
    if (!items) return
    if (items[i].builtin) return
    setItems(items.filter((_, k) => k !== i))
  }

  const add = () => {
    if (!items) return
    setItems([...items, {
      id: null, key: '', label: 'Новая стадия', kind: 'active',
      position: items.length, active: true, builtin: false,
    }])
  }

  const save = async () => {
    if (!items || busy) return
    setBusy(true)
    setErr('')
    try {
      await api.post('/funnel/stages', {
        items: items.map(it => ({ key: it.key || undefined, label: it.label, kind: it.kind, active: it.active })),
      })
      onSaved()
    } catch (e: any) {
      setErr(typeof e.detail === 'string' ? e.detail : e.message)
    } finally { setBusy(false) }
  }

  const reset = async () => {
    if (busy) return
    setBusy(true)
    try {
      const r = await api.post<{ items: StageDef[] }>('/funnel/stages/reset')
      setItems(r.items)
      setErr('')
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <div className="card" style={{
        position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
        zIndex: 50, width: 560, maxHeight: '84vh', overflowY: 'auto',
        display: 'flex', flexDirection: 'column', gap: 10,
      }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <b>Стадии воронки</b>
          <div style={{ flex: 1 }} />
          <button className="btn ghost" onClick={onClose}>✕</button>
        </div>
        <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>
          Переименовывайте, меняйте порядок, добавляйте свои. Встроенные стадии (🔒) нельзя
          удалить — бот двигает сделки по ним сам; их можно скрыть «глазом». Встроенные
          зеркалятся в HubSpot, кастомные живут только здесь.
        </p>
        {!items && !err && <div className="empty"><span className="spin" /></div>}
        {items?.map((it, i) => (
          <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <button className="btn sm ghost" onClick={() => move(i, -1)} disabled={i === 0}>↑</button>
            <button className="btn sm ghost" onClick={() => move(i, 1)} disabled={i === items.length - 1}>↓</button>
            <input value={it.label} onChange={e => update(i, { label: e.target.value })}
                   style={{ flex: 1, opacity: it.active ? 1 : 0.45 }} />
            {!it.builtin && (
              <select value={it.kind} onChange={e => update(i, { kind: e.target.value as any })} style={{ fontSize: 12 }}>
                <option value="active">этап</option>
                <option value="won">✅ выигрыш</option>
                <option value="lost">❌ проигрыш</option>
              </select>
            )}
            <button className="btn sm ghost" title={it.active ? 'Скрыть с канбана' : 'Показать'}
                    onClick={() => update(i, { active: !it.active })}>
              {it.active ? '👁' : '🙈'}
            </button>
            {it.builtin
              ? <span title="Встроенная — бот на неё ссылается">🔒</span>
              : <button className="btn sm danger" onClick={() => remove(i)}>✕</button>}
          </div>
        ))}
        {err && <div style={{ color: 'var(--danger)', fontSize: 13 }}>{err}</div>}
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn sm" onClick={add}>+ Добавить стадию</button>
          <div style={{ flex: 1 }} />
          <button className="btn sm ghost" onClick={reset} disabled={busy}>↺ Сброс на заводские</button>
          <button className="btn sm primary" onClick={save} disabled={busy || !items}>
            {busy ? <span className="spin" /> : 'Сохранить'}
          </button>
        </div>
      </div>
    </>
  )
}
