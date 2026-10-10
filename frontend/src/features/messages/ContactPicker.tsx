import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Input } from "@/components/ui/Field"
import { useDebounced } from "@/lib/useDebounced"
import type { ContactSummary } from "@/features/steward/types"

export type PickedContact = Pick<ContactSummary, "id" | "full_name" | "email"> & { account_name?: string | null }

/** Search-and-pick an active contact by name or email; shows the picked one with a way to change it. */
export function ContactPicker({ value, onChange, placeholder }: {
  value: PickedContact | null
  onChange: (c: PickedContact | null) => void
  placeholder?: string
}) {
  const [q, setQ] = useState("")
  const dq = useDebounced(q)
  const { data } = useQuery({
    queryKey: ["contacts", "pick", dq],
    queryFn: async () => (await api.get<PickedContact[]>("/contacts", { params: { q: dq, limit: 8 } })).data,
    enabled: !value && dq.trim().length >= 2,
  })
  if (value) {
    return (
      <div className="flex items-center justify-between gap-2 rounded-md border border-line bg-surface-2 px-3 py-2 text-[13.5px]">
        <span className="min-w-0 truncate">
          {value.full_name}
          <span className="text-ink-300">{value.email ? ` · ${value.email}` : " · no email address"}{value.account_name ? ` · ${value.account_name}` : ""}</span>
        </span>
        <button type="button" onClick={() => onChange(null)} className="text-[12px] text-teal-600 hover:underline shrink-0">
          Change
        </button>
      </div>
    )
  }
  return (
    <div className="relative">
      <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder ?? "Search contacts by name or email"} />
      {data && data.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full rounded-md border border-line bg-surface-1 shadow-md max-h-60 overflow-auto">
          {data.map((c) => (
            <li key={c.id}>
              <button type="button" onClick={() => { onChange(c); setQ("") }}
                className="w-full text-left px-3 py-2 text-[13px] hover:bg-teal-100">
                {c.full_name}
                <span className="text-ink-300">{c.email ? ` · ${c.email}` : " · no email"}{c.account_name ? ` · ${c.account_name}` : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {data && data.length === 0 && dq.trim().length >= 2 && (
        <p className="absolute z-10 mt-1 w-full rounded-md border border-line bg-surface-1 shadow-md px-3 py-2 text-[12.5px] text-ink-300">No contact found.</p>
      )}
    </div>
  )
}
