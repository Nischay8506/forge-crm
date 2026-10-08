import { useState } from "react"
import { Link } from "react-router-dom"
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input } from "@/components/ui/Field"
import { ScoreBadge } from "@/components/ui/ScoreBadge"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { hasPermission } from "@/lib/permissions"
import { useDebounced } from "@/lib/useDebounced"
import { usePermissions } from "@/stores/authStore"
import type { AccountSummary, ContactSummary } from "@/features/steward/types"
import { AccountPicker } from "./AccountPicker"

const PAGE = 50
const EMPTY = { full_name: "", email: "", phone: "", designation: "", department: "" }

export function ContactsPage() {
  const canWrite = hasPermission(usePermissions(), "accounts.write")
  const qc = useQueryClient()
  const [q, setQ] = useState("")
  const [limit, setLimit] = useState(PAGE)
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState(EMPTY)
  const [company, setCompany] = useState<AccountSummary | null>(null)
  const dq = useDebounced(q)
  const { data, isLoading, error } = useQuery({
    queryKey: ["contacts", dq, limit],
    queryFn: async () => (await api.get<(ContactSummary & { account_name: string | null })[]>("/contacts", {
      params: { q: dq || undefined, limit } })).data,
    placeholderData: keepPreviousData,
  })
  const add = useMutation({
    mutationFn: async () => {
      const body: Record<string, string> = {}
      for (const [k, v] of Object.entries(form)) if (v.trim()) body[k] = v.trim()
      if (company) body.account_id = company.id
      return (await api.post("/contacts", body)).data
    },
    onSuccess: () => {
      setForm(EMPTY)
      setCompany(null)
      setOpen(false)
      qc.invalidateQueries({ queryKey: ["contacts"] })
    },
  })
  const problems = errorProblems(add.error)
  const set = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.value })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-ink-900">Contacts</h1>
        {canWrite && !open && <Button onClick={() => setOpen(true)}>New contact</Button>}
      </div>
      {open && (
        <Card>
          <CardHeader><CardTitle>New contact</CardTitle></CardHeader>
          <CardContent>
            <form className="grid grid-cols-2 gap-3" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
              <Field label="Full name *"><Input required value={form.full_name} onChange={set("full_name")} /></Field>
              <Field label="Company" group><AccountPicker value={company} onChange={setCompany} /></Field>
              <Field label="Email"><Input value={form.email} onChange={set("email")} /></Field>
              <Field label="Phone"><Input value={form.phone} onChange={set("phone")} /></Field>
              <Field label="Designation"><Input value={form.designation} onChange={set("designation")} /></Field>
              <Field label="Department"><Input value={form.department} onChange={set("department")} /></Field>
              {add.error && (
                <div className="col-span-2 bg-red-100 text-red-500 text-[13px] p-3 rounded-md">
                  {problems.length ? problems.map((p) => <p key={p}>{p}</p>) : errorMessage(add.error, "Could not save")}
                </div>
              )}
              <div className="col-span-2 flex gap-2">
                <Button type="submit" disabled={add.isPending}>Save contact</Button>
                <Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}
      <Input value={q} onChange={(e) => { setQ(e.target.value); setLimit(PAGE) }}
        placeholder="Search by name or email" className="max-w-md" />
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load contacts" />
      {data && (
        <Card>
          {data.length === 0 ? <Empty>No contacts found.</Empty> : (
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-line">
                  <th className="p-3 font-semibold">Name</th>
                  <th className="p-3 font-semibold">Designation</th>
                  <th className="p-3 font-semibold">Email</th>
                  <th className="p-3 font-semibold">Phone</th>
                  <th className="p-3 font-semibold">Company</th>
                  <th className="p-3 font-semibold text-right">Hygiene</th>
                </tr>
              </thead>
              <tbody>
                {data.map((c) => (
                  <tr key={c.id} className="border-b border-line last:border-0">
                    <td className="p-3 font-medium">{c.full_name}</td>
                    <td className="p-3 text-ink-500">{c.designation ?? "–"}</td>
                    <td className="p-3">{c.email ?? "–"}</td>
                    <td className="p-3">{c.phone ?? "–"}</td>
                    <td className="p-3">
                      {c.account_id ? <Link to={`/accounts/${c.account_id}`} className="text-teal-600 hover:underline">{c.account_name ?? "Open"}</Link> : <span className="text-ink-300">–</span>}
                    </td>
                    <td className="p-3 text-right"><ScoreBadge score={c.hygiene_score} /></td>
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
