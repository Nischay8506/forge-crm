import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Input } from "@/components/ui/Field"
import { ErrorText } from "@/components/ui/States"
import { hasPermission } from "@/lib/permissions"
import { usePermissions } from "@/stores/authStore"
import type { PersonalData } from "./types"

/** Personal data in the trail is hidden by default (R4). People with the permission
 * can reveal it for this page after typing why; the reveal is recorded. */
export function PersonalDataBar<T>({ pd, revealUrl, onRevealed, revealed }: {
  pd: PersonalData; revealUrl: string; onRevealed: (data: T) => void; revealed: boolean
}) {
  const perms = usePermissions()
  const canReveal = hasPermission(perms, "audit.personal_data")
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState("")
  const reveal = useMutation({
    mutationFn: async () => (await api.post<T>(revealUrl, { reason })).data,
    onSuccess: (data) => { setOpen(false); setReason(""); onRevealed(data) },
  })
  if (revealed) {
    return (
      <p className="rounded-md bg-gold-100 text-ink-700 p-2 text-[12.5px]">
        Personal data is shown on this page because you revealed it. Your reason is in the audit log. Leaving or
        reloading the page hides it again.
      </p>
    )
  }
  if (pd.hidden === 0) return null
  return (
    <div className="rounded-md bg-surface-2 p-2 text-[12.5px] space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-ink-700">
          {pd.hidden} piece{pd.hidden === 1 ? "" : "s"} of personal data {pd.hidden === 1 ? "is" : "are"} hidden
          (names, emails, phone numbers, PAN, Aadhaar).
        </span>
        {canReveal && !open && (
          <button className="text-teal-600 hover:underline" onClick={() => setOpen(true)}>Show personal data…</button>
        )}
        {!canReveal && <span className="text-ink-300">Seeing it needs the permission “See personal data in the audit trail”.</span>}
      </div>
      {open && (
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); reveal.mutate() }}>
          <Input autoFocus className="max-w-md" placeholder="Why do you need to see it? (recorded)"
            value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button size="sm" type="submit" disabled={reason.trim().length < 10 || reveal.isPending}>
            {reveal.isPending ? "Revealing…" : "Reveal and record why"}
          </Button>
          <button type="button" className="text-ink-500 hover:underline" onClick={() => setOpen(false)}>Cancel</button>
          {reason.trim().length > 0 && reason.trim().length < 10 && (
            <span className="text-ink-300">A reason of at least 10 characters</span>
          )}
          <ErrorText error={reveal.error} fallback="Could not reveal" />
        </form>
      )}
    </div>
  )
}
