"""Archive direct administrative attendance edits.

Revision ID: f1a2b3c4d5e6
Revises: ef0a1b2c3d4e
"""

from alembic import op
import sqlalchemy as sa


revision = 'f1a2b3c4d5e6'
down_revision = 'ef0a1b2c3d4e'
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table('attendance_admin_edits'):
        op.create_table(
            'attendance_admin_edits',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
            sa.Column('attendance_date', sa.Date(), nullable=False),
            sa.Column('performed_by', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
            sa.Column('attendance_id', sa.Integer(), nullable=False),
            sa.Column('original_row', sa.Text(), nullable=True),
            sa.Column('corrected_row', sa.Text(), nullable=False),
            sa.Column('reason', sa.String(length=500), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )


def downgrade():
    raise RuntimeError(
        'Attendance edit history must be preserved. '
        'Roll back application code without dropping this table.'
    )
