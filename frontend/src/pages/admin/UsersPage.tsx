import { useQuery } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Card } from "@/components/ui/Card"
import { Tag } from "@/components/ui/Tag"

interface UserRow { id: string; email: string; full_name: string; is_active: boolean }

export function UsersPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["users"],
    queryFn: async () => (await api.get<UserRow[]>("/users")).data,
  })

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-ink-900">Users</h1>
      {isLoading && <p className="text-ink-500 text-sm">Loading…</p>}
      {error && <p className="text-red-500 text-sm">{errorMessage(error, "Could not load users")}</p>}
      {data && (
        <Card>
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-ink-500 border-b border-line">
                <th className="p-3 font-semibold">Name</th>
                <th className="p-3 font-semibold">Email</th>
                <th className="p-3 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.map((u) => (
                <tr key={u.id} className="border-b border-line last:border-0">
                  <td className="p-3">{u.full_name}</td>
                  <td className="p-3">{u.email}</td>
                  <td className="p-3">
                    <Tag variant={u.is_active ? "green" : "neutral"}>
                      {u.is_active ? "Active" : "Inactive"}
                    </Tag>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  )
}
