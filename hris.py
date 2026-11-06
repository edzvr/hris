# ------------------ HRIS MAIN APP ------------------
import os, random, logging, re, hashlib, csv, base64
import secrets
import json
import sqlite3
import tempfile
from urllib.parse import urlsplit
from decimal import Decimal, InvalidOperation
import struct
from math import isfinite
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
from flask import Flask, abort, render_template, render_template_string, request, redirect, url_for, flash, send_file, jsonify, send_from_directory, has_request_context, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import MetaData, Table as SQLAlchemyTable, create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from pypdf import PdfReader, PdfWriter, Transformation
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename 
from werkzeug.http import dump_options_header, parse_options_header
from utils.email_delivery import send_brevo_email
from utils.names import format_person_name, format_suffix_name
from utils.performance import actual_evaluation_points, evaluation_points, merit_report, unique_attendance

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

# ------------------ LOGGING CONFIG ------------------
log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),  # console output
        logging.FileHandler(os.path.join(log_dir, "hris_scheduler.log"))  # file output
    ]
)
logger = logging.getLogger(__name__)


def parse_event_time(value):
    if not value:
        return datetime.now()
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(ZoneInfo('Asia/Manila')).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return datetime.now()


def attendance_status(clock_in):
    return "Late" if clock_in.time() > time(8, 10) else "Present"


def has_strong_password(password):
    return (
        len(password) >= 8
        and re.search(r"[A-Z]", password)
        and re.search(r"[a-z]", password)
        and re.search(r"\d", password)
        and re.search(r"[^A-Za-z0-9]", password)
    )


def manila_datetime(value, format_string="%Y-%m-%d %I:%M %p"):
    if value is None:
        return "N/A"
    return value.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Asia/Manila")).strftime(format_string)


def format_currency(value):
    return f"{float(value or 0):,.2f}"

# ------------------ APP CONFIG ------------------
app = Flask(__name__)
app.add_template_filter(manila_datetime, "manila_datetime")
app.add_template_filter(format_currency, "currency")
basedir = os.path.abspath(os.path.dirname(__file__))


@app.route('/service-worker.js')
def service_worker():
    response = send_from_directory(os.path.join(basedir, 'static'), 'service-worker.js')
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

if load_dotenv:
    load_dotenv(os.path.join(basedir, '.env'))
    load_dotenv(os.path.join(basedir, '.env.local'))
else:
    logger.warning("python-dotenv not installed; .env file values will not be loaded")

WEAK_SECRET_KEYS = {"", "secret", "change-this-secret"}
LEGACY_DOCUMENT_HASH_KEY = "secret"
app.secret_key = os.environ.get("HRIS_SECRET_KEY", "")
database_url = os.environ.get(
    'DATABASE_URL',
    f"sqlite:///{os.path.join(basedir, 'instance', 'hris.db')}"
)
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql+psycopg2://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', os.path.join(basedir, 'static', 'uploads'))
app.config['FILES_FOLDER'] = os.environ.get('FILES_FOLDER', os.path.join(basedir, 'static', 'files'))
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', '')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', '587'))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', app.config['MAIL_USERNAME'])
app.config['MAIL_PROVIDER'] = os.environ.get('MAIL_PROVIDER', 'smtp').strip().lower()
app.config['BREVO_API_KEY'] = os.environ.get('BREVO_API_KEY', '')
instance_dir = os.path.join(basedir, 'instance')
os.makedirs(instance_dir, exist_ok=True)
app.config['HR_DOCUMENTS_FOLDER'] = os.environ.get(
    'HR_DOCUMENTS_FOLDER',
    os.path.join(instance_dir, 'hr_documents'),
)
if not os.path.isabs(app.config['HR_DOCUMENTS_FOLDER']):
    app.config['HR_DOCUMENTS_FOLDER'] = os.path.join(
        basedir, app.config['HR_DOCUMENTS_FOLDER']
    )
os.makedirs(app.config['HR_DOCUMENTS_FOLDER'], exist_ok=True)
app.config['HR_TEMPLATES_FOLDER'] = os.environ.get(
    'HR_TEMPLATES_FOLDER',
    os.path.join(instance_dir, 'hr_templates'),
)
if not os.path.isabs(app.config['HR_TEMPLATES_FOLDER']):
    app.config['HR_TEMPLATES_FOLDER'] = os.path.join(
        basedir, app.config['HR_TEMPLATES_FOLDER']
    )
os.makedirs(app.config['HR_TEMPLATES_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(basedir, app.config['UPLOAD_FOLDER']), exist_ok=True)
os.makedirs(app.config['FILES_FOLDER'], exist_ok=True)

if not os.path.isabs(app.config['UPLOAD_FOLDER']):
    app.config['UPLOAD_FOLDER'] = os.path.join(basedir, app.config['UPLOAD_FOLDER'])

if not os.path.isabs(app.config['FILES_FOLDER']):
    app.config['FILES_FOLDER'] = os.path.join(basedir, app.config['FILES_FOLDER'])

# Optional Flask-Mail support
try:
    from flask_mail import Mail, Message
    mail = Mail(app)
except ImportError:
    mail = None
    Message = None
    logger.warning("Flask-Mail not installed — email features disabled")


def send_notification_email(recipients, subject, body, attachments=None, *, html_body=None):
    """Send an email only when the optional mail service is configured."""
    recipients = [email for email in recipients if email]
    if not recipients:
        logger.warning("Email send skipped: no recipients provided.")
        return False
    provider = app.config.get('MAIL_PROVIDER', 'smtp')
    if provider == 'brevo':
        return send_brevo_email(
            app.config.get('BREVO_API_KEY'),
            app.config.get('MAIL_DEFAULT_SENDER'),
            recipients,
            subject,
            body,
            attachments,
            html_body=html_body,
        )
    if provider != 'smtp':
        logger.error("Email send skipped: unsupported MAIL_PROVIDER.")
        return False
    if not mail:
        logger.warning("Email send skipped: Flask-Mail is not available.")
        return False
    if not app.config.get('MAIL_SERVER'):
        logger.warning("Email send skipped: MAIL_SERVER is not configured.")
        return False
    try:
        message = Message(
            subject=subject,
            sender=app.config.get('MAIL_DEFAULT_SENDER'),
            recipients=recipients,
            body=body
        )
        if html_body:
            message.html = html_body
        for filename, content, mimetype in attachments or []:
            message.attach(filename, mimetype, content)
        mail.send(message)
        return True
    except Exception:
        logger.exception("Notification email could not be sent")
        return False


def notify_admins(subject, body):
    admin_emails = [
        employee.email for employee in Employee.query.filter(Employee.role.ilike('%admin%')).all()
    ]
    return send_notification_email(admin_emails, subject, body)


def notify_attendance_event(employee, event_type, event_time):
    event_label = "Clock in" if event_type == "clockin" else "Clock out"
    timestamp = event_time.strftime("%Y-%m-%d %I:%M %p")
    record_audit_action(event_label)
    notify_admins(
        f"HRIS {event_label}: {employee.full_name()}",
        (
            f"{employee.full_name()} ({employee.company or 'No company'}) {event_label.lower()} "
            f"on {timestamp}."
        )
    )


def send_leave_email(employee, leave, decision):
    send_notification_email(
        [employee.email],
        f"Leave request {decision}",
        (
            f"Your {leave.leave_type} leave request for "
            f"{leave.start_date} to {leave.end_date} was {decision}."
        )
    )


def send_loan_email(employee, loan, decision):
    send_notification_email(
        [employee.email],
        f"Loan request {decision}",
        (
            f"Your loan request for PHP {loan.amount:,.2f}, needed on "
            f"{loan.date_needed}, was {decision}."
        )
    )


def send_resume_work_email(employee):
    if not mail or not employee.email or not app.config.get('MAIL_SERVER'):
        return
    try:
        mail.send(Message(
            subject="Work timer resumed",
            recipients=[employee.email],
            body="Your work timer has resumed after the lunch break."
        ))
    except Exception:
        logger.exception("Resume email could not be sent")

# ------------------ MODELS ------------------
from models import (
    db,
    Employee,
    JobDescriptionOption,
    Attendance,
    AttendanceCorrection,
    AttendanceReconciliation,
    Holiday,
    LeaveRequest,
    LeaveHistory,
    Loan,
    LoanDeductionSkipRequest,
    LoanHistory,
    Evaluation,
    EvaluationQuestion,
    Quiz,
    QuizResult,
    Bulletin,
    Payroll,
    EmployeeLiability,
    EmployerTaxProfile,
    RedemptionHistory,
    IncidentReport,
    MeritDemerit,
    EmployeeDocument,
    HRDocument,
    HRTemplate,
    StaffConcern,
    StaffMessage,
    PasswordResetToken,
    OTApplication,
    AuditLog,
    PayslipVerification,
    CalendarReminder,
    CalendarReminderDelivery,
    GovContributionAudit,
    GovContributionSetting,
    SSSContributionBracket,
)
from utils.calendar_reminders import (
    MANILA, dispatch_reminders, local_time as reminder_local_time,
    reminder_events, visible_reminders, _lock_reminder,
)

from utils.helpers import (
    compute_weekly_deductions,
    compute_weekly_employer_deductions,
    compute_withholding_tax,
    compute_merit_demerit,
    ai_suggestion,
    split_allowance,
    compute_weekly_payroll,
)
from services.government_contributions import (
    AMOUNT_KEYS as GOV_AMOUNT_KEYS,
    RULES_START as GOV_RULES_START,
    AuditTrailService as GovAuditTrailService,
    ContributionSettingsError,
    GovernmentContributionCalculator,
    GovernmentReportsService as GovReportsService,
    LaborCostService as GovLaborCostService,
    PayrollService as GovPayrollService,
    PortalPaymentService as GovPortalPaymentService,
    bracket_values as gov_bracket_values,
    ensure_default_settings as ensure_default_contribution_settings,
    format_sss_table,
    money as gov_money,
    parse_sss_table,
    setting_values as gov_setting_values,
    validate_setting as validate_gov_setting,
)


@app.route('/assessment', endpoint='assessment', methods=['GET', 'POST'])
@login_required
def assessment():
    action = request.args.get("action")
    quiz_id = request.args.get("quiz_id")

    if request.method == 'POST' and action == "upload_quiz":
        file = request.files.get('quiz_file')
        if file and file.filename:
            filename = secure_filename(file.filename)
            upload_folder = app.config.get('UPLOAD_FOLDER', 'static/uploads')
            filepath = os.path.join(upload_folder, filename)
            os.makedirs(upload_folder, exist_ok=True)
            file.save(filepath)

            if filename.lower().endswith('.csv'):
                with open(filepath, newline='', encoding='utf-8') as file_handle:
                    reader = csv.DictReader(file_handle)
                    for row in reader:
                        db.session.add(Quiz(
                            question=row['Question'],
                            choice_a=row['OptionA'],
                            choice_b=row['OptionB'],
                            choice_c=row.get('OptionC'),
                            choice_d=row.get('OptionD'),
                            correct_answer=row['CorrectAnswer'],
                            points=1
                        ))
                db.session.commit()
                flash("✅ CSV Quiz uploaded!", "success")
            elif filename.lower().endswith('.xlsx'):
                import pandas as pd
                data_frame = pd.read_excel(filepath)
                for _, row in data_frame.iterrows():
                    db.session.add(Quiz(
                        question=row['Question'],
                        choice_a=row['OptionA'],
                        choice_b=row['OptionB'],
                        choice_c=row.get('OptionC'),
                        choice_d=row.get('OptionD'),
                        correct_answer=row['CorrectAnswer'],
                        points=1
                    ))
                db.session.commit()
                flash("✅ Excel Quiz uploaded!", "success")
            else:
                flash("❌ Invalid file format.", "danger")
        return redirect(url_for('assessment'))

    if action == "quiz_results":
        results = db.session.query(
            QuizResult, Employee.first_name, Employee.last_name,
            Quiz.question.label("quiz_title")
        ).join(Employee, QuizResult.employee_id == Employee.id) \
         .join(Quiz, QuizResult.quiz_id == Quiz.id).all()
        return render_template("assessment.html", results=results, view="quiz_results")

    if action == "quiz_leaderboard" and quiz_id:
        results = db.session.query(
            QuizResult, Employee.first_name, Employee.last_name
        ).join(Employee, QuizResult.employee_id == Employee.id) \
         .filter(QuizResult.quiz_id == quiz_id) \
         .order_by((QuizResult.score * 1.0 / QuizResult.total_points).desc()).all()
        return render_template("assessment.html", results=results, view="quiz_leaderboard")

    if action == "evaluation":
        evaluations = Evaluation.query.filter_by(
            employee_id=current_user.id,
            approval_status='Approved',
        ).order_by(Evaluation.date.desc()).all()
        month_start = datetime(datetime.today().year, datetime.today().month, 1)
        pending_evals = Employee.query.filter(
            ~Employee.evaluations.any(Evaluation.date >= month_start)
        ).all()
        if pending_evals and current_user.role.lower() == "admin":
            flash(f"⚠️ {len(pending_evals)} employees still need evaluation this month!", "warning")
        average_rating = round(
            sum(e.rating or 0 for e in evaluations) / len(evaluations), 2
        ) if evaluations else 0
        return render_template(
            "assessment.html",
            evaluations=evaluations,
            average_rating=average_rating,
            view="evaluation"
        )

    if request.method == 'POST' and action == "peer_eval":
        score = int(request.form.get('score'))
        remarks = request.form.get('remarks')
        if score >= 9 or score <= 2:
            if not remarks:
                flash("❌ Remarks required for extreme scores.", "danger")
                return redirect(url_for('assessment', action="evaluation"))

        db.session.add(Evaluation(
            employee_id=request.form.get('employee_id'),
            evaluator_id=current_user.id,
            rating=score,
            remarks=remarks,
            category="peer_legacy",
            date=datetime.now()
        ))
        db.session.commit()
        flash("✅ Peer evaluation submitted!", "success")
        return redirect(url_for('assessment', action="evaluation"))

    if action == "history":
        evals = Evaluation.query.filter_by(employee_id=current_user.id).order_by(Evaluation.date.desc()).all()
        quiz_results = QuizResult.query.filter_by(employee_id=current_user.id).order_by(QuizResult.date_taken.desc()).all()
        return render_template("assessment.html", evals=evals, quiz_results=quiz_results, view="history")

    if action in ["pdf", "csv", "excel"]:
        evals = Evaluation.query.filter_by(employee_id=current_user.id).all()
        quiz_results = QuizResult.query.filter_by(employee_id=current_user.id).all()
        return render_template("assessment.html", evals=evals, quiz_results=quiz_results, view=action)

    if action == "ai_insights":
        _, _, total, _ = compute_merit_demerit(current_user.id, datetime.today().strftime("%Y-%m"))
        suggestion = ai_suggestion(total)
        if total < 5:
            return render_template("assessment.html", suggestion=suggestion, view="ai_private")

        db.session.add(Bulletin(
            title="AI Performance Insight",
            content=suggestion,
            author="System"
        ))
        db.session.commit()
        return render_template("assessment.html", suggestion=suggestion, view="ai_public")

    return render_template("assessment.html", view="menu")

DEFAULT_PEER_QUESTIONS = [
    "Nakikipag-usap nang malinaw at may respeto sa mga kasama sa trabaho.",
    "Tinatapos nang tama at nasa oras ang mga nakatalagang gawain.",
    "Nakikipagtulungan at tumutulong sa mga katrabaho.",
    "Nagpapakita ng propesyonalismo at pananagutan sa trabaho.",
    "Tumatanggap at tumutugon nang maayos sa feedback at mga alalahanin sa trabaho."
]

LEGACY_PEER_QUESTION_TRANSLATIONS = {
    "Communicates clearly and respectfully with the team.": DEFAULT_PEER_QUESTIONS[0],
    "Completes assigned work accurately and on time.": DEFAULT_PEER_QUESTIONS[1],
    "Shows teamwork and supports coworkers.": DEFAULT_PEER_QUESTIONS[2],
    "Demonstrates professionalism and accountability.": DEFAULT_PEER_QUESTIONS[3],
    "Responds constructively to feedback and workplace concerns.": DEFAULT_PEER_QUESTIONS[4],
}

HANDBOOK_QUIZ_QUESTIONS = [
    ("Kailan naging epektibo ang Employee Policy Handbook?", "Oktubre 2025", "Enero 2024", "Marso 2026", "Hulyo 2023", "A"),
    ("Ano ang dapat gawin kapag may hindi malinaw sa handbook?", "Magtanong sa kinauukulan", "Huwag pansinin", "I-share sa social media", "Hulaan ang patakaran", "A"),
    ("Ano ang ipinagbabawal sa workplace ayon sa handbook?", "Diskriminasyon at panliligalig", "Maayos na komunikasyon", "Pagtutulungan", "Pag-report ng sira", "A"),
    ("Ano ang ibig sabihin ng Clean As You Go?", "Linisin ang sariling gamit at paligid pagkatapos gamitin", "Iwan ang tools sa sahig", "Hintayin ang ibang staff", "Itago ang kalat", "A"),
    ("Ano ang dapat gawin kung may sira sa ari-arian ng kumpanya?", "I-report agad", "Itago ang sira", "Iwan sa susunod na shift", "Gamitin pa rin kahit delikado", "A"),
    ("Ano ang dapat gawin kung mahuhuli o liliban sa trabaho?", "Ipaalam agad sa kinauukulan", "Huwag magsabi", "Mag-clock in para sa iba", "Maghintay ng memo", "A"),
    ("Ilang araw ang Service Incentive Leave para sa kwalipikadong regular employee?", "Limang araw", "Isang araw", "Pitong araw", "Sampung araw", "A"),
    ("Ano ang dapat isumite bago gumamit ng leave?", "Leave Request Slip", "Quiz result", "Payslip lamang", "Incident report lamang", "A"),
    ("Ano ang batayan ng 13th-month pay ayon sa handbook?", "Mga araw na ipinasok sa loob ng 12 buwan", "Allowances lamang", "Overtime lamang", "Birthday leave lamang", "A"),
    ("Ano ang tamang asal sa paggamit ng company resources?", "Gamitin nang wasto at makatarungan", "Gamitin para sa personal na negosyo", "Iuwi nang walang pahintulot", "Ibigay sa bisita", "A"),
]


def ensure_peer_questions():
    existing_questions = EvaluationQuestion.query.all()
    changed = False
    for question in existing_questions:
        translated = LEGACY_PEER_QUESTION_TRANSLATIONS.get(question.text)
        if translated:
            question.text = translated
            changed = True
    if not existing_questions:
        for text in DEFAULT_PEER_QUESTIONS:
            db.session.add(EvaluationQuestion(text=text, category="peer", is_active=True))
        changed = True
    if changed:
        db.session.commit()

# ------------------ FUNCTION: generate_auto_quiz ------------------
def generate_auto_quiz(category, num_questions=5):
    pool = {
        "Engine": [
            Quiz(category="Engine", question="Ano ang gamit ng spark plug?",
                 choice_a="Nagbibigay ng kuryente sa ilaw", choice_b="Nagpapasimula ng combustion sa engine",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapadulas ng piston",
                 correct_answer="B", points=1),
            Quiz(category="Engine", question="Ano ang gamit ng timing belt?",
                 choice_a="Nagpapakain ng gasolina", choice_b="Nagkokonekta ng crankshaft at camshaft",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagbibigay ng kuryente",
                 correct_answer="B", points=1),
            Quiz(category="Engine", question="Ano ang gamit ng piston rings?",
                 choice_a="Nagpapanatili ng compression", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagpapadulas ng gulong", choice_d="Nagbibigay ng kuryente",
                 correct_answer="A", points=1),
        ],
        "Transmission": [
            Quiz(category="Transmission", question="Ano ang gamit ng clutch?",
                 choice_a="Nagpapalit ng gulong", choice_b="Nagkokonekta ng engine sa transmission",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapalakas ng ilaw",
                 correct_answer="B", points=1),
            Quiz(category="Transmission", question="Anong fluid ang kailangan ng automatic transmission?",
                 choice_a="Brake Fluid", choice_b="Transmission Fluid", choice_c="Coolant", choice_d="Engine Oil",
                 correct_answer="B", points=1),
            Quiz(category="Transmission", question="Ano ang gamit ng gear oil?",
                 choice_a="Nagpapadulas ng gears", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagbibigay ng kuryente", choice_d="Nagpapalakas ng ilaw",
                 correct_answer="A", points=1),
        ],
        "Electrical": [
            Quiz(category="Electrical", question="Ano ang nag-iimbak ng kuryente?",
                 choice_a="Alternator", choice_b="Battery", choice_c="Starter", choice_d="Distributor",
                 correct_answer="B", points=1),
            Quiz(category="Electrical", question="Ano ang gamit ng fuse?",
                 choice_a="Proteksyon laban sa short circuit", choice_b="Nagbibigay ng kuryente",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
            Quiz(category="Electrical", question="Ano ang gamit ng ignition coil?",
                 choice_a="Nagpapalakas ng boltahe para sa spark plug", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagbibigay ng kuryente sa ilaw", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
        ],
        "Suspension": [
            Quiz(category="Suspension", question="Ano ang sumasalo ng lubak?",
                 choice_a="Shock Absorber", choice_b="Spring", choice_c="Strut", choice_d="Control Arm",
                 correct_answer="A", points=1),
            Quiz(category="Suspension", question="Ano ang gamit ng stabilizer bar?",
                 choice_a="Nagbabawas ng body roll", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagbibigay ng kuryente", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
        ],
        "Cooling": [
            Quiz(category="Cooling", question="Ano ang nagpapadaloy ng coolant?",
                 choice_a="Radiator", choice_b="Water Pump", choice_c="Fan Belt", choice_d="Thermostat",
                 correct_answer="B", points=1),
            Quiz(category="Cooling", question="Ano ang gamit ng radiator?",
                 choice_a="Nagpapalamig ng coolant", choice_b="Nagbibigay ng kuryente",
                 choice_c="Nagpapadulas ng piston", choice_d="Nagpapakain ng gasolina",
                 correct_answer="A", points=1),
        ],
        "Car Brands": [
            Quiz(category="Car Brands", question="Sino ang gumagawa ng Civic?",
                 choice_a="Toyota", choice_b="Honda", choice_c="Ford", choice_d="Nissan",
                 correct_answer="B", points=1),
            Quiz(category="Car Brands", question="Sino ang gumagawa ng Hilux?",
                 choice_a="Toyota", choice_b="Honda", choice_c="Ford", choice_d="Isuzu",
                 correct_answer="A", points=1),
        ],
        "Brakes": [
            Quiz(category="Brakes", question="Ano ang gamit ng brake pads?",
                 choice_a="Nagbibigay ng kuryente", choice_b="Nagpapadulas ng piston",
                 choice_c="Nagbibigay ng friction para huminto", choice_d="Nagpapalamig ng makina",
                 correct_answer="C", points=1),
            Quiz(category="Brakes", question="Ano ang gamit ng brake fluid?",
                 choice_a="Nagpapadala ng hydraulic pressure", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagbibigay ng kuryente", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
        ],
        "Steering": [
            Quiz(category="Steering", question="Ano ang gamit ng power steering pump?",
                 choice_a="Nagbibigay ng kuryente", choice_b="Nagpapadulas ng piston",
                 choice_c="Nagbibigay ng hydraulic pressure para sa steering", choice_d="Nagpapalamig ng makina",
                 correct_answer="C", points=1),
            Quiz(category="Steering", question="Ano ang gamit ng tie rod?",
                 choice_a="Nagkokonekta ng steering rack sa gulong", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagbibigay ng kuryente", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
        ],
        "Exhaust": [
            Quiz(category="Exhaust", question="Ano ang gamit ng muffler?",
                 choice_a="Nagpapababa ng ingay ng tambutso", choice_b="Nagbibigay ng kuryente",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
            Quiz(category="Exhaust", question="Ano ang gamit ng catalytic converter?",
                 choice_a="Nagbabawas ng harmful emissions", choice_b="Nagbibigay ng kuryente",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
        ]
    }

    questions_pool = pool.get(category, [])
    if not questions_pool:
        logger.warning(f"Walang questions para sa category: {category}")
        return

    selected = random.sample(questions_pool, min(num_questions, len(questions_pool)))
    for q in selected:
        db.session.add(q)
    db.session.commit()

    logger.info(f"Auto quiz generated for category: {category}")


def generate_monthly_peer_eval_reminder(today=None):
    with app.app_context():
        _generate_monthly_peer_eval_reminder(today)


def _generate_monthly_peer_eval_reminder(today=None):
    """Post a generic reminder and email employees who still owe peer evaluation."""
    try:
        today = today or datetime.now(ZoneInfo('Asia/Manila')).date()
        month_start = today.replace(day=1)
        month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        month_start_dt, next_month_dt = monthly_utc_window(month_start)
        due_staff = [
            employee for employee in Employee.query.filter(Employee.role.ilike('%staff%')).all()
            if peer_evaluation_is_due(employee, month_start)
        ]
        if not due_staff:
            logger.info("No pending peer evaluations this month.")
            return

        title = "Monthly peer evaluation reminder"
        existing_bulletin = Bulletin.query.filter_by(
            title=title, author="System",
        ).filter(
            Bulletin.created_at >= month_start_dt,
            Bulletin.created_at < next_month_dt,
        ).first()
        if existing_bulletin:
            return
        db.session.add(Bulletin(
            title=title,
            content=(
                f"Please complete your monthly peer-to-peer evaluation by {month_end}. "
                "Open Monthly Peer Evaluation from your staff dashboard."
            ),
            author="System",
        ))
        db.session.commit()

        for employee in due_staff:
            if employee.email:
                send_notification_email(
                    [employee.email],
                    title,
                    (
                        f"Hi {employee.first_name}, please complete your monthly peer-to-peer "
                        f"evaluation by {month_end}. Open Monthly Peer Evaluation from your "
                        "staff dashboard."
                    ),
                )
    except Exception:
        db.session.rollback()
        logger.exception("Error running monthly peer-eval reminder job")


def send_month_end_reminders(today=None):
    with app.app_context():
        try:
            _send_month_end_reminders(today)
        except Exception:
            db.session.rollback()
            logger.exception("Error running month-end quiz/evaluation reminder job")


def _send_month_end_reminders(today=None):
    """Email staff who still have a monthly quiz or peer evaluation due soon."""
    today = today or datetime.now(ZoneInfo('Asia/Manila')).date()
    month_start = today.replace(day=1)
    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    days_until_end = (month_end - today).days
    if days_until_end < 0 or days_until_end > 3:
        return

    month_start_dt, next_month_dt = monthly_utc_window(month_start)
    title = f"Monthly reminders due by {month_end}"
    existing_bulletin = Bulletin.query.filter_by(
        title=title, author="System",
    ).filter(
        Bulletin.created_at >= month_start_dt,
        Bulletin.created_at < next_month_dt,
    ).first()
    if existing_bulletin:
        return

    pending_staff = []
    for employee in Employee.query.filter(Employee.role.ilike('%staff%')).all():
        reminder_status = monthly_reminder_status(employee, month_start)
        if reminder_status['quiz_pending'] or reminder_status['peer_evaluation_pending']:
            pending_staff.append((employee, reminder_status))
    if not pending_staff:
        return

    db.session.add(Bulletin(
        title=title,
        content=(
            f"Monthly quiz and peer evaluation are due by {month_end}. "
            "Check the reminder above the Clock In panel on your staff dashboard."
        ),
        author="System",
    ))
    db.session.commit()

    for employee, reminder_status in pending_staff:
        outstanding_items = []
        if reminder_status['quiz_pending']:
            outstanding_items.append("your official monthly quiz")
        if reminder_status['peer_evaluation_pending']:
            outstanding_items.append("your monthly peer evaluation")
        if employee.email:
            send_notification_email(
                [employee.email],
                title,
                (
                    f"Hi {employee.first_name}, please complete "
                    f"{' and '.join(outstanding_items)} by {month_end}. "
                    "Your staff dashboard shows the remaining items above Clock In."
                ),
            )


def peer_evaluation_is_due(employee, month_start):
    month_start_dt, next_month_dt = monthly_utc_window(month_start)
    return not Evaluation.query.filter(
        Evaluation.evaluator_id == employee.id,
        Evaluation.date >= month_start_dt,
        Evaluation.date < next_month_dt,
        Evaluation.category.like('peer_%'),
    ).first()


def monthly_utc_window(month_start):
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    manila_timezone = ZoneInfo('Asia/Manila')
    start_local = datetime.combine(month_start, time.min, tzinfo=manila_timezone)
    end_local = datetime.combine(next_month, time.min, tzinfo=manila_timezone)
    utc_timezone = ZoneInfo('UTC')
    return (
        start_local.astimezone(utc_timezone).replace(tzinfo=None),
        end_local.astimezone(utc_timezone).replace(tzinfo=None),
    )


def monthly_reminder_status(employee, month_start):
    month_start_dt, next_month_dt = monthly_utc_window(month_start)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    quiz_taken = QuizResult.query.filter_by(
        employee_id=employee.id,
        is_official=True,
    ).filter(
        QuizResult.date_taken >= month_start_dt,
        QuizResult.date_taken < next_month_dt,
    ).first() is not None
    return {
        'quiz_taken': quiz_taken,
        'quiz_pending': not quiz_taken,
        'peer_evaluation_pending': peer_evaluation_is_due(employee, month_start),
        'month_start': month_start,
        'month_end': next_month - timedelta(days=1),
    }


def send_lunch_reminder(phase):
    """Email staff when lunch starts or the workday resumes."""
    if not mail or not app.config.get('MAIL_SERVER') or not app.config.get('MAIL_USERNAME'):
        logger.info("Lunch email skipped: Flask-Mail is not configured.")
        return

    subject = "Lunch break reminder" if phase == "start" else "Resume work reminder"
    body = (
        "Lunch break starts at 12:00 PM. Please take your one-hour lunch break."
        if phase == "start" else
        "Lunch break ends at 1:00 PM. Please resume work."
    )
    try:
        with app.app_context():
            staff = Employee.query.filter(
                Employee.role.ilike('%staff%'),
                Employee.email.isnot(None)
            ).all()
            for user in staff:
                if not user.email:
                    continue
                msg = Message(subject=subject,
                              sender=app.config['MAIL_DEFAULT_SENDER'],
                              recipients=[user.email])
                msg.body = f"Hi {user.first_name},\n\n{body}\n\nHRIS System"
                mail.send(msg)
    except Exception:
        logger.exception("Lunch reminder email failed for phase: %s", phase)

# ------------------ EXTENSIONS ------------------
db.init_app(app)


def ensure_employee_resume_columns():
    employee_columns = {column["name"] for column in inspect(db.engine).get_columns("employees")}
    for column_name in (
        "resume_summary",
        "resume_education",
        "resume_experience",
        "resume_skills",
        "resume_references",
    ):
        if column_name not in employee_columns:
            db.session.execute(text(f"ALTER TABLE employees ADD COLUMN {column_name} TEXT"))
    db.session.commit()


def remove_employee_registration_name_key_constraint():
    employee_columns = {column["name"] for column in inspect(db.engine).get_columns("employees")}
    if "registration_name_key" not in employee_columns:
        db.session.execute(text("ALTER TABLE employees ADD COLUMN registration_name_key VARCHAR(240)"))
    if db.engine.dialect.name == "postgresql":
        db.session.execute(text(
            "ALTER TABLE employees DROP CONSTRAINT IF EXISTS employees_registration_name_key_key"
        ))
    db.session.execute(text("DROP INDEX IF EXISTS uq_employees_registration_name_key"))
    db.session.commit()


def ensure_evaluation_tracking_columns():
    inspector = inspect(db.engine)
    evaluation_columns = {column["name"] for column in inspector.get_columns("evaluations")}
    merit_columns = {column["name"] for column in inspector.get_columns("merit_demerit")}
    statements = []
    if "approval_status" not in evaluation_columns:
        statements.append("ALTER TABLE evaluations ADD COLUMN approval_status VARCHAR(20) NOT NULL DEFAULT 'Pending'")
    if "points_applied" not in evaluation_columns:
        statements.append("ALTER TABLE evaluations ADD COLUMN points_applied BOOLEAN NOT NULL DEFAULT FALSE")
    if "source" not in merit_columns:
        statements.append("ALTER TABLE merit_demerit ADD COLUMN source VARCHAR(50)")
    if "reference" not in merit_columns:
        statements.append("ALTER TABLE merit_demerit ADD COLUMN reference VARCHAR(255)")
    for statement in statements:
        db.session.execute(text(statement))
    db.session.commit()


def ensure_payroll_columns():
    payroll_columns = {column["name"] for column in inspect(db.engine).get_columns("payrolls")}
    statements = []
    if "payslip_snapshot" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN payslip_snapshot TEXT")
    if "withholding_tax" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN withholding_tax FLOAT DEFAULT 0")
    if "withholding_tax_override" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN withholding_tax_override FLOAT")
    if "loan_deduction_applied" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN loan_deduction_applied BOOLEAN NOT NULL DEFAULT FALSE")
    if "liability_deduction" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN liability_deduction FLOAT DEFAULT 0")
    if "liability_deduction_override" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN liability_deduction_override FLOAT")
    if "other_deductions" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN other_deductions FLOAT NOT NULL DEFAULT 0")
    if "sss_loan" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN sss_loan FLOAT DEFAULT 0")
    if "pagibig_loan" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN pagibig_loan FLOAT DEFAULT 0")
    if "sss_override" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN sss_override FLOAT")
    if "philhealth_override" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN philhealth_override FLOAT")
    if "pagibig_override" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN pagibig_override FLOAT")
    if "contribution_salary_base" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN contribution_salary_base FLOAT")
    if "employer_sss" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN employer_sss FLOAT")
    if "employer_sss_ec" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN employer_sss_ec FLOAT")
    if "employer_philhealth" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN employer_philhealth FLOAT")
    if "employer_pagibig" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN employer_pagibig FLOAT")
    if "liability_deduction_applied" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN liability_deduction_applied BOOLEAN NOT NULL DEFAULT FALSE")
    if "confirmation_status" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN confirmation_status VARCHAR(30)")
    if "confirmation_note" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN confirmation_note TEXT")
    if "confirmed_at" not in payroll_columns:
        statements.append("ALTER TABLE payrolls ADD COLUMN confirmed_at TIMESTAMP")
    for statement in statements:
        db.session.execute(text(statement))
    db.session.commit()


def ensure_biometric_access_columns():
    employee_columns = {column["name"] for column in inspect(db.engine).get_columns("employees")}
    statements = []
    if "biometric_id" not in employee_columns:
        statements.append("ALTER TABLE employees ADD COLUMN biometric_id VARCHAR(50)")
    if "payroll_preparation_access" not in employee_columns:
        statements.append(
            "ALTER TABLE employees ADD COLUMN payroll_preparation_access BOOLEAN NOT NULL DEFAULT FALSE"
        )
    for statement in statements:
        db.session.execute(text(statement))
    db.session.commit()


def ensure_admin_payroll_access_column():
    employee_columns = {column["name"] for column in inspect(db.engine).get_columns("employees")}
    if "admin_payroll_access" not in employee_columns:
        db.session.execute(text(
            "ALTER TABLE employees ADD COLUMN admin_payroll_access BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        db.session.commit()


def ensure_employee_liability_schema():
    inspector = inspect(db.engine)
    if "employee_liabilities" in inspector.get_table_names():
        return
    db.session.execute(text("""
        CREATE TABLE employee_liabilities (
            id INTEGER PRIMARY KEY,
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            category VARCHAR(80) NOT NULL DEFAULT 'Uncollected Company Receivable',
            description TEXT NOT NULL,
            customer_name VARCHAR(150),
            agreement_date DATE,
            total_amount FLOAT NOT NULL DEFAULT 0,
            deduction_per_cutoff FLOAT NOT NULL DEFAULT 0,
            amount_deducted FLOAT NOT NULL DEFAULT 0,
            amount_recovered FLOAT NOT NULL DEFAULT 0,
            amount_refunded FLOAT NOT NULL DEFAULT 0,
            status VARCHAR(30) NOT NULL DEFAULT 'Active',
            acknowledgment_status VARCHAR(30) NOT NULL DEFAULT 'Acknowledged',
            reference VARCHAR(120),
            remarks TEXT,
            created_by INTEGER REFERENCES employees(id),
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """))
    db.session.commit()


def ensure_hr_document_schema():
    inspector = inspect(db.engine)
    if "hr_documents" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("hr_documents")}
        missing_columns = {
            "response_due_date": "ALTER TABLE hr_documents ADD COLUMN response_due_date DATE",
            "effective_date": "ALTER TABLE hr_documents ADD COLUMN effective_date DATE",
            "related_reference": "ALTER TABLE hr_documents ADD COLUMN related_reference VARCHAR(120)",
            "status": "ALTER TABLE hr_documents ADD COLUMN status VARCHAR(30) NOT NULL DEFAULT 'Draft'",
            "employee_response": "ALTER TABLE hr_documents ADD COLUMN employee_response TEXT",
            "acknowledged_at": "ALTER TABLE hr_documents ADD COLUMN acknowledged_at TIMESTAMP",
            "created_by": "ALTER TABLE hr_documents ADD COLUMN created_by INTEGER",
            "created_at": "ALTER TABLE hr_documents ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "updated_at": "ALTER TABLE hr_documents ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "issued_at": "ALTER TABLE hr_documents ADD COLUMN issued_at TIMESTAMP",
            "document_id": "ALTER TABLE hr_documents ADD COLUMN document_id VARCHAR(32)",
            "attachment_filename": "ALTER TABLE hr_documents ADD COLUMN attachment_filename VARCHAR(255)",
            "attachment_original_name": "ALTER TABLE hr_documents ADD COLUMN attachment_original_name VARCHAR(255)",
            "attachment_sha256": "ALTER TABLE hr_documents ADD COLUMN attachment_sha256 VARCHAR(64)",
            "signature_required": "ALTER TABLE hr_documents ADD COLUMN signature_required BOOLEAN NOT NULL DEFAULT FALSE",
            "employee_signature_name": "ALTER TABLE hr_documents ADD COLUMN employee_signature_name VARCHAR(150)",
            "employee_signature_image": "ALTER TABLE hr_documents ADD COLUMN employee_signature_image TEXT",
            "employee_signature_method": "ALTER TABLE hr_documents ADD COLUMN employee_signature_method VARCHAR(20)",
            "employee_signature_statement": "ALTER TABLE hr_documents ADD COLUMN employee_signature_statement TEXT",
            "employee_signed_at": "ALTER TABLE hr_documents ADD COLUMN employee_signed_at TIMESTAMP",
            "employee_signature_ip": "ALTER TABLE hr_documents ADD COLUMN employee_signature_ip VARCHAR(64)",
            "employee_signature_user_agent": "ALTER TABLE hr_documents ADD COLUMN employee_signature_user_agent VARCHAR(255)",
            "liability_id": "ALTER TABLE hr_documents ADD COLUMN liability_id INTEGER REFERENCES employee_liabilities(id)",
            "wet_signed_filename": "ALTER TABLE hr_documents ADD COLUMN wet_signed_filename VARCHAR(255)",
            "wet_signed_original_name": "ALTER TABLE hr_documents ADD COLUMN wet_signed_original_name VARCHAR(255)",
            "wet_signed_sha256": "ALTER TABLE hr_documents ADD COLUMN wet_signed_sha256 VARCHAR(64)",
            "wet_signed_at": "ALTER TABLE hr_documents ADD COLUMN wet_signed_at TIMESTAMP",
            "wet_signed_by": "ALTER TABLE hr_documents ADD COLUMN wet_signed_by INTEGER REFERENCES employees(id)",
        }
        for column_name, statement in missing_columns.items():
            if column_name not in columns:
                db.session.execute(text(statement))
        db.session.commit()
        return
    db.session.execute(text("""
        CREATE TABLE hr_documents (
            id INTEGER PRIMARY KEY,
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            document_type VARCHAR(60) NOT NULL,
            subject VARCHAR(180) NOT NULL,
            body TEXT NOT NULL,
            response_due_date DATE,
            effective_date DATE,
            related_reference VARCHAR(120),
            status VARCHAR(30) NOT NULL DEFAULT 'Draft',
            employee_response TEXT,
            acknowledged_at TIMESTAMP,
            created_by INTEGER REFERENCES employees(id),
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            issued_at TIMESTAMP,
            document_id VARCHAR(32),
            attachment_filename VARCHAR(255),
            attachment_original_name VARCHAR(255),
            attachment_sha256 VARCHAR(64),
            signature_required BOOLEAN NOT NULL DEFAULT TRUE,
            employee_signature_name VARCHAR(150),
            employee_signature_image TEXT,
            employee_signature_method VARCHAR(20),
            employee_signature_statement TEXT,
            employee_signed_at TIMESTAMP,
            employee_signature_ip VARCHAR(64),
            employee_signature_user_agent VARCHAR(255),
            liability_id INTEGER REFERENCES employee_liabilities(id),
            wet_signed_filename VARCHAR(255),
            wet_signed_original_name VARCHAR(255),
            wet_signed_sha256 VARCHAR(64),
            wet_signed_at TIMESTAMP,
            wet_signed_by INTEGER REFERENCES employees(id)
        )
    """))
    db.session.commit()


def ensure_staff_concern_schema():
    inspector = inspect(db.engine)
    if "staff_concerns" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("staff_concerns")}
        missing_columns = {
            "concern_type": "ALTER TABLE staff_concerns ADD COLUMN concern_type VARCHAR(40) NOT NULL DEFAULT 'Clarification'",
            "subject": "ALTER TABLE staff_concerns ADD COLUMN subject VARCHAR(180) NOT NULL DEFAULT 'Staff Concern'",
            "message": "ALTER TABLE staff_concerns ADD COLUMN message TEXT NOT NULL DEFAULT ''",
            "status": "ALTER TABLE staff_concerns ADD COLUMN status VARCHAR(30) NOT NULL DEFAULT 'Open'",
            "admin_response": "ALTER TABLE staff_concerns ADD COLUMN admin_response TEXT",
            "responded_by": "ALTER TABLE staff_concerns ADD COLUMN responded_by INTEGER",
            "created_at": "ALTER TABLE staff_concerns ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "responded_at": "ALTER TABLE staff_concerns ADD COLUMN responded_at TIMESTAMP",
        }
        for column_name, statement in missing_columns.items():
            if column_name not in columns:
                db.session.execute(text(statement))
        db.session.commit()
        return
    db.session.execute(text("""
        CREATE TABLE staff_concerns (
            id INTEGER PRIMARY KEY,
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            concern_type VARCHAR(40) NOT NULL DEFAULT 'Clarification',
            subject VARCHAR(180) NOT NULL,
            message TEXT NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'Open',
            admin_response TEXT,
            responded_by INTEGER REFERENCES employees(id),
            created_at TIMESTAMP NOT NULL,
            responded_at TIMESTAMP
        )
    """))
    db.session.commit()


def ensure_loan_tracking_columns():
    inspector = inspect(db.engine)
    loan_columns = {column["name"] for column in inspector.get_columns("loans")}
    employee_columns = {column["name"] for column in inspector.get_columns("employees")}
    payroll_columns = {column["name"] for column in inspector.get_columns("payrolls")}
    if "deduction_per_cutoff" not in loan_columns:
        db.session.execute(text(
            "ALTER TABLE loans ADD COLUMN deduction_per_cutoff FLOAT NOT NULL DEFAULT 500"
        ))
    if "loan_deduction_per_cutoff" not in employee_columns:
        db.session.execute(text(
            "ALTER TABLE employees ADD COLUMN loan_deduction_per_cutoff FLOAT NOT NULL DEFAULT 500"
        ))
    if "loan_deduction_override" not in payroll_columns:
        db.session.execute(text(
            "ALTER TABLE payrolls ADD COLUMN loan_deduction_override FLOAT"
        ))
    if "loan_type" not in loan_columns:
        db.session.execute(text(
            "ALTER TABLE loans ADD COLUMN loan_type VARCHAR(30) NOT NULL DEFAULT 'Employee Loan'"
        ))
    if "balance_applied" not in loan_columns:
        db.session.execute(text(
            "ALTER TABLE loans ADD COLUMN balance_applied BOOLEAN NOT NULL DEFAULT TRUE"
        ))
    db.session.commit()


def ensure_employee_hr_columns():
    employee_columns = {column["name"] for column in inspect(db.engine).get_columns("employees")}
    owner_exemption_column_missing = "payroll_attendance_exempt" not in employee_columns
    columns = {
        "job_description": "VARCHAR(150)",
        "employment_status": "VARCHAR(30)",
        "probation_end_date": "DATE",
        "regularization_date": "DATE",
        "payroll_attendance_exempt": "BOOLEAN NOT NULL DEFAULT FALSE",
        "manual_monthly_sss": "FLOAT NOT NULL DEFAULT 0",
        "manual_monthly_philhealth": "FLOAT NOT NULL DEFAULT 0",
        "manual_monthly_pagibig": "FLOAT NOT NULL DEFAULT 0",
        "manual_contribution_cutoff_start": "DATE",
        "gender": "VARCHAR(20)",
        "civil_status": "VARCHAR(30)",
        "immediate_supervisor": "VARCHAR(120)",
        "work_location": "VARCHAR(100)",
        "contribution_workweek": "INTEGER",
        "tax_status": "VARCHAR(30)",
        "bank_name": "VARCHAR(100)",
        "bank_account_name": "VARCHAR(150)",
        "bank_account_number": "VARCHAR(100)",
    }
    for column_name, column_type in columns.items():
        if column_name not in employee_columns:
            db.session.execute(text(f"ALTER TABLE employees ADD COLUMN {column_name} {column_type}"))
    if owner_exemption_column_missing:
        db.session.execute(text("""
            UPDATE employees
            SET payroll_attendance_exempt = TRUE
            WHERE LOWER(role) = 'admin'
              AND LOWER(email) IN ('randolfronquillo20@gmail.com', 'edzvronquillo@gmail.com')
        """))
    db.session.execute(text("""
        UPDATE employees
        SET payroll_attendance_exempt = FALSE
        WHERE LOWER(COALESCE(role, '')) != 'admin'
          AND payroll_attendance_exempt = TRUE
    """))
    db.session.commit()


def ensure_document_verification_columns():
    table_name = 'payslip_verifications'
    inspector = inspect(db.engine)
    if table_name not in inspector.get_table_names():
        return
    columns = {column['name'] for column in inspector.get_columns(table_name)}
    statements = []
    if 'document_type' not in columns:
        statements.append("ALTER TABLE payslip_verifications ADD COLUMN document_type VARCHAR(40) NOT NULL DEFAULT 'payslip'")
    if 'document_label' not in columns:
        statements.append("ALTER TABLE payslip_verifications ADD COLUMN document_label VARCHAR(120)")
    if 'payroll_id' not in columns:
        statements.append("ALTER TABLE payslip_verifications ADD COLUMN payroll_id INTEGER")
    if 'net_pay' not in columns:
        statements.append("ALTER TABLE payslip_verifications ADD COLUMN net_pay FLOAT NOT NULL DEFAULT 0")
    if 'cutoff_start' not in columns:
        statements.append("ALTER TABLE payslip_verifications ADD COLUMN cutoff_start DATE")
    if 'cutoff_end' not in columns:
        statements.append("ALTER TABLE payslip_verifications ADD COLUMN cutoff_end DATE")
    if 'created_at' not in columns:
        statements.append("ALTER TABLE payslip_verifications ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP")
    if 'verification_hash' not in columns:
        statements.append("ALTER TABLE payslip_verifications ADD COLUMN verification_hash VARCHAR(128) NOT NULL DEFAULT ''")
    for statement in statements:
        db.session.execute(text(statement))
    if db.engine.dialect.name == 'postgresql' and 'payroll_id' in columns:
        db.session.execute(text(
            'ALTER TABLE payslip_verifications ALTER COLUMN payroll_id DROP NOT NULL'
        ))
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(text(
            'ALTER TABLE payslip_verifications ALTER COLUMN cutoff_start DROP NOT NULL'
        ))
        db.session.execute(text(
            'ALTER TABLE payslip_verifications ALTER COLUMN cutoff_end DROP NOT NULL'
        ))
    db.session.commit()


def bootstrap_postgres_from_sqlite():
    if db.engine.dialect.name != "postgresql":
        return

    source_path = os.path.join(basedir, "instance", "hris.db")
    if not os.path.isfile(source_path):
        logger.warning("SQLite bootstrap source is unavailable: %s", source_path)
        return

    with db.engine.begin() as target_connection:
        if target_connection.execute(text("SELECT COUNT(*) FROM employees")).scalar_one() > 0:
            return

        source_engine = create_engine(f"sqlite:///{source_path}")
        try:
            source_metadata = MetaData()
            source_metadata.reflect(bind=source_engine)
            target_metadata = MetaData()
            target_metadata.reflect(bind=target_connection)
            with source_engine.connect() as source_connection:
                for source_table in source_metadata.sorted_tables:
                    if source_table.name == "alembic_version":
                        continue
                    target_table = target_metadata.tables.get(source_table.name)
                    if target_table is None:
                        logger.warning("SQLite bootstrap skipped missing target table: %s", source_table.name)
                        continue
                    rows = source_connection.execute(source_table.select()).mappings().all()
                    if rows:
                        normalized_rows = []
                        for row in rows:
                            normalized_row = {}
                            for column_name, value in row.items():
                                target_column = target_table.columns.get(column_name)
                                if target_column is None:
                                    continue
                                if value == "":
                                    try:
                                        if target_column.type.python_type is not str:
                                            value = None
                                    except NotImplementedError:
                                        pass
                                normalized_row[column_name] = value
                            normalized_rows.append(normalized_row)
                        target_connection.execute(target_table.insert(), normalized_rows)
        finally:
            source_engine.dispose()


def sync_postgres_id_sequences():
    if db.engine.dialect.name != 'postgresql':
        return
    inspector = inspect(db.engine)
    for table_name in inspector.get_table_names():
        if table_name == 'alembic_version' or 'id' not in {
            column['name'] for column in inspector.get_columns(table_name)
        }:
            continue
        safe_table_name = table_name.replace('"', '""')
        sequence_name = db.session.execute(
            text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
            {'table_name': table_name},
        ).scalar()
        if not sequence_name:
            continue
        db.session.execute(
            text(
                f'SELECT setval(:sequence_name, '
                f'COALESCE((SELECT MAX(id) FROM "{safe_table_name}"), 0) + 1, false)'
            ),
            {'sequence_name': sequence_name},
        )
    db.session.commit()


EMPLOYMENT_STATUSES = ("Trainee", "Probationary", "Regular")
OWNER_ADMIN_EMAILS = {
    "randolfronquillo20@gmail.com",
    "edzvronquillo@gmail.com",
}
DEFAULT_JOB_DESCRIPTIONS = (
    "Office Staff",
    "Warehouse Staff",
    "Driver",
    "Delivery Assistant",
    "In-House Agent",
)


def is_owner_admin_identity(role, email):
    return (
        str(role or "").strip().casefold() == "admin"
        and str(email or "").strip().casefold() in OWNER_ADMIN_EMAILS
    )


def ensure_job_description_options():
    existing = {option.name.casefold() for option in JobDescriptionOption.query.all()}
    for name in DEFAULT_JOB_DESCRIPTIONS:
        if name.casefold() not in existing:
            db.session.add(JobDescriptionOption(name=name))
    db.session.commit()


def ensure_app_secret_key():
    if app.secret_key not in WEAK_SECRET_KEYS:
        return
    db.session.execute(text(
        "CREATE TABLE IF NOT EXISTS app_secrets "
        "(name VARCHAR(64) PRIMARY KEY, value VARCHAR(128) NOT NULL)"
    ))
    db.session.execute(
        text(
            "INSERT INTO app_secrets (name, value) VALUES ('session_key', :value) "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {'value': secrets.token_hex(32)},
    )
    db.session.commit()
    app.secret_key = db.session.execute(
        text("SELECT value FROM app_secrets WHERE name = 'session_key'")
    ).scalar_one()


def normalize_employee_names():
    changed = False
    for employee in Employee.query.all():
        for field in ("first_name", "middle_name", "last_name", "suffix_name"):
            value = getattr(employee, field)
            formatter = format_suffix_name if field == "suffix_name" else format_person_name
            normalized = formatter(value) or None
            if value != normalized:
                setattr(employee, field, normalized)
                changed = True
    if changed:
        db.session.commit()


with app.app_context():
    db.create_all()
    ensure_app_secret_key()
    ensure_employee_resume_columns()
    remove_employee_registration_name_key_constraint()
    ensure_evaluation_tracking_columns()
    ensure_payroll_columns()
    ensure_biometric_access_columns()
    ensure_admin_payroll_access_column()
    ensure_employee_liability_schema()
    ensure_hr_document_schema()
    ensure_staff_concern_schema()
    ensure_loan_tracking_columns()
    ensure_employee_hr_columns()
    ensure_job_description_options()
    ensure_document_verification_columns()
    bootstrap_postgres_from_sqlite()
    normalize_employee_names()
    sync_postgres_id_sequences()
    ensure_default_contribution_settings()

migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ------------------ APScheduler setup (optional) ------------------
def run_scheduled_auto_quiz(category):
    with app.app_context():
        generate_auto_quiz(category)


def calendar_reminder_email_configured():
    if app.config.get('TESTING') or app.config.get('MAIL_SUPPRESS_SEND'):
        return False
    if app.config.get('MAIL_PROVIDER') == 'brevo':
        return bool(app.config.get('BREVO_API_KEY') and app.config.get('MAIL_DEFAULT_SENDER'))
    return bool(
        app.config.get('MAIL_PROVIDER') == 'smtp' and mail
        and app.config.get('MAIL_SERVER') and app.config.get('MAIL_DEFAULT_SENDER')
    )


def send_calendar_reminder_emails():
    with app.app_context():
        if not calendar_reminder_email_configured():
            logger.warning('Calendar email dispatcher disabled: mail provider is not configured.')
            return
        try:
            dispatch_reminders(send_notification_email)
        except (SQLAlchemyError, OSError, ValueError):
            db.session.rollback()
            logger.exception('Calendar email dispatcher failed; review reminder delivery status.')


try:
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    categories = ["Engine", "Transmission", "Electrical", "Suspension",
                  "Cooling", "Car Brands", "Brakes", "Steering", "Exhaust"]

    for cat in categories:
        if scheduler:
            scheduler.add_job(run_scheduled_auto_quiz, 'cron', day=1, hour=0, args=[cat])

    # Monthly peer evaluation reminder: post bulletin and email admins
    if scheduler:
        try:
            scheduler.add_job(generate_monthly_peer_eval_reminder, 'cron', day=1, hour=8)
            scheduler.add_job(
                send_month_end_reminders,
                'cron',
                hour=8,
                minute=0,
                timezone=ZoneInfo('Asia/Manila'),
                id='month_end_quiz_peer_reminder',
                replace_existing=True,
            )
            scheduler.add_job(send_lunch_reminder, 'cron', hour=12, minute=0,
                              args=['start'], id='lunch_start_reminder', replace_existing=True)
            scheduler.add_job(send_lunch_reminder, 'cron', hour=13, minute=0,
                              args=['resume'], id='lunch_resume_reminder', replace_existing=True)
        except Exception:
            logger.exception('Failed to register monthly peer-eval reminder job')

    if scheduler:
        scheduler.add_job(
            send_calendar_reminder_emails, 'interval', seconds=60,
            id='company_calendar_reminders', replace_existing=True, max_instances=1,
        )
        scheduler.start()
        logger.info("Scheduler started. Auto quiz jobs registered for all categories.")
except Exception:
    scheduler = None
    logger.warning("APScheduler not available — scheduler disabled for this run.")

# ------------------ ROUTES ------------------
@app.route('/')
def home():
    return render_template('home.html')

from werkzeug.security import generate_password_hash, check_password_hash



# ------------------ LOGIN MANAGER ------------------
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Employee, int(user_id))


def record_audit_action(action, response=None):
    if not current_user.is_authenticated:
        return
    try:
        db.session.add(AuditLog(
            employee_id=current_user.id,
            employee_name=current_user.full_name(),
            company=current_user.company,
            action=action,
            path=request.path,
            ip_address=request.headers.get('X-Forwarded-For', request.remote_addr),
            status_code=getattr(response, 'status_code', None)
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('Could not write audit log for %s', action)


@app.after_request
def audit_authenticated_request(response):
    if (current_user.is_authenticated
            and request.endpoint not in {'static', 'service_worker', 'audit_logs'}
            and not request.path.startswith('/static/')):
        action = 'Page access'
        if request.endpoint == 'download_payslip':
            action = 'Weekly payslip download'
        elif request.endpoint == 'monthly_payroll' and request.args.get('download') == 'true':
            action = 'Monthly payslip download'
        elif response.headers.get('Content-Disposition', '').lower().startswith('attachment'):
            action = 'File download'
        record_audit_action(action, response)
    return response


@app.after_request
def stamp_download_response(response):
    content_disposition = response.headers.get('Content-Disposition', '').lower()
    is_attachment = content_disposition.startswith('attachment')
    is_pdf = response.mimetype == 'application/pdf'
    if response.status_code != 200 or (not is_attachment and not is_pdf):
        return response
    if not current_user.is_authenticated:
        return response

    downloaded_at = datetime.now(ZoneInfo('Asia/Manila'))
    downloaded_by = (
        f'{current_user.full_name()} '
        f'({str(current_user.role or "user").title()})'
    )
    downloaded_by = downloaded_by.encode('ascii', 'replace').decode('ascii')
    timestamp_text = downloaded_at.strftime('%Y-%m-%d %I:%M:%S %p PHT')
    response.headers['X-HRIS-Downloaded-At'] = timestamp_text
    response.headers['X-HRIS-Downloaded-By'] = downloaded_by
    response.headers['Cache-Control'] = 'private, no-store'
    if is_attachment:
        disposition, options = parse_options_header(
            response.headers['Content-Disposition']
        )
        filename = options.get('filename')
        if filename:
            name, extension = os.path.splitext(filename)
            options['filename'] = (
                f'{name}_DL_{downloaded_at:%Y%m%d_%H%M%S}_PHT{extension}'
            )
            response.headers['Content-Disposition'] = dump_options_header(
                disposition, options
            )

    unmodified_document_endpoints = {
        'download_employee_document',
        'download_file',
        'view_hr_document_attachment',
        'view_wet_signed_hr_document',
    }
    if (
        not is_attachment
        or not is_pdf
        or request.endpoint in unmodified_document_endpoints
    ):
        return response

    response.direct_passthrough = False
    source_pdf = PdfReader(io.BytesIO(response.get_data()))
    if not source_pdf.pages:
        raise ValueError('Cannot stamp a PDF without pages.')

    writer = PdfWriter()
    for page_number, page in enumerate(source_pdf.pages):
        writer.add_page(page)
        if page_number == len(source_pdf.pages) - 1:
            stamped_page = writer.pages[-1]
            footer_height = 40
            stamped_page.add_transformation(
                Transformation().translate(ty=footer_height)
            )
            stamped_page.mediabox.upper_right = (
                float(page.mediabox.right),
                float(page.mediabox.top) + footer_height,
            )
            stamped_page.cropbox = stamped_page.mediabox
            page_size = (
                float(page.mediabox.width),
                float(page.mediabox.height) + footer_height,
            )
            stamp_buffer = io.BytesIO()
            stamp_pdf = canvas.Canvas(stamp_buffer, pagesize=page_size)
            stamp_pdf.setFont('Helvetica', 7)
            stamp_pdf.drawString(30, 24, f'Downloaded by: {downloaded_by}')
            stamp_pdf.drawString(30, 12, f'Downloaded at: {timestamp_text}')
            stamp_pdf.save()
            stamp_buffer.seek(0)
            stamp_page = PdfReader(stamp_buffer).pages[0]
            stamped_page.merge_page(stamp_page)

    metadata = {
        key: str(value)
        for key, value in (source_pdf.metadata or {}).items()
        if value is not None
    }
    metadata.update({
        '/HRISDownloadedBy': downloaded_by,
        '/HRISDownloadedAt': timestamp_text,
    })
    writer.add_metadata(metadata)
    stamped_pdf = io.BytesIO()
    writer.write(stamped_pdf)
    response.set_data(stamped_pdf.getvalue())
    response.headers.pop('ETag', None)
    response.headers.pop('Content-Range', None)
    response.headers.pop('Accept-Ranges', None)
    return response


@app.after_request
def inject_authenticated_sidebar(response):
    auth_pages = {'login', 'register', 'forgot_password', 'reset_password'}
    if (
        not current_user.is_authenticated
        or request.endpoint in auth_pages
        or request.path in {'/login', '/register', '/forgot-password'}
        or request.path.startswith('/reset-password')
        or not response.content_type
        or 'text/html' not in response.content_type
        or request.path.startswith('/verify-')
        or request.args.get('export') == 'print'
        or request.args.get('download') == 'true'
    ):
        return response

    html = response.get_data(as_text=True)
    if '</body>' not in html or 'hris-global-sidebar' in html:
        return response
    if '<meta name="viewport"' not in html.lower() and '</head>' in html:
        html = html.replace('</head>', '<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">\n</head>', 1)

    is_admin = 'admin' in str(current_user.role or '').lower()
    if is_admin:
        sidebar = render_template(
            'partials/admin_sidebar.html',
            sidebar_user_id=current_user.id,
            workspace_company=current_user.company,
        )
        if '<body class="' in html:
            html = html.replace('<body class="', '<body class="hris-admin-sidebar-page ', 1)
        elif '<body ' in html:
            html = html.replace('<body ', '<body class="hris-admin-sidebar-page" ', 1)
        else:
            html = html.replace('<body>', '<body class="hris-admin-sidebar-page">', 1)
        response.set_data(html.replace('</body>', f'{sidebar}</body>', 1))
        return response
    dashboard_endpoint = 'dashboard_admin' if is_admin else 'dashboard_staff'
    links = [
        (dashboard_endpoint, 'Dashboard'),
        ('payroll_dashboard' if is_admin else 'payroll', 'Payroll'),
        ('attendance', 'Attendance'),
        ('leave', 'Leave'),
        ('loan', 'Loans'),
        ('profile', 'Profile'),
    ]
    if not is_admin:
        links[1:1] = [
            ('staff_help', 'Staff Help'),
            ('monthly_reminders', 'Monthly Reminders'),
            ('quiz', 'Quiz'),
            ('thirteenth_month', '13th-Month Pay'),
        ]
        links.extend([
            ('my_analytics', 'My Analytics'),
            ('assessment', 'Performance'),
            ('peer_evaluation', 'Peer Evaluation'),
            ('merit_demerit', 'Merit / Demerit'),
            ('submit_incident', 'Incident Report'),
            ('attendance_correction', 'Attendance Correction'),
            ('apply_ot', 'Apply for OT'),
            ('employee_liabilities', 'Liabilities'),
            ('hr_documents', 'My HR Documents'),
            ('staff_concerns', 'Suggestions / Hinaing'),
            ('staff_messages', 'Messages'),
            ('bulletin', 'Company Bulletin'),
            ('company_files', 'Company Files'),
        ])
        if can_review_payroll_loans(current_user):
            links.insert(2, ('payroll_loan_review', 'Payroll Loan Review'))
        if can_prepare_payroll(current_user) or can_review_payroll_loans(current_user):
            links.insert(3, ('payroll_summary', 'Cash Payroll Summary'))
    if is_admin:
        links.extend([
            ('holiday_ot_dashboard', 'Attendance and OT'),
            ('evaluation_dashboard', 'Evaluation Dashboard'),
            ('admin_quiz_upload', 'Quiz Questionnaire'),
            ('admin_files', 'Upload Company Files'),
            ('admin_hr_templates', 'HR Templates'),
            ('employee_201_selector', 'Staff 201 Files'),
            ('admin_incidents', 'Incident Reports'),
            ('hr_documents', 'HR Documents'),
            ('staff_concerns', 'Staff Concerns'),
            ('staff_messages', 'Staff Messages'),
            ('monthly_deductions', 'Monthly Deductions'),
            ('government_contributions', 'Government Contributions'),
            ('employee_liabilities', 'Liabilities / Shortage'),
            ('tax_reports', 'Tax Reports'),
            ('compliance_reports', 'Compliance Reports'),
            ('thirteenth_month', '13th-Month Pay'),
            ('backup', 'Backup Database'),
            ('audit_logs', 'Audit Logs'),
        ])

    link_markup = ''.join(
        f'<a href="{url_for(endpoint, employee_id=current_user.id) if endpoint in {"payroll", "attendance", "quiz", "merit_demerit"} else url_for(endpoint, user_id=current_user.id) if endpoint == "profile" else url_for(endpoint)}">{label}</a>'
        for endpoint, label in links
    )
    if not is_admin:
        navigation_groups = [
            ('Attendance', {'attendance', 'attendance_correction', 'apply_ot', 'my_analytics'}),
            ('Payroll & Reports', {'payroll', 'payroll_dashboard', 'payroll_summary', 'payroll_loan_review', 'thirteenth_month', 'employee_liabilities'}),
            ('Loan & Leave', {'loan', 'leave'}),
            ('Performance', {'assessment', 'quiz', 'peer_evaluation', 'merit_demerit'}),
            ('Documents & Updates', {'hr_documents', 'bulletin', 'company_files', 'monthly_reminders'}),
            ('Help & Concerns', {'staff_concerns', 'submit_incident', 'staff_help'}),
            ('My Account', {'profile', 'staff_messages'}),
        ]
        sidebar = render_template(
            'partials/staff_navigation.html',
            navigation_groups=navigation_groups,
            navigation_links=links,
            navigation_user_id=current_user.id,
            navigation_company=current_user.company,
        )
        if '<body class="' in html:
            html = html.replace('<body class="', '<body class="hris-staff-navigation-page ', 1)
        elif '<body ' in html:
            html = html.replace('<body ', '<body class="hris-staff-navigation-page" ', 1)
        else:
            html = html.replace('<body>', '<body class="hris-staff-navigation-page">', 1)
        html = html.replace('</body>', f'{sidebar}</body>', 1)
        response.set_data(html)
        return response
    sidebar = f'''
<style id="hris-global-sidebar-style">
    body.hris-sidebar-page {{ --hris-topbar-row-height: 54px; --hris-topbar-font-size: 18px; --hris-topbar-title-size: 26px; --hris-topbar-column-width: 176px; padding-left: 0 !important; padding-top: calc((var(--hris-topbar-row-height) * 2) + 32px) !important; }}
    #hris-global-sidebar {{ position: fixed; z-index: 10000; inset: 0 0 auto 0; width: 100% !important; max-width: 100vw; height: auto; box-sizing: border-box; display: grid; grid-auto-flow: column; grid-template-rows: repeat(2, var(--hris-topbar-row-height)); grid-auto-columns: minmax(var(--hris-topbar-column-width), 1fr); align-items: stretch; gap: 8px 10px; padding: 10px 14px 12px; background: #122c3b; color: #f4f7f6; box-shadow: 0 3px 14px rgba(7,25,36,.28); font-family: Arial, sans-serif; overflow-x: auto; overflow-y: hidden; -webkit-overflow-scrolling: touch; overscroll-behavior-x: contain; touch-action: pan-x; }}
    #hris-global-sidebar h2 {{ grid-row: 1 / span 2; align-self: center; margin: 0 10px 0 0; min-width: 92px; font-size: var(--hris-topbar-title-size); line-height: 1.2; letter-spacing: .2px; white-space: nowrap; }}
    #hris-global-sidebar p {{ display: none; }}
    #hris-global-sidebar a {{ display: flex !important; align-items: center; justify-content: center; box-sizing: border-box; width: 100% !important; height: var(--hris-topbar-row-height) !important; min-height: var(--hris-topbar-row-height) !important; min-width: 0; padding: 13px 18px !important; margin: 0 !important; color: #f4f7f6; text-decoration: none; border-left: 0; border-bottom: 3px solid transparent; border-radius: 7px; font-size: var(--hris-topbar-font-size) !important; line-height: 1.25 !important; white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis; }}
    #hris-global-sidebar a:hover, #hris-global-sidebar a:focus-visible {{ background: #1e5266; border-bottom-color: #f2c14e; }}
    #hris-global-sidebar a[href="{request.path}"] {{ background: #1e5266; border-bottom-color: #f2c14e; }}
    #hris-global-sidebar .sidebar-logout {{ margin-top: 0 !important; border-top: 0; padding-top: 9px !important; }}
    @media (max-width: 760px), (max-device-width: 920px) {{ body.hris-sidebar-page {{ --hris-topbar-row-height: 44px; --hris-topbar-font-size: 15px; --hris-topbar-title-size: 18px; --hris-topbar-column-width: 138px; }} #hris-global-sidebar {{ gap: 6px 7px; padding: 8px 10px 10px; }} #hris-global-sidebar h2 {{ margin-right: 4px; min-width: 64px; }} #hris-global-sidebar a {{ padding: 10px 13px !important; }} }}
</style>
<aside id="hris-global-sidebar">
    <h2>HRIS</h2>
    <p>{'Admin workspace' if is_admin else 'Employee self-service'}</p>
    {link_markup}
    <a class="sidebar-logout" href="{url_for('logout')}">Sign out</a>
</aside>
<script>(function() {{ const sizes={{small:['46px','16px','22px','154px'],medium:['54px','18px','26px','176px'],large:['62px','20px','30px','204px']}}; const apply=()=>{{ const selected=localStorage.getItem('hrisTopbarSize')||'medium'; const size=sizes[selected]||sizes.medium; document.body.style.setProperty('--hris-topbar-row-height',size[0]); document.body.style.setProperty('--hris-topbar-font-size',size[1]); document.body.style.setProperty('--hris-topbar-title-size',size[2]); document.body.style.setProperty('--hris-topbar-column-width',size[3]); }}; apply(); window.addEventListener('storage',event=>{{ if(event.key==='hrisTopbarSize') apply(); }}); }})();</script>
'''
    if '<body class="' in html:
        html = html.replace('<body class="', '<body class="hris-sidebar-page ', 1)
    elif '<body ' in html:
        html = html.replace('<body ', '<body class="hris-sidebar-page" ', 1)
    else:
        html = html.replace('<body>', '<body class="hris-sidebar-page">', 1)
    html = html.replace('</body>', f'{sidebar}</body>', 1)
    response.set_data(html)
    return response

# ----------------- LOGIN + FORGOT PASSWORD ------------------
from werkzeug.security import check_password_hash, generate_password_hash


@app.route('/login', methods=['GET', 'POST'])
def login():
    action = request.form.get('action')

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()

        # --- LOGIN FLOW ---
        if action == "login":
            password = request.form.get('password')
            remember = 'remember' in request.form

            users = Employee.query.filter(Employee.email.ilike(email)).order_by(Employee.id.desc()).all()
            user = next(
                (candidate for candidate in users if candidate.password and check_password_hash(candidate.password, password)),
                None
            )
            if user:
                login_user(user, remember=remember)
                record_audit_action('Login', None)

                # role check
                if "admin" in user.role.lower():
                    return redirect(url_for("dashboard_admin"))
                elif "staff" in user.role.lower():
                    return redirect(url_for("dashboard_staff"))
                else:
                    flash("⚠️ Unknown role, contact admin.", "warning")
                    return redirect(url_for("login"))
            else:
                flash("❌ Invalid email or password", "danger")

    return render_template("login.html")


@app.route('/admin/audit-logs')
@login_required
def audit_logs():
    if 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(500).all()
    return render_template('audit_logs.html', logs=logs)


def backup_cell_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return str(value)
    return value


def build_database_backup_bytes():
    with tempfile.TemporaryDirectory() as backup_dir:
        backup_path = os.path.join(backup_dir, 'hris_backup.db')
        backup_engine = create_engine(f'sqlite:///{backup_path}')
        try:
            db.metadata.create_all(backup_engine)
        finally:
            backup_engine.dispose()
        backup_connection = sqlite3.connect(backup_path)
        try:
            for table in db.metadata.sorted_tables:
                columns = [column.name for column in table.columns]
                rows = db.session.execute(table.select()).all()
                if not rows:
                    continue
                column_sql = ', '.join(f'"{name}"' for name in columns)
                placeholders = ', '.join('?' for _ in columns)
                backup_connection.executemany(
                    f'INSERT INTO "{table.name}" ({column_sql}) VALUES ({placeholders})',
                    [tuple(backup_cell_value(value) for value in row) for row in rows],
                )
            backup_connection.commit()
        finally:
            backup_connection.close()
        with open(backup_path, 'rb') as backup_file:
            return backup_file.read()


@app.route('/admin/backup/download')
@login_required
def download_database_backup():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    backup_bytes = build_database_backup_bytes()
    record_audit_action('Downloaded full database backup')
    return send_file(
        io.BytesIO(backup_bytes),
        mimetype='application/x-sqlite3',
        as_attachment=True,
        download_name=f'hris_backup_{datetime.now(ZoneInfo("Asia/Manila")):%Y%m%d}.db',
    )


def gov_company_label(employee):
    return 'Trece-Uno' if str(employee.company or '').lower().startswith('trece') else 'Auto Expert'


def gov_month_from_request():
    value = request.values.get('month')
    try:
        return datetime.strptime(value, '%Y-%m').date() if value else datetime.now(ZoneInfo('Asia/Manila')).date().replace(day=1)
    except (TypeError, ValueError):
        return datetime.now(ZoneInfo('Asia/Manila')).date().replace(day=1)


def gov_company_from_request():
    company = request.values.get('company', '')
    return company if company in ('Trece-Uno', 'Auto Expert') else ''


def gov_id_digits(value):
    return re.sub(r'\D', '', str(value or ''))


def gov_duplicate_ids():
    seen = {}
    for employee in Employee.query.all():
        for field, agency in (('sss', 'SSS'), ('philhealth', 'PhilHealth'), ('pagibig', 'Pag-IBIG')):
            digits = gov_id_digits(getattr(employee, field))
            if digits:
                seen.setdefault((agency, digits), []).append(employee)
    return {key: owners for key, owners in seen.items() if len(owners) > 1}


def gov_attendance_month(employee, cutoff_dates):
    daily_rate = float(employee.daily_rate or 0)
    bases, days = [], 0
    for cutoff in cutoff_dates:
        regular = [
            item for item in payroll_attendance_records(employee, cutoff, cutoff + timedelta(days=7))
            if item.date.weekday() != 6
        ]
        hours = [regular_attendance_hours(item) for item in regular]
        days += sum(1 for value in hours if value > 0)
        bases.append(round(sum(daily_rate * value / 8.0 for value in hours), 2))
    return bases, days


def build_government_contribution_month(month, company='', only_employee_id=None):
    """Official-rate contribution register for a payroll month (no records are modified)."""
    month = month.replace(day=1)
    calculator = GovernmentContributionCalculator.for_month(month)
    cutoff_dates = payroll_cutoff_dates_in_month(month)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    today = datetime.now(ZoneInfo('Asia/Manila')).date()
    closed_cutoffs = [item for item in cutoff_dates if item + timedelta(days=7) <= today]
    saved_by_employee = {}
    for record in Payroll.query.filter(
        Payroll.cutoff_start >= month, Payroll.cutoff_start < next_month,
    ).all():
        saved_by_employee.setdefault(record.employee_id, []).append(record)
    duplicates = gov_duplicate_ids()
    duplicate_messages = {}
    for (agency, digits), owners in duplicates.items():
        for owner in owners:
            others = ', '.join(item.full_name() for item in owners if item.id != owner.id)
            duplicate_messages.setdefault(owner.id, []).append(f'Duplicate {agency} number shared with {others}')

    rows, validations = [], []
    employees = Employee.query.order_by(Employee.company, Employee.last_name, Employee.first_name).all()
    for employee in employees:
        label = gov_company_label(employee)
        if company and label != company:
            continue
        if only_employee_id is not None and employee.id != only_employee_id:
            continue
        saved = sorted(saved_by_employee.get(employee.id, []), key=lambda item: item.cutoff_start)
        manual = bool(employee.payroll_attendance_exempt)
        bases, days = ([], 0) if manual else gov_attendance_month(employee, cutoff_dates)
        if not saved and not any(bases):
            continue
        deducted = {key: sum((gov_money(getattr(item, key)) for item in saved), gov_money(0))
                    for key in GOV_AMOUNT_KEYS}
        issues = []
        if manual:
            result = None
            amounts = {
                'sss_ee': deducted['sss'], 'sss_er': gov_money(0), 'sss_ec': gov_money(0),
                'ph_ee': deducted['philhealth'], 'ph_er': gov_money(0),
                'pi_ee': deducted['pagibig'], 'pi_er': gov_money(0),
            }
        else:
            try:
                result = calculator.monthly(sum(bases), employee.daily_rate, employee.contribution_workweek)
            except ContributionSettingsError as error:
                result = None
                issues.append(f'Invalid contribution bracket: {error}')
            amounts = {
                'sss_ee': result.sss.employee if result else gov_money(0),
                'sss_er': result.sss.employer if result else gov_money(0),
                'sss_ec': result.sss.ec if result else gov_money(0),
                'ph_ee': result.philhealth.employee if result else gov_money(0),
                'ph_er': result.philhealth.employer if result else gov_money(0),
                'pi_ee': result.pagibig.employee if result else gov_money(0),
                'pi_er': result.pagibig.employer if result else gov_money(0),
            }
            if float(employee.daily_rate or 0) <= 0:
                issues.append('Missing daily rate')
            if days == 0:
                issues.append('Missing attendance for this month')
            saved_starts = {item.cutoff_start for item in saved}
            missing_payroll = [item for item in closed_cutoffs if item not in saved_starts]
            if missing_payroll:
                issues.append('Missing payroll data for cutoff ' + ', '.join(f'{item:%b %d}' for item in missing_payroll))
        for field, agency in (('sss', 'SSS'), ('philhealth', 'PhilHealth'), ('pagibig', 'Pag-IBIG MID')):
            if not gov_id_digits(getattr(employee, field)):
                issues.append(f'Missing {agency} number')
        issues.extend(duplicate_messages.get(employee.id, []))
        deducted_ee = deducted['sss'] + deducted['philhealth'] + deducted['pagibig']
        employee_share = amounts['sss_ee'] + amounts['ph_ee'] + amounts['pi_ee']
        employer_share = amounts['sss_er'] + amounts['sss_ec'] + amounts['ph_er'] + amounts['pi_er']
        actual_earnings = gov_money(sum(bases))
        gross_pay = (
            sum((gov_money(item.gross_income) for item in saved), gov_money(0)) if saved else actual_earnings
        )
        row = {
            'employee': employee, 'company': label, 'manual': manual, 'result': result,
            'daily_rate': gov_money(employee.daily_rate), 'days_worked': days,
            'cutoff_bases': list(zip(cutoff_dates, bases)), 'actual_earnings': actual_earnings,
            'saved_cutoffs': len(saved), 'paid_cutoffs': sum(1 for item in saved if item.is_paid),
            'cutoff_count': len(cutoff_dates), 'gross_pay': gross_pay,
            'deducted': deducted, 'deducted_ee': deducted_ee,
            'employee_share': employee_share, 'employer_share': employer_share,
            'remittance_total': employee_share + employer_share,
            'ee_difference': employee_share - deducted_ee,
            'true_cost': gross_pay + employer_share, 'issues': issues, **amounts,
        }
        rows.append(row)
        validations.extend({'employee': employee, 'company': label, 'message': issue} for issue in issues)
    return {
        'month': month, 'calculator': calculator, 'setting': calculator.setting,
        'cutoff_dates': cutoff_dates, 'rows': rows, 'validations': validations,
        'portal': GovPortalPaymentService.summarize(rows),
        'labor': GovLaborCostService.summarize(rows),
        'legacy_month': month < GOV_RULES_START,
    }


def gov_csrf_token():
    return session.setdefault('gov_contribution_csrf', secrets.token_urlsafe(32))


def gov_require_csrf():
    if not secrets.compare_digest(gov_csrf_token().encode(), request.form.get('csrf_token', '').encode()):
        abort(400)


def gov_page_context(view):
    month = gov_month_from_request()
    company = gov_company_from_request()
    employee_id = request.args.get('employee_id', type=int)
    employees = [
        employee for employee in Employee.query.order_by(
            Employee.company, Employee.last_name, Employee.first_name
        ).all()
        if 'admin' not in str(employee.role or '').lower()
        and (not company or gov_company_label(employee) == company)
    ]
    selected_employee = next((employee for employee in employees if employee.id == employee_id), None)
    if selected_employee is None:
        employee_id = None
    try:
        data = build_government_contribution_month(month, company, employee_id)
        error = None
    except ContributionSettingsError as exc:
        logger.warning('Government contribution settings error: %s', exc)
        data, error = None, str(exc)
    return {
        'view': view, 'month': month, 'company': company, 'data': data, 'settings_error': error,
        'employees': employees, 'selected_employee': selected_employee,
        'selected_employee_id': employee_id,
    }


@app.route('/admin/government-contributions')
@login_required
def government_contributions():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    return render_template('government_contributions.html', **gov_page_context('dashboard'))


@app.route('/admin/government-contributions/preview')
@login_required
def government_contribution_preview():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    return render_template('government_contributions.html', **gov_page_context('preview'))


@app.route('/admin/government-contributions/portal')
@login_required
def government_portal_payment():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    return render_template('government_contributions.html', **gov_page_context('portal'))


def gov_employee_history(employee):
    months = sorted({
        record.cutoff_start.replace(day=1)
        for record in Payroll.query.filter_by(employee_id=employee.id).all()
    }, reverse=True)[:24]
    history = []
    for month in months:
        try:
            data = build_government_contribution_month(month, gov_company_label(employee), employee.id)
        except ContributionSettingsError as exc:
            history.append({'month': month, 'row': None, 'error': str(exc)})
            continue
        row = next((item for item in data['rows'] if item['employee'].id == employee.id), None)
        history.append({'month': month, 'row': row, 'error': None, 'legacy_month': data['legacy_month']})
    return history


@app.route('/admin/government-contributions/history/<int:employee_id>')
@login_required
def government_contribution_history(employee_id):
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    employee = db.get_or_404(Employee, employee_id)
    return render_template(
        'government_contribution_history.html', employee=employee,
        history=gov_employee_history(employee),
    )


GOV_REPORTS = {
    'register': 'Government Contribution Register',
    'sss': 'SSS Monthly Report',
    'philhealth': 'PhilHealth Monthly Report',
    'pagibig': 'Pag-IBIG Monthly Report',
    'remittance': 'Monthly Remittance Summary',
    'payment': 'Government Payment Summary',
    'liability': 'Employer Liability Summary',
    'labor': 'Labor Cost Summary',
    'cost_per_employee': 'Cost Per Employee Summary',
    'history': 'Employee Contribution History',
}


def gov_report_table(report, data, employee=None):
    rows = data['rows'] if data else []
    name = lambda row: row['employee'].full_name()
    if report == 'register':
        headers = ['Employee No', 'Employee', 'Company', 'Daily Rate', 'Days Worked', 'Actual Earnings',
                   'SSS Bracket', 'SSS MSC', 'SSS EE', 'SSS ER', 'EC', 'SSS Total',
                   'PhilHealth Basis', 'PhilHealth EE', 'PhilHealth ER', 'PhilHealth Total',
                   'Pag-IBIG EE', 'Pag-IBIG ER', 'Pag-IBIG Total', 'Grand Total Payable', 'Issues']
        body = [[
            row['employee'].id, name(row), row['company'], row['daily_rate'], row['days_worked'],
            row['actual_earnings'],
            'Manual owner contribution' if row['manual'] else (row['result'].sss.bracket_label if row['result'] else 'Invalid'),
            row['result'].sss.msc if row['result'] else '',
            row['sss_ee'], row['sss_er'], row['sss_ec'], row['sss_ee'] + row['sss_er'] + row['sss_ec'],
            row['result'].philhealth.basis if row['result'] else '',
            row['ph_ee'], row['ph_er'], row['ph_ee'] + row['ph_er'],
            row['pi_ee'], row['pi_er'], row['pi_ee'] + row['pi_er'], row['remittance_total'],
            '; '.join(row['issues']),
        ] for row in rows]
    elif report == 'sss':
        headers = ['Employee', 'SSS Number', 'Actual Monthly Compensation', 'Salary Bracket', 'MSC',
                   'MPF Portion of MSC', 'Employee Share', 'Employer Share', 'EC', 'Total SSS']
        body = [[
            name(row), row['employee'].sss or '', row['actual_earnings'],
            row['result'].sss.bracket_label if row['result'] else 'Manual / invalid',
            row['result'].sss.msc if row['result'] else '', row['result'].sss.mpf_msc if row['result'] else '',
            row['sss_ee'], row['sss_er'], row['sss_ec'], row['sss_ee'] + row['sss_er'] + row['sss_ec'],
        ] for row in rows]
    elif report == 'philhealth':
        headers = ['Employee', 'PhilHealth Number', 'Daily Rate', 'Workweek', 'EMR',
                   'Monthly Basic Salary', 'Rate', 'Employee Share', 'Employer Share', 'Total PhilHealth']
        body = [[
            name(row), row['employee'].philhealth or '', row['daily_rate'],
            f"{row['result'].philhealth.workweek}-day" if row['result'] else 'Manual',
            row['result'].philhealth.emr if row['result'] else '',
            row['result'].philhealth.basis if row['result'] else '',
            f"{row['result'].philhealth.rate * 100:.2f}%" if row['result'] else '',
            row['ph_ee'], row['ph_er'], row['ph_ee'] + row['ph_er'],
        ] for row in rows]
    elif report == 'pagibig':
        headers = ['Employee', 'Pag-IBIG MID', 'Monthly Compensation', 'Fund Salary',
                   'Employee Share', 'Employer Share', 'Total Pag-IBIG']
        body = [[
            name(row), row['employee'].pagibig or '', row['actual_earnings'],
            row['result'].pagibig.fund_salary if row['result'] else '',
            row['pi_ee'], row['pi_er'], row['pi_ee'] + row['pi_er'],
        ] for row in rows]
    elif report in ('remittance', 'payment'):
        headers = ['Employer', 'Agency', 'Employee Share', 'Employer Share', 'EC', 'Grand Total Due',
                   'Already Deducted from Employees (EE)']
        body = []
        groups = list(data['portal']['companies'].items()) + [('ALL COMPANIES', data['portal']['overall'])]
        for label, totals in groups:
            body.append([label, 'SSS', totals['sss']['ee'], totals['sss']['er'], totals['sss']['ec'], totals['sss']['total'], ''])
            body.append([label, 'PhilHealth', totals['philhealth']['ee'], totals['philhealth']['er'], '', totals['philhealth']['total'], ''])
            body.append([label, 'Pag-IBIG', totals['pagibig']['ee'], totals['pagibig']['er'], '', totals['pagibig']['total'], ''])
            body.append([label, 'TOTAL', totals['employee_share'], totals['employer_share'], '', totals['overall'], totals['deducted_ee']])
    elif report in ('labor', 'liability', 'cost_per_employee'):
        headers = ['Employee', 'Company', 'Gross Pay', 'SSS ER', 'EC', 'PhilHealth ER', 'Pag-IBIG ER',
                   'Employer Contributions', 'Total Government Liability', 'True Cost to Company']
        body = [[
            name(row), row['company'], row['gross_pay'], row['sss_er'], row['sss_ec'], row['ph_er'],
            row['pi_er'], row['employer_share'], row['remittance_total'], row['true_cost'],
        ] for row in rows]
        labor = data['labor']
        body.append(['TOTAL', f"{labor['employees']} employees", labor['gross'], labor['sss_er'], labor['sss_ec'],
                     labor['ph_er'], labor['pi_er'], labor['employer_contributions'],
                     labor['government_liability'], labor['labor_cost']])
    elif report == 'history' and employee is not None:
        headers = ['Month', 'Actual Earnings', 'SSS EE', 'SSS ER', 'EC', 'PhilHealth EE', 'PhilHealth ER',
                   'Pag-IBIG EE', 'Pag-IBIG ER', 'Total Remittance', 'EE Deducted in Payroll', 'EE Difference']
        body = []
        for item in gov_employee_history(employee):
            row = item['row']
            if row is None:
                body.append([f"{item['month']:%B %Y}", item['error'] or 'No data'] + [''] * 10)
                continue
            body.append([f"{item['month']:%B %Y}", row['actual_earnings'], row['sss_ee'], row['sss_er'], row['sss_ec'],
                         row['ph_ee'], row['ph_er'], row['pi_ee'], row['pi_er'], row['remittance_total'],
                         row['deducted_ee'], row['ee_difference']])
    else:
        abort(404)
    return headers, body


@app.route('/admin/government-contributions/export')
@login_required
def government_contribution_export():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    report = request.args.get('report', 'register')
    fmt = request.args.get('format', 'xlsx')
    if report not in GOV_REPORTS or fmt not in GovReportsService.MIMETYPES:
        abort(404)
    employee = None
    if report == 'history':
        employee = db.get_or_404(Employee, request.args.get('employee_id', type=int) or 0)
        data = None
        subtitle = f'{employee.full_name()} - {gov_company_label(employee)}'
    else:
        context = gov_page_context('export')
        if context['data'] is None:
            flash(context['settings_error'], 'danger')
            return redirect(url_for('government_contributions'))
        data = context['data']
        subtitle = f"{context['month']:%B %Y} - {context['company'] or 'All companies'}"
    headers, body = gov_report_table(report, data, employee)
    buffer, mimetype, extension = GovReportsService.export(GOV_REPORTS[report], subtitle, headers, body, fmt)
    stamp = f"{employee.id}" if employee else f"{gov_month_from_request():%Y-%m}"
    return send_file(buffer, mimetype=mimetype, as_attachment=True,
                     download_name=f'{report}_{stamp}.{extension}')


def gov_setting_form_values(form):
    values, errors = {}, []
    try:
        values['effective_month'] = datetime.strptime(form.get('effective_month', ''), '%Y-%m').date()
    except ValueError:
        errors.append('Effective month is required (YYYY-MM).')
    numeric = {
        'philhealth_rate': 'PhilHealth rate (%)', 'philhealth_floor': 'PhilHealth floor',
        'philhealth_ceiling': 'PhilHealth ceiling', 'pagibig_low_threshold': 'Pag-IBIG low-income threshold',
        'pagibig_employee_rate_low': 'Pag-IBIG EE rate at/below threshold (%)',
        'pagibig_employee_rate_high': 'Pag-IBIG EE rate above threshold (%)',
        'pagibig_employer_rate': 'Pag-IBIG ER rate (%)', 'pagibig_max_fund_salary': 'Pag-IBIG maximum fund salary',
    }
    for key, label in numeric.items():
        raw = str(form.get(key, '')).replace(',', '').strip()
        try:
            amount = Decimal(raw)
            if not amount.is_finite():
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            errors.append(f'{label} must be a number.')
            continue
        values[key] = amount / 100 if key.endswith('_rate') or '_rate_' in key else amount
    try:
        values['default_workweek'] = int(form.get('default_workweek', 5))
    except ValueError:
        values['default_workweek'] = 0
    values['source_reference'] = str(form.get('source_reference', '')).strip()[:255]
    brackets, bracket_errors = parse_sss_table(form.get('sss_table', ''))
    errors.extend(bracket_errors)
    if not errors:
        errors.extend(validate_gov_setting(values, brackets))
    return values, brackets, errors


def gov_setting_snapshot(setting):
    values = gov_setting_values(setting)
    values['sss_table'] = format_sss_table(gov_bracket_values(setting.sss_brackets))
    return values


@app.route('/admin/government-contributions/settings', methods=['GET'])
@app.route('/admin/government-contributions/settings/<int:setting_id>', methods=['GET', 'POST'])
@app.route('/admin/government-contributions/settings/new', methods=['GET', 'POST'], endpoint='government_contribution_setting_new')
@login_required
def government_contribution_settings(setting_id=None):
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    settings = GovContributionSetting.query.order_by(GovContributionSetting.effective_month.desc()).all()
    is_new = request.endpoint == 'government_contribution_setting_new'
    setting = db.get_or_404(GovContributionSetting, setting_id) if setting_id else None
    if setting is None and not is_new and settings:
        setting = settings[0]
    errors = []
    form_values = None
    if request.method == 'POST':
        gov_require_csrf()
        values, brackets, errors = gov_setting_form_values(request.form)
        duplicate = (
            GovContributionSetting.query.filter(
                GovContributionSetting.effective_month == values.get('effective_month'),
                GovContributionSetting.id != (setting.id if setting else 0),
            ).first() if 'effective_month' in values else None
        )
        if duplicate is not None:
            errors.append('Another version already uses that effective month; edit that version instead.')
        if not errors:
            before = gov_setting_snapshot(setting) if setting else None
            if setting is None:
                setting = GovContributionSetting(created_by=current_user.id)
                db.session.add(setting)
            for key, value in values.items():
                setattr(setting, key, float(value) if isinstance(value, Decimal) else value)
            setting.updated_at = datetime.utcnow()
            setting.sss_brackets = [
                SSSContributionBracket(**{key: (float(value) if value is not None else None) for key, value in row.items()})
                for row in brackets
            ]
            db.session.flush()
            GovAuditTrailService.record(
                current_user, 'Created contribution settings' if before is None else 'Updated contribution settings',
                setting.id, {'before': before, 'after': gov_setting_snapshot(setting)},
            )
            db.session.commit()
            record_audit_action(f'Saved government contribution settings effective {setting.effective_month:%Y-%m}')
            flash(f'Contribution settings effective {setting.effective_month:%B %Y} saved.', 'success')
            return redirect(url_for('government_contribution_settings', setting_id=setting.id))
        form_values = dict(request.form)
    if form_values is None:
        source = gov_setting_snapshot(setting) if setting else gov_setting_snapshot(settings[0]) if settings else None
        form_values = {}
        if source:
            form_values = {
                key: (f'{value * 100:.4f}'.rstrip('0').rstrip('.') if key.endswith('_rate') or '_rate_' in key else value)
                for key, value in source.items()
            }
            form_values['effective_month'] = source['effective_month'].strftime('%Y-%m') if not is_new else ''
    audit = GovContributionAudit.query.order_by(GovContributionAudit.created_at.desc()).limit(30).all()
    return render_template(
        'government_contribution_settings.html', settings=settings, setting=setting, is_new=is_new,
        form=form_values, errors=errors, csrf_token=gov_csrf_token(), audit=audit,
    )


@app.route('/admin/monthly-deductions')
@login_required
def monthly_deductions():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    month_value = request.args.get('month')
    try:
        month_start = datetime.strptime(month_value, '%Y-%m').date() if month_value else datetime.today().date().replace(day=1)
    except (TypeError, ValueError):
        month_start = datetime.today().date().replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = db.session.query(Payroll, Employee).join(Employee).filter(
        Payroll.cutoff_start >= month_start,
        Payroll.cutoff_start < next_month,
        Payroll.is_paid.is_(True),
    ).order_by(Employee.company, Employee.last_name, Payroll.cutoff_start).all()

    monthly_by_employee = {}
    for record, employee in rows:
        monthly_row = monthly_by_employee.setdefault(employee.id, {
            'employee': employee,
            'cutoff_count': 0,
            'has_missing_contribution_basis': False,
            'gross_income': 0.0,
            'contribution_salary_base': 0.0,
            'sss': 0.0,
            'philhealth': 0.0,
            'pagibig': 0.0,
            'total_ee': 0.0,
            'employer_sss': 0.0,
            'employer_sss_ec': 0.0,
            'employer_philhealth': 0.0,
            'employer_pagibig': 0.0,
        })
        monthly_row['cutoff_count'] += 1
        monthly_row['has_missing_contribution_basis'] |= (
            record.contribution_salary_base is None
        )
        monthly_row['contribution_salary_base'] += float(
            record.contribution_salary_base or 0
        )
        monthly_row['gross_income'] += float(record.gross_income or 0)
        for key in (
            'sss', 'philhealth', 'pagibig', 'employer_sss',
            'employer_sss_ec', 'employer_philhealth', 'employer_pagibig',
        ):
            monthly_row[key] += float(getattr(record, key) or 0)
        monthly_row['total_ee'] += sum(
            float(getattr(record, key) or 0)
            for key in ('sss', 'philhealth', 'pagibig')
        )
    for monthly_row in monthly_by_employee.values():
        base = monthly_row['contribution_salary_base']
        has_basis = not monthly_row['has_missing_contribution_basis'] and base > 0
        monthly_row['sss_msc'] = (
            min(max(int((base + 250) // 500) * 500, 5000), 35000)
            if has_basis else 0.0
        )
        monthly_row['philhealth_base'] = (
            min(max(base, 10000), 100000) if has_basis else 0.0
        )
        monthly_row['pagibig_base'] = min(base, 10000) if has_basis else 0.0
    monthly_employee_rows = sorted(
        monthly_by_employee.values(),
        key=lambda row: (
            row['employee'].company or '',
            row['employee'].last_name or '',
            row['employee'].first_name or '',
        ),
    )

    employer_rows = []
    for record, employee in rows:
        employer_rows.append({
            'record': record,
            'employee': employee,
            'has_contribution_basis': record.contribution_salary_base is not None,
            'employer_sss': float(record.employer_sss or 0),
            'employer_sss_ec': float(record.employer_sss_ec or 0),
            'employer_philhealth': float(record.employer_philhealth or 0),
            'employer_pagibig': float(record.employer_pagibig or 0),
        })

    totals = {key: sum(float(getattr(record, key) or 0) for record, _ in rows) for key in (
        'sss', 'philhealth', 'pagibig', 'withholding_tax', 'loan', 'total_deductions'
    )}
    totals['has_missing_contribution_basis'] = any(
        not row['has_contribution_basis'] for row in employer_rows
    )
    totals['contribution_salary_base'] = sum(
        float(record.contribution_salary_base or 0) for record, _ in rows
    )
    totals.update({
        'employer_sss_ec': sum(row['employer_sss_ec'] for row in employer_rows),
        'employer_sss': sum(row['employer_sss'] for row in employer_rows),
        'employer_philhealth': sum(row['employer_philhealth'] for row in employer_rows),
        'employer_pagibig': sum(row['employer_pagibig'] for row in employer_rows),
    })
    totals.update({
        'portal_sss': totals['sss'] + totals['employer_sss'] + totals['employer_sss_ec'],
        'portal_philhealth': totals['philhealth'] + totals['employer_philhealth'],
        'portal_pagibig': totals['pagibig'] + totals['employer_pagibig'],
    })
    totals['portal_total'] = totals['portal_sss'] + totals['portal_philhealth'] + totals['portal_pagibig']
    if request.args.get('download') == 'true':
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=(792, 612))
        pdf.setFont('Helvetica-Bold', 15)
        pdf.drawString(40, 575, 'MONTHLY DEDUCTION REPORT')
        pdf.setFont('Helvetica', 10)
        pdf.drawString(40, 555, f'Employee contribution bases and deductions: {month_start.strftime("%B %Y")}')
        pdf.setFont('Helvetica-Bold', 8)
        employee_headers = (
            (35, 'Employee / Company'),
            (210, 'Gross incl OT'),
            (300, 'Contrib. Base'),
            (385, 'SSS MSC'),
            (435, 'SSS EE'),
            (485, 'PH Base'),
            (535, 'PH EE'),
            (590, 'HDMF Base'),
            (640, 'HDMF EE'),
            (690, 'ER Total'),
        )
        for x, header in employee_headers:
            pdf.drawString(x, 530, header)
        y = 512
        pdf.setFont('Helvetica', 7)
        for monthly_row in monthly_employee_rows:
            if y < 65:
                pdf.showPage()
                y = 570
                pdf.setFont('Helvetica-Bold', 8)
                for x, header in employee_headers:
                    pdf.drawString(x, y, header)
                y -= 18
                pdf.setFont('Helvetica', 7)
            monthly_employee = monthly_row['employee']
            pdf.drawString(
                35,
                y,
                f'{monthly_employee.full_name()} / {payroll_company_name(monthly_employee)}'[:32],
            )
            pdf.drawRightString(275, y, f'{monthly_row["gross_income"]:,.2f}')
            if monthly_row['has_missing_contribution_basis']:
                base_values = ('N/A', 'N/A', 'N/A', 'N/A')
            else:
                base_values = (
                    f'{monthly_row["contribution_salary_base"]:,.2f}',
                    f'{monthly_row["sss_msc"]:,.0f}',
                    f'{monthly_row["philhealth_base"]:,.2f}',
                    f'{monthly_row["pagibig_base"]:,.2f}',
                )
            for x, value in zip((375, 425, 525, 630), base_values):
                pdf.drawRightString(x, y, value)
            for x, key in ((475, 'sss'), (575, 'philhealth'), (680, 'pagibig')):
                pdf.drawRightString(x, y, f'{monthly_row[key]:,.2f}')
            employer_total = sum(
                monthly_row[key] for key in (
                    'employer_sss', 'employer_sss_ec',
                    'employer_philhealth', 'employer_pagibig',
                )
            )
            pdf.drawRightString(750, y, f'{employer_total:,.2f}')
            y -= 13
        pdf.showPage()
        pdf.setFont('Helvetica-Bold', 15)
        pdf.drawString(40, 575, 'MONTHLY DEDUCTION CUTOFF DETAILS')
        pdf.setFont('Helvetica', 10)
        pdf.drawString(40, 555, f'Period: {month_start.strftime("%B %Y")} - finalized weekly cutoffs')
        y = 525
        pdf.setFont('Helvetica-Bold', 9)
        pdf.drawString(40, y, 'Employee / Company')
        pdf.drawString(190, y, 'Cutoff Base')
        pdf.drawString(270, y, 'SSS EE')
        pdf.drawString(325, y, 'PhilHealth EE')
        pdf.drawString(395, y, 'Pag-IBIG EE')
        pdf.drawString(455, y, 'Tax')
        pdf.drawString(495, y, 'Loan')
        pdf.drawString(540, y, 'Total EE')
        pdf.drawString(625, y, 'Total ER')
        y -= 18
        pdf.setFont('Helvetica', 8)
        for record, employee in rows:
            if y < 90:
                pdf.showPage()
                y = 570
                pdf.setFont('Helvetica-Bold', 9)
                pdf.drawString(40, y, 'Employee / Company')
                pdf.drawString(190, y, 'Cutoff Base')
                pdf.drawString(270, y, 'SSS EE')
                pdf.drawString(325, y, 'PhilHealth EE')
                pdf.drawString(395, y, 'Pag-IBIG EE')
                pdf.drawString(455, y, 'Tax')
                pdf.drawString(495, y, 'Loan')
                pdf.drawString(540, y, 'Total EE')
                pdf.drawString(625, y, 'Total ER')
                y -= 18
                pdf.setFont('Helvetica', 8)
            pdf.drawString(40, y, f'{employee.full_name()} / {payroll_company_name(employee)}'[:28])
            base = (
                f'{record.contribution_salary_base:,.2f}'
                if record.contribution_salary_base is not None
                else 'N/A'
            )
            pdf.drawRightString(250, y, base)
            for x, key in [(270, 'sss'), (325, 'philhealth'), (395, 'pagibig'), (455, 'withholding_tax'), (495, 'loan'), (540, 'total_deductions')]:
                pdf.drawRightString(x + 38, y, f'{float(getattr(record, key) or 0):,.2f}')
            employer_total = (
                float(record.employer_sss or 0)
                + float(record.employer_sss_ec or 0)
                + float(record.employer_philhealth or 0)
                + float(record.employer_pagibig or 0)
            )
            pdf.drawRightString(
                675,
                y,
                f'{employer_total:,.2f}'
                if record.contribution_salary_base is not None
                else 'N/A',
            )
            y -= 15
        pdf.line(40, max(y, 75), 700, max(y, 75))
        pdf.setFont('Helvetica-Bold', 9)
        pdf.drawString(40, max(y - 15, 55), 'AUTHORIZED PERSON SIGNATURE: ____________________    DATE: __________')
        employer_y = max(y - 48, 35)
        pdf.setFont('Helvetica-Bold', 9)
        pdf.drawString(40, employer_y, 'EMPLOYER SHARE (ER) - PORTAL PAYMENT GUIDE')
        pdf.setFont('Helvetica', 8)
        pdf.drawString(40, employer_y - 14, f'SSS: PHP {totals["employer_sss"]:,.2f}')
        pdf.drawString(165, employer_y - 14, f'SSS EC: PHP {totals["employer_sss_ec"]:,.2f}')
        pdf.drawString(285, employer_y - 14, f'PhilHealth: PHP {totals["employer_philhealth"]:,.2f}')
        pdf.drawString(450, employer_y - 14, f'Pag-IBIG: PHP {totals["employer_pagibig"]:,.2f}')
        pdf.drawString(40, employer_y - 28, f'Portal total (EE + ER): PHP {totals["portal_total"]:,.2f}')
        salary_base = (
            'N/A'
            if totals['has_missing_contribution_basis']
            else f'PHP {totals["contribution_salary_base"]:,.2f}'
        )
        pdf.drawString(300, employer_y - 28, f'Monthly salary base: {salary_base}')
        if totals['has_missing_contribution_basis']:
            pdf.drawString(40, employer_y - 40, 'WARNING: N/A rows are older records; portal totals exclude unavailable employer shares.')
        pdf.showPage()
        pdf.save()
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f'monthly_deductions_{month_start:%Y_%m}.pdf', mimetype='application/pdf')
    return render_template(
        'monthly_deductions.html',
        rows=rows,
        employer_rows=employer_rows,
        monthly_employee_rows=monthly_employee_rows,
        totals=totals,
        month_start=month_start,
    )


@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().casefold()
        emp = Employee.query.filter(
            db.func.lower(Employee.email) == email
        ).first()

        if emp and emp.email:
            token = secrets.token_urlsafe(32)
            reset_token = PasswordResetToken(
                employee_id=emp.id,
                token=token,
                expires_at=datetime.utcnow() + timedelta(hours=1)
            )
            db.session.add(reset_token)
            db.session.commit()

            reset_url = url_for('reset_password', token=token, _external=True)
            email_sent = send_notification_email(
                [emp.email],
                "Password Reset Request",
                (
                    "Use this link to reset your HRIS password:\n"
                    f"{reset_url}\n\n"
                    "This link expires in 1 hour and can only be used once."
                ),
                html_body=render_template('emails/password_reset.html', reset_url=reset_url),
            )
            if not email_sent:
                db.session.delete(reset_token)
                db.session.commit()
                logger.warning(
                    "Password reset email delivery failed for employee id %s.",
                    emp.id,
                )

        flash("If that email is registered, a password reset link has been sent.", "info")
        return redirect(url_for('login'))

    return render_template('forgot_password.html')


@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    reset_token = PasswordResetToken.query.filter_by(token=token, used=False).first()
    if not reset_token or reset_token.expires_at < datetime.utcnow():
        flash("Invalid or expired reset link.", "danger")
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')

        if not has_strong_password(password):
            flash("Password must have at least 8 characters, including uppercase, lowercase, a number, and a special character.", "danger")
            return render_template('reset_password.html', token=token)
        if password != password_confirm:
            flash("Passwords do not match.", "danger")
            return render_template('reset_password.html', token=token)

        reset_token.employee.set_password(password)
        reset_token.used = True
        db.session.commit()
        flash("Your password has been reset successfully. You can now log in using your new password.", "success")
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)

# ------------------ GLOBAL AUTH CHECK ------------------
@app.before_request
def check_authentication():
    exempt_routes = ['home', 'login', 'register', 'static', 'service_worker', 'forgot_password', 'reset_password', 'verify_document', 'verify_payslip']
    if not current_user.is_authenticated and request.endpoint not in exempt_routes:
        return redirect(url_for('login'))


# ------------------ REGISTER EMP ADMIN ------------------
from werkzeug.security import generate_password_hash

@app.route('/register', methods=['GET','POST'])
def register():
    job_descriptions = JobDescriptionOption.query.order_by(JobDescriptionOption.name.asc()).all()
    if request.method == 'POST':
        required_fields = {
            'First name': request.form.get('first_name', '').strip(),
            'Last name': request.form.get('last_name', '').strip(),
            'Date of birth': request.form.get('dob', '').strip(),
            'Role': request.form.get('role', '').strip().lower(),
            'Job description': request.form.get('job_description', '').strip(),
            'Employment status': request.form.get('employment_status', '').strip(),
            'Company': request.form.get('company', '').strip(),
            'Email': request.form.get('email', '').strip(),
            'Password': request.form.get('password', ''),
            'Contact number': request.form.get('contact_no', '').strip(),
            'Date started': request.form.get('date_started', '').strip(),
            'Emergency person': request.form.get('emergency_person', '').strip(),
            'Emergency contact': request.form.get('emergency_contact', '').strip(),
        }
        missing_fields = [label for label, value in required_fields.items() if not value]
        if missing_fields:
            flash(f"Please complete all required fields: {', '.join(missing_fields)}.", 'danger')
            return redirect(url_for('register'))
        if len(required_fields['Job description']) > 150:
            flash('Job description must be 150 characters or fewer.', 'danger')
            return redirect(url_for('register'))
        if required_fields['Job description'].casefold() not in {
            option.name.casefold() for option in job_descriptions
        }:
            flash('Please select a job description from the available list.', 'danger')
            return redirect(url_for('register'))

        email = request.form.get('email', '').strip().lower()
        if '@' not in email or email.startswith('@') or email.endswith('@'):
            flash('Please enter a valid email address.', 'danger')
            return redirect(url_for('register'))
        if not has_strong_password(required_fields['Password']):
            flash('Password must have at least 8 characters, including uppercase, lowercase, a number, and a special character.', 'danger')
            return redirect(url_for('register'))
        if required_fields['Role'] not in {'staff', 'admin'}:
            flash('Please select a valid role.', 'danger')
            return redirect(url_for('register'))
        employment_status = next(
            (
                status
                for status in EMPLOYMENT_STATUSES
                if status.casefold() == required_fields['Employment status'].casefold()
            ),
            None,
        )
        if employment_status is None:
            flash('Please select a valid employment status.', 'danger')
            return redirect(url_for('register'))
        company_input = request.form.get('company', '').strip()
        company = {
            'Trece-Uno': 'Trece-Uno',
            'Trece-Uno Auto Supply': 'Trece-Uno',
            'Auto Expert': 'Auto Expert',
            'Auto-Expert Auto Supply': 'Auto Expert',
        }.get(company_input)
        if company is None:
            flash('Please select a valid company.', 'danger')
            return redirect(url_for('register'))

        file = request.files.get('profile_pic')
        filename = None
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        try:
            date_started_val = datetime.strptime(required_fields['Date started'], '%Y-%m-%d').date()
            dob_val = datetime.strptime(required_fields['Date of birth'], '%Y-%m-%d').date()
        except ValueError:
            flash('Please enter valid dates.', 'danger')
            return redirect(url_for('register'))
        if dob_val >= date.today() or date_started_val > date.today():
            flash('Please enter valid past dates for date of birth and date started.', 'danger')
            return redirect(url_for('register'))

        emp_address = request.form.get('address')
        emergency_address = request.form.get('emergency_address')
        if request.form.get('same_address'):
            emergency_address = emp_address

        emp = Employee(
            first_name=format_person_name(required_fields['First name']),
            middle_name=format_person_name(request.form.get('middle_name')) or None,
            last_name=format_person_name(required_fields['Last name']),
            suffix_name=format_suffix_name(request.form.get('suffix_name')) or None,
            dob=dob_val,
            role=required_fields['Role'],
            job_description=required_fields['Job description'],
            employment_status=employment_status,
            payroll_attendance_exempt=is_owner_admin_identity(
                required_fields['Role'], email
            ),
            company=company,
            email=email,
            password=generate_password_hash(required_fields['Password']),
            contact_no=required_fields['Contact number'],
            registered_at=datetime.utcnow(),
            date_started=date_started_val,
            sss=request.form.get('sss'),
            philhealth=request.form.get('philhealth'),
            tin=request.form.get('tin'),
            pagibig=request.form.get('pagibig'),
            emergency_person=required_fields['Emergency person'],
            emergency_contact=required_fields['Emergency contact'],
            emergency_address=emergency_address,
            profile_pic=filename,
            address=emp_address
        )
        
        try:
            db.session.add(emp)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception('Could not register employee with email %s', email)
            flash('Registration could not be completed. Please try again.', 'danger')
            return redirect(url_for('register'))
        flash("🎉 Congratulations! You are now registered.", "success")
        return redirect(url_for('login'))

    return render_template(
        "register.html",
        job_descriptions=job_descriptions,
        employment_statuses=EMPLOYMENT_STATUSES,
    )


@app.route('/admin/job-descriptions', methods=['GET', 'POST'])
@login_required
def manage_job_descriptions():
    if current_user.role.lower() != 'admin':
        abort(403)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name or len(name) > 150:
            flash('Enter a job description of 1 to 150 characters.', 'danger')
        elif any(
            option.name.casefold() == name.casefold()
            for option in JobDescriptionOption.query.all()
        ):
            flash('That job description is already available.', 'warning')
        else:
            db.session.add(JobDescriptionOption(name=name))
            db.session.commit()
            flash('Job description added to the dropdown.', 'success')
            return redirect(url_for('manage_job_descriptions'))

    options = JobDescriptionOption.query.order_by(JobDescriptionOption.name.asc()).all()
    return render_template('admin_job_descriptions.html', options=options)


# ------------------ PROFILE (SELF / ADMIN) ------------------
def attendance_scheduled_hours(employee, record_date):
    """Reporting schedule only; Sunday earnings remain in the rest-day OT calculation."""
    if record_date.weekday() == 6:
        return 4.0 if is_trece_sunday(employee, record_date) else 0.0
    return 8.0


def build_cutoff_attendance_rows(employee, cutoff_start, cutoff_end):
    history = Attendance.query.filter(
        Attendance.employee_id == employee.id,
        Attendance.date >= cutoff_start,
        Attendance.date < cutoff_end,
    ).order_by(Attendance.date.asc()).all()
    attendance_by_date = {log.date: log for log in history}
    rows = []
    for offset in range((cutoff_end - cutoff_start).days):
        record_date = cutoff_start + timedelta(days=offset)
        log = attendance_by_date.get(record_date)
        scheduled_hours = attendance_scheduled_hours(employee, record_date)
        is_rest_day = scheduled_hours == 0
        if log is None:
            rows.append({
                "date": record_date.strftime('%Y-%m-%d'),
                "clock_in": "N/A",
                "clock_out": "N/A",
                "status": "Rest Day" if is_rest_day else "Absent",
                "hours": 0,
                "undertime_hours": 0,
                "ot": "",
                "branch": "N/A",
            })
            continue
        hours_worked = (
            regular_attendance_hours(log)
            if log.clock_in and log.clock_out else 0.0
        )
        half_day = bool(scheduled_hours and hours_worked <= scheduled_hours / 2)
        attendance_status = (
            "No In / No Out" if not log.clock_in and not log.clock_out
            else "No In" if not log.clock_in
            else "No Out" if not log.clock_out
            else f"{log.status} / Half-day" if log.status == "Late" and half_day
            else "Half-day" if half_day
            else log.status
        )
        overtime_hours = payroll_overtime_hours(log)
        rows.append({
            "date": log.date.strftime('%Y-%m-%d'),
            "clock_in": log.clock_in.strftime('%H:%M:%S') if log.clock_in else "N/A",
            "clock_out": log.clock_out.strftime('%H:%M:%S') if log.clock_out else "N/A",
            "status": attendance_status,
            "hours": hours_worked,
            "undertime_hours": round(
                max(scheduled_hours - hours_worked, 0.0), 2
            ) if log.clock_in and log.clock_out else 0,
            "ot": (
                f"{attendance_overtime_status(log)} ({overtime_hours:.2f} h)"
                if overtime_hours > 0 else ""
            ),
            "branch": getattr(log, "company", "N/A"),
        })
    return rows


def monthly_attendance_summary(employee, month_start):
    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = build_cutoff_attendance_rows(employee, month_start, month_end)
    return {
        "present": sum(row["status"] == "Present" for row in rows),
        "late": sum("Late" in row["status"] for row in rows),
        "half_day": sum("Half-day" in row["status"] for row in rows),
        "absent": sum(row["status"] == "Absent" for row in rows),
        "incomplete": sum(row["status"].startswith("No ") for row in rows),
    }


@app.route('/profile/<int:user_id>', methods=['GET', 'POST'])
@login_required
def profile(user_id):
    emp = Employee.query.get_or_404(user_id)
    is_admin = 'admin' in current_user.role.lower()
    if current_user.id != emp.id and not is_admin:
        flash("❌ You can only view your own profile.", "danger")
        return redirect(url_for('dashboard_staff'))

    # --- Update only if self or admin ---
    if request.method == 'POST':
        if current_user.id != emp.id and not is_admin:
            flash("❌ You cannot edit another user's profile unless you're admin.", "danger")
            return redirect(url_for('profile', user_id=user_id))

        if 'resume_summary' in request.form:
            emp.resume_summary = request.form.get('resume_summary')
            emp.resume_education = request.form.get('resume_education')
            emp.resume_experience = request.form.get('resume_experience')
            emp.resume_skills = request.form.get('resume_skills')
            emp.resume_references = request.form.get('resume_references')

        if 'first_name' in request.form:
            emp.first_name = format_person_name(request.form.get('first_name'))
            emp.middle_name = format_person_name(request.form.get('middle_name')) or None
            emp.last_name = format_person_name(request.form.get('last_name'))
            emp.suffix_name = format_suffix_name(request.form.get('suffix_name')) or None
            emp.email = request.form.get('email', '').strip().lower() or None
            emp.contact_no = request.form.get('contact_no', '').strip() or None
            emp.address = request.form.get('address', '').strip() or None
            emp.sss = request.form.get('sss', '').strip() or None
            emp.philhealth = request.form.get('philhealth', '').strip() or None
            emp.tin = request.form.get('tin', '').strip() or None
            emp.pagibig = request.form.get('pagibig', '').strip() or None
            emp.emergency_person = request.form.get('emergency_person', '').strip() or None
            emp.emergency_contact = request.form.get('emergency_contact', '').strip() or None
            emp.emergency_address = request.form.get('emergency_address', '').strip() or None

            for form_name, attribute in (
                ('dob', 'dob'),
                ('date_started', 'date_started'),
            ):
                value = request.form.get(form_name, '')
                if value:
                    try:
                        setattr(emp, attribute, datetime.strptime(value, '%Y-%m-%d').date())
                    except ValueError:
                        flash(f'Please enter a valid {form_name.replace("_", " ")}.', 'danger')
                        return redirect(url_for('profile', user_id=user_id))

            job_description = request.form.get('job_description', '').strip()
            if job_description:
                valid_descriptions = {
                    option.name.casefold(): option.name
                    for option in JobDescriptionOption.query.all()
                }
                if emp.job_description:
                    valid_descriptions.setdefault(emp.job_description.casefold(), emp.job_description)
                if job_description.casefold() not in valid_descriptions:
                    flash('Please choose an available job description.', 'danger')
                    return redirect(url_for('profile', user_id=user_id))
                emp.job_description = valid_descriptions[job_description.casefold()]
            else:
                emp.job_description = None

            employment_status = request.form.get('employment_status', '').strip()
            valid_statuses = {status.casefold(): status for status in EMPLOYMENT_STATUSES}
            if employment_status.casefold() not in valid_statuses:
                flash('Please choose a valid employment status.', 'danger')
                return redirect(url_for('profile', user_id=user_id))
            emp.employment_status = valid_statuses[employment_status.casefold()]

            if is_admin:
                emp.gender = request.form.get('gender', '').strip() or None
                emp.civil_status = request.form.get('civil_status', '').strip() or None
                emp.immediate_supervisor = request.form.get('immediate_supervisor', '').strip() or None
                emp.work_location = request.form.get('work_location', '').strip() or None
                if 'contribution_workweek' in request.form:
                    workweek = request.form.get('contribution_workweek', '').strip()
                    emp.contribution_workweek = int(workweek) if workweek in ('5', '6') else None
                if 'tax_status' in request.form:
                    emp.tax_status = request.form.get('tax_status', '').strip()[:30] or None
                emp.bank_name = request.form.get('bank_name', '').strip() or None
                emp.bank_account_name = request.form.get('bank_account_name', '').strip() or None
                emp.bank_account_number = request.form.get('bank_account_number', '').strip() or None
                emp.biometric_id = request.form.get('biometric_id', '').strip() or None
                emp.payroll_preparation_access = request.form.get('payroll_preparation_access') == '1'

                role = request.form.get('role', emp.role or '').strip().lower()
                if role not in {'staff', 'admin'}:
                    flash('Please choose a valid system role.', 'danger')
                    return redirect(url_for('profile', user_id=user_id))
                emp.role = role
                emp.payroll_attendance_exempt = (
                    role == 'admin'
                    and request.form.get('payroll_attendance_exempt') == '1'
                )
                emp.admin_payroll_access = (
                    role == 'staff'
                    and request.form.get('admin_payroll_access') == '1'
                )
                company_input = request.form.get('company', emp.company or '').strip()
                company = {
                    'Trece-Uno': 'Trece-Uno',
                    'Trece-Uno Auto Supply': 'Trece-Uno',
                    'Auto Expert': 'Auto Expert',
                    'Auto-Expert Auto Supply': 'Auto Expert',
                }.get(company_input)
                if company is None:
                    flash('Please choose a valid company.', 'danger')
                    return redirect(url_for('profile', user_id=user_id))
                emp.company = company

                for form_name, attribute in (
                    ('probation_end_date', 'probation_end_date'),
                    ('regularization_date', 'regularization_date'),
                ):
                    value = request.form.get(form_name, '')
                    try:
                        setattr(
                            emp,
                            attribute,
                            datetime.strptime(value, '%Y-%m-%d').date() if value else None,
                        )
                    except ValueError:
                        flash(f'Please enter a valid {form_name.replace("_", " ")}.', 'danger')
                        return redirect(url_for('profile', user_id=user_id))

            if 'profile_pic' in request.files:
                file = request.files['profile_pic']
                if file and file.filename != '':
                    filename = secure_filename(file.filename)
                    file.save(os.path.join(app.static_folder, 'uploads', filename))
                    emp.profile_pic = filename

        db.session.commit()
        flash("✅ Profile updated successfully!", "success")
        return redirect(url_for('profile', user_id=user_id))

    try:
        cutoff_start, cutoff_end = payroll_cutoff_from_request(request.args.get('cutoff_start'))
    except ValueError:
        flash('Select a Saturday cutoff start date.', 'danger')
        return redirect(url_for('profile', user_id=user_id))
    profile_attendance_records = build_cutoff_attendance_rows(emp, cutoff_start, cutoff_end)
    month_value = request.args.get('month')
    try:
        attendance_month = datetime.strptime(month_value, '%Y-%m').date() if month_value else datetime.today().date().replace(day=1)
    except ValueError:
        attendance_month = datetime.today().date().replace(day=1)
    profile_monthly_summary = monthly_attendance_summary(emp, attendance_month)

    return render_template(
        "profile.html",
        emp=emp,
        viewer=current_user,
        cutoff_start=cutoff_start,
        cutoff_end=cutoff_end - timedelta(days=1),
        profile_attendance_records=profile_attendance_records,
        attendance_month=attendance_month,
        profile_monthly_summary=profile_monthly_summary,
        job_descriptions=JobDescriptionOption.query.order_by(JobDescriptionOption.name.asc()).all(),
        employment_statuses=EMPLOYMENT_STATUSES,
        bulletins=Bulletin.query.order_by(Bulletin.created_at.desc()).limit(10).all()
    )


def parse_ngteco_timecard(upload):
    lines = upload.stream.read().decode('utf-8-sig', errors='replace').splitlines()
    rows = []
    biometric_match = None
    employee_name = None
    for raw_line in lines:
        values = next(csv.reader([raw_line]))
        values = [value.strip() for value in values]
        if values and values[0] == 'Employee':
            employee_match = re.search(r'^(.*?)\s*\(([^)]+)\)\s*$', values[3] if len(values) > 3 else '')
            employee_name = employee_match.group(1).strip() if employee_match else None
            biometric_match = employee_match.group(2).strip() if employee_match else None
            continue
        if not biometric_match or len(values) < 5 or not re.fullmatch(r'\d{8}', values[1] if len(values) > 1 else ''):
            continue
        try:
            record_date = datetime.strptime(values[1], '%Y%m%d').date()
            clock_in = datetime.strptime(f'{values[1]} {values[2]}', '%Y%m%d %I:%M %p') if values[2] else None
            clock_out = datetime.strptime(f'{values[1]} {values[3]}', '%Y%m%d %I:%M %p') if values[3] else None
        except ValueError:
            continue
        rows.append({
            'biometric_id': biometric_match,
            'employee_name': employee_name,
            'date': record_date.isoformat(),
            'clock_in': clock_in.isoformat() if clock_in else None,
            'clock_out': clock_out.isoformat() if clock_out else None,
            'note': values[6] if len(values) > 6 else '',
        })
    return rows


def biometric_work_hours(clock_in, clock_out):
    if not clock_in or not clock_out:
        return None
    workday_start = datetime.combine(clock_in.date(), time(8, 0))
    lunch_start = datetime.combine(clock_in.date(), time(12, 0))
    lunch_end = datetime.combine(clock_in.date(), time(13, 0))
    morning_hours = max(
        min(clock_out, lunch_start) - max(clock_in, workday_start),
        timedelta(),
    )
    afternoon_hours = max(
        clock_out - max(clock_in, lunch_end),
        timedelta(),
    )
    return round(
        min((morning_hours + afternoon_hours).total_seconds() / 3600, 8.0),
        2,
    )


def can_prepare_payroll(user):
    return (
        'admin' in str(user.role or '').lower()
        or bool(getattr(user, 'payroll_preparation_access', False))
    )


def can_manage_admin_payroll(user):
    return 'admin' in str(user.role or '').lower()


def lock_attendance_employee(employee_id):
    """Keep attendance check-and-write operations in one employee transaction."""
    if db.session.get_bind().dialect.name == 'sqlite':
        # SQLite has no row locks; acquire its write lock before checking attendance.
        db.session.execute(
            text('UPDATE employees SET id = id WHERE id = :employee_id'),
            {'employee_id': employee_id},
        )
    else:
        db.session.execute(
            db.select(Employee.id).where(Employee.id == employee_id).with_for_update()
        ).scalar_one()


def can_review_payroll_loans(user):
    return (
        can_manage_admin_payroll(user)
        or bool(getattr(user, 'admin_payroll_access', False))
    )


@app.route('/admin/biometric-import', methods=['GET', 'POST'])
@login_required
def biometric_import():
    if not can_prepare_payroll(current_user):
        return 'Access denied', 403
    preview_rows = session.get('biometric_import_rows', [])
    if request.method == 'POST' and request.form.get('action') == 'preview':
        upload = request.files.get('biometric_file')
        if not upload or not upload.filename.lower().endswith('.csv'):
            flash('Upload an NGTeco CSV timecard report.', 'danger')
            return redirect(url_for('biometric_import'))
        parsed_rows = parse_ngteco_timecard(upload)
        if not parsed_rows:
            flash('No NGTeco attendance rows were found in the uploaded file.', 'danger')
            return redirect(url_for('biometric_import'))
        biometric_ids = {row['biometric_id'] for row in parsed_rows}
        employees = Employee.query.filter(
            Employee.biometric_id.in_(biometric_ids),
        ).all()
        employees_by_biometric_id = {employee.biometric_id: employee for employee in employees}
        seen_employee_dates = set()
        for row in parsed_rows:
            employee = employees_by_biometric_id.get(row['biometric_id'])
            row['employee_id'] = employee.id if employee else None
            row['employee'] = employee.full_name() if employee else 'Unmatched biometric ID'
            employee_date = (
                (employee.id, date.fromisoformat(row['date']))
                if employee else None
            )
            if employee is None:
                row['status'] = 'Unmatched'
            elif (
                employee_date in seen_employee_dates
                or Attendance.query.filter_by(
                    employee_id=employee.id,
                    date=employee_date[1],
                ).first()
            ):
                row['status'] = 'Duplicate'
            else:
                row['status'] = 'Ready'
            if employee_date:
                seen_employee_dates.add(employee_date)
        session['biometric_import_rows'] = parsed_rows
        preview_rows = parsed_rows
    elif request.method == 'POST' and request.form.get('action') == 'import':
        imported = 0
        duplicates_skipped = 0
        imported_employee_dates = set()
        for row in sorted(
            preview_rows, key=lambda row: (row['employee_id'] or 0, row['date'])
        ):
            if row['status'] != 'Ready':
                continue
            lock_attendance_employee(row['employee_id'])
            employee_date = (
                row['employee_id'],
                date.fromisoformat(row['date']),
            )
            if (
                employee_date in imported_employee_dates
                or Attendance.query.filter_by(
                    employee_id=employee_date[0],
                    date=employee_date[1],
                ).first()
            ):
                duplicates_skipped += 1
                continue
            clock_in = datetime.fromisoformat(row['clock_in']) if row['clock_in'] else None
            clock_out = datetime.fromisoformat(row['clock_out']) if row['clock_out'] else None
            attendance = Attendance(
                employee_id=row['employee_id'],
                date=date.fromisoformat(row['date']),
                clock_in=clock_in,
                clock_out=clock_out,
                status=attendance_status(clock_in) if clock_in else 'No In / No Out',
                hours=biometric_work_hours(clock_in, clock_out),
                company='Auto Expert',
            )
            attendance.hours = regular_attendance_hours(attendance)
            if clock_out:
                apply_overtime_details(attendance)
            db.session.add(attendance)
            imported_employee_dates.add(employee_date)
            imported += 1
        db.session.commit()
        session.pop('biometric_import_rows', None)
        flash(
            f'{imported} biometric attendance record(s) imported. '
            f'{duplicates_skipped} duplicate row(s) skipped. '
            'Unmatched and duplicate rows were skipped.',
            'success',
        )
        return redirect(url_for('biometric_import'))
    return render_template('biometric_import.html', preview_rows=preview_rows)


@app.route('/profile/<int:user_id>/resume')
@login_required
def profile_resume(user_id):
    employee = Employee.query.get_or_404(user_id)
    if current_user.id != employee.id and 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    return render_template('profile_resume.html', employee=employee)


def admin_staff_choices():
    return Employee.query.order_by(
        Employee.company, Employee.first_name, Employee.last_name
    ).all()


EMPLOYEE_201_CHECKLIST = {
    'Personal Information': ['Resume / Bio Data', 'Application Form', 'Government IDs (SSS, PhilHealth, Pag-IBIG, TIN)'],
    'Employment Records': ['Appointment Letter / Employment Contract', 'Job Description', 'Probationary Evaluation & Confirmation of Regularization'],
    'HR & Disciplinary Documents': ['Company Policies acknowledgment form', 'Notice to Explain (NTE)', 'Notice of Decision / Disciplinary Actions', 'Clearance forms (if applicable)'],
    'Attendance & Payroll': ['Daily Time Record (DTR) / Bundy Cards', 'Leave Applications & Approvals', 'Overtime Forms', 'Payslips / Payroll records'],
    'Government Compliance': ['SSS Form R1A / Employment Report', 'PhilHealth & Pag-IBIG Membership forms', 'BIR Form 1902 / 2316 (tax records)'],
    'Performance & Recognition': ['Performance Appraisals / Evaluations', 'Certificates of Training / Seminars', 'Commendations / Awards'],
}


def employee_201_checklist(documents):
    uploaded_types = {document.document_type for document in documents}
    return [
        {'category': category, 'items': [{'name': name, 'uploaded': name in uploaded_types} for name in items]}
        for category, items in EMPLOYEE_201_CHECKLIST.items()
    ]


@app.route('/admin/employee_201')
@login_required
def employee_201_selector():
    if 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    return render_template('employee_201_selector.html', employees=admin_staff_choices())


@app.route('/admin/employee_201/<int:employee_id>')
@login_required
def employee_201_pdf(employee_id):
    if 'admin' not in current_user.role.lower():
        return 'Access denied', 403

    employee = Employee.query.get_or_404(employee_id)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    page_width, page_height = letter
    y = page_height - 54

    def write_line(text, bold=False, size=10, indent=72, gap=16):
        nonlocal y
        if y < 54:
            pdf.showPage()
            y = page_height - 54
        pdf.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        pdf.drawString(indent, y, str(text)[:115])
        y -= gap

    write_line(payroll_company_name(employee), bold=True, size=16, gap=24)
    write_line('EMPLOYEE 201 FILE', bold=True, size=14, gap=24)
    write_line(f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}', size=9)
    write_line('PERSONAL INFORMATION', bold=True, size=12, gap=20)
    write_line(f'Name: {employee.first_name} {employee.last_name}')
    write_line(f'Employee ID: {employee.id}')
    write_line(f'System Role: {employee.role or "N/A"}')
    write_line(f'Job Description: {employee.job_description or "N/A"}')
    write_line(f'Company: {employee.company or "N/A"}')
    write_line(f'Email: {employee.email or "N/A"}')
    write_line(f'Contact: {employee.contact_no or "N/A"}')
    write_line(f'Address: {employee.address or "N/A"}')
    write_line(f'Date Started: {employee.date_started or "N/A"}')
    write_line('RESUME / BIO DATA', bold=True, size=12, gap=20)
    write_line(f'Professional Summary: {employee.resume_summary or "N/A"}')
    write_line(f'Education: {employee.resume_education or "N/A"}')
    write_line(f'Work Experience: {employee.resume_experience or "N/A"}')
    write_line(f'Skills: {employee.resume_skills or "N/A"}')
    write_line(f'References: {employee.resume_references or "N/A"}')
    write_line(f'SSS: {employee.sss or "N/A"}')
    write_line(f'PhilHealth: {employee.philhealth or "N/A"}')
    write_line(f'TIN: {employee.tin or "N/A"}')
    write_line(f'Pag-IBIG: {employee.pagibig or "N/A"}')
    write_line(f'Emergency Contact: {employee.emergency_contact or "N/A"}')
    write_line(f'Emergency Address: {employee.emergency_address or "N/A"}')

    write_line('201 DOCUMENT INDEX', bold=True, size=12, gap=20)
    for document in EmployeeDocument.query.filter_by(employee_id=employee.id).order_by(EmployeeDocument.phase, EmployeeDocument.uploaded_at.desc()).all():
        write_line(f'{document.phase.title()} | {document.document_type} | {document.original_filename} | Retention: {document.retention_years or "N/A"} years')

    write_line('ATTENDANCE RECORDS', bold=True, size=12, gap=20)
    for record in Attendance.query.filter_by(employee_id=employee.id).order_by(Attendance.date.desc()).limit(100).all():
        write_line(f'{record.date} | In: {record.clock_in or "N/A"} | Out: {record.clock_out or "N/A"} | Status: {record.status}')

    write_line('LEAVE REQUESTS', bold=True, size=12, gap=20)
    for leave_request in LeaveRequest.query.filter_by(employee_id=employee.id).order_by(LeaveRequest.date_filed.desc()).all():
        write_line(f'{leave_request.leave_type} | {leave_request.start_date} to {leave_request.end_date} | {leave_request.status}')

    write_line('LOAN REQUESTS', bold=True, size=12, gap=20)
    for loan_request in Loan.query.filter_by(employee_id=employee.id).order_by(Loan.date_filed.desc()).all():
        write_line(f'PHP {loan_request.amount:.2f} | {loan_request.date_needed} | {loan_request.status} | {loan_request.reason}')

    write_line('PAYROLL HISTORY', bold=True, size=12, gap=20)
    for payroll_record in Payroll.query.filter_by(employee_id=employee.id).order_by(Payroll.cutoff_start.desc()).all():
        write_line(f'{payroll_record.cutoff_start} to {payroll_record.cutoff_end} | Gross: PHP {payroll_record.gross_income or 0:.2f} | Net: PHP {payroll_record.net_pay or 0:.2f}')

    y = max(y - 24, 100)
    pdf.line(72, y, 260, y)
    pdf.line(340, y, 528, y)
    pdf.drawString(72, y - 15, 'Authorized Person Signature')
    pdf.drawString(340, y - 15, 'Date')
    verification = build_document_verification(
        'employee_201',
        employee.id,
        f'Employee201-{employee.id}-{datetime.now().strftime("%Y%m%d")}',
        cutoff_start=datetime.now().date().replace(day=1),
        cutoff_end=datetime.now().date(),
        net_pay=0.0,
    )
    pdf.setFont('Helvetica-Oblique', 8)
    pdf.drawString(420, 54, f'Document ID: {verification["document_id"]}')
    pdf.drawString(420, 44, 'Scan QR to verify.')
    if qrcode is not None:
        qr_bytes = generate_qr_image_bytes(verification['verify_url'])
        if qr_bytes:
            pdf.drawImage(ImageReader(io.BytesIO(qr_bytes)), 485, 10, width=60, height=60)
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f'employee_201_{employee.id}.pdf', mimetype='application/pdf')


@app.route('/admin/employee_201/<int:employee_id>/documents', methods=['GET', 'POST'])
@login_required
def employee_201_documents(employee_id):
    is_admin = 'admin' in current_user.role.lower()
    if not is_admin and current_user.id != employee_id:
        return 'Access denied', 403
    employee = Employee.query.get_or_404(employee_id)
    if request.method == 'POST':
        if not is_admin:
            return 'Access denied', 403
        upload = request.files.get('document')
        phase = request.form.get('phase', '').strip().lower()
        document_type = request.form.get('document_type', '').strip()
        retention = request.form.get('retention_years', type=int)
        if phase not in {'pre-employment', 'employment', 'separation'} or not document_type or not upload or not upload.filename:
            flash('❌ Phase, document type, and file are required.', 'danger')
            return redirect(url_for('employee_201_documents', employee_id=employee_id))
        safe_name = secure_filename(upload.filename)
        if not safe_name:
            flash('❌ Invalid filename.', 'danger')
            return redirect(url_for('employee_201_documents', employee_id=employee_id))
        folder = os.path.join(app.config['UPLOAD_FOLDER'], 'employee_201', str(employee_id))
        os.makedirs(folder, exist_ok=True)
        stored_name = f'{datetime.now().strftime("%Y%m%d%H%M%S%f")}_{safe_name}'
        upload.save(os.path.join(folder, stored_name))
        db.session.add(EmployeeDocument(
            employee_id=employee_id,
            phase=phase,
            document_type=document_type,
            original_filename=safe_name,
            stored_filename=stored_name,
            retention_years=retention
        ))
        db.session.commit()
        flash('✅ 201 document uploaded.', 'success')
        return redirect(url_for('employee_201_documents', employee_id=employee_id))

    documents = EmployeeDocument.query.filter_by(employee_id=employee_id).order_by(EmployeeDocument.uploaded_at.desc()).all()
    retention_guidance = {
        '201 file': '3 years',
        'payroll and wage records': '10 years',
        'sss contribution records': '30 years / indefinite',
        'philhealth and pag-ibig contributions': '10+ years',
        'hazardous medical records': '20 years'
    }
    return render_template(
        'employee_201_documents.html',
        employee=employee,
        documents=documents,
        checklist=employee_201_checklist(documents),
        employee_201_checklist_catalog=EMPLOYEE_201_CHECKLIST,
        retention_guidance=retention_guidance,
        can_manage_documents=is_admin
    )


LIABILITY_CATEGORIES = [
    'Uncollected Company Receivable',
    'Cash Shortage',
    'Damage to Company Property',
    'Unremitted Collection',
    'Authorized Other Deduction',
]


def active_employee_liabilities(employee_id):
    records = EmployeeLiability.query.filter(
        EmployeeLiability.employee_id == employee_id,
        EmployeeLiability.status.in_(['Active', 'Partially Recovered']),
        EmployeeLiability.acknowledgment_status == 'Acknowledged',
    ).order_by(EmployeeLiability.created_at.asc()).all()
    return [record for record in records if record.remaining_balance > 0]


def liability_cutoff_deduction(employee_id):
    total = 0.0
    for liability in active_employee_liabilities(employee_id):
        scheduled = float(liability.deduction_per_cutoff or 0)
        if scheduled <= 0:
            continue
        total += min(scheduled, liability.remaining_balance)
    return round(total, 2)


def apply_liability_deduction(employee_id, amount):
    remaining = max(float(amount or 0), 0.0)
    for liability in active_employee_liabilities(employee_id):
        if remaining <= 0:
            break
        applied = min(float(liability.deduction_per_cutoff or 0), liability.remaining_balance, remaining)
        if applied <= 0:
            continue
        liability.amount_deducted = float(liability.amount_deducted or 0) + applied
        remaining -= applied
        if liability.remaining_balance <= 0:
            liability.status = 'Fully Paid'


def reverse_liability_deduction(employee_id, amount):
    remaining = max(float(amount or 0), 0.0)
    records = EmployeeLiability.query.filter_by(employee_id=employee_id).order_by(EmployeeLiability.updated_at.desc()).all()
    for liability in records:
        if remaining <= 0:
            break
        reversed_amount = min(float(liability.amount_deducted or 0), remaining)
        if reversed_amount <= 0:
            continue
        liability.amount_deducted = max(float(liability.amount_deducted or 0) - reversed_amount, 0.0)
        remaining -= reversed_amount
        if liability.status == 'Fully Paid' and liability.remaining_balance > 0:
            liability.status = 'Active'



@app.route('/employee-liabilities', methods=['GET', 'POST'])
@login_required
def employee_liabilities():
    is_admin = 'admin' in str(current_user.role or '').lower()
    if request.method == 'POST':
        if not is_admin:
            return 'Access denied', 403
        action = request.form.get('action', 'create')
        try:
            if action == 'create':
                employee_id = request.form.get('employee_id', type=int)
                employee = Employee.query.get_or_404(employee_id)
                liability = EmployeeLiability(
                    employee_id=employee.id,
                    category=request.form.get('category') or 'Uncollected Company Receivable',
                    description=request.form.get('description', '').strip(),
                    customer_name=request.form.get('customer_name', '').strip() or None,
                    agreement_date=datetime.strptime(request.form.get('agreement_date'), '%Y-%m-%d').date() if request.form.get('agreement_date') else None,
                    total_amount=max(float(request.form.get('total_amount') or 0), 0.0),
                    deduction_per_cutoff=max(float(request.form.get('deduction_per_cutoff') or 0), 0.0),
                    acknowledgment_status='Pending',
                    reference=request.form.get('reference', '').strip() or None,
                    remarks=request.form.get('remarks', '').strip() or None,
                    created_by=current_user.id,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                if not liability.description or liability.total_amount <= 0:
                    flash('Description and positive total amount are required.', 'danger')
                    return redirect(url_for('employee_liabilities'))
                db.session.add(liability)
                flash('Employee liability record created.', 'success')
            else:
                liability = EmployeeLiability.query.get_or_404(request.form.get('liability_id', type=int))
                if action == 'recover':
                    liability.amount_recovered = float(liability.amount_recovered or 0) + max(float(request.form.get('amount_recovered') or 0), 0.0)
                    liability.status = 'Partially Recovered' if liability.remaining_balance > 0 else 'Fully Recovered'
                elif action == 'refund':
                    liability.amount_refunded = float(liability.amount_refunded or 0) + min(max(float(request.form.get('amount_refunded') or 0), 0.0), liability.refundable_balance)
                elif action == 'close':
                    liability.status = 'Closed'
                liability.remarks = request.form.get('remarks', liability.remarks) or liability.remarks
                liability.updated_at = datetime.utcnow()
            db.session.commit()
        except (TypeError, ValueError):
            db.session.rollback()
            flash('Enter valid liability amounts and dates.', 'danger')
        return redirect(url_for('employee_liabilities'))

    if is_admin:
        liabilities = EmployeeLiability.query.order_by(EmployeeLiability.created_at.desc()).all()
        employees = Employee.query.filter(Employee.role.ilike('%staff%')).order_by(Employee.last_name, Employee.first_name).all()
    else:
        liabilities = EmployeeLiability.query.filter_by(employee_id=current_user.id).order_by(EmployeeLiability.created_at.desc()).all()
        employees = []
    totals = {
        'original': sum(float(item.total_amount or 0) for item in liabilities),
        'deducted': sum(float(item.amount_deducted or 0) for item in liabilities),
        'recovered': sum(float(item.amount_recovered or 0) for item in liabilities),
        'refundable': sum(item.refundable_balance for item in liabilities),
        'remaining': sum(item.remaining_balance for item in liabilities),
    }
    return render_template(
        'employee_liabilities.html',
        liabilities=liabilities,
        employees=employees,
        categories=LIABILITY_CATEGORIES,
        totals=totals,
        is_admin=is_admin,
    )

@app.route('/admin/employee_document/<int:document_id>/download')
@login_required
def download_employee_document(document_id):
    document = EmployeeDocument.query.get_or_404(document_id)
    is_admin = 'admin' in current_user.role.lower()
    if not is_admin and current_user.id != document.employee_id:
        return 'Access denied', 403
    path = os.path.join(app.config['UPLOAD_FOLDER'], 'employee_201', str(document.employee_id), document.stored_filename)
    if not os.path.isfile(path):
        return 'Document not found', 404
    return send_file(path, as_attachment=True, download_name=document.original_filename)


@app.route('/admin/delete_employee/<int:employee_id>', methods=['POST'])
@login_required
def delete_employee(employee_id):
    if 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    if current_user.id == employee_id:
        flash('❌ You cannot delete your own admin account.', 'danger')
        return redirect(url_for('dashboard_admin'))
    if AttendanceReconciliation.query.filter(
        (AttendanceReconciliation.employee_id == employee_id)
        | (AttendanceReconciliation.performed_by == employee_id)
    ).first():
        flash(
            'This account has preserved attendance reconciliation history and cannot be deleted. '
            'Deactivate the account instead.',
            'danger',
        )
        return redirect(url_for('dashboard_admin'))

    try:
        employee = db.session.execute(
            db.select(Employee.id, Employee.first_name, Employee.last_name).where(Employee.id == employee_id)
        ).one_or_none()
        if employee is None:
            abort(404)

        inspector = inspect(db.engine)
        metadata = MetaData()
        for table_name in inspector.get_table_names():
            foreign_keys = inspector.get_foreign_keys(table_name)
            employee_columns = [
                column
                for foreign_key in foreign_keys
                if foreign_key.get("referred_table") == "employees"
                for column in foreign_key.get("constrained_columns", [])
            ]
            if not employee_columns:
                continue
            table = SQLAlchemyTable(table_name, metadata, autoload_with=db.engine)
            for column_name in employee_columns:
                db.session.execute(table.delete().where(table.c[column_name] == employee_id))

        db.session.execute(Employee.__table__.delete().where(Employee.id == employee_id))
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception('Could not delete employee %s because related records remain.', employee_id)
        flash('❌ Employee cannot be deleted because related records are still linked.', 'danger')
        return redirect(url_for('dashboard_admin'))
    flash(f'✅ Employee {employee.first_name} {employee.last_name} and related records were deleted.', 'success')
    return redirect(url_for('dashboard_admin'))

# ------------------ ATTENDANCE REPORT ------------------
import io, csv, uuid
from flask import Response, make_response, request, render_template
from datetime import datetime
from reportlab.lib.pagesizes import letter, landscape
from reportlab.pdfgen import canvas

try:
    import qrcode
except ImportError:  # pragma: no cover
    qrcode = None


@app.route('/profile/<int:employee_id>/download')
@login_required
def download_employee_profile(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    is_admin = 'admin' in current_user.role.lower()
    if not is_admin and current_user.id != employee.id:
        return 'Access denied', 403

    authorized_person = Employee.query.filter(
        Employee.role.ilike('%admin%')
    ).order_by(Employee.first_name, Employee.last_name).first()
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    page_width, page_height = letter
    y = page_height - 54

    def write_line(label, value, bold=False, gap=18):
        nonlocal y
        if y < 90:
            pdf.showPage()
            y = page_height - 54
        pdf.setFont('Helvetica-Bold' if bold else 'Helvetica', 10)
        pdf.drawString(54, y, f'{label}: {value or "N/A"}')
        y -= gap

    pdf.setTitle(f'Employee Information - {employee.full_name()}')
    pdf.setFont('Helvetica-Bold', 16)
    pdf.drawCentredString(page_width / 2, y, employee.company or 'Company')
    y -= 26
    pdf.setFont('Helvetica-Bold', 13)
    pdf.drawCentredString(page_width / 2, y, 'EMPLOYEE INFORMATION RECORD')
    y -= 34

    write_line('Employee Name', employee.full_name(), bold=True)
    write_line('Employee ID', employee.id)
    write_line('Company', employee.company)
    write_line('System Role', employee.role)
    write_line('Job Description', employee.job_description)
    write_line('Employment Status', employee.employment_status)
    write_line('Email', employee.email)
    write_line('Contact Number', employee.contact_no)
    write_line('Date of Birth', employee.dob)
    write_line('Address', employee.address)
    write_line('Date Started', employee.date_started)
    write_line('Probation End Date', employee.probation_end_date)
    write_line('Regularization Date', employee.regularization_date)
    y -= 8
    write_line('GOVERNMENT INFORMATION', '', bold=True, gap=20)
    write_line('SSS', employee.sss)
    write_line('PhilHealth', employee.philhealth)
    write_line('TIN', employee.tin)
    write_line('Pag-IBIG', employee.pagibig)
    y -= 8
    write_line('EMERGENCY CONTACT', '', bold=True, gap=20)
    write_line('Emergency Person', employee.emergency_person)
    write_line('Contact Number', employee.emergency_contact)
    write_line('Address', employee.emergency_address)

    y = max(y - 48, 100)
    pdf.line(page_width - 250, y, page_width - 70, y)
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawCentredString(page_width - 160, y - 15, authorized_person.full_name() if authorized_person else 'Authorized Representative')
    pdf.setFont('Helvetica', 9)
    pdf.drawCentredString(page_width - 160, y - 29, 'Authorized Signatory')
    verification = build_document_verification(
        'employee_profile',
        employee.id,
        f'Profile-{employee.id}',
        net_pay=0.0,
    )
    pdf.setFont('Helvetica-Oblique', 8)
    pdf.drawString(430, 38, f'Document ID: {verification["document_id"]}')
    pdf.drawString(430, 28, 'Scan QR to verify.')
    if qrcode is not None:
        qr_bytes = generate_qr_image_bytes(verification['verify_url'])
        if qr_bytes:
            pdf.drawImage(ImageReader(io.BytesIO(qr_bytes)), 505, 48, width=55, height=55)
    pdf.drawString(54, 44, f'Generated on {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    pdf.save()
    buffer.seek(0)
    filename = f'employee_information_{employee.id}.pdf'
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')

@app.route('/attendance/<int:employee_id>')
@login_required
def attendance(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    if current_user.id != emp.id and 'admin' not in current_user.role.lower():
        flash("❌ You can only view your own attendance record.", "danger")
        return redirect(url_for('dashboard_staff'))
    try:
        cutoff_start, cutoff_end = payroll_cutoff_from_request(request.args.get('cutoff_start'))
    except ValueError:
        flash('Select a Saturday cutoff start date.', 'danger')
        return redirect(url_for('attendance', employee_id=employee_id))
    history = Attendance.query.filter(
        Attendance.employee_id == employee_id,
        Attendance.date >= cutoff_start,
        Attendance.date < cutoff_end,
    ).order_by(Attendance.date.asc()).all()

    month_value = request.args.get("month")
    try:
        month_start = datetime.strptime(month_value, "%Y-%m").date() if month_value else datetime.today().date().replace(day=1)
    except ValueError:
        month_start = datetime.today().date().replace(day=1)
    monthly_summary = monthly_attendance_summary(emp, month_start)

    daily_date_value = request.args.get('attendance_date')
    try:
        selected_attendance_date = (
            datetime.strptime(daily_date_value, '%Y-%m-%d').date()
            if daily_date_value else None
        )
    except ValueError:
        selected_attendance_date = None
    daily_attendance_record = (
        build_cutoff_attendance_rows(
            emp,
            selected_attendance_date,
            selected_attendance_date + timedelta(days=1),
        )[0]
        if selected_attendance_date else None
    )

    # --- Summary metrics ---
    dtr_records = build_cutoff_attendance_rows(emp, cutoff_start, cutoff_end)
    total_present = sum(record["status"] == "Present" for record in dtr_records)
    total_late = sum("Late" in record["status"] for record in dtr_records)
    total_absent = sum(record["status"] == "Absent" for record in dtr_records)
    total_days = sum(record["status"] != "Rest Day" for record in dtr_records)

    total_hours = sum(
        regular_attendance_hours(log)
        for log in history if log.clock_in and log.clock_out
    )
    valid_days = sum(1 for log in history if log.clock_in and log.clock_out)
    avg_hours = total_hours / valid_days if valid_days else 0
    total_undertime_hours = 0.0
    for log in history:
        if not log.clock_out:
            continue
        scheduled_hours = attendance_scheduled_hours(emp, log.date)
        total_undertime_hours += max(
            scheduled_hours - regular_attendance_hours(log),
            0.0,
        )

    punctuality_score = (total_present / total_days) * 50 if total_days else 0
    attendance_score = ((total_present + total_late) / total_days) * 30 if total_days else 0
    productivity_score = (avg_hours / 8) * 20
    performance_score = round(punctuality_score + attendance_score + productivity_score, 2)

    if performance_score >= 85:
        motivation = "🌟 Excellent work! Keep maintaining your punctuality and consistency."
    elif performance_score >= 70:
        motivation = "👍 Good job! Try to reduce late arrivals to boost your score further."
    elif performance_score >= 50:
        motivation = "⚠️ Needs improvement. Focus on being on time and completing full shifts."
    else:
        motivation = "❌ Attendance is affecting performance. Let's work on discipline and consistency."

    # --- Date range ---
    date_range = f"{cutoff_start:%b %d, %Y} - {(cutoff_end - timedelta(days=1)):%b %d, %Y}"

    # --- DOWNLOAD HANDLING ---
    format = request.args.get("format")
    month = request.args.get("month")
    year = request.args.get("year")

    # PDF download
    if format == "pdf":
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=letter)

        # Header
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(180, 780, payroll_company_name(emp))
        pdf.setFont("Helvetica", 12)
        pdf.drawString(200, 740, "Official Attendance Report")

        # Employee Info
        pdf.setFont("Helvetica", 11)
        pdf.drawString(50, 730, f"Employee: {emp.first_name} {emp.last_name}")
        pdf.drawString(50, 715, f"Employee ID: {emp.id}")
        pdf.drawString(50, 700, f"Date Range: {date_range}")
        pdf.drawString(50, 685, f"Performance Score: {performance_score}/100")
        pdf.drawString(50, 670, f"AI Suggestion: {motivation}")

        # Attendance Table
        y = 640
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(50, y, "Date | Clock In | Clock Out | Status | Hours | Branch")
        y -= 20
        pdf.setFont("Helvetica", 10)
        for log in history:
            hours_worked = (
                regular_attendance_hours(log)
                if log.clock_in and log.clock_out else 0
            )
            line = f"{log.date.strftime('%Y-%m-%d')} | {log.clock_in.strftime('%H:%M:%S') if log.clock_in else 'N/A'} | {log.clock_out.strftime('%H:%M:%S') if log.clock_out else 'N/A'} | {log.status} | {hours_worked:.2f} | {getattr(log, 'company', 'N/A')}"
            pdf.drawString(50, y, line)
            y -= 20
            if y < 100:
                pdf.showPage()
                pdf.setFont("Helvetica", 10)
                y = 750

        # Signature
        pdf.setFont("Helvetica", 11)
        pdf.drawString(50, 80, "Certified True and Correct:")
        pdf.line(200, 60, 400, 60)
        pdf.drawString(200, 45, "Authorized Signature")
        pdf.line(50, 35, 220, 35)
        pdf.drawString(50, 20, "Authorized Person Signature")

        verification = build_document_verification(
            'attendance_report',
            emp.id,
            f'Attendance-{emp.id}-{date_range}',
            cutoff_start=history[-1].date if history else datetime.today().date(),
            cutoff_end=history[0].date if history else datetime.today().date(),
            net_pay=float(performance_score),
        )
        pdf.setFont('Helvetica-Oblique', 8)
        pdf.drawString(430, 100, f'Document ID: {verification["document_id"]}')
        pdf.drawString(430, 90, 'Scan QR to verify.')
        if qrcode is not None:
            qr_bytes = generate_qr_image_bytes(verification['verify_url'])
            if qr_bytes:
                pdf.drawImage(ImageReader(io.BytesIO(qr_bytes)), 470, 18, width=70, height=70)

        pdf.save()
        output.seek(0)
        return Response(output.read(),
                        mimetype="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename=attendance_{emp.id}.pdf"})

    # CSV download
    elif format == "csv":
        filtered = history
        if month:
            filtered = [log for log in filtered if log.date.month == int(month)]
        if year:
            filtered = [log for log in filtered if log.date.year == int(year)]

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Date","Clock In","Clock Out","Status","Hours","Branch"])
        for log in filtered:
            hours_worked = (
                regular_attendance_hours(log)
                if log.clock_in and log.clock_out else 0
            )
            writer.writerow([log.date, log.clock_in, log.clock_out, log.status, f"{hours_worked:.2f}", getattr(log, "company", "N/A")])

        response = make_response(output.getvalue())
        response.headers["Content-Disposition"] = f"attachment; filename=attendance_{year or 'all'}_{month or 'all'}.csv"
        response.headers["Content-Type"] = "text/csv"
        return response

    # --- DEFAULT HTML PAGE ---
    return render_template("attendance.html",
                           emp=emp,
                           dtr_records=dtr_records,
                           total_present=total_present,
                           total_late=total_late,
                           total_absent=total_absent,
                           avg_hours=avg_hours,
                           total_undertime_hours=round(total_undertime_hours, 2),
                           performance_score=performance_score,
                           motivation=motivation,
                           date_range=date_range,
                           cutoff_start=cutoff_start,
                           month_start=month_start,
                           monthly_summary=monthly_summary,
                           selected_attendance_date=selected_attendance_date,
                           daily_attendance_record=daily_attendance_record,
                           months=list(range(1,13)),
                           years=[datetime.today().year, datetime.today().year-1, datetime.today().year-2])


# ------------------ ADMIN DASHBOARD ------------------
def admin_cutoff_overview(cutoff_start, employee_ids=None, today=None):
    """Read cutoff activity without calculating or changing saved payroll."""
    today = today or datetime.now(ZoneInfo('Asia/Manila')).date()
    cutoff_end = cutoff_start + timedelta(days=6)
    employees = Employee.query.filter(Employee.role.ilike('%staff%'))
    if employee_ids is not None:
        employees = employees.filter(Employee.id.in_(employee_ids))
    employees = employees.order_by(Employee.company, Employee.last_name, Employee.first_name).all()
    ids = [employee.id for employee in employees]
    attendance = Attendance.query.filter(
        Attendance.employee_id.in_(ids), Attendance.date >= cutoff_start,
        Attendance.date <= min(cutoff_end, today),
    ).order_by(Attendance.date, Attendance.id).all()
    attendance_by_employee = {}
    for record in attendance:
        attendance_by_employee.setdefault(record.employee_id, {}).setdefault(record.date, []).append(record)
    payrolls = Payroll.query.filter(
        Payroll.employee_id.in_(ids),
        db.or_(
            db.and_(Payroll.cutoff_start == cutoff_start, Payroll.cutoff_end == cutoff_end),
            db.and_(Payroll.is_paid.is_(True), Payroll.cutoff_end < cutoff_start),
        ),
    ).order_by(Payroll.cutoff_start.desc(), Payroll.id.desc()).all()
    current_payrolls, previous_payrolls = {}, {}
    for record in payrolls:
        if record.cutoff_start == cutoff_start:
            current_payrolls.setdefault(record.employee_id, []).append(record)
        else:
            previous_payrolls.setdefault(record.employee_id, []).append(record)
    leaves = LeaveRequest.query.filter(
        LeaveRequest.employee_id.in_(ids), LeaveRequest.start_date <= cutoff_end,
        LeaveRequest.end_date >= cutoff_start,
    ).all()
    request_specs = (
        (Loan, Loan.date_needed.between(cutoff_start, cutoff_end), 'loans'),
        (OTApplication, OTApplication.ot_date.between(cutoff_start, cutoff_end), 'overtime'),
        (AttendanceCorrection, AttendanceCorrection.correction_date.between(cutoff_start, cutoff_end), 'corrections'),
        (LoanDeductionSkipRequest, LoanDeductionSkipRequest.cutoff_start == cutoff_start, 'skips'),
    )
    pending = {key: {} for key in ('loans', 'overtime', 'corrections', 'skips', 'leaves')}
    for model, condition, key in request_specs:
        for record in model.query.filter(
            model.employee_id.in_(ids), condition, model.status == 'Pending',
        ).all():
            pending[key][record.employee_id] = pending[key].get(record.employee_id, 0) + 1
    for leave in leaves:
        if leave.status == 'Pending':
            pending['leaves'][leave.employee_id] = pending['leaves'].get(leave.employee_id, 0) + 1
    rows = []
    totals = dict.fromkeys(('missing_out', 'absent', 'no_record', 'late', 'finalized', 'draft', 'not_prepared', 'attention', 'pending_requests'), 0)
    days = [cutoff_start + timedelta(days=offset) for offset in range(7)]
    for employee in employees:
        records_by_day = attendance_by_employee.get(employee.id, {})
        day_rows = []
        flags = set()
        counts = dict.fromkeys(('present', 'late', 'absent', 'missing_out', 'no_record', 'working', 'duplicates'), 0)
        for day in days:
            records = records_by_day.get(day, [])
            approved_leave = next((
                leave for leave in leaves if leave.employee_id == employee.id
                and leave.status == 'Approved' and leave.start_date <= day <= leave.end_date
            ), None)
            tone = 'neutral'
            if day > today:
                status = 'Upcoming'
            elif len(records) > 1:
                status, tone = 'Duplicate records', 'danger'
                counts['duplicates'] += 1
                flags.add('attendance')
            elif records:
                record = records[0]
                if record.status == 'Absent':
                    status, tone = 'Absent recorded', 'danger'
                    counts['absent'] += 1
                    flags.add('absent')
                elif record.clock_in and not record.clock_out:
                    if day == today:
                        status, tone = 'Clocked in / awaiting out', 'info'
                        counts['working'] += 1
                    else:
                        status, tone = 'Missing clock-out', 'danger'
                        counts['missing_out'] += 1
                        flags.add('missing-out')
                elif record.clock_out and not record.clock_in:
                    status, tone = 'Missing clock-in', 'danger'
                    flags.add('attendance')
                elif not record.clock_in and not record.clock_out:
                    status, tone = 'Incomplete record', 'warning'
                    flags.add('attendance')
                else:
                    status = record.status or 'Completed'
                    tone = 'warning' if status == 'Late' else 'success'
                    counts['late' if status == 'Late' else 'present'] += 1
                    if status == 'Late':
                        flags.add('late')
            elif approved_leave:
                status, tone = 'Approved leave', 'info'
            elif employee.payroll_attendance_exempt:
                status = 'Attendance exempt'
            elif attendance_scheduled_hours(employee, day) == 0:
                status = 'Rest day'
            elif day == today:
                status, tone = 'No clock-in yet', 'warning'
            else:
                status, tone = 'No record / verify', 'warning'
                counts['no_record'] += 1
                flags.add('no-record')
            day_rows.append({'date': day, 'status': status, 'tone': tone, 'records': records})
        current = current_payrolls.get(employee.id, [])
        previous = previous_payrolls.get(employee.id, [])
        last_payroll = previous[0] if previous else None
        previous_duplicate = bool(last_payroll and sum(
            record.cutoff_start == last_payroll.cutoff_start and record.cutoff_end == last_payroll.cutoff_end
            for record in previous
        ) > 1)
        payroll_state = (
            'Duplicate records' if len(current) > 1 else 'Finalized' if current and current[0].is_paid
            else 'Draft' if current else 'Not prepared'
        )
        if len(current) > 1 or previous_duplicate:
            flags.add('payroll')
        elif not employee.payroll_attendance_exempt and payroll_state != 'Finalized':
            flags.add('payroll')
        requests = {key: counts_by_id.get(employee.id, 0) for key, counts_by_id in pending.items()}
        if any(requests.values()):
            flags.add('requests')
        for key in ('missing_out', 'absent', 'no_record', 'late'):
            totals[key] += counts[key]
        if not employee.payroll_attendance_exempt:
            state_key = {'Finalized': 'finalized', 'Draft': 'draft', 'Not prepared': 'not_prepared'}.get(payroll_state)
            if state_key:
                totals[state_key] += 1
        totals['attention'] += bool(flags)
        totals['pending_requests'] += sum(requests.values())
        rows.append({
            'employee': employee, 'days': day_rows, 'counts': counts, 'flags': sorted(flags),
            'payroll_state': payroll_state, 'current_payroll': current[0] if len(current) == 1 else None,
            'previous_payroll': None if previous_duplicate else last_payroll,
            'previous_duplicate': previous_duplicate, 'requests': requests,
        })
    return {
        'start': cutoff_start, 'end': cutoff_end, 'today': today, 'rows': rows, 'totals': totals,
        'previous': cutoff_start - timedelta(days=7), 'next': cutoff_start + timedelta(days=7),
        'finalized_net': sum(
            float(row['current_payroll'].net_pay or 0) for row in rows
            if row['payroll_state'] == 'Finalized' and not row['employee'].payroll_attendance_exempt
        ),
    }


def dashboard_summary(employee=None, employee_ids=None):
    """Read saved records; dashboard viewing never recalculates or finalizes payroll."""
    today = datetime.now(ZoneInfo('Asia/Manila')).date()
    month_start = today.replace(day=1)
    attendance_query = Attendance.query.filter(Attendance.date >= month_start, Attendance.date <= today)
    payroll_query = Payroll.query
    leave_query = LeaveRequest.query
    loan_query = Loan.query
    evaluation_query = Evaluation.query
    if employee is not None:
        attendance_query = attendance_query.filter_by(employee_id=employee.id)
        payroll_query = payroll_query.filter_by(employee_id=employee.id)
        leave_query = leave_query.filter_by(employee_id=employee.id)
        loan_query = loan_query.filter_by(employee_id=employee.id)
        evaluation_query = evaluation_query.filter_by(employee_id=employee.id)
    else:
        attendance_query = attendance_query.join(Employee).filter(Employee.role.ilike('%staff%'))
    if employee_ids is not None:
        attendance_query = attendance_query.filter(Attendance.employee_id.in_(employee_ids))
        payroll_query = payroll_query.filter(Payroll.employee_id.in_(employee_ids))
        leave_query = leave_query.filter(LeaveRequest.employee_id.in_(employee_ids))
        loan_query = loan_query.filter(Loan.employee_id.in_(employee_ids))
        evaluation_query = evaluation_query.filter(Evaluation.employee_id.in_(employee_ids))
    finalized = payroll_query.filter(Payroll.is_paid.is_(True)).order_by(
        Payroll.cutoff_start.desc(), Payroll.id.desc()
    )
    latest = finalized.first()
    cutoff_records = payroll_query.filter(
        Payroll.cutoff_start == latest.cutoff_start,
        Payroll.cutoff_end == latest.cutoff_end,
    ).all() if latest else []
    attendance_rows = attendance_query.all()
    approved_ratings = [
        record.rating for record in evaluation_query.filter_by(approval_status='Approved').all()
        if record.rating is not None
    ]
    return {
        'month_start': month_start,
        'today': today,
        'attendance_count': len(attendance_rows),
        'present': sum(row.status == 'Present' for row in attendance_rows),
        'late': sum(row.status == 'Late' for row in attendance_rows),
        'absent': sum(row.status == 'Absent' for row in attendance_rows),
        'latest_payroll': latest,
        'finalized_count': sum(bool(row.is_paid) for row in cutoff_records),
        'draft_count': sum(not row.is_paid for row in cutoff_records),
        'net_pay': sum(float(row.net_pay or 0) for row in cutoff_records if row.is_paid),
        'pending_leaves': leave_query.filter_by(status='Pending').count(),
        'approved_leaves': leave_query.filter_by(status='Approved').count(),
        'pending_loans': loan_query.filter_by(status='Pending').count(),
        'loan_balance': float(employee.loan_balance or 0) if employee else sum(
            float(row.loan_balance or 0) for row in Employee.query.filter(
                Employee.role.ilike('%staff%'),
                Employee.id.in_(employee_ids) if employee_ids is not None else db.true(),
            ).all()
        ),
        'pending_evaluations': evaluation_query.filter_by(approval_status='Pending').count(),
        'approved_evaluations': len(approved_ratings),
        'average_rating': sum(approved_ratings) / len(approved_ratings) if approved_ratings else None,
    }


def admin_request_calendar(month_start, employee_ids=None, company=''):
    import calendar

    month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
    events = {}

    def add_event(day, label, endpoint, kind):
        events.setdefault(day, []).append({'label': label, 'url': url_for(endpoint), 'kind': kind})

    leaves = LeaveRequest.query.filter(
        LeaveRequest.employee_id.in_(employee_ids) if employee_ids is not None else db.true(),
        LeaveRequest.start_date <= month_end, LeaveRequest.end_date >= month_start
    ).order_by(LeaveRequest.start_date, LeaveRequest.id).all()
    for leave in leaves:
        day = max(leave.start_date, month_start)
        last = min(leave.end_date, month_end)
        while day <= last:
            add_event(day, f'{leave.employee.full_name()} - Leave ({leave.status})', 'leave', 'leave')
            day += timedelta(days=1)
    loans = Loan.query.filter(
        Loan.employee_id.in_(employee_ids) if employee_ids is not None else db.true(),
        Loan.date_needed >= month_start, Loan.date_needed <= month_end
    ).order_by(Loan.date_needed, Loan.id).all()
    for loan in loans:
        add_event(loan.date_needed, f'{loan.employee.full_name()} - Loan needed ({loan.status})', 'loan', 'loan')
    for day_number in range(1, month_end.day + 1):
        day = month_start.replace(day=day_number)
        if day.weekday() == 4:
            add_event(day, '10 PM - Payroll / approved OT review reminder', 'payroll_dashboard', 'deadline')
    add_event(month_end, 'Month-end - Staff quiz and peer evaluation deadline', 'evaluation_dashboard', 'deadline')
    if current_user.is_authenticated:
        for event in reminder_events(current_user, month_start, month_end):
            reminder = event['reminder']
            if company and reminder.company not in (company, 'All'):
                continue
            events.setdefault(event['at'].date(), []).append({
                'label': f"{event['at']:%I:%M %p} - {reminder.title} ({reminder.company})",
                'url': url_for('company_reminder_edit', reminder_id=reminder.id),
                'kind': 'reminder',
            })
    return {
        'month': month_start, 'weeks': calendar.Calendar().monthdatescalendar(month_start.year, month_start.month),
        'events': events,
        'previous': (month_start - timedelta(days=1)).strftime('%Y-%m'),
        'next': (month_end + timedelta(days=1)).strftime('%Y-%m'),
        'today': datetime.now(ZoneInfo('Asia/Manila')).date(),
    }


def calendar_reminder_csrf():
    return session.setdefault('calendar_reminder_csrf', secrets.token_urlsafe(32))


def check_calendar_reminder_csrf():
    if not secrets.compare_digest(
        calendar_reminder_csrf().encode(), request.form.get('csrf_token', '').encode()
    ):
        abort(400, description='Invalid reminder form. Reload the page and try again.')


def reminder_month():
    value = request.args.get('month') or datetime.now(MANILA).strftime('%Y-%m')
    month = datetime.strptime(value, '%Y-%m').date().replace(day=1)
    if not 1901 <= month.year <= 9998:
        raise ValueError('Calendar month out of range')
    return month


@app.route('/calendar/reminders')
@login_required
def company_reminders():
    import calendar
    try:
        month = reminder_month()
    except ValueError:
        flash('Select a valid calendar month between 1901 and 9998.', 'danger')
        return redirect(url_for('company_reminders'))
    end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    is_admin = 'admin' in str(current_user.role or '').lower()
    reminders = visible_reminders(current_user).order_by(CalendarReminder.scheduled_at, CalendarReminder.id).all()
    events = reminder_events(current_user, month, end)
    events_by_day = {}
    for event in events:
        events_by_day.setdefault(event['at'].date(), []).append(event)
    deliveries = CalendarReminderDelivery.query.order_by(
        CalendarReminderDelivery.attempted_at.desc(), CalendarReminderDelivery.id.desc()
    ).limit(100).all() if is_admin else []
    return render_template(
        'company_reminders.html', month=month, events=events_by_day,
        weeks=calendar.Calendar().monthdatescalendar(month.year, month.month),
        previous=(month - timedelta(days=1)).strftime('%Y-%m'),
        next_month=(end + timedelta(days=1)).strftime('%Y-%m'),
        reminders=reminders, deliveries=deliveries, is_admin=is_admin,
        csrf_token=calendar_reminder_csrf(), local_time=reminder_local_time,
        email_worker_enabled=scheduler is not None and scheduler.running
        and scheduler.get_job('company_calendar_reminders') is not None
        and calendar_reminder_email_configured(),
    )


@app.route('/calendar/reminders/new', methods=['GET', 'POST'])
@app.route('/calendar/reminders/<int:reminder_id>/edit', methods=['GET', 'POST'])
@login_required
def company_reminder_edit(reminder_id=None):
    if 'admin' not in str(current_user.role or '').lower():
        return 'Access denied', 403
    reminder = db.session.get(CalendarReminder, reminder_id) if reminder_id is not None else None
    if reminder_id is not None and reminder is None:
        abort(404)
    companies = ['Trece-Uno', 'Auto Expert']
    if request.method == 'POST':
        check_calendar_reminder_csrf()
        if reminder:
            reminder = _lock_reminder(reminder.id)
            if request.form.get('revision') != str(reminder.revision):
                db.session.rollback()
                flash('This reminder changed in another session. Reload and review before editing.', 'danger')
                return redirect(url_for('company_reminder_edit', reminder_id=reminder_id))
        try:
            title = request.form.get('title', '').strip()
            notes = request.form.get('notes', '').strip()
            company = request.form.get('company', '')
            recurrence = request.form.get('recurrence', '')
            local = datetime.strptime(request.form.get('scheduled_at', ''), '%Y-%m-%dT%H:%M')
            scheduled_at = local.replace(tzinfo=MANILA).astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
            if not title or len(title) > 150 or len(notes) > 5000 or '\n' in title or '\r' in title:
                raise ValueError('Enter a title (1–150 characters) and notes (up to 5000 characters).')
            if company not in companies or recurrence not in {'once', 'monthly'}:
                raise ValueError('Choose a valid company and recurrence.')
            if not 1901 <= local.year <= 9998:
                raise ValueError('Reminder year must be between 1901 and 9998.')
            if scheduled_at <= datetime.utcnow() and not (
                reminder and reminder.active and reminder.recurrence == recurrence == 'monthly'
                and scheduled_at == reminder.scheduled_at
            ):
                raise ValueError('Choose a future reminder time in Philippine time.')
            if reminder is None:
                reminder = CalendarReminder(created_by=current_user.id)
                db.session.add(reminder)
            else:
                reminder.revision += 1
            reminder.title, reminder.notes, reminder.company = title, notes, company
            reminder.scheduled_at, reminder.recurrence = scheduled_at, recurrence
            reminder.active = True
            reminder.updated_at = datetime.utcnow()
            db.session.add(AuditLog(
                employee_id=current_user.id, employee_name=current_user.full_name(),
                action='Created company reminder' if reminder_id is None else 'Edited company reminder',
                company=company, path=request.path, status_code=302,
            ))
            db.session.commit()
        except ValueError as error:
            db.session.rollback()
            flash(str(error), 'danger')
            return render_template('company_reminder_form.html', reminder=reminder,
                                   companies=companies, csrf_token=calendar_reminder_csrf(),
                                   local_time=reminder_local_time, values=request.form), 400
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception('Could not save company calendar reminder')
            flash('Reminder could not be saved. No changes were applied.', 'danger')
            return redirect(url_for('company_reminders'))
        flash('Reminder saved. Calendar updated; emails are scheduled, not sent immediately.', 'success')
        return redirect(url_for('company_reminders', month=local.strftime('%Y-%m')))
    return render_template(
        'company_reminder_form.html', reminder=reminder, companies=companies,
        csrf_token=calendar_reminder_csrf(), local_time=reminder_local_time, values={},
    )


@app.route('/calendar/reminders/<int:reminder_id>/cancel', methods=['POST'])
@login_required
def company_reminder_cancel(reminder_id):
    if 'admin' not in str(current_user.role or '').lower():
        return 'Access denied', 403
    check_calendar_reminder_csrf()
    try:
        reminder = _lock_reminder(reminder_id)
        if reminder is None:
            abort(404)
        if request.form.get('revision') != str(reminder.revision):
            db.session.rollback()
            flash('Reminder changed. Reload before cancelling.', 'danger')
            return redirect(url_for('company_reminders'))
        reminder.active = False
        reminder.revision += 1
        reminder.updated_at = datetime.utcnow()
        db.session.add(AuditLog(
            employee_id=current_user.id, employee_name=current_user.full_name(),
            action=f'Cancelled company reminder #{reminder.id}', company=reminder.company,
            path=request.path, status_code=302,
        ))
        db.session.commit()
        flash('Reminder cancelled. Already queued or accepted emails cannot be recalled.', 'success')
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception('Could not cancel company reminder %s', reminder_id)
        flash('Reminder could not be cancelled. Reload and try again.', 'danger')
    return redirect(url_for('company_reminders'))


@app.route('/dashboard_admin', methods=['GET','POST'])
@login_required
def dashboard_admin():
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    selected_company = request.args.get('company', '').strip()
    if selected_company not in ('', 'Auto Expert', 'Trece-Uno'):
        flash('Select a valid company.', 'danger')
        return redirect(url_for('dashboard_admin'))
    company_employees = Employee.query
    if selected_company == 'Trece-Uno':
        company_employees = company_employees.filter(Employee.company.in_(['Trece', 'Trece-Uno']))
    elif selected_company:
        company_employees = company_employees.filter_by(company=selected_company)
    employee_ids = [emp.id for emp in company_employees.all()] if selected_company else None
    try:
        overview_start, _ = payroll_cutoff_from_request(request.args.get('overview_cutoff'))
    except ValueError:
        flash('Select a Saturday for the dashboard cutoff.', 'danger')
        return redirect(url_for('dashboard_admin', company=selected_company))

    def scoped(query, model):
        return query.filter(model.employee_id.in_(employee_ids)) if employee_ids is not None else query

    calendar_month = datetime.now(ZoneInfo('Asia/Manila')).date().replace(day=1)
    if request.args.get('calendar_month'):
        try:
            calendar_month = datetime.strptime(request.args['calendar_month'], '%Y-%m').date()
            if not 1901 <= calendar_month.year <= 9998:
                raise ValueError('Calendar year is out of range')
        except ValueError:
            flash('Invalid calendar month. Choose a valid month between 1901 and 9998.', 'danger')
            return redirect(url_for('dashboard_admin'))

    # Profile update
    if request.method == 'POST' and 'first_name' in request.form:
        current_user.first_name = format_person_name(request.form['first_name'])
        current_user.last_name = format_person_name(request.form['last_name'])
        current_user.email = request.form['email']
        current_user.address = request.form.get('address')
        current_user.sss = request.form.get('sss')
        current_user.philhealth = request.form.get('philhealth')
        current_user.tin = request.form.get('tin')
        current_user.pagibig = request.form.get('pagibig')
        current_user.emergency_contact = request.form.get('emergency_contact')
        current_user.emergency_address = request.form.get('emergency_address')

        # Profile picture upload
        file = request.files.get('profile_pic')
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            current_user.profile_pic = filename

        # Password change validation
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        if new_password:
            if new_password == confirm_password:
                current_user.password = generate_password_hash(new_password)
                flash("🔑 Password updated successfully!", "success")
            else:
                flash("❌ Passwords do not match!", "danger")
                return redirect(url_for('dashboard_admin'))

        db.session.commit()
        flash("✅ Admin profile updated successfully!", "success")
        return redirect(url_for('dashboard_admin'))

    # Dashboard data
    trece_employees = Employee.query.filter(Employee.company.in_(["Trece-Uno", "Trece"])).all()
    auto_employees = Employee.query.filter_by(company="Auto Expert").all()
    if selected_company == 'Auto Expert':
        trece_employees = []
    elif selected_company == 'Trece-Uno':
        auto_employees = []
    trece_leaves = LeaveRequest.query.join(Employee).filter(Employee.company.in_(["Trece-Uno", "Trece"])).all()
    auto_leaves = LeaveRequest.query.join(Employee).filter(Employee.company=="Auto Expert").all()
    unread_count = Bulletin.query.count()
    total_employees = Employee.query.filter(
        Employee.id.in_(employee_ids) if employee_ids is not None else db.true(),
        Employee.role.ilike('%staff%'),
        Employee.first_name.isnot(None),
        Employee.first_name != '',
        Employee.last_name.isnot(None),
        Employee.last_name != ''
    ).count()

    pending_ot = scoped(OTApplication.query, OTApplication).filter_by(status="Pending").count()
    pending_leaves = scoped(LeaveRequest.query, LeaveRequest).filter_by(status="Pending").count()
    pending_loans = scoped(Loan.query, Loan).filter_by(status="Pending").count()
    pending_evaluations = scoped(Evaluation.query, Evaluation).filter_by(approval_status='Pending').count()
    pending_leaves_list = scoped(LeaveRequest.query, LeaveRequest).filter_by(status="Pending").order_by(LeaveRequest.date_filed.desc()).all()
    pending_loans_list = scoped(Loan.query, Loan).filter_by(status="Pending").order_by(Loan.date_filed.desc()).all()
    bulletins = Bulletin.query.order_by(Bulletin.created_at.desc()).all()
    attendance_events = AuditLog.query.filter(
        AuditLog.employee_id.in_(employee_ids) if employee_ids is not None else db.true(),
        AuditLog.action.in_(["Clock in", "Clock out"])
    ).order_by(AuditLog.created_at.desc()).limit(10).all()
    today_date = datetime.now(ZoneInfo('Asia/Manila')).date()
    staff_attendance = []
    for employee in company_employees.filter(Employee.role.ilike('%staff%')).order_by(
        Employee.company, Employee.last_name, Employee.first_name
    ).all():
        today_log = Attendance.query.filter_by(
            employee_id=employee.id,
            date=today_date
        ).order_by(Attendance.clock_in.desc()).first()
        staff_attendance.append({
            'employee': employee,
            'record': today_log,
            'status': (
                today_log.status if today_log and today_log.status
                else 'Clocked In' if today_log and today_log.clock_in and not today_log.clock_out
                else 'Completed' if today_log and today_log.clock_out
                else 'No Record'
            )
        })
    staff_attendance_by_employee = {
        item['employee'].id: item for item in staff_attendance
    }
    pending_attendance_corrections = scoped(AttendanceCorrection.query, AttendanceCorrection).filter_by(
        status='Pending'
    ).order_by(AttendanceCorrection.created_at.asc()).all()

    from sqlalchemy import func
    trend_records = (
        db.session.query(
            Payroll.cutoff_start,
            Payroll.cutoff_end,
            func.sum(Payroll.net_pay).label("total_payroll")
        )
        .filter(Payroll.employee_id.in_(employee_ids) if employee_ids is not None else db.true())
        .group_by(Payroll.cutoff_start, Payroll.cutoff_end)
        .order_by(Payroll.cutoff_start.desc())
        .limit(6)
        .all()
    )

    trend_labels = [
        f"{r.cutoff_start.strftime('%b %d')} - {r.cutoff_end.strftime('%b %d')}"
        for r in trend_records
    ][::-1]
    trend_values = [r.total_payroll for r in trend_records][::-1]

    return render_template(
        "dashboard_admin.html",
        trece_employees=trece_employees,
        auto_employees=auto_employees,
        trece_leaves=trece_leaves,
        auto_leaves=auto_leaves,
        unread_count=unread_count,
        total_employees=total_employees,
        pending_ot=pending_ot,
        pending_leaves=pending_leaves,
        pending_loans=pending_loans,
        pending_evaluations=pending_evaluations,
        pending_leaves_list=pending_leaves_list,
        pending_loans_list=pending_loans_list,
        bulletins=bulletins,
        attendance_events=attendance_events,
        staff_attendance=staff_attendance,
        staff_attendance_by_employee=staff_attendance_by_employee,
        pending_attendance_corrections=pending_attendance_corrections,
        trend_labels=trend_labels,
        trend_values=trend_values,
        admin=current_user,
        selected_company=selected_company,
        cutoff_overview=admin_cutoff_overview(overview_start, employee_ids=employee_ids),
        summary=dashboard_summary(employee_ids=employee_ids),
        request_calendar=admin_request_calendar(calendar_month, employee_ids=employee_ids, company=selected_company)
    )


# ------------------ STAFF DASHBOARD ------------------
def generate_ai_insights(emp):
    """Build explainable employee insights from attendance and payroll records."""
    attendance_logs = Attendance.query.filter_by(employee_id=emp.id).all()
    completed_logs = [log for log in attendance_logs if log.clock_in and log.clock_out]
    present_count = sum(1 for log in attendance_logs if log.status == "Present")
    late_count = sum(1 for log in attendance_logs if log.status == "Late")
    absent_count = sum(1 for log in attendance_logs if log.status == "Absent")
    total_hours = sum(float(log.hours or 0) for log in completed_logs)
    payroll_record = Payroll.query.filter_by(employee_id=emp.id).order_by(Payroll.cutoff_start.desc()).first()

    insights = {
        "attendance": (
            f"Perfect attendance across {present_count} present day(s)."
            if attendance_logs and absent_count == 0 and late_count == 0
            else f"Notice: {late_count} late day(s) and {absent_count} absent day(s) recorded."
        ),
        "work_hours": (
            f"Complete: {total_hours:.2f} worked hour(s) recorded."
            if completed_logs else
            "Notice: No completed work hours are recorded yet."
        ),
        "payroll": (
            f"Strong payroll record: latest net pay is ₱{float(payroll_record.net_pay or 0):,.2f}."
            if payroll_record and payroll_record.net_pay is not None else
            "Warning: No payroll record is available yet."
        )
    }
    return insights


@app.route('/dashboard_staff', methods=["GET", "POST"])
@login_required
def dashboard_staff():
    # ✅ Access control: staff only
    if "staff" not in current_user.role.lower():
        flash("❌ Access denied. Staff only.", "danger")
        return redirect(url_for('dashboard_admin'))  # or ibang page, wag balik sa login

    # ✅ Handle clock in/out
    if request.method == "POST":
        if "clockin" in request.form:
            return attendance_action(current_user.id)
        elif "clockout" in request.form:
            return attendance_action(current_user.id)

    manila_now = datetime.now(ZoneInfo('Asia/Manila')).replace(tzinfo=None)
    today = manila_now.date()
    today_log = Attendance.query.filter_by(
        employee_id=current_user.id,
        date=today
    ).order_by(Attendance.clock_in.desc()).first()
    clocked_in = bool(today_log and today_log.clock_in and not today_log.clock_out)
    clock_in_iso = today_log.clock_in.isoformat() if clocked_in else ""
    worked_hours = 0.0
    if today_log:
        if today_log.clock_out:
            worked_hours = float(today_log.hours or 0)
        elif today_log.clock_in:
            worked_hours = max(
                (manila_now - today_log.clock_in).total_seconds() / 3600,
                0.0
            )
    cutoff_start = today - timedelta(days=(today.weekday() + 2) % 7)
    cutoff_attendance = Attendance.query.filter(
        Attendance.employee_id == current_user.id,
        Attendance.date >= cutoff_start,
        Attendance.date <= today,
    ).order_by(Attendance.date.desc()).all()
    attendance_dates = [record.date for record in cutoff_attendance]
    pending_correction_dates = set()
    if attendance_dates:
        pending_correction_dates = {
            correction.correction_date
            for correction in AttendanceCorrection.query.filter(
                AttendanceCorrection.employee_id == current_user.id,
                AttendanceCorrection.correction_date.in_(attendance_dates),
                AttendanceCorrection.status == 'Pending',
            ).all()
        }
    attendance_correction_alerts = [
        {
            'date': record.date,
            'missing_clock_in': record.clock_in is None,
            'missing_clock_out': record.clock_out is None,
            'correction_pending': record.date in pending_correction_dates,
        }
        for record in cutoff_attendance
        if (record.clock_in is None or record.clock_out is None)
        and not (
            record.date == today
            and record.clock_in is not None
            and record.clock_out is None
        )
    ]
    insights = generate_ai_insights(current_user)
    month_start = today.replace(day=1)
    monthly_reminder = monthly_reminder_status(current_user, month_start)
    peer_evaluation_pending = monthly_reminder['peer_evaluation_pending']
    latest_payslip = Payroll.query.filter_by(
        employee_id=current_user.id,
        is_paid=True
    ).order_by(Payroll.cutoff_start.desc()).first()

    # ✅ Render staff dashboard template
    return render_template("dashboard_staff.html",
        upcoming_company_reminders=reminder_events(
            current_user, datetime.now(MANILA).date(),
            (datetime.now(MANILA) + timedelta(days=31)).date(),
        )[:8],
        attendance_records=Attendance.query.filter_by(employee_id=current_user.id).all(),
        leaves=LeaveRequest.query.filter_by(employee_id=current_user.id).all(),
        payrolls=Payroll.query.filter_by(employee_id=current_user.id).all(),
        loans=Loan.query.filter_by(employee_id=current_user.id).all(),
        quizzes=QuizResult.query.filter_by(employee_id=current_user.id).all(),
        bulletins=Bulletin.query.order_by(Bulletin.created_at.desc()).limit(5).all(),
        clocked_in=clocked_in,
        clock_in_iso=clock_in_iso,
        worked_hours=worked_hours,
        insights=insights,
        peer_evaluation_pending=peer_evaluation_pending,
        monthly_reminder=monthly_reminder,
        latest_payslip=latest_payslip,
        attendance_correction_alerts=attendance_correction_alerts,
        summary=dashboard_summary(current_user)
    )


STAFF_HELP_TOPICS = [
    {
        'keywords': ('settings', 'setting', 'customize', 'customise', 'customization', 'color',
                     'colour', 'kulay', 'sidebar', 'button size', 'layout'),
        'title': 'Dashboard settings and colors',
        'answer': 'Sa sidebar, buksan ang My Account > Settings (sa mobile, buksan muna ang Staff menu). '
                  'Ang default na kulay ng sidebar at buttons ay base sa company mo. Sa Buttons & sidebar color, pumili ng sariling kulay at pindutin ang Save Settings. '
                  'Ang custom color ay sabay na ginagamit sa buttons at sidebar; automatic ang readable text color. '
                  'AutoXpert ang may yellow sidebar at blue buttons; Trece-Uno ang may blue sidebar at yellow buttons. '
                  'Pareho silang may light gray background at readable text colors. '
                  'Puwede ring baguhin ang sidebar text size, button size, at buttons per row. '
                  'Ang settings ay saved sa browser/device na gamit mo, hindi sa lahat ng devices. '
                  'Gamitin ang Reset Layout para ibalik ang default colors ng company mo at layout; hindi nito binubura ang HR records. '
                  'Red pa rin ang warning/destructive actions tulad ng Clock Out.',
        'endpoint': 'dashboard_staff',
        'fragment': '#staffSettingsPanel',
    },
    {
        'keywords': ('clock in', 'clockin', 'clock out', 'clockout', 'attendance', 'dtr', 'time record'),
        'title': 'Attendance and DTR',
        'answer': 'Open My Attendance from the sidebar. Use Clock In at the start of work and Clock Out at the end. To download your DTR, open My Attendance and choose the PDF download. If a time is missing or wrong, use Request Attendance Correction and include the correct time and reason.',
        'endpoint': 'attendance',
    },
    {
        'keywords': ('payslip', 'payroll', 'salary', 'net pay', 'download pay'),
        'title': 'Payroll and payslip',
        'answer': 'Open My Payroll from the sidebar. Finalized payroll records show View and Download options. Staff can download a payslip after Admin finalizes the cutoff. The PDF contains the earnings, deductions, net pay, and QR verification details.',
        'endpoint': 'payroll',
    },
    {
        'keywords': ('leave', 'sil', 'vacation', 'sick leave', 'leave balance', 'leave credit'),
        'title': 'Leave request',
        'answer': 'Open Leave from the sidebar, select the leave type, enter the dates, number of days, and reason, then submit. SIL requests are checked against your remaining credits. The request stays Pending until Admin approves or rejects it.',
        'endpoint': 'leave',
    },
    {
        'keywords': ('loan', 'outstanding', 'deduction'),
        'title': 'Loan account',
        'answer': 'Open Loans from the sidebar to view your outstanding balance, approved loans, and cutoff deductions. You can file a loan request there. The loan statement can be downloaded and includes the QR verification details.',
        'endpoint': 'loan',
    },
    {
        'keywords': ('overtime', 'ot', 'sunday'),
        'title': 'Overtime request',
        'answer': 'Open Apply for OT from the dashboard or sidebar. Enter the OT date, start and end time, and reason. Admin must approve the request before the overtime is included in payroll. Sunday and holiday OT are shown in the payslip breakdown when approved.',
        'endpoint': 'apply_ot',
    },
    {
        'keywords': ('profile', 'address', 'contact', 'government id', 'tin', 'sss', 'philhealth', 'pagibig'),
        'title': 'Profile and personal information',
        'answer': 'Open Profile from the sidebar to review or update your personal information, contact details, address, and government IDs. Downloaded profile documents include a stable document ID and QR verification.',
        'endpoint': 'profile',
    },
    {
        'keywords': ('bulletin', 'announcement', 'company files', 'document'),
        'title': 'Company information',
        'answer': 'Use Company Bulletin for announcements and Company Files for documents shared with your company. Open the document first, then use its download action when available.',
        'endpoint': 'bulletin',
    },
    {
        'keywords': ('message', 'messages', 'inbox', 'admin message', 'hr message'),
        'title': 'Messages from Admin/HR',
        'answer': 'Open My Account > Messages in the sidebar to read private messages and announcements from Admin/HR. Messages are shown inside HRIS; they do not send email.',
        'endpoint': 'staff_messages',
    },
]


def answer_staff_help(question):
    normalized = ' '.join(str(question or '').lower().split())
    if not normalized:
        return {
            'title': 'Ask Staff Help',
            'answer': 'Type a question such as "How do I download my payslip?" or "How do I file leave?"',
            'matched': True,
            'topic': None,
        }
    for topic in STAFF_HELP_TOPICS:
        if any(keyword in normalized for keyword in topic['keywords']):
            return {'title': topic['title'], 'answer': topic['answer'], 'matched': True, 'topic': topic}
    return {
        'title': 'I need more information',
        'answer': 'Hindi ko pa alam ang sagot sa tanong na iyan. Subukan ang keywords na settings, kulay, attendance, payslip, leave, loan, overtime, profile, bulletin, messages, o download. Kung hindi pa rin malinaw, gamitin ang Staff Guide at mag-submit ng Attendance Correction o Incident Report kung iyon ang concern.',
        'matched': False,
        'topic': None,
    }


@app.route('/staff-help', methods=['GET', 'POST'])
@login_required
def staff_help():
    if 'staff' not in current_user.role.lower():
        return redirect(url_for('dashboard_admin'))
    question = request.form.get('question', '') if request.method == 'POST' else request.args.get('question', '')
    help_result = answer_staff_help(question)
    topic_url = None
    if help_result.get('topic'):
        topic = help_result['topic']
        if topic['endpoint'] == 'attendance':
            topic_url = url_for('attendance', employee_id=current_user.id)
        elif topic['endpoint'] == 'payroll':
            topic_url = url_for('payroll', employee_id=current_user.id)
        elif topic['endpoint'] == 'profile':
            topic_url = url_for('profile', user_id=current_user.id)
        elif topic['endpoint'] == 'loan':
            topic_url = url_for('loan', employee_id=current_user.id)
        else:
            topic_url = url_for(topic['endpoint'])
        topic_url += topic.get('fragment', '')
    return render_template('staff_help.html', question=question, help_result=help_result, topic_url=topic_url)


@app.route('/monthly-reminders')
@login_required
def monthly_reminders():
    if 'staff' not in current_user.role.lower():
        return redirect(url_for('dashboard_admin'))
    today = datetime.now(ZoneInfo('Asia/Manila')).date()
    month_start = today.replace(day=1)
    monthly_reminder = monthly_reminder_status(current_user, month_start)
    return render_template(
        'monthly_reminders.html',
        month_start=month_start,
        month_end=monthly_reminder['month_end'],
        quiz_taken=monthly_reminder['quiz_taken'],
        peer_due=monthly_reminder['peer_evaluation_pending'],
    )


@app.route('/staff-guide')
@login_required
def staff_guide():
    if 'staff' not in current_user.role.lower():
        return redirect(url_for('dashboard_admin'))
    return render_template('staff_guide.html')


@app.route('/incident_report', methods=['GET', 'POST'])
@login_required
def submit_incident():
    if "staff" not in current_user.role.lower():
        flash("❌ Incident reports are for staff submissions.", "danger")
        return redirect(url_for('dashboard_admin'))

    coworkers = Employee.query.filter(
        Employee.id != current_user.id,
        Employee.role.ilike('%staff%'),
        Employee.first_name.isnot(None),
        Employee.first_name != '',
        Employee.last_name.isnot(None),
        Employee.last_name != '',
    ).order_by(Employee.last_name, Employee.first_name).all()

    if request.method == 'POST':
        category = request.form.get('category', '').strip()
        description = request.form.get('description', '').strip()
        signature = request.form.get('signature', '').strip()
        reported_employee_id = request.form.get('reported_employee_id', type=int)
        reported_employee = next(
            (employee for employee in coworkers if employee.id == reported_employee_id),
            None
        )
        if not category or not description or not signature or not reported_employee:
            flash("❌ Category, coworker, description, and signature are required.", "danger")
            return render_template(
                'incident_report.html',
                coworkers=coworkers,
                selected_coworker_id=reported_employee_id
            )
        db.session.add(IncidentReport(
            employee_id=current_user.id,
            reported_employee_id=reported_employee.id,
            category=category,
            description=description,
            signature=signature,
            staff_signature=signature,
            status='pending'
        ))
        db.session.commit()
        flash("✅ Incident report submitted for admin review.", "success")
        return redirect(url_for('dashboard_staff'))
    return render_template('incident_report.html', coworkers=coworkers)


@app.route('/incident_review/<int:report_id>', methods=['POST'])
@login_required
def review_incident(report_id):
    if "admin" not in current_user.role.lower():
        return "Access denied", 403
    if db.session.get_bind().dialect.name == 'sqlite':
        lock_attendance_employee(current_user.id)
    report = IncidentReport.query.filter_by(id=report_id).with_for_update().first_or_404()
    action = request.form.get('action')
    admin_signature = request.form.get('admin_signature', '').strip()
    if action not in {'approve', 'reject'} or not admin_signature:
        flash("❌ Review action and admin signature are required.", "danger")
        return redirect(url_for('dashboard_admin'))

    if report.status != 'pending':
        flash('This incident has already been reviewed. No additional points were applied.', 'warning')
        return redirect(url_for('dashboard_admin'))
    employee = db.session.get(Employee, report.reported_employee_id) if report.reported_employee_id else None
    if action == 'approve' and employee is None:
        flash('Select a valid reported employee before approving this legacy incident. No points were applied.', 'danger')
        return redirect(url_for('dashboard_admin'))
    report.status = 'approved' if action == 'approve' else 'rejected'
    report.reviewed_by = current_user.id
    report.admin_signature = admin_signature
    if action == 'approve':
        lock_attendance_employee(employee.id)
        points = 5
        if report.category.lower() == 'compliment':
            employee.merit_points = (employee.merit_points or 0) + points
            merit = MeritDemerit(employee_id=employee.id, merit_points=points)
            message = "🌟 Compliment approved: +5 merit."
        elif report.category.lower() in {'lapses', 'lapse'}:
            employee.demerit_points = (employee.demerit_points or 0) + points
            merit = MeritDemerit(employee_id=employee.id, demerit_points=points)
            message = "⚠️ Lapses approved: +5 demerit."
        else:
            merit = None
            message = "✅ Incident approved."
        if merit:
            merit.source = 'incident'
            merit.reference = f'Incident #{report.id}: {report.category}'
            db.session.add(merit)
        flash(message, "success")
    else:
        flash("Incident report rejected. No merit/demerit applied.", "info")
    db.session.commit()
    return redirect(url_for('dashboard_admin'))


@app.route('/incident_report_pdf/<int:emp_id>')
@login_required
def incident_report_pdf(emp_id):
    emp = Employee.query.get_or_404(emp_id)
    if current_user.id != emp_id and "admin" not in current_user.role.lower():
        return "Access denied", 403
    selected_month = request.args.get('month', type=int)
    selected_year = request.args.get('year', type=int)
    report_query = IncidentReport.query.filter_by(employee_id=emp_id)
    period_label = "All dates"
    filename_period = "all"
    if selected_month and selected_year and 1 <= selected_month <= 12:
        period_start = datetime(selected_year, selected_month, 1)
        period_end = datetime(
            selected_year + (selected_month == 12),
            1 if selected_month == 12 else selected_month + 1,
            1
        )
        report_query = report_query.filter(
            IncidentReport.created_at >= period_start,
            IncidentReport.created_at < period_end
        )
        period_label = f"{period_start.strftime('%b')} 1-{(period_end - timedelta(days=1)).day} {selected_year}"
        filename_period = f"{selected_year}_{selected_month:02d}"
    reports = report_query.order_by(IncidentReport.created_at.desc()).all()
    from reportlab.lib.utils import ImageReader
    import base64

    def draw_signature(pdf, value, x, y, label):
        if not value:
            return y
        try:
            encoded = value.split(',', 1)[1] if ',' in value else value
            image = ImageReader(io.BytesIO(base64.b64decode(encoded)))
            pdf.drawImage(image, x, y - 50, width=150, height=50, preserveAspectRatio=True, mask='auto')
            pdf.drawString(x, y - 65, label)
            return y - 80
        except (ValueError, TypeError, base64.binascii.Error):
            return y

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawString(72, 750, payroll_company_name(emp))
    pdf.drawString(72, 732, f'Incident Reports for {period_label}')
    pdf.setFont('Helvetica', 11)
    pdf.drawString(72, 712, f'Employee: {emp.first_name} {emp.last_name}')
    y = 700
    for report in reports:
        text = f'[{report.status.upper()}] {report.category.title()} - {report.description}'
        for line in [text[i:i + 100] for i in range(0, len(text), 100)]:
            pdf.drawString(72, y, line)
            y -= 16
        y = draw_signature(pdf, report.staff_signature or report.signature, 72, y, 'Staff Signature')
        y = draw_signature(pdf, report.admin_signature, 300, y + 80, 'Admin Signature')
        y -= 12
        if y < 110:
            pdf.showPage()
            pdf.setFont('Helvetica', 11)
            y = 750
    if not reports:
        pdf.drawString(72, y, 'No incident reports found.')
    pdf.line(72, 75, 260, 75)
    pdf.line(340, 75, 528, 75)
    pdf.drawString(72, 60, 'Authorized Person Signature')
    pdf.drawString(340, 60, 'Date')
    verification = build_document_verification(
        'incident_report',
        emp.id,
        f'IncidentReport-{emp.id}-{selected_year or datetime.now().year}-{selected_month or "all"}',
        cutoff_start=date(selected_year, selected_month, 1) if selected_year and selected_month else None,
        cutoff_end=(date(selected_year + (selected_month == 12), 1 if selected_month == 12 else selected_month + 1, 1) - timedelta(days=1)) if selected_year and selected_month else None,
        net_pay=0.0,
    )
    pdf.setFont('Helvetica-Oblique', 8)
    pdf.drawString(420, 54, f'Document ID: {verification["document_id"]}')
    pdf.drawString(420, 44, 'Scan QR to verify.')
    if qrcode is not None:
        qr_bytes = generate_qr_image_bytes(verification['verify_url'])
        if qr_bytes:
            pdf.drawImage(ImageReader(io.BytesIO(qr_bytes)), 485, 10, width=60, height=60)
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f'incident_reports_{emp.id}_{filename_period}.pdf', mimetype='application/pdf')


@app.route('/admin/incidents')
@login_required
def admin_incidents():
    if 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    selected_month = request.args.get('month', type=int)
    selected_year = request.args.get('year', type=int)
    report_query = IncidentReport.query
    if selected_month and selected_year and 1 <= selected_month <= 12:
        period_start = datetime(selected_year, selected_month, 1)
        period_end = datetime(selected_year + (selected_month == 12), 1 if selected_month == 12 else selected_month + 1, 1)
        report_query = report_query.filter(IncidentReport.created_at >= period_start, IncidentReport.created_at < period_end)
    current_year = datetime.today().year
    return render_template(
        'admin_incidents.html',
        reports=report_query.order_by(IncidentReport.created_at.desc()).all(),
        selected_month=selected_month,
        selected_year=selected_year,
        months=range(1, 13),
        years=range(current_year - 2, current_year + 1)
    )


@app.route('/export_insights_pdf/<int:emp_id>', methods=['POST'])
@login_required
def export_insights_pdf(emp_id):
    emp = Employee.query.get_or_404(emp_id)
    if current_user.id != emp_id and "admin" not in current_user.role.lower():
        flash("❌ Access denied.", "danger")
        return redirect(url_for('dashboard_staff'))

    insights = generate_ai_insights(emp)
    today = datetime.today()
    cutoff_start = today - timedelta(days=(today.weekday() + 2) % 7)
    cutoff_end = cutoff_start + timedelta(days=6)

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(72, 750, payroll_company_name(emp))
    pdf.drawString(72, 732, "AI Insights Report")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, 712, f"Employee: {emp.first_name} {emp.last_name}")
    pdf.drawString(72, 694, f"Cut-off: {cutoff_start.strftime('%m-%d-%Y')} ~ {cutoff_end.strftime('%m-%d-%Y')}")

    y = 675
    for key, value in insights.items():
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(72, y, key.replace('_', ' ').title())
        y -= 18
        pdf.setFont("Helvetica", 10)
        words = value.split()
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if pdf.stringWidth(candidate, "Helvetica", 10) > 460:
                pdf.drawString(90, y, line)
                y -= 15
                line = word
            else:
                line = candidate
        if line:
            pdf.drawString(90, y, line)
            y -= 25
        if y < 80:
            pdf.showPage()
            y = 750

    pdf.setFont("Helvetica-Oblique", 9)
    pdf.line(72, 85, 260, 85)
    pdf.line(340, 85, 528, 85)
    pdf.drawString(72, 70, "Authorized Person Signature")
    pdf.drawString(340, 70, "Date")
    pdf.drawString(72, 50, f"Generated on {datetime.now().strftime('%m-%d-%Y %H:%M')}")
    verification = build_document_verification(
        'ai_insights',
        emp.id,
        f'Insights-{emp.id}-{cutoff_start.isoformat()}-{cutoff_end.isoformat()}',
        cutoff_start=cutoff_start,
        cutoff_end=cutoff_end,
        net_pay=0.0,
    )
    pdf.setFont('Helvetica-Oblique', 8)
    pdf.drawString(420, 54, f'Document ID: {verification["document_id"]}')
    pdf.drawString(420, 44, 'Scan QR to verify.')
    if qrcode is not None:
        qr_bytes = generate_qr_image_bytes(verification['verify_url'])
        if qr_bytes:
            pdf.drawImage(ImageReader(io.BytesIO(qr_bytes)), 485, 10, width=60, height=60)
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"insights_{emp.id}.pdf",
        mimetype="application/pdf"
    )


@app.route('/peer_evaluation', methods=['GET', 'POST'])
@login_required
def peer_evaluation():
    if "staff" not in current_user.role.lower():
        flash("❌ Peer evaluation is available to staff only.", "danger")
        return redirect(url_for('dashboard_admin'))

    ensure_peer_questions()
    questions = EvaluationQuestion.query.filter_by(is_active=True).order_by(EvaluationQuestion.id).all()
    employees = Employee.query.filter(
        Employee.id != current_user.id,
        Employee.role.ilike('%staff%'),
        Employee.first_name.isnot(None),
        Employee.first_name != '',
        Employee.last_name.isnot(None),
        Employee.last_name != '',
    ).order_by(Employee.last_name, Employee.first_name).all()
    month_start = datetime.today().date().replace(day=1)
    month_start_dt = datetime.combine(month_start, datetime.min.time())

    if request.method == 'POST':
        target_id = request.form.get('employee_id', type=int)
        target = Employee.query.filter(Employee.id == target_id, Employee.id != current_user.id).first()
        if not target:
            flash("❌ Please choose a valid coworker.", "danger")
            return redirect(url_for('peer_evaluation'))

        already_submitted = Evaluation.query.filter(
            Evaluation.employee_id == target.id,
            Evaluation.evaluator_id == current_user.id,
            Evaluation.date >= month_start_dt,
            Evaluation.category.like("peer_%")
        ).first()
        if already_submitted:
            flash("⚠️ You already evaluated this coworker this month.", "warning")
            return redirect(url_for('peer_evaluation'))

        ratings = []
        for question in questions:
            rating = request.form.get(f'question_{question.id}', type=int)
            if rating not in range(1, 6):
                flash("❌ Please answer every questionnaire item from 1 to 5.", "danger")
                return redirect(url_for('peer_evaluation'))
            ratings.append((question, rating))

        remarks = request.form.get('remarks', '').strip()
        for question, rating in ratings:
            db.session.add(Evaluation(
                employee_id=target.id,
                evaluator_id=current_user.id,
                rating=rating,
                remarks=f"{question.text}: {remarks}" if remarks else question.text,
                category=f"peer_{question.id}",
                approval_status="Pending",
                date=datetime.now()
            ))
        db.session.commit()
        flash("✅ Monthly peer evaluation submitted!", "success")
        return redirect(url_for('dashboard_staff'))

    submitted_targets = db.session.query(Evaluation.employee_id).filter(
        Evaluation.evaluator_id == current_user.id,
        Evaluation.date >= month_start_dt,
        Evaluation.category.like("peer_%")
    ).distinct().all()
    submitted_ids = {row[0] for row in submitted_targets}
    return render_template(
        "peer_evaluation.html",
        questions=questions,
        employees=employees,
        submitted_ids=submitted_ids,
        month_start=month_start
    )


# ------------------ CLOCK IN / OUT (Unified) ------------------
def is_trece_sunday(employee, target_date):
    return bool(
        target_date.weekday() == 6
        and employee
        and str(employee.company or '').lower().startswith('trece')
    )


def is_restday_overtime(attendance):
    return bool(attendance.is_restday_ot or attendance.date.weekday() == 6)


def automatic_trece_sunday_hours(attendance):
    if not (
        is_trece_sunday(getattr(attendance, 'employee', None), attendance.date)
        and attendance.clock_in
        and attendance.clock_out
    ):
        return 0.0
    scheduled_start = datetime.combine(attendance.date, time(8, 0))
    scheduled_end = datetime.combine(attendance.date, time(12, 0))
    worked_start = max(attendance.clock_in, scheduled_start)
    worked_end = min(attendance.clock_out, scheduled_end)
    return round(
        max((worked_end - worked_start).total_seconds(), 0) / 3600,
        2,
    )


def attendance_overtime_status(attendance):
    if automatic_trece_sunday_hours(attendance) > 0:
        return "Approved"
    if (
        attendance.ot_status == "Approved"
        and payroll_overtime_hours(attendance) > 0
    ):
        return "Approved"
    if attendance.ot_status == "Rejected":
        return "Rejected"
    return attendance.ot_status or "Pending"


def applied_overtime_hours(attendance, application):
    requested_start = datetime.combine(attendance.date, application.start_time)
    requested_end = datetime.combine(attendance.date, application.end_time)
    return round(
        max((requested_end - requested_start).total_seconds() / 3600, 0),
        2,
    )


def approved_application_overtime_hours(attendance, application):
    if attendance.date.weekday() != 6 or not is_trece_sunday(
        attendance.employee, attendance.date
    ):
        return applied_overtime_hours(attendance, application)
    requested_start = datetime.combine(attendance.date, application.start_time)
    requested_end = datetime.combine(attendance.date, application.end_time)
    scheduled_start = datetime.combine(attendance.date, time(8, 0))
    scheduled_end = datetime.combine(attendance.date, time(12, 0))
    requested_seconds = max((requested_end - requested_start).total_seconds(), 0)
    scheduled_overlap = max(
        (
            min(requested_end, scheduled_end)
            - max(requested_start, scheduled_start)
        ).total_seconds(),
        0,
    )
    return round(max(requested_seconds - scheduled_overlap, 0) / 3600, 2)


def approved_ot_application(attendance):
    return OTApplication.query.filter_by(
        employee_id=attendance.employee_id,
        ot_date=attendance.date,
        status="Approved",
    ).first()


def payroll_overtime_hours(attendance):
    automatic_sunday_hours = automatic_trece_sunday_hours(attendance)
    if attendance.ot_status == "Approved" or automatic_sunday_hours > 0:
        application = approved_ot_application(attendance)
        if application:
            if not attendance.clock_out:
                return automatic_sunday_hours
            return automatic_sunday_hours + approved_application_overtime_hours(
                attendance, application
            )
        if automatic_sunday_hours > 0:
            return automatic_sunday_hours
        if attendance.ot_status == "Approved":
            return 0.0
    return float(attendance.overtime_hours or 0)


def attendance_overtime_is_payable(attendance):
    if automatic_trece_sunday_hours(attendance) > 0:
        return True
    return (
        attendance.ot_status == "Approved"
        and payroll_overtime_hours(attendance) > 0
    )


def apply_overtime_details(attendance):
    if not attendance.clock_in or not attendance.clock_out:
        return False

    automatic_sunday_hours = automatic_trece_sunday_hours(attendance)
    holiday = Holiday.query.filter_by(date=attendance.date).first()
    is_regular_weekday = attendance.date.weekday() != 6 and not holiday
    overtime_start = datetime.combine(
        attendance.date,
        time(18, 0) if is_regular_weekday else time(17, 0)
    )
    application = approved_ot_application(attendance)
    is_approved = bool(
        application or automatic_sunday_hours > 0
    )
    if automatic_sunday_hours > 0:
        attendance.overtime_hours = automatic_sunday_hours
        if application:
            attendance.overtime_hours += approved_application_overtime_hours(
                attendance, application
            )
    elif application:
        attendance.overtime_hours = applied_overtime_hours(attendance, application)
    else:
        attendance.overtime_hours = round(
            max((attendance.clock_out - overtime_start).total_seconds() / 3600, 0),
            2,
        )
    attendance.is_restday_ot = bool(
        is_approved and attendance.overtime_hours > 0 and attendance.date.weekday() == 6
    )
    attendance.is_holiday_ot = bool(is_approved and holiday)
    attendance.is_weekday_ot = bool(
        is_approved
        and attendance.overtime_hours > 0
        and not attendance.is_restday_ot
        and not attendance.is_holiday_ot
    )
    attendance.ot_status = "Approved" if is_approved and attendance.overtime_hours > 0 else None
    return attendance.ot_status == "Approved"


def holiday_multiplier(attendance):
    holiday = Holiday.query.filter_by(date=attendance.date).first()
    is_restday = is_restday_overtime(attendance)
    if holiday and holiday.holiday_type == 'Regular Holiday':
        base_multiplier = 2.60 if is_restday else 2.00
    elif holiday:
        base_multiplier = 1.50 if is_restday else 1.30
    elif is_restday:
        base_multiplier = 1.30
    else:
        return 1.25
    return round(base_multiplier * 1.30, 2)


def restday_pay_breakdown(attendance, daily_rate):
    hours = payroll_overtime_hours(attendance)
    holiday = Holiday.query.filter_by(date=attendance.date).first()
    if attendance.date.weekday() == 6 and not holiday:
        regular_hours = min(hours, 8.0)
        overtime_hours = max(hours - 8.0, 0.0)
        return (
            regular_hours * (daily_rate / 8) * 1.30,
            overtime_hours * (daily_rate / 8) * 1.69,
            regular_hours,
            overtime_hours,
        )
    return (0.0, (daily_rate / 8) * holiday_multiplier(attendance) * hours, 0.0, hours)


def payroll_extra_pay(attendance, daily_rate):
    restday_pay, overtime_pay, _, _ = restday_pay_breakdown(attendance, daily_rate)
    return restday_pay + overtime_pay


def scheduled_workday_near(employee, holiday_date, direction):
    target = holiday_date + timedelta(days=direction)
    for _ in range(7):
        if target.weekday() != 6:
            return target
        target += timedelta(days=direction)
    return holiday_date + timedelta(days=direction)


def paid_attendance_or_leave(employee_id, target_date):
    attended = Attendance.query.filter(
        Attendance.employee_id == employee_id,
        Attendance.date == target_date,
        Attendance.clock_out != None,
    ).first() is not None
    if attended:
        return True
    return LeaveRequest.query.filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status == 'Approved',
        LeaveRequest.is_paid.is_(True),
        LeaveRequest.start_date <= target_date,
        LeaveRequest.end_date >= target_date,
    ).first() is not None


def eligible_for_regular_holiday_pay(attendance):
    employee = attendance.employee or db.session.get(Employee, attendance.employee_id)
    before_date = scheduled_workday_near(employee, attendance.date, -1)
    after_date = scheduled_workday_near(employee, attendance.date, 1)
    return (
        paid_attendance_or_leave(attendance.employee_id, before_date)
        and paid_attendance_or_leave(attendance.employee_id, after_date)
    )


def regular_attendance_hours(attendance):
    clock_in = getattr(attendance, 'clock_in', None)
    clock_out = getattr(attendance, 'clock_out', None)
    if (
        clock_in
        and clock_out
        and is_trece_sunday(getattr(attendance, 'employee', None), attendance.date)
    ):
        return automatic_trece_sunday_hours(attendance)
    recorded_hours = (
        biometric_work_hours(clock_in, clock_out)
        if clock_in and clock_out
        else float(attendance.hours or 0)
    )
    return min(max(float(recorded_hours or 0), 0.0), 8.0)


def regular_day_pay(attendance, daily_rate, hours_override=None):
    holiday = Holiday.query.filter_by(date=attendance.date).first()
    regular_hours = (
        regular_attendance_hours(attendance)
        if hours_override is None
        else min(max(float(hours_override or 0), 0.0), 8.0)
    )
    prorated_daily_rate = daily_rate * (regular_hours / 8.0) if regular_hours else 0.0
    is_restday = attendance.date.weekday() == 6
    if holiday and holiday.holiday_type == 'Regular Holiday':
        if not eligible_for_regular_holiday_pay(attendance):
            return 0.0
        if is_restday:
            return prorated_daily_rate * 2.6
        return prorated_daily_rate * 2.0
    if holiday:
        if is_restday:
            return prorated_daily_rate * 1.5
        return prorated_daily_rate * 1.3
    if attendance.date.weekday() == 6:
        return 0.0
    return prorated_daily_rate


def payroll_worked_days_count(employee, attendance_records):
    return sum(
        attendance.date.weekday() != 6
        for attendance in attendance_records
    )


def loan_cutoff_deduction(loan_balance, requested_deduction=0.0):
    """Return the requested cutoff deduction, capped by the outstanding balance."""
    balance = max(float(loan_balance or 0), 0)
    requested = max(float(requested_deduction or 0), 0)
    return min(balance, requested)


def automatic_loan_deduction(employee, record, cutoff_start):
    if record and (record.is_paid or record.loan_deduction_applied):
        return float(record.loan or 0)
    if approved_loan_deduction_skip(employee.id, cutoff_start):
        return 0.0
    requested = float(employee.loan_deduction_per_cutoff or 500)
    if record and record.loan_deduction_override is not None:
        requested = float(record.loan_deduction_override)
    elif record and not record.payslip_snapshot and float(record.loan or 0) > 0:
        # A legacy draft without a snapshot carries a manually entered deduction.
        requested = float(record.loan)
    if not isfinite(requested) or requested < 0:
        raise ValueError('Invalid company loan deduction.')
    return loan_cutoff_deduction(employee.loan_balance, requested)


def payroll_statutory_deductions(payroll_record, calculated_deductions):
    """Use attendance-adjusted statutory calculations for each weekly cutoff."""
    return calculated_deductions


def completed_cutoff(today=None):
    """Return the available Saturday-through-Friday payroll cutoff."""
    current_time = (
        datetime.now(ZoneInfo('Asia/Manila'))
        if today is None else datetime.combine(today, time.min)
    )
    current_start = current_time.date() - timedelta(days=(current_time.weekday() + 2) % 7)
    start = (
        current_start
        if current_time.weekday() == 4 and current_time.time() >= time(22, 0)
        else current_start - timedelta(days=7)
    )
    return start, start + timedelta(days=7)

def payroll_cutoff_from_request(value=None):
    """Return a selected Saturday-to-Friday cutoff or the active cutoff."""
    if value:
        selected_start = datetime.strptime(value, '%Y-%m-%d').date()
        if selected_start.weekday() != 5:
            raise ValueError('Cutoff start date must be a Saturday.')
        return selected_start, selected_start + timedelta(days=7)
    current_start, _ = current_payroll_cutoff()
    return current_start, current_start + timedelta(days=7)


def current_payroll_cutoff(today=None):
    today = today or datetime.now(ZoneInfo('Asia/Manila')).date()
    cutoff_start = today - timedelta(days=(today.weekday() + 2) % 7)
    return cutoff_start, cutoff_start + timedelta(days=6)


def approved_loan_deduction_skip(employee_id, cutoff_start):
    return LoanDeductionSkipRequest.query.filter_by(
        employee_id=employee_id,
        cutoff_start=cutoff_start,
        status='Approved',
    ).first()


def payroll_cutoffs_in_month(cutoff_start):
    """Count Saturday-to-Friday payroll cutoffs assigned to the start-date month."""
    month_start = cutoff_start.replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    first_saturday = month_start + timedelta(days=(5 - month_start.weekday()) % 7)
    return 1 + (next_month - timedelta(days=1) - first_saturday).days // 7


def payroll_cutoff_dates_in_month(cutoff_start):
    month_start = cutoff_start.replace(day=1)
    first_saturday = month_start + timedelta(days=(5 - month_start.weekday()) % 7)
    return [
        first_saturday + timedelta(days=7 * index)
        for index in range(payroll_cutoffs_in_month(cutoff_start))
    ]


def payroll_dashboard_cutoff_options(selected_start):
    today = datetime.now(ZoneInfo('Asia/Manila')).date()
    current_week_start = today - timedelta(days=(today.weekday() + 2) % 7)
    starts = {
        current_week_start - timedelta(days=7 * weeks_ago)
        for weeks_ago in range(52)
    }
    starts.update(
        cutoff_start
        for (cutoff_start,) in db.session.query(Payroll.cutoff_start).distinct()
        if cutoff_start is not None and cutoff_start.weekday() == 5
    )
    starts.add(selected_start)
    return [
        {
            'start': cutoff_start,
            'end': cutoff_start + timedelta(days=6),
            'label': (
                f'{cutoff_start.strftime("%b %d")} – '
                f'{(cutoff_start + timedelta(days=6)).strftime("%b %d, %Y")}'
            ),
        }
        for cutoff_start in sorted(starts, reverse=True)
    ]


@app.route('/payroll-loan-review', methods=['GET', 'POST'])
@login_required
def payroll_loan_review():
    if not can_review_payroll_loans(current_user):
        return 'Access denied', 403
    try:
        cutoff_start, cutoff_end = payroll_cutoff_from_request(
            request.values.get('cutoff_start')
        )
    except ValueError:
        flash('Select a valid Saturday cutoff start date.', 'danger')
        return redirect(url_for('payroll_loan_review'))

    employee_query = Employee.query.filter(
        ~Employee.role.ilike('%admin%'),
        Employee.payroll_attendance_exempt.is_(False),
        Employee.first_name.isnot(None),
        Employee.first_name != '',
        Employee.last_name.isnot(None),
        Employee.last_name != '',
    )
    if not can_manage_admin_payroll(current_user):
        staff_company = str(current_user.company or '').strip().lower()
        if staff_company.startswith('trece'):
            employee_query = employee_query.filter(company_employee_filter('Trece-Uno'))
        elif staff_company in {'auto expert', 'auto-expert', 'autoxpert', 'autoexpert'}:
            employee_query = employee_query.filter(company_employee_filter('Auto Expert'))
        else:
            return 'Access denied', 403
    employees = employee_query.order_by(
        Employee.company, Employee.last_name, Employee.first_name
    ).all()
    employee_ids = [employee.id for employee in employees]
    skip_requests = LoanDeductionSkipRequest.query.filter(
        LoanDeductionSkipRequest.cutoff_start == cutoff_start,
        LoanDeductionSkipRequest.employee_id.in_(employee_ids),
    ).order_by(LoanDeductionSkipRequest.created_at.desc()).all()
    active_skip_by_employee = {
        item.employee_id: item for item in skip_requests
        if item.status in {'Pending', 'Approved'}
    }
    approved_skip_employee_ids = {
        item.employee_id for item in skip_requests if item.status == 'Approved'
    }

    if request.method == 'POST':
        try:
            updates = []
            for employee in employees:
                balance_value = request.form.get(f'loan_balance_{employee.id}')
                deduction_value = request.form.get(f'loan_deduction_{employee.id}')
                if balance_value is None or deduction_value is None:
                    continue
                balance = float(balance_value)
                requested_deduction = float(deduction_value)
                if (
                    not isfinite(balance)
                    or not isfinite(requested_deduction)
                    or balance < 0
                    or requested_deduction < 0
                ):
                    raise ValueError
                payroll_record = Payroll.query.filter_by(
                    employee_id=employee.id,
                    cutoff_start=cutoff_start,
                    cutoff_end=cutoff_end - timedelta(days=1),
                ).first()
                if payroll_record and (
                    payroll_record.is_paid or payroll_record.loan_deduction_applied
                ):
                    return 'Loan details cannot be changed after payroll finalization.', 409
                updates.append((
                    employee,
                    balance,
                    requested_deduction,
                    payroll_record,
                ))
        except (TypeError, ValueError):
            db.session.rollback()
            flash('Enter valid non-negative loan balances and deductions.', 'danger')
            return redirect(url_for(
                'payroll_loan_review',
                cutoff_start=cutoff_start.isoformat(),
            ))

        deduction_was_capped = False
        for employee, balance, requested_deduction, payroll_record in updates:
            employee.loan_balance = balance
            cutoff_deduction = (
                0.0 if employee.id in approved_skip_employee_ids
                else loan_cutoff_deduction(balance, requested_deduction)
            )
            if employee.id not in approved_skip_employee_ids:
                deduction_was_capped = deduction_was_capped or cutoff_deduction < requested_deduction
            if payroll_record is None and cutoff_deduction > 0:
                payroll_record = Payroll(
                    employee_id=employee.id,
                    cutoff_start=cutoff_start,
                    cutoff_end=cutoff_end - timedelta(days=1),
                )
                db.session.add(payroll_record)
            if payroll_record is not None:
                payroll_record.loan = cutoff_deduction
                payroll_record.loan_deduction_override = cutoff_deduction
        db.session.commit()
        flash('Loan balances and cutoff deductions saved for your review.', 'success')
        if deduction_was_capped:
            flash("Some cutoff deductions were capped at the employee's remaining loan balance.", 'warning')
        return redirect(url_for(
            'payroll_loan_review',
            cutoff_start=cutoff_start.isoformat(),
        ))

    payroll_records = {
        record.employee_id: record
        for record in Payroll.query.filter_by(
            cutoff_start=cutoff_start,
            cutoff_end=cutoff_end - timedelta(days=1),
        ).all()
    }
    loan_rows = []
    for employee in employees:
        record = payroll_records.get(employee.id)
        balance = float(employee.loan_balance or 0)
        skip_request = active_skip_by_employee.get(employee.id)
        deduction = automatic_loan_deduction(employee, record, cutoff_start)
        if record and record.loan_deduction_applied and deduction > 0:
            # The current balance already has this deduction removed, so rebuild the
            # before/after values for this cutoff from the loan history.
            after_deduction = loan_account_summary(employee)['remaining_after'].get(
                record.id, balance,
            )
            balance = after_deduction + deduction
        else:
            after_deduction = max(balance - deduction, 0.0)
        loan_rows.append({
            'employee': employee,
            'balance': balance,
            'deduction': deduction,
            'after_deduction': after_deduction,
            'skip_request': skip_request,
            'skip_approved': bool(skip_request and skip_request.status == 'Approved'),
            'finalized': bool(
                record and (record.is_paid or record.loan_deduction_applied)
            ),
        })
    return render_template(
        'payroll_loan_review.html',
        loan_rows=loan_rows,
        skip_requests=skip_requests,
        cutoff_options=payroll_dashboard_cutoff_options(cutoff_start),
        start_cutoff=cutoff_start,
        end_cutoff=cutoff_end - timedelta(days=1),
    )


def manual_owner_contribution_deductions(employee, cutoff_start):
    if (
        not getattr(employee, "payroll_attendance_exempt", False)
        or getattr(employee, "manual_contribution_cutoff_start", None) != cutoff_start
    ):
        return {"sss": 0.0, "philhealth": 0.0, "pagibig": 0.0}
    return {
        "sss": float(getattr(employee, "manual_monthly_sss", 0) or 0),
        "philhealth": float(getattr(employee, "manual_monthly_philhealth", 0) or 0),
        "pagibig": float(getattr(employee, "manual_monthly_pagibig", 0) or 0),
    }


def update_manual_owner_contribution_settings(employee, form, cutoff_start):
    if not getattr(employee, "payroll_attendance_exempt", False):
        return
    fields = {
        "manual_monthly_sss": "manual_monthly_sss",
        "manual_monthly_philhealth": "manual_monthly_philhealth",
        "manual_monthly_pagibig": "manual_monthly_pagibig",
    }
    for form_field, attribute in fields.items():
        value = form.get(f"{form_field}_{employee.id}")
        if value is None:
            continue
        amount = float(value or 0)
        if not isfinite(amount) or amount < 0:
            raise ValueError("Manual contribution amounts must be non-negative.")
        setattr(employee, attribute, amount)

    selected_cutoff = form.get(f"manual_contribution_cutoff_start_{employee.id}")
    if selected_cutoff is not None:
        selected_date = datetime.strptime(selected_cutoff, "%Y-%m-%d").date()
        if selected_date not in payroll_cutoff_dates_in_month(cutoff_start):
            raise ValueError("Choose a cutoff date from the selected month.")
        employee.manual_contribution_cutoff_start = selected_date


def payroll_attendance_records(employee, cutoff_start, cutoff_end):
    if getattr(employee, "payroll_attendance_exempt", False):
        return []
    records = Attendance.query.filter(
        Attendance.employee_id == employee.id,
        Attendance.clock_out != None,
        Attendance.clock_in >= datetime.combine(cutoff_start, time.min),
        Attendance.clock_in < datetime.combine(cutoff_end, time.min),
    ).order_by(Attendance.clock_out.desc()).all()
    records_by_date = {}
    for record in records:
        if (
            record.date.weekday() == 6
            and not str(employee.company or '').lower().startswith('trece')
            and getattr(record, 'ot_status', None) != 'Approved'
        ):
            continue
        records_by_date.setdefault(record.date, record)
    return list(records_by_date.values())


ZERO_EMPLOYEE_CONTRIBUTIONS = {'sss': 0.0, 'philhealth': 0.0, 'pagibig': 0.0}
ZERO_EMPLOYER_CONTRIBUTIONS = {'sss': 0.0, 'sss_ec': 0.0, 'philhealth': 0.0, 'pagibig': 0.0}


def contribution_cutoff_basis(employee, cutoff_start):
    """Basic pay actually earned in a cutoff: daily rate x worked hours / 8, no OT or Sundays."""
    daily_rate = float(employee.daily_rate or 0)
    return round(sum(
        daily_rate * regular_attendance_hours(item) / 8.0
        for item in payroll_attendance_records(employee, cutoff_start, cutoff_start + timedelta(days=7))
        if item.date.weekday() != 6
    ), 2)


def saved_cutoff_contributions(employee_id, cutoff_start):
    record = Payroll.query.filter_by(employee_id=employee_id, cutoff_start=cutoff_start).first()
    if record is None:
        return None
    return {key: float(getattr(record, key) or 0) for key in GOV_AMOUNT_KEYS}


def cutoff_statutory_contributions(employee, cutoff_start, legacy_contribution_salary):
    """Return employee/employer contributions for one weekly cutoff.

    Cutoffs before RULES_START keep the legacy calculation exactly. Later months
    use the database contribution tables with a last-cutoff monthly true-up.
    """
    if isinstance(cutoff_start, datetime):
        cutoff_start = cutoff_start.date()
    weeks = payroll_cutoffs_in_month(cutoff_start)
    if getattr(employee, 'payroll_attendance_exempt', False):
        statutory = manual_owner_contribution_deductions(employee, cutoff_start)
        return {
            'employee': statutory, 'employer': dict(ZERO_EMPLOYER_CONTRIBUTIONS),
            'monthly_employee_total': sum(statutory.values()) * weeks,
            'basis': legacy_contribution_salary, 'legacy': True,
        }
    if cutoff_start < GOV_RULES_START:
        monthly_salary = legacy_contribution_salary * weeks
        statutory = (
            compute_weekly_deductions(monthly_salary, weeks=weeks)
            if legacy_contribution_salary > 0 else dict(ZERO_EMPLOYEE_CONTRIBUTIONS)
        )
        employer = (
            compute_weekly_employer_deductions(monthly_salary, weeks=weeks)
            if legacy_contribution_salary > 0 else dict(ZERO_EMPLOYER_CONTRIBUTIONS)
        )
        return {
            'employee': statutory, 'employer': employer,
            'monthly_employee_total': sum(statutory.values()) * weeks,
            'basis': legacy_contribution_salary, 'legacy': True,
        }
    cutoff_dates = payroll_cutoff_dates_in_month(cutoff_start)
    if cutoff_start not in cutoff_dates:
        raise ValueError('Payroll cutoff start must be a Saturday.')
    index = cutoff_dates.index(cutoff_start)
    calculator = GovernmentContributionCalculator.for_month(cutoff_start)
    if index < len(cutoff_dates) - 1:
        bases = [0.0] * len(cutoff_dates)
        bases[index] = contribution_cutoff_basis(employee, cutoff_start)
        prior = []
    else:
        bases = [contribution_cutoff_basis(employee, item) for item in cutoff_dates]
        prior = [saved_cutoff_contributions(employee.id, item) for item in cutoff_dates[:-1]]
    amounts, monthly_employee_total = GovPayrollService.allocate(
        calculator, bases, index, prior, employee.daily_rate,
        getattr(employee, 'contribution_workweek', None),
    )
    return {
        'employee': {key: float(amounts[key]) for key in ('sss', 'philhealth', 'pagibig')},
        'employer': {
            'sss': float(amounts['employer_sss']), 'sss_ec': float(amounts['employer_sss_ec']),
            'philhealth': float(amounts['employer_philhealth']),
            'pagibig': float(amounts['employer_pagibig']),
        },
        'monthly_employee_total': float(monthly_employee_total),
        'basis': bases[index], 'legacy': False,
    }


def contribution_taxable_month(contributions, gross, weeks):
    if contributions['legacy']:
        return (gross - sum(contributions['employee'].values())) * weeks
    return gross * weeks - contributions['monthly_employee_total']


def payroll_company_name(employee):
    if str(employee.company or '').lower().startswith('trece'):
        return 'TRECE-UNO AUTO SUPPLY'
    return 'AUTO-EXPERT AUTO SUPPLY'


def company_employee_filter(company):
    staff_only = ~Employee.role.ilike('%admin%') & ~Employee.payroll_attendance_exempt.is_(True)
    if company == 'Trece-Uno':
        return Employee.company.in_(['Trece', 'Trece-Uno']) & staff_only
    return (Employee.company == 'Auto Expert') & staff_only


def employer_tax_details(company):
    profile = EmployerTaxProfile.query.filter_by(company=company).first()
    if profile:
        return {
            'name': profile.registered_company_name or '',
            'tin': profile.employer_tin or '',
            'address': profile.registered_business_address or '',
            'signatory': profile.authorized_signatory_name or '',
            'position': profile.authorized_signatory_position or '',
            'legal_entity': profile.legal_employer_entity or '',
            'tax_year': profile.tax_year or datetime.now().year,
        }
    prefix = 'TRECE' if company == 'Trece-Uno' else 'AUTO_EXPERT'
    return {
        'name': os.environ.get(f'{prefix}_EMPLOYER_NAME', ''),
        'tin': os.environ.get(f'{prefix}_EMPLOYER_TIN', ''),
        'address': os.environ.get(f'{prefix}_EMPLOYER_ADDRESS', ''),
        'signatory': os.environ.get(f'{prefix}_AUTHORIZED_SIGNATORY', ''),
        'position': '',
        'legal_entity': '',
        'tax_year': datetime.now().year,
    }


def company_tax_records(company, year, month=None):
    query = Payroll.query.join(Employee).filter(
        company_employee_filter(company),
        extract('year', Payroll.cutoff_start) == year
    )
    if month is not None:
        query = query.filter(extract('month', Payroll.cutoff_start) == month)
    return query.order_by(Employee.last_name, Employee.first_name, Payroll.cutoff_start).all()


def tax_summary_pdf(title, company, period, records):
    employer = employer_tax_details(company)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawString(50, 770, title)
    pdf.setFont('Helvetica', 10)
    pdf.drawString(50, 750, f'Company: {employer["name"] or company}')
    pdf.drawString(50, 735, f'Legal Employer Entity: {employer["legal_entity"] or "Not configured"}')
    pdf.drawString(50, 720, f'Employer TIN: {employer["tin"] or "Not configured"}')
    pdf.drawString(50, 705, f'Address: {employer["address"] or "Not configured"}')
    pdf.drawString(50, 690, f'Period: {period}')
    pdf.setFont('Helvetica-Bold', 9)
    headers = ['Employee', 'Cutoff', 'Gross Income', 'Withholding Tax']
    positions = [50, 210, 330, 450]
    for position, header in zip(positions, headers):
        pdf.drawString(position, 660, header)
    y = 643
    pdf.setFont('Helvetica', 9)
    for record in records:
        values = [
            record.employee.full_name()[:28],
            f'{record.cutoff_start} to {record.cutoff_end}',
            f'PHP {float(record.gross_income or 0):,.2f}',
            f'PHP {float(record.withholding_tax or 0):,.2f}',
        ]
        for position, value in zip(positions, values):
            pdf.drawString(position, y, value)
        y -= 16
        if y < 110:
            pdf.showPage()
            y = 750
    total_compensation = sum(float(record.gross_income or 0) for record in records)
    total_tax = sum(float(record.withholding_tax or 0) for record in records)
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawString(50, y - 15, f'Total Compensation: PHP {total_compensation:,.2f}')
    pdf.drawString(50, y - 32, f'Total Tax Withheld: PHP {total_tax:,.2f}')
    pdf.drawString(50, y - 49, f'Authorized Signatory: {employer["signatory"] or "Not configured"}')
    pdf.drawString(50, y - 64, f'Position: {employer["position"] or "Not configured"}')
    pdf.setFont('Helvetica-Oblique', 8)
    pdf.drawString(50, 55, 'Internal payroll tax report. Validate against the current BIR-prescribed form before filing.')
    verification = build_document_verification(
        'tax_summary',
        0,
        f'{title}-{company}-{period}',
        cutoff_start=None,
        cutoff_end=None,
        net_pay=float(sum(float(record.withholding_tax or 0) for record in records)),
    )
    pdf.setFont('Helvetica-Oblique', 8)
    pdf.drawString(420, 54, f'Document ID: {verification["document_id"]}')
    pdf.drawString(420, 44, 'Scan QR to verify.')
    if qrcode is not None:
        qr_bytes = generate_qr_image_bytes(verification['verify_url'])
        if qr_bytes:
            pdf.drawImage(ImageReader(io.BytesIO(qr_bytes)), 485, 10, width=60, height=60)
    pdf.save()
    buffer.seek(0)
    return buffer


COMPLIANCE_REPORTS = {
    'sss_r3': {
        'title': 'SSS R3 Employee Contribution Report',
        'columns': ('Employee', 'SSS Number', 'Compensation', 'Employee Share'),
    },
    'philhealth_rf1': {
        'title': 'PhilHealth RF1 Employee Contribution Report',
        'columns': ('Employee', 'PhilHealth Number', 'Compensation', 'Employee Share'),
    },
    'pagibig_mcrf': {
        'title': 'Pag-IBIG MCRF Employee Contribution Report',
        'columns': ('Employee', 'Pag-IBIG Number', 'Compensation', 'Employee Share'),
    },
    'bir_1601c': {
        'title': 'BIR 1601-C Withholding Tax Report',
        'columns': ('Employee', 'TIN', 'Compensation', 'Tax Withheld'),
    },
    'bir_2316': {
        'title': 'BIR 2316 Annual Compensation Report',
        'columns': ('Employee', 'TIN', 'Annual Compensation', 'Tax Withheld'),
    },
}


def build_compliance_report_rows(report_type, year, company=None):
    if report_type not in COMPLIANCE_REPORTS:
        abort(404)
    query = Employee.query.filter(Employee.role.ilike('%staff%'))
    if company in {'Trece-Uno', 'Auto Expert'}:
        query = query.filter(company_employee_filter(company))
    employees = query.order_by(Employee.last_name, Employee.first_name).all()
    rows = []
    for employee in employees:
        records = Payroll.query.filter(
            Payroll.employee_id == employee.id,
            Payroll.cutoff_start >= date(year, 1, 1),
            Payroll.cutoff_start <= date(year, 12, 31),
        ).all()
        gross = round(sum(float(record.gross_income or 0) for record in records), 2)
        tax = round(sum(float(record.withholding_tax or 0) for record in records), 2)
        if report_type == 'sss_r3':
            identifier = employee.sss or 'N/A'
            contribution = round(sum(float(record.sss or 0) for record in records), 2)
        elif report_type == 'philhealth_rf1':
            identifier = employee.philhealth or 'N/A'
            contribution = round(sum(float(record.philhealth or 0) for record in records), 2)
        elif report_type == 'pagibig_mcrf':
            identifier = employee.pagibig or 'N/A'
            contribution = round(sum(float(record.pagibig or 0) for record in records), 2)
        else:
            identifier = employee.tin or 'N/A'
            contribution = tax
        rows.append({
            'employee': employee,
            'identifier': identifier,
            'compensation': gross,
            'contribution': contribution,
            'periods': len(records),
        })
    return rows


def compliance_report_pdf(report_type, year, company, rows, verification):
    report = COMPLIANCE_REPORTS[report_type]
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=landscape(letter))
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawString(35, 555, report['title'])
    pdf.setFont('Helvetica', 9)
    pdf.drawString(35, 540, f'Year: {year} | Company: {company or "All Companies"}')
    pdf.drawString(35, 526, 'HRIS-prepared report. Validate against the agency-prescribed form before filing.')
    headers = report['columns']
    positions = (35, 220, 390, 540)
    pdf.setFont('Helvetica-Bold', 8)
    for position, header in zip(positions, headers):
        pdf.drawString(position, 500, header)
    y = 482
    pdf.setFont('Helvetica', 8)
    for row in rows:
        if y < 65:
            pdf.showPage()
            y = 750
            pdf.setFont('Helvetica', 8)
        pdf.drawString(positions[0], y, row['employee'].full_name()[:28])
        pdf.drawString(positions[1], y, row['identifier'][:24])
        pdf.drawRightString(positions[2] + 85, y, f'PHP {row["compensation"]:,.2f}')
        pdf.drawRightString(positions[3] + 85, y, f'PHP {row["contribution"]:,.2f}')
        y -= 15
    pdf.setFont('Helvetica-Oblique', 8)
    pdf.drawString(35, 32, f'Document ID: {verification["document_id"]}')
    pdf.drawString(35, 20, 'Scan QR to verify this HRIS report.')
    qr_bytes = generate_qr_image_bytes(verification['verify_url'])
    if qr_bytes:
        pdf.drawImage(ImageReader(io.BytesIO(qr_bytes)), 700, 10, width=60, height=60)
    pdf.save()
    buffer.seek(0)
    return buffer


@app.route('/admin/compliance-reports')
@login_required
def compliance_reports():
    if 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    try:
        year = int(request.args.get('year', datetime.today().year))
    except (TypeError, ValueError):
        abort(400)
    if year < 2000 or year > datetime.today().year + 1:
        abort(400)
    return render_template(
        'compliance_reports.html',
        year=year,
        companies=('All Companies', 'Trece-Uno', 'Auto Expert'),
        reports=COMPLIANCE_REPORTS,
    )


@app.route('/admin/compliance-reports/<string:report_type>/download')
@login_required
def download_compliance_report(report_type):
    if 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    if report_type not in COMPLIANCE_REPORTS:
        abort(404)
    try:
        year = int(request.args.get('year', datetime.today().year))
    except (TypeError, ValueError):
        abort(400)
    company = request.args.get('company')
    company = company if company in {'Trece-Uno', 'Auto Expert'} else None
    rows = build_compliance_report_rows(report_type, year, company)
    total_contribution = sum(row['contribution'] for row in rows)
    verification = build_document_verification(
        report_type,
        current_user.id,
        f'{report_type}-{year}-{company or "all"}',
        cutoff_start=date(year, 1, 1),
        cutoff_end=date(year, 12, 31),
        net_pay=total_contribution,
    )
    return send_file(
        compliance_report_pdf(report_type, year, company, rows, verification),
        as_attachment=True,
        download_name=f'{report_type.upper()}_{year}.pdf',
        mimetype='application/pdf',
    )


@app.route('/admin/tax-reports', methods=['GET', 'POST'])
@login_required
def tax_reports():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    companies = ('Trece-Uno', 'Auto Expert')
    if request.method == 'POST':
        company = request.form.get('company')
        if company not in companies:
            abort(400)
        profile = EmployerTaxProfile.query.filter_by(company=company).first()
        if profile is None:
            profile = EmployerTaxProfile(company=company)
            db.session.add(profile)
        profile.legal_employer_entity = request.form.get('legal_employer_entity', '').strip() or None
        profile.registered_company_name = request.form.get('registered_company_name', '').strip() or None
        profile.employer_tin = request.form.get('employer_tin', '').strip() or None
        profile.registered_business_address = request.form.get('registered_business_address', '').strip() or None
        profile.authorized_signatory_name = request.form.get('authorized_signatory_name', '').strip() or None
        profile.authorized_signatory_position = request.form.get('authorized_signatory_position', '').strip() or None
        profile.tax_year = request.form.get('tax_year', type=int) or datetime.now().year
        db.session.commit()
        flash(f'{company} employer tax details saved.', 'success')
        return redirect(url_for('tax_reports'))
    profiles = {company: employer_tax_details(company) for company in companies}
    return render_template('tax_reports.html', current_year=datetime.now().year, profiles=profiles)


@app.route('/admin/tax-reports/<string:company>/monthly/<string:month>')
@login_required
def download_monthly_tax_summary(company, month):
    if not can_manage_admin_payroll(current_user) or company not in {'Trece-Uno', 'Auto Expert'}:
        return 'Access denied', 403
    try:
        year, month_number = map(int, month.split('-'))
        if not 1 <= month_number <= 12:
            raise ValueError
    except ValueError:
        abort(400)
    records = company_tax_records(company, year, month_number)
    return send_file(tax_summary_pdf('Monthly Withholding Tax Summary', company, month, records), as_attachment=True,
                     download_name=f'Monthly_Tax_Summary_{company}_{month}.pdf', mimetype='application/pdf')


@app.route('/admin/tax-reports/<string:company>/annual/<int:year>')
@login_required
def download_annual_tax_summary(company, year):
    if not can_manage_admin_payroll(current_user) or company not in {'Trece-Uno', 'Auto Expert'}:
        return 'Access denied', 403
    records = company_tax_records(company, year)
    return send_file(tax_summary_pdf('Annual Withholding Tax Summary', company, str(year), records), as_attachment=True,
                     download_name=f'Annual_Tax_Summary_{company}_{year}.pdf', mimetype='application/pdf')


CAVITE_MINIMUM_DAILY_WAGE = 600.0


def is_minimum_wage_earner(employee):
    return (
        0 < float(getattr(employee, 'daily_rate', 0) or 0)
        <= CAVITE_MINIMUM_DAILY_WAGE
    )


def weekly_withholding_tax(employee, monthly_taxable_income, weeks_in_month):
    if is_minimum_wage_earner(employee):
        return 0.0
    return round(
        compute_withholding_tax(monthly_taxable_income) / weeks_in_month,
        2,
    )


def attendance_issue_rows(employee, cutoff_start, cutoff_end):
    attendance_rows = Attendance.query.filter(
        Attendance.employee_id == employee.id,
        Attendance.date >= cutoff_start,
        Attendance.date <= cutoff_end,
    ).order_by(Attendance.date).all()
    issues_by_date = {}
    for attendance in attendance_rows:
        if str(attendance.status or '').strip().lower() == 'absent':
            issues_by_date.setdefault(attendance.date, []).append('Absent')
            continue
        issues = issues_by_date.setdefault(attendance.date, [])
        if attendance.clock_in and attendance.clock_in.time() > time(8, 10):
            issues.append('Late')
        if attendance.clock_in and attendance.clock_out:
            scheduled_hours = (
                4.0
                if is_trece_sunday(employee, attendance.date)
                else 8.0 if attendance.date.weekday() != 6 else None
            )
            hours = regular_attendance_hours(attendance)
            if scheduled_hours is not None and hours < scheduled_hours:
                issues.append(
                    'Half-day' if hours <= scheduled_hours / 2 else 'Undertime'
                )
        if not issues:
            issues_by_date.pop(attendance.date, None)
    return [
        {
            'date': issue_date,
            'issues': issue_list,
        }
        for issue_date, issue_list in sorted(issues_by_date.items())
    ]


def build_company_payroll_summary(company, cutoff_start, cutoff_end, include_admins=False):
    if include_admins:
        company_filter = (
            Employee.company.in_(['Trece', 'Trece-Uno'])
            if company == 'Trece-Uno'
            else Employee.company == 'Auto Expert'
        )
        employees = Employee.query.filter(
            company_filter,
            Employee.payroll_attendance_exempt.is_(False),
        ).order_by(Employee.last_name, Employee.first_name).all()
    else:
        employees = Employee.query.filter(company_employee_filter(company)).order_by(
            Employee.last_name, Employee.first_name
        ).all()
    rows = []
    for emp in employees:
        attendance = payroll_attendance_records(emp, cutoff_start, cutoff_end)
        daily_rate = float(emp.daily_rate or 0)
        basic_pay = sum(regular_day_pay(record, daily_rate) for record in attendance)
        overtime_pay = sum(
            payroll_extra_pay(record, daily_rate)
            for record in attendance
            if attendance_overtime_is_payable(record)
        )
        worked_days_count = payroll_worked_days_count(emp, attendance)
        weeks_in_month = payroll_cutoffs_in_month(cutoff_start)
        rice_days, rice_monthly_used = rice_allowance_month_context(
            emp, cutoff_start, worked_days_count
        )
        rice_exempt, rice_taxable = de_minimis_allowance_breakdown(
            emp,
            rice_days,
            weeks_in_month,
            rice_monthly_used=rice_monthly_used,
        )
        gross_income = basic_pay + float(emp.allowance or 0) + float(emp.incentives or 0) + rice_taxable + overtime_pay
        contribution_salary = basic_pay + rice_taxable
        contributions = cutoff_statutory_contributions(emp, cutoff_start, contribution_salary)
        deductions = contributions['employee']
        employer_deductions = contributions['employer']
        payroll_record = Payroll.query.filter_by(
            employee_id=emp.id,
            cutoff_start=cutoff_start,
            cutoff_end=cutoff_end - timedelta(days=1)
        ).first()
        loan = automatic_loan_deduction(emp, payroll_record, cutoff_start)
        sss_loan = float(payroll_record.sss_loan or 0) if payroll_record else 0.0
        pagibig_loan = (
            float(payroll_record.pagibig_loan or 0) if payroll_record else 0.0
        )
        liability_deduction = (
            float(payroll_record.liability_deduction_override)
            if payroll_record is not None
            and payroll_record.liability_deduction_override is not None
            else liability_cutoff_deduction(emp.id)
        )
        monthly_taxable_income = contribution_taxable_month(
            contributions, gross_income, weeks_in_month
        )
        withholding_tax = weekly_withholding_tax(
            emp, monthly_taxable_income, weeks_in_month
        )
        total_deductions = (
            deductions['sss'] + deductions['philhealth'] + deductions['pagibig']
            + sss_loan + pagibig_loan + loan + liability_deduction
            + withholding_tax
        )
        rows.append({
            'employee': emp,
            'is_admin': 'admin' in str(emp.role or '').lower(),
            'worked_days': worked_days_count,
            'gross_income': gross_income,
            'sss': deductions['sss'],
            'philhealth': deductions['philhealth'],
            'pagibig': deductions['pagibig'],
            'sss_loan': sss_loan,
            'pagibig_loan': pagibig_loan,
            'contribution_salary_base': contributions['basis'],
            'employer_sss': employer_deductions['sss'],
            'employer_sss_ec': employer_deductions['sss_ec'],
            'employer_philhealth': employer_deductions['philhealth'],
            'employer_pagibig': employer_deductions['pagibig'],
            'loan': loan,
            'liability_deduction': liability_deduction,
            'withholding_tax': withholding_tax,
            'total_deductions': total_deductions,
            'net_pay': gross_income + rice_exempt - total_deductions,
            'payroll_record': payroll_record,
        })
        snapshot_record = payroll_record or Payroll(employee_id=emp.id)
        if payroll_record and payroll_record.is_paid:
            for field in (
                'gross_income', 'total_deductions', 'net_pay', 'sss', 'philhealth',
                'pagibig', 'withholding_tax', 'liability_deduction',
                'contribution_salary_base', 'employer_sss', 'employer_sss_ec',
                'employer_philhealth', 'employer_pagibig',
            ):
                rows[-1][field] = float(getattr(payroll_record, field) or 0)
        else:
            rows[-1].update(
                calculate_payroll_snapshot(emp, snapshot_record, cutoff_start, cutoff_end)
            )
        row = rows[-1]
        row['other_deductions_total'] = max(
            row['total_deductions'] - row['sss'] - row['philhealth'] - row['pagibig']
            - row['withholding_tax'] - row['loan'],
            0.0,
        )
    rows.sort(key=lambda row: (not row['is_admin'], row['employee'].last_name or '', row['employee'].first_name or ''))
    return rows


def build_thirteenth_month_rows(year, employee_id=None):
    query = Employee.query.filter(Employee.role.ilike('%staff%'))
    if employee_id is not None:
        query = query.filter(Employee.id == employee_id)
    employees = query.order_by(Employee.last_name, Employee.first_name).all()
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    rows = []

    for employee in employees:
        attendance_records = Attendance.query.filter(
            Attendance.employee_id == employee.id,
            Attendance.date >= year_start,
            Attendance.date <= year_end,
            Attendance.clock_out != None,
        ).all()
        basic_pay = sum(
            regular_day_pay(record, float(employee.daily_rate or 0))
            for record in attendance_records
        )
        payroll_records = Payroll.query.filter(
            Payroll.employee_id == employee.id,
            Payroll.cutoff_start >= year_start,
            Payroll.cutoff_start <= year_end,
        ).all()
        if not attendance_records:
            basic_pay = sum(float(record.gross_income or 0) for record in payroll_records)
        rows.append({
            'employee': employee,
            'basic_pay': round(basic_pay, 2),
            'thirteenth_month': round(basic_pay / 12, 2),
            'payroll_periods': len(payroll_records),
        })
    return rows


def loan_limit_breakdown(employee):
    today = datetime.today()
    year_start = date(today.year, 1, 1)
    attendance_days = Attendance.query.filter(
        Attendance.employee_id == employee.id,
        Attendance.clock_out != None,
        Attendance.clock_in >= datetime.combine(year_start, time.min),
    ).count()
    base_limit = 3000.0
    attendance_component = float(employee.daily_rate or 0) * attendance_days / 12
    thirteenth_rows = build_thirteenth_month_rows(today.year, employee.id)
    thirteenth_month = float(thirteenth_rows[0]['thirteenth_month']) if thirteenth_rows else 0.0
    thirteenth_component = round(thirteenth_month * 0.50, 2)
    outstanding = float(employee.loan_balance or 0)
    gross_limit = base_limit + attendance_component + thirteenth_component
    return {
        'base_limit': round(base_limit, 2),
        'attendance_component': round(attendance_component, 2),
        'thirteenth_month': round(thirteenth_month, 2),
        'thirteenth_component': thirteenth_component,
        'outstanding': round(outstanding, 2),
        'gross_limit': round(gross_limit, 2),
        'remaining_limit': round(max(0, gross_limit - outstanding), 2),
    }


def thirteenth_month_pdf(year, rows, verification):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont('Helvetica-Bold', 15)
    pdf.drawString(50, 770, '13TH-MONTH PAY REPORT')
    pdf.setFont('Helvetica', 10)
    pdf.drawString(50, 750, f'Calendar Year: {year}')
    pdf.drawString(50, 735, 'Basis: prorated basic salary earned during the calendar year divided by 12')
    pdf.drawString(50, 720, 'Unpaid absences, late time, half-day, undertime, and HWOP reduce the basis only')
    pdf.drawString(50, 705, 'when they reduce earned basic pay; they are not deducted again from 13th-month pay')
    pdf.drawString(50, 690, 'Excluded from basis: overtime, allowances, incentives, statutory deductions, and loans')

    y = 655
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(50, y, 'Employee')
    pdf.drawString(300, y, 'Basic Pay Earned')
    pdf.drawString(440, y, '13th-Month Pay')
    y -= 18
    pdf.setFont('Helvetica', 9)
    for row in rows:
        if y < 90:
            pdf.showPage()
            y = 750
            pdf.setFont('Helvetica', 9)
        pdf.drawString(50, y, row['employee'].full_name()[:36])
        pdf.drawRightString(390, y, f'PHP {row["basic_pay"]:,.2f}')
        pdf.drawRightString(540, y, f'PHP {row["thirteenth_month"]:,.2f}')
        y -= 16

    total_basic = sum(row['basic_pay'] for row in rows)
    total_thirteenth = sum(row['thirteenth_month'] for row in rows)
    pdf.line(50, max(y, 70), 540, max(y, 70))
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(50, max(y - 16, 54), 'TOTAL')
    pdf.drawRightString(390, max(y - 16, 54), f'PHP {total_basic:,.2f}')
    pdf.drawRightString(540, max(y - 16, 54), f'PHP {total_thirteenth:,.2f}')
    pdf.setFont('Helvetica-Oblique', 8)
    pdf.drawString(50, 35, f'Document ID: {verification["document_id"]}')
    pdf.drawString(50, 23, 'Scan QR to verify this report.')
    qr_bytes = generate_qr_image_bytes(verification['verify_url'])
    if qr_bytes:
        pdf.drawImage(ImageReader(io.BytesIO(qr_bytes)), 470, 12, width=60, height=60)
    pdf.save()
    buffer.seek(0)
    return buffer


@app.route('/13th-month')
@login_required
def thirteenth_month():
    try:
        year = int(request.args.get('year', datetime.today().year))
    except (TypeError, ValueError):
        abort(400)
    if year < 2000 or year > datetime.today().year + 1:
        abort(400)

    has_payroll_access = can_manage_admin_payroll(current_user)
    rows = build_thirteenth_month_rows(year, None if has_payroll_access else current_user.id)
    total_basic = sum(row['basic_pay'] for row in rows)
    total_thirteenth = sum(row['thirteenth_month'] for row in rows)
    if request.args.get('download') == 'true':
        verification = build_document_verification(
            'thirteenth_month',
            current_user.id,
            f'13thMonth-{year}-{"all" if has_payroll_access else current_user.id}',
            cutoff_start=date(year, 1, 1),
            cutoff_end=date(year, 12, 31),
            net_pay=total_thirteenth,
        )
        return send_file(
            thirteenth_month_pdf(year, rows, verification),
            as_attachment=True,
            download_name=f'13th_Month_Pay_{year}.pdf',
            mimetype='application/pdf',
        )
    return render_template(
        'thirteenth_month.html',
        rows=rows,
        year=year,
        total_basic=total_basic,
        total_thirteenth=total_thirteenth,
        is_admin=has_payroll_access,
    )


def payroll_summary_pdf(company, cutoff_start, cutoff_end, rows, employee_id):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=landscape(letter))
    pdf.setFont('Helvetica-Bold', 15)
    pdf.drawString(30, 560, f'{company.upper()} PAYROLL SUMMARY')
    pdf.setFont('Helvetica', 9)
    pdf.drawString(30, 544, f'Cutoff: {cutoff_start} to {cutoff_end - timedelta(days=1)}')
    pdf.drawString(550, 544, 'Address: Trece Martires Cavite, Philippines')
    pdf.drawString(550, 532, 'Prepared by: Admin')
    headers = ['Employee', 'Days', 'Gross', 'SSS', 'PhilHealth', 'Pag-IBIG', 'Tax', 'Loan', 'Other Ded.', 'Deductions', 'Net Pay', 'Status']
    x_positions = [25, 165, 195, 250, 297, 350, 400, 445, 495, 552, 622, 700]
    pdf.setFont('Helvetica-Bold', 8)
    for x, header in zip(x_positions, headers):
        pdf.drawString(x, 520, header)
    y = 503
    pdf.setFont('Helvetica', 8)
    current_group = None
    for row in rows:
        group = 'Admin Payroll' if row['is_admin'] else 'Staff Payroll'
        if group != current_group:
            if y < 58:
                break
            pdf.setFont('Helvetica-Bold', 9)
            pdf.drawString(30, y, group)
            y -= 14
            current_group = group
            pdf.setFont('Helvetica', 8)
        values = [
            row['employee'].full_name()[:28], str(row['worked_days']),
            f"{row['gross_income']:,.2f}", f"{row['sss']:,.2f}",
            f"{row['philhealth']:,.2f}", f"{row['pagibig']:,.2f}",
            f"{row['withholding_tax']:,.2f}", f"{row['loan']:,.2f}",
            f"{row.get('other_deductions_total', 0):,.2f}", f"{row['total_deductions']:,.2f}",
            f"{row['net_pay']:,.2f}", 'PAID' if row['payroll_record'] and row['payroll_record'].is_paid else 'UNPAID'
        ]
        for x, value in zip(x_positions, values):
            pdf.drawString(x, y, value)
        y -= 18
        if y < 45:
            break
    pdf.line(30, y - 5, 760, y - 5)
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(30, y - 22, 'Total Net Pay')
    pdf.drawRightString(760, y - 22, f"PHP {sum(row['net_pay'] for row in rows):,.2f}")
    verification = build_document_verification(
        'payroll_summary',
        employee_id,
        f'{company}-{cutoff_start}',
        cutoff_start=cutoff_start,
        cutoff_end=cutoff_end - timedelta(days=1),
        net_pay=float(sum(row['net_pay'] for row in rows)),
    )
    pdf.setFont('Helvetica-Oblique', 8)
    pdf.drawString(520, 30, f'Document ID: {verification["document_id"]}')
    pdf.drawString(520, 20, 'Scan QR to verify.')
    if qrcode is not None:
        qr_bytes = generate_qr_image_bytes(verification['verify_url'])
        if qr_bytes:
            pdf.drawImage(ImageReader(io.BytesIO(qr_bytes)), 665, 12, width=60, height=60)
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def build_document_verification(document_type, employee_id, label, reference_id=None, cutoff_start=None, cutoff_end=None, net_pay=0.0):
    if isinstance(cutoff_start, str):
        try:
            cutoff_start = datetime.strptime(cutoff_start, '%Y-%m-%d').date()
        except ValueError:
            cutoff_start = None
    if isinstance(cutoff_end, str):
        try:
            cutoff_end = datetime.strptime(cutoff_end, '%Y-%m-%d').date()
        except ValueError:
            cutoff_end = None

    verification_filters = {
        'employee_id': employee_id, 'document_type': document_type,
        'document_label': label, 'payroll_id': reference_id,
    }
    if document_type == 'payslip':
        verification_filters['net_pay'] = float(net_pay or 0)
    existing = PayslipVerification.query.filter_by(**verification_filters).first()
    if existing is not None:
        verification_id = existing.document_id
        verification_hash = existing.verification_hash
    else:
        verification_id = f"{document_type.upper()[:3]}-{str(uuid.uuid4())[:8].upper()}"
        payload = f"{document_type}|{employee_id}|{label}|{reference_id or 'none'}|{cutoff_start or 'none'}|{cutoff_end or 'none'}|{float(net_pay or 0)}|{app.secret_key}"
        verification_hash = hashlib.sha256(payload.encode()).hexdigest()
        record = PayslipVerification(
            employee_id=employee_id,
            payroll_id=reference_id,
            document_type=document_type,
            document_id=verification_id,
            document_label=label,
            net_pay=float(net_pay or 0),
            cutoff_start=cutoff_start,
            cutoff_end=cutoff_end,
            verification_hash=verification_hash,
        )
        db.session.add(record)
        db.session.commit()

    if has_request_context():
        verify_url = url_for('verify_document', verification_id=verification_id, _external=True)
    else:
        verify_url = f"https://railway.app/verify-document/{verification_id}"
    return {
        'document_id': verification_id,
        'employee_id': employee_id,
        'document_type': document_type,
        'document_label': label,
        'reference_id': reference_id,
        'cutoff_start': cutoff_start,
        'cutoff_end': cutoff_end,
        'net_pay': float(net_pay or 0),
        'verify_url': verify_url,
        'verification_hash': verification_hash,
    }


def build_payslip_verification(employee_id, payroll_id, cutoff_start, cutoff_end, net_pay):
    return build_document_verification(
        'payslip',
        employee_id,
        f'Payslip-{payroll_id}',
        reference_id=payroll_id,
        cutoff_start=cutoff_start,
        cutoff_end=cutoff_end,
        net_pay=net_pay,
    )


HR_DOCUMENT_TYPES = {
    'NTE': 'Notice to Explain',
    'NDE': 'Notice of Decision',
    'Memo': 'Company Memo',
    'Resolution': 'Case Resolution',
    'Contract': 'Employment Contract / Agreement',
    'Warning': 'Written Warning',
    'PIP': 'Performance Improvement Plan',
    'Suspension': 'Suspension Notice',
    'Return to Work': 'Return to Work Order',
    'Liability Agreement': 'Recoverable Deduction / Liability Agreement',
    'Loan Deduction Agreement': 'Employee Loan Payroll Deduction Authorization',
    'COE': 'Certificate of Employment',
    'Policy Acknowledgment': 'Policy Acknowledgment',
}


def default_hr_document_body(document_type, employee):
    employee_name = employee.full_name() if employee else '[Employee Name]'
    templates = {
        'NTE': f'Dear {employee_name},\n\nYou are hereby required to submit a written explanation regarding the matter stated below. Please explain your side and provide supporting details or documents on or before the response due date.\n\nFacts / Details:\n[Describe incident, date, policy, and relevant facts.]\n\nThis notice is issued for documentation and due process purposes.',
        'NDE': f'Dear {employee_name},\n\nAfter review of the records, explanation, and available evidence, management has reached the following decision.\n\nDecision / Action:\n[State decision, corrective action, warning, suspension, or closure.]\n\nThis document forms part of the employee record.',
        'Memo': 'This memo is issued to document the following company instruction, reminder, or announcement.\n\nDetails:\n[Write memo details here.]',
        'Resolution': 'This resolution records the findings and closure/action for the matter referenced below.\n\nFindings:\n[State findings.]\n\nResolution:\n[State action or closure.]',
        'PIP': f'Performance Improvement Plan for {employee_name}\n\nReason / Evaluation Result:\n[State failed evaluation score, performance gap, attendance issue, or policy concern.]\n\nSupport and Coaching Provided:\n[State coaching, training, mentoring, reminders, tools, or schedule support given by the company.]\n\nImprovement Targets:\n1. [Target 1]\n2. [Target 2]\n3. [Target 3]\n\nReview Period and Check-ins:\n[State start date, review dates, and final review date.]\n\nExpected Outcome:\nThe employee is given an opportunity to improve and meet company standards. Progress will be reviewed fairly based on documented performance, attendance, behavior, and policy compliance.',
        'Contract': f'This agreement is entered into by the company and {employee_name}.\n\nTerms and Conditions:\n[Write agreed terms here.]\n\nBoth parties acknowledge and agree to the terms stated in this document.',
        'Liability Agreement': f'This recoverable deduction/liability agreement documents an employee accountability or uncollected company receivable involving {employee_name}.\n\nThe deduction shall follow the agreed schedule. If the customer or account pays the outstanding receivable, the corresponding deducted amount shall be refunded or released to the employee based on actual recovery and company records.',
        'Loan Deduction Agreement': (
            f'EMPLOYEE LOAN PAYROLL DEDUCTION AUTHORIZATION\n\n'
            f'Employee: {employee_name}\n'
            f'Company: {employee.company or "[Company Name]"}\n'
            'Loan reference / approval date: [Enter approved loan reference and date]\n'
            'Approved loan amount: PHP [Enter approved amount]\n'
            'Agreed deduction per cutoff: PHP [Enter PHP 500.00 or more]\n'
            'First deduction cutoff: [Enter cutoff]\n\n'
            f'I, {employee_name}, voluntarily authorize {employee.company or "[Company Name]"} '
            'to deduct the agreed amount from my salary for repayment of this approved employee loan. '
            'The deduction will be at least PHP 500.00 per payroll cutoff, except for the final '
            'deduction when the remaining loan balance is less than PHP 500.00. No deduction will '
            'exceed the remaining loan balance. Deductions are subject to applicable law and '
            'required limits on wage deductions.\n\n'
            'I have reviewed the loan amount and deduction schedule above, understand the terms, '
            'and authorize the deductions until the loan is fully paid. I may request an updated '
            'loan balance and deduction record from the company.'
        ),
    }
    return templates.get(document_type, 'Document details:\n[Write complete details here.]')


def hr_document_footer_note(document_type):
    if document_type == 'Loan Deduction Agreement':
        return (
            'Loan deductions are limited to the amount and cutoff schedule authorized above, '
            'subject to applicable law.'
        )
    if document_type == 'Liability Agreement':
        return (
            'Any deduction is limited to the amount and terms stated in the attached agreement, '
            'subject to applicable law.'
        )
    if document_type in {'NTE', 'NDE', 'Warning', 'PIP', 'Suspension', 'Resolution'}:
        return (
            'Acknowledgment confirms receipt only and does not automatically mean admission of '
            'fault unless expressly stated.'
        )
    return None


def hr_document_pdf(document):
    employee = document.employee
    prepared_by = document.creator.full_name() if document.creator else 'Admin / HR'
    try:
        ensure_document_verification_columns()
        verification = build_document_verification(
            'hr_document', employee.id, f'HRDocument-{document.id}-{document.document_type}',
            reference_id=None,
            cutoff_start=document.created_at.date() if document.created_at else datetime.utcnow().date(),
            cutoff_end=document.response_due_date or document.effective_date or (document.created_at.date() if document.created_at else datetime.utcnow().date()),
            net_pay=0.0,
        )
        if document.document_id != verification['document_id']:
            document.document_id = verification['document_id']
            db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('HR document verification failed; generating fallback PDF for document %s', document.id)
        verification = {
            'document_id': document.document_id or f'HRD-{document.id:06d}',
            'verify_url': None,
        }
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawString(50, 780, payroll_company_name(employee))
    pdf.setFont('Helvetica-Bold', 13)
    pdf.drawString(50, 758, HR_DOCUMENT_TYPES.get(document.document_type, document.document_type).upper())
    pdf.setFont('Helvetica', 10)
    pdf.drawString(50, 735, f'Document ID: {verification["document_id"]}')
    pdf.drawString(50, 720, f'Employee: {employee.full_name()} (ID: {employee.id})')
    pdf.drawString(50, 705, f'Subject: {document.subject}')
    pdf.drawString(50, 690, f'Status: {document.status}')
    pdf.drawString(300, 735, f'Prepared by: {prepared_by[:34]}')
    pdf.drawString(300, 720, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    if document.issued_at:
        pdf.drawString(300, 705, f'Issued: {document.issued_at.strftime("%Y-%m-%d %H:%M")}')
    if document.acknowledged_at:
        pdf.drawString(300, 690, f'Acknowledged: {document.acknowledged_at.strftime("%Y-%m-%d %H:%M")}')
    if document.employee_response:
        pdf.drawString(300, 675, f'Responded: {document.updated_at.strftime("%Y-%m-%d %H:%M") if document.updated_at else "Recorded"}')
    y = 640
    text_object = pdf.beginText(50, y)
    text_object.setFont('Helvetica', 10)
    for paragraph in (document.body or '').splitlines():
        for start in range(0, max(len(paragraph), 1), 92):
            text_object.textLine(paragraph[start:start + 92])
            y -= 13
            if y < 130:
                pdf.drawText(text_object)
                pdf.showPage()
                y = 760
                text_object = pdf.beginText(50, y)
                text_object.setFont('Helvetica', 10)
    if document.employee_response:
        text_object.textLine('')
        text_object.textLine('Employee Response / Explanation:')
        for paragraph in document.employee_response.splitlines():
            for start in range(0, max(len(paragraph), 1), 92):
                text_object.textLine(paragraph[start:start + 92])
    pdf.drawText(text_object)
    pdf.setFont('Helvetica', 8)
    footer_note = hr_document_footer_note(document.document_type)
    if footer_note:
        pdf.drawString(50, 112, footer_note)
    pdf.line(45, 92, 205, 92)
    pdf.line(225, 92, 385, 92)
    pdf.line(405, 92, 555, 92)
    pdf.drawString(45, 76, 'Employee Signature / Date')
    pdf.drawString(225, 76, 'Authorized Employer Signature / Date')
    pdf.drawString(405, 76, 'HR / Witness Signature / Date')
    if document.employee_signed_at:
        pdf.showPage()
        pdf.setFont('Helvetica-Bold', 13)
        pdf.drawString(50, 750, 'EMPLOYEE ELECTRONIC SIGNATURE RECORD')
        pdf.setFont('Helvetica', 10)
        pdf.drawString(50, 728, f'Employee: {document.employee_signature_name or employee.full_name()}')
        pdf.drawString(50, 712, f'Signed: {document.employee_signed_at.strftime("%Y-%m-%d %H:%M UTC")}')
        pdf.drawString(50, 696, f'Method: {document.employee_signature_method or "Electronic"}')
        if document.attachment_sha256:
            pdf.drawString(50, 680, f'Original attachment SHA-256: {document.attachment_sha256}')
        if document.wet_signed_sha256:
            pdf.drawString(50, 664, f'Wet-signed copy SHA-256: {document.wet_signed_sha256}')
            pdf.drawString(
                50, 648,
                f'Wet-signed copy recorded by: {document.wet_signer.full_name() if document.wet_signer else "Admin"}',
            )
        text = pdf.beginText(50, 620)
        text.setFont('Helvetica', 10)
        for paragraph in (document.employee_signature_statement or '').splitlines():
            for start in range(0, max(len(paragraph), 1), 92):
                text.textLine(paragraph[start:start + 92])
        pdf.drawText(text)
        if document.employee_signature_image:
            import base64
            try:
                encoded = document.employee_signature_image.split(',', 1)[1]
                signature_bytes = base64.b64decode(encoded, validate=True)
                pdf.drawImage(
                    ImageReader(io.BytesIO(signature_bytes)),
                    50, 470, width=220, height=70, preserveAspectRatio=True, mask='auto',
                )
            except (OSError, ValueError, TypeError, base64.binascii.Error):
                logger.warning('Could not render electronic signature for HR document %s', document.id)
        pdf.line(50, 455, 300, 455)
        pdf.drawString(50, 438, f'{document.employee_signature_name or employee.full_name()} / Employee')
        pdf.drawString(50, 422, 'This page records the signature or wet-signed copy held in HRIS.')
    qr_bytes = generate_qr_image_bytes(verification['verify_url']) if verification.get('verify_url') else None
    if qr_bytes:
        pdf.drawImage(ImageReader(io.BytesIO(qr_bytes)), 455, 12, width=70, height=70)
    pdf.setFont('Helvetica-Oblique', 8)
    pdf.drawString(50, 36, 'Scan QR to verify this HRIS-generated document.')
    pdf.save()
    buffer.seek(0)
    return buffer


HR_DOCUMENT_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def hr_document_signature_statement(document_type):
    if document_type == 'Loan Deduction Agreement':
        return (
            'I voluntarily authorize the stated salary deduction for my approved employee loan: '
            'at least PHP 500.00 per payroll cutoff, except when the remaining balance is less '
            'than PHP 500.00 for the final deduction. I understand deductions are subject to '
            'applicable law and required limits on wage deductions.'
        )
    if document_type == 'Liability Agreement':
        return (
            'I confirm that I received and reviewed this liability/deduction agreement. '
            'I voluntarily authorize only the deductions specifically stated in the attached agreement, '
            'subject to applicable law and the stated terms.'
        )
    if document_type in {'Contract', 'Policy Acknowledgment'}:
        return (
            'I confirm that I received and reviewed this document and agree to the terms or policy '
            'acknowledgment stated in it.'
        )
    return (
        'I confirm receipt of this document. My signature acknowledges receipt only and does not '
        'by itself mean admission of fault or agreement with its contents.'
    )


def read_hr_pdf_upload(upload):
    original_name = secure_filename(upload.filename or '')
    if not original_name or not original_name.lower().endswith('.pdf'):
        raise ValueError('Upload a PDF file.')
    file_bytes = upload.stream.read(HR_DOCUMENT_MAX_ATTACHMENT_BYTES + 1)
    if len(file_bytes) > HR_DOCUMENT_MAX_ATTACHMENT_BYTES:
        raise ValueError('The uploaded PDF must be 10 MB or smaller.')
    if not file_bytes.startswith(b'%PDF-'):
        raise ValueError('The uploaded file is not a valid PDF.')
    return original_name, file_bytes


def store_hr_pdf(employee_id, file_bytes):
    folder = os.path.join(app.config['HR_DOCUMENTS_FOLDER'], str(employee_id))
    os.makedirs(folder, exist_ok=True)
    filename = f'{secrets.token_hex(16)}.pdf'
    path = os.path.join(folder, filename)
    with open(path, 'xb') as uploaded_file:
        uploaded_file.write(file_bytes)
    return filename, path


def acknowledge_hr_document_liability(document, signed_at):
    if document.document_type != 'Liability Agreement':
        return
    liability = document.liability
    if (
        liability is None
        or liability.employee_id != document.employee_id
        or liability.status not in {'Active', 'Partially Recovered'}
        or liability.remaining_balance <= 0
    ):
        abort(409)
    liability.acknowledgment_status = 'Acknowledged'
    if liability.agreement_date is None:
        liability.agreement_date = signed_at.date()
    liability.updated_at = signed_at


@app.route('/hr-documents', methods=['GET', 'POST'])
@login_required
def hr_documents():
    is_admin = 'admin' in str(current_user.role or '').lower()
    if request.method == 'POST':
        action = request.form.get('action')
        if action in {'respond', 'sign'}:
            document = HRDocument.query.get_or_404(request.form.get('document_id', type=int))
            if is_admin or document.employee_id != current_user.id:
                return 'Access denied', 403
            if action == 'respond':
                if document.status == 'Draft':
                    return 'Draft documents are not available to employees.', 409
                employee_response = request.form.get('employee_response', '').strip()
                if not employee_response:
                    flash('Enter your response before submitting.', 'danger')
                    return redirect(url_for('hr_documents'))
                document.employee_response = employee_response
                document.status = 'Responded'
                flash('Response submitted.', 'success')
            else:
                if not document.signature_required or document.status != 'Issued' or document.employee_signed_at:
                    return 'Document is not awaiting an employee signature.', 409
                signature_method = request.form.get('signature_method')
                signature_name = request.form.get('signature_name', '').strip()
                expected_name = current_user.full_name().strip()
                if (
                    request.form.get('signature_confirmation') != '1'
                    or not signature_name
                    or signature_name.casefold() != expected_name.casefold()
                    or signature_method not in {'drawn', 'typed'}
                ):
                    flash('Enter your account name, choose a signature method, and confirm the statement.', 'danger')
                    return redirect(url_for('hr_documents'))

                signature_image = None
                if signature_method == 'drawn':
                    signature_data = request.form.get('signature_image', '')
                    if len(signature_data) > 400_000 or not signature_data.startswith('data:image/png;base64,'):
                        flash('Please draw your signature before submitting.', 'danger')
                        return redirect(url_for('hr_documents'))
                    import base64
                    try:
                        image_bytes = base64.b64decode(signature_data.split(',', 1)[1], validate=True)
                    except (ValueError, base64.binascii.Error):
                        flash('The drawn signature image is invalid. Please sign again.', 'danger')
                        return redirect(url_for('hr_documents'))
                    if (
                        len(image_bytes) < 24
                        or not image_bytes.startswith(b'\x89PNG\r\n\x1a\n')
                    ):
                        flash('The drawn signature image is invalid. Please sign again.', 'danger')
                        return redirect(url_for('hr_documents'))
                    image_width, image_height = struct.unpack('>II', image_bytes[16:24])
                    if not (0 < image_width <= 2400 and 0 < image_height <= 840):
                        flash('The drawn signature image dimensions are invalid.', 'danger')
                        return redirect(url_for('hr_documents'))
                    try:
                        ImageReader(io.BytesIO(image_bytes)).getRGBData()
                    except (OSError, ValueError, TypeError):
                        flash('The drawn signature image is invalid. Please sign again.', 'danger')
                        return redirect(url_for('hr_documents'))
                    signature_image = signature_data

                signed_at = datetime.utcnow()
                document.employee_signature_name = expected_name
                document.employee_signature_method = signature_method
                document.employee_signature_image = signature_image
                document.employee_signature_statement = hr_document_signature_statement(document.document_type)
                document.employee_signed_at = signed_at
                document.employee_signature_ip = request.remote_addr
                document.employee_signature_user_agent = request.user_agent.string[:255]
                document.acknowledged_at = signed_at
                document.status = 'Signed'
                acknowledge_hr_document_liability(document, signed_at)
                flash('Electronic signature recorded. You can download the original document and sign it on paper if needed.', 'success')
            document.updated_at = datetime.utcnow()
            db.session.commit()
            return redirect(url_for('hr_documents'))
        if action == 'record_wet_signature':
            if not is_admin:
                return 'Access denied', 403
            document = HRDocument.query.get_or_404(request.form.get('document_id', type=int))
            if not document.signature_required or document.status != 'Issued' or document.employee_signed_at:
                return 'Document is not awaiting an employee signature.', 409
            if document.document_type == 'Liability Agreement' and (
                document.liability is None
                or document.liability.employee_id != document.employee_id
                or document.liability.status not in {'Active', 'Partially Recovered'}
                or document.liability.remaining_balance <= 0
            ):
                return 'A liability agreement must have a matching active liability balance before signing.', 409
            upload = request.files.get('wet_signed_copy')
            if not upload or not upload.filename:
                flash('Upload the wet-signed PDF copy before recording it.', 'danger')
                return redirect(url_for('hr_documents'))
            try:
                original_name, file_bytes = read_hr_pdf_upload(upload)
            except ValueError as error:
                flash(str(error), 'danger')
                return redirect(url_for('hr_documents'))
            stored_path = None
            try:
                filename, stored_path = store_hr_pdf(document.employee_id, file_bytes)
                signed_at = datetime.utcnow()
                document.wet_signed_filename = filename
                document.wet_signed_original_name = original_name
                document.wet_signed_sha256 = hashlib.sha256(file_bytes).hexdigest()
                document.wet_signed_at = signed_at
                document.wet_signed_by = current_user.id
                document.employee_signature_name = document.employee.full_name()
                document.employee_signature_method = 'wet'
                document.employee_signature_statement = hr_document_signature_statement(document.document_type)
                document.employee_signed_at = signed_at
                document.acknowledged_at = signed_at
                document.status = 'Signed'
                document.updated_at = signed_at
                acknowledge_hr_document_liability(document, signed_at)
                db.session.commit()
            except (OSError, SQLAlchemyError):
                db.session.rollback()
                if stored_path and os.path.exists(stored_path):
                    os.remove(stored_path)
                logger.exception('Could not record wet-signed HR document %s', document.id)
                flash('The wet-signed copy could not be recorded. Please try again.', 'danger')
                return redirect(url_for('hr_documents'))
            flash('Wet-signed copy recorded and saved to the HR document record.', 'success')
            return redirect(url_for('hr_documents'))
        if action == 'issue':
            if not is_admin:
                return 'Access denied', 403
            document = HRDocument.query.get_or_404(request.form.get('document_id', type=int))
            if document.status != 'Draft':
                return 'Only draft documents can be issued.', 409
            if document.document_type == 'Liability Agreement' and (
                not document.signature_required
                or not document.attachment_filename
                or document.liability is None
                or document.liability.employee_id != document.employee_id
                or document.liability.status not in {'Active', 'Partially Recovered'}
                or document.liability.remaining_balance <= 0
            ):
                return 'A liability agreement needs an uploaded PDF, a required employee signature, and the matching liability record.', 409
            document.status = 'Issued'
            document.issued_at = datetime.utcnow()
            document.updated_at = datetime.utcnow()
            db.session.commit()
            if document.employee.email:
                sent = send_notification_email(
                    [document.employee.email],
                    f'HR document received: {document.subject}',
                    f'{document.employee.full_name()}, an HR document is ready for you in HRIS. '
                    f'Please sign in to review it: {url_for("hr_documents", _external=True)}',
                )
                if not sent:
                    logger.warning('HR document %s was issued, but email notification was not sent.', document.id)
            flash('HR document issued to the employee.', 'success')
            return redirect(url_for('hr_documents'))
        if not is_admin:
            return 'Access denied', 403
        employee = Employee.query.get_or_404(request.form.get('employee_id', type=int))
        document_type = request.form.get('document_type') or 'Memo'
        if document_type not in HR_DOCUMENT_TYPES:
            abort(400)
        attachment = request.files.get('attachment')
        attachment_bytes = None
        attachment_original_name = None
        if attachment and attachment.filename:
            try:
                attachment_original_name, attachment_bytes = read_hr_pdf_upload(attachment)
            except ValueError as error:
                flash(str(error), 'danger')
                return redirect(url_for('hr_documents'))
        signature_required = request.form.get('signature_required') == '1'
        liability_id = request.form.get('liability_id', type=int)
        liability = db.session.get(EmployeeLiability, liability_id) if liability_id else None
        if document_type == 'Liability Agreement' and (
            not attachment_bytes
            or not signature_required
            or liability is None
            or liability.employee_id != employee.id
            or liability.status not in {'Active', 'Partially Recovered'}
            or liability.remaining_balance <= 0
        ):
            flash(
                'A liability agreement needs an uploaded PDF, a required employee signature, '
                'and the matching employee liability record.',
                'danger',
            )
            return redirect(url_for('hr_documents'))
        issue_now = request.form.get('issue_now') == 'true'
        document = HRDocument(
            employee_id=employee.id,
            document_type=document_type,
            subject=request.form.get('subject', '').strip() or HR_DOCUMENT_TYPES.get(document_type, document_type),
            body=request.form.get('body', '').strip() or default_hr_document_body(document_type, employee),
            response_due_date=datetime.strptime(request.form.get('response_due_date'), '%Y-%m-%d').date() if request.form.get('response_due_date') else None,
            effective_date=datetime.strptime(request.form.get('effective_date'), '%Y-%m-%d').date() if request.form.get('effective_date') else None,
            related_reference=request.form.get('related_reference', '').strip() or None,
            status='Issued' if issue_now else 'Draft',
            issued_at=datetime.utcnow() if issue_now else None,
            created_by=current_user.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            signature_required=signature_required,
            attachment_original_name=attachment_original_name,
            attachment_sha256=hashlib.sha256(attachment_bytes).hexdigest() if attachment_bytes else None,
            liability_id=liability.id if document_type == 'Liability Agreement' else None,
        )
        stored_attachment_path = None
        try:
            db.session.add(document)
            db.session.flush()
            if attachment_bytes:
                attachment_filename, stored_attachment_path = store_hr_pdf(
                    employee.id, attachment_bytes
                )
                document.attachment_filename = attachment_filename
            db.session.commit()
        except (OSError, SQLAlchemyError):
            db.session.rollback()
            if stored_attachment_path and os.path.exists(stored_attachment_path):
                os.remove(stored_attachment_path)
            logger.exception('Could not save HR document for employee %s', employee.id)
            flash('The HR document could not be saved. Please try again.', 'danger')
            return redirect(url_for('hr_documents'))
        if issue_now and employee.email:
            sent = send_notification_email(
                [employee.email],
                f'HR document received: {document.subject}',
                f'{employee.full_name()}, an HR document is ready for you in HRIS. '
                f'Please sign in to review it: {url_for("hr_documents", _external=True)}',
            )
            if not sent:
                logger.warning('HR document %s was issued, but email notification was not sent.', document.id)
        flash('HR document saved.', 'success')
        return redirect(url_for('hr_documents'))
    if is_admin:
        documents = HRDocument.query.order_by(HRDocument.created_at.desc()).all()
        employees = Employee.query.filter(Employee.first_name.isnot(None), Employee.last_name.isnot(None)).order_by(Employee.last_name, Employee.first_name).all()
        liabilities = [
            liability for liability in EmployeeLiability.query.filter(
                EmployeeLiability.status.in_(['Active', 'Partially Recovered']),
            ).order_by(EmployeeLiability.created_at.desc()).all()
            if liability.remaining_balance > 0
        ]
    else:
        documents = HRDocument.query.filter(
            HRDocument.employee_id == current_user.id,
            HRDocument.status != 'Draft',
        ).order_by(HRDocument.created_at.desc()).all()
        employees = []
        liabilities = []
    return render_template(
        'hr_documents.html',
        documents=documents,
        employees=employees,
        liabilities=liabilities,
        document_types=HR_DOCUMENT_TYPES,
        is_admin=is_admin,
        signature_statements={
            document.id: hr_document_signature_statement(document.document_type)
            for document in documents
        },
    )


@app.route('/hr-documents/<int:document_id>/download')
@login_required
def download_hr_document(document_id):
    document = HRDocument.query.get_or_404(document_id)
    if document.employee_id != current_user.id and 'admin' not in str(current_user.role or '').lower():
        return 'Access denied', 403
    if document.status == 'Draft' and 'admin' not in str(current_user.role or '').lower():
        abort(404)
    download_name = secure_filename(f'HR_Document_{document.id}_{document.document_type}.pdf') or f'HR_Document_{document.id}.pdf'
    return send_file(hr_document_pdf(document), as_attachment=True, download_name=download_name, mimetype='application/pdf')


@app.route('/hr-documents/<int:document_id>/attachment')
@login_required
def view_hr_document_attachment(document_id):
    document = HRDocument.query.get_or_404(document_id)
    if document.employee_id != current_user.id and 'admin' not in str(current_user.role or '').lower():
        return 'Access denied', 403
    if not document.attachment_filename or (document.status == 'Draft' and 'admin' not in str(current_user.role or '').lower()):
        abort(404)
    attachment_path = os.path.join(
        app.config['HR_DOCUMENTS_FOLDER'], str(document.employee_id), document.attachment_filename,
    )
    if not os.path.isfile(attachment_path):
        abort(404)
    return send_file(
        attachment_path,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=document.attachment_original_name or 'HR_Document.pdf',
    )


@app.route('/hr-documents/<int:document_id>/wet-signed')
@login_required
def view_wet_signed_hr_document(document_id):
    document = HRDocument.query.get_or_404(document_id)
    if document.employee_id != current_user.id and 'admin' not in str(current_user.role or '').lower():
        return 'Access denied', 403
    if not document.wet_signed_filename:
        abort(404)
    signed_path = os.path.join(
        app.config['HR_DOCUMENTS_FOLDER'], str(document.employee_id), document.wet_signed_filename,
    )
    if not os.path.isfile(signed_path):
        abort(404)
    return send_file(
        signed_path,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=document.wet_signed_original_name or 'Wet_Signed_HR_Document.pdf',
    )


CONCERN_TYPES = ['Suggestion', 'Clarification', 'Hinaing / Grievance', 'Payroll Question', 'Policy Question', 'Other']


@app.route('/staff-concerns', methods=['GET', 'POST'])
@login_required
def staff_concerns():
    is_admin = 'admin' in str(current_user.role or '').lower()
    if request.method == 'POST':
        action = request.form.get('action', 'create')
        if action == 'respond':
            if not is_admin:
                return 'Access denied', 403
            concern = StaffConcern.query.get_or_404(request.form.get('concern_id', type=int))
            concern.admin_response = request.form.get('admin_response', '').strip()
            concern.status = request.form.get('status') or 'Responded'
            concern.responded_by = current_user.id
            concern.responded_at = datetime.utcnow()
            db.session.commit()
            flash('Response saved.', 'success')
            return redirect(url_for('staff_concerns'))

        if is_admin:
            flash('Use the response form to reply to staff concerns.', 'warning')
            return redirect(url_for('staff_concerns'))
        concern = StaffConcern(
            employee_id=current_user.id,
            concern_type=request.form.get('concern_type') if request.form.get('concern_type') in CONCERN_TYPES else 'Clarification',
            subject=request.form.get('subject', '').strip(),
            message=request.form.get('message', '').strip(),
            created_at=datetime.utcnow(),
        )
        if not concern.subject or not concern.message:
            flash('Subject and message are required.', 'danger')
            return redirect(url_for('staff_concerns'))
        db.session.add(concern)
        db.session.commit()
        flash('Your concern was submitted to Admin/HR.', 'success')
        return redirect(url_for('staff_concerns'))

    concerns = (
        StaffConcern.query.order_by(StaffConcern.created_at.desc()).all()
        if is_admin
        else StaffConcern.query.filter_by(employee_id=current_user.id).order_by(StaffConcern.created_at.desc()).all()
    )
    return render_template('staff_concerns.html', concerns=concerns, concern_types=CONCERN_TYPES, is_admin=is_admin)


@app.route('/staff-messages', methods=['GET', 'POST'])
@login_required
def staff_messages():
    is_admin = 'admin' in str(current_user.role or '').lower()
    if request.method == 'POST':
        if not is_admin:
            return 'Access denied', 403

        message_type = request.form.get('message_type')
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()
        if message_type not in {'direct', 'announcement'}:
            flash('Choose a valid message type.', 'danger')
            return redirect(url_for('staff_messages'))
        if not subject or len(subject) > 180 or not body or len(body) > 10000:
            flash('Enter a subject (up to 180 characters) and message (up to 10,000 characters).', 'danger')
            return redirect(url_for('staff_messages'))

        if message_type == 'direct':
            recipient_id = request.form.get('recipient_id', type=int)
            recipient = Employee.query.filter(
                Employee.id == recipient_id,
                Employee.role.ilike('%staff%'),
            ).first() if recipient_id is not None else None
            if recipient is None:
                flash('Choose one valid staff member for a private message.', 'danger')
                return redirect(url_for('staff_messages'))
            recipients = [recipient]
        else:
            if request.form.get('send_to_all') == '1':
                recipients = Employee.query.filter(Employee.role.ilike('%staff%')).order_by(
                    Employee.last_name, Employee.first_name,
                ).all()
            else:
                raw_ids = request.form.getlist('recipient_ids')
                try:
                    recipient_ids = {int(value) for value in raw_ids}
                except ValueError:
                    recipient_ids = set()
                if not recipient_ids or len(recipient_ids) != len(raw_ids):
                    flash('Choose at least one valid staff member for the announcement.', 'danger')
                    return redirect(url_for('staff_messages'))
                recipients = Employee.query.filter(
                    Employee.id.in_(recipient_ids),
                    Employee.role.ilike('%staff%'),
                ).order_by(Employee.last_name, Employee.first_name).all()
                if len(recipients) != len(recipient_ids):
                    flash('Announcements can only be sent to staff accounts.', 'danger')
                    return redirect(url_for('staff_messages'))

        if not recipients:
            flash('There are no staff recipients for this message.', 'warning')
            return redirect(url_for('staff_messages'))
        db.session.add_all([
            StaffMessage(
                sender_id=current_user.id,
                recipient_id=recipient.id,
                message_type=message_type,
                subject=subject,
                body=body,
            )
            for recipient in recipients
        ])
        db.session.commit()
        flash(f'Message sent to {len(recipients)} staff member(s).', 'success')
        return redirect(url_for('staff_messages'))

    if is_admin:
        staff_members = Employee.query.filter(Employee.role.ilike('%staff%')).order_by(
            Employee.last_name, Employee.first_name,
        ).all()
        sent_messages = StaffMessage.query.filter_by(sender_id=current_user.id).order_by(
            StaffMessage.created_at.desc(),
        ).limit(100).all()
        return render_template(
            'staff_messages.html',
            is_admin=True,
            staff_members=staff_members,
            sent_messages=sent_messages,
        )

    inbox = StaffMessage.query.filter_by(recipient_id=current_user.id).order_by(
        StaffMessage.created_at.desc(),
    ).limit(100).all()
    now = datetime.utcnow()
    unread_messages = [message for message in inbox if message.read_at is None]
    for message in unread_messages:
        message.read_at = now
    if unread_messages:
        db.session.commit()
    return render_template('staff_messages.html', is_admin=False, inbox=inbox)


def generate_qr_image_bytes(verify_url, size=140):
    if qrcode is None:
        return None
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(verify_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer.getvalue()


def format_recorded_time(recorded_at):
    if recorded_at is None:
        return 'N/A'
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=ZoneInfo('UTC'))
    return recorded_at.astimezone(ZoneInfo('Asia/Manila')).strftime(
        '%Y-%m-%d %I:%M:%S %p PHT'
    )


@app.route('/verify-document/<verification_id>')
def verify_document(verification_id):
    record = PayslipVerification.query.filter_by(document_id=verification_id).first()
    if record is None:
        return render_template_string('''
            <html><body style="font-family:Arial,sans-serif;padding:32px;background:#eef3f1;color:#263238;">
                <main style="max-width:760px;margin:0 auto;background:#fff;padding:28px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.08);">
                <h2 style="color:#c0392b;">Invalid Document</h2>
                <p>Document ID not found in the HRIS records.</p>
                </main>
            </body></html>
        '''), 404

    employee = Employee.query.get(record.employee_id)
    payroll = Payroll.query.get(record.payroll_id) if record.payroll_id else None
    payload_prefix = f"{record.document_type}|{record.employee_id}|{record.document_label}|{record.payroll_id or 'none'}|{record.cutoff_start or 'none'}|{record.cutoff_end or 'none'}|{float(record.net_pay or 0)}"
    expected_hashes = {
        hashlib.sha256(f"{payload_prefix}|{key}".encode()).hexdigest()
        for key in (app.secret_key, LEGACY_DOCUMENT_HASH_KEY)
    }
    is_valid = employee is not None and record.verification_hash in expected_hashes and (record.document_type != 'payslip' or payroll is not None)
    company_name = payroll_company_name(employee) if employee else 'AUTO-EXPERT AUTO SUPPLY / TRECE-UNO AUTO SUPPLY'
    company_key = str(employee.company or '').lower() if employee else ''
    if company_key.startswith('trece'):
        logo = url_for('static', filename='images/trece-uno-logo.jpg')
    else:
        logo = url_for('static', filename='images/autoxpertlogo.jpg')
    email = 'edzvronquillo@gmail.com'
    contact_no = '09985695903'

    return render_template_string('''
        <html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>HRIS Document Verification</title></head>
        <body style="font-family:Arial,sans-serif;padding:24px;background:#eef3f1;color:#263238;">
            <main style="max-width:820px;margin:0 auto;background:#fff;padding:28px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.08);">
                <div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap;border-bottom:1px solid #d8e0dc;padding-bottom:18px;margin-bottom:18px;">
                    <img src="{{ logo }}" alt="Company Logo" style="max-height:72px;max-width:220px;object-fit:contain;">
                    <div><h1 style="margin:0;color:#176b4d;">{{ company_name }}</h1><p style="margin:5px 0 0;"><strong>Email:</strong> {{ email }}</p><p style="margin:5px 0 0;"><strong>Contact No.:</strong> {{ contact_no }}</p></div>
                </div>
                <h2 style="color:{{ '#176b4d' if is_valid else '#c0392b' }};">{% if is_valid %}This document is authentic{% else %}Verification Failed{% endif %}</h2>
                {% if is_valid %}<p>This document matches the official HRIS record and was generated by the system.</p>{% else %}<p>This document could not be verified against the official HRIS records.</p>{% endif %}
                <section style="background:#f8faf9;border-left:5px solid #2879ad;padding:16px;margin-top:16px;">
                    <p><strong>Document ID:</strong> {{ record.document_id }}</p>
                    <p><strong>Employee:</strong> {{ employee_name }}</p>
                    <p><strong>Document Type:</strong> {{ record.document_type }}</p>
                    <p><strong>Document Label:</strong> {{ record.document_label or 'N/A' }}</p>
                    <p><strong>First Generated / Recorded (Philippine time):</strong> {{ recorded_at }}</p>
                    {% if record.cutoff_start and record.cutoff_end %}<p><strong>Period:</strong> {{ record.cutoff_start }} to {{ record.cutoff_end }}</p>{% endif %}
                    {% if record.net_pay %}<p><strong>Net Pay / Amount:</strong> PHP {{ "%.2f"|format(record.net_pay) }}</p>{% endif %}
                </section>
                <p style="font-size:.92rem;color:#546e7a;margin-top:18px;">For validation questions, contact the email/contact number shown above and provide the Document ID.</p>
            </main>
        </body></html>
    ''', record=record, employee_name=(employee.full_name() if employee else 'Unknown Employee'), is_valid=is_valid, company_name=company_name, logo=logo, email=email, contact_no=contact_no,
        recorded_at=format_recorded_time(record.created_at))


@app.route('/verify-payslip/<verification_id>')
def verify_payslip(verification_id):
    return verify_document(verification_id)


@app.route('/payroll/summary')
@login_required
def payroll_summary():
    is_admin = can_manage_admin_payroll(current_user)
    can_view_summary = (
        can_prepare_payroll(current_user)
        or can_review_payroll_loans(current_user)
    )
    if not can_view_summary:
        return 'Access denied', 403
    if is_admin:
        company = request.args.get('company', 'Trece-Uno')
    else:
        staff_company = str(current_user.company or '').strip().lower()
        if staff_company.startswith('trece'):
            company = 'Trece-Uno'
        elif staff_company in {'auto expert', 'auto-expert', 'autoxpert'}:
            company = 'Auto Expert'
        else:
            return 'Access denied', 403
        requested_company = request.args.get('company')
        if requested_company and requested_company != company:
            return 'Access denied', 403
    if company not in {'Trece-Uno', 'Auto Expert'}:
        abort(400)
    try:
        cutoff_start, cutoff_end = payroll_cutoff_from_request(request.args.get('cutoff_start'))
    except ValueError:
        abort(400)
    rows = build_company_payroll_summary(
        company,
        cutoff_start,
        cutoff_end,
        include_admins=is_admin or can_review_payroll_loans(current_user),
    )
    if request.args.get('view') == 'true':
        return render_template(
            'payroll_summary_view.html',
            rows=rows,
            company=company,
            cutoff_start=cutoff_start,
            cutoff_end=cutoff_end - timedelta(days=1),
            cutoff_options=payroll_dashboard_cutoff_options(cutoff_start),
        )
    pdf_data = payroll_summary_pdf(
        company, cutoff_start, cutoff_end, rows, current_user.id
    )
    return send_file(
        io.BytesIO(pdf_data), as_attachment=True,
        download_name=f'Payroll_Summary_{company}_{cutoff_start}.pdf',
        mimetype='application/pdf'
    )


@app.route('/payroll/summary/email', methods=['POST'])
@login_required
def email_payroll_summary():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    company = request.form.get('company', 'Trece-Uno')
    recipient = request.form.get('email', '').strip()
    if company not in {'Trece-Uno', 'Auto Expert'} or not recipient:
        flash('Please select a company and enter an email address.', 'danger')
        return redirect(url_for('payroll_dashboard'))
    cutoff_start, cutoff_end = completed_cutoff()
    rows = build_company_payroll_summary(
        company, cutoff_start, cutoff_end, include_admins=True
    )
    pdf_data = payroll_summary_pdf(
        company, cutoff_start, cutoff_end, rows, current_user.id
    )
    sent = send_notification_email(
        [recipient], f'{company} Payroll Summary - {cutoff_start}',
        f'Attached is the payroll summary for {company}, cutoff {cutoff_start} to {cutoff_end - timedelta(days=1)}.',
        attachments=[(f'Payroll_Summary_{company}_{cutoff_start}.pdf', pdf_data, 'application/pdf')]
    )
    flash('Payroll summary emailed successfully.' if sent else 'Email was not sent. Check MAIL_SERVER settings.', 'success' if sent else 'danger')
    return redirect(url_for('payroll_dashboard'))


@app.route('/payroll/summary/mark-paid', methods=['POST'])
@login_required
def mark_payroll_summary_paid():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    company = request.form.get('company', 'Trece-Uno')
    if company not in {'Trece-Uno', 'Auto Expert'}:
        abort(400)
    cutoff_start, cutoff_end = completed_cutoff()
    for row in build_company_payroll_summary(company, cutoff_start, cutoff_end):
        record = row['payroll_record'] or Payroll(
            employee_id=row['employee'].id,
            cutoff_start=cutoff_start,
            cutoff_end=cutoff_end - timedelta(days=1)
        )
        if record.id is None:
            db.session.add(record)
        finalize_payroll_record(row['employee'], record, cutoff_start, cutoff_end)
    db.session.commit()
    flash(f'{company} payroll marked as paid.', 'success')
    return redirect(url_for('payroll_dashboard'))


@app.route('/payroll/confirmation/<int:payroll_id>', methods=['POST'])
@login_required
def submit_payroll_confirmation(payroll_id):
    payroll_record = Payroll.query.get_or_404(payroll_id)
    if payroll_record.employee_id != current_user.id:
        return 'Access denied', 403
    if not payroll_record.is_paid:
        abort(400, 'Payroll must be finalized before staff confirmation.')

    action = request.form.get('action')
    if action == 'confirm':
        if payroll_record.confirmation_status == 'Needs Correction':
            flash('Wait for Admin to review the reported issue before confirming again.', 'warning')
            return redirect(url_for('payroll', employee_id=current_user.id))
        payroll_record.confirmation_status = 'Confirmed'
        payroll_record.confirmation_note = None
        payroll_record.confirmed_at = datetime.utcnow()
        flash('Your payroll review confirmation was saved.', 'success')
    elif action == 'report_issue':
        note = request.form.get('confirmation_note', '').strip()
        if not note:
            flash('Describe what looks incorrect so Admin can review it.', 'danger')
            return redirect(url_for('payroll', employee_id=current_user.id))
        if len(note) > 2000:
            flash('The payroll issue description must be 2,000 characters or fewer.', 'danger')
            return redirect(url_for('payroll', employee_id=current_user.id))
        payroll_record.confirmation_status = 'Needs Correction'
        payroll_record.confirmation_note = note
        payroll_record.confirmed_at = None
        flash('Your payroll issue was sent to Admin for review.', 'success')
    else:
        abort(400, 'Invalid payroll review action.')

    db.session.commit()
    return redirect(url_for('payroll', employee_id=current_user.id))


@app.route('/payroll/confirmation/<int:payroll_id>/request-again', methods=['POST'])
@login_required
def request_payroll_reconfirmation(payroll_id):
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    payroll_record = Payroll.query.get_or_404(payroll_id)
    if not payroll_record.is_paid or payroll_record.confirmation_status not in {
        None, 'Needs Correction'
    }:
        abort(400, 'Only finalized payroll without an active confirmation can be requested.')
    payroll_record.confirmation_status = 'Pending'
    payroll_record.confirmation_note = None
    payroll_record.confirmed_at = None
    db.session.commit()
    flash('Payroll is marked ready for staff to review again.', 'success')
    employee_company = str(payroll_record.employee.company or '').strip().lower()
    company = 'Trece-Uno' if employee_company.startswith('trece') else 'Auto Expert'
    return redirect(url_for(
        'payroll_summary',
        company=company,
        cutoff_start=payroll_record.cutoff_start.isoformat(),
        view='true',
    ))


@app.route('/apply_ot', methods=['GET', 'POST'])
@login_required
def apply_ot():
    if "staff" not in current_user.role.lower():
        return redirect(url_for('dashboard_admin'))
    if request.method == 'POST':
        try:
            ot_date = datetime.strptime(request.form['ot_date'], '%Y-%m-%d').date()
            start_time = datetime.strptime(request.form['start_time'], '%H:%M').time()
            end_time = datetime.strptime(request.form['end_time'], '%H:%M').time()
        except (KeyError, TypeError, ValueError):
            flash('Please provide a valid OT date and time.', 'danger')
            return redirect(url_for('apply_ot'))
        if end_time <= start_time or not request.form.get('reason', '').strip():
            flash('OT end time must be after the start time and a reason is required.', 'danger')
            return redirect(url_for('apply_ot'))
        scheduled_end = (
            time(12, 0)
            if is_trece_sunday(current_user, ot_date)
            else time(17, 0)
        )
        if start_time < scheduled_end:
            flash(
                f'OT applications must start after the regular schedule ends '
                f'at {scheduled_end.strftime("%I:%M %p")}.',
                'danger',
            )
            return redirect(url_for('apply_ot'))
        lock_attendance_employee(current_user.id)
        existing = OTApplication.query.filter_by(
            employee_id=current_user.id, ot_date=ot_date, status='Pending'
        ).first()
        if existing:
            flash('You already have a pending OT application for this date.', 'warning')
            return redirect(url_for('apply_ot'))
        db.session.add(OTApplication(
            employee_id=current_user.id,
            ot_date=ot_date,
            start_time=start_time,
            end_time=end_time,
            reason=request.form['reason'].strip()
        ))
        db.session.commit()
        flash('OT application submitted for admin approval.', 'success')
        return redirect(url_for('dashboard_staff'))
    applications = OTApplication.query.filter_by(
        employee_id=current_user.id
    ).order_by(OTApplication.ot_date.desc()).all()
    return render_template('apply_ot.html', applications=applications)


@app.route('/admin/ot_applications/<int:application_id>/<action>', methods=['POST'])
@login_required
def decide_ot_application(application_id, action):
    if 'admin' not in current_user.role.lower() or action not in {'approve', 'reject'}:
        return 'Access denied', 403
    application = OTApplication.query.get_or_404(application_id)
    lock_attendance_employee(application.employee_id)
    application.status = 'Approved' if action == 'approve' else 'Rejected'
    application.decision_note = request.form.get('decision_note', '').strip() or None
    application.decided_at = datetime.now()
    attendance = Attendance.query.filter_by(
        employee_id=application.employee_id, date=application.ot_date
    ).filter(Attendance.clock_out != None).first()
    if attendance:
        apply_overtime_details(attendance)
        if action == 'reject' and attendance.ot_status is None:
            attendance.ot_status = 'Rejected'
    db.session.commit()
    employee = db.session.get(Employee, application.employee_id)
    decision = application.status.lower()
    send_notification_email(
        [employee.email if employee else None],
        f'OT application {decision}',
        (
            f'Your OT application for {application.ot_date}, '
            f'{application.start_time.strftime("%I:%M %p")} to '
            f'{application.end_time.strftime("%I:%M %p")}, was {decision}.'
            + (f' Admin note: {application.decision_note}' if application.decision_note else '')
        ),
    )
    return redirect(url_for('holiday_ot_dashboard'))


@app.route('/attendance_action/<int:employee_id>', methods=['POST'])
@login_required
def attendance_action(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    event_now = parse_event_time(request.form.get('event_time'))

    if current_user.id != employee_id and "admin" not in current_user.role.lower():
        flash("❌ Access denied.", "danger")
        return redirect(url_for('dashboard_staff'))

    lock_attendance_employee(employee_id)
    if "clockin" in request.form:
        day_logs = Attendance.query.filter_by(
            employee_id=employee_id,
            date=event_now.date()
        ).order_by(Attendance.id).all()
        if len(day_logs) > 1:
            flash(
                "⚠️ Multiple attendance records already exist for this date. "
                "Ask an admin to review them before clocking in.",
                "warning",
            )
            return redirect(url_for('dashboard_staff'))
        if day_logs and (
            day_logs[0].clock_in is not None or day_logs[0].clock_out is not None
        ):
            flash(
                "⚠️ An attendance record already exists for today. "
                "Contact an admin if it needs correction.",
                "warning",
            )
            return redirect(url_for('dashboard_staff'))

        log = day_logs[0] if day_logs else Attendance(
            date=event_now.date(),
            employee_id=employee_id,
        )
        log.clock_in = event_now
        log.status = attendance_status(event_now)
        if not day_logs:
            db.session.add(log)
        db.session.commit()
        notify_attendance_event(emp, "clockin", event_now)
        flash("🟢 Clocked in successfully!", "success")

    elif "clockout" in request.form:
        day_logs = Attendance.query.filter_by(
            employee_id=employee_id,
            date=event_now.date()
        ).order_by(Attendance.clock_in.desc()).all()
        if len(day_logs) > 1:
            flash(
                "⚠️ Multiple attendance records already exist for this date. "
                "Ask an admin to review them before clocking out.",
                "warning",
            )
            return redirect(url_for('attendance', employee_id=employee_id))
        log = (
            day_logs[0]
            if day_logs and day_logs[0].clock_in and not day_logs[0].clock_out
            else None
        )
        if log:
            log.clock_out = event_now
            log.hours = regular_attendance_hours(log)
            apply_overtime_details(log)
            db.session.commit()
            notify_attendance_event(emp, "clockout", event_now)
            flash("🔴 Clocked out successfully!", "success")
        else:
            flash("⚠️ No active clock-in found.", "warning")

    return redirect(url_for('attendance', employee_id=employee_id))


# JSON API for AJAX clock in/out from dashboard
@app.route('/attendance_api/<int:employee_id>', methods=['POST'])
@login_required
def attendance_api(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    event_now = parse_event_time(request.form.get('event_time'))

    try:
        if current_user.id != employee_id and "admin" not in current_user.role.lower():
            return jsonify(status="error", message="❌ Access denied."), 403

        lock_attendance_employee(employee_id)
        if "clockin" in request.form:
            day_logs = Attendance.query.filter_by(
                employee_id=employee_id,
                date=event_now.date()
            ).order_by(Attendance.id).all()
            if len(day_logs) > 1:
                return jsonify(
                    status="warning",
                    message=(
                        "⚠️ Multiple attendance records already exist for this date. "
                        "Ask an admin to review them before clocking in."
                    ),
                )
            if day_logs and (
                day_logs[0].clock_in is not None
                or day_logs[0].clock_out is not None
            ):
                return jsonify(
                    status="warning",
                    message=(
                        "⚠️ An attendance record already exists for today. "
                        "Contact an admin if it needs correction."
                    ),
                )

            log = day_logs[0] if day_logs else Attendance(
                date=event_now.date(),
                employee_id=employee_id,
            )
            log.clock_in = event_now
            log.status = attendance_status(event_now)
            if not day_logs:
                db.session.add(log)
            db.session.commit()
            notify_attendance_event(emp, "clockin", event_now)
            worked_hours = max(
                (datetime.now() - log.clock_in).total_seconds() / 3600,
                0.0
            )
            return jsonify(status="success", message="🟢 Clocked in successfully!",
                           clocked_in=True, clock_in=log.clock_in.isoformat(),
                           worked_hours=worked_hours)

        elif "clockout" in request.form:
            day_logs = Attendance.query.filter_by(
                employee_id=employee_id,
                date=event_now.date()
            ).order_by(Attendance.clock_in.desc()).all()
            if len(day_logs) > 1:
                return jsonify(
                    status="warning",
                    message=(
                        "⚠️ Multiple attendance records already exist for this date. "
                        "Ask an admin to review them before clocking out."
                    ),
                )
            log = (
                day_logs[0]
                if day_logs and day_logs[0].clock_in and not day_logs[0].clock_out
                else None
            )
            if log:
                log.clock_out = event_now
                log.hours = regular_attendance_hours(log)
                apply_overtime_details(log)
                db.session.commit()
                notify_attendance_event(emp, "clockout", event_now)
                return jsonify(status="success", message="🔴 Clocked out successfully!",
                               clocked_in=False, clock_in="", worked_hours=log.hours)
            else:
                return jsonify(status="warning", message="⚠️ No active clock-in found.")

        return jsonify(status="error", message="Invalid request")
    except Exception:
        db.session.rollback()
        logger.exception("Error in attendance_api")
        return jsonify(status="error", message="Internal server error"), 500


def reconciliation_snapshot(records):
    return [
        {
            column.name: backup_cell_value(getattr(record, column.name))
            for column in Attendance.__table__.columns
        }
        for record in records
    ]


def reconciliation_fingerprint(records):
    return hashlib.sha256(
        json.dumps(reconciliation_snapshot(records), sort_keys=True).encode()
    ).hexdigest()


def validate_reconciliation_records(employee, attendance_date, records, clock_out):
    if len(records) < 2:
        raise ValueError('This tool requires at least two records for the selected date.')
    if attendance_date.weekday() == 6 or Holiday.query.filter_by(date=attendance_date).first():
        raise ValueError('Sunday and holiday records require a separate payroll review.')
    if clock_out.time() >= time(18):
        raise ValueError('Clock-out at or after 6 PM requires a separate overtime review.')
    if any(
        record.is_weekday_ot or record.is_restday_ot or record.is_holiday_ot
        or record.overtime_hours or record.ot_status not in (None, '', 'Pending')
        or (record.clock_out and record.clock_out.time() >= time(18))
        for record in records
    ) or OTApplication.query.filter_by(
        employee_id=employee.id, ot_date=attendance_date
    ).first():
        raise ValueError('Records with overtime or OT applications require a separate review.')
    corrections = AttendanceCorrection.query.filter(
        (AttendanceCorrection.attendance_id.in_([record.id for record in records]))
        | (
            (AttendanceCorrection.employee_id == employee.id)
            & (AttendanceCorrection.correction_date == attendance_date)
        )
    ).all()
    if any(
        correction.status not in ('Approved', 'Rejected')
        or correction.employee_id != employee.id
        or correction.correction_date != attendance_date
        for correction in corrections
    ):
        raise ValueError('Pending or inconsistent correction requests must be reviewed separately.')
    linked_ids = {
        correction.attendance_id for correction in corrections
        if correction.attendance_id is not None
    }
    if len(linked_ids) > 1:
        raise ValueError('Reviewed corrections link to different duplicate records. Review separately.')
    retained_id = next(iter(linked_ids), records[0].id)
    if retained_id not in {record.id for record in records}:
        raise ValueError('A correction links to a record outside this date. Review separately.')
    return next(record for record in records if record.id == retained_id)


def reconciliation_backup(backup_id=None):
    query = AuditLog.query.filter(
        AuditLog.employee_id == current_user.id,
        AuditLog.action.in_([
            'Downloaded full database backup',
            'Downloaded complete backup with files',
        ]),
        AuditLog.created_at >= datetime.utcnow() - timedelta(minutes=30),
    )
    if backup_id is not None:
        query = query.filter(AuditLog.id == backup_id)
    return query.order_by(AuditLog.id.desc()).first()


@app.route('/admin/attendance-reconciliation/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def attendance_reconciliation(employee_id):
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    employee = Employee.query.get_or_404(employee_id)
    csrf_token = session.setdefault('attendance_reconciliation_csrf', secrets.token_urlsafe(32))
    preview = None
    selected_date = request.values.get('attendance_date', '')
    status_code = 200
    if request.method == 'POST':
        submitted_token = request.form.get('csrf_token', '')
        if not secrets.compare_digest(csrf_token.encode(), submitted_token.encode()):
            abort(400, description='Invalid reconciliation form. Reload and try again.')
        try:
            action = request.form.get('action')
            if action == 'preview':
                session.pop('attendance_reconciliation_preview', None)
                attendance_date = date.fromisoformat(selected_date)
                clock_in = datetime.fromisoformat(request.form.get('clock_in', ''))
                clock_out = datetime.fromisoformat(request.form.get('clock_out', ''))
                if (
                    clock_in.tzinfo is not None or clock_out.tzinfo is not None
                    or clock_in.date() != attendance_date
                    or clock_out.date() != attendance_date or clock_out <= clock_in
                ):
                    raise ValueError('Enter same-day local times with clock-out after clock-in.')
                reason = request.form.get('reason', '').strip()
                if not reason or len(reason) > 500:
                    raise ValueError('Enter a source/reason of 1 to 500 characters.')
                backup = reconciliation_backup()
                if backup is None:
                    raise ValueError('Download and verify a fresh database backup before previewing.')
                records = Attendance.query.filter_by(
                    employee_id=employee.id, date=attendance_date
                ).order_by(Attendance.id).all()
                retained = validate_reconciliation_records(
                    employee, attendance_date, records, clock_out
                )
                preview = {
                    'employee_id': employee.id,
                    'attendance_date': attendance_date.isoformat(),
                    'clock_in': clock_in.isoformat(),
                    'clock_out': clock_out.isoformat(),
                    'hours': biometric_work_hours(clock_in, clock_out),
                    'status': attendance_status(clock_in),
                    'retained_id': retained.id,
                    'reason': reason,
                    'fingerprint': reconciliation_fingerprint(records),
                    'backup_id': backup.id,
                    'expires_at': (datetime.utcnow() + timedelta(minutes=10)).isoformat(),
                    'nonce': secrets.token_urlsafe(32),
                }
                session['attendance_reconciliation_preview'] = preview
            elif action == 'confirm':
                preview = session.get('attendance_reconciliation_preview')
                if (
                    not preview or preview['employee_id'] != employee.id
                    or not secrets.compare_digest(
                        preview['nonce'].encode(), request.form.get('preview_nonce', '').encode()
                    )
                    or datetime.fromisoformat(preview['expires_at']) < datetime.utcnow()
                ):
                    raise ValueError('Preview expired or invalid. Generate a new preview.')
                if request.form.get('backup_verified') != 'yes':
                    raise ValueError('Confirm that the downloaded backup was saved and verified.')
                if reconciliation_backup(preview['backup_id']) is None:
                    raise ValueError('Backup expired. Download a fresh backup and preview again.')
                attendance_date = date.fromisoformat(preview['attendance_date'])
                clock_in = datetime.fromisoformat(preview['clock_in'])
                clock_out = datetime.fromisoformat(preview['clock_out'])
                lock_attendance_employee(employee.id)
                records = Attendance.query.filter_by(
                    employee_id=employee.id, date=attendance_date
                ).order_by(Attendance.id).populate_existing().with_for_update().all()
                if reconciliation_fingerprint(records) != preview['fingerprint']:
                    raise ValueError('Attendance changed after preview. Review a new preview.')
                retained = validate_reconciliation_records(
                    employee, attendance_date, records, clock_out
                )
                if retained.id != preview['retained_id']:
                    raise ValueError('Correction links changed after preview. Review a new preview.')
                original_rows = reconciliation_snapshot(records)
                retained.clock_in = clock_in
                retained.clock_out = clock_out
                retained.status = attendance_status(clock_in)
                retained.hours = regular_attendance_hours(retained)
                db.session.add(AttendanceReconciliation(
                    employee_id=employee.id,
                    attendance_date=attendance_date,
                    performed_by=current_user.id,
                    retained_attendance_id=retained.id,
                    original_rows=json.dumps(original_rows, sort_keys=True),
                    corrected_row=json.dumps(reconciliation_snapshot([retained])[0], sort_keys=True),
                    reason=preview['reason'],
                    backup_audit_id=preview['backup_id'],
                ))
                for duplicate in records:
                    if duplicate.id != retained.id:
                        db.session.delete(duplicate)
                db.session.add(AuditLog(
                    employee_id=current_user.id,
                    employee_name=current_user.full_name(),
                    company=current_user.company,
                    action=f'Reconciled attendance employee {employee.id} on {attendance_date}',
                    path=request.path,
                    ip_address=request.remote_addr,
                    status_code=302,
                ))
                db.session.commit()
                session.pop('attendance_reconciliation_preview', None)
                flash(
                    'Attendance reconciled; original rows preserved in history. '
                    'Saved payroll was not changed. Review payroll and payslip separately.',
                    'success',
                )
                return redirect(url_for(
                    'attendance_reconciliation', employee_id=employee.id,
                    attendance_date=attendance_date.isoformat(),
                ))
            else:
                raise ValueError('Select preview or confirm.')
        except ValueError as exc:
            db.session.rollback()
            preview = None
            session.pop('attendance_reconciliation_preview', None)
            flash(str(exc), 'danger')
            status_code = 400
        except SQLAlchemyError:
            db.session.rollback()
            preview = None
            session.pop('attendance_reconciliation_preview', None)
            logger.exception('Attendance reconciliation failed for employee %s', employee_id)
            flash('Reconciliation failed. No attendance changes were saved. Try again after review.', 'danger')
            status_code = 503

    records = []
    try:
        target_date = date.fromisoformat(selected_date) if selected_date else None
    except ValueError:
        target_date = None
        flash('Select a valid attendance date.', 'danger')
        status_code = 400
    if target_date:
        records = Attendance.query.filter_by(
            employee_id=employee.id, date=target_date
        ).order_by(Attendance.id).all()
    history = AttendanceReconciliation.query.filter_by(
        employee_id=employee.id
    ).order_by(AttendanceReconciliation.id.desc()).limit(10).all()
    return render_template(
        'attendance_reconciliation.html', emp=employee,
        selected_date=selected_date, records=records,
        preview=preview, csrf_token=csrf_token,
        history=[
            {'entry': entry, 'originals': json.loads(entry.original_rows),
             'corrected': json.loads(entry.corrected_row)}
            for entry in history
        ],
    ), status_code


@app.route('/attendance/correction', methods=['GET', 'POST'])
@login_required
def attendance_correction():
    if 'staff' not in current_user.role.lower():
        return redirect(url_for('dashboard_admin'))
    if request.method == 'POST':
        try:
            correction_date = datetime.strptime(request.form['correction_date'], '%Y-%m-%d').date()
            clock_in_value = request.form.get('requested_clock_in')
            clock_out_value = request.form.get('requested_clock_out')
            requested_clock_in = datetime.fromisoformat(clock_in_value) if clock_in_value else None
            requested_clock_out = datetime.fromisoformat(clock_out_value) if clock_out_value else None
            reason = request.form.get('reason', '').strip()
            lock_attendance_employee(current_user.id)
            attendance_record = Attendance.query.filter_by(
                employee_id=current_user.id,
                date=correction_date,
            ).order_by(Attendance.clock_in.desc()).first()
            attendance_records = Attendance.query.filter_by(
                employee_id=current_user.id,
                date=correction_date,
            ).all()
            if len(attendance_records) > 1:
                flash(
                    'Multiple attendance records exist for this date. '
                    'Ask an admin to reconcile them before submitting a correction.',
                    'danger',
                )
                return redirect(url_for('attendance_correction'))
            effective_clock_in = requested_clock_in or (
                attendance_record.clock_in if attendance_record else None
            )
            if (
                not reason
                or (not requested_clock_in and not requested_clock_out)
                or not effective_clock_in
            ):
                raise ValueError
            db.session.add(AttendanceCorrection(
                employee_id=current_user.id,
                correction_date=correction_date,
                requested_clock_in=requested_clock_in,
                requested_clock_out=requested_clock_out,
                reason=reason,
                attendance=attendance_record,
            ))
            db.session.commit()
            flash('Attendance correction submitted for admin review.', 'success')
        except (TypeError, ValueError, KeyError):
            db.session.rollback()
            flash('Enter a date, at least one corrected time, and a reason. A clock-in time is required for the attendance record.', 'danger')
        return redirect(url_for('attendance_correction'))
    requests = AttendanceCorrection.query.filter_by(
        employee_id=current_user.id
    ).order_by(AttendanceCorrection.created_at.desc()).all()
    try:
        selected_correction_date = datetime.strptime(
            request.args.get('correction_date', ''), '%Y-%m-%d'
        ).date()
    except ValueError:
        selected_correction_date = None
    return render_template(
        'attendance_correction.html',
        correction_requests=requests,
        selected_correction_date=selected_correction_date,
    )


@app.route('/admin/attendance-correction/<int:correction_id>/<action>', methods=['POST'])
@login_required
def review_attendance_correction(correction_id, action):
    if 'admin' not in current_user.role.lower() or action not in {'approve', 'reject'}:
        return 'Access denied', 403
    correction = AttendanceCorrection.query.get_or_404(correction_id)
    if correction.status != 'Pending':
        flash('This correction request was already reviewed.', 'warning')
        return redirect(url_for('dashboard_admin'))
    if action == 'approve':
        lock_attendance_employee(correction.employee_id)
        attendance_records = Attendance.query.filter_by(
            employee_id=correction.employee_id,
            date=correction.correction_date
        ).order_by(Attendance.clock_in.desc()).all()
        if len(attendance_records) > 1:
            flash(
                'Multiple attendance records exist for this date. '
                'Reconcile them before approving the correction.',
                'danger',
            )
            return redirect(url_for('dashboard_admin'))
        attendance_record = attendance_records[0] if attendance_records else None
        if correction.attendance_id and (
            attendance_record is None
            or attendance_record.id != correction.attendance_id
        ):
            flash(
                'The attendance record linked to this correction is no longer available. '
                'Review the attendance before approving.',
                'danger',
            )
            return redirect(url_for('dashboard_admin'))
        if attendance_record is None:
            employee = db.session.get(Employee, correction.employee_id)
            attendance_record = Attendance(
                employee_id=correction.employee_id,
                date=correction.correction_date,
                company=employee.company if employee else None,
                status='Present'
            )
            db.session.add(attendance_record)
        correction.status = 'Approved'
        correction.reviewed_by = current_user.id
        correction.reviewed_at = datetime.utcnow()
        attendance_record.clock_in = (
            correction.requested_clock_in or attendance_record.clock_in
        )
        attendance_record.clock_out = (
            correction.requested_clock_out or attendance_record.clock_out
        )
        attendance_record.status = attendance_status(attendance_record.clock_in)
        attendance_record.hours = (
            regular_attendance_hours(attendance_record)
            if attendance_record.clock_out else None
        )
        correction.attendance = attendance_record
        flash('Attendance correction approved. Recalculate the employee payroll.', 'success')
    else:
        correction.status = 'Rejected'
        correction.reviewed_by = current_user.id
        correction.reviewed_at = datetime.utcnow()
        flash('Attendance correction rejected.', 'info')
    db.session.commit()
    employee = db.session.get(Employee, correction.employee_id)
    decision = correction.status.lower()
    requested_times = ', '.join(
        f'{label}: {value.strftime("%Y-%m-%d %I:%M %p")}'
        for label, value in (
            ('Clock in', correction.requested_clock_in),
            ('Clock out', correction.requested_clock_out),
        )
        if value is not None
    )
    send_notification_email(
        [employee.email if employee else None],
        f'Attendance correction {decision}',
        (
            f'Your attendance correction request for {correction.correction_date} '
            f'was {decision}. Requested times: {requested_times or "not specified"}.'
            + (f' Admin note: {correction.admin_note}' if correction.admin_note else '')
        ),
    )
    return redirect(url_for('dashboard_admin'))


# ------------------ PAYROLL + PAYSLIP ------------------
from flask import send_file
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from sqlalchemy import extract
import os, io


RICE_SUBSIDY_PER_DAY = 95.0
RICE_SUBSIDY_MONTHLY_DE_MINIMIS_LIMIT = 2500.0
OTHER_DE_MINIMIS_MONTHLY_LIMITS = {
    'laundry_allowance': 400.0,
    'medical_allowance': 12000.0 / 12,
    'uniform_allowance': 8000.0 / 12,
    'christmas_gift_allowance': 6000.0 / 12,
    'meal_allowance': 0.30 * 610.0 * 26,
}


def de_minimis_monthly_ceiling():
    """Return the sum of monthly-equivalent ceilings for supported benefits."""
    return RICE_SUBSIDY_MONTHLY_DE_MINIMIS_LIMIT + sum(
        OTHER_DE_MINIMIS_MONTHLY_LIMITS.values()
    )


def rice_allowance_breakdown(
    employee, worked_days, cutoff_count=4, monthly_used=0.0
):
    """Split this cutoff's rice subsidy against the remaining monthly limit."""
    configured_amount = getattr(employee, 'rice_allowance_per_day', None)
    amount = max(
        float(configured_amount or RICE_SUBSIDY_PER_DAY),
        0.0,
    )
    total = amount * max(float(worked_days or 0), 0.0)
    remaining_limit = max(
        RICE_SUBSIDY_MONTHLY_DE_MINIMIS_LIMIT
        - max(float(monthly_used or 0), 0.0),
        0.0,
    )
    return split_allowance(total, remaining_limit, True)


def rice_allowance_month_context(employee, cutoff_start, worked_days):
    """Return this cutoff's eligible rice days and earlier monthly rice amount."""
    cutoff_starts = payroll_cutoff_dates_in_month(cutoff_start)
    prior_days = 0
    if cutoff_starts and cutoff_start > cutoff_starts[0]:
        prior_attendance = payroll_attendance_records(
            employee, cutoff_starts[0], cutoff_start
        )
        prior_days = payroll_worked_days_count(employee, prior_attendance)

    configured_amount = getattr(employee, 'rice_allowance_per_day', None)
    daily_amount = max(
        float(configured_amount or RICE_SUBSIDY_PER_DAY),
        0.0,
    )
    if str(getattr(employee, 'company', '') or '').lower().startswith('trece'):
        prior_days = min(prior_days, 26)
        current_days = min(
            max(float(worked_days or 0), 0.0),
            max(26 - prior_days, 0),
        )
    else:
        current_days = max(float(worked_days or 0), 0.0)
    return current_days, prior_days * daily_amount


def other_de_minimis_monthly_total(employee):
    return sum(
        max(float(getattr(employee, field, 0) or 0), 0.0)
        for field in OTHER_DE_MINIMIS_MONTHLY_LIMITS
    )


def de_minimis_allowance_breakdown(
    employee, worked_days, cutoff_count=4, rice_monthly_used=0.0
):
    """Return total exempt and taxable portions for configured de minimis benefits."""
    exempt, taxable = rice_allowance_breakdown(
        employee,
        worked_days,
        cutoff_count,
        monthly_used=rice_monthly_used,
    )
    for field, monthly_ceiling in OTHER_DE_MINIMIS_MONTHLY_LIMITS.items():
        amount = max(float(getattr(employee, field, 0) or 0), 0.0) / max(cutoff_count, 1)
        category_exempt, category_taxable = split_allowance(
            amount,
            monthly_ceiling / max(cutoff_count, 1),
            True,
        )
        exempt += category_exempt
        taxable += category_taxable
    return exempt, taxable


def build_payslip_breakdown(emp, payroll_record, worked_days_count=0, overtime_pay=0.0):
    if payroll_record.payslip_snapshot:
        data = json.loads(payroll_record.payslip_snapshot)
        if data.get('snapshot_version') != 1:
            raise ValueError('Unsupported payslip snapshot version.')
        for issue in data['attendance_issues']:
            issue['date'] = date.fromisoformat(issue['date'])
        data['employee'] = emp
        return data
    if payroll_record.id is not None:
        # Legacy totals are authoritative; missing historical earnings cannot be reconstructed.
        return {
            'employee': emp,
            'legacy': True,
            'warning': 'Legacy payroll: complete historical breakdown was not recorded. '
                       'Saved totals are preserved; current attendance/rates are not substituted.',
            'gross_income': float(payroll_record.gross_income or 0),
            'total_deductions': float(payroll_record.total_deductions or 0),
            'net_pay': float(payroll_record.net_pay or 0),
            'attendance_issues': [],
        }
    return calculate_payslip_breakdown(emp, payroll_record)


def calculate_payslip_breakdown(emp, payroll_record):
    """Return an itemized weekly payslip that reconciles to the payroll record."""
    duplicate_date = db.session.query(Attendance.date).filter(
        Attendance.employee_id == emp.id,
        Attendance.date >= payroll_record.cutoff_start,
        Attendance.date <= payroll_record.cutoff_end,
    ).group_by(Attendance.date).having(db.func.count(Attendance.id) > 1).first()
    if duplicate_date:
        raise ValueError('Reconcile duplicate attendance before recalculating payroll.')
    attendance_records = payroll_attendance_records(
        emp, payroll_record.cutoff_start, payroll_record.cutoff_end + timedelta(days=1)
    )
    worked_days_count = payroll_worked_days_count(emp, attendance_records)

    attendance_issues = attendance_issue_rows(
        emp, payroll_record.cutoff_start, payroll_record.cutoff_end
    )

    daily_rate = float(emp.daily_rate or 0)
    basic_pay = 0.0
    basic_hours = 0.0
    late_ut = 0.0
    late_ut_hours = 0.0
    rest_day_pay = 0.0
    rest_day_hours = 0.0
    special_holiday = 0.0
    regular_holiday = 0.0
    overtime_amounts = {
        "regular_overtime": 0.0,
        "sunday_overtime": 0.0,
        "rest_day": 0.0,
        "special_holiday_ot": 0.0,
        "regular_holiday_ot": 0.0,
    }
    overtime_hours = {key: 0.0 for key in overtime_amounts}
    for attendance in attendance_records:
        holiday = Holiday.query.filter_by(date=attendance.date).first()
        day_pay = regular_day_pay(attendance, daily_rate)
        attendance_hours = regular_attendance_hours(attendance)
        full_day_pay = regular_day_pay(attendance, daily_rate, hours_override=8)
        daily_shortfall = max(full_day_pay - day_pay, 0.0)
        shortfall_hours = max(8.0 - attendance_hours, 0.0)
        trece_sunday = is_trece_sunday(emp, attendance.date)
        if holiday and holiday.holiday_type == "Regular Holiday":
            regular_holiday += day_pay
            late_ut += daily_shortfall
            late_ut_hours += shortfall_hours
        elif holiday:
            special_holiday += day_pay
            late_ut += daily_shortfall
            late_ut_hours += shortfall_hours
        elif attendance.date.weekday() == 6 and not trece_sunday:
            rest_day_pay += day_pay
            late_ut += daily_shortfall
            late_ut_hours += shortfall_hours
        else:
            basic_pay += day_pay
            if not trece_sunday:
                basic_hours += attendance_hours
                late_ut += daily_shortfall
                late_ut_hours += shortfall_hours

        if not attendance_overtime_is_payable(attendance):
            continue
        restday_amount, amount, paid_restday_hours, hours = restday_pay_breakdown(
            attendance, daily_rate
        )
        rest_day_pay += restday_amount
        rest_day_hours += paid_restday_hours
        if holiday and holiday.holiday_type == "Regular Holiday":
            key = "regular_holiday_ot"
        elif holiday:
            key = "special_holiday_ot"
        elif attendance.date.weekday() == 6:
            key = (
                "rest_day"
                if is_restday_overtime(attendance)
                else "sunday_overtime"
            )
        else:
            key = "regular_overtime"
        overtime_amounts[key] += amount
        overtime_hours[key] += hours

    calculated_overtime_pay = sum(overtime_amounts.values())

    gross_income = float(payroll_record.gross_income or 0)
    sss = float(payroll_record.sss or 0)
    philhealth = float(payroll_record.philhealth or 0)
    pagibig = float(payroll_record.pagibig or 0)
    withholding_tax = float(payroll_record.withholding_tax or 0)
    company_loan = float(payroll_record.loan or 0)
    sss_loan = float(getattr(payroll_record, "sss_loan", 0) or 0)
    pagibig_loan = float(getattr(payroll_record, "pagibig_loan", 0) or 0)
    liability_deduction = float(payroll_record.liability_deduction or 0)
    cash_advance = float(payroll_record.cash_advance or 0)
    night_differential = 0.0
    allowance = float(emp.allowance or 0)
    rice_allowance_per_day = max(
        float(getattr(emp, "rice_allowance_per_day", None) or RICE_SUBSIDY_PER_DAY),
        0.0,
    )
    rice_days, rice_monthly_used = rice_allowance_month_context(
        emp, payroll_record.cutoff_start, worked_days_count
    )
    rice_exempt, rice_taxable = de_minimis_allowance_breakdown(
        emp,
        rice_days,
        payroll_cutoffs_in_month(payroll_record.cutoff_start),
        rice_monthly_used=rice_monthly_used,
    )
    incentives = float(emp.incentives or 0)
    regular_overtime = overtime_amounts["regular_overtime"]
    sunday_overtime = overtime_amounts["sunday_overtime"]
    rest_day_overtime = overtime_amounts["rest_day"]
    special_holiday_ot = overtime_amounts["special_holiday_ot"]
    regular_holiday_ot = overtime_amounts["regular_holiday_ot"]

    itemized_earnings = (
        basic_pay + allowance + incentives + rice_taxable + rice_exempt + rest_day_pay
        + special_holiday + regular_holiday + calculated_overtime_pay
        + night_differential
    )
    gross_pay = round(gross_income + rice_exempt, 2)
    adjustment = 0.0
    if abs(round(gross_pay - itemized_earnings, 2)) > 0.01:
        raise ValueError('Calculated earnings do not reconcile to payroll totals.')
    known_deductions = (
        sss + philhealth + pagibig + company_loan + sss_loan
        + pagibig_loan + liability_deduction + cash_advance + withholding_tax
    )
    total_deductions = float(
        payroll_record.total_deductions
        if payroll_record.total_deductions is not None
        else known_deductions + float(payroll_record.other_deductions or 0)
    )
    other_deductions = float(payroll_record.other_deductions or 0)
    if abs(round(total_deductions - known_deductions - other_deductions, 2)) > 0.01:
        raise ValueError('Calculated deductions do not reconcile to payroll totals.')
    net_pay = float(
        payroll_record.net_pay
        if payroll_record.net_pay is not None
        else gross_pay - total_deductions
    )

    if abs(round(gross_pay - total_deductions - net_pay, 2)) > 0.01:
        raise ValueError('Calculated gross, deductions and net pay do not reconcile.')
    return {
        "snapshot_version": 1,
        "legacy": False,
        "daily_rate": daily_rate,
        "company": emp.company,
        "employee_name": emp.full_name(),
        "job_description": emp.job_description,
        "employee": emp,
        "actual_worked_days": worked_days_count,
        "attendance_issues": attendance_issues,
        "basic_hours": basic_hours,
        "basic_pay": basic_pay,
        "late_ut_hours": late_ut_hours,
        "allowance": allowance,
        "rice_allowance": rice_taxable + rice_exempt,
        "rice_allowance_per_day": rice_allowance_per_day,
        "rice_allowance_days": rice_days,
        "rice_allowance_exempt": rice_exempt,
        "rice_allowance_taxable": rice_taxable,
        "incentives": incentives,
        "adjustment": adjustment,
        "payslip_adjustment": round(
            allowance + incentives + sunday_overtime, 2
        ),
        "night_differential": night_differential,
        "regular_overtime": regular_overtime,
        "sunday_overtime": sunday_overtime,
        "rest_day_pay": rest_day_pay,
        "rest_day_hours": rest_day_hours,
        "rest_day": rest_day_overtime,
        "special_holiday": special_holiday,
        "special_holiday_ot": special_holiday_ot,
        "regular_holiday": regular_holiday,
        "regular_holiday_ot": regular_holiday_ot,
        "late_ut": late_ut,
        "sss": sss,
        "philhealth": philhealth,
        "pagibig": pagibig,
        "withholding_tax": withholding_tax,
        "company_loan": company_loan,
        "loan_remaining_balance": round(max(
            float(emp.loan_balance or 0)
            - (0 if payroll_record.loan_deduction_applied else company_loan), 0,
        ), 2),
        "sss_loan": sss_loan,
        "pagibig_loan": pagibig_loan,
        "liability_deduction": liability_deduction,
        "cash_advance": cash_advance,
        "other_deductions": other_deductions,
        "gross_income": gross_income,
        "gross_pay": gross_pay,
        "total_deductions": total_deductions,
        "net_pay": net_pay,
        "regular_overtime_hours": overtime_hours["regular_overtime"],
        "sunday_overtime_hours": overtime_hours["sunday_overtime"],
        "rest_day_overtime_hours": overtime_hours["rest_day"],
        "special_holiday_overtime_hours": overtime_hours["special_holiday_ot"],
        "regular_holiday_overtime_hours": overtime_hours["regular_holiday_ot"],
    }


def weekly_payslip_table_data(payslip):
    """Return the shared earnings and deductions rows for weekly payslip PDFs."""
    if payslip.get('legacy'):
        return [
            ['LEGACY PAYROLL', '', '', 'Saved totals only', '', ''],
            ['Breakdown not recorded', '', '', 'No reconstruction', '', ''],
            ['Saved Gross Income', '', f'{payslip["gross_income"]:,.2f}',
             'Saved Deductions', '', f'{payslip["total_deductions"]:,.2f}'],
            ['Exempt benefits detail', '', 'Not recorded', 'Saved Net Pay', '', f'{payslip["net_pay"]:,.2f}'],
        ]
    amount = lambda key: f"{payslip[key]:,.2f}"
    return [
        ["Earnings", "Days/Hrs", "Amount", "Deductions", "", "Amount"],
        ["Basic Pay", f'{payslip["actual_worked_days"]} / {payslip["basic_hours"]:.2f}', amount("basic_pay"), "Attendance reduction*", f'{payslip["late_ut_hours"]:.2f} hrs', "Included in earnings"],
        ["De Minimis", "", amount("rice_allowance"), "SSS", "", amount("sss")],
        ["Rest Day Pay", "", amount("rest_day_pay"), "PhilHealth", "", amount("philhealth")],
        ["Special Holiday Pay", "", amount("special_holiday"), "Pag-IBIG", "", amount("pagibig")],
        ["Regular Holiday Pay", "", amount("regular_holiday"), "SSS Loan", "", amount("sss_loan")],
        ["Regular OT", f'{payslip["regular_overtime_hours"]:.2f}', amount("regular_overtime"), "Pag-IBIG Loan", "", amount("pagibig_loan")],
        ["Rest Day OT", f'{payslip["rest_day_overtime_hours"]:.2f}', amount("rest_day"), "Company Loan", "", amount("company_loan")],
        ["Special Holiday OT", f'{payslip["special_holiday_overtime_hours"]:.2f}', amount("special_holiday_ot"), "Liability Deduction", "", amount("liability_deduction")],
        ["Regular Holiday OT", f'{payslip["regular_holiday_overtime_hours"]:.2f}', amount("regular_holiday_ot"), "Cash Advance", "", amount("cash_advance")],
        ["Night Differential", "", amount("night_differential"), "Withholding Tax", "", amount("withholding_tax")],
        ["Other Earnings / Adjustment", "", amount("payslip_adjustment"), "Other Deductions", "", amount("other_deductions")],
        ["GROSS PAY", "", amount("gross_pay"), "TOTAL DEDUCTIONS", "", amount("total_deductions")],
        ["Remaining Loan", "", f'{payslip["loan_remaining_balance"]:,.2f}' if "loan_remaining_balance" in payslip else "Not recorded", "NET PAY", "", amount("net_pay")],
        ["*Already in basic pay", "", "", "Not deducted again", "", ""],
    ]


@app.route('/payroll/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def payroll(employee_id):
    has_payroll_access = can_manage_admin_payroll(current_user)
    if not has_payroll_access and current_user.id != employee_id:
        return 'Access denied', 403
    emp = Employee.query.get_or_404(employee_id)
    history = Payroll.query.filter_by(employee_id=employee_id).order_by(
        Payroll.cutoff_start.desc()
    ).all()

    # Staff without payroll-management access may only view their own records.
    if not has_payroll_access:
        history = Payroll.query.filter_by(employee_id=current_user.id)\
                               .order_by(Payroll.cutoff_start.desc()).all()
        latest_finalized_payslip = Payroll.query.filter_by(
            employee_id=current_user.id,
            is_paid=True
        ).order_by(Payroll.cutoff_start.desc()).first()

        cutoff_options = [f"{p.cutoff_start.strftime('%b %d, %Y')} - {p.cutoff_end.strftime('%b %d, %Y')}" for p in history]
        years = sorted({p.cutoff_start.year for p in history}, reverse=True)

        selected_year = request.args.get("year")
        if selected_year:
            history = Payroll.query.filter_by(employee_id=current_user.id)\
                                   .filter(extract('year', Payroll.cutoff_start) == int(selected_year))\
                                   .order_by(Payroll.cutoff_start.desc()).all()

        return render_template("payroll.html",
                               emp=current_user,
                               history=history,
                               cutoff_options=cutoff_options,
                               years=years,
                               selected_year=selected_year,
                               latest_finalized_payslip=latest_finalized_payslip,
                               now=datetime.now())

    # ADMIN VIEW
    try:
        cutoff_start, cutoff_end = payroll_cutoff_from_request(request.values.get('cutoff_start'))
    except ValueError:
        flash('Select a Saturday cutoff start date.', 'danger')
        return redirect(url_for('payroll_dashboard'))
    start_cutoff = datetime.combine(cutoff_start, time.min)
    end_cutoff = datetime.combine(cutoff_end, time.min)
    end_cutoff = start_cutoff + timedelta(days=7)

    cutoff_record_count = Payroll.query.filter_by(
        employee_id=emp.id, cutoff_start=cutoff_start,
        cutoff_end=cutoff_end - timedelta(days=1),
    ).count()
    if cutoff_record_count > 1:
        flash('Multiple saved payroll records exist for this cutoff. Review individual history records; editing is blocked.', 'danger')
        if request.method == 'POST':
            return redirect(url_for('payroll', employee_id=emp.id, cutoff_start=cutoff_start))

    if request.method == 'POST':
        # Update payroll fields
        emp.daily_rate = float(request.form.get('daily_rate', emp.daily_rate))
        emp.allowance = float(request.form.get('allowance', emp.allowance))
        emp.incentives = float(request.form.get('incentives', emp.incentives))

        # Allow admin to set/update existing loan balance
        loan_input = request.form.get('loan_balance')
        if loan_input is not None and loan_input.strip() != "":
            emp.loan_balance = float(loan_input)

        loan_inputs = {
            "loan": request.form.get('loan_deduction'),
            "sss_loan": request.form.get('sss_loan'),
            "pagibig_loan": request.form.get('pagibig_loan'),
            "cash_advance": request.form.get('cash_advance'),
            "withholding_tax_override": request.form.get('withholding_tax'),
            "liability_deduction_override": request.form.get('liability_deduction'),
            "other_deductions": request.form.get('other_deductions'),
        }
        provided_loan_inputs = {
            key: value
            for key, value in loan_inputs.items()
            if value is not None and value.strip() != ""
        }
        save_payroll = request.form.get('save_payroll') == '1'
        if provided_loan_inputs:
            payroll_record = Payroll.query.filter_by(
                employee_id=emp.id,
                cutoff_start=start_cutoff.date(),
                cutoff_end=(end_cutoff - timedelta(days=1)).date()
            ).first()
            if payroll_record and (
                payroll_record.is_paid or payroll_record.loan_deduction_applied
            ):
                db.session.rollback()
                flash(
                    "Reopen this payroll before changing deductions.",
                    "warning",
                )
                return redirect(url_for(
                    'payroll',
                    employee_id=employee_id,
                    cutoff_start=cutoff_start.isoformat(),
                ))
            try:
                parsed_loan_inputs = {
                    key: float(value)
                    for key, value in provided_loan_inputs.items()
                }
            except ValueError:
                db.session.rollback()
                flash("Enter valid deduction amounts.", "danger")
                return redirect(url_for(
                    'payroll',
                    employee_id=employee_id,
                    cutoff_start=cutoff_start.isoformat(),
                ))
            if any(value < 0 for value in parsed_loan_inputs.values()):
                db.session.rollback()
                flash("Deductions cannot be negative.", "danger")
                return redirect(url_for(
                    'payroll',
                    employee_id=employee_id,
                    cutoff_start=cutoff_start.isoformat(),
                ))
            if payroll_record is None:
                payroll_record = Payroll(
                    employee_id=emp.id,
                    cutoff_start=start_cutoff.date(),
                    cutoff_end=(end_cutoff - timedelta(days=1)).date()
                )
                db.session.add(payroll_record)
            for key, value in parsed_loan_inputs.items():
                if key == "loan":
                    value = loan_cutoff_deduction(emp.loan_balance, value)
                    payroll_record.loan_deduction_override = value
                setattr(payroll_record, key, value)
            payroll_record.payslip_snapshot = None

        sil_eligible_input = request.form.get('sil_eligible')
        if sil_eligible_input is not None:
            emp.sil_eligible = sil_eligible_input == '1'

        db.session.commit()
        if save_payroll:
            return redirect(url_for(
                'recalculate_payroll',
                employee_id=employee_id,
                cutoff_start=cutoff_start.isoformat(),
            ))
        flash("✅ Payroll updated successfully!", "success")
        return redirect(url_for(
            'payroll',
            employee_id=employee_id,
            cutoff_start=cutoff_start.isoformat(),
        ))

    # Compute payroll
    paid_attendance = payroll_attendance_records(emp, cutoff_start, cutoff_end)
    worked_days_count = payroll_worked_days_count(emp, paid_attendance)

    daily_rate = emp.daily_rate or 0
    basic_pay = sum(regular_day_pay(attendance, daily_rate) for attendance in paid_attendance)
    approved_overtime_pay = sum(
        payroll_extra_pay(attendance, daily_rate)
        for attendance in paid_attendance
        if attendance_overtime_is_payable(attendance)
    )
    weeks_in_month = payroll_cutoffs_in_month(start_cutoff.date())
    rice_days, rice_monthly_used = rice_allowance_month_context(
        emp, start_cutoff.date(), worked_days_count
    )
    rice_exempt, rice_taxable = de_minimis_allowance_breakdown(
        emp,
        rice_days,
        weeks_in_month,
        rice_monthly_used=rice_monthly_used,
    )

    cutoff_salary = sum(
        regular_day_pay(attendance, daily_rate) for attendance in paid_attendance
    )
    contribution_salary = cutoff_salary + rice_taxable
    contributions = cutoff_statutory_contributions(emp, cutoff_start, contribution_salary)
    deductions = contributions['employee']
    employer_deductions = contributions['employer']

    payroll_record = Payroll.query.filter_by(
        employee_id=emp.id,
        cutoff_start=start_cutoff.date(),
        cutoff_end=(end_cutoff - timedelta(days=1)).date()
    ).first()
    if not emp.payroll_attendance_exempt:
        deductions = payroll_statutory_deductions(payroll_record, deductions)
    sss = deductions["sss"]
    philhealth = deductions["philhealth"]
    pagibig = deductions["pagibig"]
    loan = automatic_loan_deduction(emp, payroll_record, cutoff_start)
    sss_loan = float(payroll_record.sss_loan or 0) if payroll_record else 0.0
    pagibig_loan = float(payroll_record.pagibig_loan or 0) if payroll_record else 0.0
    liability_deduction = liability_cutoff_deduction(emp.id)

    gross_income = basic_pay + (emp.allowance or 0) + (emp.incentives or 0) + rice_taxable + approved_overtime_pay
    monthly_taxable_income = contribution_taxable_month(
        contributions, gross_income, weeks_in_month
    )
    withholding_tax = weekly_withholding_tax(
        emp, monthly_taxable_income, weeks_in_month
    )
    if payroll_record is not None and payroll_record.withholding_tax_override is not None:
        withholding_tax = float(payroll_record.withholding_tax_override)
    cash_advance = float(payroll_record.cash_advance or 0) if payroll_record else 0.0
    other_deductions = float(payroll_record.other_deductions or 0) if payroll_record else 0.0
    total_deductions = (
        sss + philhealth + pagibig + sss_loan + pagibig_loan + loan
        + liability_deduction + withholding_tax + cash_advance + other_deductions
    )
    net_pay = gross_income + rice_exempt - total_deductions

    if payroll_record is None:
        payroll_record = Payroll(
            employee_id=emp.id,
            cutoff_start=start_cutoff.date(),
            cutoff_end=(end_cutoff - timedelta(days=1)).date()
        )
    if payroll_record.id is None:
        save_payroll_snapshot(
            payroll_record,
            calculate_payroll_snapshot(emp, payroll_record, cutoff_start, cutoff_end),
        )
    gross_income = payroll_record.gross_income
    total_deductions = payroll_record.total_deductions
    net_pay = payroll_record.net_pay
    if request.args.get('save') == 'true':
        return redirect(url_for(
            'recalculate_payroll', employee_id=emp.id, cutoff_start=cutoff_start
        ))
    if request.args.get('finalize') == 'true':
        flash('Use the explicit Finalize Payroll action; a page visit cannot finalize payroll.', 'warning')

    # Verification QR codes require a stable payroll record reference.
    if request.args.get("download") == "true" and payroll_record.id is None:
        flash('Preview and save the draft before downloading an official payslip.', 'warning')
        return redirect(url_for(
            'recalculate_payroll', employee_id=emp.id, cutoff_start=cutoff_start
        ))

    payslip = build_payslip_breakdown(emp, payroll_record, worked_days_count, approved_overtime_pay)
    verification = None
    qr_bytes = None
    if request.args.get("download") == "true" and payroll_record.id is not None:
        verification = build_payslip_verification(
            emp.id,
            payroll_record.id,
            payroll_record.cutoff_start,
            payroll_record.cutoff_end,
            payroll_record.net_pay,
        )
        qr_bytes = generate_qr_image_bytes(verification['verify_url'])
    generated_at = datetime.now(ZoneInfo('Asia/Manila'))

    # 👉 Generate payslip PDF in memory
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)

    snapshot_company = payslip.get('company', emp.company)
    company_display = (
        'TRECE-UNO AUTO SUPPLY' if str(snapshot_company or '').lower().startswith('trece')
        else 'AUTO-EXPERT AUTO SUPPLY'
    )
    company_brand = "TRECE-UNO" if company_display.startswith("TRECE") else "AUTO-EXPERT"

    # Header
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 780, company_display)
    c.setFont("Helvetica", 12)
    c.drawString(50, 760, "Official Payslip")

    c.setFont("Helvetica", 9)
    c.drawString(50, 730, f'Employee: {payslip.get("employee_name", emp.full_name())} (ID: {emp.id})')
    c.drawString(50, 714, f"Cutoff: {payroll_record.cutoff_start} to {payroll_record.cutoff_end}")
    c.drawString(50, 698, f'Position: {payslip.get("job_description", emp.job_description) or "N/A"}')
    rate_label = 'Current rate' if payslip.get('legacy') else 'Snapshot rate'
    c.drawString(300, 714, f'{rate_label}: PHP {float(payslip.get("daily_rate", emp.daily_rate) or 0):.2f}')
    c.drawString(300, 698, f"Department: {snapshot_company or 'N/A'}")
    c.drawString(
        50,
        682,
        f"Reference: {verification['document_id'] if verification else f'PAY-{payroll_record.id or 0:06d}'}",
    )
    c.drawString(300, 682, f"Generated: {generated_at:%Y-%m-%d %I:%M %p} PHT")

    data = weekly_payslip_table_data(payslip)

    table = Table(data, colWidths=[120, 60, 80, 120, 60, 80])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.whitesmoke),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BACKGROUND', (0,-2), (-1,-1), colors.lightgrey),
        ('FONTNAME', (0,-2), (2,-2), 'Helvetica-Bold'),
        ('FONTNAME', (3,-2), (5,-1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.75, colors.black),
        ('ALIGN', (1,1), (-1,-1), 'CENTER'),
        ('ALIGN', (2,2), (2,-1), 'RIGHT'),
        ('ALIGN', (5,2), (5,-1), 'RIGHT'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 4),
    ]))
    _, table_height = table.wrapOn(c, 50, 600)
    table_y = 675 - table_height
    table.drawOn(c, 20, table_y)

    signature_y = table_y - 20
    if payslip["attendance_issues"]:
        attendance_rows = [["Attendance Issue Dates", "Issue"]]
        attendance_rows.extend(
            [
                [issue["date"].strftime("%Y-%m-%d"), ", ".join(issue["issues"])]
                for issue in payslip["attendance_issues"]
            ]
        )
        attendance_table = Table(
            attendance_rows,
            colWidths=[150, 250],
            repeatRows=1,
        )
        attendance_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.whitesmoke),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('TOPPADDING', (0,0), (-1,-1), 3),
        ]))
        _, attendance_table_height = attendance_table.wrapOn(c, 400, 200)
        attendance_table_y = table_y - attendance_table_height - 8
        attendance_table.drawOn(c, 50, attendance_table_y)
        signature_y = attendance_table_y - 20

    c.line(50, signature_y, 550, signature_y)
    c.setFont("Helvetica-Oblique", 10)
    c.drawString(50, signature_y - 15, "Authorized by Admin")
    c.line(50, signature_y - 60, 250, signature_y - 60)
    c.line(350, signature_y - 60, 550, signature_y - 60)
    c.setFont("Helvetica", 10)
    c.drawString(50, signature_y - 75, "Authorized Person Signature")
    c.drawString(350, signature_y - 75, "Date")
    if qr_bytes:
        c.drawImage(ImageReader(io.BytesIO(qr_bytes)), 485, 24, width=60, height=60)
        c.setFont("Helvetica-Oblique", 7)
        c.drawString(440, 15, "Scan QR to verify")

    c.showPage()
    c.save()
    buffer.seek(0)

    # If download requested
    if request.args.get("download") == "true":
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"Payslip_{emp.first_name}_{emp.last_name}.pdf",
            mimetype="application/pdf"
        )

    return render_template("payroll.html",
         emp=emp,
         history=history,
         payslip=payslip,
         payroll_record=payroll_record,
            loan_deduction=loan,
         cutoff_start=cutoff_start,
         can_manage_payroll=has_payroll_access,
         loan_editable=not (
             payroll_record.is_paid or payroll_record.loan_deduction_applied
         ),
         selected_year=None,
         years=[],
         now=datetime.now())


@app.route('/payroll/<int:employee_id>/monthly')
@login_required
def monthly_payroll(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    if current_user.id != employee_id and not can_manage_admin_payroll(current_user):
        return 'Access denied', 403

    month_value = request.args.get('month')
    try:
        month_start = datetime.strptime(month_value, '%Y-%m').date() if month_value else datetime.today().date().replace(day=1)
    except ValueError:
        month_start = datetime.today().date().replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    records = Payroll.query.filter(
        Payroll.employee_id == employee_id,
        Payroll.cutoff_start >= month_start,
        Payroll.cutoff_start < next_month
    ).order_by(Payroll.cutoff_start).all()
    totals = {
        'gross_income': sum(float(record.gross_income or 0) for record in records),
        'sss': sum(float(record.sss or 0) for record in records),
        'philhealth': sum(float(record.philhealth or 0) for record in records),
        'pagibig': sum(float(record.pagibig or 0) for record in records),
        'withholding_tax': sum(float(record.withholding_tax or 0) for record in records),
        'deductions': sum(float(record.total_deductions or 0) for record in records),
        'net_pay': sum(float(record.net_pay or 0) for record in records),
        'loan': sum(float(record.loan or 0) for record in records),
        'liability_deduction': sum(float(record.liability_deduction or 0) for record in records),
        'cash_advance': sum(float(record.cash_advance or 0) for record in records),
    }

    if request.args.get('download') == 'true':
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        pdf.setFont('Helvetica-Bold', 16)
        pdf.drawString(50, 780, 'MONTHLY PAYSLIP SUMMARY')
        pdf.setFont('Helvetica', 10)
        pdf.drawString(50, 750, f'Employee: {emp.first_name} {emp.last_name} (ID: {emp.id})')
        pdf.drawString(50, 735, f'Month: {month_start.strftime("%B %Y")}')
        pdf.setFont('Helvetica', 8)
        pdf.drawString(50, 718, 'Saved values; Gross Income excludes exempt benefits. Legacy benefits breakdown may be incomplete.')
        y = 690
        for label, key in [
            ('Gross Income', 'gross_income'),
            ('SSS', 'sss'),
            ('PhilHealth', 'philhealth'),
            ('Pag-IBIG', 'pagibig'),
            ('Withholding Tax', 'withholding_tax'),
            ('Loan Deductions', 'loan'),
            ('Recoverable Receivable / Liability', 'liability_deduction'),
            ('Cash Advance', 'cash_advance'),
            ('Total Deductions', 'deductions'),
            ('NET PAY', 'net_pay'),
        ]:
            pdf.setFont('Helvetica-Bold' if key == 'net_pay' else 'Helvetica', 12)
            pdf.drawString(70, y, label)
            pdf.drawRightString(500, y, f'PHP {totals[key]:,.2f}')
            y -= 28
        pdf.line(70, y - 20, 260, y - 20)
        pdf.line(330, y - 20, 500, y - 20)
        pdf.setFont('Helvetica', 10)
        pdf.drawString(70, y - 35, 'Authorized Person Signature')
        pdf.drawString(330, y - 35, 'Date')
        pdf.showPage()
        pdf.save()
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f'Monthly_Payslip_{emp.first_name}_{month_start:%Y_%m}.pdf', mimetype='application/pdf')

    return render_template('monthly_payslip.html', emp=emp, month_start=month_start, records=records, totals=totals)


def calculate_payroll_snapshot(employee, record, cutoff_start, cutoff_end):
    attendance = payroll_attendance_records(employee, cutoff_start, cutoff_end)
    daily_rate = float(employee.daily_rate or 0)
    basic_pay = sum(regular_day_pay(item, daily_rate) for item in attendance)
    overtime_pay = sum(
        payroll_extra_pay(item, daily_rate)
        for item in attendance if attendance_overtime_is_payable(item)
    )
    weeks = payroll_cutoffs_in_month(cutoff_start)
    rice_days, rice_used = rice_allowance_month_context(
        employee, cutoff_start, payroll_worked_days_count(employee, attendance)
    )
    rice_exempt, rice_taxable = de_minimis_allowance_breakdown(
        employee, rice_days, weeks, rice_monthly_used=rice_used,
    )
    contribution_salary = basic_pay + rice_taxable
    contributions = cutoff_statutory_contributions(employee, cutoff_start, contribution_salary)
    statutory = contributions['employee']
    employer = contributions['employer']
    if not employee.payroll_attendance_exempt:
        statutory = payroll_statutory_deductions(record, statutory)
    gross = (
        basic_pay + float(employee.allowance or 0)
        + float(employee.incentives or 0) + rice_taxable + overtime_pay
    )
    statutory_total = sum(
        float(statutory.get(field, 0) or 0)
        for field in ('sss', 'philhealth', 'pagibig')
    )
    tax = weekly_withholding_tax(
        employee, contribution_taxable_month(contributions, gross, weeks), weeks
    )
    if record.withholding_tax_override is not None:
        tax = float(record.withholding_tax_override)
    liability = (
        float(record.liability_deduction_override)
        if record.liability_deduction_override is not None
        else float(record.liability_deduction or 0)
        if record.liability_deduction_applied
        else liability_cutoff_deduction(employee.id)
    )
    loan = automatic_loan_deduction(employee, record, cutoff_start)
    total = statutory_total + tax + liability + loan + sum(
        float(getattr(record, field) or 0)
        for field in ('sss_loan', 'pagibig_loan', 'cash_advance', 'other_deductions')
    )
    snapshot = {
        'gross_income': gross, 'total_deductions': total,
        'net_pay': gross + rice_exempt - total,
        'sss': statutory['sss'], 'philhealth': statutory['philhealth'],
        'pagibig': statutory['pagibig'], 'withholding_tax': tax,
        'loan': loan, 'liability_deduction': liability, 'contribution_salary_base': contributions['basis'],
        'employer_sss': employer['sss'], 'employer_sss_ec': employer['sss_ec'],
        'employer_philhealth': employer['philhealth'], 'employer_pagibig': employer['pagibig'],
    }
    if any(not isfinite(float(value)) for value in snapshot.values()):
        raise ValueError(f'Invalid payroll amounts for employee #{employee.id}.')
    return snapshot


def save_payroll_snapshot(record, snapshot):
    with db.session.no_autoflush:
        proposed = Payroll(**{
            column.name: getattr(record, column.name)
            for column in Payroll.__table__.columns
            if column.name not in ('id', 'payslip_snapshot')
        })
        for field, value in snapshot.items():
            setattr(proposed, field, value)
        employee = db.session.get(Employee, record.employee_id)
        breakdown = calculate_payslip_breakdown(employee, proposed)
        serializable = {key: value for key, value in breakdown.items() if key != 'employee'}
        serialized = json.dumps(
            serializable, default=lambda value: value.isoformat(), allow_nan=False
        )
    for field, value in snapshot.items():
        setattr(record, field, value)
    record.payslip_snapshot = serialized


def draft_payroll_preview(employee, record, cutoff_start, cutoff_end):
    projected = Payroll(
        employee_id=employee.id, cutoff_start=cutoff_start,
        cutoff_end=cutoff_end - timedelta(days=1),
    )
    if record:
        for column in Payroll.__table__.columns:
            if column.name not in ('id', 'payslip_snapshot'):
                setattr(projected, column.name, getattr(record, column.name))
    snapshot = calculate_payroll_snapshot(employee, projected, cutoff_start, cutoff_end)
    save_payroll_snapshot(projected, snapshot)
    original = {
        column.name: backup_cell_value(getattr(record, column.name))
        for column in Payroll.__table__.columns
    } if record else None
    fingerprint = hashlib.sha256(json.dumps(
        {'original': original, 'snapshot': snapshot, 'detail': projected.payslip_snapshot},
        sort_keys=True, allow_nan=False,
    ).encode()).hexdigest()
    return projected, snapshot, fingerprint


@app.route('/payroll/<int:employee_id>/recalculate', methods=['GET', 'POST'])
@login_required
def recalculate_payroll(employee_id):
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    employee = Employee.query.get_or_404(employee_id)
    try:
        start, end = payroll_cutoff_from_request(request.values.get('cutoff_start'))
    except ValueError:
        flash('Select a Saturday cutoff start date.', 'danger')
        return redirect(url_for('payroll_dashboard'))
    csrf_token = session.setdefault('payroll_recalculation_csrf', secrets.token_urlsafe(32))
    if request.method == 'POST':
        if not secrets.compare_digest(
            csrf_token.encode(), request.form.get('csrf_token', '').encode()
        ):
            abort(400, description='Invalid recalculation form.')
        lock_attendance_employee(employee.id)
    records = Payroll.query.filter_by(
        employee_id=employee.id, cutoff_start=start, cutoff_end=end - timedelta(days=1)
    ).all()
    if len(records) > 1:
        flash('Multiple payroll records exist for this cutoff. Review them separately; no totals were changed.', 'danger')
        return redirect(url_for('payroll', employee_id=employee.id, cutoff_start=start))
    record = records[0] if records else None
    if record and (
        record.is_paid or record.loan_deduction_applied or record.liability_deduction_applied
    ):
        flash('Only unpaid, unapplied drafts may be recalculated. Historical totals were not changed.', 'warning')
        return redirect(url_for('payroll', employee_id=employee.id, cutoff_start=start))
    try:
        projected, snapshot, fingerprint = draft_payroll_preview(employee, record, start, end)
        if request.method == 'POST':
            preview = session.pop('payroll_recalculation_preview', None)
            if (
                not preview or preview['employee_id'] != employee.id
                or preview['start'] != start.isoformat()
                or preview['fingerprint'] != fingerprint
                or datetime.fromisoformat(preview['expires_at']) < datetime.utcnow()
                or request.form.get('approved') != 'yes'
            ):
                db.session.rollback()
                flash('Preview expired, changed, or not approved. Review the new preview before saving.', 'danger')
                return redirect(url_for('recalculate_payroll', employee_id=employee.id, cutoff_start=start))
            if record is None:
                record = Payroll(employee_id=employee.id, cutoff_start=start, cutoff_end=end - timedelta(days=1))
                db.session.add(record)
            for key, value in snapshot.items():
                setattr(record, key, value)
            record.payslip_snapshot = projected.payslip_snapshot
            db.session.add(AuditLog(
                employee_id=current_user.id, employee_name=current_user.full_name(),
                action=f'Approved draft recalculation employee {employee.id}',
                company=current_user.company, path=request.path,
                ip_address=request.remote_addr, status_code=302,
            ))
            db.session.commit()
            flash('Approved draft saved with itemized snapshot. Payroll was not finalized and balances were not deducted.', 'success')
            return redirect(url_for('payroll', employee_id=employee.id, cutoff_start=start))
        session['payroll_recalculation_preview'] = {
            'employee_id': employee.id, 'start': start.isoformat(),
            'fingerprint': fingerprint,
            'expires_at': (datetime.utcnow() + timedelta(minutes=10)).isoformat(),
        }
    except (ValueError, SQLAlchemyError):
        db.session.rollback()
        logger.exception('Could not preview or save payroll recalculation employee %s', employee_id)
        flash('Payroll could not be reconciled. No recalculated totals were saved. Review the source records.', 'danger')
        return redirect(url_for('payroll_dashboard', cutoff_start=start))
    return render_template(
        'payroll_recalculation.html', emp=employee, original=record, projected=projected,
        cutoff_start=start, csrf_token=csrf_token,
        rows=weekly_payslip_table_data(build_payslip_breakdown(employee, projected)),
    )


def finalize_payroll_record(employee, record, cutoff_start, cutoff_end):
    lock_attendance_employee(employee.id)
    if record.is_paid:
        return False
    save_payroll_snapshot(
        record, calculate_payroll_snapshot(employee, record, cutoff_start, cutoff_end)
    )
    if not record.loan_deduction_applied:
        employee.loan_balance = max(
            float(employee.loan_balance or 0) - float(record.loan or 0), 0,
        )
        record.loan_deduction_applied = True
    if not record.liability_deduction_applied:
        apply_liability_deduction(employee.id, record.liability_deduction)
        record.liability_deduction_applied = True
    record.is_paid = True
    record.confirmation_status = 'Pending'
    record.confirmation_note = None
    record.confirmed_at = None
    return True


@app.route('/payroll/zero-review', methods=['GET', 'POST'])
@login_required
def review_zero_payroll():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    records = Payroll.query.filter(
        Payroll.is_paid.is_(True),
        db.or_(Payroll.gross_income == 0, Payroll.gross_income.is_(None)),
        db.or_(Payroll.total_deductions == 0, Payroll.total_deductions.is_(None)),
        db.or_(Payroll.net_pay == 0, Payroll.net_pay.is_(None)),
    ).order_by(Payroll.cutoff_start.desc(), Payroll.employee_id).all()
    rows = []
    selected = set(request.form.getlist('payroll_ids')) if request.method == 'POST' else set()
    for record in records:
        employee = db.session.get(Employee, record.employee_id)
        end = record.cutoff_end + timedelta(days=1)
        snapshot = calculate_payroll_snapshot(employee, record, record.cutoff_start, end)
        repairable = snapshot['gross_income'] > 0 or snapshot['net_pay'] > 0
        rows.append({'record': record, 'employee': employee, 'snapshot': snapshot, 'repairable': repairable})
        if str(record.id) in selected and repairable:
            save_payroll_snapshot(record, snapshot)
            record.confirmation_status = 'Pending'
            record.confirmation_note = None
            record.confirmed_at = None
            db.session.add(AuditLog(
                employee_id=current_user.id, employee_name=current_user.full_name(),
                company=current_user.company, action=f'Repaired zero payroll #{record.id}',
                path=request.path, ip_address=request.remote_addr, status_code=302,
            ))
    if request.method == 'POST':
        repaired = sum(str(row['record'].id) in selected and row['repairable'] for row in rows)
        db.session.commit()
        flash(
            f'{repaired} zero payroll records repaired. Loan and liability balances were not deducted again.',
            'success' if repaired else 'warning',
        )
        return redirect(url_for('review_zero_payroll'))
    return render_template('payroll_zero_review.html', rows=rows)


@app.route('/payroll/<int:employee_id>/finalize', methods=['POST'])
@login_required
def finalize_payroll(employee_id):
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    employee = Employee.query.get_or_404(employee_id)
    try:
        cutoff_start, cutoff_end = payroll_cutoff_from_request(request.form.get('cutoff_start'))
    except ValueError:
        flash('Select a Saturday cutoff start date.', 'danger')
        return redirect(url_for('payroll_dashboard'))
    lock_attendance_employee(employee.id)
    db.session.refresh(employee)
    existing_records = Payroll.query.filter_by(
        employee_id=employee.id, cutoff_start=cutoff_start,
        cutoff_end=cutoff_end - timedelta(days=1)
    ).all()
    if len(existing_records) > 1:
        flash('Multiple payroll records exist for this cutoff. Finalization was blocked; review separately.', 'danger')
        return redirect(url_for('payroll', employee_id=employee.id, cutoff_start=cutoff_start))
    existing = existing_records[0] if existing_records else None
    if existing and existing.is_paid:
        flash('Payroll is already finalized. Its saved totals and snapshot were not changed.', 'warning')
        return redirect(url_for('payroll', employee_id=employee.id, cutoff_start=cutoff_start))
    field_values = {
        'daily_rate': request.form.get(f'daily_rate_{employee_id}'),
        'allowance': request.form.get(f'allowance_{employee_id}'),
        'incentives': request.form.get(f'incentives_{employee_id}'),
        'loan_balance': request.form.get(f'loan_{employee_id}'),
    }
    try:
        for field_name, value in field_values.items():
            if value is not None and value.strip() != '':
                if field_name == 'loan_balance':
                    employee.loan_balance = max(float(value), 0)
                else:
                    setattr(employee, field_name, max(float(value), 0))
        loan_deduction = request.form.get(f'loan_deduction_{employee_id}')
        if loan_deduction is not None and loan_deduction.strip() != '':
            payroll_record = Payroll.query.filter_by(
                employee_id=employee_id,
                cutoff_start=cutoff_start,
                cutoff_end=cutoff_end - timedelta(days=1)
            ).first()
            if payroll_record is None:
                payroll_record = Payroll(
                    employee_id=employee_id,
                    cutoff_start=cutoff_start,
                    cutoff_end=cutoff_end - timedelta(days=1)
                )
                db.session.add(payroll_record)
            payroll_record.loan = (
                0.0 if approved_loan_deduction_skip(employee_id, cutoff_start)
                else loan_cutoff_deduction(employee.loan_balance, float(loan_deduction))
            )
            payroll_record.loan_deduction_override = payroll_record.loan
        update_manual_owner_contribution_settings(employee, request.form, cutoff_start)
        record = Payroll.query.filter_by(
            employee_id=employee.id, cutoff_start=cutoff_start,
            cutoff_end=cutoff_end - timedelta(days=1)
        ).first()
        if record is None:
            record = Payroll(employee_id=employee.id, cutoff_start=cutoff_start, cutoff_end=cutoff_end - timedelta(days=1))
            db.session.add(record)
        finalize_payroll_record(employee, record, cutoff_start, cutoff_end)
        db.session.commit()
    except (TypeError, ValueError, SQLAlchemyError):
        db.session.rollback()
        logger.exception('Could not finalize payroll employee %s', employee_id)
        flash('Payroll could not be finalized. Check the inputs and source records; no changes were saved.', 'danger')
        return redirect(url_for('payroll_dashboard'))
    return redirect(url_for('payroll', employee_id=employee.id, cutoff_start=cutoff_start))


@app.route('/payroll/bulk-finalize', methods=['POST'])
@login_required
def bulk_finalize_payroll():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    try:
        cutoff_start, cutoff_end = payroll_cutoff_from_request(request.form.get('cutoff_start'))
    except ValueError:
        flash('Select a Saturday cutoff start date.', 'danger')
        return redirect(url_for('payroll_dashboard'))

    selected_ids = request.form.getlist('selected_employee_ids')
    if not selected_ids:
        flash('Select at least one employee to finalize.', 'warning')
        return redirect(url_for('payroll_dashboard', cutoff_start=cutoff_start))

    finalized = 0
    try:
        for employee_id in sorted(selected_ids, key=int):
            employee = db.session.get(Employee, int(employee_id))
            if employee is None or employee.payroll_attendance_exempt:
                continue
            lock_attendance_employee(employee.id)
            db.session.refresh(employee)
            records = Payroll.query.filter_by(
                employee_id=int(employee_id),
                cutoff_start=cutoff_start,
                cutoff_end=cutoff_end - timedelta(days=1)
            ).all()
            if len(records) > 1:
                raise ValueError(f'Multiple payroll records for employee {employee.id}.')
            payroll_record = records[0] if records else None
            if payroll_record is None:
                payroll_record = Payroll(
                    employee_id=employee.id, cutoff_start=cutoff_start,
                    cutoff_end=cutoff_end - timedelta(days=1),
                )
                db.session.add(payroll_record)
            if finalize_payroll_record(employee, payroll_record, cutoff_start, cutoff_end):
                finalized += 1
        db.session.commit()
    except (TypeError, ValueError, SQLAlchemyError):
        db.session.rollback()
        logger.exception('Could not bulk finalize payroll cutoff %s', cutoff_start)
        flash('Bulk finalization failed. Review the inputs and source records; no changes were saved.', 'danger')
        return redirect(url_for('payroll_dashboard', cutoff_start=cutoff_start))
    flash(f'{finalized} payroll records finalized for this cutoff.', 'success')
    return redirect(url_for('payroll_dashboard'))


@app.route('/payroll/<int:employee_id>/reopen', methods=['POST'])
@login_required
def reopen_payroll(employee_id):
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    employee = Employee.query.get_or_404(employee_id)
    lock_attendance_employee(employee.id)
    db.session.refresh(employee)
    cutoff_start, cutoff_end = completed_cutoff()
    payroll_record = Payroll.query.filter_by(
        employee_id=employee_id,
        cutoff_start=cutoff_start,
        cutoff_end=cutoff_end - timedelta(days=1)
    ).first()
    if payroll_record:
        if payroll_record.loan_deduction_applied:
            employee = db.session.get(Employee, employee_id)
            employee.loan_balance = float(employee.loan_balance or 0) + float(payroll_record.loan or 0)
            payroll_record.loan_deduction_applied = False
        if payroll_record.liability_deduction_applied:
            reverse_liability_deduction(employee_id, payroll_record.liability_deduction)
            payroll_record.liability_deduction_applied = False
        payroll_record.is_paid = False
        payroll_record.confirmation_status = None
        payroll_record.confirmation_note = None
        payroll_record.confirmed_at = None
        db.session.commit()
        flash('Payroll reopened and its loan deduction was reversed. Open the payroll page to recalculate it.', 'success')
    else:
        flash('No payroll record found for the current cutoff.', 'warning')
    return redirect(url_for('payroll', employee_id=employee_id))


@app.route('/payslip/<int:emp_id>/<int:payroll_id>')
@login_required
def payslip(emp_id, payroll_id):
    employee = Employee.query.get_or_404(emp_id)
    payroll_record = Payroll.query.filter_by(
        id=payroll_id,
        employee_id=emp_id
    ).first_or_404()

    if current_user.id != emp_id and not can_manage_admin_payroll(current_user):
        flash("Access denied.", "danger")
        return redirect(url_for('dashboard_staff'))

    breakdown = build_payslip_breakdown(employee, payroll_record)
    verification = build_payslip_verification(
        employee.id,
        payroll_record.id,
        payroll_record.cutoff_start,
        payroll_record.cutoff_end,
        payroll_record.net_pay,
    )
    qr_bytes = generate_qr_image_bytes(verification['verify_url'])

    return render_template(
        "payslip.html",
        employee=employee,
        payroll=payroll_record,
        payslip=breakdown,
        verification=verification,
        qr_data_uri=(
            f"data:image/png;base64,{base64.b64encode(qr_bytes).decode('ascii')}"
            if qr_bytes else None
        ),
        generated_at=datetime.now(ZoneInfo('Asia/Manila')),
    )


@app.route('/payslip/<int:emp_id>/<int:payroll_id>/download')
@login_required
def download_payslip(emp_id, payroll_id):
    employee = Employee.query.get_or_404(emp_id)
    payroll_record = Payroll.query.filter_by(id=payroll_id, employee_id=emp_id).first_or_404()
    if current_user.id != emp_id and not can_manage_admin_payroll(current_user):
        return 'Access denied', 403

    payslip = build_payslip_breakdown(employee, payroll_record)
    verification = build_payslip_verification(
        employee.id,
        payroll_record.id,
        payroll_record.cutoff_start,
        payroll_record.cutoff_end,
        payroll_record.net_pay,
    )
    qr_bytes = generate_qr_image_bytes(verification['verify_url'])
    generated_at = datetime.now(ZoneInfo('Asia/Manila'))

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont('Helvetica-Bold', 16)
    snapshot_company = payslip.get('company', employee.company)
    pdf.drawString(50, 780, (
        'TRECE-UNO AUTO SUPPLY' if str(snapshot_company or '').lower().startswith('trece')
        else 'AUTO-EXPERT AUTO SUPPLY'
    ))
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawString(50, 760, 'PAYSLIP')
    pdf.setFont('Helvetica', 9)
    pdf.drawString(50, 730, f'Employee: {payslip.get("employee_name", employee.full_name())} (ID: {employee.id})')
    pdf.drawString(50, 714, f'Cutoff: {payroll_record.cutoff_start} to {payroll_record.cutoff_end}')
    pdf.drawString(50, 698, f'Position: {payslip.get("job_description", employee.job_description) or "N/A"}')
    rate_label = 'Current rate' if payslip.get('legacy') else 'Snapshot rate'
    pdf.drawString(300, 714, f'{rate_label}: PHP {float(payslip.get("daily_rate", employee.daily_rate) or 0):,.2f}')
    pdf.drawString(300, 698, f'Reference: {verification["document_id"]}')
    pdf.drawString(50, 682, f'Department: {snapshot_company or "N/A"}')
    pdf.drawString(300, 682, 'Prepared by: Admin')
    pdf.drawString(50, 666, f'Generated: {generated_at:%Y-%m-%d %I:%M %p} PHT')
    data = weekly_payslip_table_data(payslip)
    table = Table(data, colWidths=[120, 60, 80, 120, 60, 80])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (2, -1), 'Helvetica-Bold'),
        ('FONTNAME', (3, -5), (5, -4), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.75, colors.black),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),
        ('ALIGN', (3, 1), (3, -1), 'LEFT'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    _, table_height = table.wrapOn(pdf, 50, 600)
    table_y = 675 - table_height
    table.drawOn(pdf, 20, table_y)
    y = table_y - 5
    pdf.line(50, y - 20, 250, y - 20)
    pdf.line(350, y - 20, 550, y - 20)
    pdf.setFont('Helvetica', 10)
    pdf.drawString(50, y - 35, 'Authorized Person Signature')
    pdf.drawString(350, y - 35, 'Date')
    if qr_bytes:
        pdf.drawImage(ImageReader(io.BytesIO(qr_bytes)), 455, 36, width=90, height=90)
    pdf.setFont('Helvetica-Oblique', 8)
    pdf.drawString(440, 24, f'Official Document ID: {verification["document_id"]}')
    pdf.drawString(440, 14, 'Scan QR to verify.')
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f'Payslip_{employee.first_name}_{employee.last_name}_{payroll_record.cutoff_start}.pdf', mimetype='application/pdf')


# ------------------ PAYROLL DASHBOARD------------------
@app.route('/admin/payroll-history')
@login_required
def admin_payroll_history():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403

    year_value = request.args.get('year', '').strip()
    query = Payroll.query.join(Employee)
    selected_year = None
    if year_value:
        try:
            selected_year = int(year_value)
            if selected_year < 1900 or selected_year > 9999:
                raise ValueError
        except ValueError:
            flash('Select a valid payroll year.', 'danger')
            return redirect(url_for('admin_payroll_history'))
        query = query.filter(extract('year', Payroll.cutoff_start) == selected_year)

    records = query.order_by(
        Payroll.cutoff_start.desc(),
        Employee.company,
        Employee.last_name,
        Employee.first_name,
    ).all()
    years = [
        row[0]
        for row in db.session.query(extract('year', Payroll.cutoff_start).label('year'))
        .distinct()
        .order_by(extract('year', Payroll.cutoff_start).desc())
        .all()
        if row[0] is not None
    ]
    return render_template(
        'admin_payroll_history.html',
        records=records,
        years=years,
        selected_year=selected_year,
    )


@app.route('/payroll_dashboard', methods=['GET', 'POST'])
@login_required
def payroll_dashboard():
    if not can_manage_admin_payroll(current_user):
        return 'Access denied', 403
    try:
        cutoff_start, cutoff_end = payroll_cutoff_from_request(request.values.get('cutoff_start'))
    except ValueError:
        flash('Select a Saturday cutoff start date.', 'danger')
        return redirect(url_for('payroll_dashboard'))
    start_cutoff = datetime.combine(cutoff_start, time.min)
    end_cutoff = datetime.combine(cutoff_end, time.min)

    if request.method == 'POST':
        for emp in Employee.query.order_by(
            Employee.role.ilike('%admin%'), Employee.last_name, Employee.first_name
        ).all():
            payroll_record = None
            daily_rate = request.form.get(f'daily_rate_{emp.id}')
            allowance = request.form.get(f'allowance_{emp.id}')
            rice_allowance = request.form.get(f'rice_allowance_{emp.id}')
            other_de_minimis = {
                field: request.form.get(f'{field}_{emp.id}')
                for field in ('laundry_allowance', 'medical_allowance', 'uniform_allowance', 'christmas_gift_allowance', 'meal_allowance')
            }
            incentives = request.form.get(f'incentives_{emp.id}')
            loan_balance = request.form.get(f'loan_{emp.id}')
            loan_deduction = request.form.get(f'loan_deduction_{emp.id}')
            sss_loan_deduction = request.form.get(f'sss_loan_{emp.id}')
            pagibig_loan_deduction = request.form.get(f'pagibig_loan_{emp.id}')
            withholding_tax_override = request.form.get(f'withholding_tax_{emp.id}')
            liability_deduction_override = request.form.get(f'liability_deduction_{emp.id}')
            cash_advance = request.form.get(f'cash_advance_{emp.id}')
            other_deductions = request.form.get(f'other_deductions_{emp.id}')
            try:
                if daily_rate is not None:
                    emp.daily_rate = max(float(daily_rate), 0)
                if allowance is not None:
                    emp.allowance = max(float(allowance), 0)
                if rice_allowance is not None:
                    emp.rice_allowance_per_day = max(float(rice_allowance), 0)
                emp.rice_allowance_is_de_minimis = True
                for field, value in other_de_minimis.items():
                    if value is not None:
                        setattr(emp, field, max(float(value), 0))
                if incentives is not None:
                    emp.incentives = max(float(incentives), 0)
                if loan_balance is not None:
                    emp.loan_balance = max(float(loan_balance), 0)
                deduction_inputs_present = any(
                    value is not None and str(value).strip() != ""
                    for value in (
                        loan_deduction,
                        sss_loan_deduction,
                        pagibig_loan_deduction,
                        withholding_tax_override,
                        liability_deduction_override,
                        cash_advance,
                        other_deductions,
                    )
                )
                if deduction_inputs_present:
                    payroll_record = Payroll.query.filter_by(
                        employee_id=emp.id,
                        cutoff_start=start_cutoff.date(),
                        cutoff_end=(end_cutoff - timedelta(days=1)).date()
                    ).first()
                    if payroll_record and (
                        payroll_record.is_paid
                        or payroll_record.loan_deduction_applied
                    ):
                        db.session.rollback()
                        flash(
                            f"Reopen {emp.first_name} {emp.last_name}'s payroll "
                            "before changing deductions.",
                            'warning',
                        )
                        return redirect(url_for(
                            'payroll_dashboard',
                            cutoff_start=cutoff_start.isoformat(),
                        ))
                    if payroll_record is None:
                        payroll_record = Payroll(
                            employee_id=emp.id,
                            cutoff_start=start_cutoff.date(),
                            cutoff_end=(end_cutoff - timedelta(days=1)).date()
                        )
                        db.session.add(payroll_record)
                    if loan_deduction is not None and loan_deduction.strip() != "":
                        requested_deduction = max(float(loan_deduction), 0)
                        payroll_record.loan = (
                            0.0 if approved_loan_deduction_skip(emp.id, cutoff_start)
                            else loan_cutoff_deduction(emp.loan_balance, requested_deduction)
                        )
                        payroll_record.loan_deduction_override = payroll_record.loan
                    if sss_loan_deduction is not None and sss_loan_deduction.strip() != "":
                        payroll_record.sss_loan = max(float(sss_loan_deduction), 0)
                    if pagibig_loan_deduction is not None and pagibig_loan_deduction.strip() != "":
                        payroll_record.pagibig_loan = max(float(pagibig_loan_deduction), 0)
                    if withholding_tax_override is not None and withholding_tax_override.strip() != "":
                        payroll_record.withholding_tax_override = max(float(withholding_tax_override), 0)
                    if liability_deduction_override is not None and liability_deduction_override.strip() != "":
                        payroll_record.liability_deduction_override = max(float(liability_deduction_override), 0)
                    if cash_advance is not None and cash_advance.strip() != "":
                        payroll_record.cash_advance = max(float(cash_advance), 0)
                    if other_deductions is not None and other_deductions.strip() != "":
                        payroll_record.other_deductions = max(float(other_deductions), 0)
                    payroll_record.payslip_snapshot = None
                update_manual_owner_contribution_settings(
                    emp, request.form, cutoff_start
                )
            except (TypeError, ValueError):
                db.session.rollback()
                flash('Please enter valid non-negative payroll values.', 'danger')
                return redirect(url_for(
                    'payroll_dashboard',
                    cutoff_start=cutoff_start.isoformat(),
                ))
        db.session.commit()
        flash('Payroll rates and allowances updated successfully.', 'success')
        return redirect(url_for(
            'payroll_dashboard',
            cutoff_start=cutoff_start.isoformat(),
        ))

    payroll_data = []
    employees = Employee.query.order_by(
        Employee.role.ilike('%admin%'), Employee.last_name, Employee.first_name
    ).filter(
        Employee.payroll_attendance_exempt.is_(False),
        Employee.first_name.isnot(None),
        Employee.first_name != '',
        Employee.last_name.isnot(None),
        Employee.last_name != '',
    ).all()
    accounting_totals = {
        'staff_net_pay': 0.0,
        'admin_net_pay': 0.0,
        'total_net_pay': 0.0,
    }

    for emp in employees:
        paid_attendance = payroll_attendance_records(emp, cutoff_start, cutoff_end)
        worked_days_count = payroll_worked_days_count(emp, paid_attendance)

        daily_rate = float(emp.daily_rate or 0)
        basic_pay = sum(regular_day_pay(attendance, daily_rate) for attendance in paid_attendance)
        ot_records = [
            attendance for attendance in paid_attendance
            if payroll_overtime_hours(attendance) > 0
        ]
        ot_hours = sum(
            payroll_overtime_hours(attendance)
            for attendance in ot_records
        )
        approved_ot_hours = sum(
            payroll_overtime_hours(attendance)
            for attendance in ot_records
            if attendance_overtime_is_payable(attendance)
        )
        approved_ot_pay = sum(
            payroll_extra_pay(attendance, daily_rate)
            for attendance in ot_records
            if attendance_overtime_is_payable(attendance)
        )
        weeks_in_month = payroll_cutoffs_in_month(cutoff_start)
        rice_days, rice_monthly_used = rice_allowance_month_context(
            emp, cutoff_start, worked_days_count
        )
        rice_exempt, rice_taxable = de_minimis_allowance_breakdown(
            emp,
            rice_days,
            weeks_in_month,
            rice_monthly_used=rice_monthly_used,
        )
        gross_income = basic_pay + float(emp.allowance or 0) + float(emp.incentives or 0) + rice_taxable + approved_ot_pay

        cutoff_salary = sum(
            regular_day_pay(attendance, daily_rate) for attendance in paid_attendance
        )
        contribution_salary = cutoff_salary + rice_taxable
        contributions = cutoff_statutory_contributions(emp, cutoff_start, contribution_salary)
        deduction_values = contributions['employee']
        employer_deduction_values = contributions['employer']
        sss = deduction_values['sss']
        philhealth = deduction_values['philhealth']
        pagibig = deduction_values['pagibig']
        payroll_record = Payroll.query.filter_by(
            employee_id=emp.id,
            cutoff_start=start_cutoff.date(),
            cutoff_end=(end_cutoff - timedelta(days=1)).date()
        ).first()
        loan = automatic_loan_deduction(emp, payroll_record, cutoff_start)
        sss_loan = (
            float(payroll_record.sss_loan or 0)
            if payroll_record is not None
            else 0.0
        )
        pagibig_loan = (
            float(payroll_record.pagibig_loan or 0)
            if payroll_record is not None
            else 0.0
        )
        liability_deduction = (
            float(payroll_record.liability_deduction_override)
            if payroll_record is not None
            and payroll_record.liability_deduction_override is not None
            else liability_cutoff_deduction(emp.id)
        )

        deduction_values = payroll_statutory_deductions(payroll_record, deduction_values)
        sss = deduction_values['sss']
        philhealth = deduction_values['philhealth']
        pagibig = deduction_values['pagibig']

        monthly_taxable_income = contribution_taxable_month(
            contributions, gross_income, weeks_in_month
        )
        withholding_tax = weekly_withholding_tax(
            emp, monthly_taxable_income, weeks_in_month
        )
        if payroll_record is not None and payroll_record.withholding_tax_override is not None:
            withholding_tax = float(payroll_record.withholding_tax_override)
        cash_advance = float(payroll_record.cash_advance or 0) if payroll_record else 0.0
        other_deductions = float(payroll_record.other_deductions or 0) if payroll_record else 0.0
        deductions = (
            sss + philhealth + pagibig + sss_loan + pagibig_loan + loan
            + liability_deduction + cash_advance + withholding_tax + other_deductions
        )
        net_pay = gross_income + rice_exempt - deductions
        if payroll_record and payroll_record.is_paid:
            gross_income = float(payroll_record.gross_income or 0)
            deductions = float(payroll_record.total_deductions or 0)
            net_pay = float(payroll_record.net_pay or 0)
            sss = float(payroll_record.sss or 0)
            philhealth = float(payroll_record.philhealth or 0)
            pagibig = float(payroll_record.pagibig or 0)
            withholding_tax = float(payroll_record.withholding_tax or 0)
            liability_deduction = float(payroll_record.liability_deduction or 0)
        is_admin = 'admin' in str(emp.role or '').lower()
        review_reasons = []
        if payroll_record and payroll_record.is_paid and gross_income == 0 and deductions == 0 and net_pay == 0:
            review_reasons.append('Finalized zero totals - review / repair required')
        if daily_rate <= 0:
            review_reasons.append('Missing daily rate')
        if worked_days_count == 0 and not emp.payroll_attendance_exempt:
            review_reasons.append('No completed attendance')
        if any(
            not attendance_overtime_is_payable(attendance)
            for attendance in ot_records
        ):
            review_reasons.append('OT needs approval')
        review_status = 'Needs Review' if review_reasons else 'Ready'
        if not emp.payroll_attendance_exempt:
            accounting_totals['total_net_pay'] += net_pay
            accounting_totals['admin_net_pay' if is_admin else 'staff_net_pay'] += net_pay

        payroll_data.append({
            "emp": emp,
            "is_admin": is_admin,
            "is_payroll_attendance_exempt": bool(emp.payroll_attendance_exempt),
            "worked_days": worked_days_count,
            "ot_hours": ot_hours,
            "approved_ot_hours": approved_ot_hours,
            "approved_ot_pay": approved_ot_pay,
            "other_de_minimis_monthly_total": other_de_minimis_monthly_total(emp),
            "de_minimis_monthly_ceiling": de_minimis_monthly_ceiling(),
            "ot_statuses": sorted({
                attendance_overtime_status(attendance)
                for attendance in ot_records
            }),
            "sss": sss,
            "philhealth": philhealth,
            "pagibig": pagibig,
            "sss_loan": sss_loan,
            "pagibig_loan": pagibig_loan,
            "loan_deduction": loan,
            "liability_deduction": liability_deduction,
            "cash_advance": cash_advance,
            "other_deductions": other_deductions,
            "withholding_tax": withholding_tax,
            "gross_income": gross_income,
            "deductions": deductions,
            "net_pay": net_pay,
            "is_paid": bool(payroll_record and payroll_record.is_paid),
            "review_status": review_status,
            "review_reasons": review_reasons,
        })

    return render_template("payroll_dashboard.html",
                           payroll_data=payroll_data,
                           accounting_totals=accounting_totals,
                           cutoff_options=payroll_dashboard_cutoff_options(cutoff_start),
                           start_cutoff=start_cutoff.date(),
                           end_cutoff=(end_cutoff - timedelta(days=1)).date())

# ------------------ HOLIDAY + OVERTIME (Unified with Approvals + Beyond 6PM) ------------------
@app.route('/holiday_overtime', methods=['GET'])
@login_required
def holiday_ot_dashboard():
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))
    try:
        cutoff_start, cutoff_end = payroll_cutoff_from_request(request.values.get("cutoff_start"))
    except ValueError:
        flash('Select a Saturday cutoff start date.', 'danger')
        return redirect(url_for('holiday_ot_dashboard'))

    # --- Query lahat ng attendance na may OT OR lumabas beyond 6 PM ---
    query = Attendance.query.filter(
        Attendance.clock_out != None,
        Attendance.date >= cutoff_start,
        Attendance.date < cutoff_end,
    )
    records = [
        attendance for attendance in query.order_by(Attendance.date.desc()).all()
        if (
            attendance.is_holiday_ot or attendance.is_weekday_ot or attendance.is_restday_ot
            or automatic_trece_sunday_hours(attendance) > 0
            or (
                attendance.date.weekday() == 6
                and attendance.employee
                and str(attendance.employee.company or '').lower().startswith('trece')
                and attendance.clock_out.time() > time(12, 0)
            )
            or (
                attendance.clock_out.time() > time(17, 0)
                and (
                    Holiday.query.filter_by(date=attendance.date).first()
                    or attendance.date.weekday() == 6
                )
            )
            or (
                attendance.date.weekday() != 6
                and not Holiday.query.filter_by(date=attendance.date).first()
                and attendance.clock_out.time() >= time(18, 0)
            )
        )
    ]

    applications = OTApplication.query.order_by(OTApplication.ot_date.desc()).all()
    applications = OTApplication.query.filter(
        OTApplication.ot_date >= cutoff_start,
        OTApplication.ot_date < cutoff_end,
    ).order_by(OTApplication.ot_date.desc()).all()

    # --- Optional filter by status ---
    filter_status = request.args.get("status")
    if filter_status:
        records = [
            attendance for attendance in records
            if attendance_overtime_status(attendance) == filter_status
        ]

    # --- Holidays dictionary ---
    holidays = {h.date: h.description for h in Holiday.query.all()}

    employee_search = request.args.get("employee_search", "").strip()
    attendance_report = Attendance.query.filter(
        Attendance.date >= cutoff_start,
        Attendance.date < cutoff_end,
    ).order_by(Attendance.date.desc()).all()
    company = gov_company_from_request()
    if company:
        records = [row for row in records if gov_company_label(row.employee) == company]
        applications = [row for row in applications if gov_company_label(row.employee) == company]
        attendance_report = [row for row in attendance_report if gov_company_label(row.employee) == company]
    if employee_search:
        search_value = employee_search.casefold()
        attendance_report = [
            attendance for attendance in attendance_report
            if attendance.employee
            and search_value in (
                f"{attendance.employee.first_name} "
                f"{attendance.employee.last_name}"
            ).casefold()
        ]
    attendance_report_rows = []
    for attendance in attendance_report:
        clock_in = attendance.clock_in
        clock_out = attendance.clock_out
        complete_punches = bool(clock_in and clock_out)
        regular_hours = (
            regular_attendance_hours(attendance) if complete_punches else 0.0
        )
        scheduled_hours = attendance_scheduled_hours(
            attendance.employee, attendance.date
        )
        late_minutes = 0
        if clock_in and attendance_status(clock_in) == "Late":
            late_minutes = max(
                int(
                    (
                        clock_in
                        - datetime.combine(attendance.date, time(8, 10))
                    ).total_seconds()
                    // 60
                ),
                0,
            )
        attendance_report_rows.append({
            "attendance": attendance,
            "regular_hours": regular_hours,
            "late_minutes": late_minutes,
            "undertime_hours": (
                max(scheduled_hours - regular_hours, 0.0) if complete_punches else 0.0
            ),
            "half_day": bool(
                complete_punches
                and scheduled_hours
                and regular_hours <= scheduled_hours / 2
            ),
            "overtime_hours": payroll_overtime_hours(attendance),
            "ot_status": attendance_overtime_status(attendance),
            "missing_clock_in": clock_in is None,
            "missing_clock_out": clock_out is None,
        })

    # --- Export CSV ---
    export_type = request.args.get("export")
    if export_type == "attendance_csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Employee", "Date", "Clock In", "Clock Out", "Late Minutes",
            "Regular Hours", "Undertime Hours", "Half-day", "OT Hours",
            "OT Status", "Missing Clock In", "Missing Clock Out",
        ])
        for row in attendance_report_rows:
            attendance = row["attendance"]
            writer.writerow([
                attendance.employee.full_name(),
                attendance.date,
                attendance.clock_in or "",
                attendance.clock_out or "",
                row["late_minutes"],
                f'{row["regular_hours"]:.2f}',
                f'{row["undertime_hours"]:.2f}',
                "Yes" if row["half_day"] else "No",
                f'{row["overtime_hours"]:.2f}',
                row["ot_status"],
                "Yes" if row["missing_clock_in"] else "No",
                "Yes" if row["missing_clock_out"] else "No",
            ])
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition":
                    "attachment;filename=attendance_cutoff_report.csv"
            },
        )

    if export_type == "csv":
        def generate():
            data = [['Attendance ID','Employee','Date','Holiday Name','Status','Clock Out','OT Hours','OT Type','OT Status']]
            for att in records:
                ot_type = (
                    "Rest Day"
                    if automatic_trece_sunday_hours(att) > 0
                    else "Weekday"
                    if att.is_weekday_ot
                    else "Rest Day"
                    if att.is_restday_ot
                    else "Holiday"
                    if att.is_holiday_ot
                    else "Beyond 5PM"
                )
                ot_hours = payroll_overtime_hours(att)
                row = [
                    att.id,
                    f"{att.employee.first_name} {att.employee.last_name}",
                    att.date,
                    holidays.get(att.date, ""),
                    att.status,
                    att.clock_out,
                    ot_hours,
                    ot_type,
                    attendance_overtime_status(att)
                ]
                data.append(row)
            return '\n'.join([','.join(map(str, row)) for row in data])

        return Response(generate(), mimetype="text/csv",
                        headers={"Content-Disposition":"attachment;filename=holiday_overtime.csv"})

    return render_template("holiday_ot_dashboard.html",
                           records=records,
                           overtime_hours_by_attendance_id={
                               attendance.id: payroll_overtime_hours(attendance)
                               for attendance in records
                           },
                           ot_status_by_attendance_id={
                               attendance.id: attendance_overtime_status(attendance)
                               for attendance in records
                           },
                           applications=applications,
                           attendance_report_rows=attendance_report_rows,
                           employee_search=employee_search,
                           holidays=holidays,
                           filter_status=filter_status,
                           cutoff_start=cutoff_start,
                           cutoff_end=cutoff_end - timedelta(days=1),
                           time=time)


# ------------------ LEAVE ------------------
@app.route('/leave', methods=['GET','POST'])
@login_required
def leave():
    export_type = request.args.get("export")

    # --- Apply leave (POST) ---
    if request.method == 'POST':
        leave_type = request.form.get('leave_type')
        try:
            days = int(request.form.get('days', ''))
        except (TypeError, ValueError):
            flash("❌ Please enter a valid number of leave days.", "danger")
            return redirect(url_for('leave'))

        if days < 1:
            flash("❌ Leave days must be at least 1.", "danger")
            return redirect(url_for('leave'))

        start_date = datetime.strptime(request.form.get('start_date'), "%Y-%m-%d").date()
        end_date = datetime.strptime(request.form.get('end_date'), "%Y-%m-%d").date()

        emp = Employee.query.get(current_user.id)
        if leave_type == "SIL" and not emp.sil_eligible:
            flash("❌ You are not yet eligible for Service Incentive Leave.", "danger")
            return redirect(url_for('leave'))

        if leave_type == "SIL" and emp.sil_credits < days:
            flash(f"❌ Not enough SIL credits. You only have {emp.sil_credits} left.", "danger")
            return redirect(url_for('leave'))

        new_leave = LeaveRequest(
            employee_id=current_user.id,
            leave_type=leave_type,
            days=days,
            start_date=start_date,
            end_date=end_date,
            reason=request.form.get('reason'),
            status='Pending',
            is_paid=(leave_type == "SIL")
        )
        db.session.add(new_leave)
        db.session.commit()
        notify_admins(
            'New leave application',
            (
                f'{emp.full_name()} submitted a {leave_type} leave request for '
                f'{start_date} to {end_date} ({days} day(s)).'
            )
        )
        flash("✅ Leave application submitted!", "success")
        return redirect(url_for('leave'))

    # --- Approval action (GET param) ---
    leave_id = request.args.get("leave_id")
    action = request.args.get("action")
    if leave_id and action and current_user.role.lower() == 'admin':
        leave = LeaveRequest.query.get_or_404(int(leave_id))
        emp = Employee.query.get_or_404(leave.employee_id)

        if action == "approve":
            if leave.leave_type == "SIL" and not emp.sil_eligible:
                flash("❌ Employee is not yet eligible for Service Incentive Leave.", "danger")
                return redirect(url_for('leave'))

            if leave.leave_type == "SIL" and leave.status != "Approved":
                if emp.sil_credits >= leave.days:
                    emp.sil_credits -= leave.days
                    leave.status = "Approved"
                    leave.is_paid = True
                else:
                    flash("❌ Not enough SIL credits.", "danger")
            else:
                leave.status = "Approved"
                leave.is_paid = (leave.leave_type == "SIL")
            leave.decision_date = datetime.utcnow()
            db.session.commit()
            send_leave_email(emp, leave, "approved")
            flash("✅ Leave approved.", "success")

        elif action == "reject":
            leave.status = "Rejected"
            leave.decision_date = datetime.utcnow()
            db.session.commit()
            send_leave_email(emp, leave, "rejected")
            flash("❌ Leave request rejected.", "danger")

        return redirect(url_for('leave'))

    # --- Automatic SIL reset every January 1 ---
    today = date.today()
    if today.month == 1 and today.day == 1:
        for emp in Employee.query.all():
            emp.sil_credits = 5
        db.session.commit()
        flash("🔄 SIL credits reset to 5 days for all employees.", "info")

    # --- Query leaves + histories ---
    if current_user.role.lower() == 'admin':
        leaves = LeaveRequest.query.order_by(LeaveRequest.date_filed.desc()).all()
        histories = LeaveHistory.query.order_by(LeaveHistory.cutoff_start.desc()).all()
    else:
        leaves = LeaveRequest.query.filter_by(employee_id=current_user.id)\
                                   .order_by(LeaveRequest.date_filed.desc()).all()
        histories = LeaveHistory.query.filter_by(employee_id=current_user.id)\
                                      .order_by(LeaveHistory.cutoff_start.desc()).all()

    # --- Summary counts ---
    approved = LeaveRequest.query.filter_by(status="Approved").count()
    pending = LeaveRequest.query.filter_by(status="Pending").count()
    rejected = LeaveRequest.query.filter_by(status="Rejected").count()
    lwop = LeaveRequest.query.filter_by(leave_type="LWOP").count()

    # --- Trend (last 6 cutoff periods) ---
    leave_trend = LeaveHistory.query.order_by(LeaveHistory.cutoff_start.desc()).limit(6).all()
    leave_labels = [f"{r.cutoff_start.strftime('%b %d')} - {r.cutoff_end.strftime('%b %d')}" for r in leave_trend][::-1]
    leave_values = [r.approved_count for r in leave_trend][::-1]

    # --- PDF-only print view ---
    if export_type == "print":
        approved_leaves = [l for l in leaves if l.status == "Approved"] or leaves
        return render_template(
            "leave.html",
            leaves=approved_leaves,
            histories=histories,
            print_mode=True,
            current_user=current_user,
            generated_at=datetime.now()
        )
    if export_type == "pdf":
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        pdf.setFont('Helvetica-Bold', 14)
        pdf.drawString(50, 780, payroll_company_name(current_user))
        pdf.drawString(50, 760, 'LEAVE RECORDS')
        pdf.setFont('Helvetica', 10)
        pdf.drawString(50, 735, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
        pdf.drawString(50, 720, f'Employee: {"All Employees" if current_user.role.lower() == "admin" else current_user.full_name()}')
        y = 690
        pdf.setFont('Helvetica-Bold', 9)
        for x, label in [(40, 'Employee'), (160, 'Type'), (230, 'Days'), (275, 'Start'), (340, 'End'), (405, 'Status'), (465, 'Paid')]:
            pdf.drawString(x, y, label)
        y -= 16
        pdf.setFont('Helvetica', 9)
        for leave_record in leaves:
            if y < 70:
                pdf.showPage()
                y = 760
                pdf.setFont('Helvetica', 9)
            employee_name = leave_record.employee.full_name() if leave_record.employee else 'N/A'
            pdf.drawString(40, y, employee_name[:18])
            pdf.drawString(160, y, str(leave_record.leave_type or '')[:12])
            pdf.drawRightString(250, y, str(leave_record.days or 0))
            pdf.drawString(275, y, str(leave_record.start_date or ''))
            pdf.drawString(340, y, str(leave_record.end_date or ''))
            pdf.drawString(405, y, str(leave_record.status or ''))
            pdf.drawString(465, y, 'Yes' if leave_record.is_paid else 'No')
            y -= 14
        pdf.line(60, 58, 250, 58)
        pdf.line(330, 58, 520, 58)
        pdf.drawString(60, 43, 'Employee / Authorized Signature')
        pdf.drawString(330, 43, 'Date')
        pdf.save()
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f'Leave_Records_{datetime.now():%Y%m%d}.pdf', mimetype='application/pdf')

    # --- Default: normal leave page ---
    return render_template("leave.html",
                           leaves=leaves,
                           histories=histories,
                           approved=approved,
                           pending=pending,
                           rejected=rejected,
                           lwop=lwop,
                           leave_labels=leave_labels,
                           leave_values=leave_values,
                           print_mode=False)


# ------------------ LEAVE APPROVAL ACTIONS (Admin helper routes) ------------------
@app.route('/approve_leave/<int:id>', methods=['GET','POST'])
@login_required
def approve_leave(id):
    if current_user.role.lower() != 'admin':
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('leave'))
    leave = LeaveRequest.query.get_or_404(id)
    emp = Employee.query.get_or_404(leave.employee_id)

    if leave.leave_type == "SIL" and not emp.sil_eligible:
        flash("❌ Employee is not yet eligible for Service Incentive Leave.", "danger")
        return redirect(url_for('leave'))

    if leave.leave_type == "SIL" and emp.sil_credits < leave.days:
        flash("❌ Not enough SIL credits.", "danger")
        return redirect(url_for('leave'))

    if leave.leave_type == "SIL":
        emp.sil_credits -= leave.days
    leave.status = "Approved"
    leave.is_paid = (leave.leave_type == "SIL")
    leave.decision_date = datetime.utcnow()
    db.session.commit()
    try:
        send_leave_email(emp, leave, "approved")
    except Exception:
        logger.exception("Failed to send leave approval email")
    flash("✅ Leave approved.", "success")
    return redirect(url_for('dashboard_admin'))


@app.route('/reject_leave/<int:id>', methods=['POST'])
@login_required
def reject_leave(id):
    if current_user.role.lower() != 'admin':
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('leave'))
    reason = request.form.get('reason')
    leave = LeaveRequest.query.get_or_404(id)
    emp = Employee.query.get_or_404(leave.employee_id)
    leave.status = "Rejected"
    leave.remarks = reason
    leave.decision_date = datetime.utcnow()
    db.session.commit()
    try:
        send_leave_email(emp, leave, "rejected")
    except Exception:
        logger.exception("Failed to send leave rejection email")
    flash("❌ Leave request rejected.", "danger")
    return redirect(url_for('dashboard_admin'))

# ------------------ LOAN ------------------
def apply_approved_loan_balance(loan_record):
    """Add an approved loan to the employee balance exactly once."""
    if loan_record.balance_applied:
        return False
    employee = db.session.get(Employee, loan_record.employee_id, with_for_update=True)
    if float(employee.loan_balance or 0) <= 0:
        employee.loan_deduction_per_cutoff = float(getattr(loan_record, 'deduction_per_cutoff', None) or 500)
    employee.loan_balance = float(employee.loan_balance or 0) + float(loan_record.amount or 0)
    loan_record.balance_applied = True
    return True


def loan_deduction_records(employee_id):
    return Payroll.query.filter(
        Payroll.employee_id == employee_id,
        Payroll.loan > 0,
        Payroll.loan_deduction_applied.is_(True)
    ).order_by(Payroll.cutoff_start.desc()).all()


def loan_account_summary(employee):
    approved_loans = Loan.query.filter_by(
        employee_id=employee.id,
        status="Approved",
        balance_applied=True
    ).order_by(Loan.date_filed.desc()).all()
    deductions = loan_deduction_records(employee.id)
    outstanding = float(employee.loan_balance or 0)
    return {
        "employee": employee,
        "outstanding": outstanding,
        "approved_loans": approved_loans,
        "deductions": deductions,
        "remaining_after": loan_remaining_after_deductions(outstanding, approved_loans, deductions),
        "total_credited": sum(float(item.amount or 0) for item in approved_loans),
        "total_deducted": sum(float(item.loan or 0) for item in deductions),
    }


def loan_credit_date(loan_record):
    if loan_record.loan_type == 'Prior-System Balance' or loan_record.decision_date is None:
        credited_at = loan_record.date_filed
    else:
        credited_at = loan_record.decision_date
    return credited_at.date() if credited_at else loan_record.date_needed


def loan_remaining_after_deductions(outstanding, approved_loans, deductions):
    """Walk back from the current balance to show what was left after each payroll deduction."""
    credits = [
        (loan_credit_date(loan_record), float(loan_record.amount or 0))
        for loan_record in approved_loans
        if loan_credit_date(loan_record) is not None
    ]
    remaining_after = {}
    for payroll_record in deductions:
        saved = json.loads(getattr(payroll_record, 'payslip_snapshot', None) or '{}')
        if 'loan_remaining_balance' in saved:
            remaining_after[payroll_record.id] = float(saved['loan_remaining_balance'])
            continue
        order_key = (payroll_record.cutoff_start, payroll_record.id)
        later_deductions = sum(
            float(other.loan or 0)
            for other in deductions
            if (other.cutoff_start, other.id) > order_key
        )
        later_credits = sum(
            amount for credited_on, amount in credits
            if credited_on > payroll_record.cutoff_end
        )
        remaining_after[payroll_record.id] = max(
            outstanding + later_deductions - later_credits, 0.0,
        )
    return remaining_after


def review_loan_request(loan_id, decision):
    loan_record = Loan.query.filter_by(id=loan_id).with_for_update().first_or_404()
    if loan_record.status != 'Pending' or loan_record.balance_applied:
        flash('This loan has already been reviewed. Its balance was not changed.', 'warning')
        return redirect(url_for('loan'))
    reason = request.form.get('reason', '').strip()
    if decision == 'Rejected' and not reason:
        flash('Enter a reason for declining this loan.', 'danger')
        return redirect(url_for('loan'))
    if not isfinite(float(loan_record.amount)) or loan_record.amount <= 0:
        flash('This loan has an invalid amount. Correct the record before review.', 'danger')
        return redirect(url_for('loan'))
    employee = db.session.get(Employee, loan_record.employee_id, with_for_update=True)
    if decision == 'Approved' and float(employee.loan_balance or 0) > 0 and (
        float(loan_record.deduction_per_cutoff) != float(employee.loan_deduction_per_cutoff or 500)
    ):
        flash('This application has a different deduction from the existing loan. Decline it and request a new application with the current repayment amount.', 'danger')
        return redirect(url_for('loan'))
    loan_record.status = decision
    loan_record.decision_date = datetime.utcnow()
    loan_record.approver = current_user.full_name()
    if decision == 'Approved':
        apply_approved_loan_balance(loan_record)
    else:
        loan_record.remarks = reason
    db.session.commit()
    send_loan_email(
        Employee.query.get_or_404(loan_record.employee_id),
        loan_record, decision.lower(),
    )
    if decision == 'Approved':
        flash(
            f'Loan approved: PHP {loan_record.amount:,.2f} added to the employee remaining balance.',
            'success',
        )
    else:
        flash('Loan request declined. No amount was added to the employee balance.', 'success')
    return redirect(url_for('loan'))


@app.route('/approve_loan/<int:id>', methods=['POST'])
@login_required
def approve_loan(id):
    if current_user.role.lower() != 'admin':
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('loan'))
    return review_loan_request(id, 'Approved')


@app.route('/reject_loan/<int:id>', methods=['POST'])
@login_required
def reject_loan(id):
    if current_user.role.lower() != 'admin':
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('loan'))
    return review_loan_request(id, 'Rejected')


@app.route('/loan/balance/<int:employee_id>', methods=['POST'])
@login_required
def update_loan_balance(employee_id):
    if current_user.role.lower() != 'admin':
        return 'Access denied', 403
    employee = Employee.query.filter_by(id=employee_id).with_for_update().first_or_404()
    try:
        balance = float(request.form.get('balance', ''))
        previous_balance = float(request.form.get('previous_balance', ''))
        if not isfinite(balance) or not isfinite(previous_balance) or balance < 0:
            raise ValueError
    except (TypeError, ValueError):
        flash('Enter a valid non-negative remaining loan balance.', 'danger')
        return redirect(url_for('loan'))
    old_balance = float(employee.loan_balance or 0)
    if previous_balance != old_balance:
        flash('The loan balance changed since this page opened. Reload and review before saving.', 'warning')
        return redirect(url_for('loan'))
    employee.loan_balance = balance
    db.session.add(AuditLog(
        employee_id=current_user.id,
        employee_name=current_user.full_name(),
        company=current_user.company,
        action=f'Loan balance employee #{employee.id}: PHP {old_balance:.2f} -> {balance:.2f}',
        path=request.path,
        ip_address=request.remote_addr,
        status_code=302,
    ))
    db.session.commit()
    flash(f'Remaining loan for {employee.full_name()} saved: PHP {balance:,.2f}.', 'success')
    return redirect(url_for('loan'))


@app.route('/loan/<int:loan_id>/delete', methods=['POST'])
@login_required
def delete_loan(loan_id):
    if current_user.role.lower() != 'admin':
        return 'Access denied', 403
    loan_record = Loan.query.filter_by(id=loan_id).with_for_update().first_or_404()
    employee = Employee.query.filter_by(id=loan_record.employee_id).with_for_update().first()
    amount = float(loan_record.amount or 0)
    reference = f'LOAN-{loan_record.id:06d}'
    balance_note = ''
    if loan_record.balance_applied and employee is not None:
        old_balance = float(employee.loan_balance or 0)
        employee.loan_balance = max(old_balance - amount, 0.0)
        balance_note = f'; balance PHP {old_balance:.2f} -> {employee.loan_balance:.2f}'
    db.session.add(AuditLog(
        employee_id=current_user.id,
        employee_name=current_user.full_name(),
        company=current_user.company,
        action=(
            f'Deleted {reference} ({loan_record.status}, PHP {amount:.2f}) '
            f'for employee #{loan_record.employee_id}{balance_note}'
        ),
        path=request.path,
        ip_address=request.remote_addr,
        status_code=302,
    ))
    db.session.delete(loan_record)
    db.session.commit()
    if balance_note:
        flash(
            f'{reference} deleted. PHP {amount:,.2f} was removed from '
            f'{employee.full_name()}\'s remaining loan.',
            'success',
        )
    else:
        flash(f'{reference} deleted.', 'success')
    return redirect(url_for('loan'))


@app.route('/loan/deduction-skip/<int:request_id>/<action>', methods=['POST'])
@login_required
def review_loan_deduction_skip_request(request_id, action):
    if not can_review_payroll_loans(current_user) or action not in {'approve', 'reject'}:
        return 'Access denied', 403
    skip_request = LoanDeductionSkipRequest.query.get_or_404(request_id)
    lock_attendance_employee(skip_request.employee_id)
    db.session.refresh(skip_request)
    if skip_request.status != 'Pending':
        flash('This skip request was already reviewed.', 'warning')
        return redirect(url_for(
            'payroll_loan_review', cutoff_start=skip_request.cutoff_start.isoformat(),
        ))
    decision_note = request.form.get('decision_note', '').strip()
    if action == 'reject' and not decision_note:
        flash('Enter a reason for declining this skip request.', 'danger')
        return redirect(url_for(
            'payroll_loan_review', cutoff_start=skip_request.cutoff_start.isoformat(),
        ))
    payroll_record = Payroll.query.filter_by(
        employee_id=skip_request.employee_id,
        cutoff_start=skip_request.cutoff_start,
        cutoff_end=skip_request.cutoff_end,
    ).first()
    if payroll_record and (payroll_record.is_paid or payroll_record.loan_deduction_applied):
        flash('This payroll cutoff is finalized; its loan deduction cannot be changed.', 'warning')
        return redirect(url_for(
            'payroll_loan_review', cutoff_start=skip_request.cutoff_start.isoformat(),
        ))

    skip_request.status = 'Approved' if action == 'approve' else 'Rejected'
    skip_request.decision_note = decision_note or None
    skip_request.reviewed_by = current_user.id
    skip_request.reviewed_at = datetime.utcnow()
    if action == 'approve' and payroll_record:
        payroll_record.loan = 0
    db.session.commit()
    employee = db.session.get(Employee, skip_request.employee_id)
    decision = skip_request.status.lower()
    if employee and employee.email:
        send_notification_email(
            [employee.email],
            f'Loan deduction skip request {decision}',
            (
                f'Your request to skip the loan deduction for the payroll cutoff '
                f'{skip_request.cutoff_start} to {skip_request.cutoff_end} was {decision}.'
                + (f' Admin note: {decision_note}' if decision_note else '')
            ),
        )
    flash(
        'Loan deduction skipped for this cutoff only.' if action == 'approve'
        else 'Loan deduction skip request declined.',
        'success' if action == 'approve' else 'info',
    )
    return redirect(url_for(
        'payroll_loan_review', cutoff_start=skip_request.cutoff_start.isoformat(),
    ))


@app.route('/loan', methods=['GET','POST'])
@login_required
def loan():
    action = request.args.get("action")
    loan_id = request.args.get("loan_id")

    # --- Loan Approval (Admin) ---
    if action in ["approve","reject"] and loan_id:
        if current_user.role.lower() != "admin":
            flash("❌ Access denied!", "danger")
            return redirect(url_for('loan'))
        if request.method != 'POST':
            flash('Use the Approve or Decline button to review a pending loan.', 'warning')
            return redirect(url_for('loan'))
        parsed_loan_id = request.args.get('loan_id', type=int)
        if parsed_loan_id is None:
            flash('Select a valid loan request.', 'danger')
            return redirect(url_for('loan'))
        return review_loan_request(
            parsed_loan_id, 'Approved' if action == 'approve' else 'Rejected',
        )

    # --- PDF-only print view ---
    if action == "print":
        return redirect(url_for('download_loan_statement', employee_id=current_user.id))

    # --- Loan Application ---
    emp = Employee.query.get(current_user.id)
    loan_limit_info = loan_limit_breakdown(emp)
    remaining_limit = loan_limit_info['remaining_limit']
    current_cutoff_start, current_cutoff_end = current_payroll_cutoff()

    if request.method == 'POST':
        if request.form.get('action') == 'request_deduction_skip':
            if 'admin' in str(current_user.role or '').lower():
                return 'Access denied', 403
            reason = request.form.get('skip_reason', '').strip()
            if not reason:
                flash('Tell us why you need to skip this cutoff\'s loan deduction.', 'danger')
                return redirect(url_for('loan'))
            if float(emp.loan_balance or 0) <= 0:
                flash('You do not have an outstanding loan deduction to skip.', 'warning')
                return redirect(url_for('loan'))
            active_request = LoanDeductionSkipRequest.query.filter(
                LoanDeductionSkipRequest.employee_id == current_user.id,
                LoanDeductionSkipRequest.cutoff_start == current_cutoff_start,
                LoanDeductionSkipRequest.status.in_(['Pending', 'Approved']),
            ).first()
            if active_request:
                flash('A skip request already exists for this cutoff.', 'warning')
                return redirect(url_for('loan'))
            payroll_record = Payroll.query.filter_by(
                employee_id=current_user.id,
                cutoff_start=current_cutoff_start,
                cutoff_end=current_cutoff_end,
            ).first()
            if payroll_record and (payroll_record.is_paid or payroll_record.loan_deduction_applied):
                flash('This cutoff is already finalized; a skip request can no longer be submitted.', 'warning')
                return redirect(url_for('loan'))
            skip_request = LoanDeductionSkipRequest(
                employee_id=current_user.id,
                cutoff_start=current_cutoff_start,
                cutoff_end=current_cutoff_end,
                reason=reason,
            )
            db.session.add(skip_request)
            db.session.commit()
            notify_admins(
                'Loan deduction skip request',
                (
                    f'{emp.full_name()} requested to skip the loan deduction for '
                    f'{current_cutoff_start} to {current_cutoff_end}. Reason: {reason}'
                ),
            )
            flash('Your request to skip this cutoff\'s loan deduction was sent for review.', 'success')
            return redirect(url_for('loan'))

        try:
            amount = float(request.form.get('amount', ''))
            deduction_per_cutoff = float(request.form.get('deduction_per_cutoff', '500'))
            if not isfinite(amount) or amount <= 0:
                raise ValueError
            if not isfinite(deduction_per_cutoff) or deduction_per_cutoff < 500:
                raise ValueError
        except (TypeError, ValueError):
            flash('Enter a valid loan amount and a deduction of at least PHP 500 per cutoff.', 'danger')
            return redirect(url_for('loan'))
        if float(emp.loan_balance or 0) > 0 and deduction_per_cutoff != float(emp.loan_deduction_per_cutoff or 500):
            flash('An additional loan keeps your existing deduction per cutoff; it does not add a second deduction.', 'danger')
            return redirect(url_for('loan'))
        if amount > remaining_limit:
            flash(f"❌ Loan exceeds your current limit ₱{remaining_limit:.2f}", "danger")
        else:
            date_needed_str = request.form.get('date_needed')
            try:
                date_needed = datetime.strptime(date_needed_str, "%Y-%m-%d").date() if date_needed_str else None
            except Exception:
                flash("❌ Invalid date format for Date Needed.", "danger")
                return redirect(url_for('loan'))

            if date_needed is None:
                flash("❌ Date Needed is required.", "danger")
                return redirect(url_for('loan'))

            new_loan = Loan(
                employee_id=current_user.id,
                amount=amount,
                deduction_per_cutoff=deduction_per_cutoff,
                reason=request.form.get('reason'),
                date_needed=date_needed,
                status='Pending'
            )
            db.session.add(new_loan)
            db.session.commit()
            notify_admins(
                'New loan application',
                (
                    f'{emp.full_name()} submitted a loan request for PHP {amount:,.2f}, '
                    f'needed on {date_needed}. Reason: {new_loan.reason}'
                )
            )
            flash("✅ Loan application submitted successfully!", "success")
        return redirect(url_for('loan'))

    loans = Loan.query.order_by(Loan.date_filed.desc()).all() if current_user.role.lower() == 'admin' \
            else Loan.query.filter_by(employee_id=current_user.id).order_by(Loan.date_filed.desc()).all()

    status_query = Loan.query if current_user.role.lower() == 'admin' else Loan.query.filter_by(employee_id=current_user.id)
    approved = status_query.filter_by(status="Approved").count()
    pending = status_query.filter_by(status="Pending").count()
    rejected = status_query.filter_by(status="Rejected").count()

    if current_user.role.lower() == 'admin':
        account_employees = Employee.query.filter(db.or_(
            Employee.role.ilike('%staff%'),
            Employee.id.in_(db.session.query(Loan.employee_id)),
        )).order_by(
            Employee.company, Employee.last_name, Employee.first_name
        ).all()
        loan_accounts = [loan_account_summary(employee) for employee in account_employees]
        deduction_history = [
            {
                "employee": account["employee"],
                "payroll": payroll_record,
                "remaining": account["remaining_after"][payroll_record.id],
            }
            for account in loan_accounts
            for payroll_record in account["deductions"]
        ]
        deduction_history.sort(key=lambda item: item["payroll"].cutoff_start, reverse=True)
        account = None
    else:
        account = loan_account_summary(current_user)
        loan_accounts = []
        deduction_history = [
            {
                "employee": current_user,
                "payroll": payroll_record,
                "remaining": account["remaining_after"][payroll_record.id],
            }
            for payroll_record in account["deductions"]
        ]

    skip_request_query = LoanDeductionSkipRequest.query.order_by(
        LoanDeductionSkipRequest.created_at.desc(),
    )
    if current_user.role.lower() != 'admin':
        skip_request_query = skip_request_query.filter_by(employee_id=current_user.id)
    deduction_skip_requests = skip_request_query.all()
    current_cutoff_skip_request = next(
        (
            item for item in deduction_skip_requests
            if item.cutoff_start == current_cutoff_start
        ),
        None,
    )

    loan_trend_query = LoanHistory.query
    if current_user.role.lower() != 'admin':
        loan_trend_query = loan_trend_query.filter_by(employee_id=current_user.id)
    loan_trend = loan_trend_query.order_by(LoanHistory.cutoff_start.desc()).limit(6).all()
    loan_labels = [f"{r.cutoff_start.strftime('%b %d')} - {r.cutoff_end.strftime('%b %d')}" for r in loan_trend][::-1]
    loan_values = [r.total_amount - r.deductions_applied for r in loan_trend][::-1]

    return render_template("loan.html",
                           loans=loans,
                           approved=approved,
                           pending=pending,
                           rejected=rejected,
                           remaining_limit=remaining_limit,
                           loan_limit_info=loan_limit_info,
                           loan_labels=loan_labels,
                           loan_values=loan_values,
                           account=account,
                           loan_accounts=loan_accounts,
                           deduction_history=deduction_history,
                           deduction_skip_requests=deduction_skip_requests,
                           current_cutoff_skip_request=current_cutoff_skip_request,
                           current_cutoff_start=current_cutoff_start,
                           current_cutoff_end=current_cutoff_end,
                           now=datetime.now(),
                           print_mode=False)


def loan_agreement_body(loan, deduction_per_cutoff):
    employee_name = loan.employee.full_name()
    return "\n".join([
        "EMPLOYEE LOAN AGREEMENT",
        f"Employee: {employee_name}",
        f"Loan Amount: PHP {float(loan.amount):,.2f}",
        f"Reason filed by employee: {loan.reason}",
        f"Deduction per cutoff: PHP {deduction_per_cutoff:,.2f}",
        "",
        f"I fully agree and authorize the company to deduct {deduction_per_cutoff:,.2f} pesos from my salary every cut off until the loan is completely settled.",
        "Only one regular company loan deduction applies per employee each cutoff, including additional loans. The final deduction cannot exceed the remaining balance. A cutoff may be skipped only with an approved skip request.",
        "In the event of resignation or separation from the company, I agree that any remaining balance shall be deducted from my final pay or settled in full before clearance.",
        "This agreement is made voluntarily and with full understanding of its terms.",
        "",
        "Signed:",
        "Borrower/Employee: ___________________________",
        f"Name: {employee_name}",
        "Date: _______________________________________",
        "Employer/Authorized Representative: ___________________________",
        "Name: _______________________________________",
        "Date: _______________________________________",
    ])


@app.route('/loan/<int:loan_id>/agreement', methods=['GET', 'POST'])
@login_required
def create_loan_agreement(loan_id):
    if 'admin' not in str(current_user.role or '').lower():
        return 'Access denied', 403

    loan = Loan.query.get_or_404(loan_id)
    if loan.status != 'Approved':
        flash("Loan must be approved before creating an agreement.", "warning")
        return redirect(url_for('loan'))

    from decimal import Decimal, InvalidOperation

    default_deduction = Decimal(str(loan.deduction_per_cutoff))
    if request.method == 'POST':
        try:
            deduction_per_cutoff = Decimal(request.form.get('deduction_per_cutoff', ''))
        except InvalidOperation:
            flash("Enter a valid deduction amount.", "danger")
            return redirect(url_for('create_loan_agreement', loan_id=loan.id))
        if not deduction_per_cutoff.is_finite() or deduction_per_cutoff <= 0:
            flash("Deduction per cutoff must be greater than zero.", "danger")
            return redirect(url_for('create_loan_agreement', loan_id=loan.id))
        if deduction_per_cutoff != default_deduction:
            flash('Use the deduction selected in the loan application; the agreement must match it.', 'danger')
            return redirect(url_for('create_loan_agreement', loan_id=loan.id))

        from io import BytesIO
        from xml.sax.saxutils import escape
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        body = loan_agreement_body(loan, deduction_per_cutoff)
        buffer = BytesIO()
        pdf = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=60, leftMargin=60)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'LoanAgreementTitle', parent=styles['Title'], alignment=1, spaceAfter=20,
        )
        body_style = ParagraphStyle(
            'LoanAgreementBody', parent=styles['BodyText'], leading=17, spaceAfter=10,
        )
        story = []
        for line_number, line in enumerate(body.splitlines()):
            if line_number == 0:
                story.append(Paragraph(escape(line), title_style))
            elif line:
                story.append(Paragraph(escape(line), body_style))
            else:
                story.append(Spacer(1, 10))
        pdf.build(story)
        buffer.seek(0)
        employee_name = secure_filename(loan.employee.full_name()) or 'Employee'
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"Loan_Agreement_LOAN-{loan.id:06d}_{employee_name}.pdf",
            mimetype='application/pdf',
        )

    return render_template(
        'loan_agreement.html',
        loan=loan,
        deduction_per_cutoff=default_deduction,
        agreement_body=loan_agreement_body(loan, default_deduction),
    )


@app.route('/admin/loans/opening-balance', methods=['POST'])
@login_required
def add_opening_loan_balance():
    if 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    employee = Employee.query.get_or_404(request.form.get('employee_id', type=int))
    try:
        amount = float(request.form.get('amount', 0))
        effective_date = datetime.strptime(request.form.get('effective_date', ''), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        flash('Enter a valid prior loan amount and effective date.', 'danger')
        return redirect(url_for('loan'))
    if amount <= 0:
        flash('Prior loan outstanding amount must be greater than zero.', 'danger')
        return redirect(url_for('loan'))
    if Loan.query.filter_by(
        employee_id=employee.id,
        loan_type='Prior-System Balance',
        balance_applied=True
    ).first():
        flash('A prior-system opening balance is already recorded for this employee.', 'warning')
        return redirect(url_for('loan'))

    balance_mode = request.form.get('balance_mode', 'add')
    if balance_mode == 'record_existing':
        employee.loan_balance = amount
    else:
        employee.loan_balance = float(employee.loan_balance or 0) + amount

    opening_loan = Loan(
        employee_id=employee.id,
        amount=amount,
        date_needed=effective_date,
        reason=request.form.get('reason', '').strip() or 'Outstanding loan before HRIS implementation',
        status='Approved',
        approver=current_user.full_name(),
        decision_date=datetime.utcnow(),
        date_filed=datetime.combine(effective_date, time.min),
        loan_type='Prior-System Balance',
        balance_applied=True,
    )
    db.session.add(opening_loan)
    db.session.commit()
    flash(f'Prior loan balance of PHP {amount:,.2f} added for {employee.full_name()}.', 'success')
    return redirect(url_for('loan'))


@app.route('/loan/statement/<int:employee_id>/download')
@login_required
def download_loan_statement(employee_id):
    if current_user.id != employee_id and 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    employee = Employee.query.get_or_404(employee_id)
    account = loan_account_summary(employee)
    loan_applications = Loan.query.filter_by(
        employee_id=employee.id
    ).order_by(Loan.date_filed.asc(), Loan.id.asc()).all()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30,
    )
    styles = getSampleStyleSheet()
    elements = [
        Paragraph(payroll_company_name(employee), styles['Title']),
        Paragraph('LOAN ACCOUNT STATEMENT', styles['Heading2']),
        Paragraph(f'Employee: {employee.full_name()} (ID: {employee.id})', styles['Normal']),
        Paragraph(f'Company: {employee.company or "N/A"}', styles['Normal']),
        Paragraph(f'Generated: {datetime.now():%Y-%m-%d %H:%M}', styles['Normal']),
        Spacer(1, 10),
        Paragraph(
            f'<b>Current Outstanding Balance: PHP {account["outstanding"]:,.2f}</b>',
            styles['Heading3'],
        ),
        Paragraph(
            f'Total approved/credited: PHP {account["total_credited"]:,.2f} | '
            f'Total payroll deductions: PHP {account["total_deducted"]:,.2f}',
            styles['Normal'],
        ),
        Spacer(1, 10),
    ]

    elements.append(Paragraph('Loan Applications', styles['Heading3']))
    application_rows = [['Application Date', 'Reference', 'Status', 'Amount', 'Reason']]
    for loan_record in loan_applications:
        application_rows.append([
            loan_record.date_filed.strftime('%Y-%m-%d') if loan_record.date_filed else str(loan_record.date_needed),
            f'LOAN-{loan_record.id:06d}',
            loan_record.status or 'Pending',
            f'PHP {float(loan_record.amount or 0):,.2f}',
            loan_record.reason or '',
        ])
    if len(application_rows) == 1:
        application_rows.append(['No applications', '', '', 'PHP 0.00', ''])

    table = Table(application_rows, colWidths=[80, 75, 65, 85, 215], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.75, colors.black),
        ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 10))
    elements.append(Paragraph('Payroll Deduction History', styles['Heading3']))
    deduction_rows = [['Payroll Reference', 'Cutoff Period', 'Total Deduction', 'Remaining Balance']]
    for payroll_record in sorted(account['deductions'], key=lambda record: (record.cutoff_start, record.id)):
        deduction_amount = float(payroll_record.loan or 0)
        running_balance = account['remaining_after'][payroll_record.id]
        deduction_rows.append([
            f'PAY-{payroll_record.id:06d}',
            f'{payroll_record.cutoff_start} to {payroll_record.cutoff_end}',
            f'PHP {deduction_amount:,.2f}',
            f'PHP {running_balance:,.2f}',
        ])
    if len(deduction_rows) == 1:
        deduction_rows.append(['No deductions', '', 'PHP 0.00', f'PHP {account["outstanding"]:,.2f}'])

    deduction_table = Table(deduction_rows, colWidths=[105, 175, 105, 135], repeatRows=1)
    deduction_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.75, colors.black),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(deduction_table)
    verification = build_document_verification(
        'loan_statement',
        employee.id,
        f'Loan-{employee.id}-{datetime.now().strftime("%Y%m%d")}',
        cutoff_start=datetime.now().date().replace(day=1),
        cutoff_end=datetime.now().date(),
        net_pay=float(account['outstanding']),
    )
    elements.append(Paragraph(f'Document ID: {verification["document_id"]}', styles['Italic']))
    if qrcode is not None:
        qr_bytes = generate_qr_image_bytes(verification['verify_url'])
        if qr_bytes:
            elements.append(Image(io.BytesIO(qr_bytes), width=60, height=60))
    document.build(elements)
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'Loan_Statement_{employee.first_name}_{datetime.now():%Y%m%d}.pdf',
        mimetype='application/pdf'
    )


# ------------------ EVALUATION DASHBOARD------------------
@app.route('/evaluation_dashboard', methods=['GET','POST'])
@login_required
def evaluation_dashboard():
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    ensure_peer_questions()
    employees = Employee.query.filter(
        Employee.role.ilike('%staff%'),
        Employee.first_name.isnot(None),
        Employee.first_name != '',
        Employee.last_name.isnot(None),
        Employee.last_name != '',
    ).order_by(Employee.last_name, Employee.first_name).all()
    questions = EvaluationQuestion.query.filter_by(is_active=True).all()
    month_start = datetime.today().date().replace(day=1)
    month_start_dt = datetime.combine(month_start, datetime.min.time())
    peer_status = []
    for emp in employees:
        submitted = Evaluation.query.filter(
            Evaluation.evaluator_id == emp.id,
            Evaluation.date >= month_start_dt,
            Evaluation.category.like("peer_%")
        ).first() is not None
        peer_status.append({"employee": emp, "submitted": submitted})

    if request.method == 'POST':
        action = request.form.get('action')
        if action in {'approve', 'reject'}:
            employee_id = request.form.get('employee_id', type=int)
            evaluator_id = request.form.get('evaluator_id', type=int)
            evaluation_type = request.form.get('evaluation_type')
            if evaluation_type not in {'peer', 'admin'}:
                flash('❌ Invalid evaluation type.', 'danger')
                return redirect(url_for('evaluation_dashboard'))
            evaluations_to_review = Evaluation.query.filter(
                Evaluation.employee_id == employee_id,
                Evaluation.evaluator_id == evaluator_id,
                Evaluation.date >= month_start_dt,
                Evaluation.category.like(f'{evaluation_type}_%'),
                Evaluation.approval_status == 'Pending'
            ).all()
            if not evaluations_to_review:
                flash('❌ No pending evaluation found.', 'danger')
                return redirect(url_for('evaluation_dashboard'))
            if action == 'reject':
                for evaluation in evaluations_to_review:
                    evaluation.approval_status = 'Rejected'
                db.session.commit()
                flash('Evaluation rejected. No merit or demerit points were applied.', 'success')
                return redirect(url_for('evaluation_dashboard'))

            average_rating = sum(evaluation.rating for evaluation in evaluations_to_review) / len(evaluations_to_review)
            if evaluation_type == 'admin':
                merit_points, demerit_points = actual_evaluation_points(average_rating)
            else:
                merit_points, demerit_points = evaluation_points(average_rating)
            employee = db.session.get(Employee, employee_id)
            if employee is None:
                flash('❌ Employee not found.', 'danger')
                return redirect(url_for('evaluation_dashboard'))
            employee.merit_points = (employee.merit_points or 0) + merit_points
            employee.demerit_points = (employee.demerit_points or 0) + demerit_points
            db.session.add(MeritDemerit(
                employee_id=employee.id,
                merit_points=merit_points,
                demerit_points=demerit_points,
                source='evaluation',
                reference=f'{evaluation_type.title()} evaluation average: {average_rating:.2f}'
            ))
            for evaluation in evaluations_to_review:
                evaluation.approval_status = 'Approved'
                evaluation.points_applied = True
            db.session.commit()
            flash(f'Evaluation approved. Average: {average_rating:.2f}; merit: {merit_points}, demerit: {demerit_points}.', 'success')
            return redirect(url_for('evaluation_dashboard'))

        for emp in employees:
            remarks = request.form.get(f'remarks_{emp.id}')
            rating_values = [request.form.get(f'q{question.id}_{emp.id}') for question in questions]
            if any(rating_values) and not all(rating_values):
                flash(f'❌ Complete every rating for {emp.full_name()} or leave the row blank.', 'danger')
                return redirect(url_for('evaluation_dashboard'))
            if all(rating_values):
                already_submitted = Evaluation.query.filter(
                    Evaluation.employee_id == emp.id,
                    Evaluation.evaluator_id == current_user.id,
                    Evaluation.date >= month_start_dt,
                    Evaluation.category.like('admin_%')
                ).first()
                if already_submitted:
                    flash(f'⚠️ You already evaluated {emp.full_name()} this month.', 'warning')
                    return redirect(url_for('evaluation_dashboard'))
                for question, rating_value in zip(questions, rating_values):
                    rating = int(rating_value)
                    if rating not in range(1, 6):
                        flash(f'❌ Ratings for {emp.full_name()} must be from 1 to 5.', 'danger')
                        return redirect(url_for('evaluation_dashboard'))
                    eval = Evaluation(
                        employee_id=emp.id,
                        evaluator_id=current_user.id,
                        rating=rating,
                        remarks=f"{question.text}: {remarks}" if remarks else question.text,
                        category=f'admin_{question.id}',
                        approval_status='Pending',
                        date=datetime.today()
                    )
                    db.session.add(eval)
        db.session.commit()
        flash("✅ Evaluations saved successfully!", "success")
        return redirect(url_for('evaluation_dashboard'))

    evaluations = Evaluation.query.order_by(Evaluation.date.desc()).all()
    pending_evaluations = {}
    for evaluation in Evaluation.query.filter(
        Evaluation.date >= month_start_dt,
        Evaluation.approval_status == 'Pending'
    ).order_by(Evaluation.date.desc()).all():
        evaluation_type = 'peer' if evaluation.category.startswith('peer_') else 'admin' if evaluation.category.startswith('admin_') else None
        if evaluation_type is None:
            continue
        key = (evaluation.employee_id, evaluation.evaluator_id, evaluation_type)
        pending_evaluations.setdefault(key, []).append(evaluation)
    pending_reviews = [
        {
            'employee': rows[0].employee,
            'evaluator': rows[0].evaluator,
            'evaluation_type': evaluation_type,
            'average_rating': sum(row.rating for row in rows) / len(rows),
            'question_count': len(rows)
        }
        for (_, _, evaluation_type), rows in pending_evaluations.items()
    ]
    return render_template("evaluation_dashboard.html",
                           employees=employees,
                           questions=questions,
                           evaluations=evaluations,
                           pending_reviews=pending_reviews,
                           peer_status=peer_status,
                           month_start=month_start)


@app.route('/evaluation_results/<string:file_format>', methods=['GET'])
@login_required
def evaluation_results_export(file_format):
    if file_format not in {'csv', 'pdf'}:
        return "Unsupported evaluation export format", 400

    requested_employee_id = request.args.get('employee_id', type=int)
    employee = None
    is_admin = "admin" in current_user.role.lower()
    if is_admin:
        query = Evaluation.query
        employee_label = "All Employees"
        if requested_employee_id:
            employee = Employee.query.get_or_404(requested_employee_id)
            query = query.filter_by(employee_id=employee.id)
            employee_label = employee.full_name()
    else:
        query = Evaluation.query.filter_by(
            employee_id=current_user.id,
            approval_status='Approved',
        )
        employee_label = current_user.full_name()

    evaluations = query.order_by(Evaluation.date.desc()).all()

    if file_format == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Date', 'Employee', 'Company', 'Evaluator', 'Rating', 'Category', 'Remarks'])
        for evaluation in evaluations:
            writer.writerow([
                evaluation.date.strftime('%Y-%m-%d %H:%M') if evaluation.date else '',
                evaluation.employee.full_name() if evaluation.employee else '',
                evaluation.employee.company if evaluation.employee else '',
                evaluation.evaluator.full_name() if is_admin and evaluation.evaluator else 'Anonymous',
                evaluation.rating or '',
                evaluation.category or '',
                evaluation.remarks or ''
            ])
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        response.headers['Content-Disposition'] = 'attachment; filename=evaluation_results.csv'
        return response

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont('Helvetica-Bold', 14)
    report_employee = employee if requested_employee_id else None
    report_company = payroll_company_name(report_employee) if report_employee else 'AUTO-EXPERT AUTO SUPPLY / TRECE-UNO AUTO SUPPLY'
    pdf.drawString(50, 780, report_company)
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawString(50, 760, 'EVALUATION RESULTS')
    pdf.setFont('Helvetica', 10)
    pdf.drawString(50, 730, f'Employee: {employee_label}')
    pdf.drawString(50, 715, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    y = 685
    for evaluation in evaluations:
        date_text = evaluation.date.strftime('%Y-%m-%d') if evaluation.date else 'N/A'
        employee_text = evaluation.employee.full_name() if evaluation.employee else 'N/A'
        evaluator_text = evaluation.evaluator.full_name() if is_admin and evaluation.evaluator else 'Anonymous'
        line = f'{date_text} | {employee_text} | Evaluator: {evaluator_text} | Rating: {evaluation.rating or "N/A"}'
        pdf.drawString(72, y, line[:115])
        y -= 15
        remarks = evaluation.remarks or 'No remarks'
        pdf.drawString(90, y, f'Remarks: {remarks}'[:105])
        y -= 22
        if y < 70:
            pdf.showPage()
            pdf.setFont('Helvetica', 10)
            y = 750
    if not evaluations:
        pdf.drawString(72, y, 'No evaluation results found.')
    pdf.line(72, 75, 260, 75)
    pdf.line(340, 75, 528, 75)
    pdf.drawString(72, 60, 'Authorized Person Signature')
    pdf.drawString(340, 60, 'Date')
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name='evaluation_results.pdf',
        mimetype='application/pdf'
    )


# ------------------ MANAGE QUESTIONS ------------------
@app.route('/manage_questions', methods=['GET','POST'])
@login_required
def manage_questions():
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    ensure_peer_questions()
    if request.method == 'POST':
        new_text = request.form.get('question_text')
        if new_text:
            q = EvaluationQuestion(text=new_text, category="core")
            db.session.add(q)
            db.session.commit()
            flash("✅ Question added!", "success")
    questions = EvaluationQuestion.query.order_by(EvaluationQuestion.id).all()
    return render_template("manage_questions.html", questions=questions)


@app.route('/edit_question/<int:id>', methods=['GET','POST'])
@login_required
def edit_question(id):
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    q = EvaluationQuestion.query.get_or_404(id)
    if request.method == 'POST':
        q.text = request.form.get('question_text')
        q.category = request.form.get('category') or q.category
        db.session.commit()
        flash("✅ Question updated!", "success")
        return redirect(url_for('manage_questions'))
    return render_template("edit_question.html", question=q)


@app.route('/delete_question/<int:id>', methods=['POST', 'GET'])
@login_required
def delete_question(id):
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    q = EvaluationQuestion.query.get_or_404(id)
    q.is_active = False
    db.session.commit()
    flash("✅ Question deactivated.", "success")
    return redirect(url_for('manage_questions'))


@app.route('/toggle_question/<int:id>', methods=['POST'])
@login_required
def toggle_question(id):
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    question = EvaluationQuestion.query.get_or_404(id)
    question.is_active = not question.is_active
    db.session.commit()
    flash(
        "✅ Question approved and active." if question.is_active else "✅ Question deactivated.",
        "success"
    )
    return redirect(url_for('manage_questions'))

# ------------------ QUIZ MODULE ------------------
import random, io, csv
try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:
    BackgroundScheduler = None

def generate_auto_quiz(category, num_questions=5):
    auto_questions = []

    if category == "General":
        auto_questions = [
            Quiz(category="General", question="Ano ang gamit ng spark plug?",
                 choice_a="Nagbibigay ng kuryente sa ilaw", choice_b="Nagpapasimula ng combustion sa engine",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapadulas ng piston",
                 correct_answer="B", points=1),
            Quiz(category="General", question="Ano ang gamit ng clutch?",
                 choice_a="Nagpapalit ng gulong", choice_b="Nagkokonekta ng engine sa transmission",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapalakas ng ilaw",
                 correct_answer="B", points=1),
            Quiz(category="General", question="Ano ang nag-iimbak ng kuryente?",
                 choice_a="Alternator", choice_b="Battery", choice_c="Starter", choice_d="Distributor",
                 correct_answer="B", points=1),
            Quiz(category="General", question="Ano ang nagpapadaloy ng coolant?",
                 choice_a="Radiator", choice_b="Water Pump", choice_c="Fan Belt", choice_d="Thermostat",
                 correct_answer="B", points=1),
            Quiz(category="General", question="Sino ang gumagawa ng Civic?",
                 choice_a="Toyota", choice_b="Honda", choice_c="Ford", choice_d="Nissan",
                 correct_answer="B", points=1),
        ]

    elif category == "Engine":
        auto_questions = [
            Quiz(category=category, question="Ano ang gamit ng spark plug?",
                 choice_a="Nagbibigay ng kuryente sa ilaw", choice_b="Nagpapasimula ng combustion sa engine",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapadulas ng piston",
                 correct_answer="B", points=1),
            Quiz(category=category, question="Ano ang gamit ng timing belt?",
                 choice_a="Nagpapakain ng gasolina", choice_b="Nagkokonekta ng crankshaft at camshaft",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagbibigay ng kuryente",
                 correct_answer="B", points=1),
            Quiz(category=category, question="Ano ang gamit ng piston rings?",
                 choice_a="Nagpapanatili ng compression", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagpapadulas ng gulong", choice_d="Nagbibigay ng kuryente",
                 correct_answer="A", points=1),
        ]

    elif category == "Transmission":
        auto_questions = [
            Quiz(category=category, question="Ano ang gamit ng clutch?",
                 choice_a="Nagpapalit ng gulong", choice_b="Nagkokonekta ng engine sa transmission",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapalakas ng ilaw",
                 correct_answer="B", points=1),
            Quiz(category=category, question="Anong fluid ang kailangan ng automatic transmission?",
                 choice_a="Brake Fluid", choice_b="Transmission Fluid", choice_c="Coolant", choice_d="Engine Oil",
                 correct_answer="B", points=1),
            Quiz(category=category, question="Ano ang gamit ng gear oil?",
                 choice_a="Nagpapadulas ng gears", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagbibigay ng kuryente", choice_d="Nagpapalakas ng ilaw",
                 correct_answer="A", points=1),
        ]

    elif category == "Electrical":
        auto_questions = [
            Quiz(category=category, question="Ano ang nag-iimbak ng kuryente?",
                 choice_a="Alternator", choice_b="Battery", choice_c="Starter", choice_d="Distributor",
                 correct_answer="B", points=1),
            Quiz(category=category, question="Ano ang gamit ng fuse?",
                 choice_a="Proteksyon laban sa short circuit", choice_b="Nagbibigay ng kuryente",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
            Quiz(category=category, question="Ano ang gamit ng ignition coil?",
                 choice_a="Nagpapalakas ng boltahe para sa spark plug", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagbibigay ng kuryente sa ilaw", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
        ]

    elif category == "Suspension":
        auto_questions = [
            Quiz(category=category, question="Ano ang sumasalo ng lubak?",
                 choice_a="Shock Absorber", choice_b="Spring", choice_c="Strut", choice_d="Control Arm",
                 correct_answer="A", points=1),
            Quiz(category=category, question="Ano ang gamit ng stabilizer bar?",
                 choice_a="Nagbabawas ng body roll", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagbibigay ng kuryente", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
        ]

    elif category == "Cooling":
        auto_questions = [
            Quiz(category=category, question="Ano ang nagpapadaloy ng coolant?",
                 choice_a="Radiator", choice_b="Water Pump", choice_c="Fan Belt", choice_d="Thermostat",
                 correct_answer="B", points=1),
            Quiz(category=category, question="Ano ang gamit ng radiator?",
                 choice_a="Nagpapalamig ng coolant", choice_b="Nagbibigay ng kuryente",
                 choice_c="Nagpapadulas ng piston", choice_d="Nagpapakain ng gasolina",
                 correct_answer="A", points=1),
        ]

    elif category == "Car Brands":
        auto_questions = [
            Quiz(category=category, question="Sino ang gumagawa ng Civic?",
                 choice_a="Toyota", choice_b="Honda", choice_c="Ford", choice_d="Nissan",
                 correct_answer="B", points=1),
            Quiz(category=category, question="Sino ang gumagawa ng Hilux?",
                 choice_a="Toyota", choice_b="Honda", choice_c="Ford", choice_d="Isuzu",
                 correct_answer="A", points=1),
        ]

    elif category == "Brakes":
        auto_questions = [
            Quiz(category=category, question="Ano ang gamit ng brake pads?",
                 choice_a="Nagbibigay ng kuryente", choice_b="Nagpapadulas ng piston",
                 choice_c="Nagbibigay ng friction para huminto", choice_d="Nagpapalamig ng makina",
                 correct_answer="C", points=1),
            Quiz(category=category, question="Ano ang gamit ng brake fluid?",
                 choice_a="Nagpapadala ng hydraulic pressure", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagbibigay ng kuryente", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
        ]

    elif category == "Steering":
        auto_questions = [
            Quiz(category=category, question="Ano ang gamit ng power steering pump?",
                 choice_a="Nagbibigay ng kuryente", choice_b="Nagpapadulas ng piston",
                 choice_c="Nagbibigay ng hydraulic pressure para sa steering", choice_d="Nagpapalamig ng makina",
                 correct_answer="C", points=1),
            Quiz(category=category, question="Ano ang gamit ng tie rod?",
                 choice_a="Nagkokonekta ng steering rack sa gulong", choice_b="Nagpapalamig ng makina",
                 choice_c="Nagbibigay ng kuryente", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
        ]

    elif category == "Exhaust":
        auto_questions = [
            Quiz(category=category, question="Ano ang gamit ng muffler?",
                 choice_a="Nagpapababa ng ingay ng tambutso", choice_b="Nagbibigay ng kuryente",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
            Quiz(category=category, question="Ano ang gamit ng catalytic converter?",
                 choice_a="Nagbabawas ng harmful emissions", choice_b="Nagbibigay ng kuryente",
                 choice_c="Nagpapalamig ng makina", choice_d="Nagpapadulas ng piston",
                 correct_answer="A", points=1),
        ]

    questions_pool = locals().get("auto_questions", [])

    if not questions_pool:
        raise ValueError(f"Walang available na questions para sa category: {category}")

    # Randomly select up to num_questions
    selected = random.sample(questions_pool, min(num_questions, len(questions_pool)))

    # Insert sa DB
    for q in selected:
        db.session.add(q)
    db.session.commit()

# ------------------ QUIZ RESULT ------------------

@app.route('/admin/quiz-upload', methods=['GET', 'POST'])
@login_required
def admin_quiz_upload():
    if "admin" not in current_user.role.lower():
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('dashboard_staff'))

    if request.method == 'POST':
        file = request.files.get('quiz_file')
        if not file or not file.filename.lower().endswith('.csv'):
            flash("❌ Please upload a CSV questionnaire file.", "danger")
            return redirect(url_for('admin_quiz_upload'))

        try:
            stream = io.StringIO(file.stream.read().decode('utf-8-sig'), newline=None)
            reader = csv.DictReader(stream)
            required = {'question', 'choice_a', 'choice_b', 'correct_answer'}
            headers = {str(header).strip().lower() for header in (reader.fieldnames or [])}
            if not required.issubset(headers):
                missing = ', '.join(sorted(required - headers))
                flash(f"❌ Missing CSV columns: {missing}", "danger")
                return redirect(url_for('admin_quiz_upload'))

            imported = 0
            for row in reader:
                normalized = {str(key).strip().lower(): (value or '').strip() for key, value in row.items()}
                if not normalized.get('question'):
                    continue
                db.session.add(Quiz(
                    category=normalized.get('category') or 'General',
                    question=normalized['question'],
                    choice_a=normalized['choice_a'],
                    choice_b=normalized['choice_b'],
                    choice_c=normalized.get('choice_c') or None,
                    choice_d=normalized.get('choice_d') or None,
                    correct_answer=normalized['correct_answer'].upper(),
                    points=int(normalized.get('points') or 1)
                ))
                imported += 1

            if not imported:
                flash("❌ The CSV did not contain any quiz questions.", "danger")
            else:
                db.session.commit()
                flash(f"✅ Imported {imported} quiz question(s).", "success")
        except (UnicodeDecodeError, ValueError, KeyError) as exc:
            db.session.rollback()
            logger.exception("Quiz CSV import failed")
            flash(f"❌ Quiz upload failed: {exc}", "danger")
        return redirect(url_for('admin_quiz_upload'))

    quiz_counts = db.session.query(Quiz.category, db.func.count(Quiz.id)).group_by(Quiz.category).all()
    return render_template('admin_quiz_upload.html', quiz_counts=quiz_counts)


@app.route('/quiz/<int:employee_id>', methods=['GET','POST'])
@login_required
def quiz(employee_id):
    emp = Employee.query.get_or_404(employee_id)

    if "staff" in current_user.role.lower() and current_user.id != employee_id:
        flash("❌ You can only take your own quiz.", "danger")
        return redirect(url_for('dashboard_staff'))

    # Staff can take quizzes; questionnaire uploads belong to the admin route.
    mode = request.form.get("mode", "view")
    category = request.form.get("category") or request.args.get("category", "General")
    start_requested = request.method == 'POST' and mode in {'auto', 'start'}

    if request.method == 'POST' and mode == "upload" and 'file' in request.files:
        flash("❌ Questionnaire uploads are available to administrators only.", "danger")
        return redirect(url_for('quiz', employee_id=employee_id, category=category))

    # Keep quiz availability and official attempts within the Manila calendar month.
    current_month_start = datetime.now(ZoneInfo('Asia/Manila')).date().replace(day=1)
    current_month = current_month_start.month
    current_year = current_month_start.year
    quiz_window_start, quiz_window_end = monthly_utc_window(current_month_start)
    category_quizzes = Quiz.query.filter(
        Quiz.date_created >= quiz_window_start,
        Quiz.date_created < quiz_window_end,
        Quiz.category == category
    ).all()

    if len({question.question.strip().casefold() for question in category_quizzes}) < 10:
        fallback_questions = HANDBOOK_QUIZ_QUESTIONS if category == 'Company Handbook' else [
            ("Ano ang pangunahing layunin ng regular na maintenance ng sasakyan?", "Maiwasan ang pagkasira", "Dagdagan ang ingay", "Bawasan ang kaligtasan", "Tanggalin ang preno", "A"),
            ("Ano ang dapat gawin bago magtrabaho sa makina?", "Patayin at palamigin ang makina", "Buksan ang lahat ng ilaw", "Tanggalin ang gulong", "Lagyan ng tubig ang gasolina", "A"),
            ("Ano ang gamit ng warning light sa dashboard?", "Magbigay ng babala", "Magpalit ng gulong", "Magdagdag ng gasolina", "Maglinis ng upuan", "A"),
            ("Ano ang mahalaga kapag nag-iinspeksyon ng sasakyan?", "Sundin ang checklist", "Hulaan ang resulta", "Laktawan ang safety", "Itago ang sira", "A"),
            ("Ano ang dapat gamitin upang maprotektahan ang mga mata?", "Safety goggles o salaming pangkaligtasan", "Open sandals", "Maluwag na tela", "Paper bag", "A"),
            ("Ano ang unang hakbang kapag may nakitang tagas ng langis?", "I-report at siyasatin", "Balewalain ito", "Dagdagan ang bilis", "Takpan ng papel", "A"),
            ("Bakit kailangang panatilihing malinis ang work area?", "Para maiwasan ang aksidente", "Para bumigat ang tools", "Para madulas ang sahig", "Para mawala ang labels", "A"),
            ("Ano ang tamang paraan ng paggamit ng tools?", "Gamitin ayon sa tamang gamit nito", "Ihagis pagkatapos gamitin", "Gamitin kahit sira", "Itago nang basa", "A"),
            ("Ano ang dapat gawin sa sirang equipment?", "Lagyan ng tag at i-report", "Gamitin pa rin", "Itago sa daan", "Ibigay sa customer", "A"),
            ("Ano ang dapat suriin bago i-release ang sasakyan?", "Kaligtasan at kalidad ng trabaho", "Kulay lang", "Busina lang", "Radio lang", "A")
        ]
        existing_questions = {question.question.strip().casefold() for question in category_quizzes}
        for question, choice_a, choice_b, choice_c, choice_d, correct_answer in fallback_questions:
            if len(existing_questions) >= 10:
                break
            if question.strip().casefold() in existing_questions:
                continue
            db.session.add(Quiz(
                category=category,
                question=question,
                choice_a=choice_a,
                choice_b=choice_b,
                choice_c=choice_c,
                choice_d=choice_d,
                correct_answer=correct_answer,
                points=1
            ))
            existing_questions.add(question.strip().casefold())
        db.session.commit()
        category_quizzes = Quiz.query.filter(
            Quiz.date_created >= quiz_window_start,
            Quiz.date_created < quiz_window_end,
            Quiz.category == category
        ).all()

    # Auto-generate kung wala pang quiz sa buwan na ito
    if not category_quizzes and mode == "auto":
        generate_auto_quiz(category)
        category_quizzes = Quiz.query.filter(
            Quiz.date_created >= quiz_window_start,
            Quiz.date_created < quiz_window_end,
            Quiz.category == category
        ).all()

    attempt_key = f'quiz_attempt_{employee_id}'
    attempt = session.get(attempt_key)
    selected_ids = []
    if request.method == 'POST' and mode == "take":
        if not attempt or attempt.get('category') != category:
            flash('This quiz attempt is no longer active. Start a new quiz.', 'warning')
            return redirect(url_for('quiz', employee_id=employee_id))
        try:
            attempt_started = datetime.fromisoformat(attempt['started_at'])
        except (KeyError, TypeError, ValueError):
            session.pop(attempt_key, None)
            flash('This quiz attempt could not be verified. Start a new quiz.', 'warning')
            return redirect(url_for('quiz', employee_id=employee_id))
        if (datetime.utcnow() - attempt_started).total_seconds() > 300:
            session.pop(attempt_key, None)
            flash('Time expired. The quiz was not accepted. Start a new attempt next month or contact Admin.', 'danger')
            return redirect(url_for('quiz', employee_id=employee_id))
        selected_ids = [int(value) for value in attempt.get('quiz_ids', [])]
        quizzes = Quiz.query.filter(Quiz.id.in_(selected_ids)).all()
        quizzes.sort(key=lambda question: selected_ids.index(question.id))
    elif start_requested:
        available_quizzes = list({question.question.strip().casefold(): question for question in category_quizzes}.values())
        if len(available_quizzes) < 10:
            available_quizzes = Quiz.query.filter(
                Quiz.date_created >= quiz_window_start,
                Quiz.date_created < quiz_window_end,
            ).all()
            available_quizzes = list({question.question.strip().casefold(): question for question in available_quizzes}.values())
        month_seed = f'{employee_id}:{current_year}:{current_month}:{category}'
        question_selector = random.Random(month_seed)
        quizzes = question_selector.sample(available_quizzes, min(10, len(available_quizzes)))
        session[attempt_key] = {
            'category': category,
            'quiz_ids': [question.id for question in quizzes],
            'started_at': datetime.utcnow().isoformat(),
        }
        return redirect(url_for('quiz', employee_id=employee_id, category=category))
    elif attempt and attempt.get('category') == category:
        try:
            attempt_started = datetime.fromisoformat(attempt['started_at'])
            if (datetime.utcnow() - attempt_started).total_seconds() > 300:
                session.pop(attempt_key, None)
                quizzes = []
            else:
                selected_ids = [int(value) for value in attempt.get('quiz_ids', [])]
                quizzes = Quiz.query.filter(Quiz.id.in_(selected_ids)).all()
                quizzes.sort(key=lambda question: selected_ids.index(question.id))
        except (KeyError, TypeError, ValueError):
            session.pop(attempt_key, None)
            quizzes = []
    else:
        quizzes = []

    if attempt and attempt.get('category') == category and not quizzes and request.method != 'POST':
        available_quizzes = list({
            question.question.strip().casefold(): question
            for question in category_quizzes
        }.values())
        if len(available_quizzes) < 10:
            available_quizzes = Quiz.query.filter_by(category=category).all()
            available_quizzes = list({
                question.question.strip().casefold(): question
                for question in available_quizzes
            }.values())
        if available_quizzes:
            month_seed = f'{employee_id}:{current_year}:{current_month}:{category}'
            question_selector = random.Random(month_seed)
            quizzes = question_selector.sample(available_quizzes, min(10, len(available_quizzes)))
            attempt['quiz_ids'] = [question.id for question in quizzes]
            attempt.pop('answer_keys', None)
            session[attempt_key] = attempt

    quiz_options = {}
    quiz_answer_keys = {}
    stored_options = attempt.get('display_options', {}) if attempt else {}
    for question in quizzes:
        stored_question_options = stored_options.get(str(question.id))
        if stored_question_options and attempt.get('answer_keys'):
            displayed_options = [tuple(option) for option in stored_question_options]
            quiz_answer_keys[str(question.id)] = attempt.get('answer_keys', {}).get(str(question.id))
        else:
            options = [
                (question.choice_a, 'A'),
                (question.choice_b, 'B'),
                (question.choice_c, 'C'),
                (question.choice_d, 'D'),
            ]
            options = [option for option in options if option[0]]
            random.shuffle(options)
            displayed_options = []
            for index, (option_text, original_key) in enumerate(options):
                display_key = chr(ord('A') + index)
                displayed_options.append((display_key, option_text))
                if original_key == question.correct_answer:
                    quiz_answer_keys[str(question.id)] = display_key
        quiz_options[str(question.id)] = displayed_options
    if request.method == 'POST' and mode == 'take':
        quiz_answer_keys = attempt.get('answer_keys', {})
    elif attempt and quizzes and not attempt.get('answer_keys'):
        attempt = session.get(attempt_key, {})
        attempt['answer_keys'] = quiz_answer_keys
        attempt['display_options'] = quiz_options
        session[attempt_key] = attempt

    # Handle quiz answers
    if request.method == 'POST' and mode == "take":
        score, total_points = 0, 0
        for quiz in quizzes:
            answer = request.form.get(f"quiz_{quiz.id}")
            total_points += quiz.points
            if answer == quiz_answer_keys.get(str(quiz.id)):
                score += quiz.points

        percentage = (score / total_points * 100) if total_points else 0

        existing = QuizResult.query.filter_by(
            employee_id=employee_id,
            is_official=True
        ).filter(
            QuizResult.date_taken >= quiz_window_start,
            QuizResult.date_taken < quiz_window_end,
        ).first()

        if existing:
            result = QuizResult(employee_id=employee_id, score=score, total_points=total_points, is_official=False)
            flash("Retake completed. This attempt is for practice only.", "info")
        else:
            result = QuizResult(employee_id=employee_id, score=score, total_points=total_points, is_official=True)
            if percentage >= 75:
                flash("🎉 Congratulations! You passed the quiz!", "success")
                emp.merit_points += 5
            else:
                flash("⚠️ Quiz failed. No demerit was applied automatically; Admin review is required.", "warning")

        db.session.add(result)
        db.session.commit()
        session.pop(attempt_key, None)
        return redirect(url_for('quiz', employee_id=employee_id))

    results = QuizResult.query.filter_by(employee_id=employee_id).all()
    return render_template(
        "quiz.html",
        emp=emp,
        quizzes=quizzes,
        results=results,
        category=category,
        mode=mode,
        quiz_ids=','.join(str(question.id) for question in quizzes),
        quiz_options=quiz_options,
        quiz_started=bool(session.get(attempt_key)),
        quiz_duration_seconds=300
    )

# ------------------ WORKFORCE ANALYTICS ------------------
def workforce_analytics(start, end, company='', employee_id=None):
    from collections import Counter, defaultdict

    query = Employee.query
    if company:
        query = query.filter(Employee.company == company)
    if employee_id is not None:
        query = query.filter(Employee.id == employee_id)
    employees = query.order_by(Employee.last_name, Employee.first_name, Employee.id).all()
    monthly = defaultdict(lambda: {
        'present': 0, 'absent': 0, 'late': 0, 'ot_hours': 0.0,
        'paid_gross': 0.0, 'paid_deductions': 0.0, 'paid_net': 0.0,
        'draft_gross': 0.0, 'draft_deductions': 0.0, 'draft_net': 0.0,
    })
    rows = []
    for employee in employees:
        logs, leaves, report = employee_performance_sources(employee.id, start, end)
        row = {
            'employee': employee, 'report': report, 'present': 0, 'absent': 0,
            'late': 0, 'late_minutes': 0, 'undertime': 0.0, 'regular_hours': 0.0,
            'ot_hours': 0.0, 'ot_estimate': 0.0, 'incomplete': 0,
        }
        for log in unique_attendance(logs):
            month = monthly[log.date.strftime('%Y-%m')]
            on_leave = any(leave.start_date <= log.date <= leave.end_date for leave in leaves)
            present = log.status == 'Present'
            absent = log.status == 'Absent' and not on_leave
            late = bool(log.clock_in and attendance_status(log.clock_in) == 'Late')
            for key, value in (('present', present), ('absent', absent), ('late', late)):
                row[key] += value
                month[key] += value
            if late:
                row['late_minutes'] += max(int((
                    log.clock_in - datetime.combine(log.date, time(8, 10))
                ).total_seconds() // 60), 0)
            if log.clock_in and log.clock_out:
                hours = regular_attendance_hours(log)
                row['regular_hours'] += hours
                row['undertime'] += max(attendance_scheduled_hours(employee, log.date) - hours, 0)
            elif log.status in {'Present', 'Late'} or log.clock_in or log.clock_out:
                row['incomplete'] += 1
            ot_hours = payroll_overtime_hours(log) if attendance_overtime_is_payable(log) else 0.0
            row['ot_hours'] += ot_hours
            month['ot_hours'] += ot_hours
            if ot_hours:
                row['ot_estimate'] += payroll_extra_pay(log, employee.daily_rate or 0)
        rows.append(row)
    employee_ids = [employee.id for employee in employees]
    payrolls = Payroll.query.filter(
        Payroll.employee_id.in_(employee_ids),
        Payroll.cutoff_start >= start, Payroll.cutoff_start <= end,
    ).order_by(Payroll.id).all() if employee_ids else []
    totals = {
        key: sum(row[key] for row in rows) for key in (
            'present', 'absent', 'late', 'late_minutes', 'undertime',
            'regular_hours', 'ot_hours', 'ot_estimate', 'incomplete',
        )
    }
    paid = {'count': 0, 'gross': 0.0, 'deductions': 0.0, 'net': 0.0}
    draft = dict(paid)
    snapshot_ot = 0.0
    legacy_payrolls = 0
    for record in payrolls:
        bucket = paid if record.is_paid else draft
        prefix = 'paid' if record.is_paid else 'draft'
        bucket['count'] += 1
        month = monthly[record.cutoff_start.strftime('%Y-%m')]
        for label, field in (('gross', 'gross_income'), ('deductions', 'total_deductions'), ('net', 'net_pay')):
            value = float(getattr(record, field) or 0)
            bucket[label] += value
            month[f'{prefix}_{label}'] += value
        if record.payslip_snapshot and record.is_paid:
            detail = build_payslip_breakdown(record.employee, record)
            snapshot_ot += sum(detail[key] for key in (
                'regular_overtime', 'rest_day', 'special_holiday_ot', 'regular_holiday_ot',
            ))
        elif record.is_paid:
            legacy_payrolls += 1
    return {
        'rows': rows, 'totals': totals, 'paid': paid, 'draft': draft,
        'monthly': dict(sorted(monthly.items())), 'snapshot_ot': snapshot_ot,
        'legacy_payrolls': legacy_payrolls,
        'companies': Counter(emp.company or 'Unassigned' for emp in employees),
        'statuses': Counter(emp.employment_status or 'Not recorded' for emp in employees),
    }


@app.route('/my-analytics', endpoint='my_analytics')
@app.route('/admin/analytics')
@login_required
def analytics():
    personal = request.endpoint == 'my_analytics'
    if not personal and 'admin' not in str(current_user.role or '').lower():
        return 'Access denied', 403
    if personal:
        supplied_employee = request.args.getlist('employee_id')
        supplied_company = request.args.getlist('company')
        if (
            any(value and value != str(current_user.id) for value in supplied_employee)
            or any(value and value != (current_user.company or '') for value in supplied_company)
        ):
            abort(403, description='My Analytics only permits your own account.')
    today = datetime.now(ZoneInfo('Asia/Manila')).date()
    company = '' if personal else request.args.get('company', '').strip()
    companies = [] if personal else [
        row[0] for row in db.session.query(Employee.company).distinct().order_by(Employee.company)
        if row[0]
    ]
    try:
        start = date.fromisoformat(request.args.get('start', today.replace(day=1).isoformat()))
        end = date.fromisoformat(request.args.get('end', today.isoformat()))
        if start > end or end == date.max:
            raise ValueError('Invalid date range')
        if company and company not in companies:
            raise ValueError('Unknown company')
        employee_value = str(current_user.id) if personal else request.args.get('employee_id', '').strip()
        employee_id = int(employee_value) if employee_value else None
        if employee_id is not None:
            employee = db.session.get(Employee, employee_id)
            if employee is None or (company and employee.company != company):
                raise ValueError('Employee does not match company')
        export = request.args.get('export', '')
        if export not in {'', 'csv'}:
            raise ValueError('Unknown export format')
    except ValueError:
        flash('Select a valid date range, company, employee and export format.', 'danger')
        return render_template('analytics.html', data=None, companies=companies,
                               personal=personal,
                               start=request.args.get('start', ''), end=request.args.get('end', ''),
                               company=company, employee_id=None, employees=[]), 400
    data = workforce_analytics(start, end, company, employee_id)
    if export == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)

        def write_row(values):
            writer.writerow([
                "'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@'))
                else value for value in values
            ])

        write_row(['My analytics' if personal else 'Workforce analytics', start.isoformat(), end.isoformat(),
                   (current_user.company or 'Unassigned') if personal else company or 'All companies'])
        write_row(['Scope', 'Attendance dates inclusive; payroll by cutoff start; current workforce; reporting points are not cash or balances.'])
        write_row(['OT estimate', 'Current rate estimate includes automatic Sunday/rest-day pay; not historical OT cost.'])
        write_row(['Employee ID', 'Employee', 'Company', 'Present', 'Recorded absent excluding approved leave',
                   'Late days', 'Late minutes', 'Undertime hours', 'Regular hours', 'OT hours',
                   'Estimated extra pay at current rate', 'Incomplete punches', 'Reporting merit', 'Reporting demerit',
                   'Admin average /5', 'Peer average /5', 'Official quiz average %',
                   'Excluded evaluations', 'Ignored duplicate attendance', 'Unattributed awards',
                   'Invalid official quiz records'])
        for row in data['rows']:
            emp, report = row['employee'], row['report']
            write_row([emp.id, emp.full_name(), emp.company or 'Unassigned',
                       *[row[key] for key in ('present', 'absent', 'late', 'late_minutes', 'undertime',
                                              'regular_hours', 'ot_hours', 'ot_estimate', 'incomplete')],
                       *[report[key] if report[key] is not None else '' for key in (
                           'merit', 'demerit', 'admin_average', 'peer_average', 'quiz_average',
                           'excluded_evaluations', 'duplicate_attendance', 'legacy_awards', 'invalid_quizzes')]])
        write_row([])
        write_row(['Employee ID', 'Point category', 'Reporting merit', 'Reporting demerit'])
        for row in data['rows']:
            for category, points in row['report']['categories'].items():
                write_row([row['employee'].id, category, *points])
        write_row([])
        write_row(['Saved payroll status', 'Record count', 'Gross income', 'Deductions', 'Net pay'])
        for name in ('paid', 'draft'):
            write_row([name, *[data[name][key] for key in ('count', 'gross', 'deductions', 'net')]])
        write_row(['Finalized snapshot OT cost only', data['snapshot_ot'], 'Finalized legacy records without OT breakdown', data['legacy_payrolls']])
        write_row([])
        write_row(['Month', 'Present', 'Recorded absent', 'Late', 'OT hours',
                   'Finalized gross', 'Finalized deductions', 'Finalized net',
                   'Saved draft gross', 'Saved draft deductions', 'Saved draft net'])
        for month, values in data['monthly'].items():
            write_row([month, *values.values()])
        if not personal:
            for label in ('companies', 'statuses'):
                write_row([f'Current workforce: {label}', 'Headcount'])
                for name, count in sorted(data[label].items()):
                    write_row([name, count])
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        filename = 'my_analytics' if personal else 'workforce_analytics'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}_{start}_{end}.csv'
        return response
    if personal:
        return render_template('analytics.html', data=data, start=start, end=end,
                               personal=True, company='', employee_id=current_user.id,
                               companies=[], employees=[])
    employees_query = Employee.query
    if company:
        employees_query = employees_query.filter(Employee.company == company)
    return render_template('analytics.html', data=data, start=start, end=end,
                           personal=False,
                           company=company, employee_id=employee_id, companies=companies,
                           employees=employees_query.order_by(Employee.last_name, Employee.first_name).all())


# ------------------ MERIT / DEMERIT ------------------
def employee_performance_sources(employee_id, start=None, end=None):
    models_and_dates = (
        (Attendance, Attendance.date), (Evaluation, Evaluation.date),
        (QuizResult, QuizResult.date_taken), (MeritDemerit, MeritDemerit.date),
    )
    sources = []
    for model, column in models_and_dates:
        query = model.query.filter(model.employee_id == employee_id)
        if start is not None:
            query = query.filter(column >= (start.replace(day=1) if model == Evaluation else start))
        if end is not None:
            if model == Evaluation:
                from calendar import monthrange
                month_end = end.replace(day=monthrange(end.year, end.month)[1])
                query = query.filter(column <= datetime.combine(month_end, time.max))
            else:
                query = query.filter(column < end + timedelta(days=1))
        sources.append(query.order_by(column, model.id).all())
    leave_query = LeaveRequest.query.filter_by(employee_id=employee_id, status='Approved')
    if start is not None:
        leave_query = leave_query.filter(LeaveRequest.end_date >= start)
    if end is not None:
        leave_query = leave_query.filter(LeaveRequest.start_date <= end)
    attendance, evaluations, quizzes, awards = sources
    leaves = leave_query.all()
    return attendance, leaves, merit_report(attendance, evaluations, quizzes, awards, leaves, start, end)


@app.route('/merit_demerit/<int:employee_id>')
@login_required
def merit_demerit(employee_id):
    if current_user.id != employee_id and 'admin' not in str(current_user.role or '').lower():
        return 'Access denied', 403
    emp = Employee.query.get_or_404(employee_id)

    attendance_logs, _, report = employee_performance_sources(employee_id)
    from collections import defaultdict
    monthly_records = defaultdict(list)
    for log in reversed(unique_attendance(attendance_logs)):
        month_key = log.date.strftime("%Y-%m")
        monthly_records[month_key].append(log)
    attendance_merit, attendance_demerit = report['categories']['attendance']
    eval_merit, eval_demerit = report['categories']['admin']
    peer_merit, peer_demerit = report['categories']['peer']
    quiz_merit, quiz_demerit = report['categories']['quiz']

    return render_template("merit_demerit.html",
                           report=report,
                           current_year=datetime.now(ZoneInfo('Asia/Manila')).year,
                           employee=emp,
                           monthly_records=monthly_records,
                           attendance_merit=attendance_merit,
                           attendance_demerit=attendance_demerit,
                           eval_merit=eval_merit,
                           eval_demerit=eval_demerit,
                           peer_merit=peer_merit,
                           peer_demerit=peer_demerit,
                           quiz_merit=quiz_merit,
                           quiz_demerit=quiz_demerit,
                           merit_points=report['merit'],
                           demerit_points=report['demerit'])


# ------------------ REDEEM POINTS ------------------
@app.route('/redeem_points/<int:employee_id>')
@login_required
def redeem_points(employee_id):
    emp = Employee.query.get_or_404(employee_id)

    # Check if already redeemed this year
    current_year = datetime.utcnow().year
    existing = RedemptionHistory.query.filter(
        RedemptionHistory.employee_id == emp.id,
        db.extract('year', RedemptionHistory.date_redeemed) == current_year
    ).first()

    if existing:
        flash("⚠ You already redeemed your points this year.", "warning")
        return redirect(url_for('merit_demerit', employee_id=employee_id))

    # recompute totals
    attendance_logs = Attendance.query.filter_by(employee_id=employee_id).all()
    attendance_merit = sum(1 for log in attendance_logs if log.status == "Present")
    attendance_demerit = sum(1 for log in attendance_logs if log.status == "Absent")

    evaluations = Evaluation.query.filter_by(employee_id=employee_id).all()
    eval_merit = sum(5 if e.score >= 90 else 3 if e.score >= 75 else 1 for e in evaluations)
    eval_demerit = sum(2 for e in evaluations if e.score < 60)

    quizzes = QuizResult.query.filter_by(employee_id=employee_id).all()
    quiz_merit = sum(3 if q.score >= 90 else 1 if q.score >= 75 else 0 for q in quizzes)
    quiz_demerit = sum(2 for q in quizzes if q.score < 75)

    merit_points = attendance_merit + eval_merit + quiz_merit
    demerit_points = attendance_demerit + eval_demerit + quiz_demerit
    cash_value = merit_points * 10

    # Save redemption history
    redemption = RedemptionHistory(
        employee_id=emp.id,
        points_redeemed=merit_points,
        cash_value=cash_value
    )
    db.session.add(redemption)
    db.session.commit()

    # Generate PDF claim form
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(100, 750, payroll_company_name(emp))
    c.drawString(150, 730, "Merit Points Redemption Form")
    c.setFont("Helvetica", 12)
    c.drawString(100, 700, f"Employee: {emp.first_name} {emp.last_name}")
    c.drawString(100, 680, f"Role: {emp.role}")
    c.drawString(100, 660, f"Company: {emp.company}")
    c.drawString(100, 640, f"Merit Points Redeemed: {merit_points}")
    c.drawString(100, 620, f"Cash Equivalent: PHP {cash_value:.2f}")
    c.drawString(100, 600, f"Date Redeemed: {datetime.utcnow().strftime('%Y-%m-%d')}")
    c.line(100, 540, 300, 540)
    c.line(380, 540, 550, 540)
    c.drawString(100, 525, "Authorized Person Signature")
    c.drawString(380, 525, "Date")
    c.save()
    buffer.seek(0)

    return send_file(buffer, as_attachment=True,
                     download_name="redeem_points.pdf",
                     mimetype="application/pdf")

# ------------------ REDEMPTION DASHBOARD ------------------
@app.route('/redemption_dashboard')
@login_required
def redemption_dashboard():
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    # Get all redemption records
    redemptions = RedemptionHistory.query.order_by(RedemptionHistory.date_redeemed.desc()).all()

    return render_template("redemption_dashboard.html", redemptions=redemptions)


# ------------------ IMPORT / EXPORT ------------------
@app.route('/export/<string:data_type>')
@login_required
def export_data(data_type):
    if data_type in {'merit_demerit_csv', 'merit_demerit_excel', 'merit_demerit_pdf'}:
        employee_id = request.args.get('employee_id', type=int) or current_user.id
        if current_user.id != employee_id and 'admin' not in str(current_user.role or '').lower():
            return 'Access denied', 403
        employee = Employee.query.get_or_404(employee_id)
        _, _, report = employee_performance_sources(employee_id)
        rows = [['Category', 'Merit', 'Demerit']]
        rows.extend([category, *points] for category, points in report['categories'].items())
        rows.append(['TOTAL', report['merit'], report['demerit']])
        if data_type in {'merit_demerit_csv', 'merit_demerit_excel'}:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['Employee', employee.full_name()])
            writer.writerow(['Generated', datetime.now().strftime('%Y-%m-%d %H:%M')])
            writer.writerow(['Scope', 'Read-only reporting points; not stored balances or cash entitlement'])
            writer.writerows(rows)
            response = make_response(output.getvalue())
            response.headers['Content-Type'] = 'text/csv; charset=utf-8'
            extension = 'csv' if data_type == 'merit_demerit_csv' else 'xls'
            response.headers['Content-Disposition'] = f'attachment; filename=merit_demerit_{employee.id}.{extension}'
            return response
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        pdf.setFont('Helvetica-Bold', 14)
        pdf.drawString(50, 780, payroll_company_name(employee))
        pdf.drawString(50, 760, 'MERIT / DEMERIT REPORT')
        pdf.setFont('Helvetica', 10)
        pdf.drawString(50, 735, f'Employee: {employee.full_name()} (ID: {employee.id})')
        pdf.drawString(50, 720, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}')
        pdf.drawString(50, 705, 'Reporting points only; stored balances and redemption are separate.')
        y = 690
        for row in rows:
            pdf.setFont('Helvetica-Bold' if row[0] in {'Category', 'TOTAL'} else 'Helvetica', 10)
            pdf.drawString(70, y, str(row[0]))
            pdf.drawRightString(260, y, str(row[1]))
            pdf.drawRightString(380, y, str(row[2]))
            y -= 20
        pdf.line(70, 92, 260, 92)
        pdf.line(330, 92, 520, 92)
        pdf.drawString(70, 76, 'Employee Signature / Date')
        pdf.drawString(330, 76, 'Authorized Admin / Date')
        pdf.save()
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f'Merit_Demerit_{employee.id}.pdf', mimetype='application/pdf')

    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
      <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
    </head>
    <body>
      <div class="card">
        <h2>Export Data</h2>
        <p>You requested to export: <b>{{ data_type }}</b></p>
        <p>Available formats: CSV / PDF</p>
        <button>Download {{ data_type }} (CSV)</button>
        <button>Download {{ data_type }} (PDF)</button>
        <a href="{{ url_for('dashboard_admin') }}">&#8592; Back to Dashboard</a>
      </div>
    </body>
    </html>
    ''', data_type=data_type)


COMPANY_FILE_FOLDERS = {
    'Auto Expert': 'auto-expert',
    'Trece': 'trece-uno',
}


def company_file_directory(company):
    normalized_company = normalize_company_name(company)
    folder = COMPANY_FILE_FOLDERS.get(normalized_company)
    if not folder:
        return None
    directory = os.path.join(app.config['FILES_FOLDER'], folder)
    os.makedirs(directory, exist_ok=True)
    return directory


def normalize_company_name(company):
    value = ' '.join(str(company or '').strip().lower().replace('-', ' ').split())
    if value.startswith('trece'):
        return 'Trece'
    if value.startswith('auto expert') or value.startswith('auto-expert'):
        return 'Auto Expert'
    return str(company or '').strip()


@app.route('/admin/files', methods=['GET', 'POST'])
@login_required
def admin_files():
    if 'admin' not in current_user.role.lower():
        flash('❌ Access denied. Admins only.', 'danger')
        return redirect(url_for('dashboard_staff'))

    if request.method == 'POST':
        company = request.form.get('company')
        file = request.files.get('file')
        directory = company_file_directory(company)
        filename = secure_filename(file.filename) if file and file.filename else ''
        if not directory or not filename:
            flash('❌ Select a company and a file to upload.', 'danger')
            return redirect(url_for('admin_files'))

        file.save(os.path.join(directory, filename))
        flash(f'✅ {filename} uploaded for {company}.', 'success')
        return redirect(url_for('admin_files'))

    company_files = {
        company: sorted(os.listdir(directory)) if directory and os.path.isdir(directory) else []
        for company in COMPANY_FILE_FOLDERS
        for directory in [company_file_directory(company)]
    }
    return render_template('admin_files.html', company_files=company_files,
                           companies=list(COMPANY_FILE_FOLDERS))


@app.route('/files')
@login_required
def company_files():
    company = normalize_company_name(current_user.company)
    directory = company_file_directory(company)
    files = sorted(os.listdir(directory)) if directory and os.path.isdir(directory) else []
    return render_template('company_files.html', files=files, company=company)


@app.route('/download/<string:company>/<string:filename>')
@login_required
def download_file(company, filename):
    normalized_company = normalize_company_name(company)
    normalized_user_company = normalize_company_name(current_user.company)
    if normalized_company != normalized_user_company and 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    directory = company_file_directory(normalized_company)
    safe_filename = secure_filename(filename)
    if not directory or not safe_filename or safe_filename != filename:
        return 'File not found', 404

    filepath = os.path.join(directory, safe_filename)
    if not os.path.isfile(filepath):
        return 'File not found', 404
    return send_file(filepath, as_attachment=True, download_name=filename)


HR_TEMPLATE_ALLOWED_EXTENSIONS = {'.doc', '.docx', '.xls', '.xlsx', '.pdf'}
HR_TEMPLATE_MAX_FILE_BYTES = 20 * 1024 * 1024


def save_hr_template_upload(upload):
    original_filename = secure_filename(upload.filename or '')
    extension = os.path.splitext(original_filename)[1].lower()
    if not original_filename or extension not in HR_TEMPLATE_ALLOWED_EXTENSIONS:
        raise ValueError('Upload a Word, Excel, or PDF file.')

    upload.stream.seek(0, os.SEEK_END)
    file_size = upload.stream.tell()
    upload.stream.seek(0)
    if file_size > HR_TEMPLATE_MAX_FILE_BYTES:
        raise ValueError('Template files must be 20 MB or smaller.')

    stored_filename = f'{secrets.token_hex(16)}{extension}'
    try:
        upload.save(os.path.join(app.config['HR_TEMPLATES_FOLDER'], stored_filename))
    except OSError:
        logger.exception('Could not save an HR template upload')
        raise
    return stored_filename, original_filename


def validate_hr_template_url(value):
    value = (value or '').strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        parsed = None
        hostname = None
    if (
        not parsed
        or parsed.scheme.lower() != 'https'
        or not hostname
        or any(character.isspace() for character in hostname)
        or parsed.username
        or parsed.password
    ):
        raise ValueError('Online edit links must be valid HTTPS URLs.')
    return value


def remove_hr_template_file(filename):
    if not filename:
        return True
    safe_filename = secure_filename(filename)
    if safe_filename != filename:
        logger.error('Refusing to remove an unsafe HR template filename')
        return False
    path = os.path.join(app.config['HR_TEMPLATES_FOLDER'], safe_filename)
    try:
        if os.path.exists(path):
            os.remove(path)
        return True
    except OSError:
        logger.exception('Could not remove stored HR template file %s', safe_filename)
        return False


@app.route('/admin/hr-templates', methods=['GET', 'POST'])
@login_required
def admin_hr_templates():
    if 'admin' not in str(current_user.role or '').lower():
        abort(403)

    if request.method == 'POST':
        action = request.form.get('action', 'create')
        template = None
        if action in {'update', 'delete'}:
            template = HRTemplate.query.get_or_404(request.form.get('template_id', type=int))

        if action == 'delete':
            old_filename = template.stored_filename
            db.session.delete(template)
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                logger.exception('Could not delete HR template record %s', template.id)
                flash('The template could not be deleted. Please try again.', 'danger')
                return redirect(url_for('admin_hr_templates'))
            if not remove_hr_template_file(old_filename):
                flash('The template record was deleted, but its stored file could not be removed.', 'danger')
            else:
                flash('Template deleted.', 'success')
            return redirect(url_for('admin_hr_templates'))

        if action not in {'create', 'update'}:
            flash('Choose a valid template action.', 'danger')
            return redirect(url_for('admin_hr_templates'))

        title = request.form.get('title', '').strip()
        category = request.form.get('category', '').strip()
        description = request.form.get('description', '').strip()
        source_filename = secure_filename(request.form.get('source_filename', '').strip()) or None
        upload = request.files.get('template_file')
        has_upload = bool(upload and upload.filename)
        try:
            online_url = validate_hr_template_url(request.form.get('online_url'))
            if not title or len(title) > 180 or not category or len(category) > 80:
                raise ValueError('Enter a template name (up to 180 characters) and category (up to 80 characters).')
            if len(description) > 4000:
                raise ValueError('Description must be 4,000 characters or fewer.')
            if source_filename and len(source_filename) > 255:
                raise ValueError('Expected filename must be 255 characters or fewer.')
            if not has_upload and not online_url and template is None:
                raise ValueError('Upload a template file or provide an online edit link.')
            new_filename, original_filename = save_hr_template_upload(upload) if has_upload else (None, None)
        except ValueError as error:
            flash(str(error), 'danger')
            return redirect(url_for('admin_hr_templates'))
        except OSError:
            flash('The template file could not be saved. Please try again.', 'danger')
            return redirect(url_for('admin_hr_templates'))

        old_filename = template.stored_filename if template else None
        if template is None:
            template = HRTemplate(created_by=current_user.id)
            db.session.add(template)
        template.title = title
        template.category = category
        template.description = description or None
        template.source_filename = source_filename
        template.online_url = online_url
        if new_filename:
            template.stored_filename = new_filename
            template.original_filename = original_filename
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            if new_filename:
                remove_hr_template_file(new_filename)
            logger.exception('Could not save HR template record')
            flash('The template could not be saved. Please try again.', 'danger')
            return redirect(url_for('admin_hr_templates'))

        previous_file_removed = (
            remove_hr_template_file(old_filename)
            if new_filename and old_filename
            else True
        )
        if previous_file_removed:
            flash('Template saved.', 'success')
        else:
            flash('Template saved, but the previous file could not be removed.', 'warning')
        return redirect(url_for('admin_hr_templates'))

    templates = HRTemplate.query.order_by(HRTemplate.category, HRTemplate.title).all()
    return render_template('admin_hr_templates.html', templates=templates)


@app.route('/admin/hr-templates/<int:template_id>/download')
@login_required
def download_hr_template(template_id):
    if 'admin' not in str(current_user.role or '').lower():
        abort(403)
    template = HRTemplate.query.get_or_404(template_id)
    filename = template.stored_filename or ''
    safe_filename = secure_filename(filename)
    if not safe_filename or safe_filename != filename:
        abort(404)
    filepath = os.path.join(app.config['HR_TEMPLATES_FOLDER'], safe_filename)
    if not os.path.isfile(filepath):
        abort(404)
    return send_file(
        filepath,
        as_attachment=True,
        download_name=template.original_filename or template.title,
    )


# ------------------ BULLETIN MODULE ------------------
@app.route('/admin/bulletins', methods=['POST'])
@login_required
def create_bulletin():
    if 'admin' not in current_user.role.lower():
        return 'Access denied', 403

    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    if not title or not content:
        flash('Announcement title and content are required.', 'danger')
        return redirect(url_for('dashboard_admin') + '#bulletins')

    post = Bulletin(title=title, content=content, author=current_user.full_name())
    db.session.add(post)
    db.session.commit()

    recipient_emails = [
        employee.email for employee in Employee.query.filter(Employee.email.isnot(None)).all()
        if employee.email
    ]
    email_sent = False
    if mail and app.config.get('MAIL_SERVER') and recipient_emails:
        try:
            message = Message(
                subject=f'Company Announcement: {title}',
                sender=app.config.get('MAIL_DEFAULT_SENDER'),
                recipients=recipient_emails
            )
            message.body = f'{title}\n\n{content}\n\nPosted by: {current_user.full_name()}'
            mail.send(message)
            email_sent = True
        except Exception:
            logger.exception('Announcement email could not be sent')

    flash(
        'Announcement published and email notifications sent.' if email_sent else
        'Announcement published. Email notifications were skipped because email is not configured.',
        'success'
    )
    return redirect(url_for('dashboard_admin') + '#bulletins')


@app.route('/admin/bulletins/<int:bulletin_id>/delete', methods=['POST'])
@login_required
def delete_bulletin(bulletin_id):
    if 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    post = Bulletin.query.get_or_404(bulletin_id)
    db.session.delete(post)
    db.session.commit()
    flash('Announcement deleted.', 'success')
    return redirect(url_for('dashboard_admin') + '#bulletins')


@app.route('/bulletin')
@login_required
def bulletin():
    posts = Bulletin.query.order_by(Bulletin.created_at.desc()).all()
    return render_template("bulletin.html", posts=posts)

# ------------------BACK UP ------------------
BACKUP_FILE_FOLDERS = ('static/uploads', 'static/files')


def build_complete_backup_zip():
    import zipfile
    buffer = io.BytesIO()
    file_count = 0
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('hris_database.db', build_database_backup_bytes())
        for relative_root in BACKUP_FILE_FOLDERS:
            source_root = os.path.join(basedir, relative_root)
            if not os.path.isdir(source_root):
                continue
            for root, _, filenames in os.walk(source_root):
                for filename in filenames:
                    source_path = os.path.join(root, filename)
                    archive_name = os.path.relpath(source_path, basedir).replace(os.sep, '/')
                    archive.write(source_path, archive_name)
                    file_count += 1
        archive.writestr(
            'BACKUP_CONTENTS.txt',
            'hris_database.db: all HRIS records (employees, attendance, payroll, loans, leave, etc.)\n'
            f'static/: {file_count} uploaded profile/company files\n',
        )
    return buffer.getvalue()


@app.route('/backup')
@login_required
def backup():
    if "admin" not in current_user.role.lower():
        flash("? Access denied. Admins only.", "danger")
        return redirect(url_for('dashboard_staff'))
    last_backup = AuditLog.query.filter(
        AuditLog.action.in_([
            'Downloaded full database backup',
            'Downloaded complete backup with files',
        ])
    ).order_by(AuditLog.id.desc()).first()
    last_backup_text = None
    if last_backup:
        last_backup_text = (
            f"{(last_backup.created_at + timedelta(hours=8)):%B %d, %Y %I:%M %p} "
            f"by {last_backup.employee_name}"
        )
    return render_template("backup_local.html", last_backup=last_backup_text)


@app.route('/download_db')
@login_required
def download_db():
    if "admin" not in current_user.role.lower():
        return "Access denied", 403
    backup_bytes = build_complete_backup_zip()
    record_audit_action('Downloaded complete backup with files')
    return send_file(
        io.BytesIO(backup_bytes),
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'hris_full_backup_{datetime.now(ZoneInfo("Asia/Manila")):%Y%m%d_%H%M}.zip',
    )

# ------------------ LOG OUT ------------------
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

from flask_login import current_user
from datetime import datetime

@app.before_request
def check_lunch_resume():
    now = datetime.now()
    if now.hour == 13 and now.minute == 0:
        if current_user.is_authenticated:
            active_attendance = Attendance.query.filter_by(
                employee_id=current_user.id,
                date=now.date()
            ).filter(Attendance.clock_out == None).first()
            if active_attendance:
                send_resume_work_email(current_user)

# ------------------ MAIN ------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    cert_file = os.environ.get("HRIS_SSL_CERT")
    key_file = os.environ.get("HRIS_SSL_KEY")
    ssl_context = (cert_file, key_file) if cert_file and key_file else None
    if ssl_context:
        app.config["SESSION_COOKIE_SECURE"] = True
        logger.info("Starting HRIS with HTTPS on port 5002")
    else:
        logger.warning("Starting HRIS over HTTP. Set HRIS_SSL_CERT and HRIS_SSL_KEY for HTTPS.")
    debug_mode = os.environ.get("HRIS_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode, host="0.0.0.0", port=5002, ssl_context=ssl_context)
