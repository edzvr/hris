"""Add overtime applications.

Revision ID: k9e3f7b1a5c2
Revises: i8d2f6b0c3e9
"""

from alembic import op
import sqlalchemy as sa


revision = "k9e3f7b1a5c2"
down_revision = "i8d2f6b0c3e9"
branch_labels = None
depends_on = None


def upgrade():
    if "ot_applications" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "ot_applications",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("ot_date", sa.Date(), nullable=False),
            sa.Column("start_time", sa.Time(), nullable=False),
            sa.Column("end_time", sa.Time(), nullable=False),
            sa.Column("reason", sa.String(length=500), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("decision_note", sa.String(length=500), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
            sa.PrimaryKeyConstraint("id")
        )


def downgrade():
    op.drop_table("ot_applications")