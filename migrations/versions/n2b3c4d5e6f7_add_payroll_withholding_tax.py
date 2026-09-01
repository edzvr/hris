"""Add withholding tax to payroll records.

Revision ID: n2b3c4d5e6f7
Revises: m1a2b3c4d5e6
"""

from alembic import op
import sqlalchemy as sa


revision = "n2b3c4d5e6f7"
down_revision = "m1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("payrolls")}
    if "withholding_tax" not in columns:
        op.add_column(
            "payrolls",
            sa.Column("withholding_tax", sa.Float(), nullable=True, server_default="0")
        )


def downgrade():
    op.drop_column("payrolls", "withholding_tax")
