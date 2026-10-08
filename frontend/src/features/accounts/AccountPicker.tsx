import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Input } from "@/components/ui/Field"
import { useDebounced } from "@/lib/useDebounced"
import type { AccountSummary, Kind } from "@/features/steward/types"

/** Search-and-pick an account; shows the picked one with a way to clear it. */
export function AccountPicker({ value, onChange, kind, placeholder }: {
  value: AccountSummary | null
  onChange: (a: AccountSummary | null) => void
  kind?: Kind
  placeholder?: string
}) {
  const [q, setQ] = useState("")
  const dq = useDebounced(q)
  const { data } = useQuery({
    queryKey: ["accounts", "pick", kind, dq],
    queryFn: async () => (await api.get<AccountSummary[]>("/accounts", {
      params: { q: dq, kind, limit: 8 } })).data,
    enabled: !value && dq.trim().length >= 2,
  })
  if (value) {
    return (
      <div className="flex items-center justify-between rounded-md border border-line bg-surface-2 px-3 py-2 text-[13.5px]">
        <span>{value.name}{value.city ? `, ${value.city}` : ""}</span>
        <button type="button" onClick={() => onChange(null)} className="text-[12px] text-teal-600 hover:underline">
          Change
        </button>
      </div>
    )
  }
  return (
    <div className="relative">
      <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder ?? "Search by name, GSTIN or city"} />
      {data && data.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full rounded-md border border-line bg-surface-1 shadow-md max-h-60 overflow-auto">
          {data.map((a) => (
            <li key={a.id}>
              <button type="button" onClick={() => { onChange(a); setQ("") }}
                className="w-full text-left px-3 py-2 text-[13px] hover:bg-teal-100">
                {a.name}
                <span className="text-ink-300">{a.city ? ` · ${a.city}` : ""}{a.gstin ? ` · ${a.gstin}` : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
