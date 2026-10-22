/** Shapes of the M1 Enquiry API (F1.1, F1.4-F1.10).
 *  Mirrors backend app/api/v1/enquiries.py. */
import type { MessageView } from "@/features/messages/types"
import type { Configuration } from "@/features/products/types"

export type EnquiryStatus = "new" | "open" | "waiting_customer" | "parked" | "closed" | "merged"

export type View = "open" | "mine" | "unassigned" | "review" | "duplicates" | "engineering" | "waiting" | "parked" | "closed" | "all"

export type Sort = "priority" | "newest" | "oldest"

/** Can we make it (F1.6): standard, configurable, needs engineering, no fit. */
export type MakeAbility = "standard" | "cto" | "eto" | "no_fit"

export type Grade = "High" | "Medium" | "Low"

export interface ScoreReason { text: string; up: boolean }

export interface ScoreSignal { key: string; label: string; points: number; weight: number; text: string }

export interface ScorePart { points: number; grade: Grade; signals: ScoreSignal[] }

export type ScoreParts = Record<"fit" | "intent" | "risk", ScorePart>

export interface Timer {
  stage: "first_response" | "first_look" | "quote" | "engineering_review"
  label: string
  clock: "always" | "working"
  target_seconds: number
  used_seconds: number
  pct: number
  running: boolean
  paused: boolean
  started_at: string
  due_at: string | null
  stopped_at: string | null
  outcome: "met" | "missed" | "cancelled" | null
  breached_at: string | null
  reminded_at: string | null
  escalated_at: string | null
  escalated2_at: string | null
}

export interface DuplicatePair { of_id: string; score: number; reasons: string[] }

export interface EnquiryRow {
  id: string
  ref: string
  status: EnquiryStatus
  status_label: string
  source: "email" | "manual"
  summary: string | null
  who: string
  company: string | null
  account_id: string | null
  account_tier: string | null
  contact_id: string | null
  owner_id: string | null
  owner_name: string | null
  assigned_rule: string | null
  is_tender: boolean
  needs_review: boolean
  review_reasons: string[]
  language: string | null
  waiting_on: "us" | "customer" | null
  lines: number
  possible_duplicate: DuplicatePair | null
  confidence: string | null
  make_ability: MakeAbility | null
  make_label: string
  score: number | null
  score_grades: Partial<Record<"fit" | "intent" | "risk", Grade>>
  score_reasons: ScoreReason[]
  timer: Timer | null
  first_response_at: string | null
  quote_sent_at: string | null
  created_at: string
  last_activity_at: string | null
}

export interface Line {
  id: string
  position: number
  item_ref: string | null
  description: string
  material: string | null
  dimensions: string | null
  qty: string | null
  unit: string | null
  standard: string | null
  notes: string | null
  confidence: string | null
  unsure: string[]
  make_ability: MakeAbility | null
  make_label: string | null
  make_reasons: string[]
  source: "email" | "attachment" | "person"
  source_ref: string | null
  original: Record<string, unknown> | null
  corrected_by: string | null
  corrected_at: string | null
  from_ref: string | null
  /** The configured product (F3.1), and the family the line belongs to. */
  configuration: Configuration | null
  family: { id: string; name: string; has_options: boolean } | null
}

export interface Attachment {
  filename: string
  content_type: string | null
  size: number
  file_id?: string | null
  skipped?: string | null
}

export interface InboundEmailView {
  id: string
  provider: string
  from_address: string | null
  from_name: string | null
  to: string[]
  cc: string[]
  subject: string | null
  received_at: string
  attachments: Attachment[]
  auto_kind: string | null
  label: string | null
  label_name: string | null
  label_confidence: string | null
  label_source: "model" | "rules" | "person" | "local" | null
  label_reason: string | null
  original_label: string | null
  relabelled_by: string | null
  relabelled_at: string | null
  stage: "new" | "done" | "failed"
  outcome: string | null
  attempts: number
  last_error: string | null
  size_bytes: number
  enquiry_id: string | null
  message_id: string | null
  erased: boolean
  body: string | null
}

export interface Question { key: string; label: string; asked_at: string; answered_at: string | null; answer?: string | null }

export interface EnquiryEvent {
  kind: string
  actor: string
  actor_name: string
  detail: Record<string, unknown>
  at: string
}

export interface Assignment {
  user_id: string | null
  user_name: string | null
  rule: string
  rule_label: string
  reason: string
  overrode_rule: string | null
  actor: string
  actor_name: string
  at: string
}

export interface Customer {
  company?: string | null
  contact_name?: string | null
  designation?: string | null
  phone?: string | null
  email?: string | null
  city?: string | null
  state?: string | null
}

export interface Details {
  target_date?: string | null
  target_date_text?: string | null
  delivery_location?: string | null
  special_requirements?: string | null
  tender_ref?: string | null
}

export interface EnquiryDetail extends Omit<EnquiryRow, "lines"> {
  customer: Customer
  details: Details
  questions: Question[]
  account: { id: string; name: string; tier: string | null; city: string | null; state: string | null } | null
  contact: { id: string; full_name: string; email: string | null; phone: string | null; designation: string | null } | null
  product_family: { id: string; name: string } | null
  checklist: { key: string; label: string | null; required: boolean | null }[]
  lines: Line[]
  merged_in: { id: string; ref: string; merged_at: string; merged_by: string }[]
  merged_into: { id: string; ref: string } | null
  duplicate_of: { id: string; ref: string; summary: string | null; status: EnquiryStatus } | null
  emails: InboundEmailView[]
  messages: MessageView[]
  events: EnquiryEvent[]
  assignments: Assignment[]
  timers: Timer[]
  close_reason: string | null
  closed_at: string | null
  closed_by: string | null
  owner_seen_at: string | null
  quote_sent_by: string | null
  first_response_by: string | null
  nudges: number
  waiting_since: string | null
  parked_at: string | null
  make: { ability: MakeAbility | null; label: string; reasons: string[]; source: "rules" | "model" | "person" | null; checked_at: string | null }
  score_parts: ScoreParts | null
  scored_at: string | null
  review: Review | null
  quote: { id: string; number: string; status: string } | null
  may: { work: boolean; assign: boolean; send: boolean; quote: boolean; see_quotes: boolean }
}

/** The engineering review (P2 A): the lines, files and reasons for the engineering role. */
export interface Review {
  id: string
  status: "open" | "done" | "cancelled"
  role: string | null
  assignee_id: string | null
  assignee: string | null
  reasons: string[]
  package: {
    summary: string | null
    customer: string | null
    special_requirements: string | null
    lines: { position: number; description: string; dimensions: string | null; material: string | null; standard: string | null; qty: string | null; unit: string | null; notes: string | null; make_ability: MakeAbility | null; reasons: string[] }[]
    files: { filename: string; file_id: string; content_type: string | null }[]
    reasons: string[]
  }
  outcome: "can_make" | "cannot_make" | null
  outcome_text: string | null
  notes: string | null
  opened_at: string
  taken_at: string | null
  done_by: string | null
  done_at: string | null
}

export type Counts = Record<View | "other_mail" | "mail_review" | "failed", number>

export interface AckDraft {
  subject: string
  body: string
  language: "en" | "hi" | "te"
  question_keys: string[]
  sent_before: boolean
  checklist: { key: string; label: string | null }[]
}

export interface StageSetting { key: string; label: string; target: number; unit: "minutes" | "hours" | "working_hours" | "working_days" }

export interface EnquirySettings {
  routing: { steps: string[]; max_open: number; fallback_user_id: string | null }
  timers: { stages: StageSetting[]; reminder_pct: number; manager_pct: number; hod_pct: number }
  intake: { auto_acknowledge: boolean; nudge_days: number[]; park_after_days: number; complaints_role_id: string | null; unsure_below: number }
  scoring: ScoringSettings
  is_default: boolean
  updated_by: string | null
  updated_at: string | null
  suggestions: { rule: string; label: string; overrides: number; text: string }[]
  step_labels: Record<string, string>
  signal_labels: Record<"fit" | "intent" | "risk", Record<string, string>>
}

export interface ScoringSettings {
  fit: Record<string, number>
  intent: Record<string, number>
  risk: Record<string, number>
  risk_weight: number
  jump: number
  export_watch: string[]
}

export interface Person {
  id: string
  full_name: string
  email: string
  is_active: boolean
  takes_enquiries: boolean
  product_family_ids: string[]
  regions: string[]
  max_open_enquiries: number | null
  reports_to_id: string | null
  reports_to_name: string | null
  open: number
  away: boolean
  stand_in_name: string | null
}

export interface ChecklistItem {
  key: string
  label: string
  required: boolean
  questions: { en?: string; hi?: string; te?: string }
}

export interface Family {
  id: string
  name: string
  template: string | null
  keywords: string[]
  checklist: ChecklistItem[]
  make_ability: MakeAbility
  moq: { qty: number; unit: string } | null
  sweet_spot: { min?: number; max?: number; unit: string } | null
  refer_to: string | null
  active: boolean
  updated_by: string | null
  updated_at: string | null
}

export interface FamiliesView {
  families: Family[]
  templates: { key: string; name: string; keywords: string[]; checklist: ChecklistItem[]; make_ability: MakeAbility }[]
  general: ChecklistItem[]
}

export interface MailboxView {
  id: string
  provider: "imap" | "gmail" | "microsoft"
  provider_name: string
  address: string | null
  status: "active" | "paused" | "broken" | "disconnected"
  imap_host: string | null
  imap_port: number | null
  imap_security: "ssl" | "starttls" | null
  username: string | null
  folder: string | null
  secret_hint: string | null
  last_poll_at: string | null
  last_ok_at: string | null
  last_error: string | null
  last_error_at: string | null
  failures: number
  push: boolean
  push_expires_at: string | null
  updated_by: string | null
  updated_at: string | null
}

export interface MailboxSetup {
  mailbox: MailboxView | null
  gmail: { ready: boolean; missing: string | null; redirect_uri: string }
  microsoft: { ready: boolean; missing: string | null; redirect_uri: string }
}

export interface TestMailResult {
  email_id: string
  outcome: string
  label: string | null
  label_source: string | null
  enquiry_id: string | null
  ref: string | null
}

export interface TestMailRow extends InboundEmailView {
  ref: string | null
  replies: { id: string; subject: string | null; status: string; body: string; feature: string | null; created_at: string }[]
}

export type LimitKey = "max_diameter_mm" | "max_length_mm" | "max_thickness_mm" | "max_weight_kg"

export interface Process extends Partial<Record<LimitKey, number | null>> {
  name: string
  families?: string[]
  tolerance?: string | null
  family_templates?: string[]
}

/** What we make (F1.6, N8 A). */
export interface Capability {
  processes: Process[]
  materials: string[]
  certifications: string[]
  excluded_industries: string[]
  target_industries: string[]
  engineering_role_id: string | null
  engineering_role: string | null
  is_set_up: boolean
  updated_by: string | null
  updated_at: string | null
  limits: Record<LimitKey, string>
  example: Omit<Capability, "engineering_role_id" | "engineering_role" | "is_set_up" | "updated_by" | "updated_at" | "limits" | "example">
}
