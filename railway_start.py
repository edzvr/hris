import os
import subprocess
import sys

from sqlalchemy import inspect, text

from hris import app, db

CURRENT_SCHEMA_BASELINE = 'b2c3d4e5f6a7'


def run_flask_db(*arguments):
    subprocess.run(
        [sys.executable, '-m', 'flask', '--app', 'hris', 'db', *arguments],
        check=True,
    )


with app.app_context():
    inspector = inspect(db.engine)
    current_revision = None
    if inspector.has_table('alembic_version'):
        current_revision = db.session.execute(
            text('SELECT version_num FROM alembic_version')
        ).scalar()
    needs_baseline = current_revision != CURRENT_SCHEMA_BASELINE

if needs_baseline:
    # Existing Railway tables were created by db.create_all before Alembic was enabled.
    # They already contain the current schema, so do not replay historical migrations.
    run_flask_db('stamp', CURRENT_SCHEMA_BASELINE)

run_flask_db('upgrade')
os.execvp(
    'gunicorn',
    ['gunicorn', '--bind', f"0.0.0.0:{os.environ['PORT']}", 'wsgi:app'],
)