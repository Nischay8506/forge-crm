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
          {row.evidence.state === "removed" && (
            <p className="text-[12px] text-ink-500 italic">
              Details removed {row.evidence.removed_at ? `on ${formatDateTime(row.evidence.removed_at)}` : ""} ({row.evidence.reason}).
            </p>
          )}
          {row.evidence.state === "missing" && <p className="text-[12px] text-red-500">This record's details are missing.</p>}
          <pre className="bg-surface-2 rounded-md p-2 text-[11px] overflow-x-auto max-h-80">{JSON.stringify(row.data, null, 2)}</pre>
        </div>
      )}
    </li>
  )
}

function ChainSummary({ v }: { v: ChainCheck }) {
  const s = v.seals
  return (
    <div className="rounded-md bg-surface-2 p-3 text-[12.5px] text-ink-700 space-y-1">
      {s.state === "off" && (
        <p className="text-amber-500 font-semibold">
          Seals are off: AUDIT_SEAL_KEYS is not set in backend/.env, so someone with full server access could rewrite
          history without it showing. See the handover doc, section 4.
        </p>
      )}
      {s.state === "misconfigured" && (
        <p className="text-red-500 font-semibold">AUDIT_SEAL_KEYS in backend/.env is not valid, so nothing is being sealed.</p>
      )}
      {s.state === "on" && (s.sealed_upto
        ? <p>Sealed with a signed seal up to record #{s.sealed_upto} ({s.sealed_at ? formatDateTime(s.sealed_at) : ""}); {s.checked} seal{s.checked === 1 ? "" : "s"} checked. Seals are made every hour.</p>
        : <p>No seal yet. The chain is sealed every hour while the background worker runs.</p>)}
      {s.unknown_keys.length > 0 && (
        <p className="text-amber-500">
          Some seals were signed with a key no longer in AUDIT_SEAL_KEYS ({s.unknown_keys.join(", ")}), so they could
          not be checked. Put the old key back after the current one to check them.
        </p>
      )}
      {v.warning && <p className="text-amber-500">{v.warning}</p>}
      {v.starts_at > 1 && <p>Records before #{v.starts_at} were removed by the retention rules, after being sealed.</p>}
      {v.details_removed > 0 && (
        <p>The details of {v.details_removed} record{v.details_removed === 1 ? " were" : "s were"} removed by retention or erasure, as allowed; what happened still checks out.</p>
      )}
    </div>
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
      {v && <ChainSummary v={v} />}
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
