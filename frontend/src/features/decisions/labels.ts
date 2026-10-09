/** Words for the approval inbox (F12.3). Pure functions, tested in labels.test.ts. */

export const OPTION_LABELS: Record<string, string> = {
  approve: "Approve", reject: "Reject", counter: "Counter",
}

export const STEP_STATUS: Record<string, string> = {
  waiting: "Waiting its turn", pending: "Deciding now", approved: "Approved", rejected: "Rejected",
  countered: "Countered", cancelled: "Not needed", superseded: "Closed",
}

function span(ms: number): string {
  const min = Math.max(1, Math.round(ms / 60_000))
  if (min < 60) return `${min} min`
  const h = Math.floor(min / 60)
  const m = min % 60
  if (h < 48) return m ? `${h} h ${m} min` : `${h} h`
  return `${Math.round(h / 24)} days`
}

/** "Due in 2 h 10 min", "Past its deadline by 25 min". */
export function dueLabel(due: string, now = Date.now()): string {
  const ms = new Date(due).getTime() - now
  return ms >= 0 ? `Due in ${span(ms)}` : `Past its deadline by ${span(-ms)}`
}

/** "42 s", "1 min 5 s", "–". */
export function secondsLabel(s: number | null | undefined): string {
  if (s === null || s === undefined) return "–"
  const r = Math.round(s)
  if (r < 60) return `${r} s`
  return r % 60 ? `${Math.floor(r / 60)} min ${r % 60} s` : `${Math.floor(r / 60)} min`
}

/** "1.5 working hours", "–". */
export function workingHours(h: number | string | null | undefined): string {
  if (h === null || h === undefined || h === "") return "–"
  const n = Number(h)
  const v = Number.isInteger(n) ? String(n) : n.toFixed(1)
  return `${v} working hour${n === 1 ? "" : "s"}`
}

export function pct(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? "–" : `${Math.round(rate * 100)}%`
}

/** "2 approved, 1 rejected". */
export function tallyLabel(byStatus: Record<string, number>): string {
  const order = ["approved", "countered", "rejected", "failed", "superseded"]
  return Object.entries(byStatus)
    .sort(([a], [b]) => order.indexOf(a) - order.indexOf(b))
    .map(([k, n]) => `${n} ${k}`).join(", ")
}

/** How old a copy is: "3 min ago", "2 h ago". */
export function agoLabel(at: string, now = Date.now()): string {
  return `${span(Math.max(0, now - new Date(at).getTime()))} ago`
}
