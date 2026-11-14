import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa
from sqlalchemy import inspect

from hris import Evaluation, app, db, generate_auto_quiz


def test_evaluation_schema_and_quiz_generator():
    with app.app_context():
        Evaluation.query.filter_by(employee_id=1).all()
        nested = db.session.begin_nested()
        generate_auto_quiz("General", num_questions=1)
        nested.rollback()
        columns = [column["name"] for column in inspect(db.engine).get_columns("evaluations")]
        assert "category" in columns


def test_summary_pdf_visibility_migration_preserves_existing_employees():
    path = (
        Path(__file__).resolve().parents[1] / 'migrations' / 'versions'
        / 'f2a3b4c5d6e7_add_payroll_summary_pdf_visibility.py'
    )
    spec = importlib.util.spec_from_file_location('summary_pdf_visibility_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.exec_driver_sql(
            'CREATE TABLE employees (id INTEGER PRIMARY KEY, first_name VARCHAR(50))'
        )
        connection.exec_driver_sql("INSERT INTO employees (id, first_name) VALUES (1, 'Existing')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            assert connection.exec_driver_sql(
                'SELECT hide_from_payroll_summary_pdf FROM employees WHERE id = 1'
            ).scalar_one() == 0
            connection.exec_driver_sql(
                'UPDATE employees SET hide_from_payroll_summary_pdf = TRUE WHERE id = 1'
            )
            migration.upgrade()
        assert connection.exec_driver_sql(
            'SELECT first_name, hide_from_payroll_summary_pdf FROM employees WHERE id = 1'
        ).one() == ('Existing', 1)
