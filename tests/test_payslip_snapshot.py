import json
from datetime import date, datetime, timedelta
from io import BytesIO

import pytest
from pypdf import PdfReader

from hris import (
    app, build_payslip_breakdown, calculate_payroll_snapshot, db,
    finalize_payroll_record, save_payroll_snapshot, weekly_payslip_table_data,
)
from models import Attendance, AuditLog, Employee, Payroll, PayslipVerification


START = date(2026, 10, 3)
END = date(2026, 10, 10)


@pytest.fixture
def people():
    with app.app_context():
        admin = Employee(first_name="Snapshot", last_name="Admin", role="admin")
        staff = Employee(
            first_name="Snapshot", last_name="Staff", role="staff",
            daily_rate=600, company="Trece-Uno", loan_balance=1000,
            rice_allowance_per_day=95, allowance=25, incentives=10,
        )
        db.session.add_all([admin, staff])
        db.session.flush()
        db.session.add(Attendance(
            employee_id=staff.id, date=date(2026, 10, 5), status="Present",
            clock_in=datetime(2026, 10, 5, 8), clock_out=datetime(2026, 10, 5, 12),
            hours=4, company=staff.company,
        ))
        record = Payroll(
            employee_id=staff.id, cutoff_start=START, cutoff_end=END - timedelta(days=1),
            gross_income=111, total_deductions=20, net_pay=150,
            loan=15, other_deductions=5,
        )
        db.session.add(record)
        db.session.commit()
        values = admin.id, staff.id, record.id
    yield values
    with app.app_context():
        db.session.rollback()
        for model in (PayslipVerification, Payroll, Attendance):
            model.query.filter_by(employee_id=values[1]).delete()
        AuditLog.query.filter(AuditLog.employee_id.in_(values[:2])).delete()
        Employee.query.filter(Employee.id.in_(values[:2])).delete()
        db.session.commit()


def client_for(employee_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(employee_id)
        session["_fresh"] = True
    return client


def payroll_state(record):
    return {column.name: getattr(record, column.name) for column in Payroll.__table__.columns}


def test_legacy_detail_never_reconstructs_historical_components(people):
    with app.app_context():
        record = db.session.get(Payroll, people[2])
        staff = db.session.get(Employee, people[1])
        original = payroll_state(record)
        detail = build_payslip_breakdown(staff, record)
        assert detail["legacy"]
        assert detail["gross_income"] == 111
        assert detail["net_pay"] == 150
        assert "basic_pay" not in detail
        assert "gross_pay" not in detail
        assert "adjustment" not in detail
        assert "Legacy" in detail["warning"]
        assert payroll_state(record) == original
        assert "No reconstruction" in str(weekly_payslip_table_data(detail))
    client = client_for(people[0])
    for url in [
        f"/payroll/{people[1]}?cutoff_start={START}",
        f"/payslip/{people[1]}/{people[2]}",
        f"/payslip/{people[1]}/{people[2]}/download",
    ]:
        response = client.get(url)
        assert response.status_code == 200
        if response.mimetype == "application/pdf":
            text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(response.data)).pages)
            assert "LEGACY PAYROLL" in text
            assert "150.00" in text
        else:
            assert b"Legacy payroll" in response.data
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original


def test_uncomputed_unpaid_draft_shows_unfinalized_preview_without_saving(people):
    with app.app_context():
        record = db.session.get(Payroll, people[2])
        record.gross_income = 0
        record.total_deductions = 0
        record.net_pay = 0
        record.payslip_snapshot = None
        db.session.commit()
        original = payroll_state(record)

    client = client_for(people[0])
    response = client.get(f"/payroll/{people[1]}?cutoff_start={START}")
    assert response.status_code == 200
    assert b"Draft Payroll Preview" in response.data
    assert b"Unfinalized preview" in response.data
    assert b"Legacy payroll" not in response.data

    download = client.get(
        f"/payroll/{people[1]}?cutoff_start={START}&download=true",
        follow_redirects=False,
    )
    assert download.status_code == 302
    assert "/recalculate?" in download.headers["Location"]

    for path in (
        f"/payslip/{people[1]}/{people[2]}",
        f"/payslip/{people[1]}/{people[2]}/download",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert "/recalculate?" in response.headers["Location"]

    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original


@pytest.mark.parametrize("operation", ["inputs", "finalize", "bulk-finalize"])
def test_duplicate_payroll_blocks_all_new_write_paths(people, operation):
    with app.app_context():
        db.session.add(Payroll(
            employee_id=people[1], cutoff_start=START, cutoff_end=END - timedelta(days=1),
            gross_income=222, total_deductions=0, net_pay=222,
        ))
        db.session.commit()
        original = payroll_state(db.session.get(Payroll, people[2]))
    client = client_for(people[0])
    url = f"/payroll/{people[1]}"
    data = {"cutoff_start": str(START), "loan_deduction": "900"}
    if operation == "finalize":
        url += "/finalize"
        data[f"daily_rate_{people[1]}"] = "900"
    elif operation == "bulk-finalize":
        url = "/payroll/bulk-finalize"
        data["selected_employee_ids"] = [str(people[1])]
    response = client.post(url, data=data)
    assert response.status_code == 302
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original
        assert db.session.get(Employee, people[1]).daily_rate == 600


def test_failed_finalization_rolls_back_edited_inputs_and_totals(people):
    with app.app_context():
        db.session.add(Attendance(
            employee_id=people[1], date=date(2026, 10, 5), status="Present",
            clock_in=datetime(2026, 10, 5, 8), clock_out=datetime(2026, 10, 5, 17),
            hours=8,
        ))
        db.session.commit()
        original = payroll_state(db.session.get(Payroll, people[2]))
    response = client_for(people[0]).post(
        f"/payroll/{people[1]}/finalize",
        data={"cutoff_start": str(START), f"daily_rate_{people[1]}": "900"},
    )
    assert response.status_code == 302
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original
        assert db.session.get(Employee, people[1]).daily_rate == 600


def test_failed_bulk_finalization_rolls_back_earlier_records(people, monkeypatch):
    import hris

    calls = []

    def finalize_then_fail(*args):
        if calls:
            raise ValueError("Invalid next source record")
        calls.append(True)
        return finalize_payroll_record(*args)

    with app.app_context():
        original = payroll_state(db.session.get(Payroll, people[2]))
    monkeypatch.setattr(hris, "finalize_payroll_record", finalize_then_fail)
    response = client_for(people[0]).post(
        "/payroll/bulk-finalize",
        data={"cutoff_start": str(START), "selected_employee_ids": [str(people[1])] * 2},
    )
    assert response.status_code == 302
    assert calls == [True]
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original
        assert db.session.get(Employee, people[1]).loan_balance == 1000


def test_future_snapshot_reconciles_and_survives_attendance_and_rate_edits(people):
    with app.app_context():
        record = db.session.get(Payroll, people[2])
        staff = db.session.get(Employee, people[1])
        save_payroll_snapshot(record, calculate_payroll_snapshot(staff, record, START, END))
        db.session.commit()
        original = record.payslip_snapshot
        detail = build_payslip_breakdown(staff, record)
        assert detail["basic_pay"] == 300
        assert detail["late_ut"] == 300
        assert detail["late_ut_hours"] == 4
        assert detail["payslip_adjustment"] == 35
        assert detail["gross_pay"] == 430
        deduction_sum = sum(detail[key] for key in (
            "sss", "philhealth", "pagibig", "withholding_tax", "company_loan",
            "sss_loan", "pagibig_loan", "liability_deduction", "cash_advance", "other_deductions",
        ))
        assert deduction_sum == pytest.approx(detail["total_deductions"])
        assert detail["gross_pay"] - deduction_sum == pytest.approx(detail["net_pay"])
        staff.daily_rate = 900
        staff.allowance = 999
        staff.first_name = "Changed"
        attendance = Attendance.query.filter_by(employee_id=staff.id).one()
        attendance.clock_out = datetime(2026, 10, 5, 17)
        db.session.commit()
        updated = build_payslip_breakdown(staff, record)
        assert record.payslip_snapshot == original
        assert {k: v for k, v in updated.items() if k != "employee"} == {
            k: v for k, v in detail.items() if k != "employee"
        }
    response = client_for(people[0]).get(f"/payslip/{people[1]}/{people[2]}")
    assert response.status_code == 200
    assert b"Snapshot Staff" in response.data
    assert b"Snapshot Daily Rate" in response.data
    assert b"600.00" in response.data


def test_draft_requires_explicit_approval_and_never_deducts_balances(people):
    client = client_for(people[0])
    url = f"/payroll/{people[1]}/recalculate?cutoff_start={START}"
    with app.app_context():
        original = payroll_state(db.session.get(Payroll, people[2]))
    assert client.get(url).status_code == 200
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original
    assert client.post(url, data={"approved": "yes"}).status_code == 400
    with client.session_transaction() as session:
        token = session["payroll_recalculation_csrf"]
    assert client.post(url, data={"csrf_token": token}).status_code == 302
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original
    client.get(url)
    assert client.post(url, data={"csrf_token": token, "approved": "yes"}).status_code == 302
    with app.app_context():
        record = db.session.get(Payroll, people[2])
        assert record.payslip_snapshot is not None
        assert not record.is_paid
        assert not record.loan_deduction_applied
        assert db.session.get(Employee, people[1]).loan_balance == 1000
        saved = payroll_state(record)
    # A replay has no valid pending preview.
    client.post(url, data={"csrf_token": token, "approved": "yes"})
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == saved


@pytest.mark.parametrize("change", ["attendance", "rate", "payroll", "expired"])
def test_stale_recalculation_preview_cannot_overwrite_saved_totals(people, change):
    client = client_for(people[0])
    url = f"/payroll/{people[1]}/recalculate?cutoff_start={START}"
    client.get(url)
    with client.session_transaction() as session:
        token = session["payroll_recalculation_csrf"]
        if change == "expired":
            values = session["payroll_recalculation_preview"]
            values["expires_at"] = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
            session["payroll_recalculation_preview"] = values
    with app.app_context():
        if change == "attendance":
            Attendance.query.filter_by(employee_id=people[1]).one().clock_out = datetime(2026, 10, 5, 17)
        elif change == "rate":
            db.session.get(Employee, people[1]).daily_rate = 700
        elif change == "payroll":
            db.session.get(Payroll, people[2]).loan = 50
        db.session.commit()
        original = payroll_state(db.session.get(Payroll, people[2]))
    response = client.post(url, data={"csrf_token": token, "approved": "yes"})
    assert response.status_code == 302
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original


def test_paid_records_and_unauthorized_users_cannot_recalculate(people):
    url = f"/payroll/{people[1]}/recalculate?cutoff_start={START}"
    staff = client_for(people[1])
    assert staff.get(url).status_code == 403
    assert staff.post(url).status_code == 403
    with app.app_context():
        record = db.session.get(Payroll, people[2])
        record.is_paid = True
        db.session.commit()
        original = payroll_state(record)
    assert client_for(people[0]).get(url).status_code == 302
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original


def test_legacy_finalize_and_save_get_parameters_never_mutate_payroll(people):
    client = client_for(people[0])
    with app.app_context():
        original = payroll_state(db.session.get(Payroll, people[2]))
    for suffix in ("", "&finalize=true", "&save=true", "&download=true"):
        response = client.get(f"/payroll/{people[1]}?cutoff_start={START}{suffix}")
        assert response.status_code in (200, 302)
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original
        assert db.session.get(Employee, people[1]).loan_balance == 1000


def test_finalization_stores_full_snapshot_only_once(people):
    with app.app_context():
        record = db.session.get(Payroll, people[2])
        staff = db.session.get(Employee, people[1])
        assert finalize_payroll_record(staff, record, START, END)
        db.session.commit()
        original = record.payslip_snapshot
        staff.daily_rate = 900
        assert not finalize_payroll_record(staff, record, START, END)
        assert record.payslip_snapshot == original
        assert staff.loan_balance == 985
        assert json.loads(original)["snapshot_version"] == 1


def test_invalid_snapshot_cannot_partially_replace_saved_totals(people):
    with app.app_context():
        record = db.session.get(Payroll, people[2])
        staff = db.session.get(Employee, people[1])
        before = payroll_state(record)
        snapshot = calculate_payroll_snapshot(staff, record, START, END)
        snapshot["net_pay"] += 100
        with pytest.raises(ValueError, match="do not reconcile"):
            save_payroll_snapshot(record, snapshot)
        assert payroll_state(record) == before


def test_finalize_post_cannot_change_existing_paid_payroll(people):
    with app.app_context():
        record = db.session.get(Payroll, people[2])
        record.is_paid = True
        db.session.commit()
        original = payroll_state(record)
    client = client_for(people[0])
    response = client.post(f"/payroll/{people[1]}/finalize", data={
        "cutoff_start": str(START), f"loan_deduction_{people[1]}": "999",
        f"daily_rate_{people[1]}": "900",
    })
    assert response.status_code == 302
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original
        assert db.session.get(Employee, people[1]).daily_rate == 600


def test_snapshot_migration_is_repeatable_and_does_not_backfill_history():
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    migration = import_module("migrations.versions.ac3d4e5f6a7b_add_payslip_snapshot")
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE payrolls (id INTEGER PRIMARY KEY, net_pay FLOAT)"))
            connection.execute(text("INSERT INTO payrolls VALUES (1, 150)"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
                with pytest.raises(RuntimeError, match="Preserve payslip snapshots"):
                    migration.downgrade()
            assert connection.execute(text(
                "SELECT id, net_pay, payslip_snapshot FROM payrolls"
            )).one() == (1, 150, None)
            assert "payslip_snapshot" in {column["name"] for column in inspect(connection).get_columns("payrolls")}
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["payroll", "attendance"])
def test_ambiguous_source_records_block_draft_recalculation(people, kind):
    with app.app_context():
        if kind == "payroll":
            db.session.add(Payroll(
                employee_id=people[1], cutoff_start=START, cutoff_end=END - timedelta(days=1),
                gross_income=222, total_deductions=0, net_pay=222,
            ))
        else:
            db.session.add(Attendance(
                employee_id=people[1], date=date(2026, 10, 5), status="Present",
                clock_in=datetime(2026, 10, 5, 8), clock_out=datetime(2026, 10, 5, 17),
                hours=8,
            ))
        db.session.commit()
        original = payroll_state(db.session.get(Payroll, people[2]))
    response = client_for(people[0]).get(
        f"/payroll/{people[1]}/recalculate?cutoff_start={START}"
    )
    assert response.status_code == 302
    with app.app_context():
        assert payroll_state(db.session.get(Payroll, people[2])) == original


def test_legacy_payslip_rebuilds_itemized_layout_when_it_matches_saved_totals():
    from models import DailyRateHistory
    with app.app_context():
        staff = Employee(
            first_name="Rebuilt", last_name="Legacy", role="staff", daily_rate=645,
            company="Trece-Uno", loan_balance=0, rice_allowance_per_day=95,
        )
        db.session.add(staff)
        db.session.flush()
        staff_id = staff.id
        db.session.add_all([
            DailyRateHistory(employee_id=staff_id, daily_rate=600, effective_date=date(2031, 1, 1)),
            DailyRateHistory(employee_id=staff_id, daily_rate=645, effective_date=date(2031, 10, 28)),
        ])
        for day in (27, 28):
            db.session.add(Attendance(
                employee_id=staff_id, date=date(2031, 10, day), status="Present",
                clock_in=datetime(2031, 10, day, 8), clock_out=datetime(2031, 10, day, 17),
                hours=8, company=staff.company,
            ))
        record = Payroll(
            employee_id=staff_id, cutoff_start=date(2031, 10, 25), cutoff_end=date(2031, 10, 31),
            gross_income=1245, total_deductions=100, other_deductions=100, net_pay=1335,
        )
        db.session.add(record)
        db.session.commit()
        try:
            detail = build_payslip_breakdown(staff, record)
            assert not detail["legacy"] and detail["reconstructed"]
            assert detail["basic_pay"] == 1245
            assert detail["rice_allowance_days"] == 2
            assert detail["rice_allowance"] == 190
            assert detail["net_pay"] == 1335
            assert "Basic Pay" in str(weekly_payslip_table_data(detail))

            record.net_pay = 1300
            mismatch = build_payslip_breakdown(staff, record)
            assert mismatch["legacy"] and mismatch["net_pay"] == 1300
        finally:
            db.session.rollback()
            for model in (Payroll, Attendance, DailyRateHistory):
                model.query.filter_by(employee_id=staff_id).delete()
            Employee.query.filter_by(id=staff_id).delete()
            db.session.commit()
