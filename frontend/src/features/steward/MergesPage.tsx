import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Card } from "@/components/ui/Card"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { formatDateTime } from "@/lib/utils"
import { Actor } from "./Actor"
import type { MergeRow } from "./types"

export function MergesPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["merges"],
    queryFn: async () => (await api.get<MergeRow[]>("/steward/merges", { params: { limit: 200 } })).data,
  })
  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-ink-500">
        Every merge, by the Data Steward or a person. Open one to see what changed or to undo it.
      </p>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load merges" />
      {data && (
        <Card>
          {data.length === 0 ? <Empty>No merges yet.</Empty> : (
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-line">
                  <th className="p-3 font-semibold">When</th>
                  <th className="p-3 font-semibold">Merged</th>
                  <th className="p-3 font-semibold">Into</th>
                  <th className="p-3 font-semibold">By</th>
                  <th className="p-3 font-semibold" />
                </tr>
              </thead>
              <tbody>
                {data.map((m) => (
                  <tr key={m.id} className="border-b border-line last:border-0 hover:bg-surface-0">
                    <td className="p-3 text-ink-500 whitespace-nowrap">
                      <Link to={`/steward/merges/${m.id}`} className="text-teal-600 hover:underline">{formatDateTime(m.created_at)}</Link>
                    </td>
                    <td className="p-3">{m.merged_name ?? "–"} <span className="text-ink-300 text-[11.5px]">{m.record_type}</span></td>
                    <td className="p-3">{m.survivor_name ?? "–"}</td>
                    <td className="p-3"><Actor actor={m.decided_by} /></td>
                    <td className="p-3 text-right">{m.undone_at && <Tag variant="amber">Undone</Tag>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </div>
  )
}
