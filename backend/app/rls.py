"""Helpers for migrations. EVERY new tenant-owned table must call
`enable_tenant_rls(op, "<table>")` in the migration that creates it;
tests/test_tenancy.py fails if a table with tenant_id has no RLS."""

TENANT_PREDICATE = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"


def enable_tenant_rls(op, table: str, *, allow_system: bool = False) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    if allow_system:
        op.execute(
            f"CREATE POLICY system_access ON {table} "
            "USING (current_setting('app.system', true) = 'on')"
        )
