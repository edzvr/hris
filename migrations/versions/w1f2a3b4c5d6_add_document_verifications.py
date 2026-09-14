"""add authenticated document verification records

Revision ID: w1f2a3b4c5d6
Revises: v0e1f2a3b4c5
Create Date: 2026-09-14
"""

from alembic import op
import sqlalchemy as sa


revision = "w1f2a3b4c5d6"
down_revision = "v0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "payslip_verifications"
    if table_name not in inspector.get_table_names():
        op.create_table(
            table_name,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
            sa.Column("payroll_id", sa.Integer(), sa.ForeignKey("payrolls.id"), nullable=True),
            sa.Column("document_type", sa.String(length=40), nullable=False, server_default="payslip"),
            sa.Column("document_id", sa.String(length=32), nullable=False),
            sa.Column("document_label", sa.String(length=120), nullable=True),
            sa.Column("net_pay", sa.Float(), nullable=False, server_default="0"),
            sa.Column("cutoff_start", sa.Date(), nullable=True),
            sa.Column("cutoff_end", sa.Date(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("verification_hash", sa.String(length=128), nullable=False),
            sa.UniqueConstraint("document_id", name="uq_payslip_verifications_document_id"),
        )
    else:
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing_columns = {
            "payroll_id": sa.Column("payroll_id", sa.Integer(), nullable=True),
            "document_type": sa.Column("document_type", sa.String(length=40), nullable=False, server_default="payslip"),
            "document_label": sa.Column("document_label", sa.String(length=120), nullable=True),
            "net_pay": sa.Column("net_pay", sa.Float(), nullable=False, server_default="0"),
            "cutoff_start": sa.Column("cutoff_start", sa.Date(), nullable=True),
            "cutoff_end": sa.Column("cutoff_end", sa.Date(), nullable=True),
            "created_at": sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            "verification_hash": sa.Column("verification_hash", sa.String(length=128), nullable=False, server_default=""),
        }
        for column_name, column in missing_columns.items():
            if column_name not in existing_columns:
                op.add_column(table_name, column)

    index_names = {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}
    if "ix_payslip_verifications_document_id" not in index_names:
        op.create_index(
            "ix_payslip_verifications_document_id",
            table_name,
            ["document_id"],
            unique=True,
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "payslip_verifications" in inspector.get_table_names():
        index_names = {index["name"] for index in inspector.get_indexes("payslip_verifications")}
        if "ix_payslip_verifications_document_id" in index_names:
            op.drop_index("ix_payslip_verifications_document_id", table_name="payslip_verifications")
        op.drop_table("payslip_verifications")
