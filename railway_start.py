import os
import subprocess
import sys

from sqlalchemy import inspect, text

from hris import app, db


def run_flask_db(*arguments):
    subprocess.run(
        [sys.executable, '-m', 'flask', '--app', 'hris', 'db', *arguments],
        check=True,
    )


with app.app_context():
    inspector = inspect(db.engine)
    needs_baseline = not inspector.has_table('hris_alembic_baseline')

if needs_baseline:
    # Existing Railway tables were created by db.create_all before Alembic was enabled.
    # They already contain the current schema, so do not replay historical migrations.
    run_flask_db('stamp', 'head')
    with app.app_context():
        db.session.execute(text(
            'CREATE TABLE hris_alembic_baseline (id INTEGER PRIMARY KEY)'
        ))
        db.session.commit()

run_flask_db('upgrade')
os.execvp(
    'gunicorn',
    ['gunicorn', '--bind', f"0.0.0.0:{os.environ['PORT']}", 'wsgi:app'],
)