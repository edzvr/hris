"""Preserve superseded payslip verification records."""

from alembic import op
import sqlalchemy as sa

revision = 'ef0a1b2c3d4e'
down_revision = 'de9f0a1b2c3d'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('payslip_verifications')}
    if 'superseded_at' not in columns:
        op.add_column(
            'payslip_verifications',
            sa.Column('superseded_at', sa.DateTime(), nullable=True),
        )


def downgrade():
    raise RuntimeError('Preserve payslip verification history; roll back code without dropping data.')
