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
  prev_hash: string
  hash: string
}

export interface ChainCheck { ok: boolean; checked: number; first_bad_seq: number | null; problem: string | null }

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
