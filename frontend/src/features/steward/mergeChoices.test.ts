import { describe, expect, it } from "vitest"
import { defaultPicks, differingFields, fieldChoices } from "./mergeChoices"
import type { CandidateDetail } from "./types"

const A = "a-id", B = "b-id"

function detail(extra: Partial<CandidateDetail> = {}): CandidateDetail {
  return {
    candidate: { id: "c", record_type: "account", left_id: A, right_id: B, score: 80, band: "review",
                 reasons: [], status: "open", decided_by: null, decided_at: null, created_at: "" },
    suggested_survivor_id: A,
    records: {
      [A]: { name: "Bharat Forge Limited", industry: "Forgings", phone: null, city: "Pune" },
      [B]: { name: "Bharath Forge Ltd", industry: "Automotive", phone: "+912000000000", city: "Pune" },
    },
    fields: ["name", "industry", "phone", "city"],
    suggested_choices: {
      industry: { survivor_value: "Forgings", merged_value: "Automotive", take: "survivor", reason: "" },
      phone: { survivor_value: null, merged_value: "+912000000000", take: "merged", reason: "" },
    },
    ...extra,
  }
}

describe("merge choices", () => {
  it("lists only fields that differ", () => {
    expect(differingFields(detail())).toEqual(["name", "industry", "phone"])
  })

  it("starts from the steward's suggestion", () => {
    expect(defaultPicks(detail())).toEqual({ name: A, industry: A, phone: B })
  })

  it("maps picks to survivor/merged for the chosen survivor", () => {
    const d = detail()
    const picks = { name: A, industry: B, phone: B }
    expect(fieldChoices(d, A, picks)).toEqual({ name: "survivor", industry: "merged", phone: "merged" })
    // Same picks with B surviving: A's empty phone is never sent, A's values become "merged".
    expect(fieldChoices(d, B, picks)).toEqual({ name: "merged", industry: "survivor" })
  })

  it("leaves GSTIN and address to the branch rule", () => {
    const d = detail({
      records: { [A]: { gstin: "27AAACB1111B1Z5", city: "Pune" }, [B]: { gstin: "29AAACB1111B1Z1", city: "Bengaluru" } },
      fields: ["gstin", "city"],
      suggested_choices: { _branch_site: { take: "created", reason: "" } },
    })
    expect(fieldChoices(d, A, { gstin: B, city: B })).toEqual({})
  })
})
