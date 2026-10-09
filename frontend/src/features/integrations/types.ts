/** Shapes returned by backend app/api/v1/integrations.py. */

export interface Flow {
  id: string
  entity: string
  label: string
  enabled: boolean
  running_since: string | null
  last_started_at: string | null
  last_finished_at: string | null
  last_success_at: string | null
  last_error_cause: string | null
  last_error_message: string | null
  consecutive_failures: number
  records_read: number
  reconciled_at: string | null
  last_reconcile: { at?: string; error?: string | null; checked?: number; missing?: number; gone?: number; repaired?: number } | null
  run_requested_at: string | null
  resuming: boolean
}

export interface Problem {
  code: string
  title: string
  detail: string
  fix: string | null
  entity?: string
  cause?: string
}

export interface Connection {
  id: string
  name: string
  kind: string
  base_url: string
  settings: Record<string, string>
  key_hint: string
  status: "active" | "paused"
  health: "ok" | "problem" | "paused"
  problems: Problem[]
  alert_id: string | null
  last_check: { at: string | null; ok: boolean | null; cause: string | null; message: string | null }
  last_ok_at: string | null
  about: About | null
  flows: Flow[]
  dead_letters: number
  customers_to_review: number
  codes_to_map: number
  webhook: boolean
  created_at: string
  created_by: string
}

export interface Run {
  id: string
  entity: string | null
  kind: "sync" | "reconcile"
  trigger: string
  status: string
  started_at: string
  finished_at: string | null
  read: number
  problems: number
  retries: number
  error_cause: string | null
  error_message: string | null
  details: Record<string, unknown>
}

export interface ConnectionDetail extends Connection { runs: Run[] }

export interface Overview {
  credential_keys_configured: boolean
  kinds: { kind: string; label: string }[]
  entities: { entity: string; label: string }[]
  connections: Connection[]
}

export interface About {
  time_zone?: string
  time_zone_source?: "erpnext" | "setting"
  user?: string
  versions?: Record<string, string>
  cannot_read?: string[]
}

export interface LoginCheck {
  ok: boolean
  cause: string | null
  message: string | null
  title: string | null
  fix: string | null
  about: About | null
}

export interface DeadLetter {
  id: string
  entity: string
  label: string
  external_id: string | null
  reason: string
  status: "open" | "resolved" | "skipped"
  attempts: number
  first_seen_at: string
  last_seen_at: string
  resolved_by: string | null
  resolved_at: string | null
  note: string | null
  raw: unknown
}

export interface Candidate {
  account_id: string
  name: string
  gstin: string | null
  city: string | null
  band: string
  score: number
  reasons: string[]
}

export interface ErpCustomerRow {
  id: string
  connection_id: string
  connection: string | null
  external_id: string
  name: string
  gstin: string | null
  pan: string | null
  customer_group: string | null
  territory: string | null
  payment_terms: string | null
  credit_limit: string | null
  currency: string | null
  city: string | null
  disabled: boolean
  gone: boolean
  synced_at: string
  account_id: string | null
  account_name: string | null
  link_status: "review" | "pending" | "linked" | "ignored"
  link_method: string | null
  link_reasons: string[]
  candidates: Candidate[]
  linked_by: string | null
  linked_at: string | null
}

export interface CustomerList {
  total: number
  counts: Record<string, number>
  items: ErpCustomerRow[]
}

export interface CodeRow {
  id: string
  connection_id: string
  connection: string | null
  list: string
  erp_code: string
  forge_code: string | null
  forge_label: string | null
  status: "suggested" | "unmapped" | "confirmed"
  suggestion_source: string | null
  confidence: string | null
  version: number
  updated_by: string | null
  updated_at: string
}

export interface CodeLists {
  lists: { list: string; label: string; standard: boolean; counts: Record<string, number> }[]
  items: CodeRow[]
}

export interface ForgeCode { code: string; label: string; source: string }

export interface CodeHistory {
  version: number
  forge_code: string | null
  status: string
  suggestion_source: string | null
  changed_by: string
  changed_at: string
}

export interface ErpWrite {
  id: string
  kind: string
  status: "pending" | "sending" | "succeeded" | "failed" | "unknown" | "refused"
  account_id: string
  connection_id: string
  external_id: string | null
  error: string | null
  attempts: number
  requested_by: string
  decision_id: string | null
  created_at: string
  updated_at: string
}

interface ErpRow { id: string; connection: string | null; external_id: string; synced_at: string }

export interface AccountErp {
  links: (ErpRow & {
    name: string; gstin: string | null; link_method: string | null; credit_limit: string | null
    currency: string | null; payment_terms: string | null; customer_group: string | null
    territory: string | null; gone: boolean
  })[]
  orders: (ErpRow & {
    number: string; order_date: string; status: string; erp_status: string | null
    delivery_date: string | null; customer_po: string | null; currency: string
    grand_total: string; lines: number
  })[]
  invoices: (ErpRow & {
    number: string; invoice_date: string; due_date: string | null; status: string
    erp_status: string | null; currency: string; grand_total: string; outstanding: string
    overdue: boolean
  })[]
  receipts: (ErpRow & { number: string; received_on: string; amount: string; currency: string; status: string })[]
  outstanding: { currency: string; outstanding: string; overdue: string }[]
  as_of: string | null
  can_create_in: { id: string; name: string }[]
  writes: ErpWrite[]
}

export interface CreateResult {
  outcome: "done" | "waiting" | "refused" | "blocked" | "failed" | "already"
  write: ErpWrite
  summary?: string
  reasons?: string[]
  error?: string | null
  decision_id?: string | null
}

export interface McpClient {
  id: string
  name: string
  token_hint: string
  allow_write: boolean
  created_by: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
  revoked_by: string | null
}
