from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hris import app, build_cutoff_attendance_rows, db
from models import Attendance, Employee, OTApplication


def test_attendance_cards_keep_each_employees_records_separate():
    from flask import render_template
    from html.parser import HTMLParser

    class CardParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.cards = []
            self.inside_card = False

        def handle_starttag(self, tag, attrs):
            if tag == "article":
                self.inside_card = True
                self.cards.append("")

        def handle_endtag(self, tag):
            if tag == "article":
                self.inside_card = False

        def handle_data(self, data):
            if self.inside_card:
                self.cards[-1] += data

    rows = []
    for employee_id, name, day in ((1, "Alice", 19), (2, "Bob", 20), (1, "Alice", 21)):
        rows.append({
            "attendance": SimpleNamespace(
                employee_id=employee_id,
                employee=SimpleNamespace(first_name=name, last_name="Staff", company="Auto Expert"),
                date=date(2026, 10, day), clock_in=None, clock_out=None, ot_status=None,
            ),
            "regular_hours": 0, "late_minutes": 0, "undertime_hours": 0,
            "half_day": False, "overtime_hours": 0,
            "missing_clock_in": True, "missing_clock_out": True,
        })
    with app.test_request_context():
        html = render_template(
            "holiday_ot_dashboard.html", attendance_report_rows=rows,
            cutoff_start="2026-10-17", filter_status="", employee_search="",
            applications=[], records=[],
        )
        empty = render_template(
            "holiday_ot_dashboard.html", attendance_report_rows=[],
            cutoff_start="2026-10-17", filter_status="", employee_search="",
            applications=[], records=[],
        )
    parser = CardParser()
    parser.feed(html)
    assert len(parser.cards) == 2
    assert "Alice Staff" in parser.cards[0] and "Bob Staff" not in parser.cards[0]
    assert "2026-10-19" in parser.cards[0] and "2026-10-21" in parser.cards[0]
    assert "Bob Staff" in parser.cards[1] and "2026-10-20" in parser.cards[1]
    assert "2026-10-19" not in parser.cards[1]
    assert "No In" in parser.cards[0] and "No Out" in parser.cards[0]
    assert "/attendance/1?cutoff_start=2026-10-17" in html
    assert "No attendance records found for this cutoff and employee search." in empty


@pytest.mark.parametrize(
    "company,day,start,end,hours,undertime,half_day",
    [
        ("Trece-Uno", 18, 8, 12, 4, 0, False),
        ("Trece-Uno", 18, 8, 14, 4, 0, False),
        ("Trece-Uno", 18, 9, 14, 3, 1, False),
        ("Trece-Uno", 18, 8, 10, 2, 2, True),
        ("Auto Expert", 18, 8, 12, 4, 0, False),
        ("Auto Expert", 19, 8, 12, 4, 4, True),
    ],
)
def test_dtr_uses_scheduled_hours(
    company, day, start, end, hours, undertime, half_day
):
    employee = SimpleNamespace(id=1, company=company)
    target_date = date(2026, 10, day)
    attendance = SimpleNamespace(
        employee=employee,
        date=target_date,
        clock_in=datetime(2026, 10, day, start),
        clock_out=datetime(2026, 10, day, end),
        hours=hours,
        status="Present",
        company=company,
    )
    with app.app_context(), patch("hris.Attendance.query") as query, patch(
        "hris.payroll_overtime_hours", return_value=0
    ):
        query.filter.return_value.order_by.return_value.all.return_value = [attendance]
        rows = build_cutoff_attendance_rows(
            employee, target_date, date(2026, 10, day + 1)
        )
    assert rows[0]["hours"] == hours
    assert rows[0]["undertime_hours"] == undertime
    assert ("Half-day" in rows[0]["status"]) is half_day


def test_admin_csv_caps_sunday_shift_and_preserves_late_grace_period():
    import csv
    import io

    with app.app_context():
        admin = Employee(first_name="Report", last_name="Admin", role="admin")
        staff = Employee(
            first_name="Schedule", last_name="Report", role="staff", company="Trece-Uno"
        )
        db.session.add_all([admin, staff])
        db.session.flush()
        admin_id, staff_id = admin.id, staff.id
        sunday = Attendance(
            employee_id=staff_id,
            date=date(2026, 10, 18),
            clock_in=datetime(2026, 10, 18, 8),
            clock_out=datetime(2026, 10, 18, 14),
            status="Present",
            company="Trece-Uno",
        )
        monday = Attendance(
            employee_id=staff_id,
            date=date(2026, 10, 19),
            clock_in=datetime(2026, 10, 19, 8, 11),
            clock_out=datetime(2026, 10, 19, 17),
            status="Late",
            company="Trece-Uno",
        )
        db.session.add_all([sunday, monday])
        db.session.commit()
    try:
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin_id)
            session["_fresh"] = True
        response = client.get(
            "/holiday_overtime?cutoff_start=2026-10-17"
            "&employee_search=Schedule+Report&export=attendance_csv"
        )
        assert response.status_code == 200
        rows = {
            row["Date"]: row
            for row in csv.DictReader(io.StringIO(response.get_data(as_text=True)))
        }
        assert rows["2026-10-18"]["Regular Hours"] == "4.00"
        assert rows["2026-10-18"]["Undertime Hours"] == "0.00"
        assert rows["2026-10-18"]["Half-day"] == "No"
        assert rows["2026-10-18"]["OT Hours"] == "4.00"
        assert rows["2026-10-19"]["Late Minutes"] == "1"
    finally:
        with app.app_context():
            db.session.rollback()
            Attendance.query.filter_by(employee_id=staff_id).delete()
            for employee_id in (staff_id, admin_id):
                db.session.delete(db.session.get(Employee, employee_id))
            db.session.commit()


def test_ot_dashboard_has_company_and_specific_staff_filters():
    with app.app_context():
        admin = Employee(first_name="Filter", last_name="Admin", role="admin")
        trece = Employee(first_name="Trece", last_name="Staff", role="staff", company="Trece-Uno")
        auto = Employee(first_name="Auto", last_name="Staff", role="staff", company="Auto Expert")
        db.session.add_all([admin, trece, auto])
        db.session.commit()
        admin_id, trece_id, auto_id = admin.id, trece.id, auto.id
    try:
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin_id)
            session["_fresh"] = True
        page = client.get(
            "/holiday_overtime?cutoff_start=2026-11-07&company=Auto%20Expert"
        )
        text = page.get_data(as_text=True)
        assert page.status_code == 200
        assert "Trece-Uno" in text
        assert "Auto Expert" in text
        assert "Specific staff" in text
        assert f'value="{auto_id}"' in text
        staff_page = client.get(
            f"/holiday_overtime?cutoff_start=2026-11-07&employee_id={trece_id}"
        )
        assert staff_page.status_code == 200
    finally:
        with app.app_context():
            db.session.rollback()
            Employee.query.filter(Employee.id.in_([trece_id, auto_id, admin_id])).delete(
                synchronize_session=False
            )
            db.session.commit()


def test_admin_can_revert_approved_ot_for_correction():
    with app.app_context():
        admin = Employee(first_name="Revert", last_name="Admin", role="admin")
        staff = Employee(first_name="Revert", last_name="Staff", role="staff", company="Auto Expert")
        db.session.add_all([admin, staff])
        db.session.flush()
        record = Attendance(
            employee_id=staff.id, date=date(2026, 11, 13),
            clock_in=datetime(2026, 11, 13, 8),
            clock_out=datetime(2026, 11, 13, 20),
            status="Present", company="Auto Expert",
            overtime_hours=3.0, ot_status="Approved",
            is_weekday_ot=True,
        )
        application = OTApplication(
            employee_id=staff.id, ot_date=date(2026, 11, 13),
            start_time=time(17, 1), end_time=time(20),
            reason="Approved OT", status="Approved",
        )
        db.session.add_all([record, application])
        db.session.commit()
        admin_id, staff_id, attendance_id = admin.id, staff.id, record.id
    try:
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin_id)
            session["_fresh"] = True
        response = client.post(
            f"/admin/attendance/{attendance_id}/overtime/revert",
            data={"cutoff_start": "2026-11-07", "employee_id": str(staff_id)},
        )
        assert response.status_code == 302
        with app.app_context():
            saved_attendance = db.session.get(Attendance, attendance_id)
            saved_application = OTApplication.query.filter_by(employee_id=staff_id).one()
            assert saved_attendance.ot_status == "Pending"
            assert saved_attendance.is_weekday_ot is False
            assert saved_application.status == "Pending"
    finally:
        with app.app_context():
            db.session.rollback()
            OTApplication.query.filter_by(employee_id=staff_id).delete()
            db.session.delete(db.session.get(Attendance, attendance_id))
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.delete(db.session.get(Employee, admin_id))
            db.session.commit()


def test_admin_can_approve_autoexpert_ot_from_clock_out_without_application():
    with app.app_context():
        admin = Employee(first_name="Delivery", last_name="Admin", role="admin")
        staff = Employee(first_name="Marry Jane", last_name="Mendoza", role="staff", company="Auto Expert")
        db.session.add_all([admin, staff])
        db.session.flush()
        record = Attendance(
            employee_id=staff.id, date=date(2026, 11, 13),
            clock_in=datetime(2026, 11, 13, 8, 0),
            clock_out=datetime(2026, 11, 13, 20, 0),
            status="Present", company="Auto Expert",
        )
        db.session.add(record)
        db.session.commit()
        admin_id, staff_id, attendance_id = admin.id, staff.id, record.id
    try:
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin_id)
            session["_fresh"] = True
        page = client.get(
            "/holiday_overtime?cutoff_start=2026-11-07&employee_search=Marry+Jane+Mendoza"
        )
        assert page.status_code == 200
        assert b"Approve Clock-out OT" in page.data
        result = client.post(
            f"/admin/attendance/{attendance_id}/overtime/approve",
            data={"cutoff_start": "2026-11-07", "employee_search": "Marry Jane Mendoza"},
        )
        assert result.status_code == 302
        with app.app_context():
            saved = db.session.get(Attendance, attendance_id)
            assert saved.ot_status == "Approved"
            assert saved.overtime_hours == 2.98
            assert saved.is_weekday_ot is True
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(Attendance, attendance_id))
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.delete(db.session.get(Employee, admin_id))
            db.session.commit()


def test_admin_can_approve_matching_application_from_attendance_card():
    with app.app_context():
        admin = Employee(first_name="OT", last_name="Admin", role="admin")
        staff = Employee(first_name="OT", last_name="Staff", role="staff", company="Auto Expert")
        other = Employee(first_name="OT", last_name="Other", role="staff", company="Auto Expert")
        db.session.add_all([admin, staff, other])
        db.session.flush()
        admin_id, staff_id, other_id = admin.id, staff.id, other.id
        record = Attendance(
            employee_id=staff_id, date=date(2026, 11, 13),
            clock_in=datetime(2026, 11, 13, 7, 29),
            clock_out=datetime(2026, 11, 13, 19, 3),
            status="Present", company="Auto Expert", overtime_hours=1.05,
        )
        matching = OTApplication(
            employee_id=staff_id, ot_date=date(2026, 11, 13),
            start_time=time(17, 1), end_time=time(19, 3),
            reason="Matching request", status="Pending",
        )
        other_application = OTApplication(
            employee_id=other_id, ot_date=date(2026, 11, 13),
            start_time=time(17, 1), end_time=time(19),
            reason="Other request", status="Pending",
        )
        db.session.add_all([record, matching, other_application])
        db.session.commit()
        application_id = matching.id

    try:
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(admin_id)
            session["_fresh"] = True
        url = "/holiday_overtime?cutoff_start=2026-11-07&employee_search=OT+Staff"
        response = client.get(url)
        assert response.status_code == 200
        card = response.get_data(as_text=True).split(
            f'<article class="attendance-employee" aria-labelledby="attendance-employee-{staff_id}">'
        )[1].split("</article>")[0]
        assert "2.03" in card
        assert "Matching request" in card
        assert "Other request" not in card
        assert f"/admin/ot_applications/{application_id}/approve" in card
        assert f"/admin/ot_applications/{application_id}/reject" in card

        with patch("hris.send_notification_email"):
            result = client.post(
                f"/admin/ot_applications/{application_id}/approve",
                data={"cutoff_start": "2026-11-07", "employee_search": "OT Staff"},
            )
        assert result.status_code == 302
        assert "cutoff_start=2026-11-07" in result.location
        with app.app_context():
            saved = db.session.get(OTApplication, application_id)
            assert saved.status == "Approved"
            saved_record = Attendance.query.filter_by(employee_id=staff_id).one()
            assert saved_record.ot_status == "Approved"
            assert saved_record.overtime_hours == 2.03
        assert f"/admin/ot_applications/{application_id}/approve" not in client.get(url).get_data(as_text=True)
        assert client.post(f"/admin/ot_applications/{application_id}/reject").status_code == 409
        with app.app_context():
            assert db.session.get(OTApplication, application_id).status == "Approved"
    finally:
        with app.app_context():
            db.session.rollback()
            OTApplication.query.filter(
                OTApplication.employee_id.in_([staff_id, other_id])
            ).delete(synchronize_session=False)
            Attendance.query.filter_by(employee_id=staff_id).delete()
            for employee_id in (admin_id, staff_id, other_id):
                db.session.delete(db.session.get(Employee, employee_id))
            db.session.commit()


def test_regular_weekday_application_starts_at_five_oh_one():
    with app.app_context():
        staff = Employee(first_name="OT", last_name="Applicant", role="staff", company="Auto Expert")
        db.session.add(staff)
        db.session.commit()
        staff_id = staff.id
    try:
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(staff_id)
            session["_fresh"] = True
        data = {"ot_date": "2026-11-13", "end_time": "19:00", "reason": "Work"}
        response = client.post("/apply_ot", data={**data, "start_time": "17:00"}, follow_redirects=True)
        assert b"05:01 PM" in response.data
        with app.app_context():
            assert OTApplication.query.filter_by(employee_id=staff_id).count() == 0
        response = client.post("/apply_ot", data={**data, "start_time": "17:01"})
        assert response.status_code == 302
        with app.app_context():
            assert OTApplication.query.filter_by(employee_id=staff_id).one().start_time == time(17, 1)
    finally:
        with app.app_context():
            db.session.rollback()
            OTApplication.query.filter_by(employee_id=staff_id).delete()
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.commit()
