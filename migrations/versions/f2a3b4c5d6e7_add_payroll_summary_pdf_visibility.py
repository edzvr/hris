"""Add per-employee visibility for downloaded payroll summary PDFs."""

from alembic import op
import sqlalchemy as sa


revision = 'f2a3b4c5d6e7'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('employees')}
    if 'hide_from_payroll_summary_pdf' not in columns:
        op.add_column(
            'employees',
            sa.Column(
                'hide_from_payroll_summary_pdf',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade():
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('employees')}
    if 'hide_from_payroll_summary_pdf' in columns:
        op.drop_column('employees', 'hide_from_payroll_summary_pdf')
