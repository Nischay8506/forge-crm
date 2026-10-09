import { describe, expect, it } from "vitest"
import { agoLabel, dueLabel, pct, secondsLabel, tallyLabel, workingHours } from "./labels"

describe("approval inbox labels", () => {
  const now = Date.parse("2026-10-09T10:00:00Z")
  it("counts down to a deadline and past it", () => {
    expect(dueLabel("2026-10-09T12:10:00Z", now)).toBe("Due in 2 h 10 min")
    expect(dueLabel("2026-10-09T10:20:00Z", now)).toBe("Due in 20 min")
    expect(dueLabel("2026-10-09T09:35:00Z", now)).toBe("Past its deadline by 25 min")
    expect(dueLabel("2026-10-12T10:00:00Z", now)).toBe("Due in 3 days")
  })
  it("names times, hours and rates", () => {
    expect(secondsLabel(42.4)).toBe("42 s")
    expect(secondsLabel(65)).toBe("1 min 5 s")
    expect(secondsLabel(null)).toBe("–")
    expect(workingHours("4")).toBe("4 working hours")
    expect(workingHours(1)).toBe("1 working hour")
    expect(workingHours(2.25)).toBe("2.3 working hours")
    expect(pct(0.5)).toBe("50%")
    expect(pct(null)).toBe("–")
    expect(tallyLabel({ rejected: 1, approved: 2 })).toBe("2 approved, 1 rejected")
    expect(agoLabel("2026-10-09T09:57:00Z", now)).toBe("3 min ago")
  })
})
