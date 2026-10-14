import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDate, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { useMachines, useMaterials } from "./hooks"
import { rupees } from "./labels"
import type { AffectedQuote, MachineItem, MaterialItem, RateRow } from "./types"

const UNITS = ["kg", "m", "nos", "l", "m2"]

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
  const [saved, setSaved] = useState<{ code: string; affected: AffectedQuote[] } | null>(null)
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
        <AddForm kind={kind} today={at} start={adding} onDone={(code, affected) => { setAdding(null); setSaved({ code, affected }) }}
          onCancel={() => setAdding(null)} />
      )}
      {saved && (
        <div role="status" className="rounded-md border border-teal-500 bg-teal-100 px-3 py-2 text-[13px] text-ink-900">
          Saved the new rate for {saved.code}.{" "}
          {saved.affected.length === 0
            ? "No open quote was costed with the old rate."
            : <>These open quotes were costed with the old rate; look at them again: {saved.affected.map((a, i) => (
                <span key={a.id}>{i > 0 && ", "}<Link to={`/quotes/${a.id}`} className="text-teal-600 hover:underline font-mono">{a.number} R{a.revision}</Link></span>
              ))}</>}
        </div>
      )}
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
                <th className="py-2 pr-3" />
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const key = `${it.source ?? "forge"}:${it.code}`
                const unit = kind === "material" ? (it.current as RateRow | null)?.unit ?? it.history[0]?.unit : "hour"
                const erp = it.source === "erpnext"
                return (
                  <RateRows key={key} it={it} unit={unit ?? ""} erp={erp} open={open === key}
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

function RateRows({ it, unit, erp, open, onToggle, onAdd }: {
  it: Item; unit: string; erp: boolean; open: boolean; onToggle: () => void; onAdd?: () => void
}) {
  const cur = it.current
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
          {cur ? <>{rupees(rateOf(cur))}<span className="text-ink-300 text-[11px]"> /{unit}</span></> : <span className="text-ink-300">not yet</span>}
        </td>
        <td className="py-2 pr-2 whitespace-nowrap text-ink-700">
          {cur && "effective_from" in cur ? formatDate(cur.effective_from) : cur && "synced_at" in cur ? <span className="text-ink-500">synced {formatDateTime(cur.synced_at)}</span> : "–"}
        </td>
        <td className="py-2 pr-2 whitespace-nowrap text-ink-700">
          {it.next ? <>{rupees(rateOf(it.next))} from {formatDate(it.next.effective_from)}</> : "–"}
        </td>
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
          <td colSpan={5} className="px-3 py-2">
            <ul className="space-y-1 text-[12.5px]">
              {it.history.map((h) => (
                <li key={h.id} className="flex flex-wrap gap-x-3">
                  <span className="font-mono w-28 text-right">{rupees(rateOf(h))}</span>
                  <span className="text-ink-700">from {formatDate(h.effective_from)}</span>
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
  onDone: (code: string, affected: AffectedQuote[]) => void; onCancel: () => void
}) {
  const qc = useQueryClient()
  const [f, setF] = useState({ code: start.code ?? "", name: start.name ?? "", unit: start.unit ?? "kg", rate: "", from: today, note: "" })
  const known = !!start.code
  const save = useMutation({
    mutationFn: async () => {
      const body = kind === "material"
        ? { code: f.code, name: f.name, unit: f.unit, rate: f.rate.trim(), effective_from: f.from, note: f.note || null }
        : { code: f.code, name: f.name, hour_rate: f.rate.trim(), effective_from: f.from, note: f.note || null }
      return (await api.post<{ rate: RateRow; affected_quotes: AffectedQuote[] }>(`/costs/${kind}s`, body)).data
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["costs"] })
      onDone(d.rate.code, d.affected_quotes)
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
          ? <Field label="Per"><Select value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })}>
              {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
            </Select></Field>
          : <Field label="Per"><Input value="hour" disabled /></Field>}
        <Field label="Rate (₹)"><Input value={f.rate} inputMode="decimal" onChange={(e) => setF({ ...f, rate: e.target.value })} /></Field>
        <Field label="Applies from"><Input type="date" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></Field>
      </div>
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
