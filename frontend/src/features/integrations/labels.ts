/** Words and tones for integration screens. */

type Tone = "teal" | "gold" | "green" | "red" | "amber" | "neutral"

export const HEALTH: Record<string, { label: string; tone: Tone }> = {
  ok: { label: "Healthy", tone: "green" },
  problem: { label: "Needs attention", tone: "red" },
  paused: { label: "Paused", tone: "neutral" },
}

export const RUN_TONES: Record<string, Tone> = {
  running: "teal", succeeded: "green", partial: "amber", failed: "red",
}

export const LINK_STATUS: Record<string, { label: string; tone: Tone; help: string }> = {
  review: { label: "To review", tone: "gold", help: "The match is not certain, so a person picks the account." },
  pending: { label: "Not linked", tone: "neutral", help: "Disabled in the ERP and matches no account, so nothing was created." },
  linked: { label: "Linked", tone: "green", help: "Linked to a Forge account." },
  ignored: { label: "Ignored", tone: "neutral", help: "A person chose not to link this customer." },
}

export const LINK_METHODS: Record<string, string> = {
  gstin: "linked by the same GSTIN",
  pan: "linked by the same PAN",
  cin: "linked by the same CIN",
  manual: "linked by a person",
  new_account: "made into a new account",
  created_in_erp: "created in the ERP from Forge",
  unlinked: "unlinked by a person",
  disabled: "disabled in the ERP",
}

export const SUGGESTION_SOURCES: Record<string, string> = {
  exact: "Same code",
  alias: "Known alias",
  rate: "Same GST rate",
  similar: "Similar name",
  seed: "First ERP's list",
  manual: "Set by a person",
}

export const WRITE_STATUS: Record<string, { label: string; tone: Tone }> = {
  pending: { label: "Waiting for approval", tone: "gold" },
  sending: { label: "Sending", tone: "teal" },
  succeeded: { label: "Created", tone: "green" },
  failed: { label: "Failed", tone: "red" },
  unknown: { label: "Outcome unknown", tone: "amber" },
  refused: { label: "Refused", tone: "red" },
}

export const ORDER_TONES: Record<string, Tone> = {
  draft: "neutral", open: "teal", on_hold: "amber", partly_delivered: "gold",
  delivered: "green", closed: "neutral", cancelled: "red",
}

export const INVOICE_TONES: Record<string, Tone> = {
  draft: "neutral", unpaid: "gold", partly_paid: "amber", overdue: "red", paid: "green",
  credit_note: "neutral", cancelled: "neutral",
}

export const SETTING_LABELS: Record<string, string> = {
  time_zone: "Time zone",
  company: "Company (for credit limits)",
  host_header: "Host header",
  bom_includes_overhead: "BOM costs already include the factory overhead",
  bom_includes_packing: "BOM costs already include the packing",
}

export function words(s: string | null | undefined): string {
  return (s ?? "").replace(/_/g, " ")
}

/** "3 min ago", "2 h ago", "4 days ago"; null shows a dash. */
export function ago(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "–"
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (s < 60) return "just now"
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 48) return `${h} h ago`
  return `${Math.round(h / 24)} days ago`
}

/** Money as the ERP sent it, grouped the Indian way. */
export function money(amount: string | number | null | undefined, currency: string | null | undefined): string {
  if (amount === null || amount === undefined || amount === "") return "–"
  const n = typeof amount === "number" ? amount : Number(amount)
  if (Number.isNaN(n)) return String(amount)
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency", currency: currency || "INR", maximumFractionDigits: 2,
    }).format(n)
  } catch {
    return `${currency ?? ""} ${n.toLocaleString("en-IN")}`.trim()
  }
}
