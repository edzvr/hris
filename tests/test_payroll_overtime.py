from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from hris import app, apply_overtime_details, regular_day_pay


def test_manual_approval_calculates_trece_sunday_overtime():
    attendance = SimpleNamespace(
        employee_id=1,
        employee=SimpleNamespace(company="Trece-Uno"),
        date=date(2026, 9, 13),
        clock_in=datetime(2026, 9, 13, 8, 0),
        clock_out=datetime(2026, 9, 13, 15, 30),
        overtime_hours=0,
        is_restday_ot=False,
        is_holiday_ot=False,
        is_weekday_ot=False,
        ot_status="Pending",
    )

    with app.app_context(), patch("hris.OTApplication.query") as applications, patch("hris.Holiday.query") as holidays:
        applications.filter_by.return_value.first.return_value = None
        holidays.filter_by.return_value.first.return_value = None
        apply_overtime_details(attendance, force_approved=True)

    assert attendance.overtime_hours == 3.5
    assert attendance.is_weekday_ot is True
    assert attendance.ot_status == "Approved"


def test_weekday_overtime_requires_six_pm_clock_out():
    attendance = SimpleNamespace(
        employee_id=1,
        employee=SimpleNamespace(company="Auto-Expert"),
        date=date(2026, 9, 14),
        clock_in=datetime(2026, 9, 14, 8, 0),
        clock_out=datetime(2026, 9, 14, 17, 30),
        overtime_hours=0,
        is_restday_ot=False,
        is_holiday_ot=False,
        is_weekday_ot=False,
        ot_status="Pending",
    )

    with app.app_context(), patch("hris.OTApplication.query") as applications, patch("hris.Holiday.query") as holidays:
        applications.filter_by.return_value.first.return_value = None
        holidays.filter_by.return_value.first.return_value = None
        apply_overtime_details(attendance, force_approved=True)

    assert attendance.overtime_hours == 0
    assert attendance.is_weekday_ot is False
    assert attendance.ot_status is None


def test_trece_sunday_regular_shift_pays_half_daily_rate():
    attendance = SimpleNamespace(
        employee=SimpleNamespace(company="Trece-Uno"),
        date=date(2026, 9, 13),
        hours=4,
    )

    with app.app_context(), patch("hris.Holiday.query") as holidays:
        holidays.filter_by.return_value.first.return_value = None
        pay = regular_day_pay(attendance, 800)

    assert pay == 400