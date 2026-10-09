"""Step 4 (F12.4): the audit trail's evidence store, seals, retention and messages.

- audit_records gets a format: 1 = Step 2 records (the hash covers everything),
  2 = the hash covers a short skeleton plus the fingerprint of its evidence (R1).
- audit_evidence holds the details (inputs, prompts, notes, personal data),
  encrypted with EVIDENCE_KEYS (Step 4 safeguard 1). Details can be removed
  (retention R5, erasure R6) but never changed: a trigger allows only
  "data_enc -> NULL" with a reason, or re-encryption under a new key while
  forge.evidence_rekey is on (the chain still checks the plaintext's fingerprint).
- audit_seals: signed hourly seals of each tenant's chain head (R3), append-only,
  each linked to the previous seal so a missing or reordered seal is caught.
- agent_versions: one row per distinct agent definition, numbered per agent (R8).
- retention_settings (periods and applicable law per category, legal review) and
  legal_holds (R5); messages (R7); erased_identifiers: keyed fingerprints of erased
  emails and phones so later ERP syncs hide them (R6).
- audit_trim(): the only way to delete old skeleton records, up to a sealed record
  older than the skeleton period (never under 8 years); runs as the owner.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
JSONB = pg.JSONB
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)


def _tenant():
    return sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True)


def upgrade() -> None:
    op.add_column("audit_records", sa.Column("format", sa.SmallInteger, nullable=False,
                                             server_default="1"))
    op.add_column("audit_records", sa.Column("evidence_digest", sa.String(64)))
    op.add_column("agent_runs", sa.Column("agent_version", sa.Integer))

    op.create_table(
        "audit_evidence",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _tenant(),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("retention_class", sa.String(20), nullable=False),
        sa.Column("account_id", UUID, index=True),
        sa.Column("contact_id", UUID, index=True),
        sa.Column("data_enc", sa.Text),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("removed_at", TS),
        sa.Column("removed_reason", sa.String(200)),
        sa.UniqueConstraint("tenant_id", "seq"),
    )
    op.create_table(
        "audit_seals",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _tenant(),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("sealed_at", TS, nullable=False),
        sa.Column("key_id", sa.String(40), nullable=False),
        sa.Column("signature", sa.String(128), nullable=False),
        sa.Column("prev_signature", sa.String(128)),
        sa.UniqueConstraint("tenant_id", "seq"),
    )
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("agent_key", sa.String(60), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("definition", JSONB, nullable=False),
        sa.Column("first_seen_at", TS, server_default=NOW, nullable=False),
        sa.UniqueConstraint("agent_key", "fingerprint"),
        sa.UniqueConstraint("agent_key", "number"),
    )
    op.create_table(
        "retention_settings",
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("profile", sa.String(20), nullable=False),
        sa.Column("periods", JSONB, nullable=False),
        sa.Column("basis", JSONB, nullable=False),
        sa.Column("legal_review", JSONB),
        sa.Column("updated_by", sa.String(100), nullable=False),
        sa.Column("updated_at", TS, server_default=NOW, nullable=False),
    )
    op.create_table(
        "legal_holds",
        sa.Column("id", UUID, primary_key=True),
        _tenant(),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id"), nullable=False, index=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("placed_by", sa.String(100), nullable=False),
        sa.Column("placed_at", TS, server_default=NOW, nullable=False),
        sa.Column("lifted_by", sa.String(100)),
        sa.Column("lifted_at", TS),
    )
    op.create_table(
        "messages",
        sa.Column("id", UUID, primary_key=True),
        _tenant(),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id"), index=True),
        sa.Column("contact_id", UUID, sa.ForeignKey("contacts.id"), index=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False, server_default="out"),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("subject", sa.String(300)),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("ai_generated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("disclosure", sa.String(200)),
        sa.Column("run_id", UUID, index=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.Column("sent_at", TS),
        sa.Column("sent_by", sa.String(100)),
        sa.Column("erased_at", TS),
        sa.CheckConstraint("status in ('draft','sent','failed')", name="ck_messages_status"),
    )

    op.create_table(
        "erased_identifiers",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _tenant(),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("key_id", sa.String(16), nullable=False),
        sa.Column("erased_at", TS, server_default=NOW, nullable=False),
        sa.UniqueConstraint("tenant_id", "kind", "key_id", "fingerprint"),
    )

    for t in ("audit_evidence", "audit_seals", "retention_settings", "legal_holds", "messages",
              "erased_identifiers"):
        enable_tenant_rls(op, t)
    # 0001's default privileges give the app every right on new tables; take back what
    # these must never allow it.
    op.execute("REVOKE DELETE, TRUNCATE ON audit_evidence, retention_settings, legal_holds "
               "FROM forge_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_seals, agent_versions, "
               "erased_identifiers FROM forge_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON audit_evidence, retention_settings, legal_holds "
               "TO forge_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON messages TO forge_app")
    op.execute("GRANT SELECT, INSERT ON audit_seals, agent_versions, erased_identifiers "
               "TO forge_app")
    op.execute("GRANT USAGE ON SEQUENCE audit_evidence_id_seq, audit_seals_id_seq, "
               "agent_versions_id_seq, erased_identifiers_id_seq TO forge_app")

    # Records may be deleted only by audit_trim() (it sets forge.audit_trim and runs as
    # the owner); everything else is still refused, for every role.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_records_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND current_setting('forge.audit_trim', true) = 'on'
             AND current_user <> 'forge_app' THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION 'audit_records is append-only (% refused)', TG_OP;
        END $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_evidence_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF current_setting('forge.audit_trim', true) = 'on'
               AND current_user <> 'forge_app' THEN
              RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit_evidence rows cannot be deleted';
          END IF;
          IF NEW.digest <> OLD.digest OR NEW.seq <> OLD.seq OR NEW.tenant_id <> OLD.tenant_id
             OR NEW.created_at <> OLD.created_at OR NEW.retention_class <> OLD.retention_class
             OR NEW.account_id IS DISTINCT FROM OLD.account_id
             OR NEW.contact_id IS DISTINCT FROM OLD.contact_id THEN
            RAISE EXCEPTION 'audit evidence can only be removed (with a reason), never changed';
          END IF;
          IF OLD.data_enc IS NOT NULL AND NEW.data_enc IS NULL AND NEW.removed_at IS NOT NULL
             AND NEW.removed_reason IS NOT NULL THEN
            RETURN NEW;  -- removed by retention or erasure
          END IF;
          IF OLD.data_enc IS NOT NULL AND NEW.data_enc IS NOT NULL AND NEW.removed_at IS NULL
             AND current_setting('forge.evidence_rekey', true) = 'on' THEN
            RETURN NEW;  -- re-encrypted under a new key
          END IF;
          RAISE EXCEPTION 'audit evidence can only be removed (with a reason), never changed';
        END $$;
        """
    )
    op.execute("CREATE TRIGGER audit_evidence_guard BEFORE UPDATE OR DELETE ON audit_evidence "
               "FOR EACH ROW EXECUTE FUNCTION audit_evidence_guard()")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_seals_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_seals is append-only (% refused)', TG_OP;
        END $$;
        """
    )
    op.execute("CREATE TRIGGER audit_seals_no_update BEFORE UPDATE OR DELETE ON audit_seals "
               "FOR EACH ROW EXECUTE FUNCTION audit_seals_append_only()")

    # Trimming old skeleton records (R5): only up to a sealed record, only when that
    # record is older than the tenant's skeleton period, and never under 8 years.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_trim(p_tenant uuid, p_upto bigint) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
          v_months integer;
          v_at timestamptz;
          v_count integer;
        BEGIN
          PERFORM set_config('app.tenant_id', p_tenant::text, true);
          SELECT greatest(96, coalesce((periods->>'skeleton')::integer, 96)) INTO v_months
            FROM retention_settings WHERE tenant_id = p_tenant;
          v_months := coalesce(v_months, 96);
          IF NOT EXISTS (SELECT 1 FROM audit_seals WHERE tenant_id = p_tenant AND seq = p_upto)
          THEN
            RAISE EXCEPTION 'audit_trim: record % is not sealed', p_upto;
          END IF;
          SELECT created_at INTO v_at FROM audit_records
            WHERE tenant_id = p_tenant AND seq = p_upto;
          IF v_at IS NULL OR v_at >= now() - make_interval(months => v_months) THEN
            RAISE EXCEPTION 'audit_trim: record % is younger than % months', p_upto, v_months;
          END IF;
          PERFORM set_config('forge.audit_trim', 'on', true);
          DELETE FROM audit_evidence WHERE tenant_id = p_tenant AND seq <= p_upto;
          DELETE FROM audit_records WHERE tenant_id = p_tenant AND seq <= p_upto;
          GET DIAGNOSTICS v_count = ROW_COUNT;
          PERFORM set_config('forge.audit_trim', 'off', true);
          RETURN v_count;
        END $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION audit_trim(uuid, bigint) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION audit_trim(uuid, bigint) TO forge_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS audit_trim(uuid, bigint)")
    for t in ("erased_identifiers", "messages", "legal_holds", "retention_settings",
              "agent_versions", "audit_seals", "audit_evidence"):
        op.drop_table(t)
    op.execute("DROP FUNCTION IF EXISTS audit_seals_append_only()")
    op.execute("DROP FUNCTION IF EXISTS audit_evidence_guard()")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_records_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_records is append-only (% refused)', TG_OP;
        END $$;
        """
    )
    op.drop_column("agent_runs", "agent_version")
    op.drop_column("audit_records", "evidence_digest")
    op.drop_column("audit_records", "format")
