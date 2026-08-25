"""Add employee middle name and suffix.

Revision ID: i8d2f6b0c3e9
Revises: h7c1e9f4a2d8
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa


revision = "i8d2f6b0c3e9"
down_revision = "h7c1e9f4a2d8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employees", sa.Column("middle_name", sa.String(length=50), nullable=True))
    op.add_column("employees", sa.Column("suffix_name", sa.String(length=20), nullable=True))


def downgrade():
    op.drop_column("employees", "suffix_name")
    op.drop_column("employees", "middle_name")