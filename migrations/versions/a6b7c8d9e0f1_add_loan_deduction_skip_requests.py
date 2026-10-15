"""Add per-cutoff loan deduction skip requests.

Revision ID: a6b7c8d9e0f1
Revises: z4c5d6e7f8g9
"""

from alembic import op
import sqlalchemy as sa


revision = "a6b7c8d9e0f1"
down_revision = "z4c5d6e7f8g9"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "loan_deduction_skip_requests" not in inspector.get_table_names():
        op.create_table(
            "loan_deduction_skip_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("cutoff_start", sa.Date(), nullable=False),
            sa.Column("cutoff_end", sa.Date(), nullable=False),
            sa.Column("reason", sa.String(length=500), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="Pending"),
            sa.Column("decision_note", sa.String(length=500), nullable=True),
            sa.Column("reviewed_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
            sa.ForeignKeyConstraint(["reviewed_by"], ["employees.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("loan_deduction_skip_requests")
    }
    if "ix_loan_deduction_skip_employee_cutoff" not in indexes:
        op.create_index(
            "ix_loan_deduction_skip_employee_cutoff",
            "loan_deduction_skip_requests",
            ["employee_id", "cutoff_start", "status"],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "loan_deduction_skip_requests" in inspector.get_table_names():
        op.drop_index(
            "ix_loan_deduction_skip_employee_cutoff",
            table_name="loan_deduction_skip_requests",
        )
        op.drop_table("loan_deduction_skip_requests")