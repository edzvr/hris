"""Add employee SIL eligibility flag.

Revision ID: g6b0d4e8f3c9
Revises: f5a9c3e7b2d1
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa


revision = "g6b0d4e8f3c9"
down_revision = "a6b8c2d4e0f1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "employees",
        sa.Column("sil_eligible", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade():
    op.drop_column("employees", "sil_eligible")