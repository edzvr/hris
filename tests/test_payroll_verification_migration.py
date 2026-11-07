import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def test_superseded_verification_migration_is_idempotent_and_preserves_records():
    migration_path = (
        Path(__file__).resolve().parents[1] / 'migrations' / 'versions'
        / 'ef0a1b2c3d4e_supersede_payroll_verifications.py'
    )
    spec = importlib.util.spec_from_file_location('verification_migration', migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.exec_driver_sql(
            'CREATE TABLE payslip_verifications (id INTEGER PRIMARY KEY, document_id VARCHAR(32))'
        )
        connection.exec_driver_sql(
            "INSERT INTO payslip_verifications (id, document_id) VALUES (1, 'PAY-OLD')"
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert 'superseded_at' in {
            column['name']
            for column in sa.inspect(connection).get_columns('payslip_verifications')
        }
        row = connection.exec_driver_sql(
            'SELECT document_id, superseded_at FROM payslip_verifications WHERE id = 1'
        ).one()
        assert tuple(row) == ('PAY-OLD', None)
