import type { CandidateDetail } from "./types"

/** Fields the backend keeps on the surviving entity when the other record is another
 *  GST registration of the same company (it becomes a branch site instead). */
export const BRANCH_LOCKED = ["gstin", "address_line1", "address_line2", "city", "state", "pincode"]

const blank = (v: unknown) => v === null || v === undefined || v === ""

export function otherId(detail: CandidateDetail, id: string): string {
  const { left_id, right_id } = detail.candidate
  return id === left_id ? right_id : left_id
}

export function branchRule(detail: CandidateDetail): boolean {
  return "_branch_site" in detail.suggested_choices
}

/** Fields whose values differ between the two records (the ones a reviewer can choose). */
export function differingFields(detail: CandidateDetail): string[] {
  const [a, b] = [detail.records[detail.candidate.left_id], detail.records[detail.candidate.right_id]]
  return detail.fields.filter((f) => !(blank(a[f]) && blank(b[f])) && a[f] !== b[f])
}

/** Which record's value each differing field keeps, starting from the steward's suggestion. */
export function defaultPicks(detail: CandidateDetail): Record<string, string> {
  const survivor = detail.suggested_survivor_id
  const other = otherId(detail, survivor)
  const picks: Record<string, string> = {}
  for (const f of differingFields(detail)) {
    const choice = detail.suggested_choices[f]
    if (choice) picks[f] = choice.take === "merged" ? other : survivor
    else picks[f] = blank(detail.records[survivor][f]) ? other : survivor
  }
  return picks
}

/** Turns per-field picks into the API's field_choices for the chosen survivor. */
export function fieldChoices(detail: CandidateDetail, survivorId: string,
                             picks: Record<string, string>): Record<string, "survivor" | "merged"> {
  const merged = otherId(detail, survivorId)
  const locked = branchRule(detail) ? BRANCH_LOCKED : []
  const out: Record<string, "survivor" | "merged"> = {}
  for (const f of differingFields(detail)) {
    if (locked.includes(f) || blank(detail.records[merged][f])) continue
    out[f] = picks[f] === merged ? "merged" : "survivor"
  }
  return out
}
