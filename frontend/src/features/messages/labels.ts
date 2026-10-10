import type { MessageStatus, MessageView, Purpose } from "./types"

type Tone = "teal" | "gold" | "green" | "red" | "amber" | "neutral"

/** Tag colour per status: green has left Forge, gold waits, red stopped or failed. */
export const STATUS_TONE: Record<MessageStatus, Tone> = {
  draft: "neutral", review: "gold", queued: "teal", held: "gold", sending: "teal",
  sent: "green", delivered: "green", read: "green", failed: "red", bounced: "red",
  blocked: "red", cancelled: "neutral", received: "teal",
}

/** Mirrors backend app/channels/statuses.py LABELS, for views that do not carry status_label. */
export const STATUS_LABELS: Record<MessageStatus, string> = {
  draft: "Draft", review: "Waiting for a language review", queued: "Queued", held: "Held", sending: "Sending",
  sent: "Sent", delivered: "Delivered", read: "Read", failed: "Failed", bounced: "Bounced",
  blocked: "Stopped by a check", cancelled: "Cancelled", received: "Received",
}

export const PURPOSE_LABELS: Record<Purpose, string> = {
  promotional: "Promotional",
  transactional: "About their business with us",
  reply: "A reply",
  staff: "Notification to staff",
}

export const PURPOSE_HINTS: Record<string, string> = {
  promotional: "Offers and news. Follows the daily and weekly limits, quiet hours and rest days, and never goes to someone who asked to stop.",
  transactional: "Quotes, orders, invoices, delivery. Not limited, but waits for the end of quiet hours.",
  reply: "Answering a customer who wrote to us. Goes at once within the reply window, even in quiet hours.",
}

export const CHECK_LABELS: Record<string, string> = {
  recipient: "Who it is for",
  address: "Email address",
  controls: "Agent stops and takeovers",
  content: "Subject and text",
  consent: "Consent",
  timing: "Limits and quiet hours",
}

export const CHANNEL_LABELS: Record<string, string> = { email: "Email", whatsapp: "WhatsApp", sms: "SMS", all: "Every channel" }

export const SOURCE_LABELS: Record<string, string> = {
  person: "a person", forge: "Forge", agent: "an agent", simulator: "the Test inbox", smtp: "the mail server",
}

export const REVIEW_WHY: Record<string, string> = {
  sample: "Sample check",
  checks: "Its checks found problems",
}

/** Filters on the Messages list. `status` is sent as a comma list. */
export const FILTERS: { key: string; label: string; status?: string; direction?: "in" | "out" }[] = [
  { key: "all", label: "All" },
  { key: "waiting", label: "Waiting", status: "review,queued,held,sending" },
  { key: "sent", label: "Sent", status: "sent,delivered,read" },
  { key: "problems", label: "Problems", status: "failed,bounced,blocked" },
  { key: "received", label: "Received", direction: "in" },
  { key: "drafts", label: "Drafts", status: "draft" },
]

export const DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

/** Who a message is to or from, in a few words. */
export function partyLabel(m: Pick<MessageView, "direction" | "contact_name" | "user_name" | "to_address"
  | "from_address" | "account_name" | "purpose" | "erased_at">): string {
  if (m.erased_at) return m.direction === "in" ? "From an erased contact" : "To an erased contact"
  const who = m.purpose === "staff"
    ? (m.user_name ?? m.to_address ?? "a colleague")
    : (m.contact_name ?? (m.direction === "in" ? m.from_address : m.to_address) ?? "someone not yet a contact")
  const at = m.account_name && m.purpose !== "staff" ? ` at ${m.account_name}` : ""
  return `${m.direction === "in" ? "From" : "To"} ${who}${at}`
}

/** Why a message is waiting and until when, e.g. "Quiet hours: goes at 09:00 on Sat 11 Oct". */
export function waitLabel(m: Pick<MessageView, "status" | "reason" | "send_after">, now = Date.now()): string | null {
  if (m.status === "held") return m.reason ?? "Held until its sending time"
  if (m.status === "queued") {
    if (!m.send_after) return "Going out now"
    const ms = Date.parse(m.send_after) - now
    if (ms <= 60_000) return "Going out now"
    const min = Math.round(ms / 60_000)
    return m.reason ? m.reason : `Trying again in ${min < 60 ? `${min} min` : `${Math.round(min / 60)} h`}`
  }
  if (m.status === "review") return m.reason ?? "Waits for a person to check the writing"
  return null
}

/** What a person can do with a message, by its status and direction. */
export function actionsFor(m: Pick<MessageView, "status" | "direction" | "purpose">): {
  edit: boolean; send: boolean; cancel: boolean; sendAnyway: boolean; resend: boolean; reply: boolean
} {
  const out = m.direction === "out" && m.purpose !== "staff"
  return {
    edit: out && m.status === "draft",
    send: out && m.status === "draft",
    cancel: out && ["draft", "review", "queued", "held"].includes(m.status),
    sendAnyway: out && m.status === "held",
    resend: out && m.status === "failed",
    reply: m.direction === "in",
  }
}

/** The first line of a body, cut to `n` characters. */
export function snippet(body: string | null | undefined, n = 120): string {
  const line = (body ?? "").trim().split(/\n+/)[0] ?? ""
  return line.length > n ? `${line.slice(0, n - 1)}…` : line
}

export function scoreLabel(avg: number | null): string {
  return avg === null ? "–" : `${avg.toFixed(1)} / 5`
}
