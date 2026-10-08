import { useEffect, useMemo, useState } from "react"
import { useNavigate, useParams, useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { ErrorText, Loading } from "@/components/ui/States"
import { useDebounced } from "@/lib/useDebounced"
import { AccountPicker } from "./AccountPicker"
import { DuplicatePanel } from "./DuplicatePanel"
import { SITE_TYPE_LABELS } from "@/features/steward/labels"
import type { Account, AccountDetail, AccountSummary, DuplicateCheck, Kind } from "@/features/steward/types"

const TEXT_FIELDS = [
  ["name", "Name *"], ["legal_name", "Legal name"], ["gstin", "GSTIN"], ["pan", "PAN"],
  ["cin", "CIN"], ["website", "Website"], ["email", "Email"], ["phone", "Phone"],
  ["address_line1", "Address"], ["address_line2", "Address line 2"], ["city", "City"],
  ["state", "State"], ["pincode", "Pincode"], ["industry", "Industry"],
] as const
type TextField = (typeof TEXT_FIELDS)[number][0]
type Values = Record<TextField, string>

const HINTS: Partial<Record<TextField, string>> = {
  gstin: "15 characters. PAN and state are filled from it.",
  pan: "Filled from the GSTIN when left empty.",
}

function emptyValues(): Values {
  return Object.fromEntries(TEXT_FIELDS.map(([f]) => [f, ""])) as Values
}

function valuesOf(a: Account): Values {
  return Object.fromEntries(TEXT_FIELDS.map(([f]) => [f, (a[f] as string | null) ?? ""])) as Values
}

/** Create (/accounts/new) or edit (/accounts/:id/edit) an account, checking for
 *  duplicates and invalid IDs while the person types. */
export function AccountFormPage() {
  const { id } = useParams()
  const [search] = useSearchParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const editing = !!id

  const existing = useQuery({
    queryKey: ["account", id],
    queryFn: async () => (await api.get<AccountDetail>(`/accounts/${id}`)).data,
    enabled: editing,
  })
  const parentParam = search.get("parent")
  const parentQuery = useQuery({
    queryKey: ["account", parentParam],
    queryFn: async () => (await api.get<AccountDetail>(`/accounts/${parentParam}`)).data,
    enabled: !editing && !!parentParam,
  })

  const [kind, setKind] = useState<Kind>((search.get("kind") as Kind) || "entity")
  const [siteType, setSiteType] = useState("plant")
  const [parent, setParent] = useState<AccountSummary | null>(null)
  const [values, setValues] = useState<Values>(emptyValues)
  const [initial, setInitial] = useState<Values>(emptyValues)
  const [problems, setProblems] = useState<string[]>([])

  useEffect(() => {
    const a = existing.data?.account
    if (!a) return
    setKind(a.kind)
    setSiteType(a.site_type ?? "plant")
    setValues(valuesOf(a))
    setInitial(valuesOf(a))
  }, [existing.data])

  const parentId = existing.data?.account.parent_id
  const currentParent = useQuery({
    queryKey: ["account", parentId],
    queryFn: async () => (await api.get<AccountDetail>(`/accounts/${parentId}`)).data,
    enabled: !!parentId,
  })
  useEffect(() => {
    const p = currentParent.data?.account ?? parentQuery.data?.account
    if (p) setParent(p)
  }, [currentParent.data, parentQuery.data])

  const draft = useMemo(() => {
    const body: Record<string, string> = { kind }
    for (const [f] of TEXT_FIELDS) if (values[f].trim()) body[f] = values[f].trim()
    return body
  }, [values, kind])
  const debouncedDraft = useDebounced(draft, 500)
  const check = useQuery({
    queryKey: ["accounts", "check", debouncedDraft],
    queryFn: async () => (await api.post<DuplicateCheck>("/accounts/check", debouncedDraft)).data,
    enabled: !!debouncedDraft.name,
  })

  const save = useMutation({
    mutationFn: async () => {
      const body: Record<string, string | null> = {}
      for (const [f] of TEXT_FIELDS) {
        const v = values[f].trim()
        if (!editing ? v : v !== initial[f].trim()) body[f] = v || null
      }
      const parentChanged = (parent?.id ?? null) !== (existing.data?.account.parent_id ?? null)
      if (!editing || parentChanged) body.parent_id = parent?.id ?? null
      if (!editing) body.kind = kind
      if (kind === "site") body.site_type = siteType
      const r = editing
        ? await api.patch<Account>(`/accounts/${id}`, body)
        : await api.post<Account>("/accounts", body)
      return r.data
    },
    onSuccess: (a) => {
      qc.invalidateQueries({ queryKey: ["accounts"] })
      qc.invalidateQueries({ queryKey: ["account", a.id] })
      navigate(`/accounts/${a.id}`)
    },
    onError: (err) => setProblems(errorProblems(err)),
  })

  if (editing && existing.isLoading) return <Loading />
  const set = (f: TextField) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setValues((v) => ({ ...v, [f]: e.target.value }))
  const parentKind: Kind | undefined = kind === "site" ? "entity" : kind === "entity" ? "group" : undefined

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-ink-900">
        {editing ? `Edit ${existing.data?.account.name ?? "account"}` : "New account"}
      </h1>
      <ErrorText error={existing.error} fallback="Could not load the account" />
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-4 items-start">
        <Card>
          <CardContent className="pt-4">
            <form className="grid grid-cols-2 gap-4" onSubmit={(e) => { e.preventDefault(); setProblems([]); save.mutate() }}>
              <Field label="Type">
                <Select value={kind} onChange={(e) => { setKind(e.target.value as Kind); setParent(null) }} disabled={editing}>
                  <option value="group">Group (several companies)</option>
                  <option value="entity">Legal entity (company)</option>
                  <option value="site">Site (plant, ship-to, office…)</option>
                </Select>
              </Field>
              {kind === "site" ? (
                <Field label="Site type">
                  <Select value={siteType} onChange={(e) => setSiteType(e.target.value)}>
                    {Object.entries(SITE_TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </Select>
                </Field>
              ) : <div />}
              {parentKind && (
                <Field label={kind === "site" ? "Company this site belongs to *" : "Group (optional)"} className="col-span-2" group>
                  <AccountPicker value={parent} onChange={setParent} kind={parentKind} />
                </Field>
              )}
              {TEXT_FIELDS.map(([f, label]) => (
                <Field key={f} label={label} hint={HINTS[f]} className={f === "name" || f.startsWith("address") ? "col-span-2" : ""}>
                  <Input value={values[f]} onChange={set(f)} required={f === "name"} />
                </Field>
              ))}
              {(problems.length > 0 || save.error) && (
                <div className="col-span-2 bg-red-100 text-red-500 text-[13px] p-3 rounded-md space-y-1">
                  {problems.length > 0 ? problems.map((p) => <p key={p}>{p}</p>)
                    : <p>{errorMessage(save.error, "Could not save")}</p>}
                </div>
              )}
              <div className="col-span-2 flex gap-2">
                <Button type="submit" disabled={save.isPending || (kind === "site" && !parent)}>
                  {save.isPending ? "Saving…" : editing ? "Save changes" : "Create account"}
                </Button>
                <Button type="button" variant="secondary" onClick={() => navigate(-1)}>Cancel</Button>
              </div>
            </form>
          </CardContent>
        </Card>
        <DuplicatePanel check={check.data} excludeId={id} />
      </div>
    </div>
  )
}
