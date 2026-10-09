import { Link, useSearchParams } from "react-router-dom"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { AgentBadge } from "@/components/ui/AgentBadge"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Select } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { formatDateTime } from "@/lib/utils"
import { monthLabel } from "./EvidenceTab"
import { RUN_STATUS, offset, triggerLabel } from "./labels"

const OUTCOME_WORDS: Record<string, string> = {
  proposed: "proposed", acted_alone: "acted alone", asked: "asked a person", approved: "approved",
  rejected: "rejected", undone: "undone later", blocked: "blocked", failed: "failed",
}
import type { AgentSummary, RunRow } from "./types"

const PAGE = 50

/** How a run's steps ended, as small tags. */
export function RunTally({ run }: { run: RunRow }) {
  const t = run.tally
  const parts: { n: number; label: string; tone: "green" | "gold" | "red" | "neutral" }[] = [
    { n: t.done, label: "done", tone: "green" },
    { n: run.waiting, label: "waiting for a person", tone: "gold" },
    { n: t.asked - run.waiting, label: "asked a person", tone: "neutral" },
    { n: t.blocked, label: "blocked", tone: "red" },
    { n: t.failed, label: "failed", tone: "red" },
  ]
  const shown = parts.filter((p) => p.n > 0)
  if (shown.length === 0) return <span className="text-ink-300">Nothing to do</span>
  return (
    <span className="flex flex-wrap gap-1">
      {shown.map((p) => <Tag key={p.label} variant={p.tone}>{p.n} {p.label}</Tag>)}
    </span>
  )
}

export function RunsPage() {
  const [search, setSearch] = useSearchParams()
  const agent = search.get("agent") ?? ""
  const record = search.get("record") ?? ""
  const version = search.get("version") ?? ""
  const month = search.get("month") ?? ""
  const outcome = search.get("outcome") ?? ""
  const narrowed = Boolean(outcome || version || month)
  const agents = useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get<AgentSummary[]>("/agents")).data,
  })
  const q = useInfiniteQuery({
    queryKey: ["audit", "runs", agent, record, version, month, outcome],
    queryFn: async ({ pageParam }) => (await api.get<RunRow[]>("/audit/runs", { params: {
      agent_key: agent || undefined, record_id: record || undefined, version: version || undefined,
      month: month || undefined, outcome: outcome || undefined, before: pageParam, limit: PAGE } })).data,
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].started_at : undefined),
  })
  const rows = q.data?.pages.flat() ?? []
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(search)
    if (value) next.set(key, value); else next.delete(key)
    setSearch(next)
  }

  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-ink-500 max-w-3xl">
        Each run is one piece of agent work, from what started it to how every step ended. Open a run to replay
        it step by step: what the agent proposed, what the rules said, who decided, and what happened.
      </p>
      <div className="flex items-center gap-2">
        <Select className="w-56" value={agent} onChange={(e) => setParam("agent", e.target.value)}>
          <option value="">All agents</option>
          {(agents.data ?? []).map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}
        </Select>
        {record && (
          <button className="text-[12px] text-teal-600 hover:underline" onClick={() => setParam("record", "")}>
            Only runs about one record ✕
          </button>
        )}
        {narrowed && (
          <button className="text-[12px] text-teal-600 hover:underline" onClick={() => {
            const next = new URLSearchParams(search)
            for (const k of ["version", "month", "outcome"]) next.delete(k)
            setSearch(next)
          }}>
            Only runs behind one number{version && ` (v${version}`}{month && `${version ? ", " : " ("}${monthLabel(month)}`}{(version || month) && ")"}: {OUTCOME_WORDS[outcome] ?? outcome} ✕
          </button>
        )}
      </div>
      {q.isLoading && <Loading />}
      <ErrorText error={q.error} fallback="Could not load the runs" />
      {q.data && (
        <Card>
          {rows.length === 0 ? <Empty>No agent runs yet.</Empty> : (
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-line">
                  <th className="p-3 font-semibold">Started</th>
                  <th className="p-3 font-semibold">Agent</th>
                  <th className="p-3 font-semibold">What started it</th>
                  <th className="p-3 font-semibold">How it ended</th>
                  <th className="p-3 font-semibold">Took</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const st = RUN_STATUS[r.status] ?? { label: r.status, tone: "neutral" as const }
                  return (
                    <tr key={r.id} className="border-b border-line last:border-0 hover:bg-surface-2">
                      <td className="p-3 whitespace-nowrap">
                        <Link to={`/agents/runs/${r.id}`} className="text-teal-600 hover:underline">
                          {formatDateTime(r.started_at)}
                        </Link>
                      </td>
                      <td className="p-3">
                        <span className="flex items-center gap-1.5">
                          <AgentBadge label={r.agent_name.toUpperCase()} />
                          {r.agent_version !== null && <span className="font-mono text-[10.5px] text-ink-300">v{r.agent_version}</span>}
                        </span>
                      </td>
                      <td className="p-3 text-ink-700">{triggerLabel(r)}</td>
                      <td className="p-3">
                        <span className="flex flex-wrap items-center gap-1">
                          {r.status !== "succeeded" && <Tag variant={st.tone}>{st.label}</Tag>}
                          <RunTally run={r} />
                        </span>
                      </td>
                      <td className="p-3 text-ink-500 whitespace-nowrap">
                        {r.finished_at ? offset(r.started_at, r.finished_at).slice(1) : ""}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </Card>
      )}
      {q.hasNextPage && (
        <Button variant="secondary" size="sm" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>
          {q.isFetchingNextPage ? "Loading…" : "Older runs"}
        </Button>
      )}
    </div>
  )
}
