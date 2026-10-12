import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Loading } from "@/components/ui/States"
import { LANGUAGES } from "./labels"
import type { AckDraft, EnquiryDetail } from "./types"

/** The acknowledgement for a person to check and send: for tenders, strategic accounts,
 *  and anything Forge did not read with the AI. It may not promise prices, discounts,
 *  delivery dates or what we can supply (N7 A); Forge refuses such a message. */
export function AckComposer({ e, onClose }: { e: EnquiryDetail; onClose: () => void }) {
  const qc = useQueryClient()
  const draft = useQuery({
    queryKey: ["enquiry", e.id, "ack"],
    queryFn: async () => (await api.get<AckDraft>(`/enquiries/${e.id}/acknowledgement`)).data,
    staleTime: 0,
  })
  const [edit, setEdit] = useState<{ subject: string; body: string; language: "en" | "hi" | "te" } | null>(null)
  const v = edit ?? (draft.data ? { subject: draft.data.subject, body: draft.data.body, language: draft.data.language } : null)
  const send = useMutation({
    mutationFn: async () => (await api.post<EnquiryDetail>(`/enquiries/${e.id}/acknowledge`, {
      ...v, question_keys: draft.data?.question_keys ?? [],
    })).data,
    onSuccess: (data) => {
      qc.setQueryData(["enquiry", e.id], data)
      qc.invalidateQueries({ queryKey: ["enquiries"] })
      onClose()
    },
  })
  return (
    <Card className="border-teal-500">
      <CardHeader className="pb-2"><CardTitle>Acknowledge the customer</CardTitle></CardHeader>
      <div className="px-4 pb-4 space-y-3">
        {draft.isLoading && <Loading />}
        {draft.error && <p className="text-red-500 text-[12.5px]">{errorMessage(draft.error, "Could not write the acknowledgement")}</p>}
        {v && (
          <>
            {draft.data?.sent_before && (
              <p className="rounded-md bg-amber-100 text-ink-700 text-[12.5px] p-2">An acknowledgement was already sent. This sends another one.</p>
            )}
            <Field label="Subject"><Input value={v.subject} onChange={(ev) => setEdit({ ...v, subject: ev.target.value })} maxLength={300} /></Field>
            <Field label="Message" hint="Do not promise a price, a discount, a delivery date or that we can supply it: Forge will not send that until the product knowledge base is in place.">
              <textarea value={v.body} onChange={(ev) => setEdit({ ...v, body: ev.target.value })} rows={12}
                className="w-full rounded-md border border-input bg-surface-1 px-3 py-2 text-[13.5px] font-body focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-teal-500" />
            </Field>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Language" className="w-40">
                <Select value={v.language} onChange={(ev) => setEdit({ ...v, language: ev.target.value as "en" | "hi" | "te" })}>
                  {Object.entries(LANGUAGES).map(([k, n]) => <option key={k} value={k}>{n}</option>)}
                </Select>
              </Field>
              {(draft.data?.question_keys.length ?? 0) > 0 && (
                <p className="text-[12px] text-ink-500 pb-2">
                  Asks for: {draft.data?.question_keys.map((k) => draft.data?.checklist.find((c) => c.key === k)?.label ?? k).join(", ")}.
                  The quote timer pauses until the customer answers.
                </p>
              )}
            </div>
            {send.error && <p role="alert" className="text-red-500 text-[13px]">{errorMessage(send.error, "Could not send")}</p>}
            <div className="flex gap-2">
              <Button onClick={() => send.mutate()} disabled={send.isPending || !v.subject.trim() || !v.body.trim()}>
                {send.isPending ? "Sending…" : "Send to the customer"}
              </Button>
              <Button variant="secondary" onClick={onClose}>Cancel</Button>
            </div>
          </>
        )}
      </div>
    </Card>
  )
}
