import { ConvSummary } from '../api/types'

/* «Следующий шаг» по лиду — порт ключевой дисциплины amoCRM: у каждого активного
   лида видно состояние ближайшей задачи прямо на карточке (воронка/переписки).
   🟠 нет шага · 🔴 просрочено N дн · 🟢 на сегодня · будущее → не мусорим (под контролем).
   Для завершённых стадий (lost/won) шаг не требуем — индикатор скрыт. */

type NS = ConvSummary['next_step']

const MAP: Record<string, { dot: string; bg: string; fg: string; label: (n?: number) => string; title: string }> = {
  none: { dot: '#e0922b', bg: 'rgba(224,146,43,0.13)', fg: '#b6791f', label: () => 'нет шага', title: 'Нет следующего шага — поставь задачу' },
  overdue: { dot: '#e0524f', bg: 'rgba(224,82,79,0.14)', fg: '#c0392b', label: n => `просрочено ${n ?? 0} дн`, title: 'Задача просрочена' },
  today: { dot: '#3bb4a0', bg: 'rgba(59,180,160,0.14)', fg: '#1a8c6d', label: () => 'на сегодня', title: 'Задача на сегодня' },
}

export function NextStep({ ns, stage, compact }: { ns: NS; stage?: string; compact?: boolean }) {
  if (!ns) return null
  if (stage === 'lost' || stage === 'won') return null  // у завершённых шага не требуем
  if (ns.status === 'future') return null               // срок в будущем — под контролем, не мусорим
  const m = MAP[ns.status]
  if (!m) return null
  const text = m.label(ns.overdue_days)
  // compact (для плотных карточек воронки) — только цветная точка + число дней просрочки.
  if (compact) {
    return (
      <span title={m.title} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 10.5, fontWeight: 700, color: m.fg }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: m.dot, display: 'inline-block' }} />
        {ns.status === 'overdue' ? ns.overdue_days ?? 0 : ns.status === 'none' ? '!' : ''}
      </span>
    )
  }
  return (
    <span className="chip" title={m.title}
          style={{ background: m.bg, color: m.fg, fontWeight: 600, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: m.dot, display: 'inline-block' }} />
      {text}
    </span>
  )
}
