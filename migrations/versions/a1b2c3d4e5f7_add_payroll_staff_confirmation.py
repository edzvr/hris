"""Add staff review status to finalized payroll records.

Revision ID: a1b2c3d4e5f7
Revises: z4c5d6e7f8g9, b1d2e3f4a5c6
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f7"
down_revision = ("z4c5d6e7f8g9", "b1d2e3f4a5c6")
branch_labels = None
depends_on = None


def upgrade():
    payroll_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("payrolls")
    }
    if "confirmation_status" not in payroll_columns:
        op.add_column("payrolls", sa.Column("confirmation_status", sa.String(30), nullable=True))
    if "confirmation_note" not in payroll_columns:
        op.add_column("payrolls", sa.Column("confirmation_note", sa.Text(), nullable=True))
    if "confirmed_at" not in payroll_columns:
        op.add_column("payrolls", sa.Column("confirmed_at", sa.DateTime(), nullable=True))


def downgrade():
    payroll_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("payrolls")
    }
    for column_name in ("confirmed_at", "confirmation_note", "confirmation_status"):
        if column_name in payroll_columns:
            op.drop_column("payrolls", column_name)
