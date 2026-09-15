"""Add staff concerns for suggestions and clarifications.

Revision ID: z4c5d6e7f8g9
Revises: y3b4c5d6e7f8
"""

from alembic import op
import sqlalchemy as sa


revision = "z4c5d6e7f8g9"
down_revision = "y3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "staff_concerns" not in inspector.get_table_names():
        op.create_table(
            "staff_concerns",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("concern_type", sa.String(length=40), nullable=False, server_default="Clarification"),
            sa.Column("subject", sa.String(length=180), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="Open"),
            sa.Column("admin_response", sa.Text(), nullable=True),
            sa.Column("responded_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("responded_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
            sa.ForeignKeyConstraint(["responded_by"], ["employees.id"]),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "staff_concerns" in inspector.get_table_names():
        op.drop_table("staff_concerns")