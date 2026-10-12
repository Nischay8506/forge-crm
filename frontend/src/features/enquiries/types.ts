/** Shapes of the M1 Enquiry API (F1.1, F1.5, F1.8, F1.9, F1.10).
 *  Mirrors backend app/api/v1/enquiries.py. */
import type { MessageView } from "@/features/messages/types"

export type EnquiryStatus = "new" | "open" | "waiting_customer" | "parked" | "closed" | "merged"

export type View = "open" | "mine" | "unassigned" | "review" | "duplicates" | "waiting" | "parked" | "closed" | "all"

export interface Timer {
  stage: "first_response" | "first_look" | "quote"
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
  source: "email" | "attachment" | "person"
  source_ref: string | null
  original: Record<string, unknown> | null
  corrected_by: string | null
  corrected_at: string | null
  from_ref: string | null
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
  label_source: "model" | "rules" | "person" | null
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

export interface Question { key: string; label: string; asked_at: string; answered_at: string | null }

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
  may: { work: boolean; assign: boolean; send: boolean }
}

export type Counts = Record<View | "other_mail" | "failed", number>

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
  is_default: boolean
  updated_by: string | null
  updated_at: string | null
  suggestions: { rule: string; label: string; overrides: number; text: string }[]
  step_labels: Record<string, string>
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
  active: boolean
  updated_by: string | null
  updated_at: string | null
}

export interface FamiliesView {
  families: Family[]
  templates: { key: string; name: string; keywords: string[]; checklist: ChecklistItem[] }[]
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
