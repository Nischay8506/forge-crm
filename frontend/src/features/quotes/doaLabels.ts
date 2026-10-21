/** Reading an approval rulebook back, and turning the screen's rows into what the API
 *  stores (F3.4). Kept apart from the page so it can be tested as the arithmetic it is. */

export type RowWhen = {
  discount_pct_from?: string
  margin_pct_upto?: string
  below_floor?: boolean
  value_from?: string
}

export type EditRow = {
  discount_pct_from: string
  margin_pct_upto: string
  below_floor: boolean
  value_from: string
  approvers: string[]
  separate_stages: boolean
  note: string
}

/** An amount the way it is read in India: ₹25,00,000, not ₹2500000. Written here rather
 *  than borrowed from the quote labels so this screen stands on its own. */
export function rupees(value: string): string {
  const n = Number(value)
  return Number.isFinite(n) ? `₹${n.toLocaleString("en-IN")}` : `₹${value}`
}

/** When a row applies, in words, so a rulebook can be checked by reading it. */
export function whenRow(w: RowWhen): string {
  const parts: string[] = []
  if (w.discount_pct_from) parts.push(`a discount of ${w.discount_pct_from}% or more`)
  if (w.margin_pct_upto) parts.push(`a margin of ${w.margin_pct_upto}% or less`)
  if (w.below_floor) parts.push("a quote under its floor")
  if (w.value_from) parts.push(`a quote worth ${rupees(w.value_from)} or more`)
  // No condition at all would catch every quote; the API refuses such a row, and saying
  // so here is how the screen shows why.
  return parts.length > 0 ? parts.join(" and ") : "every quote"
}

/** The screen's rows as the API takes them: blanks dropped, a row with nobody left out. */
export function matrixBody(rows: EditRow[]) {
  return rows
    .map((r) => ({
      when: {
        ...(r.discount_pct_from.trim() && { discount_pct_from: r.discount_pct_from.trim() }),
        ...(r.margin_pct_upto.trim() && { margin_pct_upto: r.margin_pct_upto.trim() }),
        ...(r.below_floor && { below_floor: true }),
        ...(r.value_from.trim() && { value_from: r.value_from.trim() }),
      },
      approvers: r.approvers.filter((a) => a !== ""),
      separate_stages: r.separate_stages,
      ...(r.note.trim() && { note: r.note.trim() }),
    }))
    .filter((r) => r.approvers.length > 0)
}
