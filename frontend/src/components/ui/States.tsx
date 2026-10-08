import { errorMessage } from "@/config/api"

export function Loading() {
  return <p className="text-ink-500 text-sm">Loading…</p>
}

export function ErrorText({ error, fallback }: { error: unknown; fallback: string }) {
  if (!error) return null
  return <p className="text-red-500 text-sm">{errorMessage(error, fallback)}</p>
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-ink-300 text-[13px] p-4">{children}</p>
}
