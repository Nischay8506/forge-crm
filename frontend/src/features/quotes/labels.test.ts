import { describe, expect, it } from "vitest"
import { placeText, revisionText, rupees } from "./labels"

describe("quote labels", () => {
  it("writes rupees the Indian way", () => {
    expect(rupees("123456.5")).toBe("₹1,23,456.50")
    expect(rupees(0)).toBe("₹0.00")
    expect(rupees(null)).toBe("–")
  })

  it("names revisions and places", () => {
    expect(revisionText(1, null)).toBe("R1")
    expect(revisionText(2, "customer_ask")).toBe("R2 · customer asked")
    expect(placeText({ place: "Gate 3", city: "Vizag", state: "Andhra Pradesh" })).toBe("Gate 3, Vizag, Andhra Pradesh")
    expect(placeText({ city: "Pune", state: "Pune" })).toBe("Pune")
    expect(placeText(null)).toBe("")
  })
})
