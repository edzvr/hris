"""Add employee liabilities for recoverable payroll deductions.

Revision ID: x2a3b4c5d6e7
Revises: w1f2a3b4c5d6
"""

from alembic import op
import sqlalchemy as sa


revision = "x2a3b4c5d6e7"
down_revision = "w1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    payroll_columns = {column["name"] for column in inspector.get_columns("payrolls")}
    if "liability_deduction" not in payroll_columns:
        op.add_column("payrolls", sa.Column("liability_deduction", sa.Float(), server_default="0"))
    if "liability_deduction_applied" not in payroll_columns:
        op.add_column("payrolls", sa.Column("liability_deduction_applied", sa.Boolean(), nullable=False, server_default=sa.false()))

    if "employee_liabilities" not in inspector.get_table_names():
        op.create_table(
            "employee_liabilities",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(length=80), nullable=False, server_default="Uncollected Company Receivable"),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("customer_name", sa.String(length=150), nullable=True),
            sa.Column("agreement_date", sa.Date(), nullable=True),
            sa.Column("total_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("deduction_per_cutoff", sa.Float(), nullable=False, server_default="0"),
            sa.Column("amount_deducted", sa.Float(), nullable=False, server_default="0"),
            sa.Column("amount_recovered", sa.Float(), nullable=False, server_default="0"),
            sa.Column("amount_refunded", sa.Float(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="Active"),
            sa.Column("acknowledgment_status", sa.String(length=30), nullable=False, server_default="Acknowledged"),
            sa.Column("reference", sa.String(length=120), nullable=True),
            sa.Column("remarks", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["employees.id"]),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "employee_liabilities" in inspector.get_table_names():
        op.drop_table("employee_liabilities")
    payroll_columns = {column["name"] for column in inspector.get_columns("payrolls")}
    if "liability_deduction_applied" in payroll_columns:
        op.drop_column("payrolls", "liability_deduction_applied")
    if "liability_deduction" in payroll_columns:
        op.drop_column("payrolls", "liability_deduction")