import importlib.util
import json
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pypdf import PdfReader
from flask import template_rendered

import hris
from hris import app, automatic_loan_deduction, build_payslip_breakdown
from models import AuditLog, Employee, Loan, LoanDeductionSkipRequest, Payroll, PayslipVerification, db


START = date(2026, 10, 10)


def client_for(employee_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True
    return client


@pytest.fixture
def people(monkeypatch):
    monkeypatch.setattr(hris, 'notify_admins', lambda *args, **kwargs: None)
    monkeypatch.setattr(hris, 'send_loan_email', lambda *args, **kwargs: None)
    monkeypatch.setattr(hris, 'send_notification_email', lambda *args, **kwargs: None)
    monkeypatch.setattr(hris, 'current_payroll_cutoff', lambda: (START, START + timedelta(days=6)))
    with app.app_context():
        admin = Employee(first_name='Auto Loan', last_name='Admin', role='admin')
        staff = Employee(
            first_name='Auto Loan', last_name='Staff', role='staff',
            company='Trece-Uno', daily_rate=600, allowance=2000, loan_balance=0,
        )
        db.session.add_all([admin, staff])
        db.session.commit()
        ids = admin.id, staff.id
    yield ids
    with app.app_context():
        db.session.rollback()
        for model in (PayslipVerification, LoanDeductionSkipRequest, Payroll, Loan, AuditLog):
            model.query.filter(model.employee_id.in_(ids)).delete(synchronize_session=False)
        Employee.query.filter(Employee.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


@pytest.mark.parametrize('amount', ['499', '0', '-500', 'nan', 'inf', 'bad', ''])
def test_application_rejects_deductions_below_minimum_or_nonfinite(people, amount):
    response = client_for(people[1]).post('/loan', data={
        'amount': '1000', 'deduction_per_cutoff': amount,
        'reason': 'Test repayment', 'date_needed': '2026-10-12',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'at least PHP 500' in response.data
    with app.app_context():
        assert Loan.query.filter_by(employee_id=people[1]).count() == 0


def test_selected_repayment_applies_once_and_additional_loan_keeps_one_deduction(people):
    admin_id, staff_id = people
    staff = client_for(staff_id)
    admin = client_for(admin_id)
    application = {
        'amount': '1000', 'deduction_per_cutoff': '750',
        'reason': 'Test repayment', 'date_needed': '2026-10-12',
    }
    assert staff.post('/loan', data=application).status_code == 302
    with app.app_context():
        loan = Loan.query.filter_by(employee_id=staff_id).one()
        loan_id = loan.id
        assert loan.deduction_per_cutoff == 750
        assert db.session.get(Employee, staff_id).loan_balance == 0
    assert admin.post(f'/approve_loan/{loan_id}').status_code == 302
    admin.post(f'/approve_loan/{loan_id}')
    with app.app_context():
        employee = db.session.get(Employee, staff_id)
        assert employee.loan_balance == 1000
        assert employee.loan_deduction_per_cutoff == 750
    assert staff.post('/loan', data={**application, 'deduction_per_cutoff': '1000'}).status_code == 302
    with app.app_context():
        assert Loan.query.filter_by(employee_id=staff_id).count() == 1
    staff.post('/loan', data=application)
    with app.app_context():
        second_id = Loan.query.filter_by(employee_id=staff_id, status='Pending').one().id
    admin.post(f'/approve_loan/{second_id}')
    with app.app_context():
        employee = db.session.get(Employee, staff_id)
        assert employee.loan_balance == 2000
        assert automatic_loan_deduction(employee, None, START) == 750
    preview = admin.get(f'/loan/{second_id}/agreement')
    assert b'750.00' in preview.data
    pdf = admin.post(f'/loan/{second_id}/agreement', data={'deduction_per_cutoff': '750'})
    assert pdf.status_code == 200
    assert '750.00 pesos' in ''.join(p.extract_text() for p in PdfReader(BytesIO(pdf.data)).pages)


def test_automatic_finalize_caps_last_payment_and_preserves_historical_remaining(people):
    admin_id, staff_id = people
    admin = client_for(admin_id)
    with app.app_context():
        db.session.get(Employee, staff_id).loan_balance = 1300
        db.session.commit()
    assert admin.get(f'/payroll/{staff_id}?cutoff_start={START}').status_code == 200
    with app.app_context():
        assert db.session.get(Employee, staff_id).loan_balance == 1300
        assert Payroll.query.filter_by(employee_id=staff_id).count() == 0
    payroll_ids = []
    for index, (deduction, remaining) in enumerate(((500, 800), (500, 300), (300, 0), (0, 0))):
        start = START + timedelta(days=7 * index)
        for _ in range(2):
            assert admin.post(f'/payroll/{staff_id}/finalize', data={'cutoff_start': str(start)}).status_code == 302
        with app.app_context():
            record = Payroll.query.filter_by(employee_id=staff_id, cutoff_start=start).one()
            employee = db.session.get(Employee, staff_id)
            assert record.loan == deduction
            assert employee.loan_balance == remaining
            assert record.loan_deduction_applied and record.is_paid
            assert json.loads(record.payslip_snapshot)['loan_remaining_balance'] == remaining
            payroll_ids.append(record.id)
    with app.app_context():
        employee = db.session.get(Employee, staff_id)
        employee.loan_balance = 9000
        db.session.commit()
        first = db.session.get(Payroll, payroll_ids[0])
        assert build_payslip_breakdown(employee, first)['loan_remaining_balance'] == 800
    staff = client_for(staff_id)
    page = staff.get(f'/payslip/{staff_id}/{payroll_ids[0]}')
    assert page.status_code == 200
    assert b'Remaining Loan after this cutoff' in page.data
    pdf = staff.get(f'/payslip/{staff_id}/{payroll_ids[0]}/download')
    assert pdf.status_code == 200
    text = ''.join(p.extract_text() for p in PdfReader(BytesIO(pdf.data)).pages)
    assert 'Remaining Loan' in text and '800.00' in text


@pytest.mark.parametrize('decision,expected', [('approve', 0), ('reject', 500)])
def test_cutoff_skip_controls_automatic_deduction_and_next_cutoff_resumes(people, decision, expected):
    admin_id, staff_id = people
    with app.app_context():
        db.session.get(Employee, staff_id).loan_balance = 2000
        db.session.commit()
    staff = client_for(staff_id)
    page = staff.get('/loan')
    assert b'name="skip_reason"' in page.data
    assert staff.post('/loan', data={
        'action': 'request_deduction_skip', 'skip_reason': 'Emergency for this cutoff',
    }).status_code == 302
    with app.app_context():
        request_id = LoanDeductionSkipRequest.query.filter_by(employee_id=staff_id).one().id
        assert automatic_loan_deduction(db.session.get(Employee, staff_id), None, START) == 500
    assert staff.post(f'/loan/deduction-skip/{request_id}/approve').status_code == 403
    admin = client_for(admin_id)
    assert admin.post(f'/loan/deduction-skip/{request_id}/{decision}', data={
        'decision_note': 'Reviewed',
    }).status_code == 302
    admin.post(f'/payroll/{staff_id}/finalize', data={'cutoff_start': str(START)})
    with app.app_context():
        record = Payroll.query.filter_by(employee_id=staff_id, cutoff_start=START).one()
        employee = db.session.get(Employee, staff_id)
        assert record.loan == expected
        assert employee.loan_balance == 2000 - expected
        assert automatic_loan_deduction(employee, None, START + timedelta(days=7)) == 500


def test_bulk_finalize_and_reopen_do_not_double_deduct(people, monkeypatch):
    admin_id, staff_id = people
    monkeypatch.setattr(hris, 'completed_cutoff', lambda: (START, START + timedelta(days=7)))
    with app.app_context():
        db.session.get(Employee, staff_id).loan_balance = 1000
        db.session.commit()
    admin = client_for(admin_id)
    data = {'cutoff_start': str(START), 'selected_employee_ids': [str(staff_id)]}
    for _ in range(2):
        assert admin.post('/payroll/bulk-finalize', data=data).status_code == 302
    with app.app_context():
        assert db.session.get(Employee, staff_id).loan_balance == 500
    for _ in range(2):
        assert admin.post(f'/payroll/{staff_id}/reopen').status_code == 302
    with app.app_context():
        assert db.session.get(Employee, staff_id).loan_balance == 1000
    admin.post('/payroll/bulk-finalize', data=data)
    with app.app_context():
        assert db.session.get(Employee, staff_id).loan_balance == 500


@pytest.mark.parametrize('balance,expected', [(0, 0), (300, 300), (4000, 500)])
def test_payroll_dashboard_review_and_summary_share_automatic_amount(people, balance, expected):
    admin_id, staff_id = people
    with app.app_context():
        db.session.get(Employee, staff_id).loan_balance = balance
        db.session.commit()
        rows = hris.build_company_payroll_summary('Trece-Uno', START, START + timedelta(days=7))
        assert next(row for row in rows if row['employee'].id == staff_id)['loan'] == expected
    captured = []

    def capture(sender, template, context, **extra):
        if template.name == 'payroll_dashboard.html':
            captured.append(next(row['loan_deduction'] for row in context['payroll_data'] if row['emp'].id == staff_id))
        elif template.name == 'payroll_loan_review.html':
            captured.append(next(row['deduction'] for row in context['loan_rows'] if row['employee'].id == staff_id))

    template_rendered.connect(capture, app)
    try:
        admin = client_for(admin_id)
        for path in ('/payroll_dashboard', '/payroll-loan-review'):
            assert admin.get(path, query_string={'cutoff_start': str(START)}).status_code == 200
        assert captured == [expected, expected]
        with app.app_context():
            assert db.session.get(Employee, staff_id).loan_balance == balance
            assert Payroll.query.filter_by(employee_id=staff_id).count() == 0
    finally:
        template_rendered.disconnect(capture, app)


def test_schema_upgrade_is_idempotent_and_preserves_saved_amounts():
    path = Path(__file__).resolve().parents[1] / 'migrations' / 'versions' / 'de9f0a1b2c3d_add_automatic_loan_deductions.py'
    spec = importlib.util.spec_from_file_location('automatic_loan_migration', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        for table in ('employees', 'loans', 'payrolls'):
            connection.execute(sa.text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, amount FLOAT)'))
            connection.execute(sa.text(f'INSERT INTO {table} VALUES (1, 1234)'))
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        module.upgrade()
        assert connection.execute(sa.text('SELECT loan_deduction_per_cutoff FROM employees')).scalar_one() == 500
        assert connection.execute(sa.text('SELECT deduction_per_cutoff FROM loans')).scalar_one() == 500
        for table in ('employees', 'loans', 'payrolls'):
            assert connection.execute(sa.text(f'SELECT amount FROM {table}')).scalar_one() == 1234
    engine.dispose()
