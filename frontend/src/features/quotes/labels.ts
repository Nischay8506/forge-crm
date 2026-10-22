import type { CostSource, CostStatus, LineAsk, PriceSource, PriceStatus, QuoteStatus } from "./types"

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

// Prices (F3.3, decision C2 A) ------------------------------------------------------------

/** Where a line's list price came from. */
export const PRICE_SOURCE_LABELS: Record<PriceSource, string> = {
  contract: "The customer's own price", price_list: "ERP price list",
  cost_plus: "Cost at the target margin", none: "Not priced",
}

/** A line's price against its floor, in words and colour. */
export const PRICE_STATUS: Record<PriceStatus, { label: string; tone: "teal" | "amber" | "red" | "neutral" }> = {
  ok: { label: "Clears the floor", tone: "neutral" },
  below_floor: { label: "Below the floor", tone: "red" },
  no_cost: { label: "Cost not known, so no margin", tone: "amber" },
  no_floor: { label: "No floor set", tone: "amber" },
  no_price: { label: "Not priced", tone: "red" },
}

/** "25%", "12.5%"; "–" when there is no percentage. */
export function percent(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "–"
  const n = Number(v)
  return Number.isFinite(n) ? `${Number(n.toFixed(3))}%` : String(v)
}

/** The price approval card's state, for the line (C2 A). */
export function approvalText(status: string): string {
  return ({
    pending: "waiting for a price approval in Decisions", approved: "price approved in Decisions",
    rejected: "sent back: change the price, the cost or the policy",
    countered: "a price was set in Decisions", superseded: "replaced by a newer card",
    failed: "the approval could not be applied",
  } as Record<string, string>)[status] ?? status
}

/** What the rep asked for on a line, in words. */
export function askText(ask: LineAsk | null): string {
  if (!ask) return ""
  if (ask.unit_price !== undefined) return `asked ${rupees(ask.unit_price)}`
  if (ask.discount_pct !== undefined) return `asked ${percent(ask.discount_pct)} off`
  if (ask.cleared) return `had asked ${rupees(ask.cleared.unit_price)}; cleared because ${ask.cleared.why}`
  return ""
}
