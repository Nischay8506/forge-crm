"""Phase 2, M1 (first path): email enquiries, routing, timers and acknowledgements.

Decisions N1-N15 (2026-10-12): one path first, email → duplicate check → owner →
timers → acknowledgement (N1 A); email only for now, WhatsApp (F1.2) and the web form
and marketplaces (F1.3) later (N2 A, N14); Gmail, Microsoft 365 and IMAP readers (N3 A).

- stored_files: raw emails and attachments, encrypted, in a file store outside the
  database (N9 A). Rows are never deleted; removal (retention, erasure) blanks the
  file and records why.
- mailboxes: the one sales/RFQ mailbox a company connects; its password or sign-in
  token is encrypted like ERP credentials and never returned.
- inbound_emails: every email read from the mailbox (or the Test mailbox), with its
  label and what became of it (an enquiry, a reply on one, Other mail, automatic).
- enquiries, enquiry_lines: the enquiry and its requirement lines; enquiry_events and
  enquiry_assignments are its history, appended, never changed.
- enquiry_pairs: a person's decision on two enquiries that looked alike (F1.5), so
  the same question is not asked again.
- product_families: what the company makes, the words that point to each family and
  the questions needed to quote it (N8 A).
- enquiry_settings: routing, timer and intake settings per company (defaults in code).
- sla_timers: the generic SLA engine's timers (F1.10, reused by later modules).
- users get "reports to" and their enquiry profile (N12 A, N13 A); accounts get an
  owner; messages can belong to an enquiry.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
JSONB = pg.JSONB
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)
EMPTY_LIST = sa.text("'[]'::jsonb")
EMPTY_OBJ = sa.text("'{}'::jsonb")

LABELS = "('enquiry','complaint','payment','order_status','vendor_mail','spam')"
STATUSES = "('new','open','waiting_customer','parked','closed','merged')"


def _now(name: str) -> sa.Column:
    return sa.Column(name, TS, server_default=NOW, nullable=False)


def upgrade() -> None:
    op.create_table(
        "stored_files",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(15), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(150), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("store", sa.String(10), nullable=False),
        sa.Column("storage_key", sa.String(300), nullable=False),
        sa.Column("key_id", sa.String(16), nullable=False),
        sa.Column("wrapped_key", sa.Text, nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        _now("created_at"),
        sa.Column("removed_at", TS),
        sa.Column("removed_reason", sa.String(200)),
        sa.CheckConstraint("kind IN ('raw_email','attachment','upload')",
                           name="ck_stored_files_kind"),
    )

    op.create_table(
        "mailboxes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("provider", sa.String(12), nullable=False),
        sa.Column("address", sa.String(320), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("imap_host", sa.String(255)),
        sa.Column("imap_port", sa.Integer),
        sa.Column("imap_security", sa.String(10)),
        sa.Column("username", sa.String(320)),
        sa.Column("folder", sa.String(100), nullable=False, server_default="INBOX"),
        sa.Column("secret_enc", sa.Text, nullable=False),
        sa.Column("secret_hint", sa.String(12), nullable=False),
        sa.Column("cursor", JSONB, nullable=False, server_default=EMPTY_OBJ),
        sa.Column("last_poll_at", TS),
        sa.Column("last_ok_at", TS),
        sa.Column("last_error", sa.String(500)),
        sa.Column("last_error_at", TS),
        sa.Column("failures", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("poll_requested_at", TS),
        sa.Column("push_ref", sa.String(300)),
        sa.Column("push_expires_at", TS),
        sa.Column("push_secret_hash", sa.String(64)),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint("provider IN ('imap','gmail','microsoft')",
                           name="ck_mailboxes_provider"),
        sa.CheckConstraint("status IN ('active','paused','broken')", name="ck_mailboxes_status"),
        sa.CheckConstraint("imap_security IS NULL OR imap_security IN ('ssl','starttls')",
                           name="ck_mailboxes_security"),
    )

    op.create_table(
        "product_families",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("template", sa.String(30)),
        sa.Column("keywords", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("checklist", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        _now("created_at"),
        _now("updated_at"),
    )
    op.create_index("uq_product_families_name", "product_families",
                    ["tenant_id", sa.text("lower(name)")], unique=True)

    op.add_column("users", sa.Column("reports_to_id", UUID, sa.ForeignKey("users.id")))
    op.add_column("users", sa.Column("takes_enquiries", sa.Boolean, nullable=False,
                                     server_default=sa.false()))
    op.add_column("users", sa.Column("product_family_ids", JSONB, nullable=False,
                                     server_default=EMPTY_LIST))
    op.add_column("users", sa.Column("regions", JSONB, nullable=False,
                                     server_default=EMPTY_LIST))
    op.add_column("users", sa.Column("max_open_enquiries", sa.SmallInteger))
    op.create_check_constraint("ck_users_reports_to_self", "users",
                               "reports_to_id IS NULL OR reports_to_id <> id")
    op.add_column("accounts", sa.Column("owner_id", UUID, sa.ForeignKey("users.id"),
                                        index=True))

    op.create_table(
        "enquiries",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("ref", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("summary", sa.String(300)),
        sa.Column("language", sa.String(10)),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id"), index=True),
        sa.Column("contact_id", UUID, sa.ForeignKey("contacts.id"), index=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), index=True),
        sa.Column("assigned_rule", sa.String(20)),
        sa.Column("assigned_at", TS),
        sa.Column("customer", JSONB, nullable=False, server_default=EMPTY_OBJ),
        sa.Column("details", JSONB, nullable=False, server_default=EMPTY_OBJ),
        sa.Column("is_tender", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("needs_review", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("review_reasons", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("product_family_id", UUID, sa.ForeignKey("product_families.id")),
        sa.Column("waiting_on", sa.String(10), nullable=False, server_default="us"),
        sa.Column("questions", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("first_response_at", TS),
        sa.Column("first_response_by", sa.String(100)),
        sa.Column("owner_seen_at", TS),
        sa.Column("quote_sent_at", TS),
        sa.Column("quote_sent_by", sa.String(100)),
        sa.Column("nudges", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("last_nudge_at", TS),
        sa.Column("waiting_since", TS),
        sa.Column("parked_at", TS),
        sa.Column("possible_duplicate_of_id", UUID, sa.ForeignKey("enquiries.id")),
        sa.Column("duplicate_score", sa.SmallInteger),
        sa.Column("duplicate_reasons", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("merged_into_id", UUID, sa.ForeignKey("enquiries.id"), index=True),
        sa.Column("merged_at", TS),
        sa.Column("merged_by", sa.String(100)),
        sa.Column("closed_at", TS),
        sa.Column("closed_by", sa.String(100)),
        sa.Column("close_reason", sa.String(300)),
        sa.Column("erased_at", TS),
        sa.Column("created_by", sa.String(100), nullable=False),
        _now("created_at"),
        _now("updated_at"),
        _now("last_activity_at"),
        sa.UniqueConstraint("tenant_id", "seq", name="uq_enquiries_seq"),
        sa.CheckConstraint(f"status IN {STATUSES}", name="ck_enquiries_status"),
        sa.CheckConstraint("source IN ('email','manual')", name="ck_enquiries_source"),
        sa.CheckConstraint("waiting_on IN ('us','customer')", name="ck_enquiries_waiting"),
        sa.CheckConstraint("merged_into_id IS NULL OR merged_into_id <> id",
                           name="ck_enquiries_merge_self"),
    )
    op.create_index("ix_enquiries_open", "enquiries", ["tenant_id", "status", "created_at"])

    op.create_table(
        "enquiry_lines",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("enquiry_id", UUID, sa.ForeignKey("enquiries.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("item_ref", sa.String(100)),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("material", sa.String(200)),
        sa.Column("dimensions", sa.String(300)),
        sa.Column("qty", sa.Numeric(14, 3)),
        sa.Column("unit", sa.String(20)),
        sa.Column("standard", sa.String(200)),
        sa.Column("notes", sa.String(1000)),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("source_ref", sa.String(100)),
        sa.Column("original", JSONB),
        sa.Column("corrected_by", sa.String(100)),
        sa.Column("corrected_at", TS),
        sa.Column("removed_at", TS),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint("source IN ('email','reply','person','attachment')",
                           name="ck_enquiry_lines_source"),
    )

    op.create_table(
        "inbound_emails",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("mailbox_id", UUID, sa.ForeignKey("mailboxes.id")),
        sa.Column("provider", sa.String(12), nullable=False),
        sa.Column("provider_id", sa.String(300), nullable=False),
        sa.Column("internet_message_id", sa.String(500)),
        sa.Column("thread_key", sa.String(500)),
        sa.Column("in_reply_to", sa.String(500)),
        sa.Column("references", sa.Text),
        sa.Column("from_address", sa.String(320)),
        sa.Column("from_name", sa.String(200)),
        sa.Column("to_addresses", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("cc_addresses", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("subject", sa.String(500)),
        sa.Column("body_text", sa.Text, nullable=False, server_default=""),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("raw_file_id", UUID, sa.ForeignKey("stored_files.id")),
        sa.Column("attachments", JSONB, nullable=False, server_default=EMPTY_LIST),
        sa.Column("auto_kind", sa.String(12)),
        sa.Column("label", sa.String(15)),
        sa.Column("label_confidence", sa.Numeric(4, 3)),
        sa.Column("label_source", sa.String(8)),
        sa.Column("label_reason", sa.String(300)),
        sa.Column("could_be_enquiry", sa.Boolean),
        sa.Column("original_label", sa.String(15)),
        sa.Column("relabelled_by", sa.String(100)),
        sa.Column("relabelled_at", TS),
        sa.Column("stage", sa.String(10), nullable=False, server_default="new"),
        sa.Column("outcome", sa.String(10)),
        sa.Column("attempts", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", TS),
        sa.Column("last_error", sa.String(500)),
        sa.Column("enquiry_id", UUID, sa.ForeignKey("enquiries.id"), index=True),
        sa.Column("message_id", UUID, sa.ForeignKey("messages.id")),
        sa.Column("contact_id", UUID, sa.ForeignKey("contacts.id"), index=True),
        sa.Column("run_id", UUID),
        sa.Column("erased_at", TS),
        _now("created_at"),
        _now("updated_at"),
        sa.UniqueConstraint("tenant_id", "provider", "provider_id",
                            name="uq_inbound_emails_provider"),
        sa.CheckConstraint("provider IN ('test','imap','gmail','microsoft')",
                           name="ck_inbound_emails_provider"),
        sa.CheckConstraint(f"label IS NULL OR label IN {LABELS}", name="ck_inbound_emails_label"),
        sa.CheckConstraint(f"original_label IS NULL OR original_label IN {LABELS}",
                           name="ck_inbound_emails_original_label"),
        sa.CheckConstraint("label_source IS NULL OR label_source IN "
                           "('model','rules','person','none')",
                           name="ck_inbound_emails_label_source"),
        sa.CheckConstraint("auto_kind IS NULL OR auto_kind IN "
                           "('bounce','auto_reply','bulk','own')",
                           name="ck_inbound_emails_auto"),
        sa.CheckConstraint("stage IN ('new','done','failed')", name="ck_inbound_emails_stage"),
        sa.CheckConstraint("outcome IS NULL OR outcome IN "
                           "('enquiry','reply','other','auto','receipt')",
                           name="ck_inbound_emails_outcome"),
    )
    op.create_index("ix_inbound_emails_msgid", "inbound_emails",
                    ["tenant_id", "internet_message_id"])
    op.create_index("ix_inbound_emails_thread", "inbound_emails", ["tenant_id", "thread_key"])
    op.create_index("ix_inbound_emails_due", "inbound_emails", ["stage", "next_attempt_at"],
                    postgresql_where=sa.text("stage = 'new'"))
    op.create_index("ix_inbound_emails_list", "inbound_emails",
                    ["tenant_id", "outcome", "received_at"])

    op.add_column("messages", sa.Column("enquiry_id", UUID, sa.ForeignKey("enquiries.id"),
                                        index=True))

    op.create_table(
        "enquiry_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("enquiry_id", UUID, sa.ForeignKey("enquiries.id"), nullable=False,
                  index=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("detail", JSONB, nullable=False, server_default=EMPTY_OBJ),
        sa.Column("at", TS, server_default=NOW, nullable=False),
    )

    op.create_table(
        "enquiry_assignments",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("enquiry_id", UUID, sa.ForeignKey("enquiries.id"), nullable=False,
                  index=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("previous_user_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("rule", sa.String(20), nullable=False),
        sa.Column("overrode_rule", sa.String(20)),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("at", TS, server_default=NOW, nullable=False),
        sa.CheckConstraint("rule IN ('account_owner','product_line','region','round_robin',"
                           "'fallback','manual','away','merge','none')",
                           name="ck_enquiry_assignments_rule"),
    )
    op.create_index("ix_enquiry_assignments_tenant", "enquiry_assignments",
                    ["tenant_id", "at"])

    op.create_table(
        "enquiry_pairs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("first_id", UUID, sa.ForeignKey("enquiries.id"), nullable=False),
        sa.Column("second_id", UUID, sa.ForeignKey("enquiries.id"), nullable=False),
        sa.Column("decision", sa.String(15), nullable=False),
        sa.Column("decided_by", sa.String(100), nullable=False),
        _now("decided_at"),
        sa.UniqueConstraint("tenant_id", "first_id", "second_id", name="uq_enquiry_pairs"),
        sa.CheckConstraint("first_id < second_id", name="ck_enquiry_pairs_order"),
        sa.CheckConstraint("decision IN ('different','same')", name="ck_enquiry_pairs_decision"),
    )

    op.create_table(
        "enquiry_settings",
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("routing", JSONB, nullable=False),
        sa.Column("timers", JSONB, nullable=False),
        sa.Column("intake", JSONB, nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        _now("updated_at"),
    )

    op.create_table(
        "sla_timers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", UUID, nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("clock", sa.String(8), nullable=False),
        sa.Column("target_seconds", sa.Integer, nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("used_seconds", sa.Integer, nullable=False, server_default="0"),
        sa.Column("running_since", TS),
        sa.Column("remind_at", TS),
        sa.Column("due_at", TS),
        sa.Column("double_at", TS),
        sa.Column("paused_at", TS),
        sa.Column("reminded_at", TS),
        sa.Column("breached_at", TS),
        sa.Column("escalated_at", TS),
        sa.Column("escalated2_at", TS),
        sa.Column("stopped_at", TS),
        sa.Column("outcome", sa.String(10)),
        _now("created_at"),
        sa.CheckConstraint("clock IN ('always','working')", name="ck_sla_timers_clock"),
        sa.CheckConstraint("outcome IS NULL OR outcome IN ('met','missed','cancelled')",
                           name="ck_sla_timers_outcome"),
    )
    op.create_index("ix_sla_timers_subject", "sla_timers",
                    ["tenant_id", "subject_type", "subject_id"])
    op.create_index("uq_sla_timers_running", "sla_timers",
                    ["tenant_id", "subject_type", "subject_id", "stage"], unique=True,
                    postgresql_where=sa.text("stopped_at IS NULL"))
    op.create_index("ix_sla_timers_due", "sla_timers", ["remind_at", "due_at", "double_at"],
                    postgresql_where=sa.text("stopped_at IS NULL AND paused_at IS NULL"))

    for table in ("stored_files", "mailboxes", "product_families", "enquiries",
                  "enquiry_lines", "inbound_emails", "enquiry_events", "enquiry_assignments",
                  "enquiry_pairs", "enquiry_settings", "sla_timers"):
        enable_tenant_rls(op, table)
    # History is appended to, never rewritten.
    for table in ("enquiry_events", "enquiry_assignments"):
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM forge_app")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO forge_app")
    op.execute("GRANT USAGE ON SEQUENCE enquiry_events_id_seq, enquiry_assignments_id_seq "
               "TO forge_app")
    # Files are removed by blanking the stored file and recording why, never by
    # deleting the row; re-wrapping a file's key under a new master key is allowed.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON stored_files FROM forge_app")
    op.execute("GRANT SELECT, INSERT ON stored_files TO forge_app")
    op.execute("GRANT UPDATE (removed_at, removed_reason, key_id, wrapped_key) "
               "ON stored_files TO forge_app")
    op.execute("REVOKE DELETE, TRUNCATE ON enquiries, inbound_emails, enquiry_settings "
               "FROM forge_app")


def downgrade() -> None:
    op.drop_column("messages", "enquiry_id")
    for table in ("sla_timers", "enquiry_settings", "enquiry_pairs", "enquiry_assignments",
                  "enquiry_events", "inbound_emails", "enquiry_lines"):
        op.drop_table(table)
    op.drop_column("accounts", "owner_id")
    op.drop_constraint("ck_users_reports_to_self", "users")
    for col in ("max_open_enquiries", "regions", "product_family_ids", "takes_enquiries",
                "reports_to_id"):
        op.drop_column("users", col)
    op.drop_table("enquiries")
    op.drop_table("product_families")
    op.drop_table("mailboxes")
    op.drop_table("stored_files")
