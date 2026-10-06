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
            "gross_income", "late_ut", "sss", "philhealth", "pagibig",
            "withholding_tax", "sss_loan", "liability_deduction",
            "cash_advance", "rice_allowance_exempt",
            "other_deductions", "total_deductions", "net_pay",
        )
    }
    payslip["actual_worked_days"] = 6

    labels = {cell for row in weekly_payslip_table_data(payslip) for cell in row}

    assert {
        "Basic Pay", "Weekly Allowance", "Rest Day Pay",
        "Special Holiday Pay", "Regular Holiday Pay", "Regular OT",
        "Tardiness/Absence", "SSS", "PhilHealth", "Pag-IBIG",
        "Withholding Tax", "Loan Deduction", "GROSS PAY", "NET PAY",
    } <= labels