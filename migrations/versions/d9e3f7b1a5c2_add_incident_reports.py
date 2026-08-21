"""Add signed incident reports.

Revision ID: d9e3f7b1a5c2
Revises: c8d2f1a6e9b4
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa


revision = "d9e3f7b1a5c2"
down_revision = "c8d2f1a6e9b4"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "incident_reports" not in inspector.get_table_names():
        op.create_table(
            "incident_reports",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(length=100), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("signature", sa.Text(), nullable=False),
            sa.Column("staff_signature", sa.Text(), nullable=True),
            sa.Column("admin_signature", sa.Text(), nullable=True),
            sa.Column("reviewed_by", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
            sa.ForeignKeyConstraint(["reviewed_by"], ["employees.id"]),
            sa.PrimaryKeyConstraint("id")
        )
        return

    existing = {column["name"] for column in inspector.get_columns("incident_reports")}
    if "staff_signature" not in existing:
        op.add_column("incident_reports", sa.Column("staff_signature", sa.Text(), nullable=True))
    if "admin_signature" not in existing:
        op.add_column("incident_reports", sa.Column("admin_signature", sa.Text(), nullable=True))
    if "reviewed_by" not in existing:
        op.add_column("incident_reports", sa.Column("reviewed_by", sa.Integer(), nullable=True))


def downgrade():
    op.drop_table("incident_reports")
