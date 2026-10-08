import axios from 'axios'
import { useAuthStore } from '@/stores/authStore'

export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1'

export const api = axios.create({ baseURL: API_URL, headers: { 'Content-Type': 'application/json' } })

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// On 401, try one refresh, then retry the request; otherwise log out.
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config
    if (error.response?.status === 401 && original && !original._retry) {
      original._retry = true
      try {
        await useAuthStore.getState().refresh()
        original.headers.Authorization = `Bearer ${useAuthStore.getState().accessToken}`
        return api(original)
      } catch (refreshError) {
        useAuthStore.getState().logout()
        return Promise.reject(refreshError)
      }
    }
    return Promise.reject(error)
  }
)

/** Backend errors look like {"error": {"code", "message"}}. */
export function errorMessage(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) return err.response?.data?.error?.message ?? fallback
  return fallback
}
