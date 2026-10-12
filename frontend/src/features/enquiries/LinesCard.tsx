import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Input } from "@/components/ui/Field"
import { Empty } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { cn } from "@/lib/utils"
import { qtyText } from "./labels"
import type { EnquiryDetail, Line } from "./types"

type Draft = { item_ref: string; description: string; material: string; dimensions: string; qty: string; unit: string; standard: string; notes: string }

const empty: Draft = { item_ref: "", description: "", material: "", dimensions: "", qty: "", unit: "", standard: "", notes: "" }

function toDraft(l: Line): Draft {
  return {
    item_ref: l.item_ref ?? "", description: l.description, material: l.material ?? "", dimensions: l.dimensions ?? "",
    qty: l.qty === null ? "" : String(Number(l.qty)), unit: l.unit ?? "", standard: l.standard ?? "", notes: l.notes ?? "",
  }
}

function toBody(d: Draft) {
  const t = (s: string) => s.trim() || null
  return {
    item_ref: t(d.item_ref), description: d.description.trim(), material: t(d.material), dimensions: t(d.dimensions),
    qty: d.qty.trim() === "" ? null : Number(d.qty), unit: t(d.unit), standard: t(d.standard), notes: t(d.notes),
  }
}

/** Fields the reader was not sure of, or could not read, until a person corrects the line. */
function unsure(l: Line, ...fields: string[]): boolean {
  return !l.corrected_at && fields.some((f) => (l.unsure ?? []).includes(f))
}

const CHECK = "rounded bg-amber-100 px-1 outline outline-1 outline-amber-500"

function sourceText(l: Line): string {
  if (l.source === "attachment") return l.source_ref ?? "attachment"
  if (l.source === "person") return "added by a person"
  return "email"
}

/** The items asked for, as read from the email and its files. Details the reader was
 *  not sure of, or could not read on a scan, are highlighted (left empty, never guessed).
 *  A person corrects a line in place; the line as first read is kept, and the latest
 *  corrections go with the next emails the reader reads, so it learns (F1.4). */
export function LinesCard({ e }: { e: EnquiryDetail }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState<string | "new" | null>(null)
  const [draft, setDraft] = useState<Draft>(empty)
  const [err, setErr] = useState<string | null>(null)
  const may = e.may.work && !["closed", "merged"].includes(e.status)
  const done = () => {
    setEditing(null)
    setErr(null)
    qc.invalidateQueries({ queryKey: ["enquiry", e.id] })
  }
  const save = useMutation({
    mutationFn: async () => {
      const body = toBody(draft)
      if (!body.description) throw new Error("Say what the item is")
      if (body.qty !== null && !(body.qty >= 0)) throw new Error("The quantity must be a number")
      return editing === "new"
        ? api.post(`/enquiries/${e.id}/lines`, body)
        : api.patch(`/enquiries/${e.id}/lines/${editing}`, body)
    },
    onSuccess: done,
    onError: (x) => setErr(x instanceof Error && !("isAxiosError" in x) ? x.message : errorMessage(x, "Could not save the line")),
  })
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/enquiries/${e.id}/lines/${id}`),
    onSuccess: done,
    onError: (x) => setErr(errorMessage(x, "Could not remove the line")),
  })
  const start = (id: string | "new", d: Draft) => { setEditing(id); setDraft(d); setErr(null) }
  const set = (k: keyof Draft) => (ev: React.ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, [k]: ev.target.value })

  const form = (
    <div className="space-y-2 rounded-md border border-teal-500 bg-surface-0 p-3">
      <div className="grid gap-2 sm:grid-cols-6">
        <Input className="sm:col-span-3" value={draft.description} onChange={set("description")} placeholder="Item (e.g. MS Plate)" aria-label="Item" autoFocus />
        <Input className="sm:col-span-2" value={draft.dimensions} onChange={set("dimensions")} placeholder="Size (e.g. 10mm x 1500 x 6300)" aria-label="Size" />
        <Input value={draft.item_ref} onChange={set("item_ref")} placeholder="Item no." aria-label="Item number" />
        <Input className="sm:col-span-2" value={draft.material} onChange={set("material")} placeholder="Grade / material" aria-label="Grade" />
        <Input className="sm:col-span-2" value={draft.standard} onChange={set("standard")} placeholder="Standard (e.g. IS 2062)" aria-label="Standard" />
        <Input value={draft.qty} onChange={set("qty")} placeholder="Qty" inputMode="decimal" aria-label="Quantity" />
        <Input value={draft.unit} onChange={set("unit")} placeholder="Unit" aria-label="Unit" />
        <Input className="sm:col-span-6" value={draft.notes} onChange={set("notes")} placeholder="Notes" aria-label="Notes" />
      </div>
      {err && <p className="text-red-500 text-[12.5px]">{err}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save line"}</Button>
        <Button size="sm" variant="secondary" onClick={() => { setEditing(null); setErr(null) }}>Cancel</Button>
      </div>
    </div>
  )

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle>What is asked for</CardTitle>
        {may && editing === null && <Button size="sm" variant="ghost" onClick={() => start("new", empty)}>+ Add a line</Button>}
      </CardHeader>
      <div className="px-4 pb-4 space-y-2">
        {e.lines.length === 0 && editing !== "new" && <Empty>No items were read. Add them from the email or its files.</Empty>}
        {e.lines.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px] min-w-[620px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-line">
                  <th className="py-2 pr-2 font-semibold w-8">#</th>
                  <th className="py-2 pr-2 font-semibold">Item</th>
                  <th className="py-2 pr-2 font-semibold">Grade / standard</th>
                  <th className="py-2 pr-2 font-semibold text-right">Quantity</th>
                  <th className="py-2 pr-2 font-semibold">Read from</th>
                  <th className="py-2 font-semibold" />
                </tr>
              </thead>
              <tbody>
                {e.lines.map((l, i) => editing === l.id ? (
                  <tr key={l.id}><td colSpan={6} className="py-2">{form}</td></tr>
                ) : (
                  <tr key={l.id} className="border-b border-line last:border-0 align-top">
                    <td className="py-2 pr-2 text-ink-300"><span className={cn(unsure(l, "item_ref") && CHECK)}>{l.item_ref ?? i + 1}</span></td>
                    <td className="py-2 pr-2">
                      <span className={cn("text-ink-900", unsure(l, "description") && CHECK)}>{l.description}</span>
                      {l.dimensions && !l.description.toLowerCase().includes(l.dimensions.toLowerCase()) && (
                        <span className="text-ink-500"> · <span className={cn(unsure(l, "dimensions") && CHECK)}>{l.dimensions}</span></span>
                      )}
                      {!l.dimensions && unsure(l, "dimensions") && <span className={cn("ml-1 text-[12px] text-amber-500", CHECK)}>size not read</span>}
                      {l.notes && <div className="text-[12px] text-ink-500">{l.notes}</div>}
                      {l.from_ref && <div className="text-[11.5px] text-ink-300">from {l.from_ref}</div>}
                    </td>
                    <td className="py-2 pr-2 text-ink-700">
                      <span className={cn(unsure(l, "material", "standard") && CHECK)}>
                        {[l.material, l.standard].filter(Boolean).join(" · ") || (unsure(l, "material", "standard") ? "not read" : <span className="text-ink-300">–</span>)}
                      </span>
                    </td>
                    <td className={cn("py-2 pr-2 text-right font-mono whitespace-nowrap", l.qty === null && "text-amber-500")}>
                      <span className={cn(unsure(l, "qty", "unit") && CHECK)}>
                        {l.qty === null ? (unsure(l, "qty") ? "not read" : "not given") : qtyText(l.qty, l.unit)}
                      </span>
                    </td>
                    <td className="py-2 pr-2 text-[12px] text-ink-500">
                      {sourceText(l)}
                      {l.corrected_at && <> · <Tag variant="teal">Corrected</Tag></>}
                      {!l.corrected_at && ((l.confidence !== null && Number(l.confidence) < 0.7) || (l.unsure ?? []).length > 0) && (
                        <> · <Tag variant="amber" title="Check the highlighted details, then correct the line or leave it">Check</Tag></>
                      )}
                    </td>
                    <td className="py-2 text-right whitespace-nowrap">
                      {may && editing === null && !l.from_ref && (
                        <>
                          <button type="button" className="text-[12px] text-teal-600 hover:underline mr-2" onClick={() => start(l.id, toDraft(l))}>Correct</button>
                          <button type="button" className="text-[12px] text-red-500 hover:underline" disabled={remove.isPending}
                            onClick={() => { if (confirm(`Remove "${l.description}"?`)) remove.mutate(l.id) }}>Remove</button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {editing === "new" && form}
        {err && editing === null && <p className="text-red-500 text-[12.5px]">{err}</p>}
      </div>
    </Card>
  )
}
