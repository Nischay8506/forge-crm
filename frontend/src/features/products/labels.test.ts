import { describe, expect, it } from "vitest"
import { codeExample, codeOf, condText, keyOf, orList, ruleText } from "./labels"
import type { AttributeDef, RuleDef } from "./types"

const attrs: AttributeDef[] = [
  { key: "class", label: "Pressure class", kind: "choice", required: true, options: [
    { value: "150", label: "Class 150", code: "150" }, { value: "900", label: "Class 900", code: "900" },
    { value: "1500", label: "Class 1500", code: "1500" }] },
  { key: "facing", label: "Facing", kind: "choice", required: true, options: [
    { value: "RF", label: "Raised face", code: "RF" }, { value: "FF", label: "Flat face", code: "FF" },
    { value: "RTJ", label: "Ring-type joint", code: "RTJ" }] },
  { key: "length", label: "Length", kind: "number", required: false, unit: "mm", min: "100", max: "6000", step: "10" },
]

describe("product option labels", () => {
  it("lists options the way people say them", () => {
    expect(orList([])).toBe("")
    expect(orList(["a"])).toBe("a")
    expect(orList(["a", "b", "c"])).toBe("a, b or c")
  })

  it("writes conditions and rules as sentences", () => {
    expect(condText({ attr: "class", in: ["900", "1500"] }, attrs)).toBe("pressure class is Class 900 or Class 1500")
    expect(condText({ attr: "length", min: "100", max: "500" }, attrs)).toBe("length is from 100 to 500 mm")
    expect(condText({ attr: "length", min: "3000" }, attrs)).toBe("length is 3000 mm or more")
    const r: RuleDef = { kind: "requires", when: [{ attr: "class", in: ["900", "1500"] }], then: { attr: "facing", in: ["RTJ"] }, message: "" }
    expect(ruleText(r, attrs)).toBe("When pressure class is Class 900 or Class 1500, facing must be Ring-type joint")
    expect(ruleText({ ...r, kind: "excludes", then: { attr: "length", max: "200" } }, attrs))
      .toBe("When pressure class is Class 900 or Class 1500, length can't be up to 200 mm")
    expect(ruleText({ kind: "only_when", when: [{ attr: "class", in: ["150"] }], then: { attr: "facing", in: ["FF"] }, message: "" }, attrs))
      .toBe("Facing Flat face only when pressure class is Class 150")
  })

  it("shows an example variant code, collapsing empty parts", () => {
    expect(codeExample({ attributes: attrs, code_pattern: "FL-{class}-{facing}-L{length}" })).toBe("FL-150-RF-L100")
    expect(codeExample({ attributes: attrs, code_pattern: "FL-{class}-{missing}-{facing}" })).toBe("FL-150-RF")
  })

  it("suggests keys and codes from labels", () => {
    expect(keyOf("Pressure class")).toBe("pressure_class")
    expect(keyOf("2nd coat")).toBe("a_2nd_coat")
    expect(codeOf("Raised face")).toBe("RAISED")
  })
})
