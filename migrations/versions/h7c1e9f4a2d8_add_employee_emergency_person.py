"""Add employee emergency person name.

Revision ID: h7c1e9f4a2d8
Revises: g6b0d4e8f3c9
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa


revision = "h7c1e9f4a2d8"
down_revision = "g6b0d4e8f3c9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employees", sa.Column("emergency_person", sa.String(length=100), nullable=True))


def downgrade():
    op.drop_column("employees", "emergency_person")