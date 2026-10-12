import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input } from "@/components/ui/Field"
import { Loading } from "@/components/ui/States"
import { useEnquirySettings } from "./hooks"
import { PART_LABELS } from "./labels"
import type { EnquirySettings } from "./types"

const PARTS = ["fit", "intent", "risk"] as const
const PART_HINTS = {
  fit: "Can we make it, is the order the size we like, is the customer in an industry we want.",
  intent: "How formal the request is, how soon it is needed, how senior the person asking is.",
  risk: "Overdue money in the ERP, an export or watched country, a new or unknown customer.",
}

/** Lead scoring v1 (F1.7, N15 A): the weights behind Fit, Intent and Risk. No learned
 *  model and no hidden numbers; a change applies to every open enquiry at once. */
export function ScoringSetup({ canManage }: { canManage: boolean }) {
  const { data, isLoading } = useEnquirySettings()
  if (isLoading || !data) return <Loading />
  return <Form s={data} canManage={canManage} />
}

function Form({ s, canManage }: { s: EnquirySettings; canManage: boolean }) {
  const qc = useQueryClient()
  const sc = s.scoring
  const [w, setW] = useState(() => Object.fromEntries(PARTS.map((p) =>
    [p, Object.fromEntries(Object.entries(sc[p]).map(([k, x]) => [k, String(x)]))])) as Record<typeof PARTS[number], Record<string, string>>)
  const [riskWeight, setRiskWeight] = useState(String(sc.risk_weight))
  const [jump, setJump] = useState(String(sc.jump))
  const [watch, setWatch] = useState(sc.export_watch.join(", "))
  const save = useMutation({
    mutationFn: async () => (await api.put<EnquirySettings>("/enquiries/setup/settings/scoring", {
      ...Object.fromEntries(PARTS.map((p) => [p, Object.fromEntries(Object.entries(w[p]).map(([k, x]) => [k, Number(x)]))])),
      risk_weight: Number(riskWeight), jump: Number(jump),
      export_watch: watch.split(",").map((x) => x.trim()).filter(Boolean),
    })).data,
    onSuccess: (data) => {
      qc.setQueryData(["enquiries", "settings"], data)
      qc.invalidateQueries({ queryKey: ["enquiries", "list"] })
    },
  })
  return (
    <div className="space-y-4 max-w-[760px]">
      <Card className="p-4 text-[13px] text-ink-700 space-y-1">
        <p>Each enquiry gets three grades, <b>Fit</b>, <b>Intent</b> and <b>Risk</b>, and one priority from 0 to 100 that orders the queue.</p>
        <p className="text-ink-500">Priority comes from Fit and Intent together (a low one pulls it down more than an average would), then Risk lowers it.
          Each signal scores 0 to 100; the weights say how much it counts within its grade.
          The score is worked out again on every new signal and every hour.</p>
      </Card>
      {PARTS.map((p) => (
        <Card key={p}>
          <CardHeader className="pb-1"><CardTitle>{PART_LABELS[p]}</CardTitle></CardHeader>
          <div className="px-4 pb-4 space-y-2 text-[13px]">
            <p className="text-ink-500">{PART_HINTS[p]}</p>
            <div className="grid gap-2 sm:grid-cols-3">
              {Object.keys(sc[p]).map((k) => (
                <Field key={k} label={s.signal_labels[p][k] ?? k}>
                  <Input value={w[p][k]} onChange={(e) => setW({ ...w, [p]: { ...w[p], [k]: e.target.value } })} inputMode="numeric" disabled={!canManage} />
                </Field>
              ))}
            </div>
          </div>
        </Card>
      ))}
      <Card>
        <CardHeader className="pb-1"><CardTitle>Priority</CardTitle></CardHeader>
        <div className="px-4 pb-4 grid gap-2 sm:grid-cols-3 text-[13px]">
          <Field label="Risk lowers it by up to (%)" hint="0 to 90"><Input value={riskWeight} onChange={(e) => setRiskWeight(e.target.value)} inputMode="numeric" disabled={!canManage} /></Field>
          <Field label="Tell the owner on a change of" hint="Points, 5 to 100"><Input value={jump} onChange={(e) => setJump(e.target.value)} inputMode="numeric" disabled={!canManage} /></Field>
          <Field label="Countries to watch" hint="Two-letter codes, e.g. CN, IR"><Input value={watch} onChange={(e) => setWatch(e.target.value)} disabled={!canManage} /></Field>
        </div>
      </Card>
      {save.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not save")}</p>}
      {save.isSuccess && <p role="status" className="text-green-500 text-[12.5px]">Saved. Open enquiries were scored again.</p>}
      {canManage && <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save the weights</Button>}
    </div>
  )
}
