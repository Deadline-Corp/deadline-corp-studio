import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { ConvDetail, Msg } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { CHANNEL_META, STAGES, LOST_REASONS, TEMP_META, stageLabel, fmtTime, initials } from '../lib'

/* Карточка лида: переписка + ответ + takeover + стадия + пинок.
   Один и тот же компонент из Inbox, Канбана и Канваса. */

export function ConversationDrawer({ convId, onClose }: { convId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ConvDetail | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null)
  const [stagePick, setStagePick] = useState('')
  const [lostReason, setLostReason] = useState('delayed')
  const [nudgeOpen, setNudgeOpen] = useState(false)
  const msgsRef = useRef<HTMLDivElement>(null)
  const lastTsRef = useRef<string | null>(null)

  const showToast = (text: string, err = false) => {
    setToast({ text, err })
    setTimeout(() => setToast(null), 3500)
  }

  const loadDetail = async () => {
    try { setDetail(await api.get<ConvDetail>(`/conversations/${convId}`)) } catch { /* drawer закроют по 401 */ }
  }

  const loadMessages = async (initial = false) => {
    try {
      if (initial || !lastTsRef.current) {
        const r = await api.get<{ items: Msg[] }>(`/conversations/${convId}/messages?limit=80`)
        setMsgs(r.items)
        lastTsRef.current = r.items.length ? r.items[r.items.length - 1].created_at : null
        scrollDown()
      } else {
        const r = await api.get<{ items: Msg[] }>(
          `/conversations/${convId}/messages?after=${encodeURIComponent(lastTsRef.current)}`)
        if (r.items.length) {
          setMsgs(prev => [...prev, ...r.items])
          lastTsRef.current = r.items[r.items.length - 1].created_at
          scrollDown()
        }
      }
    } catch { /* поллинг переживёт разовый сбой */ }
  }

  const scrollDown = () => {
    requestAnimationFrame(() => {
      msgsRef.current?.scrollTo({ top: msgsRef.current.scrollHeight })
    })
  }

  useEffect(() => {
    lastTsRef.current = null
    setMsgs([])
    setDetail(null)
    void loadDetail()
    void loadMessages(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId])

  usePolling(() => loadMessages(false), 5000, [convId])

  const send = async () => {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true)
    try {
      const r = await api.post<{ delivered: boolean; channel: string }>(`/conversations/${convId}/reply`, { text: t })
      setText('')
      if (!r.delivered) showToast('⚠️ Сохранено, но НЕ доставлено лиду (см. логи)', true)
      else if (r.channel === 'website') showToast('Сохранено. Website-лид увидит при следующем визите.')
      else showToast('✅ Доставлено лиду')
      await loadMessages(false)
    } catch (e: any) {
      showToast(`Ошибка: ${e.message}`, true)
    } finally { setBusy(false) }
  }

  const toggleTakeover = async () => {
    if (!detail || busy) return
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/takeover`, { on: !detail.operator_takeover })
      showToast(detail.operator_takeover ? '🤖 Вернули боту' : '👤 Взяли на себя — бот молчит')
      await loadDetail()
    } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const applyStage = async () => {
    if (!stagePick || !detail || busy) return
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/stage`, {
        to_stage: stagePick,
        lost_reason: stagePick === 'lost' ? lostReason : undefined,
      })
      showToast(`Стадия → ${stageLabel(stagePick)}`)
      setStagePick('')
      await loadDetail()
      await loadMessages(false)
    } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const nudgeDraft = async () => {
    setBusy(true)
    try {
      const r = await api.post<{ draft: string }>(`/conversations/${convId}/nudge`, { mode: 'draft' })
      setText(r.draft)
      setNudgeOpen(false)
      showToast('Черновик пинка готов — правьте и отправляйте')
    } catch (e: any) { showToast(`Ошибка черновика: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const nudgeNow = async () => {
    const t = text.trim()
    if (!t) { showToast('Сначала напишите текст пинка (или возьмите черновик)', true); return }
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/nudge`, { mode: 'now', text: t })
      setText('')
      setNudgeOpen(false)
      showToast('✅ Пинок отправлен от имени бота')
      await loadMessages(false)
    } catch (e: any) { showToast(`${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  const ch = detail ? CHANNEL_META[detail.channel] : null
  const temp = detail ? TEMP_META[detail.customer.lead_temperature] : null

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <div className="drawer">
        <div className="d-head">
          <div className="d-title">
            <div className="avatar" style={{ width: 38, height: 38, borderRadius: '50%', background: 'var(--panel-2)', display: 'grid', placeItems: 'center', color: 'var(--accent)', fontWeight: 700 }}>
              {initials(detail?.customer.name)}
            </div>
            <h2>{detail?.customer.name || 'Без имени'}</h2>
            <button className="btn ghost" onClick={onClose}>✕</button>
          </div>
          {detail && (
            <>
              <div className="d-chips">
                <span className="chip">{ch?.icon} {ch?.label}</span>
                <span className="chip accent">{stageLabel(detail.lead_stage)}</span>
                {temp && <span className={`chip ${temp.cls}`}>{temp.label}</span>}
                <span className="chip">скор {detail.customer.lead_score}</span>
                {detail.operator_takeover && <span className="chip ok">👤 на операторе</span>}
                {detail.customer.email && <span className="chip mono">{detail.customer.email}</span>}
                {detail.customer.phone && <span className="chip mono">{detail.customer.phone}</span>}
              </div>
              <div className="d-actions">
                <button className="btn sm" onClick={toggleTakeover} disabled={busy}>
                  {detail.operator_takeover ? '🤖 Вернуть боту' : '👤 Взять на себя'}
                </button>
                <select value={stagePick} onChange={e => setStagePick(e.target.value)} style={{ padding: '4px 8px', fontSize: 12 }}>
                  <option value="">Сменить стадию…</option>
                  {STAGES.filter(s => s.stage !== detail.lead_stage).map(s => (
                    <option key={s.stage} value={s.stage}>{s.label}</option>
                  ))}
                </select>
                {stagePick === 'lost' && (
                  <select value={lostReason} onChange={e => setLostReason(e.target.value)} style={{ padding: '4px 8px', fontSize: 12 }}>
                    {LOST_REASONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </select>
                )}
                {stagePick && <button className="btn sm primary" onClick={applyStage} disabled={busy}>OK</button>}
                <button className="btn sm" onClick={() => setNudgeOpen(v => !v)}>⚡ Пинок</button>
                {detail.hubspot.contact_url && (
                  <a className="btn sm ghost" href={detail.hubspot.contact_url} target="_blank" rel="noreferrer">HubSpot ↗</a>
                )}
              </div>
              {nudgeOpen && (
                <div className="d-actions" style={{ background: 'var(--panel)', borderRadius: 8, padding: '8px 10px' }}>
                  <span className="muted" style={{ fontSize: 12 }}>Пинок зависшему лиду (уйдёт от имени бота):</span>
                  <button className="btn sm" onClick={nudgeDraft} disabled={busy}>🪄 Черновик от LLM</button>
                  <button className="btn sm primary" onClick={nudgeNow} disabled={busy}>Отправить сейчас</button>
                </div>
              )}
            </>
          )}
        </div>

        <div className="d-msgs" ref={msgsRef}>
          {msgs.length === 0 && <div className="empty">Сообщений пока нет</div>}
          {msgs.map(m => (
            <div key={m.id} className={`msg ${m.role}`}>
              {m.content}
              <div className="m-meta">
                {m.role === 'operator' && '👤 оператор · '}
                {m.role === 'assistant' && m.extra_meta?.kind === 'manual_nudge' && '⚡ ручной пинок · '}
                {fmtTime(m.created_at)}
              </div>
            </div>
          ))}
        </div>

        <div className="d-reply">
          <textarea
            placeholder="Ответить лиду как оператор… (Ctrl+Enter — отправить)"
            value={text}
            onChange={e => setText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send() }}
          />
          <div className="r-row">
            <span className="faint" style={{ fontSize: 11.5, flex: 1 }}>
              {detail?.channel === 'website'
                ? 'Website-канал: лид увидит ответ при следующем заходе в виджет'
                : 'Уйдёт лиду в его канал + отметится в Telegram-форуме'}
            </span>
            <button className="btn primary" onClick={send} disabled={busy || !text.trim()}>
              {busy ? <span className="spin" /> : 'Отправить'}
            </button>
          </div>
        </div>

        {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.text}</div>}
      </div>
    </>
  )
}
