/** Mirrors backend app/core/permissions.py: "*" grants everything. */
export function hasPermission(granted: readonly string[], required: string): boolean {
  return granted.includes('*') || granted.includes(required)
}
