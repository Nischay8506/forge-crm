import { useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Input, Select } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { useDebounced } from "@/lib/useDebounced"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { AccountPicker } from "@/features/accounts/AccountPicker"
import { Actor } from "@/features/steward/Actor"
import type { AccountSummary } from "@/features/steward/types"
import { useOverview } from "./hooks"
import { LINK_METHODS, LINK_STATUS, money } from "./labels"
import type { CustomerList, ErpCustomerRow } from "./types"

type Status = "review" | "linked" | "pending" | "ignored" | "all"
const STATUSES: Status[] = ["review", "linked", "pending", "ignored", "all"]

export function CustomersPage() {
  const [params, setParams] = useSearchParams()
  const connection = params.get("connection") ?? ""
  const status = (params.get("status") as Status) || "review"
  const [q, setQ] = useState("")
  const dq = useDebounced(q)
  const overview = useOverview()
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next)
  }
  const key = ["integrations", "customers", connection, status, dq]
  const { data, isLoading, error } = useQuery({
    queryKey: key,
    queryFn: async () => (await api.get<CustomerList>("/integrations/customers", {
      params: { connection_id: connection || undefined, status, q: dq || undefined, limit: 200 },
    })).data,
  })
  const total = data ? Object.values(data.counts).reduce((a, b) => a + b, 0) : 0

  return (
    <div className="space-y-4">
      <p className="text-[12.5px] text-ink-500 max-w-[820px]">
        Each ERP customer is linked to one Forge account. Forge links on its own only when a verified ID (GSTIN, PAN or
        CIN) matches exactly one account; anything less certain waits here for a person. Linking, unlinking and
        ignoring are kept in the audit log.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <nav className="flex gap-1">
          {STATUSES.map((s) => {
            const n = s === "all" ? total : data?.counts[s] ?? 0
            return (
              <button key={s} onClick={() => setParam("status", s === "review" ? "" : s)}
                className={cn("px-3 py-1.5 rounded-md text-[12.5px] font-medium text-ink-500 hover:bg-surface-2",
                  status === s && "bg-teal-100 text-teal-600")}>
                {s === "all" ? "All" : LINK_STATUS[s].label}{data ? ` (${n})` : ""}
              </button>
            )
          })}
        </nav>
        <Select className="w-[220px]" value={connection} onChange={(e) => setParam("connection", e.target.value)}>
          <option value="">Every connection</option>
          {overview.data?.connections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>
        <Input className="w-[260px]" placeholder="Search name, ERP code or GSTIN" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {status !== "all" && <p className="text-[12px] text-ink-300">{LINK_STATUS[status].help}</p>}
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load the customers" />
      {data && data.items.length === 0 && <Card><Empty>Nothing here.</Empty></Card>}
      <div className="space-y-3">
        {data?.items.map((c) => <CustomerCard key={c.id} c={c} />)}
      </div>
      {data && data.total > data.items.length && (
        <p className="text-[12px] text-ink-300">Showing {data.items.length} of {data.total}. Search to narrow it down.</p>
      )}
    </div>
  )
}

function CustomerCard({ c }: { c: ErpCustomerRow }) {
  const perms = usePermissions()
  const canMap = hasPermission(perms, "integrations.map")
  const canCreate = canMap && hasPermission(perms, "accounts.write")
  const qc = useQueryClient()
  const [picked, setPicked] = useState<AccountSummary | null>(null)
  const act = useMutation({
    mutationFn: async ({ what, account_id }: { what: "link" | "new-account" | "ignore" | "unlink"; account_id?: string }) => {
      let body: Record<string, unknown> | undefined
      if (what === "link") body = { account_id }
      if (what === "ignore" || what === "unlink") {
        const note = window.prompt(what === "ignore" ? "Why ignore this customer? (optional)" : "Why unlink it? (optional)", "")
        if (note === null) return null
        body = { note: note || null }
      }
      return (await api.post<ErpCustomerRow>(`/integrations/customers/${c.id}/${what}`, body)).data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["integrations"] })
      qc.invalidateQueries({ queryKey: ["decisions", "count"] })
    },
  })
  const st = LINK_STATUS[c.link_status]
  const open = c.link_status === "review" || c.link_status === "pending"

  return (
    <Card className={cn("p-4 space-y-2", c.link_status === "review" && "border-l-4 border-l-gold-500")}>
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-semibold text-ink-900">{c.name}</span>
            <Tag variant={st.tone}>{st.label}</Tag>
            {c.disabled && <Tag variant="amber">Disabled in ERP</Tag>}
          </div>
          <p className="text-[12px] text-ink-500">
            {c.connection} · {c.external_id}{c.gstin ? ` · GSTIN ${c.gstin}` : ""}{c.city ? ` · ${c.city}` : ""}
            {c.customer_group ? ` · ${c.customer_group}` : ""}
            {c.credit_limit && Number(c.credit_limit) > 0 ? ` · credit limit ${money(c.credit_limit, c.currency)}` : ""}
          </p>
        </div>
        {c.link_status === "linked" && c.account_id && (
          <div className="text-right text-[12.5px] shrink-0">
            <Link to={`/accounts/${c.account_id}`} className="text-teal-600 font-semibold hover:underline">{c.account_name}</Link>
            <p className="text-ink-300">{c.link_method ? LINK_METHODS[c.link_method] ?? c.link_method : ""}</p>
          </div>
        )}
      </div>

      {c.link_reasons.length > 0 && (
        <p className="text-[12.5px] text-ink-700">{c.link_reasons.join("; ")}</p>
      )}

      {open && c.candidates.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[12px] font-semibold text-ink-500">Possible accounts</p>
          {c.candidates.map((m) => (
            <div key={m.account_id} className="flex items-center justify-between gap-3 rounded-md border border-line p-2 text-[12.5px]">
              <div>
                <Link to={`/accounts/${m.account_id}`} className="font-medium text-ink-900 hover:underline">{m.name}</Link>
                <span className="text-ink-300">{m.city ? ` · ${m.city}` : ""}{m.gstin ? ` · ${m.gstin}` : ""} · score {Math.round(m.score)}</span>
                {m.reasons.length > 0 && <p className="text-ink-500">{m.reasons.join("; ")}</p>}
              </div>
              {canMap && (
                <Button size="sm" variant="gold" disabled={act.isPending}
                  onClick={() => act.mutate({ what: "link", account_id: m.account_id })}>Link to this</Button>
              )}
            </div>
          ))}
        </div>
      )}

      {canMap && open && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <div className="w-[320px]">
            <AccountPicker value={picked} onChange={setPicked} kind="entity" placeholder="Link to another account…" />
          </div>
          {picked && (
            <Button size="sm" variant="gold" disabled={act.isPending}
              onClick={() => act.mutate({ what: "link", account_id: picked.id })}>Link to {picked.name}</Button>
          )}
          {canCreate && (
            <Button size="sm" variant="secondary" disabled={act.isPending} onClick={() => act.mutate({ what: "new-account" })}>
              Create a new account
            </Button>
          )}
          {c.link_status !== "ignored" && (
            <Button size="sm" variant="danger" disabled={act.isPending} onClick={() => act.mutate({ what: "ignore" })}>Ignore</Button>
          )}
        </div>
      )}
      {canMap && c.link_status === "linked" && (
        <div className="flex items-center justify-between text-[12px] text-ink-300">
          <span className="flex items-center gap-1.5">Linked {formatDateTime(c.linked_at)} by <Actor actor={c.linked_by} /></span>
          <button className="text-red-500 hover:underline disabled:opacity-50" disabled={act.isPending}
            onClick={() => act.mutate({ what: "unlink" })}>Unlink</button>
        </div>
      )}
      {canMap && c.link_status === "ignored" && (
        <div className="flex items-center gap-2">
          <div className="w-[320px]">
            <AccountPicker value={picked} onChange={setPicked} kind="entity" placeholder="Link it after all…" />
          </div>
          {picked && (
            <Button size="sm" variant="gold" disabled={act.isPending}
              onClick={() => act.mutate({ what: "link", account_id: picked.id })}>Link to {picked.name}</Button>
          )}
        </div>
      )}
      <ErrorText error={act.error} fallback="Could not save that" />
    </Card>
  )
}
