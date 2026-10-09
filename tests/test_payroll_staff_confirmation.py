from datetime import date

from hris import app
from models import Employee, Payroll, db


def log_in(client, employee_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(employee_id)
        session["_fresh"] = True


def test_staff_can_confirm_or_report_only_own_finalized_payroll():
    with app.app_context():
        staff = Employee(
            first_name="Review",
            last_name="Staff",
            role="staff",
            company="Trece-Uno",
        )
        other_staff = Employee(
            first_name="Private",
            last_name="Coworker",
            role="staff",
            company="Trece-Uno",
        )
        admin = Employee(first_name="Payroll", last_name="Admin", role="admin")
        db.session.add_all([staff, other_staff, admin])
        db.session.flush()
        first_payroll = Payroll(
            employee_id=staff.id,
            cutoff_start=date(2026, 10, 3),
            cutoff_end=date(2026, 10, 9),
            gross_income=6000,
            total_deductions=900,
            net_pay=5100,
            is_paid=True,
            confirmation_status="Pending",
        )
        other_payroll = Payroll(
            employee_id=other_staff.id,
            cutoff_start=date(2026, 10, 3),
            cutoff_end=date(2026, 10, 9),
            gross_income=8000,
            total_deductions=1000,
            net_pay=7000,
            is_paid=True,
            confirmation_status="Pending",
        )
        db.session.add_all([first_payroll, other_payroll])
        db.session.commit()
        staff_id = staff.id
        other_staff_id = other_staff.id
        other_payroll_id = other_payroll.id
        admin_id = admin.id
        first_payroll_id = first_payroll.id

    client = app.test_client()
    try:
        log_in(client, staff_id)
        own_page = client.get(f"/payroll/{staff_id}")
        assert own_page.status_code == 200
        assert b"5100.00" in own_page.data
        assert b"Private Coworker" not in own_page.data
        assert b"not a receipt for payment" in own_page.data
        assert client.post(
            f"/payroll/confirmation/{other_payroll_id}",
            data={"action": "confirm"},
        ).status_code == 403

        reported = client.post(
            f"/payroll/confirmation/{first_payroll_id}",
            data={
                "action": "report_issue",
                "confirmation_note": "Missing Sunday rest-day OT",
            },
        )
        assert reported.status_code == 302
        with app.app_context():
            record = db.session.get(Payroll, first_payroll_id)
            assert record.confirmation_status == "Needs Correction"
            assert record.confirmation_note == "Missing Sunday rest-day OT"
            assert record.confirmed_at is None

        log_in(client, admin_id)
        summary = client.get(
            "/payroll/summary?company=Trece-Uno&view=true&cutoff_start=2026-10-03"
        )
        assert summary.status_code == 200
        assert b"Needs Correction" in summary.data
        assert b"Missing Sunday rest-day OT" in summary.data
        reset = client.post(f"/payroll/confirmation/{first_payroll_id}/request-again")
        assert reset.status_code == 302
        with app.app_context():
            record = db.session.get(Payroll, first_payroll_id)
            assert record.confirmation_status == "Pending"
            assert record.confirmation_note is None

        log_in(client, staff_id)
        confirmed = client.post(
            f"/payroll/confirmation/{first_payroll_id}",
            data={"action": "confirm"},
        )
        assert confirmed.status_code == 302
        with app.app_context():
            record = db.session.get(Payroll, first_payroll_id)
            assert record.confirmation_status == "Confirmed"
            assert record.confirmed_at is not None
    finally:
        with app.app_context():
            db.session.rollback()
            for payroll_id in (first_payroll_id, other_payroll_id):
                record = db.session.get(Payroll, payroll_id)
                if record is not None:
                    db.session.delete(record)
            for employee_id in (staff_id, other_staff_id, admin_id):
                employee = db.session.get(Employee, employee_id)
                if employee is not None:
                    db.session.delete(employee)
            db.session.commit()


def test_staff_cannot_confirm_unfinalized_payroll_and_must_explain_report():
    with app.app_context():
        staff = Employee(
            first_name="Draft",
            last_name="Payroll",
            role="staff",
            company="Auto Expert",
        )
        db.session.add(staff)
        db.session.flush()
        record = Payroll(
            employee_id=staff.id,
            cutoff_start=date(2026, 10, 3),
            cutoff_end=date(2026, 10, 9),
            is_paid=False,
        )
        db.session.add(record)
        db.session.commit()
        staff_id, payroll_id = staff.id, record.id

    client = app.test_client()
    try:
        log_in(client, staff_id)
        assert client.post(
            f"/payroll/confirmation/{payroll_id}",
            data={"action": "confirm"},
        ).status_code == 400
        with app.app_context():
            record = db.session.get(Payroll, payroll_id)
            record.is_paid = True
            db.session.commit()
        assert client.post(
            f"/payroll/confirmation/{payroll_id}",
            data={"action": "report_issue", "confirmation_note": "  "},
        ).status_code == 302
        with app.app_context():
            record = db.session.get(Payroll, payroll_id)
            assert record.confirmation_status in (None, "Pending")
            assert record.confirmation_note is None
    finally:
        with app.app_context():
            db.session.rollback()
            record = db.session.get(Payroll, payroll_id)
            if record is not None:
                db.session.delete(record)
            employee = db.session.get(Employee, staff_id)
            if employee is not None:
                db.session.delete(employee)
            db.session.commit()
