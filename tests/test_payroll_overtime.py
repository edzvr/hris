from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from hris import app, apply_overtime_details, payroll_attendance_records, regular_day_pay


def test_trece_sunday_does_not_create_automatic_overtime():
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

    assert attendance.overtime_hours == 0
    assert attendance.is_weekday_ot is False
    assert attendance.ot_status is None


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


def test_trece_sunday_regular_shift_pays_half_day_rest_day_premium():
    attendance = SimpleNamespace(
        employee=SimpleNamespace(company="Trece-Uno"),
        date=date(2026, 9, 13),
        hours=9,
    )

    with app.app_context(), patch("hris.Holiday.query") as holidays:
        holidays.filter_by.return_value.first.return_value = None
        pay = regular_day_pay(attendance, 695)

    assert pay == 451.75


def test_payroll_counts_only_one_completed_attendance_per_date():
    earlier_record = SimpleNamespace(id=1, date=date(2026, 9, 12))
    later_record = SimpleNamespace(id=2, date=date(2026, 9, 12))
    employee = SimpleNamespace(id=1, company='Auto Expert')

    with app.app_context(), patch("hris.Attendance.query") as attendance_query:
        attendance_query.filter.return_value.order_by.return_value.all.return_value = [later_record, earlier_record]
        records = payroll_attendance_records(employee, date(2026, 9, 12), date(2026, 9, 19))

    assert records == [later_record]


def test_auto_expert_sunday_attendance_is_not_paid_in_payroll():
    saturday_record = SimpleNamespace(id=1, date=date(2026, 9, 12))
    sunday_record = SimpleNamespace(id=2, date=date(2026, 9, 13))
    employee = SimpleNamespace(id=1, company='Auto Expert')

    with app.app_context(), patch("hris.Attendance.query") as attendance_query:
        attendance_query.filter.return_value.order_by.return_value.all.return_value = [sunday_record, saturday_record]
        records = payroll_attendance_records(employee, date(2026, 9, 12), date(2026, 9, 19))

    assert records == [saturday_record]