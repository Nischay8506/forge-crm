/** Shapes of the Step 6 (F12.5) API: messages, the Test inbox, consent, channel and
 *  language settings. Mirrors backend app/api/v1/channels.py. */

export type MessageStatus =
  | "draft" | "review" | "queued" | "held" | "sending" | "sent" | "delivered" | "read"
  | "failed" | "bounced" | "blocked" | "cancelled" | "received"

export type Purpose = "promotional" | "transactional" | "reply" | "staff"

export interface Check { check: string; ok: boolean; detail: string }

export interface MessageView {
  id: string
  channel: string
  direction: "in" | "out"
  status: MessageStatus
  status_label: string
  purpose: Purpose | null
  subject: string | null
  body: string
  ai_generated: boolean
  disclosure: string | null
  language: string | null
  script: string | null
  account_id: string | null
  account_name: string | null
  contact_id: string | null
  contact_name: string | null
  user_id: string | null
  user_name?: string | null
  to_address: string | null
  from_address: string | null
  provider: string | null
  reason: string | null
  send_after: string | null
  attempts: number
  checks: Check[]
  feature: string | null
  in_reply_to_id: string | null
  override_by: string | null
  run_id: string | null
  created_by: string
  created_at: string
  status_at: string | null
  sent_at: string | null
  sent_by: string | null
  erased_at: string | null
}

export interface MessageEvent {
  status: string
  label: string
  source: string
  applied: boolean
  actor: string | null
  detail: Record<string, unknown> | null
  at: string
}

export interface Review {
  id: string
  message_id: string
  language: string
  why: "sample" | "checks"
  blocking: boolean
  problems: string[]
  status: "waiting" | "approved" | "changed" | "rejected" | "closed"
  score: number | null
  comment: string | null
  original_body: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  created_at: string
  message?: MessageView | null
}

export interface MessageDetail extends MessageView {
  events: MessageEvent[]
  reviews: Review[]
  thread: { id: string; direction: "in" | "out"; status: MessageStatus; subject: string | null; created_at: string }[]
  language_name: string | null
}

export interface Written extends MessageView { problems: string[]; rewritten: boolean }

export interface TextCheck { detected: string | null; detected_label: string | null; sure: boolean; problems: string[] }

export interface ChannelSettings {
  promo_per_day: number
  promo_per_week: number
  quiet_start: string
  quiet_end: string
  promo_rest_days: number[]
  promo_skip_holidays: boolean
  reply_window_hours: number
  email_mode: "simulator" | "live"
  review_first: number
  review_every: number
  staff_email: boolean
  is_default: boolean
  updated_by: string | null
  updated_at: string | null
}

export interface EmailAccount {
  id: string
  host: string
  port: number
  security: "starttls" | "ssl" | "none"
  username: string
  password_hint: string | null
  from_address: string
  from_name: string | null
  reply_to: string | null
  status: "active" | "paused"
  last_check_at: string | null
  last_check_ok: boolean | null
  last_check_message: string | null
  updated_by: string | null
  updated_at: string | null
}

export interface Preset { label: string; host: string; port: number; security: "starttls" | "ssl" | "none"; help: string }

export interface ChannelsView {
  settings: ChannelSettings
  time_zone: string
  holidays: string[]
  email_account: EmailAccount | null
  presets: Record<string, Preset>
  credential_keys_configured: boolean
  writer_available: boolean
  speech_available: boolean
  simulator_addresses: { bounce: string; outage: string; lost: string }
  links_public: boolean
}

export interface Tone {
  language: string
  name: string
  native: string
  formality: "formal" | "neutral" | "friendly"
  greeting: string | null
  sign_off: string | null
  avoid: string[]
  notes: string | null
  is_default: boolean
  updated_by: string | null
  updated_at: string | null
}

export interface GlossaryTerm {
  id: string
  term: string
  kind: "keep" | "translate" | "forbid"
  language: string | null
  translation: string | null
  note: string | null
  created_by: string
  created_at: string
}

export interface LanguageReport {
  language: string
  name: string
  reviewed: number
  waiting: number
  changed: number
  rejected: number
  average_score: number | null
  scored: number
  target: number
  met: boolean
}

export interface LanguagesView {
  recognised: { code: string; name: string; native: string; writes: boolean }[]
  writing: string[]
  tones: Tone[]
  formality: Record<string, string>
  glossary_kinds: Record<string, string>
  glossary: GlossaryTerm[]
  report: LanguageReport[]
  writer_available: boolean
  speech_available: boolean
}

export interface ConsentChannel {
  channel: string
  status: "granted" | "withdrawn" | "unknown"
  may_promote: boolean
  how: string | null
  how_label: string | null
  at: string | null
}

export interface ConsentRecord {
  id: string
  channel: string
  status: "granted" | "withdrawn"
  how: string
  how_label: string
  note: string | null
  source_ref: string | null
  recorded_by: string
  recorded_at: string
}

export interface ConsentView {
  contact_id: string
  channels: ConsentChannel[]
  history: ConsentRecord[]
  hows: Record<string, string>
}
