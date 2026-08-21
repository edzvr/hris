"""Add monthly peer evaluation questionnaire table.

Revision ID: c8d2f1a6e9b4
Revises: b7c1e8a2f4d6
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa


revision = "c8d2f1a6e9b4"
down_revision = "b7c1e8a2f4d6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evaluation_questions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False, server_default="core"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id")
    )


def downgrade():
    op.drop_table("evaluation_questions")
