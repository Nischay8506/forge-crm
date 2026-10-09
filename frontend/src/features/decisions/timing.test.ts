import { describe, expect, it } from "vitest"
import { durationLabel, shownMs } from "./timing"

describe("card timing", () => {
  it("counts from when the card came on screen, or the previous decision if later", () => {
    expect(shownMs(10_000, 4_000, 1_000)).toBe(6_000)   // on screen at 4 s, decided at 10 s
    expect(shownMs(10_000, 4_000, 7_000)).toBe(3_000)   // decided another card at 7 s first
    expect(shownMs(10_000, null, 1_000)).toBeNull()     // never on screen
  })
  it("writes durations for people", () => {
    expect(durationLabel(42_000)).toBe("42 s")
    expect(durationLabel(65_000)).toBe("1 min 5 s")
    expect(durationLabel(120_000)).toBe("2 min")
  })
})
