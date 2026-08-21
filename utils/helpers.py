from datetime import datetime
import calendar
from sqlalchemy import extract
from flask_login import current_user
from models import Attendance, QuizResult, Evaluation, Employee
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors

# ------------------ MERIT / DEMERIT ------------------
def compute_merit_demerit(employee_id, month=None):
    query = Attendance.query.filter_by(employee_id=employee_id)
    if month:
        # filter by month (YYYY-MM format)
        year, mon = month.split("-")
        query = query.filter(extract('year', Attendance.date) == int(year),
                             extract('month', Attendance.date) == int(mon))
    logs = query.all()

    total_days = len(logs)
    total_present = sum(1 for l in logs if l.status == "Present")
    total_late = sum(1 for l in logs if l.status == "Late")
    total_absent = sum(1 for l in logs if l.status == "Absent")

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

    # Peer evaluations
    peer_evals = Evaluation.query.filter_by(employee_id=employee_id).all()
    for e in peer_evals:
        evaluator = Employee.query.get(e.evaluator_id)
        if evaluator and evaluator.role.lower() == "admin":
            merit_points += e.score * 2
        else:
            merit_points += e.score

    total_score = merit_points - demerit_points
    cash_value = merit_points * 10   # moved here after computation

    return merit_points, demerit_points, total_score, cash_value


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
SSS_TABLE = [
    (1750, 80), (2250, 100), (2750, 120), (3250, 140),
    (3750, 160), (4250, 180), (4750, 200), (5250, 220),
    (5750, 240), (6250, 260), (6750, 280), (7250, 300),
    (7750, 320), (8250, 340), (8750, 360), (9250, 380),
    (9750, 400), (10250, 420), (10750, 440), (11250, 460),
    (11750, 480), (12250, 500), (12750, 520), (13250, 540),
    (13750, 560), (14250, 580), (14750, 600), (15250, 620),
    (15750, 640), (16250, 660), (16750, 680), (17250, 700),
    (17750, 720), (18250, 740), (18750, 760), (19250, 780),
    (19750, 800), (20250, 820), (20750, 840), (21250, 860),
    (21750, 880), (22250, 900)
]


def compute_sss(monthly_salary: float) -> float:
    """Return the employee SSS share for the salary bracket."""
    salary = max(float(monthly_salary or 0), 0)
    for bracket, contribution in SSS_TABLE:
        if salary <= bracket:
            return float(contribution)
    return float(SSS_TABLE[-1][1])


def compute_philhealth(monthly_salary: float) -> float:
    """Return the employee PhilHealth share."""
    salary = max(float(monthly_salary or 0), 0)
    return round((salary * 0.0275) / 2, 2)


def compute_pagibig(monthly_salary: float) -> float:
    """Return the fixed employee Pag-IBIG share."""
    return 100.0


def compute_deductions(monthly_salary: float) -> dict:
    """Return statutory deductions based on attendance-adjusted salary."""
    sss = compute_sss(monthly_salary)
    philhealth = compute_philhealth(monthly_salary)
    pagibig = compute_pagibig(monthly_salary)
    return {
        "sss": sss,
        "philhealth": philhealth,
        "pagibig": pagibig,
        "total": round(sss + philhealth + pagibig, 2)
    }


def compute_weekly_deductions(monthly_salary: float, weeks: int = 4) -> dict:
    """Divide monthly statutory deductions into weekly shares."""
    if weeks <= 0:
        raise ValueError("weeks must be greater than zero")
    deductions = compute_deductions(monthly_salary)
    return {
        "sss": round(deductions["sss"] / weeks, 2),
        "philhealth": round(deductions["philhealth"] / weeks, 2),
        "pagibig": round(deductions["pagibig"] / weeks, 2),
        "total": round(deductions["total"] / weeks, 2)
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
        extract('month', Attendance.clock_in) == today.month
    ).count()

    monthly_salary = (emp.daily_rate or 0) * worked_days_in_month
    deductions = compute_weekly_deductions(monthly_salary)

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


# ------------------ PAYSLIP PDF GENERATOR ------------------
def generate_payslip_pdf(payroll_record, emp, file_path):
    c = canvas.Canvas(file_path, pagesize=letter)
    width, height = letter

    c.setFont("Helvetica-Bold", 16)
    c.drawString(220, height - 50, "PAYSLIP")

    c.setFont("Helvetica", 12)
    c.drawString(50, height - 100, f"Employee: {emp.full_name()}")
    c.drawString(50, height - 120, f"Role: {emp.role}")
    c.drawString(50, height - 140, f"Cutoff: {payroll_record.cutoff_start} to {payroll_record.cutoff_end}")

    data = [
        ["Earnings", "Amount (₱)", "Deductions", "Amount (₱)"],
        ["Basic Pay", f"{payroll_record.gross_income:.2f}", "SSS", f"{payroll_record.sss:.2f}"],
        ["Allowance", f"{emp.allowance:.2f}", "PhilHealth", f"{payroll_record.philhealth:.2f}"],
        ["Incentives", f"{emp.incentives:.2f}", "Pag-IBIG", f"{payroll_record.pagibig:.2f}"],
        ["", "", "Loan", f"{payroll_record.loan:.2f}"],
        ["", "", "Cash Advance", f"{payroll_record.cash_advance:.2f}"],
        ["", "", "Total Deductions", f"{payroll_record.total_deductions:.2f}"],
        ["Net Pay", f"{payroll_record.net_pay:.2f}", "", ""],
    ]

    table = Table(data, colWidths=[120, 100, 120, 100])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
    ]))

    table.wrapOn(c, width, height)
    table.drawOn(c, 50, height - 350)

    c.setFont("Helvetica-Oblique", 10)
    c.drawString(50, 50, f"Generated on {payroll_record.created_at.strftime('%Y-%m-%d %H:%M:%S')}")

    c.save()

def send_resume_work_email(user):
    msg = Message("Resume to Work Reminder",
                  recipients=[user.email])
    msg.body = f"Hi {user.first_name},\n\nIt's 1:00 PM — please resume work after lunch break.\n\nHRIS System"
    mail.send(msg)
