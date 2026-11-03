"""Add source filenames to HR templates."""

from alembic import op
import sqlalchemy as sa


revision = "ba6c7d8e9f0a"
down_revision = "af6b7c8d9e0f"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "hr_templates" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("hr_templates")}
    if "source_filename" not in columns:
        op.add_column(
            "hr_templates",
            sa.Column("source_filename", sa.String(length=255), nullable=True),
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "hr_templates" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("hr_templates")}
    if "source_filename" in columns:
        op.drop_column("hr_templates", "source_filename")
