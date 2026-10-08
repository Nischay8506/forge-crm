import { useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import type { ImportBatch, RecordType } from "./types"

const COLUMNS: Record<RecordType, string> = {
  account: "Name (or Company / Customer / Party name), Legal name, GSTIN, PAN, CIN, Website, Email, Phone, Address, Address 2, City, State, Pincode, Industry",
  contact: "Name (or Contact person), Email, Phone, Designation, Department, Company, Company GSTIN",
}

const STATUS_TONE = { processing: "teal", done: "green", failed: "red" } as const

export function ImportsPage() {
  const canImport = hasPermission(usePermissions(), "steward.import")
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [type, setType] = useState<RecordType>("account")
  const [file, setFile] = useState<File | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const { data, isLoading, error } = useQuery({
    queryKey: ["imports"],
    queryFn: async () => (await api.get<ImportBatch[]>("/steward/imports")).data,
    refetchInterval: (q) => (q.state.data?.some((b) => b.status === "processing") ? 2000 : false),
  })
  const upload = useMutation({
    mutationFn: async () => {
      const form = new FormData()
      form.append("record_type", type)
      form.append("file", file!)
      return (await api.post<ImportBatch>("/steward/imports", form, {
        headers: { "Content-Type": "multipart/form-data" } })).data
    },
    onSuccess: (b) => {
      setFile(null)
      if (fileRef.current) fileRef.current.value = ""
      setOpenId(b.id)
      qc.invalidateQueries({ queryKey: ["imports"] })
    },
  })

  return (
    <div className="space-y-4">
      {canImport && (
        <Card>
          <CardHeader><CardTitle>Import from the old system</CardTitle></CardHeader>
          <CardContent>
            <form className="grid grid-cols-[200px_1fr_auto] gap-3 items-end"
              onSubmit={(e) => { e.preventDefault(); if (file) upload.mutate() }}>
              <Field label="Records">
                <Select value={type} onChange={(e) => setType(e.target.value as RecordType)}>
                  <option value="account">Accounts (companies)</option>
                  <option value="contact">Contacts</option>
                </Select>
              </Field>
              <Field label="CSV file">
                <Input ref={fileRef} type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </Field>
              <Button type="submit" disabled={!file || upload.isPending}>{upload.isPending ? "Uploading…" : "Import"}</Button>
            </form>
            <p className="text-[12px] text-ink-500 mt-3">
              Columns recognised: {COLUMNS[type]}. Rows with problems are still loaded when they have a
              name, and get a lower hygiene score. The Data Steward then merges exact duplicates and
              queues likely ones for review.
            </p>
            <ErrorText error={upload.error} fallback="Could not start the import" />
          </CardContent>
        </Card>
      )}
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load imports" />
      {data && (
        <Card>
          {data.length === 0 ? <Empty>No imports yet.</Empty> : (
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-line">
                  <th className="p-3 font-semibold">File</th>
                  <th className="p-3 font-semibold">Status</th>
                  <th className="p-3 font-semibold text-right">Rows</th>
                  <th className="p-3 font-semibold text-right">Created</th>
                  <th className="p-3 font-semibold text-right">Auto-merged</th>
                  <th className="p-3 font-semibold text-right">To review</th>
                  <th className="p-3 font-semibold text-right">Rejected</th>
                  <th className="p-3 font-semibold">Started</th>
                </tr>
              </thead>
              <tbody>
                {data.map((b) => (
                  <ImportRow key={b.id} b={b} open={openId === b.id} onToggle={() => setOpenId(openId === b.id ? null : b.id)} />
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </div>
  )
}

function ImportRow({ b, open, onToggle }: { b: ImportBatch; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="border-b border-line hover:bg-surface-0 cursor-pointer" onClick={onToggle}>
        <td className="p-3 font-medium">{b.filename} <span className="text-ink-300 text-[11.5px]">{b.record_type}s</span></td>
        <td className="p-3"><Tag variant={STATUS_TONE[b.status]}>{b.status}</Tag></td>
        <td className="p-3 text-right font-mono">{b.total_rows}</td>
        <td className="p-3 text-right font-mono">{b.created_count}</td>
        <td className="p-3 text-right font-mono">{b.auto_merged_count}</td>
        <td className="p-3 text-right font-mono">{b.review_count}</td>
        <td className="p-3 text-right font-mono">{b.error_count}</td>
        <td className="p-3 text-ink-500 whitespace-nowrap">{formatDateTime(b.created_at)}</td>
      </tr>
      {open && b.errors.length > 0 && (
        <tr className="border-b border-line bg-surface-0">
          <td colSpan={8} className="p-3">
            <ul className="space-y-1 text-[12.5px] max-h-72 overflow-auto">
              {b.errors.map((e, i) => (
                <li key={i}>
                  <span className="font-mono text-ink-500">{e.row ? `Row ${e.row}` : "File"}</span>{" "}
                  <span className={e.kept ? "text-amber-500" : "text-red-500"}>
                    {e.kept ? "loaded with problems: " : "not loaded: "}{e.problems.join("; ")}
                  </span>
                </li>
              ))}
            </ul>
          </td>
        </tr>
      )}
    </>
  )
}
