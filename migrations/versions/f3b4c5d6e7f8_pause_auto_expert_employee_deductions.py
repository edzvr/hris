"""Refund Auto Expert staff employee contributions from the November 7 cutoff."""

import logging

from alembic import op

from services.payroll_deduction_pause import adjust_saved_employee_deductions


revision = 'f3b4c5d6e7f8'
down_revision = 'f2a3b4c5d6e7'
branch_labels = None
depends_on = None


def upgrade():
    adjusted = adjust_saved_employee_deductions(op.get_bind())
    logging.getLogger('alembic.runtime.migration').info(
        'Adjusted employee contributions on %s saved Auto Expert payroll records.', adjusted
    )


def downgrade():
    raise RuntimeError('Preserve payroll adjustment history; do not restore deductions automatically.')
