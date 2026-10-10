import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select, inputClass } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import { useLanguages } from "./hooks"
import type { LanguagesView, Tone } from "./types"

function ToneForm({ t, formality, canEdit }: { t: Tone; formality: Record<string, string>; canEdit: boolean }) {
  const qc = useQueryClient()
  const [d, setD] = useState({ formality: t.formality, greeting: t.greeting ?? "", sign_off: t.sign_off ?? "",
    avoid: t.avoid.join("\n"), notes: t.notes ?? "" })
  const set = (patch: Partial<typeof d>) => setD((x) => ({ ...x, ...patch }))
  const save = useMutation({
    mutationFn: async () => (await api.put<Tone>(`/languages/tone/${t.language}`, {
      ...d, avoid: d.avoid.split("\n").map((x) => x.trim()).filter(Boolean) })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["languages"] }),
  })
  const ro = !canEdit
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <Field label="Tone">
          <Select disabled={ro} value={d.formality} onChange={(e) => set({ formality: e.target.value as Tone["formality"] })}>
            {Object.entries(formality).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </Select>
        </Field>
        <Field label="Greeting, e.g." group>
          <textarea disabled={ro} rows={2} value={d.greeting} aria-label="Greeting" className={inputClass} onChange={(e) => set({ greeting: e.target.value })} />
        </Field>
        <Field label="Sign-off, e.g." group>
          <textarea disabled={ro} rows={2} value={d.sign_off} aria-label="Sign-off" className={inputClass} onChange={(e) => set({ sign_off: e.target.value })} />
        </Field>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label="Avoid (one per line)" group>
          <textarea disabled={ro} rows={3} value={d.avoid} onChange={(e) => set({ avoid: e.target.value })} aria-label="Avoid" className={inputClass} />
        </Field>
        <Field label="Notes for the writer" group>
          <textarea disabled={ro} rows={3} value={d.notes} onChange={(e) => set({ notes: e.target.value })} aria-label="Notes" className={inputClass} />
        </Field>
      </div>
      <ErrorText error={save.error} fallback="Could not save the tone guide" />
      <div className="flex flex-wrap items-center gap-3">
        {canEdit && <Button size="sm" type="submit" disabled={save.isPending}>Save {t.name}</Button>}
        {save.isSuccess && <span className="text-[12.5px] text-green-500">Saved.</span>}
        <span className="text-[11.5px] text-ink-300">
          {t.is_default ? "Forge's starting guide." : <>Changed by <Actor actor={t.updated_by} /> on {formatDateTime(t.updated_at)}.</>}
        </span>
      </div>
    </form>
  )
}

function Tones({ data, canEdit }: { data: LanguagesView; canEdit: boolean }) {
  const [open, setOpen] = useState(data.tones[0]?.language ?? "en")
  const t = data.tones.find((x) => x.language === open)
  return (
    <Card>
      <CardHeader><CardTitle>Tone guides</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <p className="text-[12.5px] text-ink-500">How the Writing Assistant writes in each language. It writes natively, never by translating from English.</p>
        <div className="flex gap-1 border-b border-line">
          {data.tones.map((x) => (
            <button key={x.language} type="button" onClick={() => setOpen(x.language)}
              className={cn("px-3 py-1.5 text-[13px] font-medium text-ink-500 border-b-2 border-transparent -mb-px hover:text-ink-900",
                open === x.language && "text-ink-900 border-teal-500")}>
              {x.name}{x.native !== x.name ? ` · ${x.native}` : ""}
            </button>
          ))}
        </div>
        {t && <ToneForm key={`${t.language}-${t.updated_at ?? "d"}`} t={t} formality={data.formality} canEdit={canEdit} />}
      </CardContent>
    </Card>
  )
}

function Glossary({ data, canEdit }: { data: LanguagesView; canEdit: boolean }) {
  const qc = useQueryClient()
  const empty = { term: "", kind: "keep" as "keep" | "translate" | "forbid", language: "", translation: "", note: "" }
  const [d, setD] = useState(empty)
  const set = (patch: Partial<typeof d>) => setD((x) => ({ ...x, ...patch }))
  const refresh = () => qc.invalidateQueries({ queryKey: ["languages"] })
  const add = useMutation({
    mutationFn: async () => (await api.post("/languages/glossary", { ...d, language: d.language || null,
      translation: d.translation || null, note: d.note || null })).data,
    onSuccess: () => { setD(empty); refresh() },
  })
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/languages/glossary/${id}`),
    onSuccess: refresh,
  })
  const others = data.recognised.filter((l) => l.code !== "en")
  const name = (code: string | null) => data.recognised.find((l) => l.code === code)?.name ?? code
  return (
    <Card>
      <CardHeader><CardTitle>Glossary</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <p className="text-[12.5px] text-ink-500">Checked on every AI-written message and shown while people write. A message that breaks it is rewritten once, then reviewed by a person.</p>
        {data.glossary.length === 0 ? <Empty>No terms yet. Add your brand and product names first.</Empty> : (
          <table className="w-full text-[13px]">
            <tbody>
              {data.glossary.map((g) => (
                <tr key={g.id} className="border-b border-line last:border-0">
                  <td className="py-1.5 pr-2 w-36"><Tag variant={g.kind === "forbid" ? "red" : g.kind === "translate" ? "teal" : "neutral"}>{data.glossary_kinds[g.kind]}</Tag></td>
                  <td className="py-1.5 pr-2 font-medium w-1/4">{g.term}</td>
                  <td className="py-1.5 pr-2 text-ink-700 w-1/4">{g.kind === "translate" ? `${g.translation} (${name(g.language)})` : ""}</td>
                  <td className="py-1.5 pr-2 text-ink-300 text-[12px]">{g.note}</td>
                  {canEdit && (
                    <td className="py-1.5 text-right w-16">
                      <button type="button" className="text-[12px] text-ink-300 hover:text-red-500" disabled={remove.isPending}
                        onClick={() => remove.mutate(g.id)}>Remove</button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {canEdit && (
          <form className="grid grid-cols-1 sm:grid-cols-[150px_1fr_1fr] gap-2 items-end rounded-md bg-surface-2 p-2"
            onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
            <Field label="Rule">
              <Select value={d.kind} onChange={(e) => set({ kind: e.target.value as typeof d.kind })}>
                {Object.entries(data.glossary_kinds).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              </Select>
            </Field>
            <Field label="Term"><Input value={d.term} maxLength={100} placeholder={d.kind === "keep" ? "e.g. Forge Steels" : d.kind === "forbid" ? "e.g. cheap" : "e.g. invoice"} onChange={(e) => set({ term: e.target.value })} /></Field>
            {d.kind === "translate" ? (
              <div className="grid grid-cols-[120px_1fr] gap-2">
                <Field label="In">
                  <Select value={d.language} onChange={(e) => set({ language: e.target.value })}>
                    <option value="">Choose…</option>
                    {others.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
                  </Select>
                </Field>
                <Field label="Write it as"><Input value={d.translation} onChange={(e) => set({ translation: e.target.value })} /></Field>
              </div>
            ) : (
              <Field label="Note (optional)"><Input value={d.note} maxLength={300} onChange={(e) => set({ note: e.target.value })} /></Field>
            )}
            <div className="sm:col-span-3 flex items-center gap-2">
              <Button size="sm" type="submit" disabled={!d.term.trim() || add.isPending}>Add to the glossary</Button>
              <ErrorText error={add.error ?? remove.error} fallback="Could not change the glossary" />
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  )
}

/** Which languages Forge recognises and writes, the tone guides and the glossary (L6-L9). */
export function LanguagesPage() {
  const canEdit = hasPermission(usePermissions(), "languages.manage")
  const { data, isLoading, error } = useLanguages()
  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load the languages" />
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><CardTitle>Languages</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {data.recognised.map((l) => (
              <span key={l.code} className={cn("rounded-full border px-2.5 py-1 text-[12.5px]", l.writes ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-700")}>
                {l.name}{l.native !== l.name ? ` · ${l.native}` : ""}
              </span>
            ))}
          </div>
          <p className="text-[12px] text-ink-500">
            Forge recognises incoming messages in all of these. The highlighted ones are those the Writing Assistant writes in;
            people can write in any of them. More languages, and Indian languages typed in English letters, come later (decision L6).
          </p>
          {!data.writer_available && <p className="text-[12px] text-amber-500">The Writing Assistant needs the Gemini key (open question D3).</p>}
          {!data.speech_available && <p className="text-[12px] text-amber-500">Dictation needs the Groq key (open question D3).</p>}
        </CardContent>
      </Card>
      <Tones data={data} canEdit={canEdit} />
      <Glossary data={data} canEdit={canEdit} />
      {!canEdit && <p className="text-[11.5px] text-ink-300">Only people with the languages.manage permission can change these.</p>}
    </div>
  )
}
