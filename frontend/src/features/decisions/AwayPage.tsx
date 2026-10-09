import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { cn, formatDateTime } from "@/lib/utils"
import { useAuthStore } from "@/stores/authStore"
import type { Delegation } from "@/features/agents/types"
import { useCardTypes, useColleagues, useDelegations } from "./hooks"

/** "2026-10-09T18:30" for a datetime-local input, in the browser's time. */
function localInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

function inr(v: string | null): string {
  if (v === null) return ""
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(Number(v))
}

/** H3 with Nischay's choice: the person going away picks the card types and the most the
 * stand-in may approve. The stand-in still decides only cards this person could decide. */
function AwayForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient()
  const colleagues = useColleagues()
  const types = useCardTypes()
  const weekOn = new Date(Date.now() + 7 * 24 * 3600_000)
  weekOn.setHours(18, 30, 0, 0)
  const [to, setTo] = useState("")
  const [starts, setStarts] = useState(localInput(new Date()))
  const [ends, setEnds] = useState(localInput(weekOn))
  const [keys, setKeys] = useState<string[]>([])
  const [max, setMax] = useState("")
  const [note, setNote] = useState("")
  const money = (types.data ?? []).filter((t) => t.has_amount && keys.includes(t.key))
  const save = useMutation({
    mutationFn: async () => (await api.post<Delegation>("/decisions/away", {
      to_user_id: to, starts_at: new Date(starts).toISOString(), ends_at: new Date(ends).toISOString(),
      tool_keys: keys, max_amount: money.length && max ? max : null, note: note || null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["decisions"] }); onDone() },
  })
  const byAgent = new Map<string, typeof types.data>()
  for (const t of types.data ?? []) {
    const k = t.agent ?? "Other"
    byAgent.set(k, [...(byAgent.get(k) ?? []), t])
  }
  const missing = !to ? "Pick a stand-in" : keys.length === 0 ? "Pick at least one card type"
    : money.length && !(Number(max) > 0) ? "Set the most they may approve" : null

  return (
    <Card>
      <CardHeader><CardTitle>Set an out-of-office</CardTitle></CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Field label="Stand-in">
              <Select value={to} onChange={(e) => setTo(e.target.value)}>
                <option value="">Pick a colleague</option>
                {(colleagues.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
            </Field>
            <Field label="From"><Input type="datetime-local" value={starts} onChange={(e) => setStarts(e.target.value)} /></Field>
            <Field label="Until"><Input type="datetime-local" value={ends} onChange={(e) => setEnds(e.target.value)} /></Field>
          </div>
          <Field label="Card types they may decide for you" group
            hint="Only cards you could decide yourself; anything else still goes to its own approvers.">
            {types.isLoading && <Loading />}
            <div className="space-y-2">
              {[...byAgent.entries()].map(([agent, list]) => (
                <div key={agent}>
                  <p className="text-[11.5px] text-ink-300 mb-1">{agent}</p>
                  <div className="flex flex-wrap gap-2">
                    {(list ?? []).map((t) => {
                      const on = keys.includes(t.key)
                      return (
                        <button key={t.key} type="button" aria-pressed={on}
                          onClick={() => setKeys((k) => on ? k.filter((x) => x !== t.key) : [...k, t.key])}
                          className={cn("rounded-md border px-2.5 py-1.5 text-[12.5px]",
                            on ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500 hover:text-ink-900")}>
                          {on ? "✓ " : ""}{t.title}{t.has_amount ? " (has an amount)" : ""}
                        </button>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          </Field>
          {money.length > 0 && (
            <Field label="The most they may approve, in ₹" hint={`For ${money.map((t) => t.title).join(", ")}. Cards above this wait for you or your other approvers.`}>
              <Input className="max-w-[200px]" inputMode="decimal" value={max} onChange={(e) => setMax(e.target.value)} />
            </Field>
          )}
          <Field label="Note for them (optional)"><Input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} /></Field>
          <ErrorText error={save.error} fallback="Could not set the out-of-office" />
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" disabled={!!missing || save.isPending}>{save.isPending ? "Saving…" : "Start out-of-office"}</Button>
            <Button type="button" variant="secondary" onClick={onDone}>Cancel</Button>
            {missing && <span className="text-[12px] text-ink-300">{missing}</span>}
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function state(dl: Delegation): { label: string; tone: "green" | "gold" | "neutral" } {
  if (dl.revoked_at) return { label: "ended", tone: "neutral" }
  if (dl.kind === "away" && dl.ends_at && new Date(dl.ends_at) <= new Date()) return { label: "over", tone: "neutral" }
  if (new Date(dl.starts_at) > new Date()) return { label: "starts later", tone: "gold" }
  return { label: "active", tone: "green" }
}

function DelegationRow({ dl, mine }: { dl: Delegation; mine: boolean }) {
  const qc = useQueryClient()
  const end = useMutation({
    mutationFn: async () => (await api.post<Delegation>(`/decisions/delegations/${dl.id}/end`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["decisions"] }),
  })
  const st = state(dl)
  const live = st.label === "active" || st.label === "starts later"
  return (
    <li className="p-3 flex flex-col sm:flex-row sm:items-start gap-2 text-[13px]">
      <Tag variant={st.tone} className="self-start shrink-0">{st.label}</Tag>
      <div className="flex-1 space-y-0.5">
        {dl.kind === "away" ? (
          <p className="text-ink-900">
            {mine ? <>{dl.to_name} stands in for you</> : <>You stand in for {dl.from_name}</>}
            <span className="text-ink-500"> · {formatDateTime(dl.starts_at)} to {formatDateTime(dl.ends_at)}</span>
          </p>
        ) : (
          <p className="text-ink-900">
            {mine ? <>You handed a card to {dl.to_name}</> : <>{dl.from_name} handed you a card</>}
            {" · "}<Link className="text-teal-600 hover:underline" to={`/decisions?card=${dl.decision_id}`}>Open the card</Link>
          </p>
        )}
        {dl.kind === "away" && (
          <p className="text-[12px] text-ink-500">
            {dl.tools.join(", ")}{dl.max_amount ? ` · up to ${inr(dl.max_amount)}` : ""}
          </p>
        )}
        {dl.note && <p className="text-[12px] text-ink-500">“{dl.note}”</p>}
        {dl.revoked_at && <p className="text-[11.5px] text-ink-300">Ended {formatDateTime(dl.revoked_at)}</p>}
        <ErrorText error={end.error} fallback="Could not end it" />
      </div>
      {mine && live && (
        <Button variant="secondary" size="sm" className="self-start" disabled={end.isPending}
          onClick={() => { if (confirm("End this now? Your stand-in can no longer decide for you.")) end.mutate() }}>End now</Button>
      )}
    </li>
  )
}

export function AwayPage() {
  const me = useAuthStore((s) => s.me)
  const { data, isLoading, error } = useDelegations()
  const [adding, setAdding] = useState(false)
  const given = (data ?? []).filter((d) => d.from_user_id === me?.id)
  const received = (data ?? []).filter((d) => d.to_user_id === me?.id)
  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
        <p className="text-[12.5px] text-ink-500 max-w-3xl">
          A stand-in decides your cards for you, with no more authority than yours, and every decision
          they make is recorded as theirs, made for you. They never decide a card they asked for, and never
          two steps of the same card. To hand over a single card, use “Hand to…” on the card itself.
        </p>
        {!adding && <Button onClick={() => setAdding(true)} className="self-start">Set an out-of-office</Button>}
      </div>
      {adding && <AwayForm onDone={() => setAdding(false)} />}
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load hand-overs" />
      {data && (
        <>
          <Card>
            <CardHeader><CardTitle>You handed on</CardTitle></CardHeader>
            {given.length === 0 ? <Empty>Nothing handed on.</Empty> : (
              <ul className="divide-y divide-line">{given.map((dl) => <DelegationRow key={dl.id} dl={dl} mine />)}</ul>
            )}
          </Card>
          <Card>
            <CardHeader><CardTitle>Handed to you</CardTitle></CardHeader>
            {received.length === 0 ? <Empty>Nobody has handed you their decisions.</Empty> : (
              <ul className="divide-y divide-line">{received.map((dl) => <DelegationRow key={dl.id} dl={dl} mine={false} />)}</ul>
            )}
          </Card>
        </>
      )}
    </div>
  )
}
