import { describe, expect, it } from "vitest"
import {
  chainLine, discountLine, matrixBody, money, plainPct, rupees, whenRow, type EditRow,
} from "./doaLabels"

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

describe("the discount a quote is asking for", () => {
  const stage = (name: string) => ({ role: name, role_name: name, people: 1, because: [] })

  it("names the whole chain in the order it has to be signed in", () => {
    expect(chainLine([])).toBe("Nobody: this quote can go out as it is")
    expect(chainLine([stage("Finance")])).toBe("Finance")
    expect(chainLine([stage("Sales Manager"), stage("Finance")]))
      .toBe("Sales Manager, then Finance")
    expect(chainLine([stage("Sales Manager"), stage("Finance"), stage("MD")]))
      .toBe("Sales Manager, then Finance, then MD")
  })

  it("says what the discount costs, in rupees a person can read", () => {
    expect(discountLine({
      discount_pct: "20.00", given_away: "250000", amount: "1000000",
      discounted_line_count: 2,
    })).toBe("20% off on 2 lines: ₹2,50,000.00 given away on a quote worth ₹10,00,000.00")
  })

  it("still says something useful before a quote has been priced", () => {
    expect(discountLine({
      discount_pct: "7.50", given_away: null, amount: null, discounted_line_count: 1,
    })).toBe("7.5% off on 1 line")
  })

  it("shows money to the paisa, and says so plainly when there is none", () => {
    expect(money("126071.6")).toBe("₹1,26,071.60")
    expect(money("437000")).toBe("₹4,37,000.00")
    expect(money(null)).toBe("—")
  })

  it("reads a percentage the way it is written by hand", () => {
    expect(plainPct("20.00")).toBe("20")
    expect(plainPct("7.50")).toBe("7.5")
    expect(plainPct(null)).toBe("0")
  })
})
