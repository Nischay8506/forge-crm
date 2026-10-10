"""Step 6 (F12.5): one sending path for every message, consent, limits and languages.

Decisions L1-L11 (2026-10-10): email only for now (WhatsApp and SMS later), over the
company's own SMTP mailbox (L2 A); messages written natively per language with a tone
guide and glossary (L5 A, L7 A); per-contact limits and quiet hours (L8 A); consent
per contact and channel (L9 A); a simulator first (L11 A).

- messages: the address used, the provider and its id for the message (to match
  receipts), the language and script, its purpose (promotional, transactional, a
  reply, or a notification to a member of staff), which feature asked for it, a key
  that stops the same message being queued twice, the message it replies to, when it
  may go (a held message), how many tries, the checks it last passed or failed,
  why it is held, failed or stopped, and who sent it anyway past quiet hours or limits. Statuses widen to the common set every
  channel's receipts map onto, plus "review" (waiting for a person to check the
  writing).
- message_events: each step of a message's life, from Forge, the provider, the
  simulator or a person. They carry no personal data: provider reasons are cleaned
  of addresses before they are stored. Appended, never changed.
- consents: per contact and channel, when, how and who recorded it. Appended; only
  the free text can be blanked (erasure) and the contact re-pointed (merges).
- channel_settings: each company's limits, quiet hours, test mode and sampling.
- email_accounts: the company's SMTP mailbox; the password is encrypted like ERP
  credentials (CREDENTIAL_KEYS) and never returned.
- tone_guides, glossary_terms: how each language should sound, and the terms that
  are kept, translated one fixed way, or never used.
- language_reviews: a person checking the writing of an AI-written message.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.rls import enable_tenant_rls

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
JSONB = pg.JSONB
NOW = sa.text("now()")
TS = sa.DateTime(timezone=True)

STATUSES = ("draft", "review", "queued", "held", "sending", "sent", "delivered", "read",
            "failed", "bounced", "blocked", "cancelled", "received")
_LIST = ",".join(f"'{s}'" for s in STATUSES)
PURPOSES = "('promotional','transactional','reply','staff')"

NEW_COLUMNS = (
    ("to_address", sa.String(320)),
    ("from_address", sa.String(320)),
    ("language", sa.String(10)),
    ("script", sa.String(10)),
    ("purpose", sa.String(15)),
    ("provider", sa.String(30)),
    ("provider_ref", sa.String(200)),
    ("status_at", TS),
    ("reason", sa.String(500)),
    ("send_after", TS),
    ("idempotency_key", sa.String(100)),
    ("feature", sa.String(60)),
    ("link_token_hash", sa.String(64)),
    ("override_by", sa.String(100)),
)


def _now_col(name: str) -> sa.Column:
    return sa.Column(name, TS, server_default=NOW, nullable=False)


def upgrade() -> None:
    for name, type_ in NEW_COLUMNS:
        op.add_column("messages", sa.Column(name, type_))
    op.add_column("messages", sa.Column("in_reply_to_id", UUID, sa.ForeignKey("messages.id")))
    op.add_column("messages", sa.Column("user_id", UUID, sa.ForeignKey("users.id")))
    op.add_column("messages", sa.Column("attempts", sa.SmallInteger, nullable=False,
                                        server_default="0"))
    op.add_column("messages", sa.Column("checks", JSONB, nullable=False,
                                        server_default=sa.text("'[]'::jsonb")))
    op.execute("UPDATE messages SET status_at = created_at")
    op.drop_constraint("ck_messages_status", "messages")
    op.create_check_constraint("ck_messages_status", "messages", f"status IN ({_LIST})")
    op.create_check_constraint("ck_messages_direction", "messages", "direction IN ('out','in')")
    op.create_check_constraint("ck_messages_purpose", "messages",
                               f"purpose IS NULL OR purpose IN {PURPOSES}")
    op.create_index("ix_messages_idempotency", "messages", ["tenant_id", "idempotency_key"],
                    unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"))
    op.create_index("ix_messages_provider_ref", "messages",
                    ["tenant_id", "provider", "provider_ref"], unique=True,
                    postgresql_where=sa.text("provider_ref IS NOT NULL"))
    op.create_index("ix_messages_due", "messages", ["status", "send_after"],
                    postgresql_where=sa.text("status IN ('queued','held','sending')"))
    op.create_index("ix_messages_contact_sent", "messages", ["contact_id", "sent_at"])

    op.create_table(
        "message_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("message_id", UUID, sa.ForeignKey("messages.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("applied", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("actor", sa.String(100)),
        sa.Column("detail", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("at", TS, nullable=False),
        sa.Column("recorded_at", TS, server_default=NOW, nullable=False),
        sa.CheckConstraint("source IN ('forge','provider','simulator','person')",
                           name="ck_message_events_source"),
    )
    op.create_index("ix_message_events_tenant", "message_events", ["tenant_id", "recorded_at"])

    op.create_table(
        "consents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("contact_id", UUID, sa.ForeignKey("contacts.id"), nullable=False,
                  index=True),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("scope", sa.String(15), nullable=False, server_default="promotional"),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("how", sa.String(20), nullable=False),
        sa.Column("source_ref", sa.String(200)),
        sa.Column("note", sa.String(300)),
        sa.Column("recorded_by", sa.String(100), nullable=False),
        _now_col("recorded_at"),
        sa.CheckConstraint("channel IN ('email','whatsapp','sms','all')",
                           name="ck_consents_channel"),
        sa.CheckConstraint("scope IN ('promotional')", name="ck_consents_scope"),
        sa.CheckConstraint("status IN ('granted','withdrawn')", name="ck_consents_status"),
        sa.CheckConstraint("how IN ('form','in_person','phone','email','import','link',"
                           "'stop_word','person')", name="ck_consents_how"),
    )

    op.create_table(
        "channel_settings",
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("promo_per_day", sa.SmallInteger, nullable=False),
        sa.Column("promo_per_week", sa.SmallInteger, nullable=False),
        sa.Column("quiet_start", sa.String(5), nullable=False),
        sa.Column("quiet_end", sa.String(5), nullable=False),
        sa.Column("promo_rest_days", JSONB, nullable=False),
        sa.Column("promo_skip_holidays", sa.Boolean, nullable=False),
        sa.Column("reply_window_hours", sa.SmallInteger, nullable=False),
        sa.Column("email_mode", sa.String(10), nullable=False),
        sa.Column("review_first", sa.SmallInteger, nullable=False),
        sa.Column("review_every", sa.SmallInteger, nullable=False),
        sa.Column("staff_email", sa.Boolean, nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        _now_col("updated_at"),
        sa.CheckConstraint("email_mode IN ('simulator','live')",
                           name="ck_channel_settings_mode"),
    )

    op.create_table(
        "email_accounts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False,
                  unique=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer, nullable=False),
        sa.Column("security", sa.String(10), nullable=False),
        sa.Column("username", sa.String(320), nullable=False),
        sa.Column("password_enc", sa.Text, nullable=False),
        sa.Column("secret_hint", sa.String(12), nullable=False),
        sa.Column("from_address", sa.String(320), nullable=False),
        sa.Column("from_name", sa.String(100)),
        sa.Column("reply_to", sa.String(320)),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("last_check_at", TS),
        sa.Column("last_check_ok", sa.Boolean),
        sa.Column("last_check_message", sa.String(500)),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=False),
        _now_col("created_at"),
        _now_col("updated_at"),
        sa.CheckConstraint("security IN ('starttls','ssl','none')",
                           name="ck_email_accounts_security"),
        sa.CheckConstraint("status IN ('active','paused')", name="ck_email_accounts_status"),
    )

    op.create_table(
        "tone_guides",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("formality", sa.String(10), nullable=False),
        sa.Column("greeting", sa.String(200)),
        sa.Column("sign_off", sa.String(200)),
        sa.Column("avoid", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("notes", sa.Text),
        sa.Column("updated_by", sa.String(100), nullable=False),
        _now_col("updated_at"),
        sa.UniqueConstraint("tenant_id", "language", name="uq_tone_guides_language"),
        sa.CheckConstraint("formality IN ('formal','neutral','friendly')",
                           name="ck_tone_guides_formality"),
    )

    op.create_table(
        "glossary_terms",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("term", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("language", sa.String(10)),
        sa.Column("translation", sa.String(200)),
        sa.Column("note", sa.String(300)),
        sa.Column("created_by", sa.String(100), nullable=False),
        _now_col("created_at"),
        sa.CheckConstraint("kind IN ('keep','translate','forbid')", name="ck_glossary_kind"),
        sa.CheckConstraint("kind <> 'translate' OR (language IS NOT NULL AND "
                           "translation IS NOT NULL)", name="ck_glossary_translation"),
    )
    op.create_index("uq_glossary_terms", "glossary_terms",
                    ["tenant_id", sa.text("lower(term)"), sa.text("coalesce(language, '')")],
                    unique=True)

    op.create_table(
        "language_reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("message_id", UUID, sa.ForeignKey("messages.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("why", sa.String(10), nullable=False),
        sa.Column("blocking", sa.Boolean, nullable=False),
        sa.Column("problems", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(10), nullable=False, server_default="waiting"),
        sa.Column("score", sa.SmallInteger),
        sa.Column("comment", sa.String(1000)),
        sa.Column("original_body", sa.Text),
        sa.Column("reviewed_by", sa.String(100)),
        sa.Column("reviewed_at", TS),
        _now_col("created_at"),
        sa.CheckConstraint("why IN ('sample','checks')", name="ck_language_reviews_why"),
        sa.CheckConstraint("status IN ('waiting','approved','changed','rejected','closed')",
                           name="ck_language_reviews_status"),
        sa.CheckConstraint("score IS NULL OR score BETWEEN 1 AND 5",
                           name="ck_language_reviews_score"),
    )
    op.create_index("ix_language_reviews_open", "language_reviews", ["tenant_id", "status"])

    for table in ("message_events", "consents", "channel_settings", "email_accounts",
                  "tone_guides", "glossary_terms", "language_reviews"):
        enable_tenant_rls(op, table)
    # A message's history is appended to, never rewritten.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON message_events FROM forge_app")
    op.execute("GRANT SELECT, INSERT ON message_events TO forge_app")
    # Consent is appended to. Erasure blanks the free text; a merge re-points the
    # contact (and its undo points it back). Nothing else changes or is deleted.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON consents FROM forge_app")
    op.execute("GRANT SELECT, INSERT ON consents TO forge_app")
    op.execute("GRANT UPDATE (contact_id, note, source_ref) ON consents TO forge_app")
    op.execute("REVOKE DELETE, TRUNCATE ON channel_settings, language_reviews FROM forge_app")
    op.execute("GRANT USAGE ON SEQUENCE message_events_id_seq TO forge_app")


def downgrade() -> None:
    for table in ("language_reviews", "glossary_terms", "tone_guides", "email_accounts",
                  "channel_settings", "consents", "message_events"):
        op.drop_table(table)
    op.drop_index("ix_messages_contact_sent", "messages")
    op.drop_index("ix_messages_due", "messages")
    op.drop_index("ix_messages_provider_ref", "messages")
    op.drop_index("ix_messages_idempotency", "messages")
    op.drop_constraint("ck_messages_purpose", "messages")
    op.drop_constraint("ck_messages_direction", "messages")
    op.drop_constraint("ck_messages_status", "messages")
    op.execute("UPDATE messages SET status = CASE WHEN status IN ('delivered','read') "
               "THEN 'sent' ELSE 'failed' END WHERE status NOT IN ('draft','sent','failed')")
    op.create_check_constraint("ck_messages_status", "messages",
                               "status in ('draft','sent','failed')")
    for name in ("checks", "attempts", "user_id", "in_reply_to_id",
                 *(n for n, _ in NEW_COLUMNS)):
        op.drop_column("messages", name)
