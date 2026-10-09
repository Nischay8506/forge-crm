import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { API_URL, api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { formatDateTime } from "@/lib/utils"
import { Actor } from "@/features/steward/Actor"
import { ago } from "./labels"
import type { McpClient } from "./types"

/** Where outside AI tools connect: the backend's /mcp/ address. */
export function mcpUrl(): string {
  try {
    return `${new URL(API_URL).origin}/mcp/`
  } catch {
    return `${window.location.origin}/mcp/`
  }
}

export function McpClientsPage() {
  const qc = useQueryClient()
  const [name, setName] = useState("")
  const [allowWrite, setAllowWrite] = useState(false)
  const [issued, setIssued] = useState<(McpClient & { token: string }) | null>(null)
  const { data, isLoading, error } = useQuery({
    queryKey: ["integrations", "mcp"],
    queryFn: async () => (await api.get<McpClient[]>("/integrations/mcp-clients")).data,
  })
  const create = useMutation({
    mutationFn: async () => (await api.post<McpClient & { token: string }>("/integrations/mcp-clients", {
      name: name.trim(), allow_write: allowWrite,
    })).data,
    onSuccess: (c) => {
      setIssued(c)
      setName("")
      setAllowWrite(false)
      qc.invalidateQueries({ queryKey: ["integrations", "mcp"] })
    },
  })
  const revoke = useMutation({
    mutationFn: async (c: McpClient) => {
      if (!window.confirm(`Stop ${c.name}? Tools using this token stop working at once.`)) return null
      return (await api.post(`/integrations/mcp-clients/${c.id}/revoke`)).data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["integrations", "mcp"] }),
  })
  const url = mcpUrl()

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-4 items-start">
      <div className="space-y-4">
        <p className="text-[12.5px] text-ink-500">
          AI assistants and scripts can use Forge through its MCP server at <code className="text-ink-900">{url}</code>.
          They can look up accounts and read an account's ERP orders and outstanding invoices. A token that allows
          writes may also ask for an account to be created as a customer in an ERP; a person approves that in
          Decisions first. Each call is checked by the “Outside AI tools” agent's settings
          (<Link to="/agents/list/outside_ai" className="text-teal-600 hover:underline">change them</Link>) and kept in the audit log.
        </p>
        {issued && (
          <Card className="p-4 space-y-2 border-l-4 border-l-gold-500">
            <p className="text-[13px] font-semibold">Token for {issued.name}. Copy it now; Forge will not show it again.</p>
            <pre className="text-[12px] bg-surface-2 rounded-md p-2 whitespace-pre-wrap break-all">{issued.token}</pre>
            <p className="text-[12px] text-ink-500">Server address: {url}. Send the token as <code>Authorization: Bearer &lt;token&gt;</code>.</p>
            <p className="text-[12px] text-ink-500">For Claude Code:</p>
            <pre className="text-[11.5px] bg-surface-2 rounded-md p-2 whitespace-pre-wrap break-all">
              {`claude mcp add --transport http forge-crm ${url} --header "Authorization: Bearer ${issued.token}"`}
            </pre>
            <Button size="sm" variant="secondary" onClick={() => setIssued(null)}>I have copied it</Button>
          </Card>
        )}
        <Card>
          <CardContent className="pt-4">
            {isLoading && <Loading />}
            <ErrorText error={error ?? revoke.error} fallback="Could not load the tokens" />
            {data && data.length === 0 && <Empty>No tokens yet.</Empty>}
            {data && data.length > 0 && (
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left text-ink-500 border-b border-line">
                    <th className="py-2 pr-3 font-semibold">Name</th>
                    <th className="py-2 pr-3 font-semibold">May</th>
                    <th className="py-2 pr-3 font-semibold">Token</th>
                    <th className="py-2 pr-3 font-semibold">Last used</th>
                    <th className="py-2 pr-3 font-semibold">Made by</th>
                    <th className="py-2 font-semibold" />
                  </tr>
                </thead>
                <tbody>
                  {data.map((c) => (
                    <tr key={c.id} className="border-b border-line last:border-0">
                      <td className="py-2 pr-3 font-medium">{c.name}</td>
                      <td className="py-2 pr-3">{c.allow_write ? <Tag variant="gold">Read and ask to write</Tag> : <Tag variant="teal">Read only</Tag>}</td>
                      <td className="py-2 pr-3 font-mono text-[12px] text-ink-500">{c.token_hint}</td>
                      <td className="py-2 pr-3 text-ink-500">{ago(c.last_used_at)}</td>
                      <td className="py-2 pr-3 text-[12px]"><Actor actor={c.created_by} /> <span className="text-ink-300">{formatDateTime(c.created_at)}</span></td>
                      <td className="py-2 text-right">
                        {c.revoked_at ? <Tag>Stopped</Tag> : (
                          <button className="text-[12px] text-red-500 hover:underline disabled:opacity-50" disabled={revoke.isPending}
                            onClick={() => revoke.mutate(c)}>Stop</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader><CardTitle>New token</CardTitle></CardHeader>
        <CardContent>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); create.mutate() }}>
            <Field label="What will use it" hint="For example Claude Desktop (Priya) or the nightly report script">
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <label className="flex items-start gap-2 text-[13px]">
              <input type="checkbox" className="mt-1" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} />
              <span>May ask to create ERP customers <span className="text-ink-300">(a person still approves each one)</span></span>
            </label>
            <ErrorText error={create.error} fallback="Could not make the token" />
            <Button type="submit" disabled={!name.trim() || create.isPending}>Make token</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
