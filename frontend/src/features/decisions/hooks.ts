import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import type { CardType, Delegation, DecisionCounts, InboxSettings, MyNumbers } from "@/features/agents/types"

/** The menu badge and the inbox header (cards I may decide, near or past deadline). */
export function useDecisionCount(enabled: boolean) {
  return useQuery({
    queryKey: ["decisions", "count"],
    queryFn: async () => (await api.get<DecisionCounts>("/decisions/count")).data,
    enabled,
    refetchInterval: 30_000,
  })
}

/** My median decision time and override rate over the last 30 days. */
export function useMyNumbers() {
  return useQuery({
    queryKey: ["decisions", "me"],
    queryFn: async () => (await api.get<MyNumbers>("/decisions/me")).data,
    staleTime: 60_000,
  })
}

export function useInboxSettings() {
  return useQuery({
    queryKey: ["decisions", "settings"],
    queryFn: async () => (await api.get<InboxSettings>("/decisions/settings")).data,
    staleTime: 5 * 60_000,
  })
}

export function useColleagues(enabled = true) {
  return useQuery({
    queryKey: ["decisions", "colleagues"],
    queryFn: async () => (await api.get<{ id: string; name: string }[]>("/decisions/colleagues")).data,
    enabled,
    staleTime: 5 * 60_000,
  })
}

export function useCardTypes() {
  return useQuery({
    queryKey: ["decisions", "card-types"],
    queryFn: async () => (await api.get<CardType[]>("/decisions/card-types")).data,
    staleTime: 10 * 60_000,
  })
}

/** Out-of-office and hand-overs I gave or received. */
export function useDelegations() {
  return useQuery({
    queryKey: ["decisions", "delegations"],
    queryFn: async () => (await api.get<Delegation[]>("/decisions/delegations")).data,
  })
}

/** The current time, refreshed every `ms` (for "Due in 12 min" labels). */
export function useNow(ms = 30_000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(t)
  }, [ms])
  return now
}
