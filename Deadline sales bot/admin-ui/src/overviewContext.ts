import { createContext, useContext } from 'react'
import { Overview, FunnelStage } from './api/types'

/* Overview-контекст в отдельном модуле — чтобы Drawer/страницы могли его
   использовать без циклического импорта Layout. */

export const OverviewCtx = createContext<Overview | null>(null)

export function useOverview() {
  return useContext(OverviewCtx)
}

/* Динамические стадии воронки (кастомные или встроенные) c фоллбэком,
   пока overview не загрузился. */
const FALLBACK: FunnelStage[] = [
  { stage: 'new_lead', label: '🆕 Новый лид', kind: 'active', count: 0 },
  { stage: 'in_dialog', label: '💬 В диалоге', kind: 'active', count: 0 },
  { stage: 'qualified', label: '✅ Квалифицирован', kind: 'active', count: 0 },
  { stage: 'on_call', label: '📞 Созвон назначен', kind: 'active', count: 0 },
  { stage: 'proposal', label: '📄 КП', kind: 'active', count: 0 },
  { stage: 'prepayment', label: '💰 Аванс', kind: 'active', count: 0 },
  { stage: 'completed_won', label: '🏁 Сдано', kind: 'won', count: 0 },
  { stage: 'lost', label: '❌ Проигран', kind: 'lost', count: 0 },
]

export function useStages(): FunnelStage[] {
  const ov = useOverview()
  return ov?.funnel.stages?.length ? ov.funnel.stages : FALLBACK
}

export function useStageLabel(): (key: string) => string {
  const stages = useStages()
  return (key: string) => stages.find(s => s.stage === key)?.label ?? key
}
