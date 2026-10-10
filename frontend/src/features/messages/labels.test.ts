import { describe, expect, it } from "vitest"
import { actionsFor, partyLabel, scoreLabel, snippet, waitLabel } from "./labels"

const base = {
  direction: "out" as const, contact_name: "Asha Rao", user_name: null, to_address: "asha@example.in",
  from_address: null, account_name: "Rao Steels", purpose: "transactional" as const, erased_at: null,
}

describe("message labels", () => {
  it("names who a message is to or from", () => {
    expect(partyLabel(base)).toBe("To Asha Rao at Rao Steels")
    expect(partyLabel({ ...base, direction: "in", contact_name: null, account_name: null, from_address: "x@y.in" }))
      .toBe("From x@y.in")
    expect(partyLabel({ ...base, purpose: "staff", user_name: "Ravi" })).toBe("To Ravi")
    expect(partyLabel({ ...base, erased_at: "2026-10-10T10:00:00Z" })).toBe("To an erased contact")
  })

  it("says why a message waits", () => {
    const now = Date.parse("2026-10-10T10:00:00Z")
    expect(waitLabel({ status: "held", reason: "Quiet hours: goes at 09:00 on Sat 11 Oct", send_after: null }, now))
      .toBe("Quiet hours: goes at 09:00 on Sat 11 Oct")
    expect(waitLabel({ status: "queued", reason: null, send_after: "2026-10-10T10:00:30Z" }, now)).toBe("Going out now")
    expect(waitLabel({ status: "queued", reason: null, send_after: "2026-10-10T10:15:00Z" }, now)).toBe("Trying again in 15 min")
    expect(waitLabel({ status: "sent", reason: null, send_after: null }, now)).toBeNull()
  })

  it("offers only the actions a status allows", () => {
    expect(actionsFor({ status: "draft", direction: "out", purpose: "promotional" })).toMatchObject({ edit: true, send: true, cancel: true })
    expect(actionsFor({ status: "held", direction: "out", purpose: "promotional" })).toMatchObject({ sendAnyway: true, cancel: true, send: false })
    expect(actionsFor({ status: "failed", direction: "out", purpose: "reply" })).toMatchObject({ resend: true, cancel: false })
    expect(actionsFor({ status: "sent", direction: "out", purpose: "staff" })).toMatchObject({ cancel: false, resend: false })
    expect(actionsFor({ status: "received", direction: "in", purpose: "reply" }).reply).toBe(true)
  })

  it("shortens text and scores", () => {
    expect(snippet("Hello\nsecond line")).toBe("Hello")
    expect(snippet("abcdefghij", 5)).toBe("abcd…")
    expect(scoreLabel(4.25)).toBe("4.3 / 5")
    expect(scoreLabel(null)).toBe("–")
  })
})
