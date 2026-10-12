import { useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Input, Select } from "@/components/ui/Field"
import { Nameplate } from "@/components/ui/Nameplate"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { useDebounced } from "@/lib/useDebounced"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { useEnquiryCounts } from "./hooks"
import { MAKE_TONE, PART_LABELS, STATUS_TONE, VIEWS, gradeTone } from "./labels"
import { PriorityBadge } from "./ScoreCard"
import { TimerChip } from "./TimerChip"
import type { EnquiryRow, Sort, View } from "./types"

const PAGE = 100

const GRADE_TEXT = { green: "text-green-500", amber: "text-amber-500", red: "text-red-500", neutral: "text-ink-300" } as const

const SORTS: { key: Sort; label: string }[] = [
  { key: "priority", label: "Highest priority first" },
  { key: "newest", label: "Newest first" },
  { key: "oldest", label: "Oldest first" },
]

/** The Enquiry Queue (Design frame 02): every enquiry with its priority, what was read,
 *  whether we can make it, its timer and its owner. Sorted by priority unless asked (F1.7). */
export function QueuePage() {
  const perms = usePermissions()
  const canWork = hasPermission(perms, "enquiries.work")
  const [search, setSearch] = useSearchParams()
  const view = (VIEWS.find((v) => v.key === search.get("view"))?.key ?? "open") as View
  const sort = (SORTS.find((x) => x.key === search.get("sort"))?.key ?? "priority") as Sort
  const [q, setQ] = useState(search.get("q") ?? "")
  const dq = useDebounced(q)
  const [limit, setLimit] = useState(PAGE)
  const counts = useEnquiryCounts()
  const { data, isLoading, error } = useQuery({
    queryKey: ["enquiries", "list", view, dq, limit, sort],
    queryFn: async () => (await api.get<EnquiryRow[]>("/enquiries", { params: { view, q: dq || undefined, limit, sort } })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  })
  const show = (v: View) => {
    const next = new URLSearchParams(search)
    if (v === "open") next.delete("view")
    else next.set("view", v)
    setSearch(next, { replace: true })
    setLimit(PAGE)
  }
  const order = (x: Sort) => {
    const next = new URLSearchParams(search)
    if (x === "priority") next.delete("sort")
    else next.set("sort", x)
    setSearch(next, { replace: true })
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {VIEWS.map((v) => {
          const n = v.key === "all" ? null : counts.data?.[v.key]
          return (
            <button key={v.key} type="button" title={v.hint} onClick={() => show(v.key)}
              className={cn("rounded-full border px-3 py-1 text-[12.5px]",
                v.key === view ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500 hover:text-ink-900")}>
              {v.label}{n ? <span className="ml-1 font-semibold">{n}</span> : null}
            </button>
          )
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by reference, customer or what is asked"
          className="max-w-[360px]" aria-label="Search enquiries" />
        <Select value={sort} onChange={(e) => order(e.target.value as Sort)} className="w-auto" aria-label="Order">
          {SORTS.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </Select>
        <span className="flex-1" />
        {canWork && <Link to="/enquiries/new"><Button size="sm">+ Manual enquiry</Button></Link>}
      </div>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load enquiries" />
      {data && (
        <Card className="overflow-x-auto">
          {data.length === 0 ? (
            <Empty>{dq ? "Nothing matches that search." : view === "open" ? "No open enquiries. New ones appear here as emails come in, or add one with + Manual enquiry." : "Nothing here."}</Empty>
          ) : (
            <table className="w-full text-[13px] min-w-[960px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-line">
                  <th className="p-3 font-semibold" title="Priority 0-100 from Fit, Intent and Risk">Priority</th>
                  <th className="p-3 font-semibold">Enquiry</th>
                  <th className="p-3 font-semibold">Source</th>
                  <th className="p-3 font-semibold">Requirement (read)</th>
                  <th className="p-3 font-semibold">Timer</th>
                  <th className="p-3 font-semibold">Owner / State</th>
                </tr>
              </thead>
              <tbody>{data.map((e) => <Row key={e.id} e={e} />)}</tbody>
            </table>
          )}
        </Card>
      )}
      {data && data.length === limit && (
        <Button variant="secondary" onClick={() => setLimit(limit + PAGE)}>Show more</Button>
      )}
    </div>
  )
}

function Row({ e }: { e: EnquiryRow }) {
  const read = e.needs_review
    ? e.review_reasons[0] ?? "Needs a person's check"
    : e.source === "manual" ? `${e.lines} ${e.lines === 1 ? "line" : "lines"}, entered by hand`
    : `${e.lines} ${e.lines === 1 ? "line" : "lines"} read`
  return (
    <tr className="border-b border-line last:border-0 hover:bg-surface-0 align-top">
      <td className="p-3 w-[170px]">
        <PriorityBadge score={e.score} />
        <div className="mt-1 text-[11.5px] leading-snug">
          {(["fit", "intent", "risk"] as const).filter((p) => e.score_grades[p]).map((p, i) => (
            <span key={p}>
              {i > 0 && <span className="text-ink-300"> · </span>}
              <span className={GRADE_TEXT[gradeTone(p, e.score_grades[p])]}>{PART_LABELS[p]} {e.score_grades[p]}</span>
            </span>
          ))}
        </div>
        {e.score_reasons[0] && (
          <div className="mt-1 text-[11.5px] text-ink-500 leading-snug">
            {e.score_reasons[0].up ? "▲" : "▼"} {e.score_reasons[0].text}
          </div>
        )}
      </td>
      <td className="p-3">
        <Link to={`/enquiries/${e.id}`} className="hover:opacity-80"><Nameplate id={e.ref} /></Link>
        <div className="mt-1 font-semibold text-ink-900">{e.company ?? e.who}</div>
        {e.company && e.who !== e.company && <div className="text-[12px] text-ink-500">{e.who}</div>}
        <div className="mt-0.5 flex flex-wrap gap-1">
          {e.account_tier === "strategic" && <Tag variant="gold">Strategic</Tag>}
          {e.is_tender && <Tag variant="gold">Tender</Tag>}
        </div>
      </td>
      <td className="p-3"><Tag>{e.source === "manual" ? "Manual" : "Email"}</Tag></td>
      <td className="p-3 max-w-[380px]">
        <Link to={`/enquiries/${e.id}`} className="text-ink-900 hover:underline">{e.summary ?? "(no summary)"}</Link>
        <div className={cn("mt-0.5 font-mono text-[10.5px]", e.needs_review ? "text-amber-500" : "text-teal-600")}>
          ◆ {read}
        </div>
        <div className="mt-1 flex flex-wrap gap-1">
          {e.make_ability && <Tag variant={MAKE_TONE[e.make_ability]} title="Can we make it">{e.make_label}</Tag>}
          {e.possible_duplicate && <Tag variant="amber">Possible duplicate · {e.possible_duplicate.score}%</Tag>}
        </div>
      </td>
      <td className="p-3">
        {e.timer ? (
          <>
            <TimerChip t={e.timer} />
            <div className="text-[11px] text-ink-300 mt-1">{e.timer.label}</div>
          </>
        ) : e.first_response_at ? (
          <span className="text-[12px] text-ink-500">Answered {formatDateTime(e.first_response_at)}</span>
        ) : <span className="text-ink-300">–</span>}
      </td>
      <td className="p-3">
        <div className="text-ink-700">{e.owner_name ?? <span className="text-amber-500 font-semibold">No owner</span>}</div>
        <div className="mt-1 flex flex-wrap gap-1">
          <Tag variant={STATUS_TONE[e.status]}>{e.status_label}</Tag>
        </div>
      </td>
    </tr>
  )
}
