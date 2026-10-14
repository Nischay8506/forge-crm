import type { CostBreakdown, QuoteStatus } from "./types"

export const STATUS_TONE: Record<QuoteStatus, "teal" | "gold" | "green" | "red" | "amber" | "neutral"> = {
  draft: "neutral", pending_approval: "amber", sent: "teal", accepted: "green", lost: "red", expired: "neutral",
}

export const SOURCE_LABELS: Record<CostBreakdown["source"], string> = {
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
