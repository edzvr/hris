import io
import os
import sqlite3
import tempfile

from hris import app
from models import AuditLog, Employee


def client_for(employee_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True
    return client


def test_admin_backup_contains_all_employees_and_is_logged():
    with app.app_context():
        admin = Employee.query.filter(Employee.role.ilike('%admin%')).first()
        employee_count = Employee.query.count()
        audit_count = AuditLog.query.filter_by(action='Downloaded full database backup').count()

    response = client_for(admin.id).get('/admin/backup/download')

    assert response.status_code == 200
    assert 'attachment' in response.headers['Content-Disposition']
    assert '_DL_' in response.headers['Content-Disposition']
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'backup.db')
        with open(path, 'wb') as backup_file:
            backup_file.write(response.data)
        connection = sqlite3.connect(path)
        try:
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert connection.execute('SELECT COUNT(*) FROM employees').fetchone()[0] == employee_count
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {'payrolls', 'attendances', 'loans'} <= tables
            assert 'app_secrets' not in tables
        finally:
            connection.close()
    with app.app_context():
        assert AuditLog.query.filter_by(
            action='Downloaded full database backup'
        ).count() == audit_count + 1


def test_staff_cannot_download_backup():
    with app.app_context():
        staff = Employee.query.filter(
            ~Employee.role.ilike('%admin%'), Employee.role.ilike('%staff%')
        ).first()

    response = client_for(staff.id).get('/admin/backup/download')

    assert response.status_code == 403


def test_backup_page_offers_complete_zip_without_local_db_file():
    import io, zipfile, sqlite3, tempfile, os
    with app.app_context():
        admin = Employee.query.filter(Employee.role.ilike('%admin%')).first()
    client = client_for(admin.id)
    page = client.get('/backup')
    assert page.status_code == 200
    assert b'No database file found' not in page.data
    assert b'Download Complete Backup' in page.data
    response = client.get('/download_db')
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.data))
    assert 'hris_database.db' in archive.namelist()
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'b.db')
        with open(path, 'wb') as handle:
            handle.write(archive.read('hris_database.db'))
        connection = sqlite3.connect(path)
        try:
            assert connection.execute('select count(*) from employees').fetchone()[0] > 0
        finally:
            connection.close()
    assert b'Last backup downloaded' in client.get('/backup').data
