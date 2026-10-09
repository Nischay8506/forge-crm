/** Shapes returned by backend app/api/v1/agents.py, decisions.py and audit.py. */

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
  status: "draft" | "sent" | "failed"
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
