import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDate } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { useFamilies } from "./hooks"
import { LANGUAGES } from "./labels"
import type { Customer, Details, EnquiryDetail } from "./types"

const CUSTOMER_FIELDS: { key: keyof Customer; label: string }[] = [
  { key: "company", label: "Company" },
  { key: "contact_name", label: "Contact" },
  { key: "designation", label: "Designation" },
  { key: "phone", label: "Phone" },
  { key: "email", label: "Email" },
  { key: "city", label: "City" },
  { key: "state", label: "State" },
]

const DETAIL_FIELDS: { key: keyof Details; label: string; hint?: string }[] = [
  { key: "delivery_location", label: "Delivery location" },
  { key: "target_date", label: "Needed by (date)", hint: "YYYY-MM-DD" },
  { key: "target_date_text", label: "Needed by (as written)" },
  { key: "special_requirements", label: "Special requirements" },
  { key: "tender_ref", label: "Tender reference" },
]

/** Who it is from and what else the customer said, with the CRM records it is linked to. */
export function CustomerCard({ e }: { e: EnquiryDetail }) {
  const qc = useQueryClient()
  const canWriteAccounts = hasPermission(usePermissions(), "accounts.write")
  const families = useFamilies()
  const [editing, setEditing] = useState(false)
  const [c, setC] = useState<Customer>(e.customer)
  const [d, setD] = useState<Details>(e.details)
  const [summary, setSummary] = useState(e.summary ?? "")
  const [tender, setTender] = useState(e.is_tender)
  const [family, setFamily] = useState(e.product_family?.id ?? "")
  const [language, setLanguage] = useState(e.language ?? "")
  const may = e.may.work && !["closed", "merged"].includes(e.status)
  const done = (data: EnquiryDetail) => {
    qc.setQueryData(["enquiry", e.id], data)
    qc.invalidateQueries({ queryKey: ["enquiries"] })
    setEditing(false)
  }
  const save = useMutation({
    mutationFn: async () => (await api.patch<EnquiryDetail>(`/enquiries/${e.id}`, {
      summary, customer: c, details: d, is_tender: tender,
      ...(language !== (e.language ?? "") ? { language } : {}),
      ...(family ? { product_family_id: family } : { clear_product_family: !!e.product_family }),
    })).data,
    onSuccess: done,
  })
  const addContact = useMutation({
    mutationFn: async () => (await api.post<EnquiryDetail>(`/enquiries/${e.id}/add-customer`)).data,
    onSuccess: done,
  })
  const startEdit = () => {
    setC(e.customer); setD(e.details); setSummary(e.summary ?? ""); setTender(e.is_tender)
    setFamily(e.product_family?.id ?? ""); setLanguage(e.language ?? ""); setEditing(true)
  }

  if (editing) {
    return (
      <Card>
        <CardHeader className="pb-2"><CardTitle>Customer and details</CardTitle></CardHeader>
        <div className="px-4 pb-4 space-y-3">
          <Field label="Summary"><Input value={summary} onChange={(ev) => setSummary(ev.target.value)} maxLength={300} /></Field>
          <div className="grid gap-2 sm:grid-cols-2">
            {CUSTOMER_FIELDS.map((f) => (
              <Field key={f.key} label={f.label}>
                <Input value={c[f.key] ?? ""} onChange={(ev) => setC({ ...c, [f.key]: ev.target.value })} />
              </Field>
            ))}
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {DETAIL_FIELDS.map((f) => (
              <Field key={f.key} label={f.label} hint={f.hint}>
                <Input value={d[f.key] ?? ""} onChange={(ev) => setD({ ...d, [f.key]: ev.target.value })} />
              </Field>
            ))}
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="Product family" hint="Its checklist decides what the acknowledgement asks">
              <Select value={family} onChange={(ev) => setFamily(ev.target.value)}>
                <option value="">General (no family)</option>
                {families.data?.families.filter((f) => f.active || f.id === family).map((f) => (
                  <option key={f.id} value={f.id}>{f.name}</option>
                ))}
              </Select>
            </Field>
            <Field label="Language for replies">
              <Select value={language} onChange={(ev) => setLanguage(ev.target.value)}>
                <option value="">As the customer wrote</option>
                {Object.entries(LANGUAGES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </Field>
          </div>
          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" checked={tender} onChange={(ev) => setTender(ev.target.checked)} />
            This is a tender (the acknowledgement is drafted for a person)
          </label>
          {save.error && <p className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not save")}</p>}
          <div className="flex gap-2">
            <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
            <Button size="sm" variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </div>
      </Card>
    )
  }

  const rows = CUSTOMER_FIELDS.filter((f) => e.customer[f.key])
  const details = DETAIL_FIELDS.filter((f) => e.details[f.key])
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle>Customer</CardTitle>
        {may && <Button size="sm" variant="ghost" onClick={startEdit}>Edit</Button>}
      </CardHeader>
      <div className="px-4 pb-4 space-y-3 text-[13px]">
        <div className="space-y-1">
          {e.account ? (
            <div>
              <Link to={`/accounts/${e.account.id}`} className="font-semibold text-teal-600 hover:underline">{e.account.name}</Link>
              {e.account.tier === "strategic" && <Tag variant="gold" className="ml-1.5">Strategic</Tag>}
            </div>
          ) : <div className="text-ink-500">Not linked to an account</div>}
          {e.contact ? (
            <div className="text-ink-700">{e.contact.full_name}{e.contact.designation ? `, ${e.contact.designation}` : ""}
              <span className="text-ink-300">{e.contact.email ? ` · ${e.contact.email}` : ""}{e.contact.phone ? ` · ${e.contact.phone}` : ""}</span>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-ink-500">The sender is not a contact yet.</span>
              {may && canWriteAccounts && (
                <Button size="sm" variant="secondary" onClick={() => addContact.mutate()} disabled={addContact.isPending}>
                  {addContact.isPending ? "Adding…" : "Add to contacts"}
                </Button>
              )}
            </div>
          )}
          {addContact.error && <p className="text-red-500 text-[12.5px]">{errorMessage(addContact.error, "Could not add the contact")}</p>}
        </div>
        {rows.length > 0 && (
          <dl className="grid grid-cols-[110px_1fr] gap-x-2 gap-y-1">
            <dt className="col-span-2 text-[11.5px] font-semibold text-ink-300 uppercase tracking-wide">As read from the email</dt>
            {rows.map((f) => (
              <div key={f.key} className="contents">
                <dt className="text-ink-500">{f.label}</dt><dd className="text-ink-900 break-words">{e.customer[f.key]}</dd>
              </div>
            ))}
          </dl>
        )}
        <dl className="grid grid-cols-[110px_1fr] gap-x-2 gap-y-1">
          <dt className="col-span-2 text-[11.5px] font-semibold text-ink-300 uppercase tracking-wide">Details</dt>
          {details.map((f) => (
            <div key={f.key} className="contents">
              <dt className="text-ink-500">{f.label}</dt>
              <dd className="text-ink-900 break-words">{f.key === "target_date" ? formatDate(e.details.target_date) : e.details[f.key]}</dd>
            </div>
          ))}
          <dt className="text-ink-500">Product family</dt><dd>{e.product_family?.name ?? "General"}</dd>
          <dt className="text-ink-500">Language</dt><dd>{LANGUAGES[e.language ?? ""] ?? e.language ?? "English"}</dd>
        </dl>
      </div>
    </Card>
  )
}
