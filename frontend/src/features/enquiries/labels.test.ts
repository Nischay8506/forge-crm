import { describe, expect, it } from "vitest"
import { duration, eventText, gradeTone, priorityTone, qtyText, timerText, timerTone } from "./labels"
import type { Timer } from "./types"

const timer = (over: Partial<Timer>): Timer => ({
  stage: "first_response", label: "First response", clock: "always", target_seconds: 900,
  used_seconds: 0, pct: 0, running: true, paused: false, started_at: "2026-10-12T10:00:00Z",
  due_at: null, stopped_at: null, outcome: null, breached_at: null, reminded_at: null,
  escalated_at: null, escalated2_at: null, ...over,
})

describe("enquiry labels", () => {
  it("writes durations a person can read at a glance", () => {
    expect(duration(45)).toBe("45s")
    expect(duration(130)).toBe("2m 10s")
    expect(duration(3 * 3600 + 5 * 60)).toBe("3h 05m")
    expect(duration(2 * 86400 + 4 * 3600)).toBe("2d 4h")
  })

  it("says how a timer stands", () => {
    expect(timerText(timer({ used_seconds: 300, pct: 33 }))).toBe("10m 00s left")
    expect(timerTone(timer({ used_seconds: 300, pct: 33 }))).toBe("ok")
    expect(timerTone(timer({ used_seconds: 700, pct: 78 }))).toBe("warn")
    expect(timerText(timer({ used_seconds: 1000, pct: 111 }))).toBe("1m 40s late")
    expect(timerTone(timer({ used_seconds: 1000, pct: 111 }))).toBe("late")
    const done = timer({ used_seconds: 130, stopped_at: "2026-10-12T10:02:10Z", outcome: "met", running: false })
    expect(timerText(done)).toBe("Done in 2m 10s ✓")
    expect(timerTone(done)).toBe("ok")
    const quote = timer({ stage: "quote", clock: "working", target_seconds: 8 * 3600, used_seconds: 3600, paused: true })
    expect(timerText(quote)).toBe("Paused while we wait on the customer")
    expect(timerText({ ...quote, paused: false })).toBe("7h 00m left (working time)")
  })

  it("tells the history in words", () => {
    const at = "2026-10-12T10:00:00Z"
    expect(eventText({ kind: "assigned", actor: "agent:enquiry", actor_name: "Enquiry Agent", at,
      detail: { user_id: "u1", rule: "round_robin", reason: "Next in the team" } }, { u1: "Asha" }))
      .toBe("Given to Asha (Round-robin): Next in the team")
    expect(eventText({ kind: "acknowledged", actor: "agent:enquiry", actor_name: "", at,
      detail: { status: "queued", questions: ["qty", "required_date"] } }))
      .toBe("Acknowledgement sent, with 2 questions")
    expect(eventText({ kind: "escalated", actor: "system:forge", actor_name: "", at,
      detail: { stage: "quote", level: "head_of_department" } }))
      .toBe("Quote is late: told the head of department")
  })

  it("shows quantities without trailing zeros", () => {
    expect(qtyText("25.000", "MT")).toBe("25 MT")
    expect(qtyText("12.500", null)).toBe("12.5")
    expect(qtyText(null, "nos")).toBe("nos")
  })

  it("colours grades by what is good: high fit and intent, low risk", () => {
    expect(gradeTone("fit", "High")).toBe("green")
    expect(gradeTone("intent", "Low")).toBe("red")
    expect(gradeTone("risk", "High")).toBe("red")
    expect(gradeTone("risk", "Low")).toBe("green")
    expect(gradeTone("risk", "Medium")).toBe("amber")
    expect(gradeTone("fit", undefined)).toBe("neutral")
    expect(priorityTone(82)).toBe("green")
    expect(priorityTone(50)).toBe("teal")
    expect(priorityTone(20)).toBe("amber")
    expect(priorityTone(null)).toBe("neutral")
  })

  it("tells the check, the review and the score in words", () => {
    const at = "2026-10-12T10:00:00Z"
    expect(eventText({ kind: "checked", actor: "agent:enquiry", actor_name: "", at,
      detail: { make_ability: "eto", source: "model" } })).toBe("Can we make it: Needs engineering (with the AI)")
    expect(eventText({ kind: "review_done", actor: "user:x", actor_name: "", at,
      detail: { outcome: "cannot_make", notes: "No crane bay" } })).toBe("Engineering review: we can't make it: No crane bay")
    expect(eventText({ kind: "scored", actor: "system:forge", actor_name: "", at,
      detail: { score: 72, before: 48, why: "customer replied" } })).toBe("Priority 48 → 72 (customer replied)")
    expect(eventText({ kind: "scored", actor: "system:forge", actor_name: "", at,
      detail: { score: 60, before: null, why: "new enquiry" } })).toBe("Priority set to 60 (new enquiry)")
  })
})
