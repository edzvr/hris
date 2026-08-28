"""Add audit logs for user access and downloads.

Revision ID: m1a2b3c4d5e6
Revises: l0f4a8c2e6d1
"""

from alembic import op
import sqlalchemy as sa


revision = "m1a2b3c4d5e6"
down_revision = "l0f4a8c2e6d1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("employee_name", sa.String(length=120), nullable=False),
        sa.Column("company", sa.String(length=50), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade():
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
