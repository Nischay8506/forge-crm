import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import type { FamilyOptions, GuidedState } from "./types"

/** A family's current options and rules, its versions, and the samples to start from. */
export function useFamilyOptions(familyId: string | undefined) {
  return useQuery({
    queryKey: ["products", "family", familyId],
    queryFn: async () => (await api.get<FamilyOptions>(`/products/families/${familyId}`)).data,
    enabled: !!familyId,
  })
}

/** One step of the guided configurator for the choices made so far. */
export function useGuided(familyId: string, values: Record<string, string>) {
  return useQuery({
    queryKey: ["products", "guided", familyId, values],
    queryFn: async () => (await api.post<GuidedState>(`/products/families/${familyId}/configure`, { values })).data,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
}

export interface FamilySummary {
  id: string
  name: string
  make_ability: string
  template: string | null
  options: { version: number; attributes: number; rules: number; code_pattern: string } | null
}

/** Each active family and a summary of its options, if it has any. */
export function useProductFamilies(enabled = true) {
  return useQuery({
    queryKey: ["products", "families"],
    queryFn: async () => (await api.get<{ families: FamilySummary[] }>("/products/families")).data,
    enabled,
    staleTime: 30_000,
  })
}
