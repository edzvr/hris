"""Add editable resume fields to employee profiles.

Revision ID: o3c4d5e6f7a8
Revises: n2b3c4d5e6f7
"""

from alembic import op
import sqlalchemy as sa


revision = "o3c4d5e6f7a8"
down_revision = "n2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade():
    for name in ('resume_summary', 'resume_education', 'resume_experience', 'resume_skills', 'resume_references'):
        op.add_column('employees', sa.Column(name, sa.Text(), nullable=True))


def downgrade():
    for name in ('resume_references', 'resume_skills', 'resume_experience', 'resume_education', 'resume_summary'):
        op.drop_column('employees', name)