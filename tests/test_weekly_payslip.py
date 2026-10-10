from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from hris import app, weekly_payslip_table_data, build_payslip_verification
from utils.helpers import compute_weekly_deductions


def test_build_payslip_verification_has_unique_reference_and_url():
    with (
        app.app_context(),
        patch("hris.PayslipVerification.query") as verification_query,
        patch("hris.db.session.add"),
        patch("hris.db.session.commit"),
    ):
        verification_query.filter_by.return_value.first.return_value = None
        data = build_payslip_verification(
            "EMP-101", 101, "2026-09-12", "2026-09-18", 4200.0
        )

    assert data["document_id"].startswith("PAY-")
    assert "/verify-document/" in data["verify_url"]
    assert data["employee_id"] == "EMP-101"
    assert data["document_type"] == "payslip"
    assert data["reference_id"] == 101
    assert data["cutoff_start"] == date(2026, 9, 12)
    assert data["cutoff_end"] == date(2026, 9, 18)
    assert data["net_pay"] == 4200.0


def test_same_payslip_record_keeps_same_verification_id():
    with (
        app.app_context(),
        patch("hris.PayslipVerification.query") as verification_query,
        patch("hris.db.session.add"),
        patch("hris.db.session.commit"),
    ):
        verification_query.filter_by.return_value.first.side_effect = [
            None,
            SimpleNamespace(
                document_id="PAY-EXISTING",
                verification_hash="existing-hash",
            ),
        ]
        first = build_payslip_verification(
            "EMP-101", 101, "2026-09-12", "2026-09-18", 4200.0
        )
        second = build_payslip_verification(
            "EMP-101", 101, "2026-09-12", "2026-09-18", 4200.0
        )

    assert first["document_id"].startswith("PAY-")
    assert second["document_id"] == "PAY-EXISTING"
    assert second["verification_hash"] == "existing-hash"


def test_weekly_deductions_use_monthly_equivalent_and_four_week_proration():
    deductions = compute_weekly_deductions(3222 * 4, weeks=4)

    assert deductions == {
        "sss": 162.5,
        "philhealth": 80.55,
        "pagibig": 50.0,
        "total": 293.05,
    }


def test_weekly_contributions_use_current_attendance_adjusted_monthly_basis():
    deductions = compute_weekly_deductions(600 * 6 * 4, weeks=4)

    assert deductions == {
        "sss": 181.25,
        "philhealth": 90.0,
        "pagibig": 50.0,
        "total": 321.25,
    }


def test_weekly_payslip_table_is_itemized():
    payslip = {
        key: 0.0 for key in (
            "basic_pay", "allowance", "incentives", "rest_day_pay",
            "special_holiday", "regular_holiday", "regular_overtime",
            "sunday_overtime", "rest_day", "special_holiday_ot",
            "regular_holiday_ot", "night_differential", "adjustment",
            "gross_income", "gross_pay", "late_ut", "late_ut_hours", "sss", "philhealth", "pagibig",
            "withholding_tax", "company_loan", "sss_loan", "pagibig_loan",
            "liability_deduction",
            "cash_advance", "rice_allowance", "rice_allowance_per_day",
            "rice_allowance_exempt", "rice_allowance_taxable",
            "other_deductions", "total_deductions", "net_pay", "payslip_adjustment",
            "basic_hours", "regular_overtime_hours", "sunday_overtime_hours",
            "rest_day_overtime_hours", "special_holiday_overtime_hours",
            "regular_holiday_overtime_hours",
        )
    }
    payslip.update({
        "actual_worked_days": 6,
        "rest_day": 316.88,
        "rest_day_overtime_hours": 2.5,
    })

    labels = {cell for row in weekly_payslip_table_data(payslip) for cell in row}

    assert {
        "Basic Pay", "Other Earnings / Adjustment", "Rest Day Pay",
        "Special Holiday Pay", "Regular Holiday Pay", "Regular OT",
        "De Minimis", "Rest Day OT",
        "Late/Undertime/Half-day", "SSS", "PhilHealth", "Pag-IBIG",
        "SSS Loan", "Pag-IBIG Loan",
        "Withholding Tax", "Company Loan", "GROSS PAY", "NET PAY",
    } <= labels
    assert not {"Weekly Allowance", "Incentives", "De Minimis (Taxable)", "Sunday OT"} & labels
    assert ["Rest Day OT", "2.50", "316.88", "Company Loan", "", "0.00"] in weekly_payslip_table_data(payslip)


def test_weekly_payslip_explains_attendance_hours_overtime_and_rice_split():
    payslip = {
        key: 0.0 for key in (
            "basic_pay", "allowance", "incentives", "rest_day_pay",
            "special_holiday", "regular_holiday", "regular_overtime",
            "sunday_overtime", "rest_day", "special_holiday_ot",
            "regular_holiday_ot", "night_differential", "adjustment",
            "gross_income", "gross_pay", "late_ut", "late_ut_hours", "sss", "philhealth",
            "pagibig", "withholding_tax", "company_loan", "sss_loan", "pagibig_loan",
            "liability_deduction",
            "cash_advance", "rice_allowance", "rice_allowance_per_day",
            "rice_allowance_exempt", "rice_allowance_taxable",
            "other_deductions", "total_deductions", "net_pay", "payslip_adjustment",
            "basic_hours", "regular_overtime_hours", "sunday_overtime_hours",
            "rest_day_overtime_hours", "special_holiday_overtime_hours",
            "regular_holiday_overtime_hours",
        )
    }
    payslip.update({
        "actual_worked_days": 6,
        "basic_hours": 44.43,
        "basic_pay": 3332.25,
        "regular_overtime_hours": 9.83,
        "regular_overtime": 921.56,
        "company_loan": 500,
        "rice_allowance_exempt": 500,
        "rice_allowance_taxable": 70,
        "rice_allowance": 570,
        "rice_allowance_per_day": 95,
        "late_ut": 0,
        "late_ut_hours": 0,
        "gross_income": 4323.81,
        "gross_pay": 4823.81,
        "adjustment": 0,
    })

    rows = weekly_payslip_table_data(payslip)

    assert any(row[:3] == ["Basic Pay", "6 / 44.43", "3,332.25"] for row in rows)
    assert any(row[:3] == ["Regular OT", "9.83", "921.56"] for row in rows)
    assert ["De Minimis", "", "570.00", "SSS", "", "0.00"] in rows
    assert ["Regular Holiday Pay", "", "0.00", "SSS Loan", "", "0.00"] in rows
    assert ["Rest Day OT", "0.00", "0.00", "Company Loan", "", "500.00"] in rows
    assert ["GROSS PAY", "", "4,823.81", "TOTAL DEDUCTIONS", "", "0.00"] in rows


def test_weekly_payslip_keeps_half_day_deduction_separate_from_basic_pay():
    payslip = {
        key: 0.0 for key in (
            "basic_pay", "allowance", "incentives", "rest_day_pay",
            "special_holiday", "regular_holiday", "regular_overtime",
            "sunday_overtime", "rest_day", "special_holiday_ot",
            "regular_holiday_ot", "night_differential", "adjustment",
            "gross_income", "gross_pay", "late_ut", "late_ut_hours",
            "sss", "philhealth", "pagibig", "withholding_tax",
            "company_loan", "sss_loan", "pagibig_loan",
            "liability_deduction", "cash_advance",
            "rice_allowance", "rice_allowance_per_day",
            "rice_allowance_exempt", "rice_allowance_taxable",
            "other_deductions", "total_deductions", "net_pay", "payslip_adjustment",
            "basic_hours", "regular_overtime_hours", "sunday_overtime_hours",
            "rest_day_overtime_hours", "special_holiday_overtime_hours",
            "regular_holiday_overtime_hours",
        )
    }
    payslip.update({
        "actual_worked_days": 2,
        "basic_hours": 12,
        "basic_pay": 1200,
        "late_ut_hours": 4,
        "late_ut": 300,
        "payslip_adjustment": -300,
        "rice_allowance": 190,
        "rice_allowance_per_day": 95,
        "rice_allowance_exempt": 190,
        "gross_pay": 1090,
        "total_deductions": 0,
        "net_pay": 1090,
    })

    rows = weekly_payslip_table_data(payslip)

    assert [
        "Basic Pay", "2 / 12.00", "1,200.00",
        "Late/Undertime/Half-day", "4.00 hrs", "300.00",
    ] in rows
    assert [
        "Other Earnings / Adjustment", "", "-300.00",
        "Other Deductions", "", "0.00",
    ] in rows
    assert ["De Minimis", "", "190.00", "SSS", "", "0.00"] in rows