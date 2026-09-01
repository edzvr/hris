"""Restore missing employee resume fields.

Revision ID: q5f6a7b8c9d0
Revises: p4e5f6a7b8c9
"""

from alembic import op
import sqlalchemy as sa


revision = "q5f6a7b8c9d0"
down_revision = "p4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("employees")}
    for name in (
        "resume_summary",
        "resume_education",
        "resume_experience",
        "resume_skills",
        "resume_references",
    ):
        if name not in columns:
            op.add_column("employees", sa.Column(name, sa.Text(), nullable=True))


def downgrade():
    pass