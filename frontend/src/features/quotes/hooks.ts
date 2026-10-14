import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import type { MachineItem, MaterialItem, QuoteDetail, QuoteRow } from "./types"

export function useQuotes(status: string, mine: boolean) {
  return useQuery({
    queryKey: ["quotes", status, mine],
    queryFn: async () => (await api.get<{ quotes: QuoteRow[] }>("/quotes", { params: { status: status || undefined, mine } })).data,
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
