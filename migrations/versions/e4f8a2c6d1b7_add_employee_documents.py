"""Add structured employee 201 documents.

Revision ID: e4f8a2c6d1b7
Revises: d9e3f7b1a5c2
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa

revision = "e4f8a2c6d1b7"
down_revision = "d9e3f7b1a5c2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "employee_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=30), nullable=False),
        sa.Column("document_type", sa.String(length=120), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("retention_years", sa.Integer(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id")
    )


def downgrade():
    op.drop_table("employee_documents")
