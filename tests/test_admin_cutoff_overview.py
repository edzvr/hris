from datetime import date, datetime, time

import pytest

from hris import admin_cutoff_overview, app
from models import (
    Attendance, AttendanceCorrection, Employee, LeaveRequest, Loan,
    LoanDeductionSkipRequest, OTApplication, Payroll, db,
)


START = date(2026, 10, 3)
TODAY = date(2026, 10, 7)


@pytest.fixture
def cutoff_people():
    with app.app_context():
        admin = Employee(first_name='Overview', last_name='Admin', role='admin')
        first = Employee(first_name='Same', last_name='Name', role='staff', company='Auto Expert', loan_balance=4000)
        second = Employee(first_name='Same', last_name='Name', role='staff', company='Trece-Uno', loan_balance=5000)
        exempt = Employee(first_name='Exempt', last_name='Staff', role='staff', company='Auto Expert', payroll_attendance_exempt=True)
        db.session.add_all([admin, first, second, exempt])
        db.session.flush()
        db.session.add_all([
            Attendance(employee_id=first.id, date=date(2026, 10, 3), status='Present',
                       clock_in=datetime(2026, 10, 3, 8), clock_out=datetime(2026, 10, 3, 17)),
            Attendance(employee_id=first.id, date=date(2026, 10, 5), status='Present',
                       clock_in=datetime(2026, 10, 5, 8)),
            Attendance(employee_id=first.id, date=date(2026, 10, 6), status='Absent'),
            Attendance(employee_id=first.id, date=TODAY, status='Present', clock_in=datetime(2026, 10, 7, 8)),
            Attendance(employee_id=second.id, date=date(2026, 10, 3), status='Late',
                       clock_in=datetime(2026, 10, 3, 9), clock_out=datetime(2026, 10, 3, 17)),
            LeaveRequest(employee_id=second.id, start_date=date(2026, 10, 5), end_date=date(2026, 10, 5),
                         days=1, leave_type='SIL', status='Approved'),
            LeaveRequest(employee_id=second.id, start_date=TODAY, end_date=TODAY,
                         days=1, leave_type='SIL', status='Pending'),
            Payroll(employee_id=first.id, cutoff_start=date(2026, 9, 26), cutoff_end=date(2026, 10, 2),
                    is_paid=True, net_pay=1234, gross_income=1500, total_deductions=266),
            Payroll(employee_id=first.id, cutoff_start=date(2026, 9, 19), cutoff_end=date(2026, 9, 25),
                    is_paid=True, net_pay=1111),
            Payroll(employee_id=second.id, cutoff_start=date(2026, 9, 19), cutoff_end=date(2026, 9, 25),
                    is_paid=True, net_pay=2345),
            Payroll(employee_id=second.id, cutoff_start=date(2026, 9, 26), cutoff_end=date(2026, 10, 2),
                    is_paid=False, net_pay=999999),
            Payroll(employee_id=first.id, cutoff_start=START, cutoff_end=date(2026, 10, 9),
                    is_paid=False, gross_income=1500, total_deductions=513, net_pay=987),
            Payroll(employee_id=second.id, cutoff_start=START, cutoff_end=date(2026, 10, 9),
                    is_paid=True, gross_income=4000, total_deductions=544, net_pay=3456),
            Loan(employee_id=first.id, amount=1000, reason='Current cutoff', date_needed=TODAY, status='Pending'),
            Loan(employee_id=first.id, amount=1000, reason='Outside cutoff', date_needed=date(2026, 11, 1), status='Pending'),
            OTApplication(employee_id=first.id, ot_date=TODAY, start_time=time(17), end_time=time(18), reason='Work', status='Pending'),
            AttendanceCorrection(employee_id=first.id, correction_date=date(2026, 10, 5), reason='Missing out', status='Pending'),
            LoanDeductionSkipRequest(employee_id=first.id, cutoff_start=START, cutoff_end=date(2026, 10, 9),
                                     reason='Emergency', status='Pending'),
        ])
        db.session.commit()
        ids = admin.id, first.id, second.id, exempt.id
    try:
        yield ids
    finally:
        with app.app_context():
            db.session.rollback()
            for model in (AttendanceCorrection, Attendance, LeaveRequest, Loan, LoanDeductionSkipRequest, OTApplication, Payroll):
                model.query.filter(model.employee_id.in_(ids)).delete(synchronize_session=False)
            Employee.query.filter(Employee.id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()


def test_cutoff_days_distinguish_absence_missing_out_leave_rest_and_future(cutoff_people):
    with app.app_context():
        overview = admin_cutoff_overview(START, today=TODAY)
        rows = {row['employee'].id: row for row in overview['rows']}
        first, second, exempt = (rows[employee_id] for employee_id in cutoff_people[1:])
        days = {day['date']: day for day in first['days']}
        assert days[date(2026, 10, 4)]['status'] == 'Rest day'
        assert days[date(2026, 10, 5)]['status'] == 'Missing clock-out'
        assert days[date(2026, 10, 6)]['status'] == 'Absent recorded'
        assert days[TODAY]['status'] == 'Clocked in / awaiting out'
        assert days[date(2026, 10, 8)]['status'] == 'Upcoming'
        assert first['counts']['missing_out'] == 1
        assert first['counts']['absent'] == 1
        assert first['counts']['working'] == 1
        second_days = {day['date']: day for day in second['days']}
        assert second_days[date(2026, 10, 4)]['status'] == 'No record / verify'
        assert second_days[date(2026, 10, 5)]['status'] == 'Approved leave'
        assert second['counts']['late'] == 1
        assert second['counts']['no_record'] == 2
        assert all(day['status'] in {'Attendance exempt', 'Upcoming'} for day in exempt['days'])
        assert overview['totals']['missing_out'] == overview['totals']['absent'] == 1
        assert overview['totals']['no_record'] == 2
        assert overview['totals']['pending_requests'] == 5
        assert first['requests'] == {'loans': 1, 'overtime': 1, 'corrections': 1, 'skips': 1, 'leaves': 0}


def test_overview_reads_saved_payroll_per_employee_without_mutations(cutoff_people):
    with app.app_context():
        before = [(p.id, p.is_paid, p.net_pay, p.payslip_snapshot) for p in Payroll.query.order_by(Payroll.id)]
        overview = admin_cutoff_overview(START, today=TODAY)
        rows = {row['employee'].id: row for row in overview['rows']}
        first, second = rows[cutoff_people[1]], rows[cutoff_people[2]]
        assert first['previous_payroll'].net_pay == 1234
        assert first['previous_payroll'].cutoff_end == date(2026, 10, 2)
        assert second['previous_payroll'].net_pay == 2345
        assert second['previous_payroll'].cutoff_end == date(2026, 9, 25)
        assert first['payroll_state'] == 'Draft'
        assert second['payroll_state'] == 'Finalized'
        assert overview['finalized_net'] == 3456
        assert overview['totals']['finalized'] == overview['totals']['draft'] == 1
        assert overview['totals']['not_prepared'] == 0
        db.session.commit()
        assert [(p.id, p.is_paid, p.net_pay, p.payslip_snapshot) for p in Payroll.query.order_by(Payroll.id)] == before
        assert db.session.get(Employee, cutoff_people[1]).loan_balance == 4000


def test_duplicate_attendance_and_payroll_are_flagged_not_silently_selected(cutoff_people):
    with app.app_context():
        staff_id = cutoff_people[1]
        db.session.add_all([
            Attendance(employee_id=staff_id, date=date(2026, 10, 3), status='Late'),
            Payroll(employee_id=staff_id, cutoff_start=date(2026, 9, 26), cutoff_end=date(2026, 10, 2), net_pay=999, is_paid=True),
            Payroll(employee_id=staff_id, cutoff_start=START, cutoff_end=date(2026, 10, 9), net_pay=999, is_paid=True),
        ])
        db.session.flush()
        row = admin_cutoff_overview(START, employee_ids=[staff_id], today=TODAY)['rows'][0]
        assert row['days'][0]['status'] == 'Duplicate records'
        assert row['previous_payroll'] is None
        assert row['previous_duplicate']
        assert row['payroll_state'] == 'Duplicate records'
        assert 'payroll' in row['flags']
        db.session.rollback()


def test_company_dashboard_overview_links_filters_empty_state_and_access(cutoff_people):
    admin_id, first_id, second_id, _ = cutoff_people
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True
    for company, included, excluded in (
        ('Auto Expert', first_id, second_id), ('Trece-Uno', second_id, first_id),
    ):
        response = client.get('/dashboard_admin', query_string={'company': company, 'overview_cutoff': str(START)})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert f'data-cutoff-employee="{included}"' in html
        assert f'data-cutoff-employee="{excluded}"' not in html
        assert 'id="cutoff-search"' in html and 'data-cutoff-filter="missing-out"' in html
        assert f'/attendance/{included}?cutoff_start={START}' in html
        assert f'/payroll/{included}?cutoff_start={START}' in html
        assert 'overview_cutoff=2026-09-26' in html
        assert 'name="company"' in html
        assert 'Last finalized take-home pay' in html
        assert 'Current cutoff saved payroll totals' in html
        assert 'Gross' in html and 'Deductions' in html
        if company == 'Auto Expert':
            assert 'Draft net' in html
            assert '1,500.00' in html and '513.00' in html and '987.00' in html
        else:
            assert 'Final net' in html
            assert '4,000.00' in html and '544.00' in html and '3,456.00' in html
    assert client.get('/dashboard_admin?overview_cutoff=2026-10-04').status_code == 302
    with app.app_context():
        empty = admin_cutoff_overview(START, employee_ids=[], today=TODAY)
        assert empty['rows'] == []
        assert empty['finalized_net'] == 0
        assert admin_cutoff_overview(date(2027, 1, 2), employee_ids=[first_id], today=TODAY)['rows'][0]['counts']['no_record'] == 0
    with client.session_transaction() as session:
        session['_user_id'] = str(first_id)
    assert client.get('/dashboard_admin').status_code == 302
