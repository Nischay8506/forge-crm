import { useEffect, useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Select, inputClass } from "@/components/ui/Field"
import { ErrorText, Loading } from "@/components/ui/States"
import { cn } from "@/lib/utils"
import { ContactPicker, type PickedContact } from "./ContactPicker"
import { useLanguages, useMessage } from "./hooks"
import { PURPOSE_HINTS, PURPOSE_LABELS, partyLabel } from "./labels"
import { type EditorValue, MessageEditor } from "./MessageEditor"
import { CustomerView } from "./MessageDetailPage"
import type { MessageView, Written } from "./types"

type Mode = "myself" | "ai"

/** A new message, or a reply to one a customer sent (?reply_to=). Written by the person,
 *  or drafted by the Writing Assistant in the chosen language and then checked by them. */
export function ComposePage() {
  const [search] = useSearchParams()
  const replyTo = search.get("reply_to") ?? undefined
  const contactParam = search.get("contact")
  const navigate = useNavigate()
  const qc = useQueryClient()
  const langs = useLanguages()
  const original = useMessage(replyTo)
  const [mode, setMode] = useState<Mode>("myself")
  const [contact, setContact] = useState<PickedContact | null>(null)
  const [v, setV] = useState<EditorValue>({ subject: "", body: "", language: "", purpose: replyTo ? "reply" : "transactional" })
  const [brief, setBrief] = useState("")
  const [aiLanguage, setAiLanguage] = useState("en")

  const preset = useQuery({
    queryKey: ["contact", contactParam],
    queryFn: async () => (await api.get<PickedContact & { preferred_language: string | null }>(`/contacts/${contactParam}`)).data,
    enabled: !!contactParam && !replyTo,
  })
  useEffect(() => { if (preset.data) setContact(preset.data) }, [preset.data])
  const picked = useQuery({
    queryKey: ["contact", contact?.id],
    queryFn: async () => (await api.get<{ preferred_language: string | null }>(`/contacts/${contact!.id}`)).data,
    enabled: !!contact,
    staleTime: 60_000,
  })
  const o = original.data
  useEffect(() => {
    if (!o) return
    setV((x) => ({ ...x, purpose: "reply", subject: x.subject || (o.subject?.startsWith("Re:") ? o.subject : `Re: ${o.subject ?? ""}`.trim()) }))
  }, [o])
  // Write in the customer's language: their preferred one, or the one they wrote in.
  const want = o?.language ?? picked.data?.preferred_language ?? null
  useEffect(() => {
    if (!want) return
    setV((x) => (x.language ? x : { ...x, language: want }))
    if (langs.data?.writing.includes(want)) setAiLanguage(want)
  }, [want, langs.data])

  const who = replyTo ? { in_reply_to_id: replyTo } : { contact_id: contact?.id }
  const ready = !!replyTo || !!contact
  const done = (m: MessageView, state?: object) => {
    qc.invalidateQueries({ queryKey: ["messages"] })
    navigate(`/messages/${m.id}`, { state })
  }
  const create = useMutation({
    mutationFn: async (send: boolean) => (await api.post<MessageView>("/messages", {
      ...who, purpose: v.purpose, subject: v.subject, body: v.body, language: v.language || null, send })).data,
    onSuccess: (m) => done(m),
  })
  const write = useMutation({
    mutationFn: async () => (await api.post<Written>("/messages/write", {
      ...who, purpose: replyTo ? "reply" : v.purpose, language: aiLanguage, brief })).data,
    onSuccess: (m) => done(m, { problems: m.problems, rewritten: m.rewritten }),
  })

  if (replyTo && original.isLoading) return <Loading />
  const writerOff = langs.data && !langs.data.writer_available

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4 items-start">
      <Card>
        <CardContent className="space-y-4 pt-4">
          {replyTo ? (
            o ? (
              <div className="space-y-1.5">
                <p className="text-[12px] font-semibold text-ink-700">Replying to {partyLabel(o).replace(/^From /, "")}</p>
                <CustomerView m={o} />
              </div>
            ) : <ErrorText error={original.error} fallback="Could not load the message you are replying to" />
          ) : (
            <Field label="To" group hint={contact && !contact.email ? "This contact has no email address yet; add one on their account first." : undefined}>
              <ContactPicker value={contact} onChange={setContact} />
            </Field>
          )}

          <div className="flex gap-1 border-b border-line">
            {([["myself", "Write it myself"], ["ai", "Draft it for me"]] as const).map(([k, label]) => (
              <button key={k} type="button" onClick={() => setMode(k)}
                className={cn("px-3 py-2 text-[13px] font-medium text-ink-500 border-b-2 border-transparent -mb-px hover:text-ink-900",
                  mode === k && "text-ink-900 border-teal-500")}>
                {label}
              </button>
            ))}
          </div>

          {mode === "myself" ? (
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); create.mutate(true) }}>
              <MessageEditor value={v} onChange={setV} languages={langs.data} isReply={!!replyTo} />
              <ErrorText error={create.error} fallback="Could not save the message" />
              <div className="flex flex-wrap gap-2">
                <Button type="submit" disabled={!ready || !v.subject.trim() || !v.body.trim() || create.isPending}>Send</Button>
                <Button type="button" variant="secondary" disabled={!ready || create.isPending} onClick={() => create.mutate(false)}>Save as draft</Button>
              </div>
            </form>
          ) : (
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); write.mutate() }}>
              {writerOff && (
                <p className="rounded-md bg-amber-100 p-2 text-[12.5px] text-ink-700">
                  The Writing Assistant needs the Gemini key (GEMINI_API_KEY in backend/.env, open question D3). Until then, write the message yourself.
                </p>
              )}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {!replyTo && (
                  <Field label="What kind of message" hint={PURPOSE_HINTS[v.purpose]}>
                    <Select value={v.purpose} onChange={(e) => setV({ ...v, purpose: e.target.value as EditorValue["purpose"] })}>
                      {(["transactional", "promotional", "reply"] as const).map((p) => <option key={p} value={p}>{PURPOSE_LABELS[p]}</option>)}
                    </Select>
                  </Field>
                )}
                <Field label="Write it in" hint="It writes natively in the language, never by translating from English.">
                  <Select value={aiLanguage} onChange={(e) => setAiLanguage(e.target.value)}>
                    {(langs.data?.recognised ?? []).filter((l) => l.writes).map((l) => (
                      <option key={l.code} value={l.code}>{l.name}{l.native !== l.name ? ` (${l.native})` : ""}</option>
                    ))}
                  </Select>
                </Field>
              </div>
              <Field label="What should it say?" group hint="A few lines in any language. Mention names and amounts exactly as they should appear.">
                <textarea rows={5} value={brief} maxLength={2000} aria-label="What should it say"
                  placeholder="e.g. Thank them for the order of 40 tonnes, confirm delivery on Monday 13 Oct, ask them to share the unloading contact."
                  className={inputClass} onChange={(e) => setBrief(e.target.value)} />
              </Field>
              <ErrorText error={write.error} fallback="The Writing Assistant could not draft it" />
              <div className="flex flex-wrap items-center gap-2">
                <Button type="submit" disabled={!ready || brief.trim().length < 3 || write.isPending || !!writerOff}>
                  {write.isPending ? "Writing…" : "Draft it"}
                </Button>
                <span className="text-[12px] text-ink-300">You read and change it before anything is sent.</span>
              </div>
            </form>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Before it goes</CardTitle></CardHeader>
        <CardContent className="text-[12.5px] text-ink-500 space-y-2">
          <p>Every message passes the same checks: an active contact with an email address, no agent stop or takeover, a subject and text, consent for promotional messages, then the limits and quiet hours.</p>
          <p>A message that may not go yet is <b className="text-ink-700">held</b> and goes by itself at the next allowed time. You can send a held message anyway, with a reason.</p>
          <p>Text written with the AI says so to the customer, in the message's language.</p>
          <Link to="/messages/settings" className="text-teal-600 hover:underline">See the limits and quiet hours</Link>
        </CardContent>
      </Card>
    </div>
  )
}
