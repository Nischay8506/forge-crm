import { useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { useInfiniteQuery, useMutation } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Select } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { cn, formatDateTime } from "@/lib/utils"
import { Actor } from "@/features/steward/Actor"
import { AUDIT_FAMILIES, auditLine, shortHash } from "./labels"
import type { AuditRow, ChainCheck } from "./types"

const PAGE = 50

function AuditItem({ row }: { row: AuditRow }) {
  const [open, setOpen] = useState(false)
  const tone = row.kind === "action.blocked" || row.kind === "action.failed" ? "text-red-500"
    : row.kind === "decision.requested" ? "text-gold-500" : "text-ink-900"
  return (
    <li className="p-3 text-[12.5px]">
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-start gap-3 text-left">
        <span className="font-mono text-ink-300 w-12 shrink-0">#{row.seq}</span>
        <span className="text-ink-300 w-40 shrink-0">{formatDateTime(row.created_at)}</span>
        <span className="w-44 shrink-0"><Actor actor={row.actor} /></span>
        <span className={cn("flex-1", tone)}>{auditLine(row)}</span>
      </button>
      {open && (
        <div className="mt-2 ml-12 space-y-1">
          <div className="font-mono text-[11px] text-ink-500">
            {row.kind} · hash {shortHash(row.hash)} · previous {shortHash(row.prev_hash)}
          </div>
          {row.run_id && (
            <Link to={`/agents/runs/${row.run_id}`} className="text-[12px] text-teal-600 hover:underline">
              Replay this run step by step
            </Link>
          )}
          <pre className="bg-surface-2 rounded-md p-2 text-[11px] overflow-x-auto max-h-80">{JSON.stringify(row.data, null, 2)}</pre>
        </div>
      )}
    </li>
  )
}

export function AuditPage() {
  const [search, setSearch] = useSearchParams()
  const kind = search.get("kind") ?? ""
  const agent = search.get("agent") ?? ""
  const q = useInfiniteQuery({
    queryKey: ["audit", kind, agent],
    queryFn: async ({ pageParam }) => (await api.get<AuditRow[]>("/audit", { params: {
      kind: kind || undefined, agent_key: agent || undefined, before_seq: pageParam, limit: PAGE } })).data,
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].seq : undefined),
  })
  const verify = useMutation({
    mutationFn: async () => (await api.get<ChainCheck>("/audit/verify")).data,
  })
  const rows = q.data?.pages.flat() ?? []
  const v = verify.data

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-4">
        <p className="text-[12.5px] text-ink-500 max-w-3xl">
          Every agent step and every change to agent controls, in order. Records cannot be changed or removed,
          and each one carries the fingerprint of the one before, so any tampering shows up when you check the chain.
        </p>
        <div className="flex items-center gap-2 shrink-0">
          {v && (v.ok
            ? <Tag variant="green">Intact · {v.checked} records</Tag>
            : <Tag variant="red" title={v.problem ?? ""}>Broken at #{v.first_bad_seq}</Tag>)}
          <Button size="sm" variant="secondary" onClick={() => verify.mutate()} disabled={verify.isPending}>
            {verify.isPending ? "Checking…" : "Check the chain"}
          </Button>
        </div>
      </div>
      {v && !v.ok && <p className="bg-red-100 text-red-500 text-[13px] p-3 rounded-md">{v.problem}</p>}
      <ErrorText error={verify.error} fallback="Could not check the chain" />
      <div className="flex items-center gap-2">
        <Select className="w-56" value={kind} onChange={(e) => {
          const next = new URLSearchParams(search)
          if (e.target.value) next.set("kind", e.target.value); else next.delete("kind")
          setSearch(next)
        }}>
          {AUDIT_FAMILIES.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
        </Select>
        {agent && (
          <button className="text-[12px] text-teal-600 hover:underline" onClick={() => {
            const next = new URLSearchParams(search); next.delete("agent"); setSearch(next)
          }}>Agent: {agent.replace(/_/g, " ")} ✕</button>
        )}
      </div>
      {q.isLoading && <Loading />}
      <ErrorText error={q.error} fallback="Could not load the audit log" />
      {q.data && (
        <Card>
          {rows.length === 0 ? <Empty>Nothing recorded yet.</Empty> : (
            <ul className="divide-y divide-line">{rows.map((r) => <AuditItem key={r.seq} row={r} />)}</ul>
          )}
        </Card>
      )}
      {q.hasNextPage && (
        <Button variant="secondary" size="sm" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>
          {q.isFetchingNextPage ? "Loading…" : "Older records"}
        </Button>
      )}
    </div>
  )
}
