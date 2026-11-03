"""Add database-driven government contribution settings and employee contribution fields."""

from alembic import op
import sqlalchemy as sa

revision = "ae5f6a7b8c9d"
down_revision = "ad4e5f6a7b8c"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "gov_contribution_settings" not in tables:
        op.create_table(
            "gov_contribution_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("effective_month", sa.Date(), nullable=False, unique=True),
            sa.Column("philhealth_rate", sa.Float(), nullable=False),
            sa.Column("philhealth_floor", sa.Float(), nullable=False),
            sa.Column("philhealth_ceiling", sa.Float(), nullable=False),
            sa.Column("default_workweek", sa.Integer(), nullable=False),
            sa.Column("pagibig_low_threshold", sa.Float(), nullable=False),
            sa.Column("pagibig_employee_rate_low", sa.Float(), nullable=False),
            sa.Column("pagibig_employee_rate_high", sa.Float(), nullable=False),
            sa.Column("pagibig_employer_rate", sa.Float(), nullable=False),
            sa.Column("pagibig_max_fund_salary", sa.Float(), nullable=False),
            sa.Column("source_reference", sa.String(255), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "sss_contribution_table" not in tables:
        op.create_table(
            "sss_contribution_table",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("setting_id", sa.Integer(), sa.ForeignKey("gov_contribution_settings.id"),
                      nullable=False, index=True),
            sa.Column("salary_from", sa.Float(), nullable=False),
            sa.Column("salary_to", sa.Float(), nullable=True),
            sa.Column("msc", sa.Float(), nullable=False),
            sa.Column("employee_share", sa.Float(), nullable=False),
            sa.Column("employer_share", sa.Float(), nullable=False),
            sa.Column("ec", sa.Float(), nullable=False),
        )
    if "gov_contribution_audit" not in tables:
        op.create_table(
            "gov_contribution_audit",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column("actor_name", sa.String(120), nullable=False),
            sa.Column("action", sa.String(80), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=True),
            sa.Column("details", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        )
    employee_columns = {column["name"] for column in inspector.get_columns("employees")}
    if "contribution_workweek" not in employee_columns:
        op.add_column("employees", sa.Column("contribution_workweek", sa.Integer(), nullable=True))
    if "tax_status" not in employee_columns:
        op.add_column("employees", sa.Column("tax_status", sa.String(30), nullable=True))


def downgrade():
    raise RuntimeError("Preserve contribution settings and audit history; roll back code without dropping data.")
