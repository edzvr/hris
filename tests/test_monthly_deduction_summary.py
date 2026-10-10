from datetime import date

from hris import app
from models import Employee, Payroll, db


def log_in(client, employee_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(employee_id)
        session["_fresh"] = True


def test_monthly_deductions_show_per_employee_attendance_adjusted_bases():
    with app.app_context():
        admin = Employee(first_name="Monthly", last_name="Admin", role="admin")
        employee = Employee(
            first_name="Attendance",
            last_name="Varies",
            role="staff",
            company="Auto Expert",
        )
        db.session.add_all([admin, employee])
        db.session.flush()
        first_cutoff = Payroll(
            employee_id=employee.id,
            cutoff_start=date(2026, 10, 3),
            cutoff_end=date(2026, 10, 9),
            gross_income=8500,
            contribution_salary_base=8000,
            sss=100,
            philhealth=50,
            pagibig=40,
            employer_sss=200,
            employer_sss_ec=10,
            employer_philhealth=50,
            employer_pagibig=40,
            is_paid=True,
        )
        second_cutoff = Payroll(
            employee_id=employee.id,
            cutoff_start=date(2026, 10, 10),
            cutoff_end=date(2026, 10, 16),
            gross_income=12750,
            contribution_salary_base=12000,
            sss=200,
            philhealth=75,
            pagibig=50,
            employer_sss=400,
            employer_sss_ec=30,
            employer_philhealth=75,
            employer_pagibig=60,
            is_paid=True,
        )
        db.session.add_all([first_cutoff, second_cutoff])
        db.session.commit()
        admin_id, employee_id = admin.id, employee.id
        payroll_ids = (first_cutoff.id, second_cutoff.id)

    client = app.test_client()
    try:
        log_in(client, admin_id)
        response = client.get("/admin/monthly-deductions?month=2026-10")
        assert response.status_code == 200
        assert b"Monthly Employee Contribution Summary" in response.data
        assert b"Attendance Varies" in response.data
        assert b"PHP 21250.00" in response.data
        assert b"PHP 20000.00" in response.data
        assert b"Cutoffs</th>" in response.data
        assert b"PHP 300.00" in response.data
        assert b"PHP 125.00" in response.data

        pdf_response = client.get(
            "/admin/monthly-deductions?month=2026-10&download=true"
        )
        assert pdf_response.status_code == 200
        assert pdf_response.mimetype == "application/pdf"
    finally:
        with app.app_context():
            db.session.rollback()
            for payroll_id in payroll_ids:
                record = db.session.get(Payroll, payroll_id)
                if record is not None:
                    db.session.delete(record)
            for employee_id_to_delete in (admin_id, employee_id):
                employee_record = db.session.get(Employee, employee_id_to_delete)
                if employee_record is not None:
                    db.session.delete(employee_record)
            db.session.commit()
