"""Allow a local classifier and a separate review-mail outcome.

Revision ID: 0019
Revises: 0018
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_inbound_emails_label_source", "inbound_emails", type_="check")
    op.create_check_constraint("ck_inbound_emails_label_source", "inbound_emails",
                               "label_source IS NULL OR label_source IN "
                               "('model','rules','person','none','local')")
    op.drop_constraint("ck_inbound_emails_outcome", "inbound_emails", type_="check")
    op.create_check_constraint("ck_inbound_emails_outcome", "inbound_emails",
                               "outcome IS NULL OR outcome IN "
                               "('enquiry','reply','other','auto','receipt','review')")


def downgrade():
    # Preserve captured mail when restoring the earlier application's vocabulary.
    # The migration runs as owner; FORCE RLS would otherwise hide tenant rows.
    op.execute("ALTER TABLE inbound_emails NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE inbound_emails SET label_source = 'rules' WHERE label_source = 'local'")
    op.execute("UPDATE inbound_emails SET outcome = 'other' WHERE outcome = 'review'")
    op.execute("ALTER TABLE inbound_emails FORCE ROW LEVEL SECURITY")
    op.drop_constraint("ck_inbound_emails_label_source", "inbound_emails", type_="check")
    op.create_check_constraint("ck_inbound_emails_label_source", "inbound_emails",
                               "label_source IS NULL OR label_source IN "
                               "('model','rules','person','none')")
    op.drop_constraint("ck_inbound_emails_outcome", "inbound_emails", type_="check")
    op.create_check_constraint("ck_inbound_emails_outcome", "inbound_emails",
                               "outcome IS NULL OR outcome IN "
                               "('enquiry','reply','other','auto','receipt')")
