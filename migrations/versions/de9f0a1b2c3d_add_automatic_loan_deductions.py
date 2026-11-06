"""Persist loan repayment choices and explicit cutoff overrides."""

from alembic import op
import sqlalchemy as sa

revision = "de9f0a1b2c3d"
down_revision = "cd8e9f0a1b2c"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table, column in (
        ("employees", sa.Column("loan_deduction_per_cutoff", sa.Float(), nullable=False, server_default="500")),
        ("loans", sa.Column("deduction_per_cutoff", sa.Float(), nullable=False, server_default="500")),
        ("payrolls", sa.Column("loan_deduction_override", sa.Float(), nullable=True)),
    ):
        if column.name not in {item["name"] for item in inspector.get_columns(table)}:
            op.add_column(table, column)


def downgrade():
    raise RuntimeError("Preserve loan repayment choices and payroll history; roll back code without dropping data.")
