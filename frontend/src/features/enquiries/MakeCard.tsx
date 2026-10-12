import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Tag } from "@/components/ui/Tag"
import { formatDateTime } from "@/lib/utils"
import { MAKE_SOURCE, MAKE_TONE } from "./labels"
import type { EnquiryDetail } from "./types"

/** "Can we make it?" (F1.6): the stamp, why, and who decided it. "Check again" runs the
 *  rules and, with an AI key, the AI on the enquiry as it is now. */
export function MakeCard({ e }: { e: EnquiryDetail }) {
  const qc = useQueryClient()
  const open = ["new", "open", "waiting_customer", "parked"].includes(e.status)
  const again = useMutation({
    mutationFn: async () => (await api.post<EnquiryDetail>(`/enquiries/${e.id}/check`)).data,
    onSuccess: (data) => { qc.setQueryData(["enquiry", e.id], data); qc.invalidateQueries({ queryKey: ["enquiries"] }) },
  })
  const m = e.make
  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle>Can we make it?</CardTitle></CardHeader>
      <div className="px-4 pb-4 space-y-2 text-[13px]">
        <div>{m.ability ? <Tag variant={MAKE_TONE[m.ability]}>{m.label}</Tag> : <Tag>{m.label}</Tag>}</div>
        {m.reasons.length > 0 && (
          <ul className="list-disc pl-5 space-y-0.5 text-ink-700">
            {m.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        )}
        {m.ability === "no_fit" && open && (
          <p className="text-[12px] text-ink-500">
            {e.account_tier === "strategic"
              ? "A strategic account is never declined by Forge: its owner and manager were told."
              : "A polite decline is waiting for a person to approve it under Decisions; nothing is sent before that."}
          </p>
        )}
        {m.checked_at && m.source && (
          <p className="text-[11.5px] text-ink-300">Checked by {MAKE_SOURCE[m.source] ?? m.source}, {formatDateTime(m.checked_at)}</p>
        )}
        {again.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(again.error, "Could not check it")}</p>}
        {open && e.may.work && (
          <Button size="sm" variant="secondary" onClick={() => again.mutate()} disabled={again.isPending}>
            {again.isPending ? "Checking…" : "Check again"}
          </Button>
        )}
      </div>
    </Card>
  )
}
