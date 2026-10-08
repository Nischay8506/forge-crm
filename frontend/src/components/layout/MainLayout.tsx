import { Outlet, Navigate, useLocation } from "react-router-dom"
import { AppBar } from "./AppBar"
import { useAuthStore } from "@/stores/authStore"

export function MainLayout() {
  const isAuthenticated = useAuthStore((s) => s.accessToken !== null)
  const location = useLocation()
  if (!isAuthenticated) return <Navigate to="/login" state={{ from: location }} replace />

  return (
    <div className="min-h-screen flex flex-col bg-background font-body text-foreground">
      <AppBar />
      <main className="flex-1 max-w-[1280px] w-full mx-auto px-6 py-6">
        <Outlet />
      </main>
    </div>
  )
}
