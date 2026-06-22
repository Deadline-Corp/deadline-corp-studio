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

/* ---------- Часовой пояс БИЗНЕСА (а не браузера) ----------
   Все времена в панели (задачи/календарь/созвоны) показываются и задаются в поясе
   бизнеса (настройка digest_tz_offset, дефолт +7 Пхукет). Раньше брался пояс ОС
   владельца → при заходе с устройства в другом поясе всё уезжало на часы.
   Значение гидрируется из /me при загрузке (см. Layout), кэш — localStorage. */
function _readBizTz(): number {
  const raw = localStorage.getItem('deadline_biz_tz')
  if (raw == null || raw === '') return 7
  const n = Number(raw)
  return Number.isFinite(n) ? n : 7
}
let _bizTz = _readBizTz()
export function getBizTzOffset(): number { return _bizTz }
export function setBizTzOffset(n: number): void {
  if (Number.isFinite(n)) { _bizTz = n; localStorage.setItem('deadline_biz_tz', String(n)) }
}
export function tzLabel(): string { return `UTC${_bizTz >= 0 ? '+' : ''}${_bizTz}` }

const _pad = (n: number) => String(n).padStart(2, '0')
/* Разложить UTC-ISO в «настенные» части пояса бизнеса (через UTC-геттеры сдвинутой
   даты — независимо от пояса браузера). */
function _bizParts(iso: string) {
  const shifted = new Date(new Date(iso).getTime() + _bizTz * 3600_000)
  return {
    y: shifted.getUTCFullYear(), mo: shifted.getUTCMonth(), da: shifted.getUTCDate(),
    h: shifted.getUTCHours(), mi: shifted.getUTCMinutes(), wd: shifted.getUTCDay(),
  }
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const p = _bizParts(iso)
  const now = _bizParts(new Date().toISOString())
  const hm = `${_pad(p.h)}:${_pad(p.mi)}`
  const sameDay = p.y === now.y && p.mo === now.mo && p.da === now.da
  if (sameDay) return hm
  return `${_pad(p.da)}.${_pad(p.mo + 1)} ${hm}`
}

/* datetime-local «YYYY-MM-DDTHH:mm» (его владелец трактует как время бизнеса) → ISO UTC.
   Раньше делали new Date(local).toISOString() — это пояс БРАУЗЕРА. */
export function bizLocalToUtc(local: string): string {
  const ms = Date.parse(local.length === 16 ? local + ':00Z' : local + 'Z')
  return new Date(ms - _bizTz * 3600_000).toISOString()
}
/* ISO UTC → «YYYY-MM-DDTHH:mm» в поясе бизнеса (значение для <input datetime-local>). */
export function utcToBizLocal(iso?: string | null): string {
  if (!iso) return ''
  const p = _bizParts(iso)
  return `${p.y}-${_pad(p.mo + 1)}-${_pad(p.da)}T${_pad(p.h)}:${_pad(p.mi)}`
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
