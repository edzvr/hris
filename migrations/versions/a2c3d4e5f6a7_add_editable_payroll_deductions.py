"""Add editable payroll deduction overrides.

Revision ID: a2c3d4e5f6a7
Revises: b4c5d6e7f8g9
"""

from alembic import op
import sqlalchemy as sa


revision = "a2c3d4e5f6a7"
down_revision = "b4c5d6e7f8g9"
branch_labels = None
depends_on = None


def upgrade():
    payroll_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("payrolls")
    }
    if "withholding_tax_override" not in payroll_columns:
        op.add_column(
            "payrolls",
            sa.Column("withholding_tax_override", sa.Float(), nullable=True),
        )
    if "liability_deduction_override" not in payroll_columns:
        op.add_column(
            "payrolls",
            sa.Column("liability_deduction_override", sa.Float(), nullable=True),
        )
    if "other_deductions" not in payroll_columns:
        op.add_column(
            "payrolls",
            sa.Column(
                "other_deductions",
                sa.Float(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade():
    payroll_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("payrolls")
    }
    for column_name in (
        "other_deductions",
        "liability_deduction_override",
        "withholding_tax_override",
    ):
        if column_name in payroll_columns:
            op.drop_column("payrolls", column_name)
