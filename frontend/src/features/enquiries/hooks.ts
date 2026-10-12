import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import type { Counts, EnquiryDetail, EnquirySettings, FamiliesView, MailboxSetup, Person } from "./types"

/** How many enquiries each view holds, and Other mail waiting to be looked at. */
export function useEnquiryCounts(enabled = true) {
  return useQuery({
    queryKey: ["enquiries", "counts"],
    queryFn: async () => (await api.get<Counts>("/enquiries/counts")).data,
    enabled,
    refetchInterval: 30_000,
  })
}

/** One enquiry with its lines, emails, messages, timers and history. */
export function useEnquiry(id: string | undefined) {
  return useQuery({
    queryKey: ["enquiry", id],
    queryFn: async () => (await api.get<EnquiryDetail>(`/enquiries/${id}`)).data,
    enabled: !!id,
    refetchInterval: 20_000,
  })
}

export function useEnquirySettings() {
  return useQuery({
    queryKey: ["enquiries", "settings"],
    queryFn: async () => (await api.get<EnquirySettings>("/enquiries/setup/settings")).data,
    staleTime: 60_000,
  })
}

/** Everyone, with what they take, their manager, their open enquiries and whether they are away. */
export function usePeople() {
  return useQuery({
    queryKey: ["enquiries", "people"],
    queryFn: async () => (await api.get<Person[]>("/enquiries/setup/people")).data,
    staleTime: 60_000,
  })
}

export function useFamilies() {
  return useQuery({
    queryKey: ["enquiries", "families"],
    queryFn: async () => (await api.get<FamiliesView>("/enquiries/setup/families")).data,
    staleTime: 60_000,
  })
}

export function useMailbox() {
  return useQuery({
    queryKey: ["enquiries", "mailbox"],
    queryFn: async () => (await api.get<MailboxSetup>("/enquiries/setup/mailbox")).data,
    refetchInterval: 30_000,
  })
}
