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
