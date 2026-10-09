from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from hris import app
from models import Attendance, AttendanceCorrection, Employee, db


def log_in(client, employee):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee.id)
        session['_fresh'] = True


def test_staff_dashboard_warns_about_missing_punches_in_current_cutoff():
    today = datetime.now(ZoneInfo('Asia/Manila')).date()
    cutoff_start = today - timedelta(days=(today.weekday() + 2) % 7)
    with app.app_context():
        staff = Employee(
            first_name='Punch',
            last_name='Reminder',
            role='staff',
            company='Trece-Uno',
        )
        db.session.add(staff)
        db.session.flush()
        attendance = Attendance(
            employee_id=staff.id,
            date=cutoff_start,
            clock_in=None,
            clock_out=None,
            status='Absent',
            company='Trece-Uno',
        )
        db.session.add(attendance)
        db.session.commit()
        staff_id, attendance_id = staff.id, attendance.id

    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, staff_id))
        response = client.get('/dashboard_staff')
        assert response.status_code == 200
        assert b'Paalala sa attendance at payroll' in response.data
        assert b'hindi maisasama sa payroll' in response.data
        assert b'walang Clock In at Clock Out' in response.data
        assert (
            f'/attendance/correction?correction_date={cutoff_start.isoformat()}'.encode()
            in response.data
        )
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(Attendance, attendance_id))
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.commit()


def test_staff_can_correct_only_missing_clock_out_and_admin_approval_preserves_clock_in():
    correction_date = datetime.now(ZoneInfo('Asia/Manila')).date() - timedelta(days=1)
    clock_in = datetime.combine(correction_date, datetime.min.time()).replace(
        hour=8, minute=5
    )
    requested_clock_out = datetime.combine(
        correction_date, datetime.min.time()
    ).replace(hour=17, minute=2)
    with app.app_context():
        staff = Employee(
            first_name='Punch',
            last_name='Staff',
            role='staff',
            company='Trece-Uno',
        )
        admin = Employee(first_name='Punch', last_name='Admin', role='admin')
        db.session.add_all([staff, admin])
        db.session.flush()
        attendance = Attendance(
            employee_id=staff.id,
            date=correction_date,
            clock_in=clock_in,
            clock_out=None,
            status='Present',
            company='Trece-Uno',
        )
        db.session.add(attendance)
        db.session.commit()
        staff_id, admin_id, attendance_id = staff.id, admin.id, attendance.id

    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, staff_id))
        page = client.get(
            f'/attendance/correction?correction_date={correction_date.isoformat()}'
        )
        assert page.status_code == 200
        assert (
            f'value="{correction_date.isoformat()}"'.encode() in page.data
        )
        response = client.post(
            '/attendance/correction',
            data={
                'correction_date': correction_date.isoformat(),
                'requested_clock_out': requested_clock_out.isoformat(timespec='minutes'),
                'reason': 'Nakalimutang mag-clock out.',
            },
        )
        assert response.status_code == 302

        with app.app_context():
            correction = AttendanceCorrection.query.filter_by(
                employee_id=staff_id,
                correction_date=correction_date,
            ).one()
            correction_id = correction.id
            assert correction.requested_clock_in is None
            log_in(client, db.session.get(Employee, admin_id))
        response = client.post(
            f'/admin/attendance-correction/{correction_id}/approve'
        )
        assert response.status_code == 302

        with app.app_context():
            attendance = db.session.get(Attendance, attendance_id)
            assert attendance.clock_in == clock_in
            assert attendance.clock_out == requested_clock_out
    finally:
        with app.app_context():
            db.session.rollback()
            AttendanceCorrection.query.filter_by(
                employee_id=staff_id,
                correction_date=correction_date,
            ).delete()
            db.session.delete(db.session.get(Attendance, attendance_id))
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.delete(db.session.get(Employee, admin_id))
            db.session.commit()
