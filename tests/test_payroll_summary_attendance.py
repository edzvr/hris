from datetime import date, datetime, timedelta
from io import BytesIO
import re
from unittest.mock import patch

import pytest
from pypdf import PdfReader
from werkzeug.http import parse_options_header

from hris import (
    app, attach_payroll_summary_attendance_and_loans, calculate_payroll_snapshot,
    ensure_employee_hr_columns, payroll_summary_pdf,
)
from models import Attendance, Employee, Holiday, LeaveRequest, Payroll, PayslipVerification, db


START = date(2026, 10, 31)
END = START + timedelta(days=7)


@pytest.fixture
def summary_people():
    with app.app_context():
        holiday = Holiday.query.filter_by(date=date(2026, 11, 2)).first()
        created_holiday = holiday is None
        if created_holiday:
            db.session.add(Holiday(date=date(2026, 11, 2), description='All Souls Test', holiday_type='Special Non-Working Holiday'))
        finalized = Employee(first_name='Summary', last_name='Finalized', role='staff', company='Trece-Uno', loan_balance=1500)
        draft = Employee(first_name='Summary', last_name='Draft', role='staff', company='Trece-Uno', loan_balance=2000)
        db.session.add_all([finalized, draft])
        db.session.flush()

        def punch(employee, day, status='Present', clock_in=8, clock_out=17):
            return Attendance(
                employee_id=employee.id, date=day, status=status,
                clock_in=datetime.combine(day, datetime.min.time()).replace(hour=clock_in) if clock_in else None,
                clock_out=datetime.combine(day, datetime.min.time()).replace(hour=clock_out) if clock_out else None,
            )

        db.session.add_all([
            punch(finalized, date(2026, 10, 31), status='Late', clock_in=9),
            punch(finalized, date(2026, 11, 3)),
            punch(finalized, date(2026, 11, 4), clock_out=None),
            punch(finalized, date(2026, 11, 5), clock_in=None),
            # Nov 6 has no record -> absent; Nov 2 holiday without record is not absent.
            LeaveRequest(employee_id=finalized.id, start_date=date(2026, 11, 7), end_date=date(2026, 11, 7),
                         days=1, leave_type='SIL', status='Approved'),
            Payroll(employee_id=finalized.id, cutoff_start=START, cutoff_end=END - timedelta(days=1),
                    is_paid=True, loan=500, loan_deduction_applied=True, net_pay=1000),
            Payroll(employee_id=draft.id, cutoff_start=START, cutoff_end=END - timedelta(days=1),
                    is_paid=False, loan=500, net_pay=1000),
        ])
        db.session.commit()
        ids = finalized.id, draft.id
    try:
        yield ids
    finally:
        with app.app_context():
            db.session.rollback()
            for model in (PayslipVerification, Attendance, LeaveRequest, Payroll):
                model.query.filter(model.employee_id.in_(ids)).delete(synchronize_session=False)
            Employee.query.filter(Employee.id.in_(ids)).delete(synchronize_session=False)
            if created_holiday:
                Holiday.query.filter_by(date=date(2026, 11, 2)).delete()
            db.session.commit()


def summary_rows(ids):
    rows = []
    for employee_id in ids:
        employee = db.session.get(Employee, employee_id)
        record = Payroll.query.filter_by(employee_id=employee_id, cutoff_start=START).first()
        rows.append({
            'employee': employee, 'is_admin': False, 'worked_days': 0, 'gross_income': 0.0,
            'sss': 0.0, 'philhealth': 0.0, 'pagibig': 0.0, 'withholding_tax': 0.0,
            'loan': float(record.loan or 0), 'other_deductions_total': 0.0,
            'total_deductions': 0.0, 'net_pay': 0.0, 'payroll_record': record,
        })
    return rows


def test_summary_lists_absent_late_missing_punch_and_remaining_loan(summary_people):
    with app.app_context():
        rows = summary_rows(summary_people)
        attach_payroll_summary_attendance_and_loans(rows, START, END, today=date(2026, 11, 7))
        finalized, draft = rows
        assert finalized['attendance_notes'] == {
            'tracked': True,
            'absent': ['Nov 06'],
            'late': ['Oct 31'],
            'missing_punch': ['Nov 04 (no out)', 'Nov 05 (no in)'],
        }
        # Finalized loan was already deducted from the balance; draft still deducts this cutoff.
        assert finalized['loan_remaining'] == 1500
        assert draft['loan_remaining'] == 1500
        assert 'Nov 02' not in draft['attendance_notes']['absent']

        text = ''.join(
            page.extract_text()
            for page in PdfReader(BytesIO(payroll_summary_pdf('Trece-Uno', START, END, rows, summary_people[0]))).pages
        )
        assert 'ATTENDANCE & LOAN BALANCE' in text
        assert 'Nov 04 (no out)' in text
        assert 'PHP 1,500.00' in text


@pytest.fixture
def pdf_visibility_people():
    with app.app_context():
        admin = Employee(first_name='Report', last_name='Admin', role='admin')
        db.session.add(admin)
        people = {}
        for company, prefix in (('Trece-Uno', 'Trece'), ('Auto Expert', 'Auto')):
            visible = Employee(
                first_name=prefix, last_name='Visible', role='staff', company=company,
                employment_status='Trainee', job_description='Office Staff',
                payroll_preparation_access=True,
            )
            hidden = Employee(
                first_name=prefix, last_name='Hidden', role='staff', company=company,
                employment_status='Regular', job_description='Office Staff',
            )
            hidden.hide_from_payroll_summary_pdf = True
            db.session.add_all([visible, hidden])
            db.session.flush()
            visible_payroll = Payroll(
                employee_id=visible.id, cutoff_start=START, cutoff_end=END - timedelta(days=1),
                is_paid=True, gross_income=700, total_deductions=100, net_pay=600,
            )
            hidden_payroll = Payroll(
                employee_id=hidden.id, cutoff_start=START, cutoff_end=END - timedelta(days=1),
                is_paid=True, gross_income=1000, total_deductions=100, net_pay=900,
            )
            db.session.add_all([visible_payroll, hidden_payroll])
            db.session.flush()
            people[company] = {
                'visible_id': visible.id, 'hidden_id': hidden.id,
                'hidden_payroll_id': hidden_payroll.id,
                'visible_name': visible.full_name(), 'hidden_name': hidden.full_name(),
            }
        db.session.commit()
        admin_id = admin.id
        employee_ids = [admin_id] + [
            item[key] for item in people.values() for key in ('visible_id', 'hidden_id')
        ]
    try:
        yield admin_id, people
    finally:
        with app.app_context():
            db.session.rollback()
            PayslipVerification.query.filter(
                PayslipVerification.employee_id.in_(employee_ids)
            ).delete(synchronize_session=False)
            Payroll.query.filter(
                Payroll.employee_id.in_(employee_ids)
            ).delete(synchronize_session=False)
            Employee.query.filter(
                Employee.id.in_(employee_ids)
            ).delete(synchronize_session=False)
            db.session.commit()


def log_in(client, employee_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True


def pdf_text(data):
    return '\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(data)).pages)


def test_downloaded_summary_hides_only_marked_rows(pdf_visibility_people):
    admin_id, people = pdf_visibility_people
    client = app.test_client()
    log_in(client, admin_id)

    with app.app_context():
        before = {
            record.id: (record.is_paid, record.gross_income, record.net_pay)
            for record in Payroll.query.filter(
                Payroll.employee_id.in_([
                    item[key] for item in people.values()
                    for key in ('visible_id', 'hidden_id')
                ])
            )
        }

    for company, item in people.items():
        query = {'company': company, 'cutoff_start': START.isoformat()}
        html = client.get('/payroll/summary', query_string={**query, 'view': 'true'})
        assert html.status_code == 200
        assert item['visible_name'] in html.get_data(as_text=True)
        assert item['hidden_name'] in html.get_data(as_text=True)

        download = client.get('/payroll/summary', query_string=query)
        assert download.status_code == 200
        filename = parse_options_header(download.headers['Content-Disposition'])[1]['filename']
        assert filename.startswith(
            f'Payroll_Summary_{START}_to_{END - timedelta(days=1)}_{company}_DL_'
        )
        assert filename.endswith('_PHT.pdf')
        text = pdf_text(download.data)
        assert item['visible_name'] in text
        assert item['hidden_name'] not in text
        assert 'PHP 600.00' in text

        log_in(client, item['visible_id'])
        staff_download = client.get('/payroll/summary', query_string=query)
        assert staff_download.status_code == 200
        assert item['hidden_name'] not in pdf_text(staff_download.data)
        log_in(client, admin_id)

    hidden = people['Trece-Uno']
    individual = client.get(
        f"/payslip/{hidden['hidden_id']}/{hidden['hidden_payroll_id']}/download"
    )
    assert individual.status_code == 200
    assert hidden['hidden_name'] in pdf_text(individual.data)

    with app.app_context():
        after = {
            record.id: (record.is_paid, record.gross_income, record.net_pay)
            for record in Payroll.query.filter(Payroll.id.in_(before))
        }
        assert after == before


def test_emailed_summary_keeps_all_rows_and_verifies_each_total(pdf_visibility_people):
    admin_id, people = pdf_visibility_people
    client = app.test_client()
    log_in(client, admin_id)
    with patch('hris.completed_cutoff', return_value=(START, END)), patch(
        'hris.send_notification_email', return_value=True,
    ) as send_email:
        response = client.post(
            '/payroll/summary/email',
            data={'company': 'Trece-Uno', 'email': 'test@example.invalid'},
        )
    assert response.status_code == 302
    email_pdf = send_email.call_args.kwargs['attachments'][0][1]
    email_text = pdf_text(email_pdf)
    assert people['Trece-Uno']['hidden_name'] in email_text
    assert 'PHP 1,500.00' in email_text

    download = client.get(
        '/payroll/summary',
        query_string={'company': 'Trece-Uno', 'cutoff_start': START.isoformat()},
    )
    download_text = pdf_text(download.data)
    assert people['Trece-Uno']['hidden_name'] not in download_text
    assert 'PHP 600.00' in download_text
    email_id = re.search(r'Document ID:\s*(\S+)', email_text).group(1)
    download_id = re.search(r'Document ID:\s*(\S+)', download_text).group(1)
    assert email_id != download_id
    with app.app_context():
        email_record = PayslipVerification.query.filter_by(document_id=email_id).one()
        download_record = PayslipVerification.query.filter_by(document_id=download_id).one()
        assert email_record.net_pay == 1500
        assert download_record.net_pay == 600


def test_only_admin_can_change_download_summary_visibility(pdf_visibility_people):
    admin_id, people = pdf_visibility_people
    item = people['Trece-Uno']
    client = app.test_client()
    log_in(client, admin_id)
    profile_url = f"/profile/{item['visible_id']}"
    assert b'name="hide_from_payroll_summary_pdf"' in client.get(profile_url).data
    form = {
        'first_name': 'Trece', 'last_name': 'Visible',
        'job_description': 'Office Staff', 'employment_status': 'Trainee',
        'role': 'staff', 'company': 'Trece-Uno',
        'payroll_summary_pdf_setting': '1', 'hide_from_payroll_summary_pdf': '1',
    }
    assert client.post(profile_url, data=form).status_code == 302
    with app.app_context():
        ensure_employee_hr_columns()
        assert db.session.get(Employee, item['visible_id']).hide_from_payroll_summary_pdf

    log_in(client, item['visible_id'])
    assert b'name="hide_from_payroll_summary_pdf"' not in client.get(profile_url).data
    without_checkbox = {key: value for key, value in form.items()
                        if key != 'hide_from_payroll_summary_pdf'}
    assert client.post(profile_url, data=without_checkbox).status_code == 302
    with app.app_context():
        assert db.session.get(Employee, item['visible_id']).hide_from_payroll_summary_pdf

    log_in(client, admin_id)
    assert client.post(profile_url, data=without_checkbox).status_code == 302
    with app.app_context():
        assert not db.session.get(Employee, item['visible_id']).hide_from_payroll_summary_pdf


@pytest.mark.parametrize('finalized', [False, True])
def test_summary_gross_includes_rice_without_changing_payroll(finalized):
    start, end = date(2026, 11, 7), date(2026, 11, 14)
    with app.app_context():
        admin = Employee(first_name='Gross', last_name='Admin', role='admin')
        staff = Employee(
            first_name='Gross', last_name='Staff', role='staff',
            company='Trece-Uno', daily_rate=645, rice_allowance_per_day=95,
        )
        db.session.add_all([admin, staff])
        db.session.flush()
        for day in (7, 9, 10, 11, 12, 13):
            db.session.add(Attendance(
                employee_id=staff.id, date=date(2026, 11, day), status='Present',
                clock_in=datetime(2026, 11, day, 8),
                clock_out=datetime(2026, 11, day, 17),
            ))
        db.session.add(Attendance(
            employee_id=staff.id, date=date(2026, 11, 8), status='Present',
            clock_in=datetime(2026, 11, 8, 8), clock_out=datetime(2026, 11, 8, 12),
        ))
        db.session.flush()
        record = Payroll(
            employee_id=staff.id, cutoff_start=start, cutoff_end=end - timedelta(days=1),
            is_paid=False, gross_income=0, total_deductions=0, net_pay=0,
        )
        snapshot = calculate_payroll_snapshot(staff, record, start, end)
        assert snapshot['gross_income'] == pytest.approx(4289.25)
        assert snapshot['net_pay'] + snapshot['total_deductions'] == pytest.approx(4859.25)
        if finalized:
            for key, value in snapshot.items():
                setattr(record, key, value)
            record.is_paid = True
        db.session.add(record)
        db.session.commit()
        admin_id, staff_id, payroll_id = admin.id, staff.id, record.id
        saved = (record.gross_income, record.total_deductions, record.net_pay)

    try:
        client = app.test_client()
        log_in(client, admin_id)
        query = {'company': 'Trece-Uno', 'cutoff_start': start.isoformat()}
        html = client.get('/payroll/summary', query_string={**query, 'view': 'true'})
        assert html.status_code == 200
        row = html.get_data(as_text=True).split('<td>Gross Staff</td>', 1)[1].split('</tr>', 1)[0]
        assert '<td>6</td>' in row
        assert '<td>₱4,859.25</td>' in row
        assert f"₱{snapshot['net_pay']:,.2f}" in row

        download = client.get('/payroll/summary', query_string=query)
        assert download.status_code == 200
        assert '4,859.25' in pdf_text(download.data)
        with app.app_context():
            unchanged = db.session.get(Payroll, payroll_id)
            assert (unchanged.gross_income, unchanged.total_deductions, unchanged.net_pay) == saved
    finally:
        with app.app_context():
            db.session.rollback()
            PayslipVerification.query.filter_by(employee_id=admin_id).delete()
            db.session.delete(db.session.get(Payroll, payroll_id))
            Attendance.query.filter_by(employee_id=staff_id).delete()
            for employee_id in (admin_id, staff_id):
                db.session.delete(db.session.get(Employee, employee_id))
            db.session.commit()


def test_summary_does_not_double_count_taxable_rice():
    start, end = date(2026, 10, 31), date(2026, 11, 7)
    with app.app_context():
        admin = Employee(first_name='Limit', last_name='Admin', role='admin')
        staff = Employee(
            first_name='Limit', last_name='Staff', role='staff',
            company='Trece-Uno', daily_rate=645, rice_allowance_per_day=95,
        )
        db.session.add_all([admin, staff])
        db.session.flush()
        day = date(2026, 10, 3)
        while day < end:
            if day.weekday() != 6:
                db.session.add(Attendance(
                    employee_id=staff.id, date=day, status='Present',
                    clock_in=datetime.combine(day, datetime.min.time()).replace(hour=8),
                    clock_out=datetime.combine(day, datetime.min.time()).replace(hour=17),
                ))
            day += timedelta(days=1)
        db.session.flush()
        record = Payroll(employee_id=staff.id, cutoff_start=start, cutoff_end=end - timedelta(days=1))
        snapshot = calculate_payroll_snapshot(staff, record, start, end)
        exempt = snapshot['net_pay'] + snapshot['total_deductions'] - snapshot['gross_income']
        assert exempt == pytest.approx(220)
        expected_gross = snapshot['gross_income'] + exempt
        db.session.commit()
        admin_id, staff_id = admin.id, staff.id

    try:
        client = app.test_client()
        log_in(client, admin_id)
        html = client.get(
            '/payroll/summary',
            query_string={'company': 'Trece-Uno', 'cutoff_start': start.isoformat(), 'view': 'true'},
        ).get_data(as_text=True)
        row = html.split('<td>Limit Staff</td>', 1)[1].split('</tr>', 1)[0]
        assert f'₱{expected_gross:,.2f}' in row
        assert f"₱{snapshot['gross_income'] + 570:,.2f}" not in row
    finally:
        with app.app_context():
            db.session.rollback()
            Attendance.query.filter_by(employee_id=staff_id).delete()
            for employee_id in (admin_id, staff_id):
                db.session.delete(db.session.get(Employee, employee_id))
            db.session.commit()
