import { api } from "@/config/api"

/** Files are downloaded, never opened inside Forge (N9 A). The request carries the
 *  sign-in, so the file comes as a blob and is saved under its own name. */
export async function downloadFile(fileId: string, filename: string): Promise<void> {
  const r = await api.get<Blob>(`/enquiries/files/${fileId}`, { responseType: "blob" })
  const url = URL.createObjectURL(r.data)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** A picked file as the Test mailbox sends it: name, type and base64 content. */
export function readAsBase64(file: File): Promise<{ filename: string; content_type: string; data_base64: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const s = String(reader.result ?? "")
      resolve({ filename: file.name, content_type: file.type || "application/octet-stream", data_base64: s.slice(s.indexOf(",") + 1) })
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}
