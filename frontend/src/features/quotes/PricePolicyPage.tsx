import { useState } from "react"
import { Link, useParams } from "react-router-dom"
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
import { usePricePolicies, usePricePolicy } from "./hooks"
import { percent } from "./labels"
import type { PricePolicyData, PricePolicyDetail } from "./types"

const TIERS = ["standard", "key", "strategic"] as const
const ROUNDINGS = [
  { value: "0.01", label: "no rounding (the paisa)" },
  { value: "1", label: "up to the next ₹1" },
  { value: "5", label: "up to the next ₹5" },
  { value: "10", label: "up to the next ₹10" },
]

/** What every family is priced by (F3.3, C2 A): the target margin a cost is priced at,
 *  the floor every line is checked against, the tier discounts and the quantity breaks.
 *  The company's policy is used by families that have none of their own. */
export function PricePoliciesPage() {
  const q = usePricePolicies()
  if (q.isLoading) return <Loading />
  if (q.error || !q.data) return <ErrorText error={q.error} fallback="Could not load the pricing policies" />
  const { company, families } = q.data
  return (
    <div className="space-y-3">
      <p className="text-[13px] text-ink-500 max-w-2xl">
        What a line is priced at when no price in the ERP covers it: the family's target margin on its
        cost, and the floor every line and every quote is checked against, whichever way the price was
        found. A policy is never edited; a change is a new version from a date, so an old quote still
        shows the numbers it was made under.
      </p>
      <Card className="p-4 space-y-2 text-[13px]">
        <div className="flex items-center justify-between">
          <p className="font-semibold text-ink-900">The company's policy</p>
          <Link to="/quotes/price-policies/company" className="text-teal-600 hover:underline text-[12.5px]">
            {company ? "Change" : "Set it"}
          </Link>
        </div>
        {company
          ? <p className="text-ink-700">Target {percent(company.target_margin_pct)}, floor {percent(company.floor_margin_pct)}, from {formatDate(company.from)}</p>
          : <Empty>No policy yet. Until one is set, a line Forge has to price from its cost cannot be priced at all.</Empty>}
      </Card>
      <Card className="p-4 space-y-1 text-[13px]">
        <p className="font-semibold text-ink-900">By product family</p>
        {families.length === 0 && <Empty>No product families yet.</Empty>}
        {families.map((f) => (
          <div key={f.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-line last:border-0 py-1.5">
            <span className="text-ink-900">{f.name}</span>
            <span className="flex items-center gap-2">
              {f.policy
                ? <span className="text-ink-700">Target {percent(f.policy.target_margin_pct)}, floor {percent(f.policy.floor_margin_pct)}</span>
                : <span className="text-amber-600">no policy</span>}
              {f.own ? <Tag variant="teal">its own</Tag> : f.policy ? <Tag variant="neutral">the company's</Tag> : null}
              <Link to={`/quotes/price-policies/${f.id}`} className="text-teal-600 hover:underline text-[12.5px]">{f.own ? "Change" : "Give it its own"}</Link>
            </span>
          </div>
        ))}
      </Card>
    </div>
  )
}

type Form = PricePolicyData & { effective_from: string; note: string }

function formOf(d: PricePolicyDetail, today: string): Form {
  const latest = d.versions[0]?.policy
  const from = latest ?? d.sample
  return {
    target_margin_pct: latest ? from.target_margin_pct : "",
    floor_margin_pct: latest ? from.floor_margin_pct : "",
    tier_discounts: { ...(latest ? from.tier_discounts : {}) },
    qty_breaks: latest ? [...from.qty_breaks] : [],
    rounding: latest ? from.rounding : "0.01",
    effective_from: today, note: "",
  }
}

/** One policy: the company's, or one family's. Saving makes a new dated version and
 *  prices every open draft again. */
export function PricePolicyPage() {
  const { target } = useParams()
  const q = usePricePolicy(target)
  if (q.isLoading) return <Loading />
  if (q.error || !q.data) return <ErrorText error={q.error} fallback="Could not load the policy" />
  return <Editor target={target!} d={q.data} />
}

function Editor({ target, d }: { target: string; d: PricePolicyDetail }) {
  const qc = useQueryClient()
  const today = new Date().toISOString().slice(0, 10)
  const mayManage = hasPermission(usePermissions(), "prices.manage")
  const [form, setForm] = useState<Form>(() => formOf(d, today))
  const [saved, setSaved] = useState<string[] | null>(null)
  const save = useMutation({
    mutationFn: async () => (await api.put<{ repriced: string[] }>(`/costs/price-policies/${target}`, {
      ...form,
      tier_discounts: Object.fromEntries(Object.entries(form.tier_discounts).filter(([, v]) => String(v).trim() !== "")),
      qty_breaks: form.qty_breaks.filter((b) => b.from_qty.trim() !== ""),
    })).data,
    onSuccess: (out) => {
      setSaved(out.repriced)
      qc.invalidateQueries({ queryKey: ["costs", "price-policy", target] })
      qc.invalidateQueries({ queryKey: ["costs", "price-policies"] })
      qc.invalidateQueries({ queryKey: ["quote"] })
    },
  })
  const problems = save.error ? errorProblems(save.error) : []
  const name = d.family ? d.family.name : "the company"
  return (
    <div className="space-y-3">
      <div className="text-[12.5px]"><Link to="/quotes/price-policies" className="text-teal-600 hover:underline">← Pricing policy</Link></div>
      <h2 className="text-lg font-bold text-ink-900">Pricing policy for {name}</h2>
      {d.from_company && (
        <p className="text-[13px] text-amber-600">
          {name} follows the company's policy today. Saving here gives it one of its own.
        </p>
      )}
      <Card className="p-4 space-y-3 text-[13px]">
        <div className="grid gap-2 sm:grid-cols-3">
          <Field label="Target margin %" hint="What a cost is priced at when no ERP price covers the line">
            <Input value={form.target_margin_pct} inputMode="decimal" disabled={!mayManage}
              onChange={(e) => setForm({ ...form, target_margin_pct: e.target.value })} />
          </Field>
          <Field label="Floor margin %" hint="Below this a line needs an approval">
            <Input value={form.floor_margin_pct} inputMode="decimal" disabled={!mayManage}
              onChange={(e) => setForm({ ...form, floor_margin_pct: e.target.value })} />
          </Field>
          <Field label="Round off to" hint='Used only on quotes where someone ticks "Round off prices"'>
            <select className="h-9 w-full rounded-md border border-line bg-surface-0 px-2 text-[13px]" value={form.rounding}
              disabled={!mayManage} onChange={(e) => setForm({ ...form, rounding: e.target.value })}>
              {ROUNDINGS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </Field>
        </div>
        <div>
          <p className="font-semibold text-ink-900">Tier discounts</p>
          <p className="text-ink-500 text-[12.5px]">Off the list price, by what kind of customer the account is. Leave a tier empty for none.</p>
          <div className="grid gap-2 sm:grid-cols-3 mt-1">
            {TIERS.map((tier) => (
              <Field key={tier} label={`${tier} %`}>
                <Input value={form.tier_discounts[tier] ?? ""} inputMode="decimal" disabled={!mayManage}
                  onChange={(e) => setForm({ ...form, tier_discounts: { ...form.tier_discounts, [tier]: e.target.value } })} />
              </Field>
            ))}
          </div>
        </div>
        <div>
          <p className="font-semibold text-ink-900">Quantity breaks</p>
          <p className="text-ink-500 text-[12.5px]">The highest band the line's quantity reaches is the one used.</p>
          {form.qty_breaks.map((b, i) => (
            <div key={i} className="grid gap-2 sm:grid-cols-[150px_150px_auto] items-end mt-1">
              <Field label="From quantity">
                <Input value={b.from_qty} inputMode="decimal" disabled={!mayManage}
                  onChange={(e) => setForm({ ...form, qty_breaks: form.qty_breaks.map((x, j) => j === i ? { ...x, from_qty: e.target.value } : x) })} />
              </Field>
              <Field label="Discount %">
                <Input value={b.discount_pct} inputMode="decimal" disabled={!mayManage}
                  onChange={(e) => setForm({ ...form, qty_breaks: form.qty_breaks.map((x, j) => j === i ? { ...x, discount_pct: e.target.value } : x) })} />
              </Field>
              {mayManage && (
                <button type="button" className="text-[12px] text-red-500 hover:underline pb-2"
                  onClick={() => setForm({ ...form, qty_breaks: form.qty_breaks.filter((_, j) => j !== i) })}>Remove</button>
              )}
            </div>
          ))}
          {mayManage && (
            <Button className="mt-2" size="sm" variant="ghost"
              onClick={() => setForm({ ...form, qty_breaks: [...form.qty_breaks, { from_qty: "", discount_pct: "" }] })}>
              + Add a break
            </Button>
          )}
        </div>
        <div className="grid gap-2 sm:grid-cols-[180px_1fr]">
          <Field label="From" hint="The date these numbers apply from">
            <Input type="date" value={form.effective_from} disabled={!mayManage}
              onChange={(e) => setForm({ ...form, effective_from: e.target.value })} />
          </Field>
          <Field label="Note"><Input value={form.note} maxLength={300} disabled={!mayManage}
            onChange={(e) => setForm({ ...form, note: e.target.value })} /></Field>
        </div>
        {problems.length > 0 && (
          <ul role="alert" className="list-disc pl-5 text-red-500">{problems.map((t) => <li key={t}>{t}</li>)}</ul>
        )}
        {saved && (
          <p role="status" className="text-teal-600">
            Saved. {saved.length === 0 ? "No open draft needed pricing again." : `${saved.length} open ${saved.length === 1 ? "quote was" : "quotes were"} priced again: ${saved.join(", ")}`}
          </p>
        )}
        {mayManage && (
          <div className="flex gap-2">
            <Button size="sm" disabled={save.isPending} onClick={() => { setSaved(null); save.mutate() }}>Save this version</Button>
            <Button size="sm" variant="secondary" disabled={save.isPending}
              onClick={() => setForm({ ...form, ...d.sample })}>Start from the sample</Button>
          </div>
        )}
      </Card>
      <Card className="p-4 space-y-1 text-[13px]">
        <p className="font-semibold text-ink-900">Versions</p>
        {d.versions.length === 0 && <Empty>No version yet.</Empty>}
        {d.versions.map((v) => (
          <p key={v.id} className="text-ink-700 border-b border-line last:border-0 py-1">
            From {formatDate(v.effective_from)} · target {percent(v.policy.target_margin_pct)}, floor {percent(v.policy.floor_margin_pct)}
            {v.note ? ` · ${v.note}` : ""}
            <span className="block text-[12px] text-ink-300">{v.created_by} · {formatDateTime(v.created_at)}</span>
          </p>
        ))}
      </Card>
    </div>
  )
}
