import { useEffect, useState } from 'react'

/* Контекстное обучение: подсказка-плашка вверху каждой страницы — коротко
   объясняет, что это за окно и как с ним работать. Глобальный вкл/выкл
   (Настройки или «не показывать больше» прямо на плашке) + скрытие разово.
   Хранение в localStorage — не мешает опытному, всегда включается обратно. */

const HINTS_KEY = 'deadline_hints_enabled'

export function hintsEnabled(): boolean {
  return localStorage.getItem(HINTS_KEY) !== '0'
}

export function setHintsEnabled(on: boolean) {
  localStorage.setItem(HINTS_KEY, on ? '1' : '0')
  window.dispatchEvent(new Event('hints-changed'))
}

export function HintBar({ id, icon, children }: {
  id: string
  icon?: string
  children: React.ReactNode
}) {
  const [closed, setClosed] = useState(false)
  const [enabled, setEnabled] = useState(hintsEnabled())

  // Реагируем на переключатель в Настройках без перезагрузки.
  useEffect(() => {
    const onChange = () => setEnabled(hintsEnabled())
    window.addEventListener('hints-changed', onChange)
    return () => window.removeEventListener('hints-changed', onChange)
  }, [])

  if (!enabled || closed) return null

  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 10,
      background: 'var(--accent-soft)', border: '1px solid rgba(124,108,255,0.25)',
      borderRadius: 10, padding: '10px 14px', marginBottom: 14, fontSize: 13,
      color: 'var(--text-dim)', lineHeight: 1.5,
    }}>
      <span style={{ fontSize: 16, lineHeight: 1.3 }}>{icon ?? '💡'}</span>
      <div style={{ flex: 1 }}>{children}</div>
      <button className="btn sm ghost" title="Скрыть подсказку" onClick={() => setClosed(true)}>✕</button>
      <button className="btn sm ghost" style={{ whiteSpace: 'nowrap', fontSize: 11 }}
              title="Выключить все подсказки (вернуть: Настройки → Подсказки)"
              onClick={() => setHintsEnabled(false)}>
        больше не показывать
      </button>
    </div>
  )
}
