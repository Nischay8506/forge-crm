import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Input } from "@/components/ui/Field"
import { ErrorText } from "@/components/ui/States"

/** Ask for a contact's personal data to be erased (R6). A second person approves it
 * on the Decisions screen; until then nothing changes. */
export function EraseContact({ contactId, onClose }: { contactId: string; onClose: () => void }) {
  const [reason, setReason] = useState("")
  const ask = useMutation({
    mutationFn: async () => (await api.post<{ status: string; reasons: string[] }>(
      "/privacy/erasures", { contact_id: contactId, reason })).data,
  })
  if (ask.data) {
    return (
      <div className="rounded-md bg-gold-100 p-2 text-[12.5px] space-y-1">
        {ask.data.status === "waiting" ? (
          <p>
            Asked. A second person allowed to erase personal data must approve it on{" "}
            <Link to="/decisions" className="underline font-semibold">Decisions</Link>; you cannot approve your own request.
          </p>
        ) : <p>Not asked ({ask.data.status}): {ask.data.reasons.join("; ")}</p>}
        <button className="text-ink-500 hover:underline" onClick={onClose}>Close</button>
      </div>
    )
  }
  return (
    <form className="rounded-md bg-surface-2 p-2 space-y-2 text-[12.5px]" onSubmit={(e) => { e.preventDefault(); ask.mutate() }}>
      <p className="text-ink-700">
        Erasing removes this person's name, email, phone and other personal data from Forge and from the audit
        trail's details. Only what the law requires stays (details behind money and customer messages, until their
        retention period ends), and a stub keeps the contact's place. It cannot be undone.
      </p>
      <Input autoFocus placeholder="Why? e.g. the person asked to be forgotten on 9 Oct" value={reason}
        onChange={(e) => setReason(e.target.value)} />
      <div className="flex gap-2">
        <Button size="sm" variant="danger" type="submit" disabled={reason.trim().length < 5 || ask.isPending}>
          Ask a second person to approve
        </Button>
        <Button size="sm" type="button" variant="secondary" onClick={onClose}>Cancel</Button>
      </div>
      <ErrorText error={ask.error} fallback="Could not ask for the erasure" />
    </form>
  )
}
