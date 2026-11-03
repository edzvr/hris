"""Preserve original attendance rows during administrator reconciliation."""

from alembic import op
import sqlalchemy as sa


revision = "ab2c3d4e5f6a"
down_revision = "aa1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade():
    if "attendance_reconciliations" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "attendance_reconciliations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
            sa.Column("attendance_date", sa.Date(), nullable=False),
            sa.Column("performed_by", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
            sa.Column("retained_attendance_id", sa.Integer(), nullable=False),
            sa.Column("original_rows", sa.Text(), nullable=False),
            sa.Column("corrected_row", sa.Text(), nullable=False),
            sa.Column("reason", sa.String(500), nullable=False),
            sa.Column("backup_audit_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade():
    raise RuntimeError(
        "Attendance reconciliation history must be preserved. "
        "Roll back application code without dropping this table."
    )
