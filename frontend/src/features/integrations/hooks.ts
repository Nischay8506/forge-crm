import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import type { Overview } from "./types"

/** Every connection with its health. Shared by the menu dot, the banner and the list. */
export function useOverview(enabled = true) {
  return useQuery({
    queryKey: ["integrations", "overview"],
    queryFn: async () => (await api.get<Overview>("/integrations")).data,
    enabled,
    refetchInterval: 60_000,
  })
}
