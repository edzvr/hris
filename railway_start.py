import os
import subprocess
import sys

from sqlalchemy import inspect

from hris import app, db


def run_flask_db(*arguments):
    subprocess.run(
        [sys.executable, '-m', 'flask', '--app', 'hris', 'db', *arguments],
        check=True,
    )


with app.app_context():
    has_alembic_version = inspect(db.engine).has_table('alembic_version')

if not has_alembic_version:
    # Existing Railway tables were created by db.create_all before Alembic was enabled.
    run_flask_db('stamp', '95be4a573f77')

run_flask_db('upgrade')
os.execvp(
    'gunicorn',
    ['gunicorn', '--bind', f"0.0.0.0:{os.environ['PORT']}", 'wsgi:app'],
)