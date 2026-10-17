from datetime import date, datetime
from io import BytesIO

import pytest
from pypdf import PdfReader

from hris import app, build_payslip_verification, calculate_payroll_snapshot
from models import Attendance, AuditLog, Employee, Payroll, PayslipVerification, db


@pytest.fixture
def payroll_people():
    with app.app_context():
        admin = Employee(first_name='Finalize', last_name='Manager', role='admin')
        employees = [
            Employee(first_name='Existing', last_name='Staff', role='staff',
                     company='Trece-Uno', daily_rate=600, loan_balance=1000,
                     rice_allowance_per_day=95),
            Employee(first_name='New', last_name='Staff', role='staff',
                     company='Auto Expert', daily_rate=600, loan_balance=1000,
                     rice_allowance_per_day=95),
        ]
        db.session.add_all([admin, *employees])
        db.session.flush()
        for employee in employees:
            db.session.add(Attendance(
                employee_id=employee.id, date=date(2026, 10, 5),
                clock_in=datetime(2026, 10, 5, 8),
                clock_out=datetime(2026, 10, 5, 17),
                overtime_hours=0, status='Present', hours=8, company=employee.company,
            ))
        db.session.add(Payroll(
            employee_id=employees[0].id, cutoff_start=date(2026, 10, 3),
            cutoff_end=date(2026, 10, 9), loan=100,
            cash_advance=20, other_deductions=10, withholding_tax_override=0,
        ))
        db.session.commit()
        ids = admin.id, [employee.id for employee in employees]
    try:
        yield ids
    finally:
        with app.app_context():
            db.session.rollback()
            all_ids = [ids[0], *ids[1]]
            PayslipVerification.query.filter(PayslipVerification.employee_id.in_(all_ids)).delete(synchronize_session=False)
            Payroll.query.filter(Payroll.employee_id.in_(all_ids)).delete(synchronize_session=False)
            Attendance.query.filter(Attendance.employee_id.in_(all_ids)).delete(synchronize_session=False)
            AuditLog.query.filter(AuditLog.employee_id.in_(all_ids)).delete(synchronize_session=False)
            Employee.query.filter(Employee.id.in_(all_ids)).delete(synchronize_session=False)
            db.session.commit()


def client_for(employee_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True
    return client


def test_bulk_finalization_saves_totals_for_existing_and_new_staff(payroll_people):
    admin_id, staff_ids = payroll_people
    client = client_for(admin_id)
    response = client.post('/payroll/bulk-finalize', data={
        'cutoff_start': '2026-10-03', 'selected_employee_ids': list(map(str, staff_ids)),
    })
    assert response.status_code == 302
    with app.app_context():
        for staff_id in staff_ids:
            record = Payroll.query.filter_by(employee_id=staff_id).one()
            assert record.is_paid
            assert record.gross_income == 600
            assert record.total_deductions > 0
            itemized_deductions = sum(
                float(getattr(record, field) or 0)
                for field in (
                    'sss', 'philhealth', 'pagibig', 'withholding_tax',
                    'loan', 'sss_loan', 'pagibig_loan', 'liability_deduction',
                    'cash_advance', 'other_deductions',
                )
            )
            assert record.total_deductions == pytest.approx(itemized_deductions)
            assert record.net_pay == pytest.approx(695 - record.total_deductions)
            assert record.net_pay > 0
            assert record.contribution_salary_base == 600
        first = Payroll.query.filter_by(employee_id=staff_ids[0]).one()
        payroll_id = first.id
        assert db.session.get(Employee, staff_ids[0]).loan_balance == 900
        expected_net = first.net_pay
    staff = client_for(staff_ids[0])
    profile = staff.get(f'/profile/{staff_ids[0]}')
    assert profile.status_code == 200
    assert f'{expected_net:.2f}'.encode() in profile.data
    assert b'Existing Staff - Profile' in profile.data
    payroll = staff.get(f'/payroll/{staff_ids[0]}')
    assert f'{expected_net:.2f}'.encode() in payroll.data
    pdf = staff.get(f'/payslip/{staff_ids[0]}/{payroll_id}/download')
    assert pdf.status_code == 200
    text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf.data)).pages)
    assert f'{expected_net:,.2f}' in text
    client.post('/payroll/bulk-finalize', data={
        'cutoff_start': '2026-10-03', 'selected_employee_ids': list(map(str, staff_ids)),
    })
    with app.app_context():
        assert db.session.get(Employee, staff_ids[0]).loan_balance == 900
        assert Payroll.query.filter_by(employee_id=staff_ids[0]).one().net_pay == expected_net


def test_repair_preview_and_save_leave_balances_and_nonzero_payroll_unchanged(payroll_people):
    admin_id, staff_ids = payroll_people
    with app.app_context():
        record = Payroll.query.filter_by(employee_id=staff_ids[0]).one()
        record.is_paid = True
        record.loan_deduction_applied = True
        record.liability_deduction_applied = True
        record.liability_deduction = 30
        employee = db.session.get(Employee, staff_ids[0])
        employee.loan_balance = 900
        record_id = record.id
        other = Payroll(
            employee_id=staff_ids[1], cutoff_start=date(2026, 10, 3),
            cutoff_end=date(2026, 10, 9), is_paid=True,
            gross_income=999, total_deductions=99, net_pay=900,
        )
        db.session.add(other)
        db.session.commit()
        other_id = other.id
        expected = calculate_payroll_snapshot(employee, record, record.cutoff_start, date(2026, 10, 10))
        old_verification = build_payslip_verification(
            staff_ids[0], record_id, record.cutoff_start, record.cutoff_end, 0,
        )
    admin = client_for(admin_id)
    preview = admin.get('/payroll/zero-review')
    assert preview.status_code == 200
    assert b'Existing Staff' in preview.data
    assert b'New Staff' not in preview.data
    with app.app_context():
        assert db.session.get(Payroll, record_id).gross_income == 0
    admin.post('/payroll/zero-review', data={'payroll_ids': [str(record_id), str(other_id)]})
    with app.app_context():
        repaired = db.session.get(Payroll, record_id)
        assert repaired.gross_income == expected['gross_income']
        assert repaired.net_pay == expected['net_pay']
        assert repaired.loan_deduction_applied
        assert repaired.liability_deduction_applied
        assert repaired.liability_deduction == 30
        assert db.session.get(Employee, staff_ids[0]).loan_balance == 900
        assert db.session.get(Payroll, other_id).net_pay == 900
        new_verification = build_payslip_verification(
            staff_ids[0], record_id, repaired.cutoff_start, repaired.cutoff_end, repaired.net_pay,
        )
        assert new_verification['document_id'] != old_verification['document_id']
        repeated = build_payslip_verification(
            staff_ids[0], record_id, repaired.cutoff_start, repaired.cutoff_end, repaired.net_pay,
        )
        assert repeated['document_id'] == new_verification['document_id']
    assert client_for(staff_ids[0]).get('/payroll/zero-review').status_code == 403
    assert client_for(staff_ids[0]).post('/payroll/zero-review').status_code == 403
    admin.get(f'/payroll/{staff_ids[1]}?cutoff_start=2026-10-03&save=true')
    with app.app_context():
        assert db.session.get(Payroll, other_id).net_pay == 900


@pytest.mark.parametrize('method', ['individual', 'company'])
def test_other_finalization_paths_save_the_same_totals(payroll_people, monkeypatch, method):
    admin_id, staff_ids = payroll_people
    with app.app_context():
        employee = db.session.get(Employee, staff_ids[0])
        record = Payroll.query.filter_by(employee_id=employee.id).one()
        expected = calculate_payroll_snapshot(
            employee, record, date(2026, 10, 3), date(2026, 10, 10),
        )
    client = client_for(admin_id)
    if method == 'individual':
        response = client.post(
            f'/payroll/{staff_ids[0]}/finalize',
            data={'cutoff_start': '2026-10-03'}, follow_redirects=True,
        )
        assert response.status_code == 200
    else:
        monkeypatch.setattr(
            'hris.completed_cutoff', lambda: (date(2026, 10, 3), date(2026, 10, 10)),
        )
        monkeypatch.setattr(
            'hris.build_company_payroll_summary',
            lambda *args, **kwargs: [{
                'employee': db.session.get(Employee, staff_ids[0]),
                'payroll_record': Payroll.query.filter_by(employee_id=staff_ids[0]).one(),
            }],
        )
        response = client.post('/payroll/summary/mark-paid', data={'company': 'Trece-Uno'})
        assert response.status_code == 302
    with app.app_context():
        record = Payroll.query.filter_by(employee_id=staff_ids[0]).one()
        assert record.is_paid
        assert record.gross_income == expected['gross_income']
        assert record.net_pay == expected['net_pay']
        assert record.total_deductions == expected['total_deductions']
        assert db.session.get(Employee, staff_ids[0]).loan_balance == 900
