"""Add private staff messages and announcements.

Revision ID: aa1b2c3d4e5f
Revises: b6c7d8e9f0a1, z4c5d6e7f8g9
"""

from alembic import op
import sqlalchemy as sa


revision = "aa1b2c3d4e5f"
down_revision = ("b6c7d8e9f0a1", "z4c5d6e7f8g9")
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "staff_messages" not in inspector.get_table_names():
        op.create_table(
            "staff_messages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("sender_id", sa.Integer(), nullable=False),
            sa.Column("recipient_id", sa.Integer(), nullable=False),
            sa.Column("message_type", sa.String(length=20), nullable=False, server_default="direct"),
            sa.Column("subject", sa.String(length=180), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("read_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["sender_id"], ["employees.id"]),
            sa.ForeignKeyConstraint(["recipient_id"], ["employees.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    inspector = sa.inspect(op.get_bind())
    indexes = {
        index["name"]
        for index in inspector.get_indexes("staff_messages")
    }
    if "ix_staff_messages_recipient_created" not in indexes:
        op.create_index(
            "ix_staff_messages_recipient_created",
            "staff_messages",
            ["recipient_id", "created_at"],
        )
    if "ix_staff_messages_sender_created" not in indexes:
        op.create_index(
            "ix_staff_messages_sender_created",
            "staff_messages",
            ["sender_id", "created_at"],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "staff_messages" in inspector.get_table_names():
        op.drop_table("staff_messages")
