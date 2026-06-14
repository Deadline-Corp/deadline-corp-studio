import { useEffect, useRef, useState } from 'react'
import { hintsEnabled } from './HintBar'

/* ⓘ-пояснение функции: ненавязчивый значок рядом с кнопкой/полем.
   Навёл (или тапнул) — всплыло окошко «что это и зачем», ушёл — закрылось.
   Уважает глобальный переключатель подсказок (Настройки → 💡):
   выключил — значки исчезают совсем, интерфейс чистый. */

export function Help({ text, title }: { text: string; title?: string }) {
  const [open, setOpen] = useState(false)
  const [enabled, setEnabled] = useState(hintsEnabled())
  const ref = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const onChange = () => setEnabled(hintsEnabled())
    window.addEventListener('hints-changed', onChange)
    return () => window.removeEventListener('hints-changed', onChange)
  }, [])

  // Клик мимо — закрыть (для тач-устройств, где открыли тапом).
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as any)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  if (!enabled) return null

  return (
    <span
      ref={ref}
      style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <span
        onClick={e => { e.stopPropagation(); setOpen(v => !v) }}
        style={{
          width: 15, height: 15, borderRadius: '50%',
          border: '1px solid var(--border-strong)',
          color: 'var(--text-faint)', fontSize: 10, fontWeight: 700,
          display: 'inline-grid', placeItems: 'center',
          cursor: 'help', userSelect: 'none', marginLeft: 5, flexShrink: 0,
        }}
      >?</span>
      {open && (
        <div style={{
          position: 'absolute', bottom: 'calc(100% + 8px)', left: '50%',
          transform: 'translateX(-50%)', zIndex: 95,
          width: 260, padding: '10px 12px',
          background: 'var(--panel-2)', border: '1px solid var(--accent-border)',
          borderRadius: 10, boxShadow: '0 10px 30px rgba(0,0,0,0.5)',
          fontSize: 12, lineHeight: 1.5, color: 'var(--text-dim)',
          whiteSpace: 'normal', textAlign: 'left', fontWeight: 400,
        }}>
          {title && <b style={{ color: 'var(--text)', display: 'block', marginBottom: 3 }}>{title}</b>}
          {text}
        </div>
      )}
    </span>
  )
}
