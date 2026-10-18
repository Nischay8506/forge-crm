import { describe, expect, it } from "vitest"
import { approvalText, askText, percent, placeText, revisionText, rupees } from "./labels"

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

  it("writes prices, margins and what the rep asked for", () => {
    expect(percent("25")).toBe("25%")
    expect(percent("12.500")).toBe("12.5%")
    expect(percent(null)).toBe("–")
    expect(askText({ discount_pct: "7.5" })).toBe("asked 7.5% off")
    expect(askText({ unit_price: "1200" })).toBe("asked ₹1,200.00")
    expect(askText(null)).toBe("")
    expect(approvalText("pending")).toBe("waiting for a price approval in Decisions")
    expect(approvalText("countered")).toBe("a price was set in Decisions")
  })
})
