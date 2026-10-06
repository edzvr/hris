"""Add job description to employee records.

Revision ID: a1c2e3f4b5d6
Revises: a7b9c1d3e5f7
"""

from alembic import op
import sqlalchemy as sa


revision = "a1c2e3f4b5d6"
down_revision = "a7b9c1d3e5f7"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("employees")}
    if "job_description" not in columns:
        op.add_column(
            "employees",
            sa.Column("job_description", sa.String(length=150), nullable=True),
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("employees")}
    if "job_description" in columns:
        op.drop_column("employees", "job_description")
