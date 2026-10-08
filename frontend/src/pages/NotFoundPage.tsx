import { Link } from "react-router-dom"

export function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center h-[60vh] gap-2">
      <h2 className="font-disp font-bold text-2xl text-ink-700">Page not found</h2>
      <Link to="/" className="text-teal-600 text-sm">Back to home</Link>
    </div>
  )
}
