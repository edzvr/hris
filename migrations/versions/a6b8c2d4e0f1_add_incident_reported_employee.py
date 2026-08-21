"""Add reported employee to incident reports.

Revision ID: a6b8c2d4e0f1
Revises: f5a9c3e7b2d1
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "a6b8c2d4e0f1"
down_revision = "f5a9c3e7b2d1"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "incident_reports" in inspector.get_table_names():
        existing = {column["name"] for column in inspector.get_columns("incident_reports")}
        if "reported_employee_id" not in existing:
            with op.batch_alter_table("incident_reports") as batch_op:
                batch_op.add_column(
                    sa.Column("reported_employee_id", sa.Integer(), nullable=True)
                )
                batch_op.create_foreign_key(
                    "fk_incident_reports_reported_employee",
                    "employees",
                    ["reported_employee_id"],
                    ["id"]
                )


def downgrade():
    op.drop_constraint(
        "fk_incident_reports_reported_employee",
        "incident_reports",
        type_="foreignkey"
    )
    op.drop_column("incident_reports", "reported_employee_id")