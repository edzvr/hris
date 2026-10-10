from datetime import date, datetime
from math import isclose
from types import SimpleNamespace
from unittest.mock import patch

from hris import (
    app,
    apply_overtime_details,
    build_payslip_breakdown,
    de_minimis_allowance_breakdown,
    holiday_multiplier,
    is_owner_admin_identity,
    manual_owner_contribution_deductions,
    payroll_cutoffs_in_month,
    payroll_cutoff_dates_in_month,
    payroll_attendance_records,
    payroll_statutory_deductions,
    payroll_worked_days_count,
    rice_allowance_breakdown,
    other_de_minimis_monthly_total,
    de_minimis_monthly_ceiling,
    regular_day_pay,
    update_manual_owner_contribution_settings,
)
from utils.helpers import (
    compute_weekly_deductions,
    compute_weekly_employer_deductions,
)


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
    assert attendance.is_restday_ot is False
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


def test_approved_sunday_overtime_is_marked_as_restday_for_any_company():
    attendance = SimpleNamespace(
        employee_id=1,
        employee=SimpleNamespace(company="Auto Expert"),
        date=date(2026, 9, 13),
        clock_in=datetime(2026, 9, 13, 8, 0),
        clock_out=datetime(2026, 9, 13, 19, 0),
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

    assert attendance.overtime_hours == 2
    assert attendance.is_restday_ot is True
    assert attendance.is_weekday_ot is False


def test_overtime_multipliers_match_dole_day_types():
    cases = (
        (date(2026, 9, 14), None, 1.25),
        (date(2026, 9, 13), None, 1.69),
        (date(2026, 9, 14), "Special Non-Working Holiday", 1.69),
        (date(2026, 9, 13), "Special Non-Working Holiday", 1.95),
        (date(2026, 9, 14), "Regular Holiday", 2.60),
        (date(2026, 9, 13), "Regular Holiday", 3.38),
    )

    with app.app_context():
        for target_date, holiday_type, expected in cases:
            attendance = SimpleNamespace(
                date=target_date,
                is_restday_ot=False,
                ot_status="Approved",
                overtime_hours=2,
            )
            holiday = (
                SimpleNamespace(holiday_type=holiday_type)
                if holiday_type
                else None
            )
            with patch("hris.Holiday.query") as holidays:
                holidays.filter_by.return_value.first.return_value = holiday
                assert holiday_multiplier(attendance) == expected


def test_trece_sunday_does_not_add_rest_day_to_basic_pay():
    attendance = SimpleNamespace(
        employee=SimpleNamespace(company="Trece-Uno"),
        date=date(2026, 9, 13),
        hours=9,
    )

    with app.app_context(), patch("hris.Holiday.query") as holidays:
        holidays.filter_by.return_value.first.return_value = None
        pay = regular_day_pay(attendance, 695)

    assert pay == 0


def test_regular_day_pay_deducts_unpaid_lunch_and_prorates_half_day():
    attendance = SimpleNamespace(
        date=date(2026, 9, 14),
        hours=5,
        clock_in=datetime(2026, 9, 14, 8, 0),
        clock_out=datetime(2026, 9, 14, 13, 0),
    )

    with app.app_context(), patch("hris.Holiday.query") as holidays:
        holidays.filter_by.return_value.first.return_value = None
        pay = regular_day_pay(attendance, 600)

    assert pay == 300


def test_regular_day_pay_uses_clock_times_for_undertime():
    attendance = SimpleNamespace(
        date=date(2026, 9, 14),
        hours=8,
        clock_in=datetime(2026, 9, 14, 8, 0),
        clock_out=datetime(2026, 9, 14, 16, 0),
    )

    with app.app_context(), patch("hris.Holiday.query") as holidays:
        holidays.filter_by.return_value.first.return_value = None
        pay = regular_day_pay(attendance, 600)

    assert pay == 525


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


def test_approved_auto_expert_sunday_ot_is_included_for_restday_pay():
    sunday_ot = SimpleNamespace(
        id=2,
        date=date(2026, 9, 13),
        ot_status="Approved",
    )
    employee = SimpleNamespace(id=1, company="Auto Expert")

    with app.app_context(), patch("hris.Attendance.query") as attendance_query:
        attendance_query.filter.return_value.order_by.return_value.all.return_value = [sunday_ot]
        records = payroll_attendance_records(employee, date(2026, 9, 12), date(2026, 9, 19))

    assert records == [sunday_ot]


def test_trece_payroll_counts_six_workdays_when_sunday_rest_day_is_attended():
    employee = SimpleNamespace(id=1, company="Trece-Uno")
    records = [
        SimpleNamespace(date=date(2026, 9, day))
        for day in (12, 13, 14, 15, 16, 17, 18)
    ]

    assert payroll_worked_days_count(employee, records) == 6


def test_trece_six_day_payroll_deductions_use_editable_daily_rate():
    deductions = compute_weekly_deductions(600 * 6 * 4, weeks=4)

    assert deductions == {
        "sss": 181.25,
        "philhealth": 90.0,
        "pagibig": 50.0,
        "total": 321.25,
    }


def test_rice_allowance_always_uses_current_de_minimis_ceiling():
    employee = SimpleNamespace(
        rice_allowance_per_day=200,
        rice_allowance_is_de_minimis=False,
        rice_allowance_ceiling=0,
    )

    assert rice_allowance_breakdown(employee, worked_days=5, cutoff_count=4) == (625, 375)


def test_rice_allowance_defaults_to_95_per_worked_day_without_paying_the_ceiling():
    employee = SimpleNamespace(
        rice_allowance_per_day=0,
        rice_allowance_is_de_minimis=True,
        rice_allowance_ceiling=0,
    )

    assert rice_allowance_breakdown(employee, worked_days=5, cutoff_count=4) == (475, 0)
    assert de_minimis_allowance_breakdown(
        employee, worked_days=5, cutoff_count=4
    ) == (475, 0)


def test_other_de_minimis_is_combined_for_display_but_keeps_category_limits():
    employee = SimpleNamespace(
        rice_allowance_per_day=0,
        laundry_allowance=400,
        medical_allowance=1000,
        uniform_allowance=800,
        christmas_gift_allowance=500,
        meal_allowance=300,
    )

    assert other_de_minimis_monthly_total(employee) == 3000
    exempt, taxable = de_minimis_allowance_breakdown(employee, worked_days=0)
    assert isclose(exempt, 100 + 250 + (8000 / 12 / 4) + 125 + 75)
    assert isclose(taxable, 200 - (8000 / 12 / 4))
    assert isclose(
        de_minimis_monthly_ceiling(),
        2500 + 400 + (12000 / 12) + (8000 / 12) + (6000 / 12) + (0.30 * 610 * 26),
    )
    rice_employee = SimpleNamespace(
        rice_allowance_per_day=200,
        laundry_allowance=0,
        medical_allowance=0,
        uniform_allowance=0,
        christmas_gift_allowance=0,
        meal_allowance=0,
    )
    four_cutoff_split = de_minimis_allowance_breakdown(
        rice_employee, worked_days=5, cutoff_count=4
    )
    five_cutoff_split = de_minimis_allowance_breakdown(
        rice_employee, worked_days=5, cutoff_count=5
    )
    assert four_cutoff_split == (625, 375)
    assert five_cutoff_split == (500, 500)
    assert compute_weekly_employer_deductions(600 * 6 * 4, weeks=4) == {
        "sss": 362.5,
        "sss_ec": 2.5,
        "philhealth": 90.0,
        "pagibig": 50.0,
    }


def test_weekly_contribution_share_adjusts_for_four_or_five_cutoffs():
    assert payroll_cutoffs_in_month(date(2026, 9, 12)) == 4
    assert payroll_cutoffs_in_month(date(2026, 10, 3)) == 5

    five_cutoff_deductions = compute_weekly_deductions(600 * 6 * 5, weeks=5)
    assert five_cutoff_deductions == {
        "sss": 180.0,
        "philhealth": 90.0,
        "pagibig": 40.0,
        "total": 310.0,
    }


def test_owner_admin_is_attendance_exempt_and_uses_full_manual_monthly_contributions_on_selected_cutoff():
    deduction_cutoff = date(2026, 10, 17)
    owner = SimpleNamespace(
        id=88,
        role="admin",
        first_name="Randolf",
        company="Trece-Uno",
        payroll_attendance_exempt=True,
        manual_monthly_sss=1800,
        manual_monthly_philhealth=500,
        manual_monthly_pagibig=400,
        manual_contribution_cutoff_start=deduction_cutoff,
    )

    assert is_owner_admin_identity(owner.role, "randolfronquillo20@gmail.com")
    assert is_owner_admin_identity("ADMIN", "edzvronquillo@gmail.com")
    assert not is_owner_admin_identity("admin", "randolfronquillo@gmail.com")
    assert not is_owner_admin_identity("staff", "edzvroniquillo@gmail.com")
    assert payroll_cutoff_dates_in_month(deduction_cutoff) == [
        date(2026, 10, 3),
        date(2026, 10, 10),
        date(2026, 10, 17),
        date(2026, 10, 24),
        date(2026, 10, 31),
    ]
    assert manual_owner_contribution_deductions(owner, deduction_cutoff) == {
        "sss": 1800.0,
        "philhealth": 500.0,
        "pagibig": 400.0,
    }
    assert manual_owner_contribution_deductions(owner, date(2026, 10, 24)) == {
        "sss": 0.0,
        "philhealth": 0.0,
        "pagibig": 0.0,
    }
    with app.app_context(), patch("hris.Attendance.query") as attendance_query:
        assert payroll_attendance_records(
            owner, date(2026, 10, 17), date(2026, 10, 24)
        ) == []
        attendance_query.filter.assert_not_called()


def test_admin_can_save_monthly_manual_owner_contributions_and_selected_cutoff():
    owner = SimpleNamespace(
        id=88,
        payroll_attendance_exempt=True,
        manual_monthly_sss=0,
        manual_monthly_philhealth=0,
        manual_monthly_pagibig=0,
        manual_contribution_cutoff_start=None,
    )
    selected_cutoff = date(2026, 10, 24)
    form_data = {
        "manual_monthly_sss_88": "1800",
        "manual_monthly_philhealth_88": "500",
        "manual_monthly_pagibig_88": "400",
        "manual_contribution_cutoff_start_88": selected_cutoff.isoformat(),
    }

    update_manual_owner_contribution_settings(owner, form_data, date(2026, 10, 3))

    assert owner.manual_monthly_sss == 1800
    assert owner.manual_monthly_philhealth == 500
    assert owner.manual_monthly_pagibig == 400
    assert owner.manual_contribution_cutoff_start == selected_cutoff

    try:
        update_manual_owner_contribution_settings(
            owner,
            {"manual_contribution_cutoff_start_88": "2026-11-07"},
            date(2026, 10, 3),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Cutoff selections outside the month must be rejected.")


def test_statutory_contributions_recalculate_instead_of_using_stale_overrides():
    calculated = {"sss": 181.25, "philhealth": 90.0, "pagibig": 50.0}
    payroll_record = SimpleNamespace(
        sss_override=120,
        philhealth_override=20,
        pagibig_override=25,
    )

    assert payroll_statutory_deductions(payroll_record, calculated) == calculated


def test_approved_trece_sunday_overtime_uses_rest_day_ot_even_if_flag_is_stale():
    attendance = SimpleNamespace(
        id=1,
        employee_id=1,
        employee=SimpleNamespace(company="Trece-Uno"),
        date=date(2026, 9, 13),
        hours=8,
        overtime_hours=2,
        ot_status="Approved",
        is_restday_ot=False,
        is_holiday_ot=False,
        is_weekday_ot=False,
    )
    employee = SimpleNamespace(
        id=1,
        company="Trece-Uno",
        daily_rate=600,
        allowance=0,
        incentives=0,
    )
    weekday_attendances = [
        SimpleNamespace(
            employee=employee,
            date=date(2026, 9, day),
            hours=8,
            overtime_hours=0,
            ot_status=None,
            is_restday_ot=False,
            is_holiday_ot=False,
            is_weekday_ot=False,
        )
        for day in (12, 14, 15, 16, 17, 18)
    ]
    payroll_record = SimpleNamespace(
        cutoff_start=date(2026, 9, 12),
        cutoff_end=date(2026, 9, 18),
        gross_income=3853.5,
        sss=0,
        philhealth=0,
        pagibig=0,
        withholding_tax=0,
        loan=0,
        liability_deduction=0,
        cash_advance=0,
        total_deductions=0,
        net_pay=3853.5,
    )

    with (
        app.app_context(),
        patch("hris.Attendance.query") as attendance_query,
        patch("hris.Holiday.query") as holidays,
        patch("hris.de_minimis_allowance_breakdown", return_value=(0, 0)),
    ):
        attendance_query.filter.return_value.all.return_value = [attendance, *weekday_attendances]
        holidays.filter_by.return_value.first.return_value = None
        payslip = build_payslip_breakdown(
            employee,
            payroll_record,
            worked_days_count=payroll_worked_days_count(
                employee, [attendance, *weekday_attendances]
            ),
        )

    assert payslip["rest_day"] == 253.5
    assert payslip["sunday_overtime"] == 0
    assert payslip["basic_pay"] == 3600
    assert payslip["actual_worked_days"] == 6