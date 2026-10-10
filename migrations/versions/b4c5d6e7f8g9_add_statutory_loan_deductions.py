"""Add separate SSS and Pag-IBIG loan deductions to payroll records.

Revision ID: b4c5d6e7f8g9
Revises: a1b2c3d4e5f7
"""

from alembic import op
import sqlalchemy as sa


revision = "b4c5d6e7f8g9"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade():
    payroll_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("payrolls")
    }
    for column_name in ("sss_loan", "pagibig_loan"):
        if column_name not in payroll_columns:
            op.add_column(
                "payrolls",
                sa.Column(
                    column_name,
                    sa.Float(),
                    nullable=False,
                    server_default="0",
                ),
            )


def downgrade():
    payroll_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("payrolls")
    }
    for column_name in ("pagibig_loan", "sss_loan"):
        if column_name in payroll_columns:
            op.drop_column("payrolls", column_name)
