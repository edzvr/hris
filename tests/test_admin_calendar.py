from datetime import date

from hris import admin_request_calendar, app
from models import Employee, LeaveRequest, Loan, db


def test_calendar_includes_spanning_leave_loan_dates_and_existing_deadlines():
    with app.test_request_context():
        employee = Employee(first_name='Calendar', last_name='Test', role='staff')
        db.session.add(employee)
        db.session.flush()
        db.session.add_all([
            LeaveRequest(employee_id=employee.id, leave_type='SIL', days=3,
                         start_date=date(2026, 9, 30), end_date=date(2026, 10, 2), status='Approved'),
            LeaveRequest(employee_id=employee.id, leave_type='SIL', days=2,
                         start_date=date(2026, 10, 31), end_date=date(2026, 11, 1), status='Pending'),
            Loan(employee_id=employee.id, amount=1000, date_needed=date(2026, 10, 6),
                 reason='Calendar test', status='Pending'),
            Loan(employee_id=employee.id, amount=2000, date_needed=date(2026, 11, 6),
                 reason='Outside month', status='Approved'),
        ])
        db.session.flush()
        try:
            calendar = admin_request_calendar(date(2026, 10, 1))
            events = calendar['events']
            assert calendar['previous'] == '2026-09'
            assert calendar['next'] == '2026-11'
            assert all(len(week) == 7 for week in calendar['weeks'])
            assert not events.get(date(2026, 9, 30))
            assert not events.get(date(2026, 11, 1))
            for day in (1, 2):
                assert any('Leave (Approved)' in event['label'] for event in events[date(2026, 10, day)])
            assert any('Leave (Pending)' in event['label'] for event in events[date(2026, 10, 31)])
            assert len([event for event in events[date(2026, 10, 6)] if event['kind'] == 'loan']) == 1
            assert all(event['url'] == '/loan' for event in events[date(2026, 10, 6)])
            payroll_days = [day.day for day, items in events.items()
                            if any('Payroll' in item['label'] for item in items)]
            assert payroll_days == [2, 9, 16, 23, 30]
            assert any('Month-end' in event['label'] for event in events[date(2026, 10, 31)])
        finally:
            db.session.rollback()


def test_calendar_month_validation_and_staff_cannot_view_admin_calendar():
    with app.app_context():
        users = [Employee(first_name='Calendar', last_name=role, role=role) for role in ('staff', 'admin')]
        db.session.add_all(users)
        db.session.commit()
        ids = [user.id for user in users]
    client = app.test_client()
    try:
        with client.session_transaction() as session:
            session['_user_id'] = str(ids[0])
            session['_fresh'] = True
        assert client.get('/dashboard_admin?calendar_month=2026-10').status_code == 302
        with client.session_transaction() as session:
            session['_user_id'] = str(ids[1])
            session['_fresh'] = True
        for invalid in ('not-a-month', '2026-13', '0001-01'):
            response = client.get('/dashboard_admin?calendar_month=' + invalid)
            assert response.status_code == 302
            assert response.location.endswith('/dashboard_admin')
            with client.session_transaction() as session:
                assert any('Invalid calendar month' in message for _, message in session['_flashes'])
        response = client.get('/dashboard_admin?calendar_month=2026-12')
        assert response.status_code == 200
        assert 'Request Calendar - December 2026' in response.get_data(as_text=True)
    finally:
        with app.app_context():
            for user_id in ids:
                db.session.delete(db.session.get(Employee, user_id))
            db.session.commit()
