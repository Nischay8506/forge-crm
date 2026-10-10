import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import type { ChannelsView, LanguagesView, MessageDetail } from "./types"

/** Channel settings, the email account and whether the writer and speech keys are set. */
export function useChannels() {
  return useQuery({
    queryKey: ["channels"],
    queryFn: async () => (await api.get<ChannelsView>("/channels/settings")).data,
    staleTime: 60_000,
  })
}

/** Recognised languages, tone guides, the glossary and the review report. */
export function useLanguages() {
  return useQuery({
    queryKey: ["languages"],
    queryFn: async () => (await api.get<LanguagesView>("/languages")).data,
    staleTime: 60_000,
  })
}

/** One message with its events, reviews and thread. Refreshes while it is on its way. */
export function useMessage(id: string | undefined) {
  return useQuery({
    queryKey: ["message", id],
    queryFn: async () => (await api.get<MessageDetail>(`/messages/${id}`)).data,
    enabled: !!id,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s && ["queued", "sending"].includes(s) ? 5_000 : false
    },
  })
}
