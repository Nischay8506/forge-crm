import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Card } from "@/components/ui/Card"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import type { AgentSummary } from "./types"

export function AgentsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get<AgentSummary[]>("/agents")).data,
  })
  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-ink-500">
        Every agent, what it may do and how much it may do on its own. Open one to change its level,
        see the rules that apply to it, or stop it.
      </p>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load agents" />
      {data && (
        <Card>
          <ul className="divide-y divide-line">
            {data.map((a) => (
              <li key={a.key}>
                <Link to={`/agents/list/${a.key}`} className="flex items-start gap-4 p-4 hover:bg-surface-0">
                  <div className="flex-1 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-ink-900 text-[14px]">{a.name}</span>
                      {a.test_only && <Tag>Test only</Tag>}
                      {a.stopped && <Tag variant="red">Stopped</Tag>}
                    </div>
                    <p className="text-[12.5px] text-ink-500 max-w-3xl">{a.purpose}</p>
                    <p className="text-[12px] text-ink-300">
                      Can: {a.tools.map((t) => t.title.toLowerCase()).join(", ")}
                      {a.overrides.length > 0 && ` · ${a.overrides.length} override(s)`}
                    </p>
                  </div>
                  <div className="text-right shrink-0">
                    <Tag variant="teal">{a.code}</Tag>
                    <div className="text-[11.5px] text-ink-500 mt-1">{a.label}</div>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
