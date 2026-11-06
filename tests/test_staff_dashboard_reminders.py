from datetime import date, datetime, timedelta
from inspect import unwrap
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from hris import app
from models import Attendance, AttendanceCorrection, Employee, db


def log_in(client, employee_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True


def at(day, hour):
    return datetime.combine(day, datetime.min.time()).replace(hour=hour)


def test_next_day_clock_in_reminds_staff_about_missing_punches():
    today = datetime.now(ZoneInfo('Asia/Manila')).date()
    yesterday = today - timedelta(days=1)
    two_days_ago = today - timedelta(days=2)
    with app.app_context():
        staff = Employee(first_name='Next', last_name='Day', role='staff', company='Trece-Uno')
        db.session.add(staff)
        db.session.flush()
        db.session.add_all([
            Attendance(employee_id=staff.id, date=yesterday, clock_in=at(yesterday, 8),
                       status='Present', company='Trece-Uno'),
            Attendance(employee_id=staff.id, date=two_days_ago, clock_out=at(two_days_ago, 17),
                       status='Present', company='Trece-Uno'),
        ])
        db.session.flush()
        db.session.add(AttendanceCorrection(
            employee_id=staff.id, correction_date=two_days_ago,
            requested_clock_in=at(two_days_ago, 8), reason='Forgot', status='Pending',
        ))
        db.session.commit()
        staff_id = staff.id

    try:
        client = app.test_client()
        log_in(client, staff_id)
        with patch.dict(unwrap(app.view_functions['attendance_api']).__globals__, {'notify_attendance_event': Mock()}):
            response = client.post(f'/attendance_api/{staff_id}', data={
                'clockin': '1', 'event_time': at(today, 8).isoformat(),
            })
        assert response.json['status'] == 'success'
        alerts = response.json['missing_punches']
        assert [alert['date'] for alert in alerts] == [yesterday.isoformat(), two_days_ago.isoformat()]
        assert alerts[0]['missing'] == 'walang Clock Out'
        assert alerts[0]['correction_pending'] is False
        assert alerts[0]['correction_url'].endswith(f'correction_date={yesterday.isoformat()}')
        assert alerts[1]['missing'] == 'walang Clock In'
        assert alerts[1]['correction_pending'] is True

        dashboard = client.get('/dashboard_staff').get_data(as_text=True)
        assert 'id="missingPunchReminder"' in dashboard
        assert 'Paalala sa attendance at payroll' in dashboard
        assert f'correction_date={yesterday.isoformat()}' in dashboard
    finally:
        with app.app_context():
            db.session.rollback()
            AttendanceCorrection.query.filter_by(employee_id=staff_id).delete()
            Attendance.query.filter_by(employee_id=staff_id).delete()
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.commit()


def test_clock_in_without_missing_punches_returns_empty_reminder():
    today = datetime.now(ZoneInfo('Asia/Manila')).date()
    with app.app_context():
        staff = Employee(first_name='Clean', last_name='Punch', role='staff', company='Trece-Uno')
        db.session.add(staff)
        db.session.commit()
        staff_id = staff.id
    try:
        client = app.test_client()
        log_in(client, staff_id)
        with patch.dict(unwrap(app.view_functions['attendance_api']).__globals__, {'notify_attendance_event': Mock()}):
            response = client.post(f'/attendance_api/{staff_id}', data={
                'clockin': '1', 'event_time': at(today, 8).isoformat(),
            })
        assert response.json['status'] == 'success'
        assert response.json['missing_punches'] == []
    finally:
        with app.app_context():
            db.session.rollback()
            Attendance.query.filter_by(employee_id=staff_id).delete()
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.commit()


def test_attendance_card_shows_outstanding_thirteenth_month():
    with app.app_context():
        staff = Employee(first_name='Thirteenth', last_name='Card', role='staff', company='Trece-Uno')
        db.session.add(staff)
        db.session.commit()
        staff_id = staff.id
    try:
        client = app.test_client()
        log_in(client, staff_id)
        rows = Mock(return_value=[
            {'employee': None, 'basic_pay': 60000.0, 'thirteenth_month': 5000.0, 'payroll_periods': 4,
             'source': 'attendance', 'daily_rate': 645.0, 'days_paid': 80, 'full_day_pay': 64000.0,
             'rate_lines': [
                 {'rate': 600.0, 'days': 70, 'amount': 42000.0,
                  'first_date': date(2026, 1, 2), 'last_date': date(2026, 10, 27)},
                 {'rate': 645.0, 'days': 10, 'amount': 6450.0,
                  'first_date': date(2026, 10, 28), 'last_date': date(2026, 11, 6)},
             ],
             'short_deduction': 4000.0, 'hours_short': 40.0, 'short_days': 9, 'absent_days': 3,
             'holiday_days': 2, 'holiday_excluded': 1200.0, 'coverage_start': date(2026, 9, 1)},
        ])
        # Patch the globals the route actually uses; another test re-imports `hris`.
        view_globals = unwrap(app.view_functions['dashboard_staff']).__globals__
        with patch.dict(view_globals, {'build_thirteenth_month_rows': rows}):
            html = client.get('/dashboard_staff').get_data(as_text=True)
        rows.assert_called_once_with(datetime.now(ZoneInfo('Asia/Manila')).year, staff_id)
        attendance_card = html[html.index('<h3>Attendance</h3>'):html.index('<h3>Payroll</h3>')]
        assert 'Outstanding prorated 13th month pay' in attendance_card
        assert 'PHP 5,000.00' in attendance_card
        assert 'PHP 60,000.00' in attendance_card
        assert '÷ 12' in attendance_card
        computation = attendance_card[:attendance_card.index('<details>')]
        assert 'PHP 5,000.00' in computation and 'PHP 60,000.00' in computation
        assert '70 day(s) × PHP 600.00 (Jan 02–Oct 27)' in computation
        assert '10 day(s) × PHP 645.00 (Oct 28–Nov 06)' in computation
        assert 'PHP 42,000.00' in computation and 'PHP 6,450.00' in computation
        assert 'PHP 1,200.00 holiday premium excluded from 2 day(s)' in computation
        assert 'Less late / undertime / half day (40.0 hrs, 9 day(s))' in computation
        assert '− PHP 4,000.00' in computation
        assert 'Absent days (3)' in computation
        assert 'Basic pay recorded in HRIS (Sep 01 to today)' in computation
        assert 'Basic pay before Sep 01, 2026 is computed outside HRIS' in computation
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.commit()

def test_thirteenth_month_breakdown_deducts_short_hours_and_skips_absent():
    from hris import build_thirteenth_month_rows
    from models import Holiday
    today = datetime.now(ZoneInfo('Asia/Manila')).date()
    year_start = today.replace(month=1, day=1)
    workdays = []
    day = today - timedelta(days=7)
    while len(workdays) < 3 and day >= year_start:
        with app.app_context():
            is_holiday = Holiday.query.filter_by(date=day).first() is not None
        if day.weekday() != 6 and not is_holiday:
            workdays.append(day)
        day -= timedelta(days=1)
    full_day, half_day, absent_day = workdays
    with app.app_context():
        staff = Employee(first_name='Thirteenth', last_name='Breakdown', role='staff',
                         company='Trece-Uno', daily_rate=800)
        db.session.add(staff)
        db.session.flush()
        db.session.add_all([
            Attendance(employee_id=staff.id, date=full_day, clock_in=at(full_day, 8),
                       clock_out=at(full_day, 17), status='Present', company='Trece-Uno'),
            Attendance(employee_id=staff.id, date=half_day, clock_in=at(half_day, 8),
                       clock_out=at(half_day, 12), status='Present', company='Trece-Uno'),
            Attendance(employee_id=staff.id, date=absent_day, status='Absent', company='Trece-Uno'),
        ])
        db.session.commit()
        staff_id = staff.id
    try:
        with app.app_context():
            row = build_thirteenth_month_rows(today.year, staff_id)[0]
        assert row['source'] == 'attendance'
        assert row['days_paid'] == 2
        assert row['full_day_pay'] == 1600.0
        assert row['short_days'] >= 1 and row['hours_short'] >= 4.0
        assert row['short_deduction'] == round(row['full_day_pay'] - row['basic_pay'], 2)
        assert row['basic_pay'] < 1600.0
        assert row['absent_days'] == 1
        assert row['thirteenth_month'] == round(row['basic_pay'] / 12, 2)
    finally:
        with app.app_context():
            db.session.rollback()
            Attendance.query.filter_by(employee_id=staff_id).delete()
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.commit()
