import * as React from "react"
import { useNavigate, useLocation } from "react-router-dom"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/Card"
import { Button } from "@/components/ui/Button"
import { useAuthStore } from "@/stores/authStore"
import { errorMessage } from "@/config/api"

const inputClass =
  "w-full rounded-md border border-input bg-transparent px-3 py-2 text-[13.5px] shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-teal-500"

export function LoginPage() {
  const [company, setCompany] = React.useState("")
  const [email, setEmail] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [isLoading, setIsLoading] = React.useState(false)
  const [error, setError] = React.useState("")
  const login = useAuthStore((s) => s.login)
  const navigate = useNavigate()
  const location = useLocation()
  const from = location.state?.from?.pathname || "/"

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsLoading(true)
    setError("")
    try {
      await login(company.trim(), email.trim(), password)
      navigate(from, { replace: true })
    } catch (err) {
      setError(errorMessage(err, "Login failed. Please check your details."))
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface-0">
      <Card className="w-full max-w-[400px]">
        <CardHeader className="text-center pb-2">
          <div className="font-disp font-bold text-ink-700 text-2xl mb-1">
            FORGE<b className="text-gold-500 mx-[1px]">·</b>CRM
          </div>
          <CardTitle>Sign in to your account</CardTitle>
        </CardHeader>
        <CardContent className="pt-4">
          <form onSubmit={onSubmit} className="space-y-4">
            {error && (
              <div className="bg-red-100 text-red-500 text-sm p-3 rounded-md">{error}</div>
            )}
            <label className="block space-y-1.5">
              <span className="text-[12px] font-semibold text-ink-700">Company code</span>
              <input value={company} onChange={(e) => setCompany(e.target.value)} required
                autoComplete="organization" className={inputClass} />
            </label>
            <label className="block space-y-1.5">
              <span className="text-[12px] font-semibold text-ink-700">Work email</span>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
                autoComplete="email" className={inputClass} />
            </label>
            <label className="block space-y-1.5">
              <span className="text-[12px] font-semibold text-ink-700">Password</span>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                required autoComplete="current-password" className={inputClass} />
            </label>
            <Button type="submit" className="w-full mt-2" disabled={isLoading}>
              {isLoading ? "Signing in..." : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
