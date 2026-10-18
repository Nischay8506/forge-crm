import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input } from "@/components/ui/Field"
import { Nameplate } from "@/components/ui/Nameplate"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { formatDate, formatDateTime } from "@/lib/utils"
import { CostView } from "./CostView"
import { PriceView } from "./PriceView"
import { useQuote } from "./hooks"
import { COST_STATUS, PRICE_STATUS, SOURCE_LABELS, STATUS_TONE, approvalText, askText, percent, placeText, reviewText, revisionText, rupees } from "./labels"
import type { QuoteDetail, QuoteLineView } from "./types"

/** One quote (Design frame 04, the Quote Builder): its active revision's lines with
 *  what each costs and why (F3.2), what each is priced at and why (F3.3), the rep's own
 *  ask, and the quote's total against the floor. The PDF follows in F3.5. */
export function QuoteDetailPage() {
  const { id } = useParams()
  const { data, isLoading, error } = useQuote(id)
  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load the quote" />
  return <Detail q={data} />
}

type Draft = { description: string; qty: string; unit: string; item_code: string }

function Detail({ q }: { q: QuoteDetail }) {
  const qc = useQueryClient()
  const rev = q.active
  const [editing, setEditing] = useState<string | "new" | null>(null)
  const [draft, setDraft] = useState<Draft>({ description: "", qty: "", unit: "", item_code: "" })
  const [open, setOpen] = useState<string | null>(null)
  const [why, setWhy] = useState<string | null>(null)
  const [asking, setAsking] = useState<string | null>(null)
  const [terms, setTerms] = useState(false)
  const call = useMutation({
    mutationFn: async ({ method, path, body }: { method: "post" | "patch" | "delete"; path: string; body?: unknown }) =>
      (await api.request<QuoteDetail>({ method, url: `/quotes/${q.id}${path}`, data: body })).data,
    onSuccess: (data) => {
      qc.setQueryData(["quote", q.id], data)
      qc.invalidateQueries({ queryKey: ["quotes"] })
      setEditing(null)
      setTerms(false)
      setAsking(null)
    },
  })
  const ask = useMutation({
    mutationFn: async ({ lineId, body }: { lineId: string; body: unknown }) =>
      (await api.put<QuoteDetail>(`/quotes/${q.id}/lines/${lineId}/ask`, body)).data,
    onSuccess: (data) => {
      qc.setQueryData(["quote", q.id], data)
      setAsking(null)
    },
  })
  const start = (l: QuoteLineView | null) => {
    call.reset()
    setEditing(l ? l.id : "new")
    setDraft(l ? { description: l.description, qty: l.qty ?? "", unit: l.unit ?? "", item_code: l.item_code ?? "" }
      : { description: "", qty: "", unit: "", item_code: "" })
  }
  const save = () => {
    const body: Record<string, unknown> = { qty: draft.qty.trim() === "" ? null : draft.qty.trim(), unit: draft.unit }
    const line = rev.lines.find((l) => l.id === editing)
    if (!line?.configuration_id) {
      body.description = draft.description
      body.item_code = draft.item_code
    }
    call.mutate(editing === "new" ? { method: "post", path: "/lines", body } : { method: "patch", path: `/lines/${editing}`, body })
  }
  const costs = q.may.see_costs
  const form = (
    <div className="space-y-2 rounded-md border border-teal-500 bg-surface-0 p-3">
      <div className="grid gap-2 grid-cols-2 sm:grid-cols-[1fr_150px_110px_90px]">
        <Input className="col-span-2 sm:col-span-1" value={draft.description} aria-label="Item" placeholder="Item"
          disabled={!!rev.lines.find((l) => l.id === editing)?.configuration_id}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
        <Input className="col-span-2 sm:col-span-1 font-mono" value={draft.item_code} aria-label="ERPNext item code" placeholder="ERPNext item code"
          title="Its default BOM in ERPNext costs the line when the family has no full cost model"
          disabled={!!rev.lines.find((l) => l.id === editing)?.configuration_id}
          onChange={(e) => setDraft({ ...draft, item_code: e.target.value })} />
        <Input value={draft.qty} inputMode="decimal" aria-label="Quantity" placeholder="Qty" onChange={(e) => setDraft({ ...draft, qty: e.target.value })} />
        <Input value={draft.unit} aria-label="Unit" placeholder="Unit" onChange={(e) => setDraft({ ...draft, unit: e.target.value })} />
      </div>
      <div className="flex gap-2">
        <Button size="sm" onClick={save} disabled={call.isPending}>Save line</Button>
        <Button size="sm" variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
      </div>
    </div>
  )
  return (
    <div className="space-y-4">
      <div className="text-[12.5px]"><Link to="/quotes" className="text-teal-600 hover:underline">← Quotes</Link></div>
      <div className="flex flex-wrap items-center gap-2">
        <Nameplate id={q.number} />
        <span className="text-[12.5px] font-semibold text-ink-700">{revisionText(rev.number, rev.reason)}</span>
        <Tag variant={STATUS_TONE[q.status]}>{q.status_label}</Tag>
        {q.enquiry && <Link to={`/enquiries/${q.enquiry.id}`} className="text-[12.5px] text-teal-600 hover:underline">from {q.enquiry.ref}</Link>}
      </div>
      <div>
        <h1 className="text-xl font-bold text-ink-900">{q.customer ?? "Customer not named"}</h1>
        <p className="text-[13px] text-ink-500">
          Valid until {formatDate(rev.valid_until)}
          {rev.destination && <> · to {placeText(rev.destination)}</>}
          {q.owner_name && <> · {q.owner_name}</>}
        </p>
      </div>
      {call.error && <p role="alert" className="text-red-500 text-[13px]">{errorMessage(call.error, "That did not work")}</p>}
      {rev.reprice_reason && (
        <div role="status" className="rounded-md border border-amber-500 bg-[#fdf1de] px-3 py-2 text-[13px] text-[#8a5300]">
          <p className="font-semibold">Cost changed since this revision was sent: reprice recommended</p>
          <ul className="list-disc pl-5">{rev.reprice_reason.split("; ").map((r) => <li key={r}>{r}</li>)}</ul>
          {rev.reprice_at && <p className="text-[12px]">Noticed {formatDateTime(rev.reprice_at)}. A new revision is made in F3.7; until then, look at the costs below.</p>}
        </div>
      )}

      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle>Lines</CardTitle>
          <span className="flex gap-1">
            {q.may.work && costs && editing === null && (
              <Button size="sm" variant="ghost" disabled={call.isPending} onClick={() => call.mutate({ method: "post", path: "/cost" })}>Work out costs</Button>
            )}
            {q.may.work && editing === null && (
              <Button size="sm" variant="ghost" disabled={call.isPending} onClick={() => call.mutate({ method: "post", path: "/price" })}>Price again</Button>
            )}
            {q.may.work && editing === null && <Button size="sm" variant="ghost" onClick={() => start(null)}>+ Add a line</Button>}
          </span>
        </CardHeader>
        <div className="px-4 pb-4 space-y-2">
          {rev.lines.length === 0 && editing !== "new" && <Empty>No lines. Add what is being quoted.</Empty>}
          {rev.lines.map((l) => editing === l.id ? <div key={l.id}>{form}</div> : (
            <div key={l.id} className="border-b border-line last:border-0 py-2 text-[13px]">
              <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
                <span className="w-6 text-ink-300">{l.position}</span>
                <div className="flex-1 min-w-[200px]">
                  <p className="text-ink-900">{l.description}</p>
                  {l.variant_code && <p className="font-mono text-[12px] font-semibold text-ink-700">{l.variant_code}</p>}
                  {!l.variant_code && l.item_code && <p className="font-mono text-[12px] text-ink-500">{l.item_code}</p>}
                  {l.cost_flag && <p className="text-[12px] text-amber-600">{l.cost_flag}</p>}
                  {l.price_status && l.price_status !== "ok" && (
                    <p className="text-[12px] mt-0.5">
                      <Tag variant={PRICE_STATUS[l.price_status].tone}>{PRICE_STATUS[l.price_status].label}</Tag>
                      {l.price_review && (
                        <> <Link to={`/decisions?card=${l.price_review.id}`} className="text-teal-600 hover:underline">{approvalText(l.price_review.status)}</Link></>
                      )}
                    </p>
                  )}
                  {l.ask && <p className="text-[12px] text-ink-500">The rep {askText(l.ask)}</p>}
                  {costs && l.cost_status && l.cost_status !== "complete" && (
                    <p className="text-[12px] mt-0.5">
                      <Tag variant={COST_STATUS[l.cost_status].tone}>{COST_STATUS[l.cost_status].label}</Tag>
                      {l.cost_review && (
                        <> <Link to={`/decisions?card=${l.cost_review.id}`} className="text-teal-600 hover:underline">{reviewText(l.cost_review.status)}</Link></>
                      )}
                    </p>
                  )}
                </div>
                <span className="w-24 text-right font-mono whitespace-nowrap">{l.qty === null ? <span className="text-amber-500">no qty</span> : `${Number(l.qty)} ${l.unit ?? ""}`}</span>
                {costs && (
                  <span className="w-36 text-right">
                    <span className="block font-mono">{rupees(l.unit_cost)}{l.unit_cost ? <span className="text-ink-300 text-[11px]"> /{l.unit ?? "unit"}</span> : null}</span>
                    {l.cost_source
                      ? <button type="button" className="text-[11.5px] text-teal-600 hover:underline" onClick={() => setOpen(open === l.id ? null : l.id)}>
                          {SOURCE_LABELS[l.cost_source]} · {open === l.id ? "hide" : "why this cost"}
                        </button>
                      : <span className="block text-[11.5px] text-ink-300">cost not worked out yet</span>}
                  </span>
                )}
                <span className="w-36 text-right">
                  <span className="block font-mono font-semibold">{rupees(l.unit_price)}{l.unit_price ? <span className="text-ink-300 text-[11px] font-normal"> /{l.unit ?? "unit"}</span> : null}</span>
                  {l.price
                    ? <button type="button" className="text-[11.5px] text-teal-600 hover:underline" onClick={() => setWhy(why === l.id ? null : l.id)}>
                        {l.margin_pct ? `${percent(l.margin_pct)} margin` : "no margin yet"} · {why === l.id ? "hide" : "why this price"}
                      </button>
                    : <span className="block text-[11.5px] text-ink-300">not priced yet</span>}
                </span>
                {q.may.work && editing === null && (
                  <span className="whitespace-nowrap">
                    <button type="button" className="text-[12px] text-teal-600 hover:underline mr-2" onClick={() => { ask.reset(); setAsking(asking === l.id ? null : l.id) }}>Ask</button>
                    <button type="button" className="text-[12px] text-teal-600 hover:underline mr-2" onClick={() => start(l)}>Change</button>
                    <button type="button" className="text-[12px] text-red-500 hover:underline"
                      onClick={() => { if (confirm(`Remove "${l.description}"?`)) call.mutate({ method: "delete", path: `/lines/${l.id}` }) }}>Remove</button>
                  </span>
                )}
              </div>
              {open === l.id && l.cost && <div className="mt-2 sm:ml-9"><CostView c={l.cost} unit={l.unit} /></div>}
              {why === l.id && l.price && <div className="mt-2 sm:ml-9"><PriceView p={l.price} unit={l.unit} /></div>}
              {asking === l.id && (
                <div className="mt-2 sm:ml-9">
                  <AskForm line={l} busy={ask.isPending} error={ask.error ? errorMessage(ask.error, "That did not work") : null}
                    onSave={(body) => ask.mutate({ lineId: l.id, body })} onCancel={() => setAsking(null)} />
                </div>
              )}
            </div>
          ))}
          {editing === "new" && form}
          <Totals q={q} />
          {costs && rev.costed_at && <p className="text-[11.5px] text-ink-300">Costs worked out {formatDateTime(rev.costed_at)}</p>}
          {rev.priced_at && <p className="text-[11.5px] text-ink-300">Priced {formatDateTime(rev.priced_at)}</p>}
        </div>
      </Card>

      <Card className="p-4 space-y-2 text-[13px]">
        <div className="flex items-center justify-between">
          <p className="font-semibold text-ink-900">Validity and delivery</p>
          {q.may.work && !terms && <Button size="sm" variant="ghost" onClick={() => setTerms(true)}>Change</Button>}
        </div>
        {terms ? <TermsForm q={q} busy={call.isPending} onSave={(body) => call.mutate({ method: "patch", path: "", body })} onCancel={() => setTerms(false)} />
          : (
            <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[140px_1fr]">
              <dt className="text-ink-500">Valid until</dt><dd>{formatDate(rev.valid_until)}</dd>
              <dt className="text-ink-500">Deliver to</dt><dd>{placeText(rev.destination) || "not given"}</dd>
            </dl>
          )}
      </Card>

      {q.revisions.length > 1 && (
        <Card className="p-4 text-[13px] space-y-1">
          <p className="font-semibold text-ink-900">Revisions</p>
          {q.revisions.map((r) => (
            <p key={r.number} className="text-ink-700">{revisionText(r.number, r.reason)} · {r.status} · {formatDateTime(r.created_at)}</p>
          ))}
        </Card>
      )}
    </div>
  )
}

function TermsForm({ q, busy, onSave, onCancel }: {
  q: QuoteDetail; busy: boolean; onSave: (body: unknown) => void; onCancel: () => void
}) {
  const rev = q.active
  const [valid, setValid] = useState(rev.valid_until)
  const [dest, setDest] = useState({ place: rev.destination?.place ?? "", city: rev.destination?.city ?? "", state: rev.destination?.state ?? "" })
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-4">
        <Field label="Valid until"><Input type="date" value={valid} onChange={(e) => setValid(e.target.value)} /></Field>
        <Field label="Place"><Input value={dest.place} maxLength={100} onChange={(e) => setDest({ ...dest, place: e.target.value })} /></Field>
        <Field label="City"><Input value={dest.city} maxLength={100} onChange={(e) => setDest({ ...dest, city: e.target.value })} /></Field>
        <Field label="State"><Input value={dest.state} maxLength={100} onChange={(e) => setDest({ ...dest, state: e.target.value })} /></Field>
      </div>
      <div className="flex gap-2">
        <Button size="sm" disabled={busy} onClick={() => onSave({ valid_until: valid, destination: dest })}>Save</Button>
        <Button size="sm" variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

/** The quote as a whole against the floor its lines weigh out to (C2 A). */
function Totals({ q }: { q: QuoteDetail }) {
  const t = q.active.totals
  const below = t.status === "below_floor"
  return (
    <div className="mt-2 border-t border-line pt-2 text-[13px]">
      <div className="flex flex-wrap items-baseline justify-end gap-x-4">
        {t.margin_pct && (
          <span className={below ? "text-red-500 font-semibold" : "text-ink-500"}>
            {percent(t.margin_pct)} margin against a {percent(t.floor_margin_pct)} floor
          </span>
        )}
        <span className="font-semibold text-ink-900">Total {rupees(t.total)}</span>
      </div>
      {t.lines_left_out > 0 && (
        <p className="text-right text-[12px] text-amber-600">
          {t.lines_left_out} {t.lines_left_out === 1 ? "line is" : "lines are"} left out of the total:
          no quantity, no price, or no cost to work the margin from.
        </p>
      )}
      {below && <p className="text-right text-[12px] text-red-500">The quote is below its floor, so it needs an approval before it goes out.</p>}
    </div>
  )
}

/** What the rep asks for on a line: a discount off Forge's price, or a price of their
 *  own (C2 A). Either one runs the floor check again. */
function AskForm({ line, busy, error, onSave, onCancel }: {
  line: QuoteLineView; busy: boolean; error: string | null
  onSave: (body: unknown) => void; onCancel: () => void
}) {
  const [discount, setDiscount] = useState(line.ask?.discount_pct ?? "")
  const [price, setPrice] = useState(line.ask?.unit_price ?? "")
  return (
    <div className="rounded-md border border-teal-500 bg-surface-0 p-3 space-y-2 text-[13px]">
      <p className="text-ink-500">Ask for a discount off {rupees(line.price?.list_price)}, or name the price. One or the other.</p>
      <div className="grid gap-2 sm:grid-cols-[150px_150px_1fr]">
        <Field label="Discount %"><Input value={discount} inputMode="decimal" onChange={(e) => { setDiscount(e.target.value); setPrice("") }} /></Field>
        <Field label="Or price per unit"><Input value={price} inputMode="decimal" onChange={(e) => { setPrice(e.target.value); setDiscount("") }} /></Field>
      </div>
      {error && <p role="alert" className="text-red-500">{error}</p>}
      <div className="flex gap-2">
        <Button size="sm" disabled={busy}
          onClick={() => onSave(price.trim() ? { unit_price: price.trim() } : discount.trim() ? { discount_pct: discount.trim() } : {})}>
          Save the ask
        </Button>
        {(line.ask) && <Button size="sm" variant="secondary" disabled={busy} onClick={() => onSave({})}>Clear it</Button>}
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}
