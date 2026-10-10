import { useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Field, Input, Select, inputClass } from "@/components/ui/Field"
import { useDebounced } from "@/lib/useDebounced"
import { cn } from "@/lib/utils"
import { PURPOSE_HINTS, PURPOSE_LABELS } from "./labels"
import type { LanguagesView, TextCheck } from "./types"

export interface EditorValue {
  subject: string
  body: string
  language: string
  purpose: "promotional" | "transactional" | "reply"
}

/** Subject, text, language and purpose of a message a person writes, with the language
 *  and glossary checks shown while typing and dictation when the speech key is set. */
export function MessageEditor({ value, onChange, languages, isReply }: {
  value: EditorValue
  onChange: (v: EditorValue) => void
  languages: LanguagesView | undefined
  isReply?: boolean
}) {
  const set = (patch: Partial<EditorValue>) => onChange({ ...value, ...patch })
  const text = useDebounced({ subject: value.subject, body: value.body, language: value.language }, 600)
  const check = useQuery({
    queryKey: ["check-text", text],
    queryFn: async () => (await api.post<TextCheck>("/messages/check-text", {
      subject: text.subject || null, body: text.body, language: text.language || null })).data,
    enabled: text.body.trim().length > 0,
    staleTime: Infinity,
  })
  const c = check.data
  const purposes = isReply ? (["reply"] as const) : (["transactional", "promotional", "reply"] as const)

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label="What kind of message" hint={PURPOSE_HINTS[value.purpose]}>
          <Select value={value.purpose} disabled={isReply}
            onChange={(e) => set({ purpose: e.target.value as EditorValue["purpose"] })}>
            {purposes.map((p) => <option key={p} value={p}>{PURPOSE_LABELS[p]}</option>)}
          </Select>
        </Field>
        <Field label="Language" hint="Forge recognises it from the text if you leave it on “Recognise”.">
          <Select value={value.language} onChange={(e) => set({ language: e.target.value })}>
            <option value="">Recognise from the text</option>
            {(languages?.recognised ?? []).map((l) => (
              <option key={l.code} value={l.code}>{l.name}{l.native !== l.name ? ` (${l.native})` : ""}</option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label="Subject">
        <Input value={value.subject} maxLength={300} onChange={(e) => set({ subject: e.target.value })} />
      </Field>
      <Field label="Message" group>
        <textarea rows={9} value={value.body} maxLength={20000} aria-label="Message"
          className={cn(inputClass, "leading-relaxed")} onChange={(e) => set({ body: e.target.value })} />
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="text-[12px] space-y-0.5 min-w-0">
            {c?.detected_label && (
              <p className="text-ink-500">Reads as <b className="text-ink-700">{c.detected_label}</b>{c.sure ? "" : " (not sure)"}</p>
            )}
            {c?.problems.map((p) => <p key={p} className="text-amber-500">{p}</p>)}
          </div>
          <Dictate available={!!languages?.speech_available} language={value.language || null}
            onText={(t) => set({ body: value.body ? `${value.body.trimEnd()}\n${t}` : t })} />
        </div>
      </Field>
    </div>
  )
}

/** Record from the microphone and turn speech into text (L10 A). The audio is not kept. */
export function Dictate({ available, language, onText }: {
  available: boolean; language: string | null; onText: (text: string) => void
}) {
  const [state, setState] = useState<"idle" | "recording" | "working">("idle")
  const [seconds, setSeconds] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const rec = useRef<MediaRecorder | null>(null)
  const timer = useRef<number | null>(null)
  // The recording ends later; always hand the text to the latest version of the message.
  const latest = useRef(onText)
  latest.current = onText

  useEffect(() => () => {
    if (timer.current) window.clearInterval(timer.current)
    rec.current?.stream.getTracks().forEach((t) => t.stop())
  }, [])

  if (!available) {
    return (
      <Button type="button" size="sm" variant="secondary" disabled
        title="Speech to text needs the Groq key (GROQ_API_KEY in backend/.env, open question D3)">
        Dictate (needs the speech key)
      </Button>
    )
  }

  async function start() {
    setError(null)
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("This browser cannot record")
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const chunks: Blob[] = []
      const r = new MediaRecorder(stream)
      r.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
      r.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        if (timer.current) window.clearInterval(timer.current)
        setState("working")
        try {
          const type = (r.mimeType || "audio/webm").split(";")[0]
          const form = new FormData()
          form.append("file", new Blob(chunks, { type }), `recording.${type.split("/")[1] ?? "webm"}`)
          if (language) form.append("language", language)
          const res = await api.post<{ text: string }>("/languages/transcribe", form, {
            headers: { "Content-Type": "multipart/form-data" } })
          if (res.data.text) latest.current(res.data.text)
          else setError("Nothing was heard")
        } catch (e) {
          setError(errorMessage(e, "Could not turn the recording into text"))
        } finally {
          setState("idle")
        }
      }
      rec.current = r
      r.start()
      setSeconds(0)
      timer.current = window.setInterval(() => setSeconds((s) => s + 1), 1000)
      setState("recording")
    } catch {
      setError("The microphone is blocked in this browser's settings")
    }
  }

  return (
    <div className="flex items-center gap-2 shrink-0">
      {error && <span className="text-[11.5px] text-red-500">{error}</span>}
      {state === "recording" ? (
        <Button type="button" size="sm" variant="danger" onClick={() => rec.current?.stop()}>
          Stop ({seconds} s)
        </Button>
      ) : (
        <Button type="button" size="sm" variant="secondary" disabled={state === "working"} onClick={start}
          title="Speak the message; the text is added at the end">
          {state === "working" ? "Writing it down…" : "Dictate"}
        </Button>
      )}
    </div>
  )
}
