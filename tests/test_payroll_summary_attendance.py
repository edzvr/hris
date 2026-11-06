from datetime import date, datetime, timedelta
from io import BytesIO

import pytest
from pypdf import PdfReader

from hris import app, attach_payroll_summary_attendance_and_loans, payroll_summary_pdf
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
