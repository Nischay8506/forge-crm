import { useEffect, useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Card } from "@/components/ui/Card"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { cn } from "@/lib/utils"
import { useAuthStore } from "@/stores/authStore"
import type { DecisionCard } from "@/features/agents/types"
import { DecisionCardView } from "./DecisionCard"
import { useDecisionCount, useDelegations } from "./hooks"
import { markAction } from "./timing"

// The menu badge imports it from here.
export { useDecisionCount } from "./hooks"

type View = "waiting" | "notices" | "done"
type Scope = "mine" | "all"
const VIEWS: { key: View; label: string }[] = [
  { key: "waiting", label: "Waiting" },
  { key: "notices", label: "Notices" },
  { key: "done", label: "Done" },
]

function Pills<T extends string>({ value, options, onChange, label }: {
  value: T; options: { key: T; label: string }[]; onChange: (v: T) => void; label: string
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-md border border-line overflow-hidden">
      {options.map((o) => (
        <button key={o.key} type="button" onClick={() => onChange(o.key)} aria-pressed={value === o.key}
          className={cn("px-3 py-1.5 text-[12.5px] font-medium border-r border-line last:border-r-0",
            value === o.key ? "bg-ink-700 text-white" : "bg-surface-1 text-ink-500 hover:text-ink-900")}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** The card a link points at (a push notification, a counter, another page). */
function OpenedCard({ id, onClose }: { id: string; onClose: () => void }) {
  const key = ["decisions", "opened", id]
  const { data, isLoading, error } = useQuery({
    queryKey: key,
    queryFn: async () => [(await api.get<DecisionCard>(`/decisions/${id}`)).data],
  })
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-[13px] font-semibold text-ink-700">The card you opened</h2>
        <button className="text-[12px] text-ink-500 hover:text-ink-900" onClick={onClose}>Close</button>
      </div>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not open that card" />
      {data && <div className="max-w-xl"><DecisionCardView d={data[0]} listKey={key} highlight /></div>}
    </div>
  )
}

export function DecisionsPage() {
  const [params, setParams] = useSearchParams()
  const [view, setView] = useState<View>("waiting")
  const [scope, setScope] = useState<Scope>("mine")
  const opened = params.get("card")
  useEffect(() => { markAction() }, [view, scope])
  const me = useAuthStore((s) => s.me)
  const count = useDecisionCount(true)
  const delegations = useDelegations()
  const listKey = ["decisions", view, view === "waiting" ? scope : "all"]
  const { data, isLoading, error } = useQuery({
    queryKey: listKey,
    queryFn: async () => (await api.get<DecisionCard[]>("/decisions", {
      params: { view, scope: view === "waiting" ? scope : "all", limit: 100 } })).data,
  })
  // Cards I handed to a colleague, and to whom.
  const handed = useMemo(() => new Map((delegations.data ?? [])
    .filter((dl) => dl.kind === "card" && dl.from_user_id === me?.id && !dl.revoked_at && dl.decision_id)
    .map((dl) => [dl.decision_id as string, dl.to_name ?? "a colleague"])), [delegations.data, me?.id])
  const cards = (data ?? []).filter((d) => d.id !== opened)
  const badge = (v: View) => v === "waiting" ? count.data?.waiting : v === "notices" ? count.data?.notices : undefined

  return (
    <div className="space-y-4">
      {opened && <OpenedCard id={opened} onClose={() => setParams({})} />}
      <div className="flex flex-wrap items-center gap-2">
        <Pills label="Show" value={view} onChange={setView}
          options={VIEWS.map((v) => ({ key: v.key, label: badge(v.key) ? `${v.label} (${badge(v.key)})` : v.label }))} />
        {view === "waiting" && (
          <Pills label="Whose cards" value={scope} onChange={setScope}
            options={[{ key: "mine", label: "For you" }, { key: "all", label: "Everyone's" }]} />
        )}
      </div>
      <p className="text-[12.5px] text-ink-500">
        {view === "waiting" && scope === "mine" && "Cards you can decide (yours first, the most urgent first), cards past their deadline that were escalated to you, cards you asked for or handed on, and integration alerts and tasks. Approving runs the action as proposed, after the policy rules and stops are checked again."}
        {view === "waiting" && scope === "all" && "Every card still waiting, including ones only other people can decide."}
        {view === "notices" && "What agents did on their own at level L1, and what policy rules stopped."}
        {view === "done" && "Decided, countered, superseded and read items, newest first."}
      </p>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load decisions" />
      {data && cards.length === 0 && (
        <Card><Empty>
          {view === "waiting" && scope === "mine" ? <>Nothing waiting for you. <Link className="text-teal-600 hover:underline" to="/decisions/away">Going away? Set a stand-in.</Link></> : "Nothing here."}
        </Empty></Card>
      )}
      {cards.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 items-start">
          {cards.map((d) => <DecisionCardView key={d.id} d={d} listKey={listKey} handedTo={handed.get(d.id)} />)}
        </div>
      )}
    </div>
  )
}
