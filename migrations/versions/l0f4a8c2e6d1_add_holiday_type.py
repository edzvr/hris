"""Add holiday type for statutory pay calculations.

Revision ID: l0f4a8c2e6d1
Revises: k9e3f7b1a5c2
"""

from alembic import op
import sqlalchemy as sa


revision = "l0f4a8c2e6d1"
down_revision = "k9e3f7b1a5c2"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("holidays")}
    if "holiday_type" not in columns:
        op.add_column(
            "holidays",
            sa.Column(
                "holiday_type",
                sa.String(length=40),
                nullable=False,
                server_default="Special Non-Working Holiday"
            )
        )


def downgrade():
    op.drop_column("holidays", "holiday_type")
