"""Add payroll preparation access permission.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('employees')}
    if 'payroll_preparation_access' not in columns:
        op.add_column(
            'employees',
            sa.Column('payroll_preparation_access', sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('employees')}
    if 'payroll_preparation_access' in columns:
        op.drop_column('employees', 'payroll_preparation_access')