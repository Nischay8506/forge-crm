import { useEffect, useRef } from "react"
import { api } from "@/config/api"

/** F12.3 "decidable in under 60 seconds": how long a card was in front of the person
 * before they decided. A card counts from when it came on screen, or from the
 * person's previous decision on this page if that was later (several cards can be
 * on screen at once, and people decide them one after another). */
let lastActionAt = Date.now()

export function markAction(now = Date.now()) {
  lastActionAt = now
}

export function shownMs(now: number, visibleSince: number | null, last: number): number | null {
  if (visibleSince === null) return null
  return Math.max(0, Math.round(now - Math.max(visibleSince, last)))
}

/** Watches a card; tells Forge once when it is first on screen. Returns a function
 * giving the time to send with the decision (null if it was never on screen). */
export function useCardTiming(decisionId: string, active: boolean) {
  const ref = useRef<HTMLDivElement>(null)
  const since = useRef<number | null>(null)
  useEffect(() => {
    const el = ref.current
    if (!active || !el || typeof IntersectionObserver === "undefined") return
    const io = new IntersectionObserver((entries) => {
      const visible = entries.some((e) => e.isIntersecting)
      if (visible && since.current === null) {
        since.current = Date.now()
        api.post(`/decisions/${decisionId}/shown`).catch(() => undefined)
      }
    }, { threshold: 0.3 })
    io.observe(el)
    return () => io.disconnect()
  }, [decisionId, active])
  return { ref, elapsed: () => shownMs(Date.now(), since.current, lastActionAt) }
}

/** "42 s", "1 min 5 s". */
export function durationLabel(ms: number): string {
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  return s % 60 ? `${m} min ${s % 60} s` : `${m} min`
}
