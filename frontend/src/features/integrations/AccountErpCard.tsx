import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Empty, ErrorText } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { cn, formatDate, formatDateTime } from "@/lib/utils"
import { INVOICE_TONES, LINK_METHODS, ORDER_TONES, WRITE_STATUS, ago, money, words } from "./labels"
import type { AccountErp, CreateResult } from "./types"

type Tab = "orders" | "invoices" | "outstanding" | "receipts"

/** The account page's ERP section: customer codes, orders, invoices, what is owed, and
 *  "create in ERPNext". Everything is a read-only copy with the time it was read. */
export function AccountErpCard({ accountId, isEntity }: { accountId: string; isEntity: boolean }) {
  const [tab, setTab] = useState<Tab>("orders")
  const qc = useQueryClient()
  const key = ["integrations", "account", accountId]
  const { data, error } = useQuery({
    queryKey: key,
    queryFn: async () => (await api.get<AccountErp>(`/integrations/accounts/${accountId}`)).data,
  })
  const create = useMutation({
    mutationFn: async (connection_id: string) =>
      (await api.post<CreateResult>(`/integrations/accounts/${accountId}/create-in-erp`, { connection_id })).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: key })
      qc.invalidateQueries({ queryKey: ["decisions", "count"] })
    },
  })

  if (error) return <ErrorText error={error} fallback="Could not load the ERP data" />
  if (!data) return null
  const nothing = data.links.length === 0 && data.writes.length === 0 && (data.can_create_in.length === 0 || !isEntity)
  if (nothing) return null
  const overdueCount = data.invoices.filter((i) => i.overdue).length
  const TABS: { key: Tab; label: string }[] = [
    { key: "orders", label: `Orders (${data.orders.length})` },
    { key: "invoices", label: `Invoices (${data.invoices.length})` },
    { key: "outstanding", label: overdueCount ? `Outstanding · ${overdueCount} overdue` : "Outstanding" },
    { key: "receipts", label: `Payments (${data.receipts.length})` },
  ]

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>From the ERP</CardTitle>
        <span className="text-[11.5px] text-ink-300" title={formatDateTime(data.as_of)}>
          {data.as_of ? `As of ${ago(data.as_of)}` : ""}
        </span>
      </CardHeader>
      <CardContent className="space-y-3">
        {data.links.length > 0 && (
          <ul className="space-y-1 text-[12.5px]">
            {data.links.map((l) => (
              <li key={l.id} className="flex flex-wrap items-center gap-x-2">
                <span className="font-semibold">{l.connection}</span>
                <span className="font-mono text-ink-700">{l.external_id}</span>
                {l.gone && <Tag variant="amber">No longer in the ERP</Tag>}
                <span className="text-ink-300">
                  {l.link_method ? LINK_METHODS[l.link_method] ?? l.link_method : ""}
                  {l.payment_terms ? ` · ${l.payment_terms}` : ""}
                  {l.credit_limit && Number(l.credit_limit) > 0 ? ` · credit limit ${money(l.credit_limit, l.currency)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}

        {isEntity && data.can_create_in.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 rounded-md bg-surface-2 p-2.5 text-[12.5px]">
            <span className="text-ink-500">Not a customer in {data.can_create_in.map((c) => c.name).join(", ")} yet.</span>
            {data.can_create_in.map((c) => (
              <Button key={c.id} size="sm" variant="secondary" disabled={create.isPending} onClick={() => create.mutate(c.id)}>
                Create in {c.name}
              </Button>
            ))}
          </div>
        )}
        {create.data && <CreateOutcome r={create.data} />}
        <ErrorText error={create.error} fallback="Could not ask for it" />

        {data.writes.length > 0 && (
          <ul className="text-[12px] space-y-1">
            {data.writes.slice(0, 3).map((w) => {
              const st = WRITE_STATUS[w.status] ?? { label: w.status, tone: "neutral" as const }
              return (
                <li key={w.id} className="flex flex-wrap items-center gap-2">
                  <Tag variant={st.tone}>{st.label}</Tag>
                  <span className="text-ink-500">
                    Create as customer{w.external_id ? ` (${w.external_id})` : ""} · {formatDateTime(w.updated_at)}
                  </span>
                  {w.status === "pending" && <Link to="/decisions" className="text-teal-600 hover:underline">Open Decisions</Link>}
                  {w.error && <span className="text-red-500">{w.error}</span>}
                </li>
              )
            })}
          </ul>
        )}

        {data.links.length > 0 && (
          <>
            <nav className="flex gap-1 border-b border-line">
              {TABS.map((t) => (
                <button key={t.key} onClick={() => setTab(t.key)}
                  className={cn("px-2.5 py-1.5 text-[12.5px] font-medium text-ink-500 border-b-2 border-transparent -mb-px hover:text-ink-900",
                    tab === t.key && "text-ink-900 border-teal-500")}>
                  {t.label}
                </button>
              ))}
            </nav>
            {tab === "orders" && <Orders data={data} />}
            {tab === "invoices" && <Invoices rows={data.invoices} />}
            {tab === "outstanding" && <Outstanding data={data} />}
            {tab === "receipts" && <Receipts data={data} />}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function CreateOutcome({ r }: { r: CreateResult }) {
  const text: Record<CreateResult["outcome"], string> = {
    done: `Created in the ERP as ${r.write.external_id ?? "a new customer"}.`,
    waiting: "Asked. A person approves it in Decisions before it is sent.",
    refused: "Not sent:",
    blocked: "Not allowed:",
    failed: "It failed:",
    already: `Already asked (${WRITE_STATUS[r.write.status]?.label.toLowerCase() ?? r.write.status}).`,
  }
  const bad = r.outcome === "refused" || r.outcome === "blocked" || r.outcome === "failed"
  return (
    <div className={cn("rounded-md p-2.5 text-[12.5px]", bad ? "bg-red-100 text-red-500" : "bg-teal-100 text-teal-600")}>
      <p>{text[r.outcome]}</p>
      {bad && (r.reasons?.length ? <ul className="list-disc pl-4">{r.reasons.map((x) => <li key={x}>{x}</li>)}</ul> : <p>{r.error ?? r.write.error}</p>)}
    </div>
  )
}

const th = "py-1.5 pr-3 font-semibold"
const td = "py-1.5 pr-3"

function Orders({ data }: { data: AccountErp }) {
  if (data.orders.length === 0) return <Empty>No orders.</Empty>
  const many = new Set(data.orders.map((o) => o.connection)).size > 1
  return (
    <table className="w-full text-[12.5px]">
      <thead><tr className="text-left text-ink-500 border-b border-line">
        <th className={th}>Order</th><th className={th}>Date</th><th className={th}>Status</th>
        <th className={th}>Customer PO</th><th className={`${th} text-right`}>Total</th>
      </tr></thead>
      <tbody>
        {data.orders.map((o) => (
          <tr key={o.id} className="border-b border-line last:border-0">
            <td className={td}><span className="font-mono">{o.number}</span>{many && <span className="text-ink-300"> · {o.connection}</span>}</td>
            <td className={td}>{formatDate(o.order_date)}</td>
            <td className={td}><Tag variant={ORDER_TONES[o.status] ?? "neutral"}>{words(o.status)}</Tag></td>
            <td className={`${td} text-ink-500`}>{o.customer_po ?? ""}</td>
            <td className={`${td} text-right tabular-nums`}>{money(o.grand_total, o.currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Invoices({ rows }: { rows: AccountErp["invoices"] }) {
  if (rows.length === 0) return <Empty>No invoices.</Empty>
  return (
    <table className="w-full text-[12.5px]">
      <thead><tr className="text-left text-ink-500 border-b border-line">
        <th className={th}>Invoice</th><th className={th}>Date</th><th className={th}>Due</th><th className={th}>Status</th>
        <th className={`${th} text-right`}>Total</th><th className={`${th} text-right`}>Unpaid</th>
      </tr></thead>
      <tbody>
        {rows.map((i) => (
          <tr key={i.id} className="border-b border-line last:border-0">
            <td className={td}><span className="font-mono">{i.number}</span></td>
            <td className={td}>{formatDate(i.invoice_date)}</td>
            <td className={cn(td, i.overdue && "text-red-500 font-semibold")}>{formatDate(i.due_date)}</td>
            <td className={td}><Tag variant={i.overdue ? "red" : INVOICE_TONES[i.status] ?? "neutral"}>{i.overdue ? "overdue" : words(i.status)}</Tag></td>
            <td className={`${td} text-right tabular-nums`}>{money(i.grand_total, i.currency)}</td>
            <td className={`${td} text-right tabular-nums`}>{Number(i.outstanding) > 0 ? money(i.outstanding, i.currency) : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Outstanding({ data }: { data: AccountErp }) {
  if (data.outstanding.length === 0) return <Empty>Nothing is owed.</Empty>
  const open = data.invoices.filter((i) => Number(i.outstanding) > 0 && !["draft", "cancelled", "paid"].includes(i.status))
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-4">
        {data.outstanding.map((o) => (
          <div key={o.currency} className="rounded-md border border-line p-3 min-w-[180px]">
            <p className="text-[11.5px] text-ink-500">Owed ({o.currency})</p>
            <p className="text-[18px] font-bold tabular-nums">{money(o.outstanding, o.currency)}</p>
            {Number(o.overdue) > 0 && <p className="text-[12px] text-red-500">{money(o.overdue, o.currency)} overdue</p>}
          </div>
        ))}
      </div>
      <Invoices rows={open} />
    </div>
  )
}

function Receipts({ data }: { data: AccountErp }) {
  if (data.receipts.length === 0) return <Empty>No payments.</Empty>
  return (
    <table className="w-full text-[12.5px]">
      <thead><tr className="text-left text-ink-500 border-b border-line">
        <th className={th}>Payment</th><th className={th}>Received</th><th className={th}>Status</th>
        <th className={`${th} text-right`}>Amount</th>
      </tr></thead>
      <tbody>
        {data.receipts.map((r) => (
          <tr key={r.id} className="border-b border-line last:border-0">
            <td className={td}><span className="font-mono">{r.number}</span></td>
            <td className={td}>{formatDate(r.received_on)}</td>
            <td className={td}><Tag variant={r.status === "posted" ? "green" : "neutral"}>{words(r.status)}</Tag></td>
            <td className={`${td} text-right tabular-nums`}>{money(r.amount, r.currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
