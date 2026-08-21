"""Add category to evaluations.

Revision ID: b7c1e8a2f4d6
Revises: 95be4a573f77
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa


revision = "b7c1e8a2f4d6"
down_revision = "95be4a573f77"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "evaluations",
        sa.Column("category", sa.String(length=50), nullable=True, server_default="core")
    )


def downgrade():
    op.drop_column("evaluations", "category")
