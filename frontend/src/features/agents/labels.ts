import type { AuditRow, Condition, FactInfo, RunRow } from "./types"

type Tone = "teal" | "gold" | "green" | "red" | "amber" | "neutral"

export const OUTCOME_LABELS: Record<string, { label: string; tone: Tone }> = {
  allow: { label: "Let it act", tone: "green" },
  ask_person: { label: "Ask a person", tone: "gold" },
  block: { label: "Block", tone: "red" },
}

export const STATUS_TONES: Record<string, Tone> = {
  pending: "gold", approved: "green", rejected: "red", superseded: "neutral", failed: "red", countered: "amber",
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
  "decision.countered": "Approved a changed version",
  "decision.overdue": "Went past its deadline",
  "delegation.created": "Handed decisions to a colleague",
  "delegation.ended": "Ended a hand-over",
  "inbox.settings.changed": "Changed the approval deadlines",
  "inbox.alert.raised": "Approval bottleneck alert raised",
  "inbox.alert.resolved": "Approval bottleneck alert resolved",
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
  "model.called": "Asked a model",
  "model.failed": "A model call failed",
  "audit.personal_data_revealed": "Revealed personal data in the trail",
  "retention.changed": "Changed retention periods",
  "retention.reviewed": "Recorded a legal review of retention",
  "retention.run": "Ran the nightly clean-up",
  "legal_hold.placed": "Placed a legal hold",
  "legal_hold.lifted": "Lifted a legal hold",
  "contact.erased": "Erased a contact's personal data",
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
  { value: "model.", label: "Model calls" },
  { value: "audit.", label: "Personal data revealed" },
  { value: "retention.", label: "Retention" },
  { value: "legal_hold.", label: "Legal holds" },
  { value: "contact.", label: "Erasures" },
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
      return `${str(d.summary) || str(d.tool)}: ${verdict}`
    }
    case "model.called":
      return `${base}: ${str(d.model)} (${str(d.provider)}), ${d.input_tokens ?? 0} in, ${d.output_tokens ?? 0} out`
    case "model.failed":
      return `${base}: ${str(d.error)}`
    case "audit.personal_data_revealed":
    case "legal_hold.placed":
      return d.reason ? `${base}: "${str(d.reason)}"` : base
    case "retention.run": {
      const r = (d.removed ?? {}) as Record<string, number>
      const n = Object.values(r).reduce((a, b) => a + (Number(b) || 0), 0)
      return n ? `${base}: removed ${n} old item${n === 1 ? "" : "s"}` : `${base}: nothing was old enough`
    }
    case "contact.erased": {
      const st = (d.stores ?? {}) as Record<string, number>
      return `${base} (${Object.values(st).reduce((a, b) => a + (Number(b) || 0), 0)} items in ${Object.values(st).filter(Boolean).length} places)`
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
    case "decision.rejected": {
      const step = Number(d.steps) > 1 ? ` step ${String(d.step)} of ${String(d.steps)}` : ""
      const forWhom = d.on_behalf_of ? " (as a stand-in)" : ""
      return `${base}${step}${forWhom}${d.note ? `: "${str(d.note)}"` : ""}`
    }
    case "decision.countered": {
      const asked = (d.asked ?? {}) as Record<string, unknown>
      const countered = (d.countered ?? {}) as Record<string, unknown>
      const change = Object.keys(countered).map((k) => `${k.replace(/_/g, " ")} ${String(asked[k] ?? "")} → ${String(countered[k] ?? "")}`).join(", ")
      return `${base}: ${change}${d.note ? ` ("${str(d.note)}")` : ""}`
    }
    case "decision.overdue":
      return Number(d.steps) > 1 ? `${base} (step ${String(d.step)} of ${String(d.steps)})` : base
    case "delegation.created":
      return d.kind === "card" ? "Handed a card to a colleague" : `${base}: out of office until ${new Date(str(d.ends_at)).toLocaleString("en-IN")}`
    case "inbox.alert.raised":
      return `${base}: ${str(d.title)}`
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

/** What started an agent run, in words. */
export function triggerLabel(run: Pick<RunRow, "trigger" | "trigger_ref">): string {
  const t = run.trigger
  const ref = run.trigger_ref ?? ""
  if (t === "try_it") return "Tried on the Agents screen"
  if (t === "try_message") return "Test message asked for by a person"
  if (t === "erasure_request") return "A person asked to erase a contact's personal data"
  if (t === "test") return "Automated test"
  if (t.endsWith(".check")) {
    const noun = t.slice(0, -6).replace(/_/g, " ")
    return `${/^[aeiou]/.test(noun) ? "An" : "A"} ${noun} was added or changed`
  }
  if (t.startsWith("mcp:")) return `Asked by ${t.slice(4)} (outside AI tool)`
  if (t.startsWith("erp write requested by")) return "Someone asked to create a customer in the ERP"
  if (t === "sync:sync") return ref ? `New customers read from ${ref}` : "New customers read from the ERP"
  if (t === "sync:reconcile") return ref ? `Nightly comparison with ${ref}` : "Nightly comparison with the ERP"
  if (t === "retry") return ref ? `A failed ERP record was retried (${ref})` : "A failed ERP record was retried"
  return t
}

export const RUN_STATUS: Record<string, { label: string; tone: Tone }> = {
  running: { label: "running", tone: "teal" },
  succeeded: { label: "finished", tone: "neutral" },
  failed: { label: "stopped by an error", tone: "red" },
}

/** "+1.4 s" after the run started. */
export function offset(from: string, to: string): string {
  const ms = new Date(to).getTime() - new Date(from).getTime()
  if (ms < 1000) return `+${Math.max(ms, 0)} ms`
  if (ms < 60_000) return `+${(ms / 1000).toFixed(1)} s`
  if (ms < 3_600_000) return `+${Math.round(ms / 60_000)} min`
  return `+${(ms / 3_600_000).toFixed(1)} h`
}
