/* Общие словари и форматтеры — единый язык всех вью. */

export const CHANNEL_META: Record<string, { icon: string; label: string; cls: string }> = {
  website: { icon: '🌐', label: 'Сайт', cls: 'info' },
  telegram: { icon: '✈️', label: 'Telegram', cls: 'accent' },
  instagram: { icon: '📸', label: 'Instagram', cls: 'danger' },
  messenger: { icon: '💬', label: 'Messenger', cls: 'ok' },
  whatsapp: { icon: '🟢', label: 'WhatsApp', cls: 'ok' },
}

/** Откуда именно реплика/диалог: DM или коммент (по extra_meta.kind). */
export function sourceLabel(channel: string, extraMeta?: any): string {
  const m = CHANNEL_META[channel]
  const base = m ? `${m.icon} ${m.label}` : channel
  const kind = extraMeta?.kind
  if (kind === 'comment' || kind === 'ig_comment' || kind === 'fb_comment') return `${base} · коммент`
  return base
}

export const STAGES: Array<{ stage: string; label: string }> = [
  { stage: 'new_lead', label: '🆕 Новый лид' },
  { stage: 'in_dialog', label: '💬 В диалоге' },
  { stage: 'qualified', label: '✅ Квалифицирован' },
  { stage: 'on_call', label: '📞 Созвон назначен' },
  { stage: 'proposal', label: '📄 КП' },
  { stage: 'prepayment', label: '💰 Аванс' },
  { stage: 'completed_won', label: '🏁 Сдано' },
  { stage: 'lost', label: '❌ Не сложилось' },
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

/* ---------- Шина мгновенного обновления списков ----------
   Любая мутация лида (стадия / архив / «убрать») шлёт событие — активные вью
   (Воронка / Переписки / Канвас) перезагружаются сразу, не дожидаясь поллинга. */
export const LEADS_CHANGED = 'deadline:leads-changed'
export function emitLeadsChanged(): void { window.dispatchEvent(new Event(LEADS_CHANGED)) }
export function onLeadsChanged(fn: () => void): () => void {
  window.addEventListener(LEADS_CHANGED, fn)
  return () => window.removeEventListener(LEADS_CHANGED, fn)
}

/* «Убрали лид» → глобальный тост «Вернуть» (один на всё приложение, рендерится в Layout). */
export interface DismissedLead { id: string; stage: string; label: string }
export const LEAD_DISMISSED = 'deadline:lead-dismissed'
export function emitLeadDismissed(d: DismissedLead): void {
  window.dispatchEvent(new CustomEvent<DismissedLead>(LEAD_DISMISSED, { detail: d }))
}
export function onLeadDismissed(fn: (d: DismissedLead) => void): () => void {
  const h = (e: Event) => fn((e as CustomEvent<DismissedLead>).detail)
  window.addEventListener(LEAD_DISMISSED, h)
  return () => window.removeEventListener(LEAD_DISMISSED, h)
}
