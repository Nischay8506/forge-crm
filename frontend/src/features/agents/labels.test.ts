import { describe, expect, it } from "vitest"
import { auditLine, conditionProblem } from "./labels"
import type { FactInfo } from "./types"

const amount: FactInfo = { key: "amount", label: "Amount (INR)", type: "money", choices: [],
  ops: ["lt", "lte", "gt", "gte", "eq", "ne"] }
const tier: FactInfo = { key: "account_tier", label: "Account tier", type: "choice",
  choices: ["strategic", "key", "standard"], ops: ["eq", "ne", "in", "not_in"] }

describe("auditLine", () => {
  it("explains what the gate decided", () => {
    expect(auditLine({ kind: "action.proposed",
      data: { summary: "Record a test payment of ₹1,50,000", outcome: "ask_person" } }))
      .toBe("Record a test payment of ₹1,50,000: needs a person")
    expect(auditLine({ kind: "agent.level_changed", data: { from: 2, to: 0 } }))
      .toBe("Changed autonomy level from L2 to L0")
  })
  it("falls back to the kind it does not know", () => {
    expect(auditLine({ kind: "something.new", data: {} })).toBe("something.new")
  })
})

describe("conditionProblem", () => {
  it("needs a number for money", () => {
    expect(conditionProblem({ fact: "amount", op: "gt", value: "" }, amount)).toBe("Amount (INR) needs a number")
    expect(conditionProblem({ fact: "amount", op: "gt", value: "100000" }, amount)).toBeNull()
  })
  it("checks choices and comparisons", () => {
    expect(conditionProblem({ fact: "account_tier", op: "gt", value: "key" }, tier)).toBe("Pick a comparison")
    expect(conditionProblem({ fact: "account_tier", op: "in", value: [] }, tier)).toBe("Pick account tier")
    expect(conditionProblem({ fact: "account_tier", op: "eq", value: "strategic" }, tier)).toBeNull()
  })
})
