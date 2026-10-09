import { api } from "@/config/api"

/** Push notifications for new cards, reminders and escalations (decision H5).
 * The browser subscribes with the server's public key; the service worker (public/sw.js)
 * shows each message and opens the card when it is tapped. */

export type PushState = "unsupported" | "not_set_up" | "blocked" | "on" | "off"

export function supported(): boolean {
  return typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window
    && "Notification" in window
}

/** The applicationServerKey the browser wants: the base64url key as bytes. */
export function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (base64url.length % 4)) % 4)
  const raw = atob((base64url + pad).replace(/-/g, "+").replace(/_/g, "/"))
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

export function registerWorker(): Promise<ServiceWorkerRegistration | undefined> {
  if (!supported()) return Promise.resolve(undefined)
  return navigator.serviceWorker.register("/sw.js").catch(() => undefined)
}

async function serverKey(): Promise<string | null> {
  return (await api.get<{ public_key: string | null }>("/decisions/push/key")).data.public_key
}

export async function pushState(): Promise<PushState> {
  if (!supported()) return "unsupported"
  if (!(await serverKey())) return "not_set_up"
  if (Notification.permission === "denied") return "blocked"
  const reg = await navigator.serviceWorker.getRegistration()
  const sub = await reg?.pushManager.getSubscription()
  return sub && Notification.permission === "granted" ? "on" : "off"
}

function body(sub: PushSubscription) {
  const j = sub.toJSON()
  return { endpoint: j.endpoint, keys: { p256dh: j.keys?.p256dh, auth: j.keys?.auth } }
}

export async function enablePush(): Promise<PushState> {
  const key = await serverKey()
  if (!key) return "not_set_up"
  if ((await Notification.requestPermission()) !== "granted") return "blocked"
  const reg = (await registerWorker()) ?? (await navigator.serviceWorker.ready)
  await navigator.serviceWorker.ready
  const sub = (await reg.pushManager.getSubscription())
    ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) }))
  await api.post("/decisions/push/subscribe", body(sub))
  await api.post("/decisions/push/test")
  return "on"
}

export async function disablePush(): Promise<PushState> {
  const reg = await navigator.serviceWorker.getRegistration()
  const sub = await reg?.pushManager.getSubscription()
  if (sub) {
    await api.post("/decisions/push/unsubscribe", body(sub)).catch(() => undefined)
    await sub.unsubscribe()
  }
  return "off"
}
