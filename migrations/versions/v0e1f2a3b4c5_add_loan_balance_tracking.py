"""Add auditable loan balance tracking.

Revision ID: v0e1f2a3b4c5
Revises: u9d0e1f2a3b4
"""

from alembic import op
import sqlalchemy as sa


revision = "v0e1f2a3b4c5"
down_revision = "u9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("loans")}
    if "loan_type" not in columns:
        op.add_column(
            "loans",
            sa.Column(
                "loan_type",
                sa.String(length=30),
                nullable=False,
                server_default="Employee Loan",
            ),
        )
    if "balance_applied" not in columns:
        op.add_column(
            "loans",
            sa.Column(
                "balance_applied",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade():
    op.drop_column("loans", "balance_applied")
    op.drop_column("loans", "loan_type")