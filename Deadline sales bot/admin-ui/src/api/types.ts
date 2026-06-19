export interface ChannelInfo {
  id: string
  configured: boolean
  conversations: number
  open: number
  new_yesterday?: number
  hot?: number
  no_task?: number
  last_message_at: string | null
}

export interface FunnelStage {
  stage: string
  label: string
  kind?: 'active' | 'won' | 'lost'
  count: number
}

export interface StageDef {
  id: string | null
  key: string
  label: string
  kind: 'active' | 'won' | 'lost'
  position: number
  active: boolean
  builtin: boolean
}

export interface TodayItem {
  id: string
  action_type: string
  executor: string
  due_at: string | null
  channel: string
  text: string | null
  conversation_id: string | null
  customer: { id: string; name: string | null; email: string | null }
}

export interface TodayView {
  overdue: TodayItem[]
  today: TodayItem[]
  upcoming: TodayItem[]
  calls: Array<{
    customer: { id: string; name: string | null; email: string | null }
    conversation_id: string
    channel: string
    call_at: string | null
    medium: string | null
  }>
}

export interface Overview {
  hidden_sections?: string[]
  hidden_actions?: string[]
  bot: {
    model: string
    fallback_model: string
    provider: string
    tenant: string
    display_name: string
    version: string
    prompt_source: 'db' | 'file'
  }
  channels: ChannelInfo[]
  funnel: { stages: FunnelStage[]; other: number }
  kb: { chunks: number; sources: number }
  training: { active_corrections: number }
  crm: { enabled: boolean; provider: string; events_pending: number; events_failed: number }
  tasks: { scheduled_pending: number; overdue?: number; today?: number; no_task?: number }
  inbox: { open: number; takeover: number; handed_off: number }
}

export interface CustomerBrief {
  id: string
  name: string | null
  display_name?: string | null
  email: string | null
  phone: string | null
  lead_score: number
  lead_temperature: string
  interaction_type: string
}

export interface ConvSummary {
  id: string
  channel: string
  status: string
  lead_stage: string
  lost_reason: string | null
  operator_takeover: boolean
  wa_autonomous?: boolean
  pinned?: boolean
  handoff_done: boolean
  last_message_at: string | null
  created_at: string | null
  channel_conversation_id?: string | null
  wa_hidden_phone?: boolean
  customer: CustomerBrief
  preview: string | null
  // amoCRM-style «следующий шаг»: статус ближайшей задачи лида.
  next_step?: { status: 'none' | 'overdue' | 'today' | 'future'; due_at: string | null; overdue_days?: number } | null
}

export interface CustomFieldValue {
  key: string
  label: string
  field_type: 'text' | 'number' | 'select'
  options: string[] | null
  value: any
}

export interface AutomationRuleItem {
  id: string
  name: string
  enabled: boolean
  trigger: { type: string; hours?: number; steps?: Array<{ hours: number; text: string }> }
  conditions: { channels?: string[]; stages?: string[]; temperatures?: string[]; min_score?: number } | null
  actions: Array<{ type: string; text?: string; stage?: string; lost_reason?: string; due_in_hours?: number }>
  cooldown_hours: number
  fired_count: number
  created_at: string | null
}

export interface RevenueView {
  currency: string | null
  currencies: Record<string, number>
  won_value: number
  won_deals: number
  avg_deal: number
  pipeline_value: number
  lost_value: number
  deals_with_value: number
  by_stage: Record<string, number>
  by_channel: Record<string, number>
}

export interface AnalyticsView {
  days: number
  totals: { new_leads: number; handoffs: number; booked_calls: number; automation_fires: number }
  leads_by_channel: Record<string, number>
  leads_by_day: Array<{ day: string; count: number }>
  funnel: Array<{ stage: string; label: string; count: number }>
  lost_reasons: Record<string, number>
  temperatures: Record<string, number>
  messages_by_role: Record<string, number>
  stage_moves: Record<string, number>
  stage_flows: Array<{ from: string | null; to: string; by: string; count: number }>
  revenue?: RevenueView
}

export interface PresetInfo {
  key: string
  title: string
  emoji: string
  desc: string
  stages_count: number
  fields_count: number
  automations_count: number
}

export interface ConvDetail extends ConvSummary {
  fields: CustomFieldValue[]
  summary: string | null
  forum_topic_id: number | null
  crm_deal_id: string | null
  crm_contact_id: string | null
  pending_wa_draft: { text?: string; client_msg?: string; ts?: string; source?: string; stale?: boolean; based_on_count?: number; kind?: string } | null
  pending_call_suggestion?: { at?: string; when_human?: string; medium?: string | null; reason?: string; ts?: string } | null
  wa_autonomous: boolean
  nudge_paused?: boolean
  booked_call_at?: string | null
  deal_value?: number | null
  deal_currency?: string | null
  next_action?: { label?: string; mode?: string; kind?: string } | null
  // Проекция даты следующего дожима (видна сразу, до материализации строки кроном).
  projected_next_followup?: { due_at: string | null; step?: number; of?: number; from_cadence?: boolean; text?: string | null } | null
  hubspot: { contact_url?: string; deal_url?: string }
  utm: { source: string | null; campaign: string | null; medium: string | null; content: string | null }
  scheduled_actions: Array<{
    id: string; action_type: string; executor: string; due_at: string | null; payload: any
  }>
  stage_history?: Array<{ from: string | null; to: string; by: string; at: string | null }>
  bot_decisions?: BotDecisionItem[]
}

export interface BotDecisionItem {
  id: string
  at: string | null
  decision_type: string
  reason: string
  detail: any
  actor: string
  conversation_id?: string | null
}

export interface Msg {
  id: string
  role: 'user' | 'assistant' | 'operator' | 'system'
  content: string
  created_at: string | null
  extra_meta: any
}

export interface ActivityLogItem {
  id: string
  at: string | null
  level: 'info' | 'warn' | 'error'
  category: string
  actor: string
  summary: string
  conversation_id: string | null
  meta: any
}

export interface LogsResp {
  items: ActivityLogItem[]
  cat_counts_24h: Record<string, number>
  errors_24h: number
  next_before: string | null
}

export interface PromptVersionItem {
  id: string
  is_active: boolean
  comment: string | null
  created_by: string
  created_at: string | null
  preview: string
}

export interface ScheduledActionItem {
  id: string
  action_type: string
  executor: string
  status: string
  due_at: string | null
  channel: string
  attempts: number
  payload: any
  conversation_id: string | null
  customer: { id: string; name: string | null; email: string | null }
}
