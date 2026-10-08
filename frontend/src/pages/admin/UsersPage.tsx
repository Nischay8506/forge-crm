import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input } from "@/components/ui/Field"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { usePermissions } from "@/stores/authStore"

interface RoleRef { id: string; name: string }
interface UserRow { id: string; email: string; full_name: string; is_active: boolean; roles: RoleRef[] }
interface RoleRow { id: string; name: string; description: string | null; permissions: string[]; user_count: number }
interface Permission { code: string; description: string }

function RoleChecks({ roles, value, onChange }: { roles: RoleRow[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1">
      {roles.map((r) => (
        <label key={r.id} className="flex items-center gap-1 text-[12.5px]">
          <input type="checkbox" checked={value.includes(r.id)}
            onChange={(e) => onChange(e.target.checked ? [...value, r.id] : value.filter((x) => x !== r.id))} />
          {r.name}
        </label>
      ))}
    </div>
  )
}

function UserRoles({ user, roles }: { user: UserRow; roles: RoleRow[] }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(user.roles.map((r) => r.id))
  const save = useMutation({
    mutationFn: async () => api.put(`/users/${user.id}/roles`, { role_ids: value }),
    onSuccess: () => { setEditing(false); qc.invalidateQueries({ queryKey: ["users"] }); qc.invalidateQueries({ queryKey: ["roles"] }) },
  })
  if (!editing) {
    return (
      <div className="flex items-center gap-2">
        {user.roles.map((r) => <Tag key={r.id}>{r.name}</Tag>)}
        <button className="text-[12px] text-teal-600 hover:underline" onClick={() => setEditing(true)}>Change</button>
      </div>
    )
  }
  return (
    <div className="space-y-1">
      <RoleChecks roles={roles} value={value} onChange={setValue} />
      <div className="flex gap-2">
        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
        <Button size="sm" variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
      </div>
      <ErrorText error={save.error} fallback="Could not change roles" />
    </div>
  )
}

function AddUser({ roles }: { roles: RoleRow[] }) {
  const qc = useQueryClient()
  const [v, setV] = useState({ full_name: "", email: "", password: "", role_ids: [] as string[] })
  const add = useMutation({
    mutationFn: async () => api.post("/users", v),
    onSuccess: () => { setV({ full_name: "", email: "", password: "", role_ids: [] }); qc.invalidateQueries({ queryKey: ["users"] }) },
  })
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
      <Field label="Name"><Input required value={v.full_name} onChange={(e) => setV({ ...v, full_name: e.target.value })} /></Field>
      <Field label="Email"><Input required type="email" value={v.email} onChange={(e) => setV({ ...v, email: e.target.value })} /></Field>
      <Field label="First password" hint="At least 8 characters. Share it privately."><Input required minLength={8} type="password" value={v.password} onChange={(e) => setV({ ...v, password: e.target.value })} /></Field>
      <Field label="Roles" group><RoleChecks roles={roles} value={v.role_ids} onChange={(role_ids) => setV({ ...v, role_ids })} /></Field>
      <Button type="submit" disabled={add.isPending}>Add user</Button>
      <ErrorText error={add.error} fallback="Could not add the user" />
    </form>
  )
}

function AddRole({ permissions }: { permissions: Permission[] }) {
  const qc = useQueryClient()
  const [name, setName] = useState("")
  const [perms, setPerms] = useState<string[]>([])
  const add = useMutation({
    mutationFn: async () => api.post("/roles", { name, permissions: perms }),
    onSuccess: () => { setName(""); setPerms([]); qc.invalidateQueries({ queryKey: ["roles"] }) },
  })
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
      <Field label="Role name" hint="e.g. Finance, Sales Head. Policy rules can name a role to decide."><Input required value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <div className="space-y-1 max-h-64 overflow-y-auto">
        {permissions.filter((p) => p.code !== "*").map((p) => (
          <label key={p.code} className="flex items-start gap-2 text-[12.5px]">
            <input type="checkbox" className="mt-0.5" checked={perms.includes(p.code)}
              onChange={(e) => setPerms(e.target.checked ? [...perms, p.code] : perms.filter((x) => x !== p.code))} />
            <span>{p.description} <span className="font-mono text-[10.5px] text-ink-300">{p.code}</span></span>
          </label>
        ))}
      </div>
      <Button type="submit" disabled={add.isPending || !name.trim()}>Create role</Button>
      {errorProblems(add.error).map((p) => <p key={p} className="text-red-500 text-[12px]">{p}</p>)}
      <ErrorText error={errorProblems(add.error).length ? null : add.error} fallback="Could not create the role" />
    </form>
  )
}

export function UsersPage() {
  const perms = usePermissions()
  const canManageUsers = hasPermission(perms, "users.manage")
  const canManageRoles = hasPermission(perms, "roles.manage")
  const users = useQuery({ queryKey: ["users"], queryFn: async () => (await api.get<UserRow[]>("/users")).data })
  const roles = useQuery({ queryKey: ["roles"], queryFn: async () => (await api.get<RoleRow[]>("/roles")).data })
  const permissions = useQuery({
    queryKey: ["permissions"], queryFn: async () => (await api.get<Permission[]>("/permissions")).data,
    enabled: canManageRoles,
  })

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-ink-900">Users</h1>
      {users.isLoading && <Loading />}
      <ErrorText error={users.error ?? roles.error} fallback="Could not load users" />
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-4 items-start">
        <div className="space-y-4">
          {users.data && (
            <Card>
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left text-ink-500 border-b border-line">
                    <th className="p-3 font-semibold">Name</th>
                    <th className="p-3 font-semibold">Email</th>
                    <th className="p-3 font-semibold">Roles</th>
                    <th className="p-3 font-semibold">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {users.data.map((u) => (
                    <tr key={u.id} className="border-b border-line last:border-0 align-top">
                      <td className="p-3">{u.full_name}</td>
                      <td className="p-3">{u.email}</td>
                      <td className="p-3">
                        {canManageUsers && roles.data ? <UserRoles user={u} roles={roles.data} />
                          : u.roles.map((r) => <Tag key={r.id} className="mr-1">{r.name}</Tag>)}
                      </td>
                      <td className="p-3">
                        <Tag variant={u.is_active ? "green" : "neutral"}>{u.is_active ? "Active" : "Inactive"}</Tag>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
          {roles.data && (
            <Card>
              <CardHeader><CardTitle>Roles</CardTitle></CardHeader>
              <CardContent>
                <ul className="divide-y divide-line text-[13px]">
                  {roles.data.map((r) => (
                    <li key={r.id} className="py-2">
                      <div className="flex items-center gap-2"><b>{r.name}</b><span className="text-ink-300 text-[12px]">{r.user_count} user(s)</span></div>
                      <div className="text-[12px] text-ink-500">
                        {r.permissions.includes("*") ? "Full access" : r.permissions.join(", ") || "No permissions"}
                      </div>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>
        <div className="space-y-4">
          {canManageUsers && roles.data && (
            <Card><CardHeader><CardTitle>Add a user</CardTitle></CardHeader><CardContent><AddUser roles={roles.data} /></CardContent></Card>
          )}
          {canManageRoles && permissions.data && (
            <Card><CardHeader><CardTitle>New role</CardTitle></CardHeader><CardContent><AddRole permissions={permissions.data} /></CardContent></Card>
          )}
        </div>
      </div>
    </div>
  )
}
