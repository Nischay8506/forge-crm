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
  enquiry: { id: string; ref: string; summary: string | null } | null
  customer: string | null
  account_id: string | null
  owner_id: string | null
  owner_name: string | null
  created_at: string
  updated_at: string
}

export interface CostRate { kind: "material" | "machine"; code: string; name: string; rate: string; unit: string; from: string | null; source: "forge" | "erpnext"; ref: string }

/** A line's cost breakdown, as it was worked out (F3.2). */
export interface CostBreakdown {
  source: "model" | "bom" | "rate_card" | "unknown"
  why?: string
  parts?: { group: string; label: string; detail: string; amount: string }[]
  per_unit?: Record<string, string>
  one_time?: Record<string, string>
  rates?: CostRate[]
  costed_at?: string
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
  cost_source: CostBreakdown["source"] | null
  cost_flag: string | null
  unit_cost?: string | null
  cost?: CostBreakdown | null
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
    lines: QuoteLineView[]
  }
  revisions: { number: number; status: string; reason: string | null; created_at: string; sent_at: string | null }[]
  may: { work: boolean; see_costs: boolean }
}

export interface RateRow {
  id: string
  code: string
  name: string
  unit?: string
  rate?: string
  hour_rate?: string
  effective_from: string
  note: string | null
  created_by: string
  created_at: string
}

export interface MaterialItem { code: string; name: string; current: RateRow | null; next: RateRow | null; history: RateRow[] }

export interface MachineItem {
  code: string
  name: string
  source: "forge" | "erpnext"
  current: (RateRow | { hour_rate: string; synced_at: string }) | null
  next: RateRow | null
  history: RateRow[]
  erpnext_wins: boolean
}

export interface AffectedQuote { id: string; number: string; status: QuoteStatus; revision: number }
