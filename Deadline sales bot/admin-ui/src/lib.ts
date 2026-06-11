/* Общие словари и форматтеры — единый язык всех вью. */

export const CHANNEL_META: Record<string, { icon: string; label: string }> = {
  website: { icon: '🌐', label: 'Сайт' },
  telegram: { icon: '✈️', label: 'Telegram' },
  instagram: { icon: '📸', label: 'Instagram' },
  messenger: { icon: '💬', label: 'Messenger' },
}

export const STAGES: Array<{ stage: string; label: string }> = [
  { stage: 'new_lead', label: '🆕 Новый лид' },
  { stage: 'in_dialog', label: '💬 В диалоге' },
  { stage: 'qualified', label: '✅ Квалифицирован' },
  { stage: 'on_call', label: '📞 Созвон назначен' },
  { stage: 'proposal', label: '📄 КП' },
  { stage: 'prepayment', label: '💰 Аванс' },
  { stage: 'completed_won', label: '🏁 Сдано' },
  { stage: 'lost', label: '❌ Проигран' },
]

export const LOST_REASONS: Array<{ value: string; label: string }> = [
  { value: 'price', label: 'Цена' },
  { value: 'not_our_format', label: 'Не наш формат' },
  { value: 'competitor', label: 'Ушёл к конкуренту' },
  { value: 'delayed', label: 'Пропал / отложил' },
  { value: 'no_budget', label: 'Нет бюджета' },
  { value: 'hard_stop', label: 'Жёсткий отказ' },
]

export function stageLabel(stage: string): string {
  return STAGES.find(s => s.stage === stage)?.label ?? stage
}

export const TEMP_META: Record<string, { label: string; cls: string }> = {
  cold: { label: '🧊 cold', cls: 'info' },
  warm: { label: '🌤 warm', cls: 'warn' },
  hot: { label: '🔥 hot', cls: 'danger' },
  ready: { label: '🚀 ready', cls: 'ok' },
  client: { label: '🤝 client', cls: 'ok' },
  frozen: { label: '❄️ frozen', cls: '' },
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const hm = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  if (sameDay) return hm
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) + ' ' + hm
}

export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  const m = Math.floor(ms / 60000)
  if (m < 1) return 'только что'
  if (m < 60) return `${m} мин`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} ч`
  return `${Math.floor(h / 24)} дн`
}

export function initials(name: string | null | undefined): string {
  if (!name) return '?'
  return name.trim().split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() ?? '').join('')
}
