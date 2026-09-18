"""Add biometric ID for attendance imports.

Revision ID: a1b2c3d4e5f6
Revises: z4c5d6e7f8g9
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = 'z4c5d6e7f8g9'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('employees')}
    if 'biometric_id' not in columns:
        op.add_column('employees', sa.Column('biometric_id', sa.String(length=50), nullable=True))
    indexes = {index['name'] for index in inspector.get_indexes('employees')}
    if 'ix_employees_biometric_id' not in indexes:
        op.create_index('ix_employees_biometric_id', 'employees', ['biometric_id'], unique=True)
    payroll_columns = {column['name'] for column in inspector.get_columns('payrolls')}
    for column_name in ('sss_override', 'philhealth_override', 'pagibig_override'):
        if column_name not in payroll_columns:
            op.add_column('payrolls', sa.Column(column_name, sa.Float(), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    payroll_columns = {column['name'] for column in inspector.get_columns('payrolls')}
    for column_name in ('pagibig_override', 'philhealth_override', 'sss_override'):
        if column_name in payroll_columns:
            op.drop_column('payrolls', column_name)
    columns = {column['name'] for column in inspector.get_columns('employees')}
    if 'biometric_id' in columns:
        indexes = {index['name'] for index in inspector.get_indexes('employees')}
        if 'ix_employees_biometric_id' in indexes:
            op.drop_index('ix_employees_biometric_id', table_name='employees')
        op.drop_column('employees', 'biometric_id')