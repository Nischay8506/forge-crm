import { describe, expect, it } from "vitest"
import { matrixBody, rupees, whenRow, type EditRow } from "./doaLabels"

const row = (p: Partial<EditRow> = {}): EditRow => ({
  discount_pct_from: "", margin_pct_upto: "", below_floor: false, value_from: "",
  approvers: [], separate_stages: false, note: "", ...p,
})

describe("when a row applies, in words", () => {
  it("names every condition, and says they all have to hold", () => {
    expect(whenRow({ discount_pct_from: "15", below_floor: true }))
      .toBe("a discount of 15% or more and a quote under its floor")
  })

  it("reads a margin as a ceiling and a quote's worth as a floor", () => {
    expect(whenRow({ margin_pct_upto: "12", value_from: "250000" }))
      .toBe("a margin of 12% or less and a quote worth ₹2,50,000 or more")
  })

  it("groups an amount the way it is read in India", () => {
    expect(rupees("2500000")).toBe("₹25,00,000")
    expect(rupees("not a number")).toBe("₹not a number")
  })

  it("says so plainly when a row has no condition at all", () => {
    expect(whenRow({})).toBe("every quote")
  })
})

describe("what the screen sends", () => {
  it("drops the blanks a half-filled row leaves behind", () => {
    expect(matrixBody([row({ discount_pct_from: " 5 ", approvers: ["sm", ""], note: "  " })]))
      .toEqual([{ when: { discount_pct_from: "5" }, approvers: ["sm"], separate_stages: false }])
  })

  it("leaves out a row nobody approves, rather than sending it to be refused", () => {
    expect(matrixBody([row({ discount_pct_from: "5", approvers: ["", ""] })])).toEqual([])
  })

  it("keeps below the floor and separate stages as they were ticked", () => {
    expect(matrixBody([row({ below_floor: true, approvers: ["fin"], separate_stages: true,
                             note: "under the floor" })]))
      .toEqual([{ when: { below_floor: true }, approvers: ["fin"], separate_stages: true,
                  note: "under the floor" }])
  })
})
