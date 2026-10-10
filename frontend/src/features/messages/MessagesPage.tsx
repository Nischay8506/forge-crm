import { useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { hasPermission } from "@/lib/permissions"
import { cn } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { FILTERS } from "./labels"
import { MessageRow } from "./MessageRow"
import type { MessageView } from "./types"

const PAGE = 50

/** Every message to and from customers, newest first, or the notifications sent to staff. */
export function MessagesPage() {
  const canSend = hasPermission(usePermissions(), "messages.send")
  const [search, setSearch] = useSearchParams()
  const filter = FILTERS.find((f) => f.key === search.get("show")) ?? FILTERS[0]
  const audience = search.get("audience") === "staff" ? "staff" : "customers"
  const [limit, setLimit] = useState(PAGE)
  const { data, isLoading, error } = useQuery({
    queryKey: ["messages", "list", filter.key, audience, limit],
    queryFn: async () => (await api.get<MessageView[]>("/messages", { params: {
      status: filter.status, direction: filter.direction, audience, limit } })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  })
  const show = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(search)
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) next.delete(k)
      else next.set(k, v)
    }
    setSearch(next, { replace: true })
    setLimit(PAGE)
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button key={f.key} type="button" onClick={() => show({ show: f.key === "all" ? null : f.key })}
            className={cn("rounded-full border px-3 py-1 text-[12.5px]",
              f.key === filter.key ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500 hover:text-ink-900")}>
            {f.label}
          </button>
        ))}
        <span className="flex-1" />
        <label className="flex items-center gap-1.5 text-[12.5px] text-ink-500">
          <input type="checkbox" checked={audience === "staff"} onChange={(e) => show({ audience: e.target.checked ? "staff" : null })} />
          Notifications to staff
        </label>
        {canSend && <Link to="/messages/new"><Button size="sm">New message</Button></Link>}
      </div>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load messages" />
      {data && (
        <Card className="overflow-hidden">
          {data.length === 0 ? (
            <Empty>{audience === "staff" ? "No notifications sent to staff yet." : "No messages here yet."}</Empty>
          ) : (
            <div className="divide-y divide-line">{data.map((m) => <MessageRow key={m.id} m={m} />)}</div>
          )}
        </Card>
      )}
      {data && data.length === limit && (
        <Button variant="secondary" onClick={() => setLimit(limit + PAGE)}>Show more</Button>
      )}
    </div>
  )
}
