import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { KpiCard } from "@/components/ui/KpiCard"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import type { ApprovalAnalytics } from "@/features/agents/types"
import { pct, secondsLabel, tallyLabel } from "./labels"

const PERIODS = [7, 30, 90]

function hours(h: number | null): string {
  if (h === null) return "–"
  return h < 1 ? `${Math.round(h * 60)} min` : `${h.toFixed(1)} h`
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={`px-3 py-2 font-semibold text-ink-500 text-[11.5px] ${right ? "text-right" : "text-left"}`}>{children}</th>
}
function Td({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <td className={`px-3 py-2 ${right ? "text-right tabular-nums" : ""}`}>{children}</td>
}

/** H9, safeguard 8: decisions, waiting and decision times, overrides (only where the
 * agent recorded a recommendation), missed deadlines and where cards pile up. */
export function ApprovalsPage() {
  const [days, setDays] = useState(30)
  const { data, isLoading, error } = useQuery({
    queryKey: ["decisions", "analytics", days],
    queryFn: async () => (await api.get<ApprovalAnalytics>("/decisions/analytics", { params: { days } })).data,
  })
  const o = data?.overall
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12.5px] text-ink-500 max-w-3xl">
          Waiting time is counted in working hours on the company calendar, from when a step started to its
          decision. Decision time is how long the card was on screen before the person decided. An override
          is a decision different from what the agent recommended; cards without a recommendation are left out.
        </p>
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button key={p} onClick={() => setDays(p)}
              className={`px-2.5 py-1 rounded-md text-[12px] border ${days === p ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500"}`}>
              {p} days
            </button>
          ))}
        </div>
      </div>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load the approval numbers" />
      {data && o && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <KpiCard value={o.decisions} label={`Cards decided in ${data.days} days`} />
            <KpiCard value={secondsLabel(o.median_decision_seconds)} label={`Median decision time (${o.measured} measured)`}
              trend={o.median_decision_seconds === null ? undefined : o.median_decision_seconds < 60 ? "Under the 60 s target" : "Over the 60 s target"}
              trendDirection={o.median_decision_seconds === null ? "neutral" : o.median_decision_seconds < 60 ? "up" : "down"} />
            <KpiCard value={pct(o.under_60s)} label="Decided in under 60 seconds" />
            <KpiCard value={data.waiting_by_role.reduce((a, r) => a + r.past_deadline, 0)} label="Waiting past their deadline now" />
          </div>

          <Card>
            <CardHeader><CardTitle>Waiting now, by who decides</CardTitle></CardHeader>
            {data.waiting_by_role.length === 0 ? <Empty>Nothing waiting.</Empty> : (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead><tr className="border-b border-line"><Th>Who decides</Th><Th right>Waiting</Th><Th right>Past deadline</Th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {data.waiting_by_role.map((r) => (
                      <tr key={r.role_id ?? "anyone"}>
                        <Td>{r.role}</Td><Td right>{r.waiting}</Td>
                        <Td right><span className={r.past_deadline ? "text-red-500 font-semibold" : ""}>{r.past_deadline}</span></Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card>
            <CardHeader><CardTitle>By card type</CardTitle></CardHeader>
            {data.by_type.length === 0 ? <Empty>No cards in this period.</Empty> : (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px] min-w-[720px]">
                  <thead><tr className="border-b border-line">
                    <Th>Card type</Th><Th>Decided</Th><Th right>Median wait</Th><Th right>Median decision</Th>
                    <Th right>Overrides</Th><Th right>Missed deadline</Th><Th right>Waiting now</Th>
                  </tr></thead>
                  <tbody className="divide-y divide-line">
                    {data.by_type.map((t) => (
                      <tr key={t.tool_key}>
                        <Td>{t.title}</Td>
                        <Td>{t.decided ? tallyLabel(t.by_status) : "–"}</Td>
                        <Td right>{hours(t.median_wait_hours)}</Td>
                        <Td right>{secondsLabel(t.median_decision_seconds)}</Td>
                        <Td right>{t.with_recommendation ? `${pct(t.override_rate)} (${t.overrides} of ${t.with_recommendation})` : "–"}</Td>
                        <Td right>{t.missed_deadlines}</Td>
                        <Td right>{t.pending}{t.pending_past_deadline ? <span className="text-red-500"> ({t.pending_past_deadline} late)</span> : null}</Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card>
            <CardHeader><CardTitle>By approver</CardTitle></CardHeader>
            {data.approvers.length === 0 ? <Empty>No decisions in this period.</Empty> : (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px] min-w-[680px]">
                  <thead><tr className="border-b border-line">
                    <Th>Person</Th><Th right>Decided</Th><Th right>Median wait</Th><Th right>Median decision</Th>
                    <Th right>Overrides</Th><Th right>Missed deadline</Th><Th right>As a stand-in</Th>
                  </tr></thead>
                  <tbody className="divide-y divide-line">
                    {data.approvers.map((a) => (
                      <tr key={a.actor}>
                        <Td>{a.name}</Td><Td right>{a.decided}</Td>
                        <Td right>{hours(a.median_wait_hours)}</Td>
                        <Td right>{secondsLabel(a.median_decision_seconds)}</Td>
                        <Td right>{a.with_recommendation ? `${pct(a.override_rate)} (${a.overrides} of ${a.with_recommendation})` : "–"}</Td>
                        <Td right>{a.missed_deadlines}</Td><Td right>{a.for_others}</Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <p className="text-[11.5px] text-ink-300">
            When too many cards for one role pass their deadline, or a card type waits much longer than usual,
            a bottleneck alert appears in the inbox. The limits are on the Deadlines tab.
          </p>
        </>
      )}
    </div>
  )
}
