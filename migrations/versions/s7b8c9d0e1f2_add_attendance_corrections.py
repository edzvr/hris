"""Add attendance correction requests.

Revision ID: s7b8c9d0e1f2
Revises: r6a7b8c9d0e1
"""

from alembic import op
import sqlalchemy as sa


revision = "s7b8c9d0e1f2"
down_revision = "r6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "attendance_corrections" not in inspector.get_table_names():
        op.create_table(
            "attendance_corrections",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("attendance_id", sa.Integer(), nullable=True),
            sa.Column("correction_date", sa.Date(), nullable=False),
            sa.Column("requested_clock_in", sa.DateTime(), nullable=True),
            sa.Column("requested_clock_out", sa.DateTime(), nullable=True),
            sa.Column("reason", sa.String(length=500), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="Pending"),
            sa.Column("admin_note", sa.String(length=500), nullable=True),
            sa.Column("reviewed_by", sa.Integer(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
            sa.ForeignKeyConstraint(["attendance_id"], ["attendances.id"]),
            sa.ForeignKeyConstraint(["reviewed_by"], ["employees.id"]),
        )


def downgrade():
    op.drop_table("attendance_corrections")