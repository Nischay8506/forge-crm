import { cn } from "@/lib/utils"
import { Card } from "./Card"

export interface KpiCardProps {
  value: string | number
  label: string
  trend?: string
  trendDirection?: 'up' | 'down' | 'neutral'
  className?: string
}

export function KpiCard({ value, label, trend, trendDirection = 'neutral', className }: KpiCardProps) {
  return (
    <Card className={cn("p-[16px_18px] flex flex-col gap-[2px]", className)}>
      <span className="font-disp text-[26px] font-bold text-ink-900 leading-none">{value}</span>
      <span className="text-[11.5px] text-ink-300 mb-1">{label}</span>
      
      {trend && (
        <span className={cn(
          "text-[11px] font-semibold mt-auto",
          trendDirection === 'up' && "text-green-500",
          trendDirection === 'down' && "text-red-500",
          trendDirection === 'neutral' && "text-ink-500"
        )}>
          {trendDirection === 'up' && '▲ '}
          {trendDirection === 'down' && '▼ '}
          {trend}
        </span>
      )}
    </Card>
  )
}
