import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { AgentBadge } from "@/components/ui/AgentBadge"
import { hasPermission } from "@/lib/permissions"
import { usePermissions } from "@/stores/authStore"

interface UserRow { id: string; full_name: string }

/** Shows who did something: an agent badge (teal) or the person's name. */
export function Actor({ actor }: { actor: string | null | undefined }) {
  const canReadUsers = hasPermission(usePermissions(), "users.read")
  const { data } = useQuery({
    queryKey: ["users"],
    queryFn: async () => (await api.get<UserRow[]>("/users")).data,
    enabled: canReadUsers && !!actor?.startsWith("user:"),
    staleTime: 5 * 60_000,
  })
  if (!actor) return <span className="text-ink-300">–</span>
  if (actor.startsWith("system:")) return <span className="text-ink-500">Forge</span>
  if (actor.startsWith("mcp:")) return <span className="text-ink-700">{actor.slice(4)} (outside AI tool)</span>
  if (actor.startsWith("agent:")) {
    const name = actor.slice(6).replace(/_/g, " ").toUpperCase()
    return <AgentBadge label={name} />
  }
  const id = actor.startsWith("user:") ? actor.slice(5) : actor
  const name = data?.find((u) => u.id === id)?.full_name
  return <span className="text-ink-700">{name ?? "A team member"}</span>
}
