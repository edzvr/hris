"""Add company reminders and durable per-recipient delivery records."""

from alembic import op
import sqlalchemy as sa

revision = "ad4e5f6a7b8c"
down_revision = "ac3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "calendar_reminders" not in tables:
        op.create_table(
            "calendar_reminders",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(150), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("company", sa.String(50), nullable=False),
            sa.Column("scheduled_at", sa.DateTime(), nullable=False),
            sa.Column("recurrence", sa.String(20), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "calendar_reminder_deliveries" not in tables:
        op.create_table(
            "calendar_reminder_deliveries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("reminder_id", sa.Integer(), sa.ForeignKey("calendar_reminders.id"), nullable=False),
            sa.Column("occurrence_at", sa.DateTime(), nullable=False),
            sa.Column("stage", sa.String(20), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("detail", sa.String(255), nullable=False),
            sa.Column("attempted_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("reminder_id", "occurrence_at", "stage", "employee_id",
                                name="uq_reminder_occurrence_recipient_stage"),
        )


def downgrade():
    raise RuntimeError("Preserve reminder and delivery history; roll back code without dropping data.")
