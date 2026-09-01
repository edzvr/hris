"""Add evaluation approval and merit audit fields.

Revision ID: r6a7b8c9d0e1
Revises: q5f6a7b8c9d0
"""

from alembic import op
import sqlalchemy as sa


revision = "r6a7b8c9d0e1"
down_revision = "q5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    evaluation_columns = {column["name"] for column in sa.inspect(bind).get_columns("evaluations")}
    if "approval_status" not in evaluation_columns:
        op.add_column("evaluations", sa.Column("approval_status", sa.String(length=20), nullable=False, server_default="Pending"))
    if "points_applied" not in evaluation_columns:
        op.add_column("evaluations", sa.Column("points_applied", sa.Boolean(), nullable=False, server_default=sa.false()))
    merit_columns = {column["name"] for column in sa.inspect(bind).get_columns("merit_demerit")}
    if "source" not in merit_columns:
        op.add_column("merit_demerit", sa.Column("source", sa.String(length=50), nullable=True))
    if "reference" not in merit_columns:
        op.add_column("merit_demerit", sa.Column("reference", sa.String(length=255), nullable=True))


def downgrade():
    pass