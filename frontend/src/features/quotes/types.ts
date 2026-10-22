/** Shapes of the quotes and costs APIs (decisions C12 A, C14 A; F3.2).
 *  Mirrors backend app/api/v1/quotes.py and app/api/v1/costs.py. */

export type QuoteStatus = "draft" | "pending_approval" | "sent" | "accepted" | "lost" | "expired"

export interface QuoteRow {
  id: string
  number: string
  status: QuoteStatus
  status_label: string
  revision: number
  valid_until: string
  currency: string
  /** Set when a sent revision's costs changed since (K9, K11 A). */
  reprice_reason: string | null
  reprice_at: string | null
  enquiry: { id: string; ref: string; summary: string | null } | null
  customer: string | null
  account_id: string | null
  owner_id: string | null
  owner_name: string | null
  created_at: string
  updated_at: string
}

export interface CostRate {
  kind: "material" | "machine"; code: string; name: string; rate: string; unit: string
  from: string | null; source: "forge" | "erpnext"; ref: string; yield_pct?: string; scrap_rate?: string
}

export type CostSource = "model" | "bom" | "rate_card" | "unknown"
export type CostStatus = "complete" | "incomplete" | "unknown" | "reviewed"

/** A line's cost breakdown, as it was worked out (F3.2; backend app/cpq/costing.py). */
export interface CostBreakdown {
  source: CostSource
  status: Exclude<CostStatus, "reviewed">
  why: string
  parts: { group: string; label: string; detail: string; amount: string }[]
  per_unit: string | null
  one_time: { label: string; detail: string; amount: string }[]
  one_time_total: string | null
  rates: CostRate[]
  missing: { kind: "material" | "machine"; code: string }[]
  boms: { code: string; item_code: string; level: number; fingerprint: string; modified_at: string; quantity: string; uom: string | null }[]
  conversions: { what: string; from: string; to: string; factor: string; qty: string; result: string }[]
  problems: string[]
  notes: string[]
  values: Record<string, string>
  model: { id: string; version: number } | null
  packed_kg: string | null
  packed_m3: string | null
  inputs: { values: Record<string, string>; item_code: string | null; unit: string | null; qty: string | null }
  at: string
  costed_at: string
  review_key: string
  review?: { key: string; by: string; at: string; unit_cost: string | null; worked_out: string | null }
}

export type PriceSource = "contract" | "price_list" | "cost_plus" | "none"
export type PriceStatus = "ok" | "below_floor" | "no_cost" | "no_price" | "no_floor"

/** A line's price waterfall, as Forge worked it out (F3.3; backend app/cpq/pricing.py). */
export interface PriceWaterfall {
  status: PriceStatus
  source: PriceSource
  currency: string
  list_price: string | null
  unit_price: string | null
  qty: string | null
  unit: string | null
  amount: string | null
  unit_cost: string | null
  margin_pct: string | null
  floor_margin_pct: string | null
  tier: string | null
  policy: { id: string | null; scope: "family" | "company"; from: string; target_margin_pct: string; floor_margin_pct: string; rounding: string } | null
  at: string
  steps: { kind: "list" | "discount" | "ask" | "rounding"; label: string; pct?: string; amount?: string; price: string }[]
  notes: string[]
  problems: string[]
  review_key: string
  approval?: { key: string; by: string; at: string; unit_price: string | null; priced: string | null }
}

export interface QuoteLineView {
  id: string
  position: number
  description: string
  item_code: string | null
  variant_code: string | null
  family_id: string | null
  configuration_id: string | null
  enquiry_line_id: string | null
  qty: string | null
  unit: string | null
  cost_source: CostSource | null
  cost_flag: string | null
  cost_status: CostStatus | null
  unit_cost?: string | null
  cost?: CostBreakdown | null
  /** The Decisions card a person with costs.manage reviews (K10 A). */
  cost_review?: { id: string; status: string; decided_by: string | null; note: string | null } | null
  /** F3.3: the price everyone in sales sees, and the waterfall behind it (C17 B). */
  price_source: PriceSource | null
  price_status: PriceStatus | null
  list_price: string | null
  unit_price: string | null
  amount: string | null
  margin_pct: string | null
  ask: LineAsk | null
  price?: PriceWaterfall | null
  /** The Decisions card a person with prices.approve decides (C2 A). */
  price_review?: { id: string; status: string; decided_by: string | null; note: string | null } | null
}

/** The rep's ask on a line; a typed price is cleared (and kept here) when the product
 *  or unit changes. */
export interface LineAsk {
  discount_pct?: string
  unit_price?: string
  cleared?: { unit_price: string; why: string }
}

export interface QuoteDetail extends QuoteRow {
  active: {
    number: number
    status: string
    reason: string | null
    reason_note: string | null
    valid_until: string
    delivery_terms: string | null
    destination: { place?: string; city?: string; state?: string } | null
    costed_at: string | null
    sent_at: string | null
    priced_at: string | null
    price_status: "ok" | "below_floor" | null
    round_off: boolean
    totals: QuoteTotals
    reprice_reason: string | null
    reprice_at: string | null
    lines: QuoteLineView[]
  }
  revisions: { number: number; status: string; reason: string | null; created_at: string; sent_at: string | null }[]
  may: { work: boolean; see_costs: boolean }
}

/** The quote as a whole, against the floor its lines weigh out to (C2 A). */
export interface QuoteTotals {
  total: string
  cost: string | null
  margin_pct: string | null
  floor_margin_pct: string | null
  status: "ok" | "below_floor"
  lines_left_out: number
}

/** What a family (or the company) is priced by from a date (F3.3). */
export interface PricePolicyData {
  target_margin_pct: string
  floor_margin_pct: string
  tier_discounts: Record<string, string>
  qty_breaks: { from_qty: string; discount_pct: string }[]
  rounding: string
}

export interface PricePolicyVersion {
  id: string; effective_from: string; policy: PricePolicyData; note: string | null
  created_by: string; created_at: string
}

export interface PricePolicyInForce {
  id: string | null; scope: "family" | "company"; from: string
  target_margin_pct: string; floor_margin_pct: string; rounding: string
}

export interface PricePolicyList {
  at: string
  company: PricePolicyInForce | null
  sample: PricePolicyData
  families: { id: string; name: string; own: boolean; policy: PricePolicyInForce | null }[]
}

export interface PricePolicyDetail {
  family: { id: string; name: string } | null
  scope: "family" | "company"
  in_force: PricePolicyInForce | null
  from_company: boolean
  sample: PricePolicyData
  versions: PricePolicyVersion[]
}

export interface RateRow {
  id: string
  code: string
  name: string
  unit?: string
  rate?: string
  hour_rate?: string
  /** The unit and rate as typed (₹62,000 per tonne), when not the canonical unit (K12 A). */
  source_unit?: string | null
  source_rate?: string | null
  yield_pct?: string | null
  scrap_rate?: string | null
  effective_from: string
  note: string | null
  created_by: string
  created_at: string
}

export interface MaterialItem {
  code: string; name: string; current: RateRow | null; next: RateRow | null; history: RateRow[]
  /** ERPNext's own rates for the same item code, for reference only (K5 A). */
  erpnext?: { item: string; uom: string | null; valuation_rate: string | null; last_purchase_rate: string | null; synced_at: string } | null
}

export interface MachineItem {
  code: string
  name: string
  source: "forge" | "erpnext"
  current: (RateRow | { hour_rate: string; synced_at: string }) | null
  next: RateRow | null
  history: RateRow[]
  erpnext_wins: boolean
}

export interface AffectedQuote { id: string; number: string; status: QuoteStatus; revision: number; reasons?: string[] }

/** What saving a rate did (K9 A). */
export interface RateSaved {
  rate: RateRow
  affected_quotes: AffectedQuote[]
  recosted: AffectedQuote[]
  flagged: AffectedQuote[]
  applies_now: boolean
}

// Cost models (K8, K13 A) ----------------------------------------------------------------

export interface CostTable { keys: string[]; rows: { key: string[]; value: string }[] }

export interface CostModelData {
  tables?: Record<string, CostTable>
  values?: { name: string; label?: string; formula: string }[]
  materials?: { label: string; code: string; qty: string; unit?: string }[]
  operations?: { label: string; machine: string; minutes: string }[]
  tooling?: { label: string; amount: string }[]
  tooling_spread?: boolean
  overhead_pct?: string
  packing?: { basis: "pct" | "per_unit"; value: string }
  packed_kg?: string
  packed_m3?: string
  rate_card?: { basis: "kg" | "unit"; rate: string; weight?: string }
}

export interface CostModelVersion {
  id: string; version: number; current: boolean; note: string | null; created_by: string
  created_at: string; full: boolean; rate_card: boolean; model?: CostModelData
}

export interface CostModelFamily {
  id: string; name: string; template: string | null; options_version: number | null
  cost_model: CostModelVersion | null
}

export interface CostModelDetail {
  family: { id: string; name: string; template: string | null }
  attributes: { key: string; label: string; kind: "choice" | "number" | null; values: string[] }[]
  options_version: number | null
  current: CostModelVersion | null
  versions: CostModelVersion[]
  sample: string | null
}

export interface CostCheck {
  problems: string[]
  warnings: string[]
  tried: number
  preview: CostBreakdown | null
}
