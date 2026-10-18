import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import type { CostModelDetail, CostModelFamily, MachineItem, MaterialItem, PricePolicyDetail, PricePolicyList, QuoteDetail, QuoteRow } from "./types"

export function useQuotes(status: string, mine: boolean, reprice = false) {
  return useQuery({
    queryKey: ["quotes", status, mine, reprice],
    queryFn: async () => (await api.get<{ quotes: QuoteRow[] }>("/quotes", {
      params: { status: status || undefined, mine, reprice: reprice || undefined } })).data,
  })
}

export function useQuote(id: string | undefined) {
  return useQuery({
    queryKey: ["quote", id],
    queryFn: async () => (await api.get<QuoteDetail>(`/quotes/${id}`)).data,
    enabled: !!id,
  })
}

export function useMaterials() {
  return useQuery({
    queryKey: ["costs", "materials"],
    queryFn: async () => (await api.get<{ at: string; materials: MaterialItem[] }>("/costs/materials")).data,
  })
}

export function useMachines() {
  return useQuery({
    queryKey: ["costs", "machines"],
    queryFn: async () => (await api.get<{ at: string; machines: MachineItem[] }>("/costs/machines")).data,
  })
}

export function useCostModels() {
  return useQuery({
    queryKey: ["costs", "models"],
    queryFn: async () => (await api.get<{ families: CostModelFamily[] }>("/costs/models")).data,
  })
}

export function useCostModel(familyId: string | undefined) {
  return useQuery({
    queryKey: ["costs", "model", familyId],
    queryFn: async () => (await api.get<CostModelDetail>(`/costs/models/${familyId}`)).data,
    enabled: !!familyId,
  })
}

export function usePricePolicies() {
  return useQuery({
    queryKey: ["costs", "price-policies"],
    queryFn: async () => (await api.get<PricePolicyList>("/costs/price-policies")).data,
  })
}

export function usePricePolicy(target: string | undefined) {
  return useQuery({
    queryKey: ["costs", "price-policy", target],
    queryFn: async () => (await api.get<PricePolicyDetail>(`/costs/price-policies/${target}`)).data,
    enabled: !!target,
  })
}
