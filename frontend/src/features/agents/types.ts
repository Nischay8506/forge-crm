/** Shapes returned by backend app/api/v1/agents.py, decisions.py and audit.py. */
import type { MessageStatus } from "@/features/messages/types"

export interface Level { level: number; code: string; label: string }

export interface ToolInfo {
  key: string
  title: string
  description: string
  side_effect: boolean
  version: number
  facts: { key: string; label: string }[]
}

export interface Override extends Level {
  id: string
  scope: "account_tier" | "account"
  account_tier: string | null
  account_id: string | null
  target: string
  note: string | null
  created_by: string
}

export interface Stop {
  id: string
  scope: "tenant" | "agent" | "account"
  agent_key: string | null
  account_id: string | null
  target: string
  reason: string
  created_by: string
  created_at: string
  released_at: string | null
  released_by: string | null
}

export interface Condition { fact: string; op: string; value: string | number | string[] }

export interface Rule {
  id: string
  agent_key: string | null
  tool_key: string | null
  conditions: Condition[]
  outcome: "allow" | "ask_person" | "block"
  approver_role_id: string | null
  /** H1: the roles that decide, in order (first = approver_role_id). */
  approval_steps: string[]
  /** H4: deadline in working hours; null = the company default. */
  sla_hours: string | null
  note: string | null
  enabled: boolean
  sentence: string
  created_by: string
  updated_by: string
  updated_at: string
}

export interface AgentSummary extends Level {
  key: string
  name: string
  purpose: string
  test_only: boolean
  model_tier: string | null
  triggers: string[]
  actor: string
  default_level: number
  max_level: number
  tools: ToolInfo[]
  overrides: Override[]
  stopped: boolean
  stops: Stop[]
}

export interface AgentDetail extends AgentSummary {
  rules: Rule[]
  activity: { done: number; blocked: number; failed: number; asked: number }
  recent: { seq: number; kind: string; actor: string; created_at: string; data: Record<string, unknown> }[]
  version: number | null
}

export interface FactInfo {
  key: string
  label: string
  type: "number" | "money" | "choice" | "text"
  choices: string[]
  choice_words?: Record<string, string>
  ops: string[]
}

export interface Catalog {
  agents: { key: string; name: string; test_only: boolean; tools: string[] }[]
  tools: ToolInfo[]
  facts: FactInfo[]
  ops: Record<string, string>
  outcomes: Record<string, string>
  account_tiers: string[]
  levels: Level[]
  roles: { id: string; name: string }[]
}

export interface Check { check: string; ok: boolean; detail: string }

export interface DecisionCard {
  id: string
  kind: "approval" | "notice" | "alert" | "task"
  status: string
  title: string
  agent_key: string
  agent_name: string
  tool_key: string
  tool_title: string
  recommendation: string | null
  reasons: string[]
  args: Record<string, unknown>
  facts: { key: string; label: string; value: string }[]
  checks: Check[]
  approver_role: { id: string; name: string } | null
  record_type: string | null
  record_id: string | null
  record_name: string | null
  run_id: string | null
  created_at: string
  decided_by: string | null
  decided_at: string | null
  note: string | null
  result: { status: string; result: Record<string, unknown>; error: string | null; reasons: string[] } | null
  can_decide: boolean
  why_not: string | null
  /** When someone who can decide first had it on screen (F12.3). */
  first_shown_at: string | null
  /** How long the decider had it in front of them, measured by the browser. */
  decision_ms: number | null
  /** Only on GET /decisions/{id}: what the decision led to. */
  next?: AuditRow[]
  // Step 5 (F12.3)
  recommended_option: "approve" | "reject" | "counter" | null
  decided_by_name: string | null
  as_stand_in_for: string | null
  requested_by: string | null
  requested_by_name: string | null
  counter_of: string | null
  /** On GET /decisions/{id}: the card that replaced this one after a counter. */
  countered_by?: string | null
  steps: DecisionStepInfo[]
  step: number | null
  due_at: string | null
  past_deadline: boolean
  escalated_to_me: boolean
  counter_fields: { key: string; label: string; type: string; value: unknown }[]
  /** The fingerprint of the card as shown, sent back with every decision (H7). */
  card_hash: string | null
  history: CardHistory | null
  counter: CounterInfo | null
}

export interface DecisionStepInfo {
  position: number
  role_id: string | null
  role: string | null
  status: "waiting" | "pending" | "approved" | "rejected" | "countered" | "cancelled" | "superseded"
  started_at: string | null
  due_at: string | null
  overdue_at: string | null
  reminded_at: string | null
  decided_by: string | null
  decided_by_name: string | null
  on_behalf_of: string | null
  on_behalf_of_name: string | null
  decided_at: string | null
  note: string | null
  decision_ms: number | null
}

export interface ErpFigures {
  linked: boolean
  erps?: string[]
  as_of?: string | null
  stale?: boolean
  figures?: { currency: string; outstanding: string; overdue: string; invoiced_12m: string }[]
}

export interface CardHistory {
  account: { id: string; name: string } | null
  tier?: string
  earlier?: { count: number; by_status: Record<string, number>; latest: { title: string; status: string }[] }
  erp?: ErpFigures
}

export interface CounterInfo {
  decision_id: string
  fields: string[]
  asked: { key: string; label: string; value: string }[]
  countered: { key: string; label: string; value: string }[]
  by: string | null
  by_name: string | null
  on_behalf_of: string | null
  on_behalf_of_name: string | null
  note: string | null
}

export interface DecisionCounts {
  waiting: number
  approvals: number
  near_deadline: number
  past_deadline: number
  alerts: number
  tasks: number
  notices: number
}

export interface MyNumbers {
  days: number
  decided: number
  median_decision_seconds: number | null
  override_rate: number | null
  overrides: number
  with_recommendation: number
}

export interface InboxSettings {
  time_zone: string
  work_days: number[]
  day_start: string
  day_end: string
  holidays: { date: string; name: string }[]
  default_sla_hours: string
  reminder_pct: number
  escalation_role_id: string | null
  escalation_role: string | null
  bottleneck_overdue: number
  bottleneck_wait_ratio: string
  is_default: boolean
  updated_by: string | null
  updated_at: string | null
  day_names: string[]
  roles: { id: string; name: string }[]
}

export interface Delegation {
  id: string
  kind: "card" | "away"
  from_user_id: string
  from_name: string | null
  to_user_id: string
  to_name: string | null
  decision_id: string | null
  starts_at: string
  ends_at: string | null
  tool_keys: string[]
  tools: string[]
  max_amount: string | null
  note: string | null
  created_by: string
  created_at: string
  revoked_at: string | null
  revoked_by: string | null
  active: boolean
}

export interface CardType { key: string; title: string; has_amount: boolean; agent: string | null }

interface Rates {
  median_wait_hours: number | null
  median_decision_seconds: number | null
  overrides: number
  with_recommendation: number
  override_rate: number | null
  missed_deadlines: number
}

export interface ApprovalAnalytics {
  days: number
  since: string
  by_type: (Rates & { tool_key: string; title: string; decided: number; by_status: Record<string, number>; pending: number; pending_past_deadline: number })[]
  approvers: (Rates & { actor: string; name: string; decided: number; for_others: number })[]
  waiting_by_role: { role_id: string | null; role: string; waiting: number; past_deadline: number }[]
  overall: { decisions: number; steps: number; median_decision_seconds: number | null; under_60s: number | null; measured: number }
}

export interface AuditRow {
  seq: number
  created_at: string
  actor: string
  kind: string
  agent_key: string | null
  run_id: string | null
  record_type: string | null
  record_id: string | null
  data: Record<string, unknown>
  /** Where this record's details stand (R1): inline = written before Step 4. */
  evidence: {
    state: "present" | "removed" | "missing" | "unreadable" | "none" | "inline"
    class?: string
    removed_at?: string
    reason?: string
  }
  format: number
  prev_hash: string
  hash: string
}

export interface ChainCheck {
  ok: boolean
  checked: number
  first_bad_seq: number | null
  problem: string | null
  starts_at: number
  details_removed: number
  seals: {
    state: "on" | "off" | "misconfigured"
    checked: number
    sealed_upto: number | null
    sealed_at: string | null
    unknown_keys: string[]
  }
  warning: string | null
}

export interface PersonalData { hidden: number; revealed: boolean }

export interface Takeover {
  id: string
  record_type: string
  record_id: string
  taken_by: string
  reason: string | null
  started_at: string
  released_at: string | null
  released_by: string | null
}

export interface TryResult {
  status: "done" | "waiting" | "blocked" | "failed"
  summary: string
  reasons: string[]
  decision_id: string | null
  error: string | null
}

export interface RunTally { done: number; asked: number; blocked: number; failed: number }

export interface RunRow {
  id: string
  agent_key: string
  agent_name: string
  trigger: string
  trigger_ref: string | null
  status: string
  started_at: string
  finished_at: string | null
  summary: Record<string, unknown> | null
  tally: RunTally
  waiting: number
  agent_version: number | null
}

export interface ReplayDecision {
  id: string
  kind: DecisionCard["kind"]
  status: string
  title: string
  tool_title: string
  reasons: string[]
  recommendation: string | null
  approver_role_id: string | null
  created_at: string
  decided_by: string | null
  decided_at: string | null
  note: string | null
  result: DecisionCard["result"]
}

export interface ModelCall {
  id: string
  created_at: string
  task: string
  tier: string
  provider: string
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: string
  latency_ms: number
  status: string
  error: string | null
}

export interface StepCheck { ok: boolean; checked: number; problems: { seq: number; problem: string }[] }

export interface Replay {
  run: RunRow
  steps: AuditRow[]
  decisions: ReplayDecision[]
  model_calls: ModelCall[]
  records: { record_type: string; record_id: string; name: string | null }[]
  integrity: StepCheck
  personal_data: PersonalData
}

/** What an agent was given (R2): a record as its tool saw it, or a passage it read. */
export interface Input {
  source: string
  record_type: string | null
  record_id: string | null
  read_at: string
  content: unknown
}

export type Outcome = "proposed" | "acted_alone" | "asked" | "approved" | "rejected" | "undone" | "blocked" | "failed"

export interface ScoreRow extends Record<Outcome, number> {
  month: string
  version: number | null
  override_rate: number | null
}

export interface Scorecard {
  agent_key: string
  rows: ScoreRow[]
  versions: { number: number; first_seen_at: string; changes: string[] }[]
}

export interface Message {
  id: string
  channel: "email" | "whatsapp" | string
  direction: string
  status: MessageStatus
  status_label?: string
  subject: string | null
  body: string
  ai_generated: boolean
  disclosure: string | null
  account_id: string | null
  account_name: string | null
  contact_id: string | null
  contact_name: string | null
  run_id: string | null
  created_by: string
  created_at: string
  sent_at: string | null
  sent_by: string | null
  erased_at: string | null
}

export interface Provenance {
  message: Message
  run: Replay | null
  takeovers: AuditRow[]
  active_takeovers: Takeover[]
  personal_data: PersonalData
}

export interface Retention {
  profile: string
  periods: Record<string, number>
  basis: Record<string, string>
  legal_review: { reviewed_by: string; note: string | null; recorded_by: string; recorded_at: string } | null
  updated_by: string | null
  updated_at: string | null
  is_default: boolean
  categories: Record<string, string>
  profiles: Record<string, string>
  minimums: { skeleton: number; other: number }
}

export interface LegalHold {
  id: string
  account_id: string
  account_name: string | null
  reason: string
  placed_by: string
  placed_at: string
  lifted_by: string | null
  lifted_at: string | null
}
