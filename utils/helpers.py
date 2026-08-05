from datetime import datetime
import calendar
from flask_login import current_user
from models import Attendance, QuizResult, Evaluation, User

# ------------------ MERIT / DEMERIT ------------------
def compute_merit_demerit(employee_id, month=None):
    # Attendance logs (filter by month kung may value)
    query = Attendance.query.filter_by(employee_id=employee_id)
    if month:
        query = query.filter(Attendance.date.strftime("%Y-%m") == month)
    logs = query.all()

    total_days = len(logs)
    total_present = sum(1 for l in logs if l.status == "Present")
    total_late = sum(1 for l in logs if l.status == "Late")
    total_absent = sum(1 for l in logs if l.status == "Absent")
    cash_value = merit_points * 10

    merit_points = 0
    demerit_points = 0

    # Attendance rules
    if total_days > 0 and total_present == total_days and total_late == 0 and total_absent == 0:
        merit_points += 10
    demerit_points += total_late * 2
    demerit_points += total_absent * 5

    # Quiz results
    quiz_results = QuizResult.query.filter_by(employee_id=employee_id).all()
    for r in quiz_results:
        percentage = (r.score / r.total_points * 100) if r.total_points else 0
        if percentage >= 75:
            merit_points += 5
        else:
            demerit_points += 3

    # Peer evaluations (admin weighted double)
    peer_evals = Evaluation.query.filter_by(employee_id=employee_id).all()
    for e in peer_evals:
        evaluator = User.query.get(e.evaluator_id)
        if evaluator and evaluator.role.lower() == "admin":
            merit_points += e.score * 2
        else:
            merit_points += e.score

    total_score = merit_points - demerit_points
    return merit_points, demerit_points, total_score


def ai_suggestion(total_score):
    if total_score >= 20:
        return "🌟 Outstanding! Keep up the discipline and teamwork."
    elif total_score >= 10:
        return "👍 Good job! Aim for full attendance next month."
    elif total_score >= 0:
        return "⚠️ Doing okay, but avoid lates and absences."
    else:
        return "❌ Attendance and performance issues detected. Focus on punctuality and skill improvement."


# ------------------ PAYROLL DEDUCTIONS ------------------
def compute_sss(monthly_salary):
    SSS_TABLE = [
        (0, 3250, 135.00),
        (3251, 3750, 157.50),
        # ... dagdag pa hanggang max bracket
        (19751, 20250, 900.00),  # MSC 20,000 max
    ]
    for low, high, contrib in SSS_TABLE:
        if low <= monthly_salary <= high:
            return contrib
    return SSS_TABLE[-1][2]


def compute_philhealth(monthly_salary):
    return monthly_salary * 0.0275 / 2  # employee share


def compute_pagibig(monthly_salary):
    return 100.0  # fixed employee share


def compute_weekly_deductions(emp, worked_days_in_month, cutoff_date):
    monthly_salary = (emp.daily_rate or 0) * worked_days_in_month
    monthly_sss = compute_sss(monthly_salary)
    monthly_philhealth = compute_philhealth(monthly_salary)
    monthly_pagibig = compute_pagibig(monthly_salary)

    # ilang linggo sa buwan (4 o 5)
    weeks_in_month = len([w for w in calendar.monthcalendar(cutoff_date.year, cutoff_date.month) if any(day != 0 for day in w)])

    return {
        "sss": round(monthly_sss / weeks_in_month, 2),
        "philhealth": round(monthly_philhealth / weeks_in_month, 2),
        "pagibig": round(monthly_pagibig / weeks_in_month, 2)
    }


def compute_net_pay(emp, start_cutoff, end_cutoff, today):
    worked_days_count = Attendance.query.filter(
        Attendance.employee_id == emp.id,
        Attendance.clock_out != None,
        Attendance.clock_in >= start_cutoff,
        Attendance.clock_in <= end_cutoff
    ).count()

    daily_rate = emp.daily_rate or 0
    basic_pay = daily_rate * worked_days_count

    worked_days_in_month = Attendance.query.filter(
        Attendance.employee_id == emp.id,
        Attendance.clock_out != None,
        Attendance.clock_in.month == today.month
    ).count()

    deductions = compute_weekly_deductions(emp, worked_days_in_month, today)

    sss = deductions["sss"]
    philhealth = deductions["philhealth"]
    pagibig = deductions["pagibig"]
    loan = float(emp.loan_balance) if emp.loan_balance < 500 else 500.00

    gross_income = basic_pay + (emp.allowance or 0) + (emp.incentives or 0)
    total_deductions = sss + philhealth + pagibig + loan
    net_pay = gross_income - total_deductions

    return {
        "basic_pay": basic_pay,
        "gross_income": gross_income,
        "sss": sss,
        "philhealth": philhealth,
        "pagibig": pagibig,
        "loan": loan,
        "total_deductions": total_deductions,
        "net_pay": net_pay
    }
