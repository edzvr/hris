"""Add admin-managed HR templates."""

from alembic import op
import sqlalchemy as sa


revision = "af6b7c8d9e0f"
down_revision = "ae5f6a7b8c9d"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "hr_templates" not in inspector.get_table_names():
        op.create_table(
            "hr_templates",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=180), nullable=False),
            sa.Column("category", sa.String(length=80), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("stored_filename", sa.String(length=255), nullable=True),
            sa.Column("original_filename", sa.String(length=255), nullable=True),
            sa.Column("online_url", sa.String(length=2048), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["created_by"], ["employees.id"]),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "hr_templates" in inspector.get_table_names():
        op.drop_table("hr_templates")
