import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Tag } from "@/components/ui/Tag"
import { formatDateTime } from "@/lib/utils"
import { useAuthStore } from "@/stores/authStore"
import { downloadFile } from "./files"
import { MAKE_TONE, qtyText } from "./labels"
import type { EnquiryDetail } from "./types"

/** The engineering review (P2 A): the tech package (lines, files, reasons) for the
 *  engineering role. Anyone in the role takes it and answers; "we can't make it"
 *  needs the reason, which goes to the person who approves the decline (P1 A). */
export function ReviewCard({ e }: { e: EnquiryDetail }) {
  const rv = e.review!
  const me = useAuthStore((s) => s.me)
  const qc = useQueryClient()
  const [answer, setAnswer] = useState<null | "can_make" | "cannot_make">(null)
  const [notes, setNotes] = useState("")
  const [err, setErr] = useState<string | null>(null)
  const act = useMutation({
    mutationFn: async ({ path, body }: { path: string; body?: unknown }) =>
      (await api.post<EnquiryDetail>(`/enquiries/${e.id}/reviews/${rv.id}/${path}`, body ?? {})).data,
    onSuccess: (data) => {
      qc.setQueryData(["enquiry", e.id], data)
      qc.invalidateQueries({ queryKey: ["enquiries"] })
      setAnswer(null)
      setNotes("")
    },
  })
  const open = rv.status === "open"
  const mine = rv.assignee_id && me && rv.assignee_id === me.id
  const pkg = rv.package
  return (
    <Card className={open ? "border-amber-500" : undefined}>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle>Engineering review</CardTitle>
        {open ? <Tag variant="amber">Waiting</Tag> : rv.status === "done"
          ? <Tag variant={rv.outcome === "can_make" ? "green" : "red"}>{rv.outcome_text}</Tag>
          : <Tag>No longer needed</Tag>}
      </CardHeader>
      <div className="px-4 pb-4 space-y-3 text-[13px]">
        <p className="text-ink-500">
          For {rv.role ?? "the escalation people (no engineering role is set up)"}
          {rv.assignee ? ` · taken by ${rv.assignee}` : open ? " · not taken yet" : ""}
          {` · sent ${formatDateTime(rv.opened_at)}`}
        </p>
        {rv.reasons.length > 0 && (
          <div>
            <p className="font-semibold text-ink-700">Why it needs an engineer</p>
            <ul className="list-disc pl-5 text-ink-700">{rv.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          </div>
        )}
        {pkg.special_requirements && <p><span className="font-semibold text-ink-700">Asked for:</span> {pkg.special_requirements}</p>}
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px] min-w-[480px]">
            <tbody>
              {pkg.lines.map((l) => (
                <tr key={l.position} className="border-b border-line last:border-0 align-top">
                  <td className="py-1.5 pr-2 text-ink-300 w-6">{l.position}</td>
                  <td className="py-1.5 pr-2">
                    <span className="text-ink-900">{l.description}</span>
                    {l.dimensions && <span className="text-ink-500"> · {l.dimensions}</span>}
                    {[l.material, l.standard].some(Boolean) && <div className="text-ink-500">{[l.material, l.standard].filter(Boolean).join(" · ")}</div>}
                    {l.reasons.length > 0 && <div className="text-[11.5px] text-ink-500">{l.reasons.join("; ")}</div>}
                  </td>
                  <td className="py-1.5 pr-2 text-right font-mono whitespace-nowrap">{qtyText(l.qty, l.unit)}</td>
                  <td className="py-1.5 text-right">{l.make_ability && <Tag variant={MAKE_TONE[l.make_ability]}>{l.make_ability === "eto" ? "Engineer" : l.make_ability === "no_fit" ? "No fit" : l.make_ability === "cto" ? "Config" : "Std"}</Tag>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {pkg.files.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {pkg.files.map((f) => (
              <button key={f.file_id} type="button" className="rounded-md border border-line px-2.5 py-1 text-[12.5px] text-teal-600 hover:bg-teal-100"
                onClick={() => { setErr(null); downloadFile(f.file_id, f.filename).catch(() => setErr(`Could not download ${f.filename}`)) }}>
                ⤓ {f.filename}
              </button>
            ))}
          </div>
        )}
        {rv.status === "done" && rv.notes && <p><span className="font-semibold text-ink-700">Engineer's note:</span> {rv.notes}</p>}
        {rv.done_at && <p className="text-[11.5px] text-ink-300">Answered {formatDateTime(rv.done_at)}</p>}
        {(err || act.error) && <p role="alert" className="text-red-500 text-[12.5px]">{err ?? errorMessage(act.error, "That did not work")}</p>}
        {open && e.may.work && (
          answer === null ? (
            <div className="flex flex-wrap gap-2">
              {!mine && <Button size="sm" variant="secondary" onClick={() => act.mutate({ path: "take" })} disabled={act.isPending}>Take it</Button>}
              <Button size="sm" onClick={() => setAnswer("can_make")}>We can make it</Button>
              <Button size="sm" variant="danger" onClick={() => setAnswer("cannot_make")}>We can't make it</Button>
            </div>
          ) : (
            <div className="space-y-2 rounded-md border border-teal-500 p-3">
              <p className="font-semibold text-ink-900">{answer === "can_make" ? "We can make it" : "We can't make it"}</p>
              <textarea value={notes} onChange={(ev) => setNotes(ev.target.value)} maxLength={2000} rows={3}
                className="w-full rounded-md border border-input bg-surface-1 px-3 py-2 text-[13px]"
                placeholder={answer === "can_make" ? "Anything the quote should know (optional)" : "Why not (needed): it goes to the person who approves the decline"}
                aria-label="Engineer's note" />
              <div className="flex gap-2">
                <Button size="sm" disabled={act.isPending || (answer === "cannot_make" && !notes.trim())}
                  onClick={() => act.mutate({ path: "done", body: { outcome: answer, notes: notes.trim() || null } })}>Save the answer</Button>
                <Button size="sm" variant="secondary" onClick={() => setAnswer(null)}>Cancel</Button>
              </div>
            </div>
          )
        )}
      </div>
    </Card>
  )
}
