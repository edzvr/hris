"""Add employment status fields and managed job description choices."""

from alembic import op
import sqlalchemy as sa


revision = "b1d2e3f4a5c6"
down_revision = "a1c2e3f4b5d6"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    employee_columns = {column["name"] for column in inspector.get_columns("employees")}
    owner_exemption_column_missing = "payroll_attendance_exempt" not in employee_columns
    for name, column_type in (
        ("employment_status", sa.String(length=30)),
        ("probation_end_date", sa.Date()),
        ("regularization_date", sa.Date()),
        ("payroll_attendance_exempt", sa.Boolean()),
        ("manual_monthly_sss", sa.Float()),
        ("manual_monthly_philhealth", sa.Float()),
        ("manual_monthly_pagibig", sa.Float()),
        ("manual_contribution_cutoff_start", sa.Date()),
    ):
        if name not in employee_columns:
            op.add_column(
                "employees",
                sa.Column(
                    name,
                    column_type,
                    nullable=name in {"employment_status", "probation_end_date", "regularization_date", "manual_contribution_cutoff_start"},
                    server_default=(
                        sa.false()
                        if name == "payroll_attendance_exempt"
                        else sa.text("0")
                        if name.startswith("manual_monthly_")
                        else None
                    ),
                ),
            )

    if not inspector.has_table("job_description_options"):
        op.create_table(
            "job_description_options",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=150), nullable=False, unique=True),
        )
    if owner_exemption_column_missing:
        op.execute(sa.text("""
            UPDATE employees
            SET payroll_attendance_exempt = TRUE
            WHERE LOWER(role) = 'admin'
              AND LOWER(email) IN ('randolfronquillo20@gmail.com', 'edzvronquillo@gmail.com')
        """))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("job_description_options"):
        op.drop_table("job_description_options")
    employee_columns = {column["name"] for column in inspector.get_columns("employees")}
    for name in (
        "manual_contribution_cutoff_start",
        "manual_monthly_pagibig",
        "manual_monthly_philhealth",
        "manual_monthly_sss",
        "payroll_attendance_exempt",
        "regularization_date",
        "probation_end_date",
        "employment_status",
    ):
        if name in employee_columns:
            op.drop_column("employees", name)
