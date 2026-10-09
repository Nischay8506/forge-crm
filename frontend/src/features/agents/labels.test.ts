import { describe, expect, it } from "vitest"
import { auditLine, conditionProblem, offset, triggerLabel } from "./labels"
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
  it("explains integration records", () => {
    expect(auditLine({ kind: "mapping.changed",
      data: { erp_code: "Nos", after: { forge_code: "NOS", status: "confirmed" } } }))
      .toBe("Changed a code mapping: Nos → NOS")
    expect(auditLine({ kind: "erp.write.refused", data: { reasons: ["Already in ERPNext as CUST-1"] } }))
      .toBe("Refused to create an ERP customer: Already in ERPNext as CUST-1")
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

describe("run labels", () => {
  it("says what started a run", () => {
    expect(triggerLabel({ trigger: "try_it", trigger_ref: "user:1" })).toBe("Tried on the Agents screen")
    expect(triggerLabel({ trigger: "account.check", trigger_ref: "x" })).toBe("An account was added or changed")
    expect(triggerLabel({ trigger: "contact.check", trigger_ref: "x" })).toBe("A contact was added or changed")
    expect(triggerLabel({ trigger: "mcp:Claude Desktop", trigger_ref: null })).toBe("Asked by Claude Desktop (outside AI tool)")
    expect(triggerLabel({ trigger: "sync:reconcile", trigger_ref: "Main ERP" })).toBe("Nightly comparison with Main ERP")
  })
  it("shows time since the run started", () => {
    const t0 = "2026-10-09T10:00:00.000Z"
    expect(offset(t0, "2026-10-09T10:00:00.250Z")).toBe("+250 ms")
    expect(offset(t0, "2026-10-09T10:00:01.400Z")).toBe("+1.4 s")
    expect(offset(t0, "2026-10-09T10:05:00.000Z")).toBe("+5 min")
  })
})
