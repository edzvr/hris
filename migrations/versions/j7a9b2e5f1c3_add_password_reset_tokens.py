"""Add password reset tokens.

Revision ID: j7a9b2e5f1c3
Revises: i8d2f6b0c3e9
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa


revision = "j7a9b2e5f1c3"
down_revision = "i8d2f6b0c3e9"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "password_reset_tokens" not in inspector.get_table_names():
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("token", sa.String(length=100), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used", sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token"),
        )
    indexes = {index["name"] for index in inspector.get_indexes("password_reset_tokens")}
    if "ix_password_reset_tokens_token" not in indexes:
        op.create_index("ix_password_reset_tokens_token", "password_reset_tokens", ["token"])


def downgrade():
    op.drop_index("ix_password_reset_tokens_token", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
