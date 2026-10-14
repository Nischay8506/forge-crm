import type { CostSource, CostStatus, QuoteStatus } from "./types"

export const STATUS_TONE: Record<QuoteStatus, "teal" | "gold" | "green" | "red" | "amber" | "neutral"> = {
  draft: "neutral", pending_approval: "amber", sent: "teal", accepted: "green", lost: "red", expired: "neutral",
}

export const SOURCE_LABELS: Record<CostSource, string> = {
  model: "Cost model", bom: "ERPNext BOM", rate_card: "Rate card", unknown: "Cost not known",
}

/** ₹1,23,456.50 the Indian way; "–" when there is no amount. */
export function rupees(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "–"
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return `₹${n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** "R2 · Customer asked" style revision names. */
export function revisionText(n: number, reason: string | null): string {
  const why: Record<string, string> = { customer_ask: "customer asked", cost_change: "cost changed", correction: "correction" }
  return reason ? `R${n} · ${why[reason] ?? reason}` : `R${n}`
}

/** Where the goods go, in one line. */
export function placeText(d: { place?: string; city?: string; state?: string } | null): string {
  if (!d) return ""
  return [d.place, d.city, d.state].filter(Boolean).filter((x, i, a) => a.indexOf(x) === i).join(", ")
}

/** A line's cost status (K10 A), in words and colour. */
export const COST_STATUS: Record<CostStatus, { label: string; tone: "teal" | "amber" | "red" | "neutral" }> = {
  complete: { label: "Costed", tone: "neutral" },
  incomplete: { label: "Cost incomplete", tone: "amber" },
  unknown: { label: "Cost not known", tone: "red" },
  reviewed: { label: "Cost reviewed", tone: "teal" },
}

/** What each part of a cost is (F3.2). */
export const GROUP_LABELS: Record<string, string> = {
  material: "Material", conversion: "Machining", overhead: "Overhead", packing: "Packing",
  tooling: "Tooling", rate_card: "Rate card",
}

/** The review card's state, for the line (K10 A). */
export function reviewText(status: string): string {
  return ({
    pending: "waiting for review in Decisions", approved: "accepted in Decisions",
    rejected: "sent back: fix what is missing, then work the costs out again",
    countered: "a unit cost was set in Decisions", superseded: "replaced by a newer card",
    failed: "the review could not be applied",
  } as Record<string, string>)[status] ?? status
}
