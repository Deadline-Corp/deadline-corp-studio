export interface ChannelInfo {
  id: string
  configured: boolean
  conversations: number
  open: number
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
  tasks: { scheduled_pending: number }
  inbox: { open: number; takeover: number; handed_off: number }
}

export interface CustomerBrief {
  id: string
  name: string | null
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
  handoff_done: boolean
  last_message_at: string | null
  created_at: string | null
  customer: CustomerBrief
  preview: string | null
}

export interface ConvDetail extends ConvSummary {
  summary: string | null
  forum_topic_id: number | null
  crm_deal_id: string | null
  crm_contact_id: string | null
  hubspot: { contact_url?: string; deal_url?: string }
  utm: { source: string | null; campaign: string | null; medium: string | null; content: string | null }
  scheduled_actions: Array<{
    id: string; action_type: string; executor: string; due_at: string | null; payload: any
  }>
}

export interface Msg {
  id: string
  role: 'user' | 'assistant' | 'operator' | 'system'
  content: string
  created_at: string | null
  extra_meta: any
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
