import { useState } from "react"
import { Link } from "react-router-dom"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Input, Select } from "@/components/ui/Field"
import { ScoreBadge } from "@/components/ui/ScoreBadge"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { useDebounced } from "@/lib/useDebounced"
import { usePermissions } from "@/stores/authStore"
import { KIND_LABELS, SITE_TYPE_LABELS } from "@/features/steward/labels"
import type { AccountSummary } from "@/features/steward/types"

const PAGE = 50

export function AccountsPage() {
  const canWrite = hasPermission(usePermissions(), "accounts.write")
  const [q, setQ] = useState("")
  const [kind, setKind] = useState("")
  const [limit, setLimit] = useState(PAGE)
  const dq = useDebounced(q)
  const { data, isLoading, error } = useQuery({
    queryKey: ["accounts", "list", dq, kind, limit],
    queryFn: async () => (await api.get<AccountSummary[]>("/accounts", {
      params: { q: dq || undefined, kind: kind || undefined, limit } })).data,
    placeholderData: keepPreviousData,
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-ink-900">Accounts</h1>
        {canWrite && <Link to="/accounts/new"><Button>New account</Button></Link>}
      </div>
      <div className="flex gap-3">
        <Input value={q} onChange={(e) => { setQ(e.target.value); setLimit(PAGE) }}
          placeholder="Search by name, GSTIN or city" className="max-w-md" />
        <Select value={kind} onChange={(e) => setKind(e.target.value)} className="max-w-[200px]">
          <option value="">All types</option>
          <option value="group">Groups</option>
          <option value="entity">Legal entities</option>
          <option value="site">Sites</option>
        </Select>
      </div>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load accounts" />
      {data && (
        <Card>
          {data.length === 0 ? <Empty>No accounts found.</Empty> : (
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-line">
                  <th className="p-3 font-semibold">Name</th>
                  <th className="p-3 font-semibold">Type</th>
                  <th className="p-3 font-semibold">GSTIN</th>
                  <th className="p-3 font-semibold">City</th>
                  <th className="p-3 font-semibold text-right">Hygiene</th>
                </tr>
              </thead>
              <tbody>
                {data.map((a) => (
                  <tr key={a.id} className="border-b border-line last:border-0 hover:bg-surface-0">
                    <td className="p-3"><Link to={`/accounts/${a.id}`} className="text-teal-600 font-medium hover:underline">{a.name}</Link></td>
                    <td className="p-3"><Tag>{a.kind === "site" && a.site_type ? SITE_TYPE_LABELS[a.site_type] ?? a.site_type : KIND_LABELS[a.kind]}</Tag></td>
                    <td className="p-3 font-mono text-[12px]">{a.gstin ?? "–"}</td>
                    <td className="p-3">{a.city ?? "–"}</td>
                    <td className="p-3 text-right"><ScoreBadge score={a.hygiene_score} /></td>
                  </tr>
                ))}
              </tbody>
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
