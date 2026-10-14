import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Field, Input } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDate, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { useMachines, useMaterials } from "./hooks"
import { rupees } from "./labels"
import type { MachineItem, MaterialItem, RateRow, RateSaved } from "./types"

/** Units a material rate can be typed in (K12 A); it is kept as typed and converted. */
const UNITS = ["kg", "g", "tonne", "quintal", "m", "mm", "ft", "m2", "sq ft", "m3", "litre", "nos"]
const UNIT_WORDS: Record<string, string> = { m2: "m²", m3: "m³" }

type Kind = "material" | "machine"
type Item = (MaterialItem & { source?: "forge" | "erpnext"; erpnext_wins?: boolean }) | MachineItem

/** Material rates (₹ per kg, m, nos ...) with the date each applies from (F3.2, C3 A). */
export function MaterialRatesPage() {
  const q = useMaterials()
  return <Rates kind="material" at={q.data?.at} items={q.data?.materials} loading={q.isLoading} error={q.error} />
}

/** Machine-hour rates: Forge's own, and ERPNext's workstations, which win where both exist. */
export function MachineRatesPage() {
  const q = useMachines()
  return <Rates kind="machine" at={q.data?.at} items={q.data?.machines} loading={q.isLoading} error={q.error} />
}

function rateOf(r: { rate?: string; hour_rate?: string } | null | undefined): string | undefined {
  return r?.rate ?? r?.hour_rate
}

function Rates({ kind, at, items, loading, error }: {
  kind: Kind; at?: string; items?: Item[]; loading: boolean; error: unknown
}) {
  const manage = hasPermission(usePermissions(), "costs.manage")
  const [adding, setAdding] = useState<{ code?: string; name?: string; unit?: string } | null>(null)
  const [saved, setSaved] = useState<RateSaved | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const what = kind === "material" ? "material" : "machine"
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-[13px] text-ink-500 max-w-2xl">
          {kind === "material"
            ? "What each material costs, from the date it applies. A new rate never changes an old one, so a quote's cost always says which rate it used."
            : "What an hour on each machine costs, from the date it applies. Where ERPNext has the workstation, its hour rate is used."}
        </p>
        {manage && !adding && <Button size="sm" onClick={() => { setSaved(null); setAdding({}) }}>+ New {what} rate</Button>}
      </div>
      {adding && at && (
        <AddForm kind={kind} today={at} start={adding} onDone={(d) => { setAdding(null); setSaved(d) }}
          onCancel={() => setAdding(null)} />
      )}
      {saved && <SavedNote d={saved} />}
      {loading && <Loading />}
      <ErrorText error={error} fallback="Could not load the rates" />
      {items && items.length === 0 && <Empty>No {what} rates yet.{manage ? ` Add one with "New ${what} rate".` : ""}</Empty>}
      {items && items.length > 0 && (
        <Card className="overflow-x-auto">
          <table className="w-full text-[13px] min-w-[620px]">
            <thead>
              <tr className="text-left text-ink-500 border-b border-line">
                <th className="py-2 px-3 font-semibold">{kind === "material" ? "Material" : "Machine"}</th>
                <th className="py-2 pr-2 font-semibold text-right">Rate now</th>
                <th className="py-2 pr-2 font-semibold">Since</th>
                <th className="py-2 pr-2 font-semibold">Next change</th>
                {kind === "material" && <th className="py-2 pr-2 font-semibold">ERPNext, for reference</th>}
                <th className="py-2 pr-3" />
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const key = `${it.source ?? "forge"}:${it.code}`
                const unit = kind === "material" ? (it.current as RateRow | null)?.unit ?? it.history[0]?.unit : "hour"
                const erp = it.source === "erpnext"
                return (
                  <RateRows key={key} it={it} kind={kind} unit={unit ?? ""} erp={erp} open={open === key}
                    onToggle={() => setOpen(open === key ? null : key)}
                    onAdd={manage && !erp ? () => { setSaved(null); setAdding({ code: it.code, name: it.name, unit: unit ?? undefined }) } : undefined} />
                )
              })}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  )
}

function RateRows({ it, kind, unit, erp, open, onToggle, onAdd }: {
  it: Item; kind: Kind; unit: string; erp: boolean; open: boolean; onToggle: () => void; onAdd?: () => void
}) {
  const cur = it.current
  const row = cur && "effective_from" in cur ? (cur as RateRow) : null
  const ref = kind === "material" ? (it as MaterialItem).erpnext : null
  return (
    <>
      <tr className="border-b border-line last:border-0 align-top">
        <td className="py-2 px-3">
          <span className="font-mono text-[12px] font-semibold text-ink-900">{it.code}</span>
          <span className="block text-ink-700">{it.name}</span>
          {erp && <Tag variant="teal">From ERPNext</Tag>}
          {!erp && it.erpnext_wins && <span className="block text-[11.5px] text-amber-600">ERPNext has this workstation; its rate is used</span>}
        </td>
        <td className="py-2 pr-2 text-right font-mono whitespace-nowrap">
          {cur ? <>{rupees(rateOf(cur))}<span className="text-ink-300 text-[11px]"> /{UNIT_WORDS[unit] ?? unit}</span></> : <span className="text-ink-300">not yet</span>}
          {row?.source_unit && <span className="block text-[11px] text-ink-500">typed as {rupees(row.source_rate)} per {row.source_unit}</span>}
          {(row?.yield_pct || row?.scrap_rate) && (
            <span className="block text-[11px] text-ink-500">
              {row.yield_pct ? `${Number(row.yield_pct)}% yield` : ""}{row.yield_pct && row.scrap_rate ? " · " : ""}{row.scrap_rate ? `scrap ${rupees(row.scrap_rate)}` : ""}
            </span>
          )}
        </td>
        <td className="py-2 pr-2 whitespace-nowrap text-ink-700">
          {cur && "effective_from" in cur ? formatDate(cur.effective_from) : cur && "synced_at" in cur ? <span className="text-ink-500">synced {formatDateTime(cur.synced_at)}</span> : "–"}
        </td>
        <td className="py-2 pr-2 whitespace-nowrap text-ink-700">
          {it.next ? <>{rupees(rateOf(it.next))} from {formatDate(it.next.effective_from)}</> : "–"}
        </td>
        {kind === "material" && (
          <td className="py-2 pr-2 text-[12px] text-ink-500 whitespace-nowrap">
            {ref ? <>valuation {rupees(ref.valuation_rate)} · last bought {rupees(ref.last_purchase_rate)}{ref.uom ? ` /${ref.uom}` : ""}</> : "–"}
          </td>
        )}
        <td className="py-2 pr-3 text-right whitespace-nowrap">
          {it.history.length > 0 && (
            <button type="button" className="text-[12px] text-teal-600 hover:underline mr-2" onClick={onToggle}>
              {open ? "Hide" : `History (${it.history.length})`}
            </button>
          )}
          {onAdd && <button type="button" className="text-[12px] text-teal-600 hover:underline" onClick={onAdd}>New rate</button>}
        </td>
      </tr>
      {open && (
        <tr className="border-b border-line bg-surface-0">
          <td colSpan={kind === "material" ? 6 : 5} className="px-3 py-2">
            <ul className="space-y-1 text-[12.5px]">
              {it.history.map((h) => (
                <li key={h.id} className="flex flex-wrap gap-x-3">
                  <span className="font-mono w-28 text-right">{rupees(rateOf(h))}</span>
                  <span className="text-ink-700">from {formatDate(h.effective_from)}</span>
                  {h.source_unit && <span className="text-ink-500">typed as {rupees(h.source_rate)} per {h.source_unit}</span>}
                  {h.yield_pct && <span className="text-ink-500">{Number(h.yield_pct)}% yield</span>}
                  {h.scrap_rate && <span className="text-ink-500">scrap {rupees(h.scrap_rate)}</span>}
                  <span className="text-ink-300">added {formatDateTime(h.created_at)}</span>
                  {h.note && <span className="text-ink-500">{h.note}</span>}
                </li>
              ))}
            </ul>
          </td>
        </tr>
      )}
    </>
  )
}

function AddForm({ kind, today, start, onDone, onCancel }: {
  kind: Kind; today: string; start: { code?: string; name?: string; unit?: string }
  onDone: (d: RateSaved) => void; onCancel: () => void
}) {
  const qc = useQueryClient()
  const [f, setF] = useState({ code: start.code ?? "", name: start.name ?? "", unit: start.unit ?? "kg", rate: "", from: today, note: "",
    yield_pct: "", scrap_rate: "" })
  const known = !!start.code
  const save = useMutation({
    mutationFn: async () => {
      const body = kind === "material"
        ? { code: f.code, name: f.name, unit: f.unit, rate: f.rate.trim(), effective_from: f.from, note: f.note || null,
            yield_pct: f.yield_pct.trim() || null, scrap_rate: f.scrap_rate.trim() || null }
        : { code: f.code, name: f.name, hour_rate: f.rate.trim(), effective_from: f.from, note: f.note || null }
      return (await api.post<RateSaved>(`/costs/${kind}s`, body)).data
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["costs"] })
      qc.invalidateQueries({ queryKey: ["quote"] })
      qc.invalidateQueries({ queryKey: ["quotes"] })
      onDone(d)
    },
  })
  const problems = errorProblems(save.error)
  return (
    <Card className="p-4 space-y-3">
      <p className="font-semibold text-ink-900 text-[13.5px]">{known ? `New rate for ${start.code}` : `New ${kind} rate`}</p>
      <div className="grid gap-2 sm:grid-cols-[140px_1fr_90px_130px_150px]">
        <Field label="Code"><Input value={f.code} maxLength={40} disabled={known} onChange={(e) => setF({ ...f, code: e.target.value })}
          placeholder={kind === "material" ? "F316" : "CNC Lathe"} /></Field>
        <Field label="Name"><Input value={f.name} maxLength={120} onChange={(e) => setF({ ...f, name: e.target.value })}
          placeholder={kind === "material" ? "Stainless forging F316" : "CNC lathe"} /></Field>
        {kind === "material"
          ? <Field label="Per"><Input value={f.unit} list="rate-units" maxLength={20} onChange={(e) => setF({ ...f, unit: e.target.value })} />
              <datalist id="rate-units">{UNITS.map((u) => <option key={u} value={u} />)}</datalist></Field>
          : <Field label="Per"><Input value="hour" disabled /></Field>}
        <Field label="Rate (₹)"><Input value={f.rate} inputMode="decimal" onChange={(e) => setF({ ...f, rate: e.target.value })} /></Field>
        <Field label="Applies from"><Input type="date" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></Field>
      </div>
      {kind === "material" && (
        <div className="grid gap-2 grid-cols-2 sm:grid-cols-[140px_180px]">
          <Field label="Yield % (optional)" hint="Of what is bought, ends in the part">
            <Input value={f.yield_pct} inputMode="decimal" placeholder="85" onChange={(e) => setF({ ...f, yield_pct: e.target.value })} />
          </Field>
          <Field label={`Scrap rate (₹ per ${f.unit || "unit"})`} hint="What the rest sells for">
            <Input value={f.scrap_rate} inputMode="decimal" placeholder="28" onChange={(e) => setF({ ...f, scrap_rate: e.target.value })} />
          </Field>
        </div>
      )}
      <Field label="Note (optional)" hint="Why the rate changed, e.g. the supplier's price list of 1 Oct">
        <Input value={f.note} maxLength={300} onChange={(e) => setF({ ...f, note: e.target.value })} />
      </Field>
      {problems.length > 0 && (
        <ul role="alert" className="list-disc pl-5 text-[12.5px] text-red-500">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
      )}
      <ErrorText error={problems.length ? null : save.error} fallback="Could not save the rate" />
      <div className="flex gap-2">
        <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>Save rate</Button>
        <Button size="sm" variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
      <p className="text-[11.5px] text-ink-300">A saved rate can't be changed. To correct it, add another rate for the same date; the later one is used.</p>
    </Card>
  )
}

/** What saving a rate did (K9 A): drafts worked out again, sent quotes flagged. */
function SavedNote({ d }: { d: RateSaved }) {
  const links = (qs: RateSaved["recosted"]) => qs.map((a, i) => (
    <span key={a.id}>{i > 0 && ", "}<Link to={`/quotes/${a.id}`} className="text-teal-600 hover:underline font-mono">{a.number} R{a.revision}</Link></span>
  ))
  return (
    <div role="status" className="rounded-md border border-teal-500 bg-teal-100 px-3 py-2 text-[13px] text-ink-900 space-y-0.5">
      <p>Saved the new rate for {d.rate.code}{d.applies_now ? "." : `, from ${formatDate(d.rate.effective_from)}.`}</p>
      {d.applies_now && d.recosted.length > 0 && <p>Draft quotes worked out again: {links(d.recosted)}</p>}
      {d.applies_now && d.flagged.length > 0 && <p>Sent quotes marked "reprice recommended" (their owners get it in the daily email): {links(d.flagged)}</p>}
      {d.applies_now && d.recosted.length === 0 && d.flagged.length === 0 && <p>No open quote used the old rate.</p>}
      {!d.applies_now && (d.affected_quotes.length > 0
        ? <p>On that date these open quotes are worked out again or marked for repricing: {links(d.affected_quotes)}</p>
        : <p>No open quote uses it yet.</p>)}
    </div>
  )
}
