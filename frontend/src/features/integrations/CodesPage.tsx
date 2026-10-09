import { useState } from "react"
import { useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent } from "@/components/ui/Card"
import { Input, Select } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import { useOverview } from "./hooks"
import { SUGGESTION_SOURCES } from "./labels"
import type { CodeHistory, CodeLists, CodeRow, ForgeCode } from "./types"

type Status = "open" | "confirmed" | "all"

const STATUS_TAGS: Record<string, { label: string; tone: "gold" | "green" | "neutral" }> = {
  suggested: { label: "Suggested", tone: "gold" },
  unmapped: { label: "Not mapped", tone: "neutral" },
  confirmed: { label: "Confirmed", tone: "green" },
}

export function CodesPage() {
  const perms = usePermissions()
  const canMap = hasPermission(perms, "integrations.map")
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const connection = params.get("connection") ?? ""
  const status = (params.get("status") as Status) || "open"
  const overview = useOverview()
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next)
  }
  // All lists (for the counts), then one list's rows.
  const lists = useQuery({
    queryKey: ["integrations", "codes", "lists", connection],
    queryFn: async () => (await api.get<CodeLists["lists"]>("/integrations/codes/lists", {
      params: { connection_id: connection || undefined },
    })).data,
  })
  const list = params.get("list") || lists.data?.find((l) => (l.counts.suggested ?? 0) + (l.counts.unmapped ?? 0) > 0)?.list || "uom"
  const meta = lists.data?.find((l) => l.list === list)
  const rows = useQuery({
    queryKey: ["integrations", "codes", list, connection, status],
    queryFn: async () => (await api.get<CodeLists>("/integrations/codes", {
      params: { connection_id: connection || undefined, status, list },
    })).data.items,
  })
  const forge = useQuery({
    queryKey: ["integrations", "forge-codes", list],
    queryFn: async () => (await api.get<ForgeCode[]>("/integrations/forge-codes", { params: { list } })).data,
  })
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [history, setHistory] = useState<string | null>(null)
  const done = () => qc.invalidateQueries({ queryKey: ["integrations"] })
  const setCode = useMutation({
    mutationFn: async ({ m, code }: { m: CodeRow; code: string | null }) =>
      (await api.put<CodeRow>(`/integrations/codes/${m.id}`, { forge_code: code })).data,
    onSuccess: done,
  })
  const confirm = useMutation({
    mutationFn: async (ids: string[]) => (await api.post<{ confirmed: number }>("/integrations/codes/confirm", { ids })).data,
    onSuccess: () => { setSelected(new Set()); done() },
  })
  const confirmable = (rows.data ?? []).filter((r) => r.status === "suggested" && r.forge_code)
  const toggle = (id: string) => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelected(next)
  }

  return (
    <div className="space-y-4">
      <p className="text-[12.5px] text-ink-500 max-w-[860px]">
        Each ERP code is mapped to one of Forge's codes, so the same unit, tax, group or warehouse means the same thing
        whichever ERP it came from. Units use the GST unit codes (UQC) and tax uses the GST rate slabs; the other lists
        start as a copy of the first ERP's. Every change is a new version, kept with who made it.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Select className="w-[220px]" value={connection} onChange={(e) => setParam("connection", e.target.value)}>
          <option value="">Every connection</option>
          {overview.data?.connections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>
        <Select className="w-[180px]" value={status} onChange={(e) => setParam("status", e.target.value === "open" ? "" : e.target.value)}>
          <option value="open">To confirm</option>
          <option value="confirmed">Confirmed</option>
          <option value="all">All</option>
        </Select>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-[220px_1fr] gap-4 items-start">
        <Card className="p-2">
          {lists.isLoading && <Loading />}
          {lists.data?.map((l) => {
            const open = (l.counts.suggested ?? 0) + (l.counts.unmapped ?? 0)
            return (
              <button key={l.list} onClick={() => { setParam("list", l.list); setSelected(new Set()) }}
                className={cn("w-full flex items-center justify-between rounded-md px-3 py-2 text-[13px] text-left hover:bg-surface-2",
                  list === l.list && "bg-teal-100 text-teal-600 font-semibold")}>
                <span>{l.label}{l.standard && <span className="text-ink-300 font-normal"> · GST</span>}</span>
                {open > 0 && <span className="rounded-full bg-gold-100 text-[#8a6d10] text-[11px] font-bold px-1.5">{open}</span>}
              </button>
            )
          })}
        </Card>

        <Card>
          <CardContent className="pt-4 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[13px] font-semibold text-ink-900">{meta?.label ?? list}</p>
              {canMap && confirmable.length > 0 && (
                <div className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => setSelected(new Set(confirmable.map((r) => r.id)))}>
                    Select every suggestion ({confirmable.length})
                  </Button>
                  <Button size="sm" variant="gold" disabled={selected.size === 0 || confirm.isPending}
                    onClick={() => confirm.mutate([...selected])}>
                    Confirm selected ({selected.size})
                  </Button>
                </div>
              )}
            </div>
            {confirm.data && <p className="text-[12.5px] text-green-500">Confirmed {confirm.data.confirmed}.</p>}
            <ErrorText error={rows.error ?? setCode.error ?? confirm.error} fallback="Could not save that" />
            {rows.isLoading && <Loading />}
            {rows.data && rows.data.length === 0 && <Empty>Nothing here.</Empty>}
            {rows.data && rows.data.length > 0 && (
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left text-ink-500 border-b border-line">
                    {canMap && <th className="py-2 pr-2 w-[28px]" />}
                    <th className="py-2 pr-3 font-semibold">ERP code</th>
                    <th className="py-2 pr-3 font-semibold">Forge code</th>
                    <th className="py-2 pr-3 font-semibold">Suggested by</th>
                    <th className="py-2 pr-3 font-semibold">State</th>
                    <th className="py-2 font-semibold text-right">Version</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.data.map((m) => (
                    <CodeRowView key={m.id} m={m} canMap={canMap} forge={forge.data ?? []} showConn={!connection}
                      checked={selected.has(m.id)} onCheck={() => toggle(m.id)} busy={setCode.isPending}
                      onSet={(code) => setCode.mutate({ m, code })}
                      historyOpen={history === m.id} onHistory={() => setHistory(history === m.id ? null : m.id)} />
                  ))}
                </tbody>
              </table>
            )}
            {canMap && meta && !meta.standard && <AddForgeCode list={list} label={meta.label} />}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function CodeRowView({ m, canMap, forge, showConn, checked, onCheck, busy, onSet, historyOpen, onHistory }: {
  m: CodeRow; canMap: boolean; forge: ForgeCode[]; showConn: boolean; checked: boolean; onCheck: () => void
  busy: boolean; onSet: (code: string | null) => void; historyOpen: boolean; onHistory: () => void
}) {
  const st = STATUS_TAGS[m.status]
  const confidence = m.confidence ? Math.round(Number(m.confidence)) : null
  return (
    <>
      <tr className="border-b border-line last:border-0">
        {canMap && (
          <td className="py-2 pr-2">
            {m.status === "suggested" && m.forge_code && <input type="checkbox" checked={checked} onChange={onCheck} />}
          </td>
        )}
        <td className="py-2 pr-3">
          <span className="font-medium">{m.erp_code}</span>
          {showConn && <span className="text-[11.5px] text-ink-300"> · {m.connection}</span>}
        </td>
        <td className="py-2 pr-3">
          {canMap ? (
            <Select className="py-1 text-[13px]" disabled={busy} value={m.forge_code ?? ""}
              onChange={(e) => onSet(e.target.value || null)}>
              <option value="">Not mapped</option>
              {forge.map((f) => <option key={f.code} value={f.code}>{f.code === f.label ? f.code : `${f.code} · ${f.label}`}</option>)}
            </Select>
          ) : (
            <span>{m.forge_code ? `${m.forge_code}${m.forge_label && m.forge_label !== m.forge_code ? ` · ${m.forge_label}` : ""}` : "–"}</span>
          )}
        </td>
        <td className="py-2 pr-3 text-ink-500 text-[12px]">
          {m.suggestion_source ? SUGGESTION_SOURCES[m.suggestion_source] ?? m.suggestion_source : "–"}
          {confidence !== null && m.status === "suggested" ? ` · ${confidence}%` : ""}
        </td>
        <td className="py-2 pr-3"><Tag variant={st.tone}>{st.label}</Tag></td>
        <td className="py-2 text-right">
          <button className="text-[12px] text-teal-600 hover:underline" onClick={onHistory}>v{m.version}</button>
        </td>
      </tr>
      {historyOpen && (
        <tr><td colSpan={canMap ? 6 : 5} className="pb-3"><History id={m.id} /></td></tr>
      )}
    </>
  )
}

function History({ id }: { id: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["integrations", "codes", "history", id],
    queryFn: async () => (await api.get<CodeHistory[]>(`/integrations/codes/${id}/history`)).data,
  })
  if (isLoading) return <Loading />
  return (
    <ul className="bg-surface-2 rounded-md p-2 text-[12px] space-y-1">
      {data?.map((h) => (
        <li key={h.version} className="flex items-center gap-2">
          <span className="font-mono text-ink-500">v{h.version}</span>
          <span>{h.forge_code ?? "not mapped"}</span>
          <Tag variant={STATUS_TAGS[h.status]?.tone ?? "neutral"}>{STATUS_TAGS[h.status]?.label ?? h.status}</Tag>
          <span className="text-ink-300">{h.suggestion_source ? SUGGESTION_SOURCES[h.suggestion_source] ?? h.suggestion_source : ""}</span>
          <Actor actor={h.changed_by} />
          <span className="text-ink-300">{formatDateTime(h.changed_at)}</span>
        </li>
      ))}
    </ul>
  )
}

function AddForgeCode({ list, label }: { list: string; label: string }) {
  const qc = useQueryClient()
  const [code, setCode] = useState("")
  const [name, setName] = useState("")
  const add = useMutation({
    mutationFn: async () => (await api.post("/integrations/forge-codes", { list, code, label: name || null })).data,
    onSuccess: () => {
      setCode("")
      setName("")
      qc.invalidateQueries({ queryKey: ["integrations", "forge-codes", list] })
    },
  })
  return (
    <form className="flex flex-wrap items-center gap-2 pt-2 border-t border-line"
      onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
      <span className="text-[12px] text-ink-500">Add to Forge's {label.toLowerCase()}:</span>
      <Input className="w-[160px] py-1" placeholder="Code" value={code} onChange={(e) => setCode(e.target.value)} />
      <Input className="w-[220px] py-1" placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
      <Button size="sm" variant="secondary" type="submit" disabled={!code.trim() || add.isPending}>Add</Button>
      <ErrorText error={add.error} fallback="Could not add it" />
    </form>
  )
}
