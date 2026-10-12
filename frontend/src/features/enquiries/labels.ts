import type { EnquiryEvent, EnquiryStatus, Grade, MakeAbility, Timer, View } from "./types"

export const VIEWS: { key: View; label: string; hint: string }[] = [
  { key: "open", label: "Open", hint: "New, open and waiting on the customer" },
  { key: "mine", label: "Mine", hint: "Open enquiries you own" },
  { key: "unassigned", label: "No owner", hint: "No one owns these yet" },
  { key: "review", label: "To check", hint: "What was read needs a person's look" },
  { key: "duplicates", label: "Possible duplicates", hint: "Looks like an earlier enquiry" },
  { key: "engineering", label: "Engineering", hint: "Waiting on an engineering review" },
  { key: "waiting", label: "Waiting on customer", hint: "We asked the customer something" },
  { key: "parked", label: "Parked", hint: "No answer after the reminders" },
  { key: "closed", label: "Closed", hint: "Closed or merged" },
  { key: "all", label: "All", hint: "Every enquiry" },
]

export const STATUS_TONE: Record<EnquiryStatus, "teal" | "gold" | "green" | "red" | "amber" | "neutral"> = {
  new: "gold",
  open: "teal",
  waiting_customer: "amber",
  parked: "neutral",
  closed: "neutral",
  merged: "neutral",
}

/** Can we make it (F1.6). */
export const MAKE_LABELS: Record<MakeAbility, string> = {
  standard: "Standard",
  cto: "Configurable",
  eto: "Needs engineering",
  no_fit: "No fit",
}

export const MAKE_TONE: Record<MakeAbility, "green" | "teal" | "amber" | "red"> = {
  standard: "green",
  cto: "teal",
  eto: "amber",
  no_fit: "red",
}

export const MAKE_SOURCE: Record<string, string> = {
  rules: "the rules",
  model: "the rules and the AI",
  person: "a person",
}

/** Fit and Intent are good when high; Risk is good when low. */
export function gradeTone(part: "fit" | "intent" | "risk", g: Grade | undefined): "green" | "amber" | "red" | "neutral" {
  if (!g) return "neutral"
  const good = part === "risk" ? g === "Low" : g === "High"
  const bad = part === "risk" ? g === "High" : g === "Low"
  return good ? "green" : bad ? "red" : "amber"
}

export const PART_LABELS = { fit: "Fit", intent: "Intent", risk: "Risk" } as const

/** Priority 0-100: the colour of its number. */
export function priorityTone(score: number | null | undefined): "green" | "teal" | "amber" | "neutral" {
  if (score === null || score === undefined) return "neutral"
  if (score >= 70) return "green"
  if (score >= 45) return "teal"
  return "amber"
}

export const MAIL_LABELS: Record<string, string> = {
  enquiry: "Enquiry",
  complaint: "Complaint",
  payment: "Payment",
  order_status: "Order status",
  vendor_mail: "Vendor mail",
  spam: "Spam",
}

/** Labels a person can give an email that is not an enquiry. */
export const OTHER_LABELS = ["complaint", "payment", "order_status", "vendor_mail", "spam"] as const

export const LANGUAGES: Record<string, string> = { en: "English", hi: "Hindi", te: "Telugu" }

export const RULE_LABELS: Record<string, string> = {
  account_owner: "Account owner",
  product_line: "Product family",
  region: "Region",
  round_robin: "Round-robin",
  away: "Stand-in (owner away)",
  fallback: "Fallback person",
  manual: "Chosen by a person",
  none: "No one found",
}

export const UNIT_LABELS: Record<string, string> = {
  minutes: "minutes (around the clock)",
  hours: "hours (around the clock)",
  working_hours: "working hours",
  working_days: "working days",
}

/** "45s", "12m 05s", "3h 20m", "2d 4h". */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`
  if (s < 86400) return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`
}

/** What a timer says to a person: done in time, late, time left, or paused. */
export function timerText(t: Timer): string {
  const left = t.target_seconds - t.used_seconds
  const working = t.clock === "working" ? " (working time)" : ""
  if (t.stopped_at) {
    if (t.outcome === "met") return `Done in ${duration(t.used_seconds)} ✓`
    if (t.outcome === "missed") return `Done ${duration(-left)} late`
    return "Stopped"
  }
  if (t.paused) return "Paused while we wait on the customer"
  if (left < 0) return `${duration(-left)} late${working}`
  return `${duration(left)} left${working}`
}

export function timerTone(t: Timer): "ok" | "warn" | "late" | "idle" {
  if (t.stopped_at) return t.outcome === "missed" ? "late" : t.outcome === "met" ? "ok" : "idle"
  if (t.paused) return "idle"
  if (t.pct >= 100) return "late"
  if (t.pct >= 75) return "warn"
  return "ok"
}

export const TONE_CLASS = {
  ok: "bg-green-100 text-green-500",
  warn: "bg-amber-100 text-amber-500",
  late: "bg-red-100 text-red-500",
  idle: "bg-surface-2 text-ink-500",
} as const

const STAGE_NAMES: Record<string, string> = {
  first_response: "First response",
  first_look: "Owner's first look",
  quote: "Quote",
  engineering_review: "Engineering review",
}

const FIELD_NAMES: Record<string, string> = {
  summary: "summary", customer: "customer details", details: "delivery and dates", is_tender: "tender",
  product_family_id: "product family", language: "language", customer_link: "linked account or contact",
}

function str(v: unknown): string {
  return typeof v === "string" ? v : ""
}

/** One line of history in plain words. */
const FILLED_NAMES: Record<string, string> = {
  qty: "quantity", material: "grade", standard: "standard", dimensions: "size",
  delivery_location: "delivery place", target_date: "date needed", target_date_text: "date needed",
}

/** "line 1 quantity 40 MT" or "delivery place Patancheru", for what a reply filled in. */
export function filledText(f: Record<string, unknown>): string {
  const name = FILLED_NAMES[String(f.field)] ?? String(f.field)
  let value = String(f.value ?? "")
  const ymd = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (f.field === "target_date" && ymd) {  // the day itself, whatever the viewer's time zone
    value = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" })
      .format(new Date(Number(ymd[1]), Number(ymd[2]) - 1, Number(ymd[3])))
  }
  return `${typeof f.line === "number" ? `line ${f.line} ` : ""}${name} ${value}`.trim()
}

export function eventText(ev: EnquiryEvent, names: Record<string, string> = {}): string {
  const d = ev.detail ?? {}
  const who = (id: unknown) => (typeof id === "string" && names[id]) || "someone"
  switch (ev.kind) {
    case "created": {
      const n = typeof d.lines === "number" ? d.lines : 0
      const lines = `${n} ${n === 1 ? "line" : "lines"}`
      if (d.source === "manual") return `Entered by hand with ${lines}`
      return `Opened from an email, ${lines} read${d.needs_review ? "; needs a person's check" : ""}`
    }
    case "customer_matched": return `Customer: ${str(d.how) || "not found"}`
    case "assigned": {
      const rule = RULE_LABELS[str(d.rule)] ?? str(d.rule)
      return `Given to ${who(d.user_id)} (${rule})${d.reason ? `: ${str(d.reason)}` : ""}`
    }
    case "acknowledged": {
      const q = Array.isArray(d.questions) ? d.questions.length : 0
      return `Acknowledgement ${d.status === "queued" ? "sent" : str(d.status) || "sent"}${q ? `, with ${q} ${q === 1 ? "question" : "questions"}` : ""}`
    }
    case "nudged": return `Reminder ${typeof d.nudge === "number" ? d.nudge : ""} sent to the customer`.replace("  ", " ")
    case "parked": return "Parked: the customer did not answer the reminders"
    case "customer_replied": {
      const a = Array.isArray(d.answered) ? d.answered.length : 0
      const filled = Array.isArray(d.filled) ? (d.filled as Record<string, unknown>[]) : []
      const check = Array.isArray(d.to_check) ? d.to_check.length : 0
      const parts = [
        a ? `answered ${a} ${a === 1 ? "question" : "questions"}` : "",
        filled.length ? `filled in ${filled.map(filledText).join(", ")}` : "",
        check ? `${check} ${check === 1 ? "answer" : "answers"} to check` : "",
      ].filter(Boolean)
      return `The customer replied${parts.length ? `: ${parts.join("; ")}` : ""}`
    }
    case "message_sent": return "A message was sent"
    case "timer_stopped": return `${STAGE_NAMES[str(d.stage)] ?? str(d.stage)}: ${d.outcome === "met" ? "done in time" : d.outcome === "missed" ? "done late" : "stopped"}`
    case "timer_missed": return `${STAGE_NAMES[str(d.stage)] ?? str(d.stage)} is late`
    case "escalated": return `${STAGE_NAMES[str(d.stage)] ?? str(d.stage)} is late: told the ${d.level === "manager" ? "manager" : "head of department"}`
    case "possible_duplicate": return `Looks like ${str(d.ref)} (${typeof d.score === "number" ? d.score : ""}%)`
    case "merged": return `Merged into ${str(d.into_ref)}${d.automatic ? " by Forge" : ""}`
    case "merged_in": return `${str(d.other_ref)} was merged in${d.automatic ? " by Forge" : ""}`
    case "merge_undone": return d.other_ref ? `${str(d.other_ref)} was taken out again` : "The merge was undone"
    case "not_duplicate": return `Marked as different from ${str(d.other_ref)}`
    case "owner_opened": return "The owner opened it"
    case "quote_sent": return "Quote marked as sent"
    case "closed": return "Closed"
    case "reopened": return "Reopened"
    case "converted": return "Made an enquiry from Other mail"
    case "edited": {
      const c = (d.changed ?? {}) as Record<string, unknown>
      const keys = Object.keys(c).filter((k) => k !== "checked")
      if ("checked" in c && keys.length === 0) return "A person checked what was read"
      return `Changed: ${keys.map((k) => FIELD_NAMES[k] ?? k).join(", ")}${"checked" in c ? "; checked what was read" : ""}`
    }
    case "line_added": return "A line was added"
    case "line_corrected": return `A line was corrected${Array.isArray(d.fields) ? ` (${d.fields.join(", ")})` : ""}`
    case "line_removed": return `A line was removed${d.description ? `: ${str(d.description)}` : ""}`
    case "customer_added": return "The sender was added to contacts"
    case "received_how": return `Received by ${str(d.how)}`
    case "checked": {
      const m = MAKE_LABELS[str(d.make_ability) as MakeAbility] ?? "Not checked"
      const by = d.source === "person" ? " (engineering review)" : d.source === "model" ? " (with the AI)" : ""
      return `Can we make it: ${m}${by}`
    }
    case "scored": return `Priority ${d.before === null || d.before === undefined ? "set to" : `${String(d.before)} →`} ${String(d.score)}${d.why ? ` (${str(d.why)})` : ""}`
    case "review_opened": return "Sent for an engineering review"
    case "review_taken": return "An engineer took the review"
    case "review_done": return `Engineering review: ${d.outcome === "cannot_make" ? "we can't make it" : "we can make it"}${d.notes ? `: ${str(d.notes)}` : ""}`
    case "review_cancelled": return "The engineering review was no longer needed"
    case "declined": return "Declined: the customer was told we can't make it"
    case "no_fit_escalated": return "Looks like no fit; a strategic account, so its people were told"
    default: return ev.kind.replace(/_/g, " ")
  }
}

export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** "25 MT", "12.5 nos", or "" when no quantity was read. */
export function qtyText(qty: string | null, unit: string | null): string {
  if (qty === null || qty === undefined) return unit ?? ""
  const n = Number(qty)
  const shown = Number.isFinite(n) ? String(Number(n.toFixed(3))) : qty
  return unit ? `${shown} ${unit}` : shown
}
