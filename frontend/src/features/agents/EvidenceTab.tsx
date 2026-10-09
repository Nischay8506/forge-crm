import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import type { AgentDetail, Outcome, Scorecard } from "./types"

const COLUMNS: { key: Outcome; label: string; help: string; tone?: string }[] = [
  { key: "proposed", label: "Proposed", help: "Actions it proposed" },
  { key: "acted_alone", label: "Acted alone", help: "Carried out without a person, as its level and the rules allowed", tone: "text-green-500" },
  { key: "asked", label: "Asked", help: "Had to ask a person first", tone: "text-gold-500" },
  { key: "approved", label: "Approved", help: "A person said yes" },
  { key: "rejected", label: "Rejected", help: "A person said no: an override", tone: "text-red-500" },
  { key: "undone", label: "Undone", help: "Carried out, then reversed by a person: an override", tone: "text-red-500" },
  { key: "blocked", label: "Blocked", help: "Refused by the rules or a stop", tone: "text-red-500" },
  { key: "failed", label: "Failed", help: "Went wrong while running", tone: "text-red-500" },
]

/** "2026-10" → "Oct 2026". */
export function monthLabel(m: string): string {
  const [y, mo] = m.split("-").map(Number)
  return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString("en-IN", { month: "short", year: "numeric", timeZone: "UTC" })
}

export function rate(r: number | null): string {
  return r === null ? "–" : `${(r * 100).toFixed(r > 0 && r < 0.1 ? 1 : 0)}%`
}

/** An agent's track record by month and version (R8): what to look at before
 * changing its level. It never changes the level itself. */
export function EvidenceTab({ agent }: { agent: AgentDetail }) {
  const perms = usePermissions()
  const canAudit = hasPermission(perms, "audit.read")
  const { data, isLoading, error } = useQuery({
    queryKey: ["agents", agent.key, "scorecard"],
    queryFn: async () => (await api.get<Scorecard>(`/agents/${agent.key}/scorecard`)).data,
  })
  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load the evidence" />

  const link = (row: Scorecard["rows"][number], o: Outcome) => {
    const q = new URLSearchParams({ agent: agent.key, month: row.month, outcome: o })
    if (row.version !== null) q.set("version", String(row.version))
    return `/agents/runs?${q}`
  }

  return (
    <div className="space-y-4">
      <p className="text-[12.5px] text-ink-500 max-w-3xl">
        What {agent.name} did each month, split by version, counted from the audit trail. An override is a person
        rejecting what it asked to do, or undoing what it did. These numbers help decide whether to change its
        level; they never change it by themselves. Only someone allowed to manage agents can, on the Overview tab.
      </p>
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>By month and version</CardTitle>
          {agent.version !== null && <Tag variant="teal">Running now: v{agent.version}</Tag>}
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {data.rows.length === 0 ? <Empty>No agent work yet.</Empty> : (
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-line">
                  <th className="p-2 font-semibold">Month</th>
                  <th className="p-2 font-semibold">Version</th>
                  {COLUMNS.map((c) => <th key={c.key} className="p-2 font-semibold text-right" title={c.help}>{c.label}</th>)}
                  <th className="p-2 font-semibold text-right" title="(rejected + undone) ÷ (acted alone + approved + rejected)">Override rate</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={`${r.month}-${r.version}`} className="border-b border-line last:border-0">
                    <td className="p-2 whitespace-nowrap">{monthLabel(r.month)}</td>
                    <td className="p-2">{r.version === null ? <span className="text-ink-300">before versions</span> : `v${r.version}`}</td>
                    {COLUMNS.map((c) => (
                      <td key={c.key} className={cn("p-2 text-right tabular-nums", r[c.key] > 0 && c.tone)}>
                        {r[c.key] > 0 && canAudit
                          ? <Link to={link(r, c.key)} className="hover:underline" title={`Open the runs behind this: ${c.help.toLowerCase()}`}>{r[c.key]}</Link>
                          : r[c.key]}
                      </td>
                    ))}
                    <td className="p-2 text-right tabular-nums font-semibold">{rate(r.override_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Versions</CardTitle></CardHeader>
        <CardContent>
          <p className="text-[12px] text-ink-500 mb-2">
            A new version starts whenever its charter, tools, levels, triggers or model tier change in the code.
            Levels, rules and stops your company sets are not part of the version.
          </p>
          {data.versions.length === 0 ? <Empty>Not run yet.</Empty> : (
            <ul className="divide-y divide-line text-[12.5px]">
              {data.versions.map((v) => (
                <li key={v.number} className="py-1.5 flex gap-3">
                  <span className="font-mono font-semibold w-10">v{v.number}</span>
                  <span className="text-ink-300 w-44 shrink-0">first used {formatDateTime(v.first_seen_at)}</span>
                  <span className="text-ink-700">{v.changes.join("; ")}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
