"""Track applied payroll loan deductions.

Revision ID: u9d0e1f2a3b4
Revises: t8c9d0e1f2a3
"""

from alembic import op
import sqlalchemy as sa


revision = "u9d0e1f2a3b4"
down_revision = "t8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("payrolls")}
    if "loan_deduction_applied" not in columns:
        op.add_column(
            "payrolls",
            sa.Column("loan_deduction_applied", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        bind.execute(sa.text("UPDATE payrolls SET loan_deduction_applied = 1 WHERE is_paid = 1"))


def downgrade():
    op.drop_column("payrolls", "loan_deduction_applied")