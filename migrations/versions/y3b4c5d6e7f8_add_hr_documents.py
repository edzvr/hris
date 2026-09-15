"""Add generated HR documents.

Revision ID: y3b4c5d6e7f8
Revises: x2a3b4c5d6e7
"""

from alembic import op
import sqlalchemy as sa


revision = "y3b4c5d6e7f8"
down_revision = "x2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "hr_documents" not in inspector.get_table_names():
        op.create_table(
            "hr_documents",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("document_type", sa.String(length=60), nullable=False),
            sa.Column("subject", sa.String(length=180), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("response_due_date", sa.Date(), nullable=True),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column("related_reference", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="Draft"),
            sa.Column("employee_response", sa.Text(), nullable=True),
            sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("issued_at", sa.DateTime(), nullable=True),
            sa.Column("document_id", sa.String(length=32), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["employees.id"]),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "hr_documents" in inspector.get_table_names():
        op.drop_table("hr_documents")