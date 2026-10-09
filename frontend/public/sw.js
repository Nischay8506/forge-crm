/* Forge service worker: shows approval-inbox push notifications (decision H5) and
 * opens the card when one is tapped. It does not cache pages: Forge needs the server. */

self.addEventListener("install", () => self.skipWaiting())
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()))

self.addEventListener("push", (event) => {
  let msg = {}
  try {
    msg = event.data ? event.data.json() : {}
  } catch {
    msg = { title: "Forge", body: event.data ? event.data.text() : "" }
  }
  event.waitUntil(self.registration.showNotification(msg.title || "Forge", {
    body: msg.body || "",
    tag: msg.tag || undefined,
    renotify: Boolean(msg.tag),
    icon: "/icons/icon-192.png",
    badge: "/icons/badge-96.png",
    data: { url: msg.url || "/decisions" },
  }))
})

self.addEventListener("notificationclick", (event) => {
  event.notification.close()
  const url = new URL(event.notification.data?.url || "/decisions", self.location.origin).href
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true })
    for (const c of open) {
      if (new URL(c.url).origin === self.location.origin && "navigate" in c) {
        await c.focus()
        return c.navigate(url)
      }
    }
    return self.clients.openWindow(url)
  })())
})
