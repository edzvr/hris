from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hris import app, build_cutoff_attendance_rows, db
from models import Attendance, Employee


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
