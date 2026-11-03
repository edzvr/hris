import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, time
from threading import Barrier
from unittest.mock import patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from hris import app, db, reconciliation_snapshot
from models import (
    Attendance, AttendanceCorrection, AttendanceReconciliation, AuditLog,
    Employee, Holiday, OTApplication, Payroll,
)


DAY = date(2026, 10, 24)


@pytest.fixture
def case():
    with app.app_context():
        admin = Employee(first_name="Reconcile", last_name="Admin", role="admin")
        staff = Employee(
            first_name="Reconcile", last_name="Staff", role="staff",
            company="Trece-Uno", daily_rate=645, loan_balance=1000,
        )
        db.session.add_all([admin, staff])
        db.session.flush()
        records = [
            Attendance(
                employee_id=staff.id, date=DAY,
                clock_in=datetime(2026, 10, 24, 7, 30),
                clock_out=datetime(2026, 10, 24, 5, 20),
                status="Present", hours=0, company="Trece-Uno",
            ),
            Attendance(
                employee_id=staff.id, date=DAY,
                clock_in=datetime(2026, 10, 24, 7, 31),
                clock_out=datetime(2026, 10, 24, 10, 13),
                status="Present", hours=2.22, company="Trece-Uno",
            ),
        ]
        payroll = Payroll(
            employee_id=staff.id, cutoff_start=DAY, cutoff_end=date(2026, 10, 30),
            gross_income=3823.24, total_deductions=795.10, net_pay=3598.14,
            loan=500, is_paid=True, loan_deduction_applied=True,
        )
        backup = AuditLog(
            employee_id=admin.id, employee_name="Reconcile Admin",
            action="Downloaded full database backup",
        )
        db.session.add_all([*records, payroll, backup])
        db.session.commit()
        data = {
            "admin": admin.id, "staff": staff.id, "backup": backup.id,
            "ids": [record.id for record in records],
            "originals": reconciliation_snapshot(records),
            "payroll": payroll.id,
            "payroll_before": {
                column.name: getattr(payroll, column.name)
                for column in Payroll.__table__.columns
            },
        }
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(data["admin"])
        session["_fresh"] = True
    data["client"] = client
    data["url"] = f'/admin/attendance-reconciliation/{data["staff"]}'
    assert client.get(data["url"] + "?attendance_date=2026-10-24").status_code == 200
    yield data
    with app.app_context():
        db.session.rollback()
        for model in (AttendanceCorrection, AttendanceReconciliation, OTApplication, Payroll, Attendance):
            model.query.filter_by(employee_id=data["staff"]).delete()
        Holiday.query.filter_by(date=DAY).delete()
        AuditLog.query.filter(AuditLog.employee_id.in_([data["admin"], data["staff"]])).delete()
        Employee.query.filter(Employee.id.in_([data["admin"], data["staff"]])).delete()
        db.session.commit()


def preview(case, **overrides):
    with case["client"].session_transaction() as session:
        token = session["attendance_reconciliation_csrf"]
    values = {
        "action": "preview", "csrf_token": token,
        "attendance_date": "2026-10-24",
        "clock_in": "2026-10-24T07:40",
        "clock_out": "2026-10-24T17:20",
        "reason": "Verified source DTR; same-day shift, no overnight.",
    }
    values.update(overrides)
    return case["client"].post(case["url"], data=values)


def confirm(case, **overrides):
    with case["client"].session_transaction() as session:
        values = {
            "csrf_token": session["attendance_reconciliation_csrf"],
            "preview_nonce": session.get("attendance_reconciliation_preview", {}).get("nonce", ""),
            "action": "confirm", "attendance_date": "2026-10-24",
            "backup_verified": "yes",
        }
    values.update(overrides)
    return case["client"].post(case["url"], data=values)


def assert_originals_unchanged(case):
    with app.app_context():
        records = Attendance.query.filter_by(employee_id=case["staff"]).order_by(Attendance.id).all()
        assert reconciliation_snapshot(records) == case["originals"]
        assert AttendanceReconciliation.query.filter_by(employee_id=case["staff"]).count() == 0


def test_preview_is_read_only_and_confirm_archives_every_original_field(case):
    response = preview(case)
    assert response.status_code == 200
    assert b"Regular hours: 8.0" in response.data
    assert b"Undertime hours: 0.0" in response.data
    assert_originals_unchanged(case)
    # Changes to posted times on confirm cannot bypass the signed session preview.
    response = confirm(case, clock_in="2026-10-24T09:00", clock_out="2026-10-24T10:00")
    assert response.status_code == 302
    with app.app_context():
        records = Attendance.query.filter_by(employee_id=case["staff"]).all()
        assert len(records) == 1
        retained = records[0]
        assert retained.id == case["ids"][0]
        assert retained.clock_in == datetime(2026, 10, 24, 7, 40)
        assert retained.clock_out == datetime(2026, 10, 24, 17, 20)
        assert retained.hours == 8
        assert retained.status == "Present"
        archive = AttendanceReconciliation.query.filter_by(employee_id=case["staff"]).one()
        assert json.loads(archive.original_rows) == case["originals"]
        assert json.loads(archive.corrected_row) == reconciliation_snapshot(records)[0]
        assert archive.backup_audit_id == case["backup"]
        assert archive.performed_by == case["admin"]
        payroll = db.session.get(Payroll, case["payroll"])
        assert {
            column.name: getattr(payroll, column.name)
            for column in Payroll.__table__.columns
        } == case["payroll_before"]
        assert db.session.get(Employee, case["staff"]).loan_balance == 1000
        assert AuditLog.query.filter(
            AuditLog.action.like("Reconciled attendance employee %")
        ).filter_by(employee_id=case["admin"]).count() == 1
    history = case["client"].get(response.location)
    assert b"Original Records (Preserved)" in history.data
    assert confirm(case).status_code == 400
    with app.app_context():
        assert AttendanceReconciliation.query.filter_by(employee_id=case["staff"]).count() == 1


@pytest.mark.parametrize("role", ["staff", "payroll staff", "employee"])
def test_only_admins_can_read_or_write_reconciliation(case, role):
    with app.app_context():
        staff = db.session.get(Employee, case["staff"])
        staff.role = role
        staff.payroll_preparation_access = True
        db.session.commit()
    with case["client"].session_transaction() as session:
        session["_user_id"] = str(case["staff"])
    assert case["client"].get(case["url"]).status_code == 403
    assert preview(case).status_code == 403
    assert_originals_unchanged(case)


@pytest.mark.parametrize(
    "values",
    [
        {"csrf_token": ""},
        {"csrf_token": "\u00e9"},
        {"clock_in": "2026-10-24T07:40+08:00"},
        {"clock_out": "2026-10-25T05:20"},
        {"clock_out": "2026-10-24T05:20"},
        {"clock_out": "2026-10-24T18:00"},
        {"clock_in": "invalid"},
        {"attendance_date": "invalid"},
        {"reason": ""},
        {"reason": "x" * 501},
        {"action": "delete"},
    ],
)
def test_invalid_input_never_changes_attendance(case, values):
    assert preview(case, **values).status_code == 400
    assert_originals_unchanged(case)


@pytest.mark.parametrize("kind", ["missing", "expired", "other_admin"])
def test_preview_requires_recent_backup_by_current_admin(case, kind):
    with app.app_context():
        backup = db.session.get(AuditLog, case["backup"])
        if kind == "missing":
            db.session.delete(backup)
        elif kind == "expired":
            backup.created_at = datetime.utcnow() - timedelta(minutes=31)
        else:
            backup.employee_id = case["staff"]
        db.session.commit()
    assert preview(case).status_code == 400
    assert_originals_unchanged(case)


@pytest.mark.parametrize("change", ["changed", "added", "deleted"])
def test_stale_preview_cannot_overwrite_changed_records(case, change):
    assert preview(case).status_code == 200
    with app.app_context():
        if change == "changed":
            db.session.get(Attendance, case["ids"][1]).clock_out = datetime(2026, 10, 24, 11)
        elif change == "deleted":
            db.session.delete(db.session.get(Attendance, case["ids"][1]))
        else:
            db.session.add(Attendance(
                employee_id=case["staff"], date=DAY, status="Absent",
            ))
        db.session.commit()
        expected = reconciliation_snapshot(
            Attendance.query.filter_by(employee_id=case["staff"]).order_by(Attendance.id).all()
        )
    assert confirm(case).status_code == 400
    with app.app_context():
        assert reconciliation_snapshot(
            Attendance.query.filter_by(employee_id=case["staff"]).order_by(Attendance.id).all()
        ) == expected
        assert AttendanceReconciliation.query.filter_by(employee_id=case["staff"]).count() == 0


@pytest.mark.parametrize("kind", ["nonce", "expired", "unchecked", "backup_expired"])
def test_confirmation_requires_valid_preview_and_verified_backup(case, kind):
    assert preview(case).status_code == 200
    overrides = {}
    if kind == "nonce":
        overrides["preview_nonce"] = "wrong"
    elif kind == "unchecked":
        overrides["backup_verified"] = ""
    elif kind == "expired":
        with case["client"].session_transaction() as session:
            values = session["attendance_reconciliation_preview"]
            values["expires_at"] = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
            session["attendance_reconciliation_preview"] = values
    else:
        with app.app_context():
            db.session.get(AuditLog, case["backup"]).created_at = datetime.utcnow() - timedelta(minutes=31)
            db.session.commit()
    assert confirm(case, **overrides).status_code == 400
    assert_originals_unchanged(case)


@pytest.mark.parametrize("blocker", ["holiday", "overtime", "application", "correction"])
@pytest.mark.parametrize("after_preview", [False, True])
def test_related_records_block_reconciliation_even_if_added_after_preview(case, blocker, after_preview):
    if after_preview:
        assert preview(case).status_code == 200
    with app.app_context():
        if blocker == "holiday":
            db.session.add(Holiday(date=DAY, description="Test holiday"))
        elif blocker == "overtime":
            db.session.get(Attendance, case["ids"][0]).is_weekday_ot = True
        elif blocker == "application":
            db.session.add(OTApplication(
                employee_id=case["staff"], ot_date=DAY, start_time=time(18),
                end_time=time(19), reason="Test OT",
            ))
        else:
            db.session.add(AttendanceCorrection(
                employee_id=case["staff"], correction_date=DAY,
                attendance_id=case["ids"][1], reason="Test correction",
            ))
        db.session.commit()
    response = confirm(case) if after_preview else preview(case)
    assert response.status_code == 400
    with app.app_context():
        assert Attendance.query.filter_by(employee_id=case["staff"]).count() == 2
        assert AttendanceReconciliation.query.filter_by(employee_id=case["staff"]).count() == 0


def test_commit_failure_rolls_back_originals_and_archive(case):
    assert preview(case).status_code == 200
    real_commit = db.session.commit
    with patch("hris.db.session.commit", side_effect=[SQLAlchemyError("test failure"), None]):
        response = confirm(case)
    assert response.status_code == 503
    assert b"No attendance changes were saved" in response.data
    assert_originals_unchanged(case)
    with app.app_context():
        real_commit()


@pytest.mark.parametrize("employee_key", ["admin", "staff"])
def test_employee_deletion_cannot_erase_reconciliation_history(case, employee_key):
    assert preview(case).status_code == 200
    assert confirm(case).status_code == 302
    if employee_key == "admin":
        # Another admin may not delete the reviewer either.
        with app.app_context():
            staff = db.session.get(Employee, case["staff"])
            staff.role = "admin"
            db.session.commit()
        with case["client"].session_transaction() as session:
            session["_user_id"] = str(case["staff"])
    response = case["client"].post(f'/admin/delete_employee/{case[employee_key]}')
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Employee, case[employee_key]) is not None
        assert AttendanceReconciliation.query.filter_by(employee_id=case["staff"]).count() == 1


@pytest.mark.parametrize("second_writer", ["api", "form", "import"])
def test_concurrent_attendance_writers_do_not_create_duplicates(case, second_writer):
    barrier = Barrier(2)
    target_date = "2026-10-26"

    def write_attendance(kind):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(case["admin"] if kind == "import" else case["staff"])
            session["_fresh"] = True
            if kind == "import":
                session["biometric_import_rows"] = [{
                    "employee_id": case["staff"], "status": "Ready",
                    "date": target_date, "clock_in": target_date + "T07:40:00",
                    "clock_out": target_date + "T17:20:00",
                }]
        barrier.wait(timeout=10)
        if kind == "import":
            return client.post("/admin/biometric-import", data={"action": "import"}).status_code
        endpoint = "attendance_api" if kind == "api" else "attendance_action"
        return client.post(
            f'/{endpoint}/{case["staff"]}',
            data={"clockin": "1", "event_time": target_date + "T07:40:00"},
        ).status_code

    with patch("hris.notify_attendance_event"):
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(write_attendance, ["api", second_writer]))
    assert all(status in (200, 302) for status in responses)
    with app.app_context():
        records = Attendance.query.filter_by(
            employee_id=case["staff"], date=date.fromisoformat(target_date)
        ).all()
        assert len(records) == 1
        assert records[0].clock_in == datetime(2026, 10, 26, 7, 40)


def test_sunday_duplicates_are_not_silently_reconciled(case):
    sunday = date(2026, 10, 25)
    with app.app_context():
        for record in Attendance.query.filter_by(employee_id=case["staff"]).all():
            record.date = sunday
        db.session.commit()
    response = preview(
        case, attendance_date=sunday.isoformat(),
        clock_in="2026-10-25T07:40", clock_out="2026-10-25T12:20",
    )
    assert response.status_code == 400
    assert b"Sunday and holiday" in response.data
    with app.app_context():
        assert Attendance.query.filter_by(employee_id=case["staff"]).count() == 2
        assert AttendanceReconciliation.query.filter_by(employee_id=case["staff"]).count() == 0


def test_reconciliation_migration_preserves_existing_rows_and_is_repeatable():
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    migration = import_module("migrations.versions.ab2c3d4e5f6a_add_attendance_reconciliations")
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE employees (id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE attendances (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO employees VALUES (1)"))
            connection.execute(text("INSERT INTO attendances VALUES (1)"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
                with pytest.raises(RuntimeError, match="history must be preserved"):
                    migration.downgrade()
            assert connection.scalar(text("SELECT COUNT(*) FROM attendances")) == 1
            assert connection.scalar(text("SELECT COUNT(*) FROM employees")) == 1
            columns = {column["name"] for column in inspect(connection).get_columns("attendance_reconciliations")}
            assert columns == {column.name for column in AttendanceReconciliation.__table__.columns}
        assert "CREATE TABLE attendance_reconciliations" in str(
            CreateTable(AttendanceReconciliation.__table__).compile(dialect=postgresql.dialect())
        )
    finally:
        engine.dispose()


def test_archived_originals_are_in_downloaded_backup(case, tmp_path):
    import sqlite3

    assert preview(case).status_code == 200
    assert confirm(case).status_code == 302
    response = case["client"].get("/admin/backup/download")
    assert response.status_code == 200
    backup_path = tmp_path / "reconciled-backup.db"
    backup_path.write_bytes(response.data)
    connection = sqlite3.connect(backup_path)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        originals = connection.execute(
            "SELECT original_rows FROM attendance_reconciliations WHERE employee_id = ?",
            (case["staff"],),
        ).fetchone()[0]
        assert json.loads(originals) == case["originals"]
        assert connection.execute(
            "SELECT COUNT(*) FROM attendances WHERE employee_id = ?",
            (case["staff"],),
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_logged_out_users_cannot_access_reconciliation(case):
    client = app.test_client()
    assert client.get(case["url"]).status_code == 302
    assert client.post(case["url"], data={"action": "confirm"}).status_code == 302
    assert_originals_unchanged(case)
