import { useEffect, useState } from 'react'

/* Контекстное обучение ПО ЗАПРОСУ: вместо всегда-висящей плашки — маленькая
   кнопка «📚 Обучение разделу» в шапке каждой страницы. Нажал → развернулось
   объяснение этого раздела; свернул → снова кнопка. Не мешает опытному, но всегда
   под рукой. Общий тур по всему интерфейсу — отдельно (Настройки → «Запустить
   обучение», Tour.tsx). Глобальный выкл (Настройки → Подсказки) прячет кнопки совсем. */

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
  const [open, setOpen] = useState(false)
  const [enabled, setEnabled] = useState(hintsEnabled())

  // Реагируем на переключатель в Настройках без перезагрузки.
  useEffect(() => {
    const onChange = () => setEnabled(hintsEnabled())
    window.addEventListener('hints-changed', onChange)
    return () => window.removeEventListener('hints-changed', onChange)
  }, [])

  if (!enabled) return null

  // Свёрнуто (по умолчанию) — маленькая кнопка, не мешает.
  if (!open) {
    return (
      <button
        className="btn sm ghost"
        onClick={() => setOpen(true)}
        title="Как работает этот раздел — короткое обучение"
        data-hint={id}
        style={{ marginBottom: 12, fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 6 }}
      >
        {icon ?? '📚'} Обучение разделу
      </button>
    )
  }

  // Развёрнуто — объяснение раздела.
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 10,
      background: 'var(--accent-soft)', border: '1px solid rgba(124,108,255,0.25)',
      borderRadius: 10, padding: '10px 14px', marginBottom: 14, fontSize: 13,
      color: 'var(--text-dim)', lineHeight: 1.5,
    }}>
      <span style={{ fontSize: 16, lineHeight: 1.3 }}>{icon ?? '💡'}</span>
      <div style={{ flex: 1 }}>{children}</div>
      <button className="btn sm ghost" title="Свернуть" onClick={() => setOpen(false)}>✕</button>
    </div>
  )
}
