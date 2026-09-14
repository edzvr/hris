from hris import weekly_payslip_table_data
from utils.helpers import compute_weekly_deductions


def test_weekly_deductions_use_monthly_equivalent_and_four_week_proration():
    deductions = compute_weekly_deductions(3222 * 4, weeks=4)

    assert deductions == {
        "sss": 135.0,
        "philhealth": 44.3,
        "pagibig": 25.0,
        "total": 204.3,
    }


def test_weekly_payslip_table_is_itemized():
    payslip = {
        key: 0.0 for key in (
            "basic_pay", "allowance", "incentives", "rest_day_pay",
            "special_holiday", "regular_holiday", "regular_overtime",
            "sunday_overtime", "rest_day", "special_holiday_ot",
            "regular_holiday_ot", "night_differential", "adjustment",
            "gross_income", "late_ut", "sss", "philhealth", "pagibig",
            "withholding_tax", "sss_loan", "cash_advance",
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