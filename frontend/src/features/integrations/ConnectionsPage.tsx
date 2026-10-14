import { Link } from "react-router-dom"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { usePermissions } from "@/stores/authStore"
import { useOverview } from "./hooks"
import { HEALTH, ago } from "./labels"
import type { Connection } from "./types"

function FlowDots({ c }: { c: Connection }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
      {c.flows.filter((f) => f.enabled).map((f) => {
        const failing = f.consecutive_failures > 0
        return (
          <span key={f.id} className="flex items-center gap-1.5" title={f.last_error_message ?? undefined}>
            <span className={failing ? "w-2 h-2 rounded-full bg-red-500" : f.last_success_at ? "w-2 h-2 rounded-full bg-green-500" : "w-2 h-2 rounded-full bg-ink-300"} />
            <span className="text-ink-700">{f.label}</span>
            <span className="text-ink-300">{f.running_since ? "reading now" : ago(f.last_success_at)}</span>
          </span>
        )
      })}
    </div>
  )
}

function ConnectionCard({ c }: { c: Connection }) {
  const h = HEALTH[c.health]
  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <Link to={`/integrations/connections/${c.id}`} className="text-[16px] font-semibold text-ink-900 hover:underline">
            {c.name}
          </Link>
          <p className="text-[12px] text-ink-500">
            {c.kind === "erpnext" ? "ERPNext" : c.kind} · {c.base_url}
            {c.about?.versions?.erpnext ? ` · v${c.about.versions.erpnext}` : ""}
          </p>
        </div>
        <Tag variant={h.tone}>{h.label}</Tag>
      </div>
      {c.problems.length > 0 && (
        <ul className="text-[12.5px] space-y-1">
          {c.problems.map((p) => (
            <li key={p.code + (p.entity ?? "")} className="text-red-500">
              <b>{p.title}.</b> <span className="text-ink-700">{p.fix ?? p.detail}</span>
            </li>
          ))}
        </ul>
      )}
      <FlowDots c={c} />
      <div className="flex flex-wrap gap-4 text-[12px] pt-1 border-t border-line">
        <span className="text-ink-500 pt-2">Last checked {ago(c.last_check.at)}</span>
        {c.dead_letters > 0 && (
          <Link to={`/integrations/connections/${c.id}?tab=failed`} className="text-red-500 hover:underline pt-2">
            {c.dead_letters} record{c.dead_letters === 1 ? "" : "s"} could not be read
          </Link>
        )}
        {c.customers_to_review > 0 && (
          <Link to={`/integrations/customers?connection=${c.id}`} className="text-[#8a6d10] font-semibold hover:underline pt-2">
            {c.customers_to_review} customer{c.customers_to_review === 1 ? "" : "s"} to match
          </Link>
        )}
        {c.codes_to_map > 0 && (
          <Link to={`/integrations/codes?connection=${c.id}`} className="text-teal-600 hover:underline pt-2">
            {c.codes_to_map} code{c.codes_to_map === 1 ? "" : "s"} to map
          </Link>
        )}
      </div>
    </Card>
  )
}

export function ConnectionsPage() {
  const perms = usePermissions()
  const canManage = hasPermission(perms, "integrations.manage")
  const { data, isLoading, error } = useOverview()

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-[12.5px] text-ink-500 max-w-[720px]">
          Forge reads customers, items, prices, orders, invoices, payments, stock, workstations and BOMs from each ERP every few
          minutes, checks the login every minute and compares everything once a night. Problems show here and in
          Decisions.
        </p>
        {canManage && data?.credential_keys_configured && (
          <Link to="/integrations/new"><Button>Connect an ERP</Button></Link>
        )}
      </div>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load the connections" />
      {data && data.connections.length === 0 && (
        <Card>
          <Empty>
            No ERP is connected yet.{canManage ? " Use “Connect an ERP” to add ERPNext; it takes a few minutes." : ""}
          </Empty>
        </Card>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        {data?.connections.map((c) => <ConnectionCard key={c.id} c={c} />)}
      </div>
    </div>
  )
}
