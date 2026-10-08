import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { useAuthStore } from "@/stores/authStore"

export function HomePage() {
  const me = useAuthStore((s) => s.me)
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-ink-900">Welcome, {me?.full_name}</h1>
      <Card>
        <CardHeader><CardTitle>Platform foundations in progress</CardTitle></CardHeader>
        <CardContent className="text-[13.5px] text-ink-500">
          Modules appear in the navigation as they are built and as your role allows.
        </CardContent>
      </Card>
    </div>
  )
}
