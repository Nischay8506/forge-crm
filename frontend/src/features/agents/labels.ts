import type { AuditRow, Condition, FactInfo } from "./types"

type Tone = "teal" | "gold" | "green" | "red" | "amber" | "neutral"

export const OUTCOME_LABELS: Record<string, { label: string; tone: Tone }> = {
  allow: { label: "Let it act", tone: "green" },
  ask_person: { label: "Ask a person", tone: "gold" },
  block: { label: "Block", tone: "red" },
}

export const STATUS_TONES: Record<string, Tone> = {
  pending: "gold", approved: "green", rejected: "red", superseded: "neutral", failed: "red",
  open: "teal", acknowledged: "neutral", resolved: "green", done: "green",
}

export const LEVEL_HELP: Record<number, string> = {
  0: "Drafts only. A person approves every action before it happens.",
  1: "Acts, then tells a person (a notice in Decisions).",
  2: "Acts on its own inside the policy rules.",
  3: "Like L2, and may also direct other agents.",
}

/** What each audit record kind means, in words. */
export const AUDIT_KINDS: Record<string, string> = {
  "action.proposed": "Proposed an action",
  "action.executed": "Did the action",
  "action.blocked": "Was stopped",
  "action.failed": "Action failed",
  "decision.requested": "Asked a person",
  "decision.approved": "Approved",
  "decision.rejected": "Rejected",
  "record.merged": "Merged records",
  "merge.undone": "Undid a merge",
  "candidate.not_duplicate": "Marked not a duplicate",
  "agent.level_changed": "Changed autonomy level",
  "agent.override_added": "Added an autonomy override",
  "agent.override_removed": "Removed an autonomy override",
  "killswitch.on": "Stopped agents",
  "killswitch.off": "Let agents run again",
  "takeover.started": "Took a record over",
  "takeover.released": "Handed a record back",
  "policy.created": "Added a policy rule",
  "policy.updated": "Changed a policy rule",
  "policy.deleted": "Deleted a policy rule",
  "role.created": "Created a role",
  "role.updated": "Changed a role",
  "user.roles_changed": "Changed a user's roles",
  "integration.connection.created": "Connected an ERP",
  "integration.connection.changed": "Changed an ERP connection",
  "integration.credentials.changed": "Changed an ERP login",
  "integration.credentials.reencrypted": "Re-encrypted an ERP login with the new key",
  "integration.flow.changed": "Turned a record type on or off",
  "integration.sync.requested": "Asked for a sync",
  "integration.webhook.token_issued": "Made a webhook secret",
  "integration.record.retried": "Retried a failed ERP record",
  "integration.record.skipped": "Skipped a failed ERP record",
  "integration.alert.raised": "Integration alert raised",
  "integration.alert.changed": "Integration alert changed",
  "integration.alert.resolved": "Integration alert resolved",
  "integration.alert.acknowledged": "Took on an integration alert",
  "integration.task.opened": "Opened an integration task",
  "integration.task.closed": "Closed an integration task",
  "erp.customer.linked": "Linked an ERP customer",
  "erp.customer.account_created": "Made an account from an ERP customer",
  "erp.customer.ignored": "Ignored an ERP customer",
  "erp.customer.unlinked": "Unlinked an ERP customer",
  "erp.write.requested": "Asked to create an ERP customer",
  "erp.write.refused": "Refused to create an ERP customer",
  "erp.write.succeeded": "Created an ERP customer",
  "mapping.codes_found": "Found new ERP codes",
  "mapping.changed": "Changed a code mapping",
  "mapping.code_added": "Added a Forge code",
  "mcp.client.created": "Made an outside AI tool token",
  "mcp.client.revoked": "Stopped an outside AI tool token",
}

export const AUDIT_FAMILIES: { value: string; label: string }[] = [
  { value: "", label: "Everything" },
  { value: "action.", label: "Agent actions" },
  { value: "decision.", label: "Decisions" },
  { value: "policy.", label: "Policy rules" },
  { value: "killswitch.", label: "Stops" },
  { value: "takeover.", label: "Takeovers" },
  { value: "agent.", label: "Autonomy levels" },
  { value: "record.", label: "Merges" },
  { value: "integration.", label: "ERP connections" },
  { value: "erp.", label: "ERP customers and writes" },
  { value: "mapping.", label: "Code mappings" },
  { value: "mcp.", label: "Outside AI tools" },
]

const str = (v: unknown) => (typeof v === "string" ? v : "")

/** One line explaining an audit record, from its kind and data. */
export function auditLine(row: Pick<AuditRow, "kind" | "data">): string {
  const d = row.data
  const base = AUDIT_KINDS[row.kind] ?? row.kind
  switch (row.kind) {
    case "action.proposed": {
      const outcome = str(d.outcome)
      const verdict = outcome === "act" ? "allowed" : outcome === "ask_person" ? "needs a person" : "blocked"
      return `${str(d.summary)}: ${verdict}`
    }
    case "action.blocked":
    case "decision.requested":
      return `${base}: ${(Array.isArray(d.reasons) ? d.reasons : []).join("; ")}`
    case "action.failed":
      return `${base}: ${str(d.error)}`
    case "agent.level_changed":
      return `${base} from L${d.from} to L${d.to}`
    case "killswitch.on":
      return `${base} (${str(d.scope)}): ${str(d.reason)}`
    case "policy.created":
    case "policy.updated":
    case "policy.deleted":
      return `${base}: ${str(d.sentence)}`
    case "decision.approved":
    case "decision.rejected":
      return d.note ? `${base}: "${str(d.note)}"` : base
    case "integration.alert.raised":
    case "integration.alert.changed":
      return `${base}: ${str(d.title)}`
    case "integration.record.skipped":
    case "integration.record.retried":
      return `${base}: ${str(d.external_id)}${d.note ? ` ("${str(d.note)}")` : ""}`
    case "erp.customer.linked":
    case "erp.customer.ignored":
    case "erp.customer.unlinked":
    case "erp.customer.account_created":
      return `${base}: ${str(d.erp_customer)}${d.note ? ` ("${str(d.note)}")` : ""}`
    case "erp.write.succeeded":
      return `${base}: ${str(d.external_id)} in ${str(d.connection)}`
    case "erp.write.refused":
      return `${base}: ${(Array.isArray(d.reasons) ? d.reasons : []).join("; ")}`
    case "mapping.changed": {
      const after = (d.after ?? {}) as Record<string, unknown>
      return `${base}: ${str(d.erp_code)} → ${str(after.forge_code) || "not mapped"}`
    }
    default:
      return base
  }
}

/** A condition is complete when it has a fact, a comparison and a value of the right type. */
export function conditionProblem(c: Condition, fact: FactInfo | undefined): string | null {
  if (!fact) return "Pick what to check"
  if (!fact.ops.includes(c.op)) return "Pick a comparison"
  if (fact.type === "number" || fact.type === "money") {
    return c.value === "" || Number.isNaN(Number(c.value)) ? `${fact.label} needs a number` : null
  }
  const values = Array.isArray(c.value) ? c.value : [c.value]
  return values.length === 0 || values.some((v) => !fact.choices.includes(String(v)))
    ? `Pick ${fact.label.toLowerCase()}` : null
}

export const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1)

export const shortHash = (h: string) => `${h.slice(0, 8)}…${h.slice(-4)}`
