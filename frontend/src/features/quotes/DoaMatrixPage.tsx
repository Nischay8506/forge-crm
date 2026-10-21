import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Field, Input } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDate, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { matrixBody, whenRow } from "./doaLabels"

type RoleOut = { id: string; name: string; people: number }
type RowOut = {
  when: { discount_pct_from?: string; margin_pct_upto?: string; below_floor?: boolean; value_from?: string }
  approvers: string[]
  approver_roles?: RoleOut[]
  separate_stages?: boolean
  note?: string | null
}
type VersionOut = {
  id: string
  effective_from: string
  matrix: { rows: RowOut[] }
  note: string | null
  created_by: string
  created_at: string
}
type MatrixOut = {
  at: string
  in_force: VersionOut | null
  versions: VersionOut[]
  roles: RoleOut[]
  sample: { rows: { when: RowOut["when"]; approver_names: string[]; note?: string }[] }
  clock: { sla_hours: string; reminder_at: string; target_median_hours: string }
}
type TryOut = {
  needed: boolean
  below_floor: boolean
  stages: { role: string; role_name: string; rows: number[]; because: string[] }[]
  rows: number[]
  problems: string[]
}

type Row = {
  discount_pct_from: string
  margin_pct_upto: string
  below_floor: boolean
  value_from: string
  approvers: string[]
  separate_stages: boolean
  note: string
}

const BLANK: Row = {
  discount_pct_from: "", margin_pct_upto: "", below_floor: false, value_from: "",
  approvers: [""], separate_stages: false, note: "",
}

function formRows(v: VersionOut | null): Row[] {
  if (!v || v.matrix.rows.length === 0) return [{ ...BLANK }]
  return v.matrix.rows.map((r) => ({
    discount_pct_from: r.when.discount_pct_from ?? "",
    margin_pct_upto: r.when.margin_pct_upto ?? "",
    below_floor: Boolean(r.when.below_floor),
    value_from: r.when.value_from ?? "",
    approvers: r.approvers.length > 0 ? [...r.approvers] : [""],
    separate_stages: Boolean(r.separate_stages),
    note: r.note ?? "",
  }))
}

/** Who has to approve a discount, and in what order (F3.4, Z1-Z10 A). A company writes
 *  its delegation of authority as rows; a quote keeps the rulebook of its own day, so a
 *  version is never edited and saving writes a new one from a date. */
export function DoaMatrixPage() {
  const qc = useQueryClient()
  const today = new Date().toISOString().slice(0, 10)
  const mayManage = hasPermission(usePermissions(), "approvals.manage")
  const q = useQuery({
    queryKey: ["doa", "matrix"],
    queryFn: async () => (await api.get<MatrixOut>("/doa/matrix")).data,
  })
  const [rows, setRows] = useState<Row[] | null>(null)
  const [from, setFrom] = useState(today)
  const [note, setNote] = useState("")
  const [warnings, setWarnings] = useState<string[] | null>(null)

  const save = useMutation({
    mutationFn: async () =>
      (await api.put<{ warnings: string[] }>("/doa/matrix", {
        rows: matrixBody(rows ?? []), effective_from: from, note: note || null,
      })).data,
    onSuccess: (out) => {
      setWarnings(out.warnings)
      setRows(null)
      qc.invalidateQueries({ queryKey: ["doa", "matrix"] })
    },
  })

  if (q.isLoading) return <Loading />
  if (q.error || !q.data) return <ErrorText error={q.error} fallback="Could not load the approval rulebook" />
  const d = q.data
  const edit = rows ?? formRows(d.in_force)
  const set = (i: number, patch: Partial<Row>) =>
    setRows(edit.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const problems = save.error ? errorProblems(save.error) : []

  return (
    <div className="space-y-3">
      <p className="text-[13px] text-ink-500 max-w-2xl">
        Who has to approve a discount, and in what order. Every row whose conditions all hold
        applies, and the rows combine: adding a row can only add approval, never take it away.
        A version is never edited, so a quote approved last month can still be explained with
        last month's rulebook. Each approval is given {d.clock.sla_hours} working hours, with a
        reminder halfway.
      </p>

      <Card className="p-4 space-y-2 text-[13px]">
        <div className="flex items-center justify-between">
          <p className="font-semibold text-ink-900">In force today</p>
          {d.in_force && <Tag>From {formatDate(d.in_force.effective_from)}</Tag>}
        </div>
        {d.in_force ? (
          <ol className="space-y-1">
            {d.in_force.matrix.rows.map((r, i) => (
              <li key={i} className="border-b border-line last:border-0 py-1.5">
                <span className="text-ink-900">{whenRow(r.when)}</span>
                <span className="text-ink-700">
                  {" → "}
                  {(r.approver_roles ?? []).map((a) => a.name).join(", then ")}
                  {r.separate_stages ? " (each as its own stage)" : ""}
                </span>
                {r.note && <span className="block text-[12px] text-ink-300">{r.note}</span>}
              </li>
            ))}
          </ol>
        ) : (
          <Empty>
            No rulebook yet. Until one is written, a discount cannot be judged: a quote that
            needs approving goes to a person to decide instead of going out.
          </Empty>
        )}
      </Card>

      <TryIt hasMatrix={Boolean(d.in_force)} />

      <Card className="p-4 space-y-3 text-[13px]">
        <p className="font-semibold text-ink-900">
          {d.in_force ? "Write a new version" : "Write the rulebook"}
        </p>
        {edit.map((r, i) => (
          <div key={i} className="border border-line rounded-md p-3 space-y-2">
            <div className="flex items-center justify-between">
              <p className="font-medium text-ink-900">Row {i + 1}</p>
              {mayManage && edit.length > 1 && (
                <button type="button" className="text-[12px] text-red-500 hover:underline"
                  onClick={() => setRows(edit.filter((_, j) => j !== i))}>Remove</button>
              )}
            </div>
            <p className="text-ink-500 text-[12.5px]">When all of these hold:</p>
            <div className="grid gap-2 sm:grid-cols-3">
              <Field label="Discount from %">
                <Input value={r.discount_pct_from} inputMode="decimal" disabled={!mayManage}
                  onChange={(e) => set(i, { discount_pct_from: e.target.value })} />
              </Field>
              <Field label="Margin up to %">
                <Input value={r.margin_pct_upto} inputMode="decimal" disabled={!mayManage}
                  onChange={(e) => set(i, { margin_pct_upto: e.target.value })} />
              </Field>
              <Field label="Quote worth from ₹">
                <Input value={r.value_from} inputMode="decimal" disabled={!mayManage}
                  onChange={(e) => set(i, { value_from: e.target.value })} />
              </Field>
            </div>
            <label className="flex items-center gap-2 text-ink-700">
              <input type="checkbox" checked={r.below_floor} disabled={!mayManage}
                onChange={(e) => set(i, { below_floor: e.target.checked })} />
              Only when the quote is under its floor
            </label>
            <p className="text-ink-500 text-[12.5px]">Then these roles approve, in this order:</p>
            {r.approvers.map((a, k) => (
              <div key={k} className="flex items-center gap-2">
                <select className="h-9 w-full rounded-md border border-line bg-surface-0 px-2 text-[13px]"
                  value={a} disabled={!mayManage}
                  onChange={(e) => set(i, { approvers: r.approvers.map((x, m) => (m === k ? e.target.value : x)) })}>
                  <option value="">Pick a role</option>
                  {d.roles.map((role) => (
                    <option key={role.id} value={role.id}>
                      {role.name}{role.people === 0 ? " (nobody in it yet)" : ""}
                    </option>
                  ))}
                </select>
                {mayManage && r.approvers.length > 1 && (
                  <button type="button" className="text-[12px] text-red-500 hover:underline whitespace-nowrap"
                    onClick={() => set(i, { approvers: r.approvers.filter((_, m) => m !== k) })}>Remove</button>
                )}
              </div>
            ))}
            {mayManage && (
              <Button size="sm" variant="ghost"
                onClick={() => set(i, { approvers: [...r.approvers, ""] })}>+ Add a role</Button>
            )}
            <label className="flex items-center gap-2 text-ink-700">
              <input type="checkbox" checked={r.separate_stages} disabled={!mayManage}
                onChange={(e) => set(i, { separate_stages: e.target.checked })} />
              Keep each of these as its own stage, even where another row asks for the same role
            </label>
            <Field label="Why this row exists">
              <Input value={r.note} maxLength={300} disabled={!mayManage}
                onChange={(e) => set(i, { note: e.target.value })} />
            </Field>
          </div>
        ))}
        {mayManage && (
          <Button size="sm" variant="ghost" onClick={() => setRows([...edit, { ...BLANK }])}>
            + Add a row
          </Button>
        )}
        <div className="grid gap-2 sm:grid-cols-[180px_1fr]">
          <Field label="From" hint="The date this rulebook applies from">
            <Input type="date" value={from} disabled={!mayManage}
              onChange={(e) => setFrom(e.target.value)} />
          </Field>
          <Field label="Note">
            <Input value={note} maxLength={300} disabled={!mayManage}
              onChange={(e) => setNote(e.target.value)} />
          </Field>
        </div>
        {problems.length > 0 && (
          <ul role="alert" className="list-disc pl-5 text-red-500">{problems.map((t) => <li key={t}>{t}</li>)}</ul>
        )}
        {warnings && (
          <div role="status" className="space-y-0.5">
            <p className="text-teal-600">Saved as a new version.</p>
            {warnings.map((w) => <p key={w} className="text-amber-600">{w}</p>)}
          </div>
        )}
        {mayManage && (
          <div className="flex gap-2">
            <Button size="sm" disabled={save.isPending}
              onClick={() => { setWarnings(null); save.mutate() }}>Save this version</Button>
            <Button size="sm" variant="secondary" disabled={save.isPending}
              onClick={() => setRows(d.sample.rows.map((r) => ({
                ...BLANK,
                discount_pct_from: r.when.discount_pct_from ?? "",
                margin_pct_upto: r.when.margin_pct_upto ?? "",
                below_floor: Boolean(r.when.below_floor),
                value_from: r.when.value_from ?? "",
                // The sample names roles in words; each name that is a role here is filled
                // in, and one that is not is left for a person to pick.
                approvers: r.approver_names.map(
                  (n) => d.roles.find((role) => role.name.toLowerCase() === n.toLowerCase())?.id ?? ""),
                note: r.note ?? "",
              })))}>
              Start from the sample
            </Button>
          </div>
        )}
      </Card>

      <Card className="p-4 space-y-1 text-[13px]">
        <p className="font-semibold text-ink-900">Versions</p>
        {d.versions.length === 0 && <Empty>No version yet.</Empty>}
        {d.versions.map((v) => (
          <p key={v.id} className="text-ink-700 border-b border-line last:border-0 py-1">
            From {formatDate(v.effective_from)} · {v.matrix.rows.length} {v.matrix.rows.length === 1 ? "row" : "rows"}
            {v.note ? ` · ${v.note}` : ""}
            <span className="block text-[12px] text-ink-300">{v.created_by} · {formatDateTime(v.created_at)}</span>
          </p>
        ))}
      </Card>
    </div>
  )
}

/** A quote's numbers tried against the rulebook, before a real quote meets it. */
function TryIt({ hasMatrix }: { hasMatrix: boolean }) {
  const [form, setForm] = useState({ discount_pct: "10", value: "500000", margin_pct: "", floor_margin_pct: "" })
  const run = useMutation({
    mutationFn: async () => (await api.post<TryOut>("/doa/matrix/try", {
      ...form,
      margin_pct: form.margin_pct || null,
      floor_margin_pct: form.floor_margin_pct || null,
    })).data,
  })
  return (
    <Card className="p-4 space-y-2 text-[13px]">
      <p className="font-semibold text-ink-900">Try it</p>
      <p className="text-ink-500 text-[12.5px]">
        What a quote with these numbers would need today. Nothing is saved.
      </p>
      <div className="grid gap-2 sm:grid-cols-4">
        <Field label="Discount %">
          <Input value={form.discount_pct} inputMode="decimal"
            onChange={(e) => setForm({ ...form, discount_pct: e.target.value })} />
        </Field>
        <Field label="Quote worth ₹">
          <Input value={form.value} inputMode="decimal"
            onChange={(e) => setForm({ ...form, value: e.target.value })} />
        </Field>
        <Field label="Margin %" hint="Leave empty if it is not known">
          <Input value={form.margin_pct} inputMode="decimal"
            onChange={(e) => setForm({ ...form, margin_pct: e.target.value })} />
        </Field>
        <Field label="Floor %">
          <Input value={form.floor_margin_pct} inputMode="decimal"
            onChange={(e) => setForm({ ...form, floor_margin_pct: e.target.value })} />
        </Field>
      </div>
      <Button size="sm" variant="secondary" disabled={run.isPending || !hasMatrix}
        onClick={() => run.mutate()}>See what it needs</Button>
      {!hasMatrix && <p className="text-ink-300 text-[12.5px]">Write a rulebook first.</p>}
      {run.data && (
        <div role="status" className="space-y-0.5 pt-1">
          {run.data.stages.length === 0
            ? <p className="text-ink-700">No approval needed: no row applies to this quote.</p>
            : (
              <>
                <p className="text-ink-900">
                  {run.data.stages.length} {run.data.stages.length === 1 ? "approval" : "approvals"}, in this order:
                </p>
                <ol className="list-decimal pl-5 text-ink-700">
                  {run.data.stages.map((s, i) => (
                    <li key={i}>{s.role_name} — {s.because.join("; ")}</li>
                  ))}
                </ol>
              </>
            )}
          {run.data.problems.map((p) => <p key={p} className="text-amber-600">{p}</p>)}
        </div>
      )}
      {run.error && <ErrorText error={run.error} fallback="Could not try the rulebook" />}
    </Card>
  )
}
