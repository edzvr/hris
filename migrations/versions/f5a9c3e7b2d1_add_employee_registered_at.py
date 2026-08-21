"""Add employee registration timestamp.

Revision ID: f5a9c3e7b2d1
Revises: e4f8a2c6d1b7
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa

revision = "f5a9c3e7b2d1"
down_revision = "e4f8a2c6d1b7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employees", sa.Column("registered_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("employees", "registered_at")
