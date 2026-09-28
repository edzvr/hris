"""Add rice allowance and de minimis payroll settings.

Revision ID: a7b9c1d3e5f7
Revises: z4c5d6e7f8g9
"""

from alembic import op
import sqlalchemy as sa


revision = "a7b9c1d3e5f7"
down_revision = "z4c5d6e7f8g9"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("employees")}
    if "rice_allowance_per_day" not in columns:
        op.add_column("employees", sa.Column("rice_allowance_per_day", sa.Float(), nullable=True, server_default="0"))
    if "rice_allowance_is_de_minimis" not in columns:
        op.add_column("employees", sa.Column("rice_allowance_is_de_minimis", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "rice_allowance_ceiling" not in columns:
        op.add_column("employees", sa.Column("rice_allowance_ceiling", sa.Float(), nullable=True, server_default="2500"))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("employees")}
    for name in ("rice_allowance_ceiling", "rice_allowance_is_de_minimis", "rice_allowance_per_day"):
        if name in columns:
            op.drop_column("employees", name)