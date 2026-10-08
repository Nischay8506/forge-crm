import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import axios from 'axios'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1'

/** Matches GET /api/v1/auth/me */
export interface Me {
  id: string
  email: string
  full_name: string
  tenant_id: string
  permissions: string[]
}

interface AuthState {
  me: Me | null
  accessToken: string | null
  refreshToken: string | null
  login: (company: string, email: string, password: string) => Promise<void>
  refresh: () => Promise<void>
  logout: () => void
}

async function fetchMe(accessToken: string): Promise<Me> {
  const r = await axios.get<Me>(`${API_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  return r.data
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      me: null,
      accessToken: null,
      refreshToken: null,

      login: async (company, email, password) => {
        const r = await axios.post(`${API_URL}/auth/login`, { company, email, password })
        const me = await fetchMe(r.data.access_token)
        set({ me, accessToken: r.data.access_token, refreshToken: r.data.refresh_token })
      },

      // Plain axios (not the api client) so a failing refresh cannot loop through the interceptor.
      refresh: async () => {
        const token = get().refreshToken
        if (!token) throw new Error('No refresh token')
        const r = await axios.post(`${API_URL}/auth/refresh`, { refresh_token: token })
        const me = await fetchMe(r.data.access_token)
        set({ me, accessToken: r.data.access_token, refreshToken: r.data.refresh_token })
      },

      logout: () => set({ me: null, accessToken: null, refreshToken: null }),
    }),
    { name: 'forge-crm-auth' }
  )
)

export const usePermissions = () => useAuthStore((s) => s.me?.permissions ?? [])
