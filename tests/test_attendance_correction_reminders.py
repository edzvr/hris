from io import BytesIO
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

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
            email='punch.staff@example.com',
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
            assert correction.attendance_id == attendance_id
            log_in(client, db.session.get(Employee, admin_id))
        with patch('hris.send_notification_email') as send_email:
            response = client.post(
                f'/admin/attendance-correction/{correction_id}/approve'
            )
        assert response.status_code == 302
        send_email.assert_called_once()
        assert send_email.call_args.args[0] == ['punch.staff@example.com']
        assert send_email.call_args.args[1] == 'Attendance correction approved'

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


def test_attendance_api_blocks_second_record_for_same_day():
    attendance_date = datetime.now(ZoneInfo('Asia/Manila')).date()
    with app.app_context():
        staff = Employee(
            first_name='Punch',
            last_name='Duplicate',
            role='staff',
            company='Trece-Uno',
        )
        db.session.add(staff)
        db.session.flush()
        attendance_records = [
            Attendance(
                employee_id=staff.id,
                date=attendance_date,
                clock_in=datetime.combine(
                    attendance_date, datetime.min.time()
                ).replace(hour=8 + index),
                clock_out=datetime.combine(
                    attendance_date, datetime.min.time()
                ).replace(hour=12 + index),
                status='Present',
                company='Trece-Uno',
            )
            for index in range(2)
        ]
        db.session.add_all(attendance_records)
        db.session.commit()
        staff_id = staff.id
        attendance_ids = [record.id for record in attendance_records]

    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, staff_id))
        response = client.post(
            f'/attendance_api/{staff_id}',
            data={
                'clockin': '1',
                'event_time': datetime.combine(
                    attendance_date, datetime.min.time()
                ).replace(hour=9).isoformat(),
            },
        )
        assert response.status_code == 200, response.location
        assert response.json['status'] == 'warning'
        assert b'Multiple attendance records' in response.data
        with app.app_context():
            assert Attendance.query.filter_by(
                employee_id=staff_id,
                date=attendance_date,
            ).count() == 2
    finally:
        with app.app_context():
            db.session.rollback()
            Attendance.query.filter(Attendance.id.in_(attendance_ids)).delete(
                synchronize_session=False
            )
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.commit()


def test_admin_cannot_approve_correction_when_date_has_duplicate_records():
    correction_date = datetime.now(ZoneInfo('Asia/Manila')).date() - timedelta(days=1)
    clock_in = datetime.combine(
        correction_date, datetime.min.time()
    ).replace(hour=8)
    with app.app_context():
        staff = Employee(
            first_name='Punch',
            last_name='Correction',
            role='staff',
        )
        admin = Employee(first_name='Punch', last_name='Reviewer', role='admin')
        db.session.add_all([staff, admin])
        db.session.flush()
        attendance_records = [
            Attendance(
                employee_id=staff.id,
                date=correction_date,
                clock_in=clock_in.replace(hour=8 + index),
                clock_out=clock_in.replace(hour=16 + index),
                status='Present',
            )
            for index in range(2)
        ]
        db.session.add_all(attendance_records)
        db.session.flush()
        correction = AttendanceCorrection(
            employee_id=staff.id,
            attendance=attendance_records[0],
            correction_date=correction_date,
            requested_clock_out=clock_in.replace(hour=17),
            reason='Wrong clock-out.',
        )
        db.session.add(correction)
        db.session.commit()
        staff_id, admin_id = staff.id, admin.id
        correction_id = correction.id
        attendance_ids = [record.id for record in attendance_records]

    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, admin_id))
        response = client.post(
            f'/admin/attendance-correction/{correction_id}/approve'
        )
        assert response.status_code == 302
        with app.app_context():
            correction = db.session.get(AttendanceCorrection, correction_id)
            assert correction.status == 'Pending'
            assert Attendance.query.filter(Attendance.id.in_(attendance_ids)).count() == 2
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(AttendanceCorrection, correction_id))
            Attendance.query.filter(Attendance.id.in_(attendance_ids)).delete(
                synchronize_session=False
            )
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.delete(db.session.get(Employee, admin_id))
            db.session.commit()


def test_biometric_preview_marks_duplicate_rows_from_same_upload():
    attendance_date = datetime.now(ZoneInfo('Asia/Manila')).date()
    with app.app_context():
        employee = Employee(
            first_name='Punch',
            last_name='Biometric',
            role='staff',
            company='Trece-Uno',
            biometric_id='duplicate-upload-test',
        )
        admin = Employee(first_name='Punch', last_name='Importer', role='admin')
        db.session.add_all([employee, admin])
        db.session.commit()
        employee_id, admin_id = employee.id, admin.id

    rows = [
        {
            'biometric_id': 'duplicate-upload-test',
            'employee_name': 'Punch Biometric',
            'date': attendance_date.isoformat(),
            'clock_in': datetime.combine(
                attendance_date, datetime.min.time()
            ).replace(hour=8).isoformat(),
            'clock_out': datetime.combine(
                attendance_date, datetime.min.time()
            ).replace(hour=17).isoformat(),
        },
        {
            'biometric_id': 'duplicate-upload-test',
            'employee_name': 'Punch Biometric',
            'date': attendance_date.isoformat(),
            'clock_in': datetime.combine(
                attendance_date, datetime.min.time()
            ).replace(hour=8, minute=5).isoformat(),
            'clock_out': datetime.combine(
                attendance_date, datetime.min.time()
            ).replace(hour=17, minute=5).isoformat(),
        },
    ]

    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, admin_id))
        with patch('hris.parse_ngteco_timecard', return_value=rows):
            response = client.post(
                '/admin/biometric-import',
                data={
                    'action': 'preview',
                    'biometric_file': (BytesIO(b'test'), 'attendance.csv'),
                },
                content_type='multipart/form-data',
            )
        assert response.status_code == 200
        assert b'class="ready">Ready</td>' in response.data
        assert b'class="problem">Duplicate</td>' in response.data
        with app.app_context():
            assert Attendance.query.filter_by(employee_id=employee_id).count() == 0
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(Employee, employee_id))
            db.session.delete(db.session.get(Employee, admin_id))
            db.session.commit()
