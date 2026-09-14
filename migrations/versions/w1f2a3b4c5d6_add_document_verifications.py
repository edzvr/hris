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
    op.create_table(
        "payslip_verifications",
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
    op.create_index(
        "ix_payslip_verifications_document_id",
        "payslip_verifications",
        ["document_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_payslip_verifications_document_id", table_name="payslip_verifications")
    op.drop_table("payslip_verifications")
