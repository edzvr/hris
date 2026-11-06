import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from hris import (
    PH_HOLIDAYS,
    app,
    calculate_payroll_snapshot,
    regular_day_pay,
    save_payroll_snapshot,
    seed_ph_holidays,
    unworked_regular_holiday_dates,
)
from models import Attendance, Employee, Holiday, LeaveRequest, Payroll, db


def at(day, hour):
    return datetime.combine(day, datetime.min.time()).replace(hour=hour)


def log_in(client, employee_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True


def test_seed_adds_official_holidays_without_overwriting_admin_entries():
    with app.app_context():
        Holiday.query.filter(Holiday.date == date(2026, 11, 2)).delete()
        admin_entry = Holiday(date=date(2026, 11, 2), description='Admin entry',
                              holiday_type='Regular Holiday')
        db.session.add(admin_entry)
        db.session.commit()
        try:
            seed_ph_holidays()
            assert seed_ph_holidays() == 0
            for day, _name, holiday_type in PH_HOLIDAYS[2026]:
                saved = Holiday.query.filter_by(date=day).first()
                assert saved is not None
                if day != date(2026, 11, 2):
                    assert saved.holiday_type == holiday_type
            assert Holiday.query.filter_by(date=date(2026, 11, 2)).one().description == 'Admin entry'
        finally:
            db.session.delete(db.session.get(Holiday, admin_entry.id))
            db.session.commit()
            seed_ph_holidays()


def test_worked_nov_2_special_day_is_paid_at_130_percent_automatically():
    with app.app_context():
        seed_ph_holidays()
        assert Holiday.query.filter_by(date=date(2026, 11, 2)).one().holiday_type == (
            'Special Non-Working Holiday'
        )
        attendance = SimpleNamespace(
            date=date(2026, 11, 2), clock_in=None, clock_out=None, hours=8,
            employee=SimpleNamespace(company='Trece-Uno'), employee_id=0, is_restday_ot=False,
        )
        assert round(regular_day_pay(attendance, 645), 2) == 838.50


def test_unworked_christmas_is_paid_when_present_before_and_after_skipping_special_day():
    cutoff_start, cutoff_end = date(2026, 12, 19), date(2026, 12, 25)
    with app.app_context():
        seed_ph_holidays()
        staff = Employee(first_name='Holiday', last_name='Pay', role='staff',
                         company='Auto Expert', daily_rate=600)
        db.session.add(staff)
        db.session.flush()
        # Dec 24 is a special non-working day, so Dec 23 counts as the workday before Christmas.
        before = Attendance(employee_id=staff.id, date=date(2026, 12, 23),
                            clock_in=at(date(2026, 12, 23), 8), clock_out=at(date(2026, 12, 23), 17),
                            status='Present', company='Auto Expert')
        after = Attendance(employee_id=staff.id, date=date(2026, 12, 26),
                           clock_in=at(date(2026, 12, 26), 8), clock_out=at(date(2026, 12, 26), 17),
                           status='Present', company='Auto Expert')
        record = Payroll(employee_id=staff.id, cutoff_start=cutoff_start, cutoff_end=cutoff_end)
        db.session.add_all([before, after, record])
        db.session.commit()
        staff_id = staff.id
        try:
            end = cutoff_end + timedelta(days=1)
            assert unworked_regular_holiday_dates(staff, cutoff_start, end, [before]) == [date(2026, 12, 25)]
            snapshot = calculate_payroll_snapshot(staff, record, cutoff_start, end)
            for field, value in snapshot.items():
                setattr(record, field, value)
            save_payroll_snapshot(record, snapshot)
            payslip = json.loads(record.payslip_snapshot)
            assert payslip['regular_holiday'] == 600.0
            assert payslip['unworked_regular_holiday_days'] == 1
            assert payslip['basic_pay'] == 600.0

            db.session.delete(after)
            db.session.commit()
            assert unworked_regular_holiday_dates(staff, cutoff_start, end, [before]) == []

            # Handbook: paid leave on the day after does not replace actual attendance.
            db.session.add(LeaveRequest(employee_id=staff_id, leave_type='SIL', days=1,
                                        start_date=date(2026, 12, 26), end_date=date(2026, 12, 26),
                                        status='Approved', is_paid=True))
            db.session.commit()
            assert unworked_regular_holiday_dates(staff, cutoff_start, end, [before]) == []
        finally:
            db.session.rollback()
            LeaveRequest.query.filter_by(employee_id=staff_id).delete()
            Payroll.query.filter_by(employee_id=staff_id).delete()
            Attendance.query.filter_by(employee_id=staff_id).delete()
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.commit()


def test_admin_can_add_update_and_remove_holidays():
    eid = date(2026, 5, 27)
    with app.app_context():
        Holiday.query.filter_by(date=eid).delete()
        admin = Employee(first_name='Holiday', last_name='Admin', role='admin')
        staff = Employee(first_name='Holiday', last_name='Staff', role='staff')
        db.session.add_all([admin, staff])
        db.session.commit()
        admin_id, staff_id = admin.id, staff.id
    try:
        client = app.test_client()
        log_in(client, staff_id)
        assert client.get('/admin/holidays').status_code == 403
        log_in(client, admin_id)
        page = client.get('/admin/holidays?year=2026').get_data(as_text=True)
        assert "All Souls&#39; Day" in page and 'Special Non-Working Holiday' in page
        with client.session_transaction() as session:
            token = session['holiday_csrf']
        form = {'date': eid.isoformat(), 'description': 'Eidul Adha',
                'holiday_type': 'Regular Holiday', 'csrf_token': token}
        assert client.post('/admin/holidays', data=dict(form, csrf_token='bad')).status_code == 400
        assert client.post('/admin/holidays', data=dict(form, holiday_type='Bogus')).status_code == 302
        with app.app_context():
            assert Holiday.query.filter_by(date=eid).first() is None
        assert client.post('/admin/holidays', data=form).status_code == 302
        assert client.post('/admin/holidays', data=dict(
            form, holiday_type='Special Non-Working Holiday')).status_code == 302
        with app.app_context():
            saved = Holiday.query.filter_by(date=eid).one()
            assert (saved.description, saved.holiday_type) == ('Eidul Adha', 'Special Non-Working Holiday')
        assert client.post('/admin/holidays', data={
            'date': eid.isoformat(), 'action': 'delete', 'csrf_token': token}).status_code == 302
        with app.app_context():
            assert Holiday.query.filter_by(date=eid).first() is None
    finally:
        with app.app_context():
            Holiday.query.filter_by(date=eid).delete()
            db.session.delete(db.session.get(Employee, admin_id))
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.commit()
