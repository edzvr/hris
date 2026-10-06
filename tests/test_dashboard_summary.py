from datetime import timedelta

from hris import app, dashboard_summary
from models import Attendance, Employee, Evaluation, LeaveRequest, Loan, Payroll, db


def test_summary_uses_saved_payroll_balances_and_role_scope():
    with app.app_context():
        today = dashboard_summary()['today']
        first = Employee(first_name='Summary', last_name='First', role='staff', company='Trece-Uno', loan_balance=321, sil_credits=3)
        second = Employee(first_name='Summary', last_name='Second', role='staff', company='Auto Expert', loan_balance=654)
        admin = Employee(first_name='Summary', last_name='Admin', role='admin')
        db.session.add_all([first, second, admin])
        db.session.flush()
        cutoff = today - timedelta(days=14)
        end = cutoff + timedelta(days=6)
        db.session.add_all([
            Attendance(employee_id=first.id, date=today, status='Late'),
            Attendance(employee_id=first.id, date=today.replace(day=1) - timedelta(days=1), status='Absent'),
            Attendance(employee_id=second.id, date=today, status='Present'),
            Attendance(employee_id=admin.id, date=today, status='Present'),
            Payroll(employee_id=first.id, cutoff_start=cutoff, cutoff_end=end, net_pay=1200, is_paid=True),
            Payroll(employee_id=second.id, cutoff_start=cutoff, cutoff_end=end, net_pay=2400, is_paid=True),
            Payroll(employee_id=first.id, cutoff_start=cutoff, cutoff_end=end, net_pay=9999, is_paid=False),
            Payroll(employee_id=first.id, cutoff_start=today, cutoff_end=today, net_pay=8888, is_paid=False),
            LeaveRequest(employee_id=first.id, leave_type='SIL', days=1, start_date=today, end_date=today, status='Pending'),
            Loan(employee_id=first.id, amount=5000, date_needed=today, reason='Test', status='Pending'),
            Evaluation(employee_id=first.id, rating=4, approval_status='Approved'),
            Evaluation(employee_id=first.id, rating=1, approval_status='Pending'),
            Evaluation(employee_id=second.id, rating=2, approval_status='Approved'),
        ])
        db.session.flush()
        try:
            own = dashboard_summary(first)
            assert own['attendance_count'] == 1
            assert (own['late'], own['present'], own['absent']) == (1, 0, 0)
            assert own['net_pay'] == 1200
            assert (own['finalized_count'], own['draft_count']) == (1, 1)
            assert own['loan_balance'] == 321
            assert own['pending_loans'] == own['pending_leaves'] == 1
            assert own['average_rating'] == 4
            assert own['pending_evaluations'] == 1
            company = dashboard_summary()
            assert company['net_pay'] == 3600
            assert company['attendance_count'] == 2
            assert company['average_rating'] == 3
            assert company['loan_balance'] == 975
        finally:
            db.session.rollback()


def test_empty_summary_and_authenticated_dashboard_links():
    with app.app_context():
        staff = Employee(first_name='Dashboard', last_name='Staff', role='staff', company='Trece-Uno', payroll_preparation_access=True)
        admin = Employee(first_name='Dashboard', last_name='Admin', role='admin', company='Trece-Uno')
        db.session.add_all([staff, admin])
        db.session.commit()
        user_ids = (staff.id, admin.id)
        empty = dashboard_summary(staff)
        assert empty['latest_payroll'] is None
        assert empty['net_pay'] == 0
        assert empty['average_rating'] is None
    try:
        client = app.test_client()
        for user_id, route in zip(user_ids, ('/dashboard_staff', '/dashboard_admin')):
            with client.session_transaction() as session:
                session['_user_id'] = str(user_id)
                session['_fresh'] = True
            response = client.get(route)
            assert response.status_code == 200, (response.status_code, response.location)
            html = response.get_data(as_text=True)
            for heading in ('Attendance', 'Payroll', 'Loan &amp; Leave', 'Performance', 'Reports &amp; Documents'):
                assert heading in html
            assert 'No finalized payroll yet.' in html
            if route == '/dashboard_staff':
                assert html.index('id="attendanceForm"') < html.index('Summary &amp; Reports')
                assert 'name="clockin"' in html and 'name="clockout"' in html
                assert 'All staff tools' in html
                assert 'Download Auto Expert Summary' in html
                assert 'Download Trece-Uno Summary' in html
    finally:
        with app.app_context():
            db.session.rollback()
            for user_id in user_ids:
                db.session.delete(db.session.get(Employee, user_id))
            db.session.commit()
