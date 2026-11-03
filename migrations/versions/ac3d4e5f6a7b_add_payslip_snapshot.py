"""Store complete future payslip breakdowns without reconstructing legacy data."""

from alembic import op
import sqlalchemy as sa

revision = "ac3d4e5f6a7b"
down_revision = "ab2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade():
    if "payslip_snapshot" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("payrolls")
    }:
        op.add_column("payrolls", sa.Column("payslip_snapshot", sa.Text(), nullable=True))


def downgrade():
    raise RuntimeError("Preserve payslip snapshots; roll back code without dropping historical data.")
