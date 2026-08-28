# ------------------ HRIS MAIN APP ------------------
import os, random, logging
import secrets
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
from flask import Flask, render_template, render_template_string, request, redirect, url_for, flash, send_file, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename 

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

# ------------------ APP CONFIG ------------------
app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))


@app.route('/service-worker.js')
def service_worker():
    return send_from_directory(os.path.join(basedir, 'static'), 'service-worker.js')

if load_dotenv:
    load_dotenv(os.path.join(basedir, '.env'))
    load_dotenv(os.path.join(basedir, '.env.local'))
else:
    logger.warning("python-dotenv not installed; .env file values will not be loaded")

app.secret_key = os.environ.get("HRIS_SECRET_KEY", "secret")
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
instance_dir = os.path.join(basedir, 'instance')
os.makedirs(instance_dir, exist_ok=True)
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


def send_notification_email(recipients, subject, body):
    """Send an email only when the optional mail service is configured."""
    recipients = [email for email in recipients if email]
    if not mail:
        logger.warning("Email send skipped: Flask-Mail is not available.")
        return False
    if not app.config.get('MAIL_SERVER'):
        logger.warning("Email send skipped: MAIL_SERVER is not configured.")
        return False
    if not recipients:
        logger.warning("Email send skipped: no recipients provided.")
        return False
    try:
        mail.send(Message(
            subject=subject,
            sender=app.config.get('MAIL_DEFAULT_SENDER'),
            recipients=recipients,
            body=body
        ))
        return True
    except Exception:
        logger.exception("Notification email could not be sent")
        return False


def notify_admins(subject, body):
    admin_emails = [
        employee.email for employee in Employee.query.filter(Employee.role.ilike('%admin%')).all()
    ]
    return send_notification_email(admin_emails, subject, body)


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
    Attendance,
    Holiday,
    LeaveRequest,
    LeaveHistory,
    Loan,
    LoanHistory,
    Evaluation,
    EvaluationQuestion,
    Quiz,
    QuizResult,
    Bulletin,
    Payroll,
    RedemptionHistory,
    IncidentReport,
    MeritDemerit,
    EmployeeDocument,
    PasswordResetToken,
    OTApplication
)

from utils.helpers import compute_weekly_deductions, compute_merit_demerit, ai_suggestion


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
        evaluations = Evaluation.query.filter_by(employee_id=current_user.id).order_by(Evaluation.date.desc()).all()
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
    "Communicates clearly and respectfully with the team.",
    "Completes assigned work accurately and on time.",
    "Shows teamwork and supports coworkers.",
    "Demonstrates professionalism and accountability.",
    "Responds constructively to feedback and workplace concerns."
]


def ensure_peer_questions():
    if not EvaluationQuestion.query.first():
        for text in DEFAULT_PEER_QUESTIONS:
            db.session.add(EvaluationQuestion(text=text, category="peer", is_active=True))
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


def generate_monthly_peer_eval_reminder():
    """Create a bulletin and optionally email admins for employees
    who still need peer evaluation for the current month."""
    try:
        from datetime import datetime
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        pending = Employee.query.filter(~Employee.evaluations.any(Evaluation.date >= month_start)).all()
        if not pending:
            logger.info("No pending peer evaluations this month.")
            return

        title = f"Peer evaluations due: {len(pending)} employees"
        content_lines = [f"{e.id}: {e.full_name()}" for e in pending]
        content = "\n".join(content_lines)

        post = Bulletin(title=title, content=content, author="System")
        db.session.add(post)
        db.session.commit()

        # Try to email admins if mail configured
        try:
            admins = Employee.query.filter(Employee.role.ilike('%admin%')).all()
            if mail and admins and app.config.get('MAIL_SERVER'):
                with app.app_context():
                    for a in admins:
                        if not a.email:
                            continue
                        msg = Message(subject="Peer evaluations due",
                                      sender=app.config.get('MAIL_USERNAME'),
                                      recipients=[a.email])
                        msg.body = title + "\n\n" + content
                        mail.send(msg)
        except Exception:
            logger.exception("Failed to send peer evaluation reminder emails")
    except Exception:
        logger.exception("Error running monthly peer-eval reminder job")


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
with app.app_context():
    db.create_all()

migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ------------------ APScheduler setup (optional) ------------------
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    categories = ["Engine", "Transmission", "Electrical", "Suspension",
                  "Cooling", "Car Brands", "Brakes", "Steering", "Exhaust"]

    for cat in categories:
        if scheduler:
            scheduler.add_job(generate_auto_quiz, 'cron', day=1, hour=0, args=[cat])

    # Monthly peer evaluation reminder: post bulletin and email admins
    if scheduler:
        try:
            scheduler.add_job(generate_monthly_peer_eval_reminder, 'cron', day=1, hour=8)
            scheduler.add_job(send_lunch_reminder, 'cron', hour=12, minute=0,
                              args=['start'], id='lunch_start_reminder', replace_existing=True)
            scheduler.add_job(send_lunch_reminder, 'cron', hour=13, minute=0,
                              args=['resume'], id='lunch_resume_reminder', replace_existing=True)
        except Exception:
            logger.exception('Failed to register monthly peer-eval reminder job')

    if scheduler:
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

# ----------------- LOGIN + FORGOT PASSWORD ------------------
from werkzeug.security import check_password_hash, generate_password_hash


@app.route('/login', methods=['GET', 'POST'])
def login():
    action = request.form.get('action')

    if request.method == 'POST':
        email = request.form.get('email')

        # --- LOGIN FLOW ---
        if action == "login":
            password = request.form.get('password')
            remember = 'remember' in request.form

            user = Employee.query.filter_by(email=email).first()
            if user and check_password_hash(user.password, password):
                login_user(user, remember=remember)

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


@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        emp = Employee.query.filter_by(email=email).first()

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
            send_notification_email(
                [emp.email],
                "Password Reset Request",
                (
                    "Use this link to reset your HRIS password:\n"
                    f"{reset_url}\n\n"
                    "This link expires in 1 hour and can only be used once."
                )
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

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template('reset_password.html', token=token)
        if password != password_confirm:
            flash("Passwords do not match.", "danger")
            return render_template('reset_password.html', token=token)

        reset_token.employee.set_password(password)
        reset_token.used = True
        db.session.commit()
        flash("Password reset successful. Please login.", "success")
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)

# ------------------ GLOBAL AUTH CHECK ------------------
@app.before_request
def check_authentication():
    exempt_routes = ['login', 'register', 'static', 'service_worker', 'forgot_password', 'reset_password']
    if not current_user.is_authenticated and request.endpoint not in exempt_routes:
        return redirect(url_for('login'))


# ------------------ REGISTER EMP ADMIN ------------------
from werkzeug.security import generate_password_hash

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
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

        date_started_str = request.form.get('date_started')
        date_started_val = datetime.strptime(date_started_str, '%Y-%m-%d').date() if date_started_str else None
        
        dob_str = request.form.get('dob')
        dob_val = datetime.strptime(dob_str, '%Y-%m-%d').date() if dob_str else None

        emp_address = request.form.get('address')
        emergency_address = request.form.get('emergency_address')
        if request.form.get('same_address'):
            emergency_address = emp_address

        emp = Employee(
            first_name=request.form['first_name'],
            middle_name=request.form.get('middle_name'),
            last_name=request.form['last_name'],
            suffix_name=request.form.get('suffix_name'),
            dob=dob_val,
            role=request.form['role'],
            company=company,
            email=request.form['email'],
            password=generate_password_hash(request.form['password']),
            contact_no=request.form.get('contact_no'),
            registered_at=datetime.utcnow(),
            date_started=date_started_val,
            sss=request.form.get('sss'),
            philhealth=request.form.get('philhealth'),
            tin=request.form.get('tin'),
            pagibig=request.form.get('pagibig'),
            emergency_person=request.form.get('emergency_person'),
            emergency_contact=request.form.get('emergency_contact'),
            emergency_address=emergency_address,
            profile_pic=filename,
            address=emp_address
        )
        
        db.session.add(emp)
        db.session.commit()
        flash("🎉 Congratulations! You are now registered.", "success")
        return redirect(url_for('login'))

    return render_template("register.html")


# ------------------ PROFILE (SELF / ADMIN) ------------------
@app.route('/profile/<int:user_id>', methods=['GET', 'POST'])
@login_required
def profile(user_id):
    emp = Employee.query.get_or_404(user_id)

    # --- Update only if self or admin ---
    if request.method == 'POST':
        if current_user.id == emp.id or current_user.role.lower() == "admin":
            emp.first_name = request.form.get('first_name')
            emp.last_name = request.form.get('last_name')
            emp.email = request.form.get('email')
            emp.contact_no = request.form.get('contact_no')
            emp.address = request.form.get('address')
            emp.sss = request.form.get('sss')
            emp.philhealth = request.form.get('philhealth')
            emp.tin = request.form.get('tin')
            emp.pagibig = request.form.get('pagibig')
            emp.emergency_person = request.form.get('emergency_person')
            emp.emergency_contact = request.form.get('emergency_contact')
            emp.emergency_address = request.form.get('emergency_address')

            dob_str = request.form.get('dob')
            if dob_str:
                emp.dob = datetime.strptime(dob_str, '%Y-%m-%d').date()

            date_started_str = request.form.get('date_started')
            if date_started_str:
                emp.date_started = datetime.strptime(date_started_str, '%Y-%m-%d').date()

            if 'profile_pic' in request.files:
                file = request.files['profile_pic']
                if file and file.filename != '':
                    filename = secure_filename(file.filename)
                    file.save(os.path.join(app.static_folder, 'uploads', filename))
                    emp.profile_pic = filename

            db.session.commit()
            flash("✅ Profile updated successfully!", "success")
            return redirect(url_for('profile', user_id=user_id))
        else:
            flash("❌ You cannot edit another user's profile unless you're admin.", "danger")

    return render_template(
        "profile.html",
        emp=emp,
        viewer=current_user,
        bulletins=Bulletin.query.order_by(Bulletin.created_at.desc()).limit(10).all()
    )


def admin_staff_choices():
    return Employee.query.order_by(
        Employee.company, Employee.first_name, Employee.last_name
    ).all()


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

    write_line('EMPLOYEE 201 FILE', bold=True, size=16, gap=24)
    write_line(f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}', size=9)
    write_line('PERSONAL INFORMATION', bold=True, size=12, gap=20)
    write_line(f'Name: {employee.first_name} {employee.last_name}')
    write_line(f'Employee ID: {employee.id}')
    write_line(f'Role: {employee.role or "N/A"}')
    write_line(f'Company: {employee.company or "N/A"}')
    write_line(f'Email: {employee.email or "N/A"}')
    write_line(f'Contact: {employee.contact_no or "N/A"}')
    write_line(f'Address: {employee.address or "N/A"}')
    write_line(f'Date Started: {employee.date_started or "N/A"}')
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
        retention_guidance=retention_guidance,
        can_manage_documents=is_admin
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

    employee = Employee.query.get_or_404(employee_id)
    dependent_queries = [
        Attendance.query.filter_by(employee_id=employee_id),
        LeaveRequest.query.filter_by(employee_id=employee_id),
        LeaveHistory.query.filter_by(employee_id=employee_id),
        Loan.query.filter_by(employee_id=employee_id),
        LoanHistory.query.filter_by(employee_id=employee_id),
        Payroll.query.filter_by(employee_id=employee_id),
        QuizResult.query.filter_by(employee_id=employee_id),
        MeritDemerit.query.filter_by(employee_id=employee_id),
        RedemptionHistory.query.filter_by(employee_id=employee_id),
        IncidentReport.query.filter_by(employee_id=employee_id),
        IncidentReport.query.filter_by(reviewed_by=employee_id),
        EmployeeDocument.query.filter_by(employee_id=employee_id),
        Evaluation.query.filter(
            db.or_(Evaluation.employee_id == employee_id, Evaluation.evaluator_id == employee_id)
        )
    ]
    for query in dependent_queries:
        query.delete(synchronize_session=False)
    db.session.delete(employee)
    db.session.commit()
    flash(f'✅ Employee {employee.first_name} {employee.last_name} and related records were deleted.', 'success')
    return redirect(url_for('dashboard_admin'))

# ------------------ ATTENDANCE REPORT ------------------
import io, csv
from flask import Response, make_response, request, render_template
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


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
    write_line('Role', employee.role)
    write_line('Email', employee.email)
    write_line('Contact Number', employee.contact_no)
    write_line('Date of Birth', employee.dob)
    write_line('Address', employee.address)
    write_line('Date Started', employee.date_started)
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
    pdf.drawString(54, 44, f'Generated on {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    pdf.save()
    buffer.seek(0)
    filename = f'employee_information_{employee.id}.pdf'
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')

@app.route('/attendance/<int:employee_id>')
@login_required
def attendance(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    history = Attendance.query.filter_by(employee_id=employee_id).order_by(Attendance.date.desc()).all()

    # --- Summary metrics ---
    total_present = sum(1 for log in history if log.status == "Present")
    total_late = sum(1 for log in history if log.status == "Late")
    total_absent = sum(1 for log in history if log.status == "Absent")
    total_days = len(history)

    total_hours = sum(((log.clock_out - log.clock_in).seconds / 3600)
                      for log in history if log.clock_in and log.clock_out)
    valid_days = sum(1 for log in history if log.clock_in and log.clock_out)
    avg_hours = total_hours / valid_days if valid_days else 0

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

    # --- DTR Records ---
    dtr_records = []
    for log in history:
        hours_worked = (log.clock_out - log.clock_in).seconds / 3600 if log.clock_in and log.clock_out else 0
        dtr_records.append({
            "date": log.date.strftime('%Y-%m-%d'),
            "clock_in": log.clock_in.strftime('%H:%M:%S') if log.clock_in else "N/A",
            "clock_out": log.clock_out.strftime('%H:%M:%S') if log.clock_out else "N/A",
            "status": log.status,
            "hours": hours_worked,
            "branch": getattr(log, "company", "N/A")  # ginamit ko 'company' field para consistent
        })

    # --- Date range ---
    if history:
        start_date = history[-1].date.strftime('%B %Y')
        end_date = history[0].date.strftime('%B %Y')
        date_range = f"{start_date} – {end_date}"
    else:
        date_range = datetime.today().strftime('%B %Y')

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
        pdf.drawString(180, 780, "AUTO-EXPERT AUTO SUPPLY")
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(180, 760, "TRECE-UNO AUTO SUPPLY")
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
            hours_worked = (log.clock_out - log.clock_in).seconds / 3600 if log.clock_in and log.clock_out else 0
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
            hours_worked = (log.clock_out - log.clock_in).seconds / 3600 if log.clock_in and log.clock_out else 0
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
                           performance_score=performance_score,
                           motivation=motivation,
                           date_range=date_range,
                           months=list(range(1,13)),
                           years=[datetime.today().year, datetime.today().year-1, datetime.today().year-2])


# ------------------ ADMIN DASHBOARD ------------------
@app.route('/dashboard_admin', methods=['GET','POST'])
@login_required
def dashboard_admin():
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    # Profile update
    if request.method == 'POST' and 'first_name' in request.form:
        current_user.first_name = request.form['first_name']
        current_user.last_name = request.form['last_name']
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
    trece_leaves = LeaveRequest.query.join(Employee).filter(Employee.company.in_(["Trece-Uno", "Trece"])).all()
    auto_leaves = LeaveRequest.query.join(Employee).filter(Employee.company=="Auto Expert").all()
    unread_count = Bulletin.query.count()
    total_employees = Employee.query.count()

    today = datetime.today()
    start_cutoff = today - timedelta(days=(today.weekday() + 2) % 7)
    end_cutoff = start_cutoff + timedelta(days=6)

    payroll_total = 0
    company_payroll = {"Trece-Uno": 0, "Auto Expert": 0}
    for emp in Employee.query.all():
        worked_days_count = Attendance.query.filter(
            Attendance.employee_id == emp.id,
            Attendance.clock_out != None,
            Attendance.clock_in >= start_cutoff,
            Attendance.clock_in <= end_cutoff
        ).count()
        loan_balance = float(emp.loan_balance or 0)
        deduction = 500.0 if loan_balance >= 500 else loan_balance
        basic_pay = (emp.daily_rate or 0) * worked_days_count
        gross_income = basic_pay + (emp.allowance or 0) + (emp.incentives or 0)
        deductions = 193.75 + 96.88 + 50.00 + deduction
        net_pay = gross_income - deductions

        payroll_total += net_pay
        payroll_company = "Trece-Uno" if emp.company in {"Trece", "Trece-Uno"} else emp.company
        if payroll_company in company_payroll:
            company_payroll[payroll_company] += net_pay

    pending_ot = Attendance.query.filter_by(ot_status="Pending").count()
    pending_leaves = LeaveRequest.query.filter_by(status="Pending").count()
    pending_loans = Loan.query.filter_by(status="Pending").count()
    pending_leaves_list = LeaveRequest.query.filter_by(status="Pending").order_by(LeaveRequest.date_filed.desc()).all()
    pending_loans_list = Loan.query.filter_by(status="Pending").order_by(Loan.date_filed.desc()).all()
    bulletins = Bulletin.query.order_by(Bulletin.created_at.desc()).all()

    from sqlalchemy import func
    trend_records = (
        db.session.query(
            Payroll.cutoff_start,
            Payroll.cutoff_end,
            func.sum(Payroll.net_pay).label("total_payroll")
        )
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
        payroll_total=payroll_total,
        pending_ot=pending_ot,
        pending_leaves=pending_leaves,
        pending_loans=pending_loans,
        pending_leaves_list=pending_leaves_list,
        pending_loans_list=pending_loans_list,
        bulletins=bulletins,
        company_payroll=company_payroll,
        trend_labels=trend_labels,
        trend_values=trend_values,
        start_cutoff=start_cutoff.date(),
        end_cutoff=end_cutoff.date(),
        admin=current_user
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

    today_log = Attendance.query.filter_by(
        employee_id=current_user.id,
        date=datetime.today().date()
    ).order_by(Attendance.clock_in.desc()).first()
    clocked_in = bool(today_log and today_log.clock_in and not today_log.clock_out)
    clock_in_iso = today_log.clock_in.isoformat() if clocked_in else ""
    worked_hours = 0.0
    if today_log:
        if today_log.clock_out:
            worked_hours = float(today_log.hours or 0)
        elif today_log.clock_in:
            worked_hours = max(
                (datetime.now() - today_log.clock_in).total_seconds() / 3600,
                0.0
            )
    insights = generate_ai_insights(current_user)
    month_start = datetime.today().date().replace(day=1)
    peer_evaluation_pending = not Evaluation.query.filter(
        Evaluation.evaluator_id == current_user.id,
        Evaluation.date >= datetime.combine(month_start, datetime.min.time()),
        Evaluation.category.like("peer_%")
    ).first()

    # ✅ Render staff dashboard template
    return render_template("dashboard_staff.html",
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
        peer_evaluation_pending=peer_evaluation_pending
    )


@app.route('/incident_report', methods=['GET', 'POST'])
@login_required
def submit_incident():
    if "staff" not in current_user.role.lower():
        flash("❌ Incident reports are for staff submissions.", "danger")
        return redirect(url_for('dashboard_admin'))

    coworkers = Employee.query.filter(
        Employee.company == current_user.company,
        Employee.id != current_user.id,
        Employee.role.ilike('%staff%')
    ).order_by(Employee.first_name, Employee.last_name).all()

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
    report = IncidentReport.query.get_or_404(report_id)
    action = request.form.get('action')
    admin_signature = request.form.get('admin_signature', '').strip()
    if action not in {'approve', 'reject'} or not admin_signature:
        flash("❌ Review action and admin signature are required.", "danger")
        return redirect(url_for('dashboard_admin'))

    report.status = action + 'd'
    report.reviewed_by = current_user.id
    report.admin_signature = admin_signature
    if action == 'approve':
        employee = Employee.query.get(report.employee_id)
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
    pdf.drawString(72, 750, f'Incident Reports for {period_label}')
    pdf.setFont('Helvetica', 11)
    pdf.drawString(72, 730, f'Employee: {emp.first_name} {emp.last_name}')
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
    pdf.drawString(72, 750, "AI Insights Report")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, 730, f"Employee: {emp.first_name} {emp.last_name}")
    pdf.drawString(72, 712, f"Cut-off: {cutoff_start.strftime('%m-%d-%Y')} ~ {cutoff_end.strftime('%m-%d-%Y')}")

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
    pdf.drawString(72, 50, f"Generated on {datetime.now().strftime('%m-%d-%Y %H:%M')}")
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
    employees = Employee.query.filter(Employee.id != current_user.id).all()
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
def apply_overtime_details(attendance):
    if not attendance.clock_in or not attendance.clock_out:
        return

    employee = attendance.employee or db.session.get(Employee, attendance.employee_id)
    is_trece_sunday = (
        attendance.date.weekday() == 6
        and employee
        and str(employee.company or '').lower().startswith('trece')
    )
    overtime_start = datetime.combine(
        attendance.date,
        time(12, 0) if is_trece_sunday else time(17, 0)
    )
    attendance.overtime_hours = round(
        max((attendance.clock_out - overtime_start).total_seconds() / 3600, 0),
        2
    )
    application = OTApplication.query.filter_by(
        employee_id=attendance.employee_id,
        ot_date=attendance.date,
        status="Approved"
    ).first()
    holiday = Holiday.query.filter_by(date=attendance.date).first()
    attendance.is_restday_ot = bool(application and attendance.date.weekday() == 6 and not is_trece_sunday)
    attendance.is_holiday_ot = bool(application and holiday)
    attendance.is_weekday_ot = bool(
        application
        and attendance.overtime_hours > 0
        and not attendance.is_restday_ot
        and not attendance.is_holiday_ot
    )
    attendance.ot_status = "Approved" if application and attendance.overtime_hours > 0 else None


def holiday_multiplier(attendance):
    holiday = Holiday.query.filter_by(date=attendance.date).first()
    if holiday and holiday.holiday_type == 'Regular Holiday':
        if attendance.is_restday_ot:
            return 3.38
        return 2.60
    if holiday and attendance.is_restday_ot:
        return 1.95
    if holiday:
        return 1.69
    if attendance.is_restday_ot:
        return 1.69
    return 1.25


def regular_day_pay(attendance, daily_rate):
    holiday = Holiday.query.filter_by(date=attendance.date).first()
    employee = attendance.employee or db.session.get(Employee, attendance.employee_id)
    is_trece_sunday = (
        attendance.date.weekday() == 6
        and employee
        and str(employee.company or '').lower().startswith('trece')
    )
    is_restday = attendance.date.weekday() == 6 and not is_trece_sunday
    if holiday and holiday.holiday_type == 'Regular Holiday':
        if is_restday:
            return daily_rate * 2.6
        return daily_rate * 2.0
    if holiday:
        if is_restday:
            return daily_rate * 1.5
        return daily_rate * 1.3
    if attendance.date.weekday() == 6 and not is_trece_sunday:
        return 0.0
    if is_trece_sunday:
        return daily_rate * 0.5
    return daily_rate


def loan_cutoff_deduction(loan_balance, installment=500.0):
    """Return the installment due for one cutoff, capped by the balance."""
    return min(max(float(loan_balance or 0), 0), installment)
def payroll_company_name(employee):
    if str(employee.company or '').lower().startswith('trece'):
        return 'TRECE-UNO AUTO SUPPLY'
    return 'AUTO-EXPERT AUTO SUPPLY'


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
            flash('OT end time must be after start time and a reason is required.', 'danger')
            return redirect(url_for('apply_ot'))
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
    application.status = 'Approved' if action == 'approve' else 'Rejected'
    application.decision_note = request.form.get('decision_note', '').strip() or None
    application.decided_at = datetime.now()
    if action == 'approve':
        attendance = Attendance.query.filter_by(
            employee_id=application.employee_id, date=application.ot_date
        ).filter(Attendance.clock_out != None).first()
        if attendance:
            apply_overtime_details(attendance)
    db.session.commit()
    return redirect(url_for('holiday_ot_dashboard'))


@app.route('/attendance_action/<int:employee_id>', methods=['POST'])
@login_required
def attendance_action(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    event_now = parse_event_time(request.form.get('event_time'))

    if current_user.id != employee_id and "admin" not in current_user.role.lower():
        flash("❌ Access denied.", "danger")
        return redirect(url_for('dashboard_staff'))

    if "clockin" in request.form:
        open_log = Attendance.query.filter_by(
            employee_id=employee_id,
            date=event_now.date()
        ).filter(Attendance.clock_out == None).first()
        if open_log:
            flash("⚠️ You are already clocked in.", "warning")
            return redirect(url_for('dashboard_staff'))

        # --- Clock In Logic ---
        log = Attendance(date=event_now.date(),
                         clock_in=event_now,
                         status=attendance_status(event_now),
                         employee_id=employee_id)
        db.session.add(log)
        db.session.commit()
        flash("🟢 Clocked in successfully!", "success")

    elif "clockout" in request.form:
        # --- Clock Out Logic ---
        log = Attendance.query.filter_by(
            employee_id=employee_id,
            date=event_now.date()
        ).filter(Attendance.clock_out == None).order_by(Attendance.clock_in.desc()).first()
        if log:
            log.clock_out = event_now
            log.hours = round((log.clock_out - log.clock_in).total_seconds() / 3600, 2)
            apply_overtime_details(log)
            db.session.commit()
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

        if "clockin" in request.form:
            open_log = Attendance.query.filter_by(
                employee_id=employee_id,
                date=event_now.date()
            ).filter(Attendance.clock_out == None).first()
            if open_log:
                return jsonify(status="warning", message="⚠️ You are already clocked in.")

            log = Attendance(date=event_now.date(),
                             clock_in=event_now,
                             status=attendance_status(event_now),
                             employee_id=employee_id)
            db.session.add(log)
            db.session.commit()
            worked_hours = max(
                (datetime.now() - log.clock_in).total_seconds() / 3600,
                0.0
            )
            return jsonify(status="success", message="🟢 Clocked in successfully!",
                           clocked_in=True, clock_in=log.clock_in.isoformat(),
                           worked_hours=worked_hours)

        elif "clockout" in request.form:
            log = Attendance.query.filter_by(
                employee_id=employee_id,
                date=event_now.date()
            ).filter(Attendance.clock_out == None).order_by(Attendance.clock_in.desc()).first()
            if log:
                log.clock_out = event_now
                log.hours = round((log.clock_out - log.clock_in).total_seconds() / 3600, 2)
                apply_overtime_details(log)
                db.session.commit()
                return jsonify(status="success", message="🔴 Clocked out successfully!",
                               clocked_in=False, clock_in="", worked_hours=log.hours)
            else:
                return jsonify(status="warning", message="⚠️ No active clock-in found.")

        return jsonify(status="error", message="Invalid request")
    except Exception:
        logger.exception("Error in attendance_api")
        return jsonify(status="error", message="Internal server error"), 500


# ------------------ PAYROLL + PAYSLIP ------------------
from flask import send_file
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from sqlalchemy import extract
import os, io


def build_payslip_breakdown(emp, payroll_record, worked_days_count=0, overtime_pay=0.0):
    """Return a payslip payload matching the sample payroll format."""
    gross_income = float(payroll_record.gross_income or 0)
    late_ut = 0.0
    sss = float(payroll_record.sss or 0)
    philhealth = float(payroll_record.philhealth or 0)
    pagibig = float(payroll_record.pagibig or 0)
    sss_loan = float(payroll_record.loan or 0)
    hdmf_loan = 0.0
    cash_advance = float(payroll_record.cash_advance or 0)
    withholding_tax = 0.0
    adjustment = 0.0
    night_differential = 0.0
    regular_overtime = float(overtime_pay or 0)
    rest_day = 0.0
    special_holiday = 0.0
    special_holiday_ot = 0.0
    regular_holiday = 0.0
    regular_holiday_ot = 0.0

    total_deductions = late_ut + sss + philhealth + pagibig + sss_loan + hdmf_loan + cash_advance + withholding_tax
    net_pay = gross_income - total_deductions

    return {
        "employee": emp,
        "actual_worked_days": worked_days_count,
        "adjustment": adjustment,
        "night_differential": night_differential,
        "regular_overtime": regular_overtime,
        "rest_day": rest_day,
        "special_holiday": special_holiday,
        "special_holiday_ot": special_holiday_ot,
        "regular_holiday": regular_holiday,
        "regular_holiday_ot": regular_holiday_ot,
        "late_ut": late_ut,
        "sss": sss,
        "philhealth": philhealth,
        "pagibig": pagibig,
        "sss_loan": sss_loan,
        "hdmf_loan": hdmf_loan,
        "cash_advance": cash_advance,
        "withholding_tax": withholding_tax,
        "gross_income": gross_income,
        "total_deductions": total_deductions,
        "net_pay": net_pay,
    }


@app.route('/payroll/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def payroll(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    history = Payroll.query.filter_by(employee_id=employee_id).order_by(
        Payroll.cutoff_start.desc()
    ).all()

    # STAFF VIEW
    if current_user.role.lower() == 'staff':
        if current_user.id != employee_id:
            flash("❌ Access denied.", "danger")
            return redirect(url_for('dashboard_staff'))

        history = Payroll.query.filter_by(employee_id=current_user.id)\
                               .order_by(Payroll.cutoff_start.desc()).all()

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
                               selected_year=selected_year)

    # ADMIN VIEW
    today = datetime.today().date()
    start_cutoff = datetime.combine(
        today - timedelta(days=(today.weekday() + 2) % 7),
        time.min
    )
    end_cutoff = start_cutoff + timedelta(days=7)

    if request.method == 'POST':
        # Update payroll fields
        emp.daily_rate = float(request.form.get('daily_rate', emp.daily_rate))
        emp.allowance = float(request.form.get('allowance', emp.allowance))
        emp.incentives = float(request.form.get('incentives', emp.incentives))

        # Allow admin to set/update existing loan balance
        loan_input = request.form.get('loan_balance')
        if loan_input is not None and loan_input.strip() != "":
            emp.loan_balance = float(loan_input)

        sil_eligible_input = request.form.get('sil_eligible')
        if sil_eligible_input is not None:
            emp.sil_eligible = sil_eligible_input == '1'

        db.session.commit()
        flash("✅ Payroll updated successfully!", "success")
        return redirect(url_for('payroll', employee_id=employee_id))

    # Compute payroll
    paid_attendance = Attendance.query.filter(
        Attendance.employee_id == employee_id,
        Attendance.clock_out != None,
        Attendance.clock_in >= start_cutoff,
        Attendance.clock_in <= end_cutoff
    ).all()
    worked_days_count = len(paid_attendance)

    worked_days_in_month = Attendance.query.filter(
        Attendance.employee_id == employee_id,
        Attendance.clock_out != None,
        extract('year', Attendance.clock_in) == today.year,
        extract('month', Attendance.clock_in) == today.month
    ).count()

    approved_overtime_hours = db.session.query(
        db.func.coalesce(db.func.sum(Attendance.overtime_hours), 0)
    ).filter(
        Attendance.employee_id == employee_id,
        Attendance.clock_out != None,
        Attendance.ot_status == 'Approved',
        Attendance.clock_in >= start_cutoff,
        Attendance.clock_in <= end_cutoff
    ).scalar()

    daily_rate = emp.daily_rate or 0
    basic_pay = sum(regular_day_pay(attendance, daily_rate) for attendance in paid_attendance)
    approved_overtime_pay = sum(
        (daily_rate / 8) * holiday_multiplier(attendance) * float(attendance.overtime_hours or 0)
        for attendance in paid_attendance
        if attendance.ot_status == 'Approved'
    )

    monthly_salary = daily_rate * worked_days_in_month
    deductions = compute_weekly_deductions(monthly_salary, weeks=4)
    sss = deductions["sss"]
    philhealth = deductions["philhealth"]
    pagibig = deductions["pagibig"]

    finalize = request.args.get("finalize") == "true"
    payroll_record = Payroll.query.filter_by(
        employee_id=emp.id,
        cutoff_start=start_cutoff.date(),
        cutoff_end=(end_cutoff - timedelta(days=1)).date()
    ).first()
    loan = (
        float(payroll_record.loan or 0)
        if payroll_record is not None
        else loan_cutoff_deduction(emp.loan_balance)
    )

    gross_income = basic_pay + (emp.allowance or 0) + (emp.incentives or 0) + approved_overtime_pay
    total_deductions = sss + philhealth + pagibig + loan
    net_pay = gross_income - total_deductions

    if payroll_record is None:
        payroll_record = Payroll(
            employee_id=emp.id,
            cutoff_start=start_cutoff.date(),
            cutoff_end=(end_cutoff - timedelta(days=1)).date()
        )
    payroll_record.gross_income = gross_income
    payroll_record.total_deductions = total_deductions
    payroll_record.net_pay = net_pay
    payroll_record.sss = sss
    payroll_record.philhealth = philhealth
    payroll_record.pagibig = pagibig
    payroll_record.loan = loan
    payroll_record.cash_advance = 0.0
    if finalize and payroll_record.id is None:
        db.session.add(payroll_record)
        emp.loan_balance = max(float(emp.loan_balance or 0) - loan, 0)
        db.session.commit()

    payslip = build_payslip_breakdown(emp, payroll_record, worked_days_count, approved_overtime_pay)

    # 👉 Generate payslip PDF in memory
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)

    company_display = "TRECE-UNO AUTO SUPPLY" if emp.company == "Trece-Uno" else "AUTO-EXPERT AUTO SUPPLY"
    company_brand = "TRECE-UNO" if emp.company == "Trece-Uno" else "AUTO-EXPERT"

    # Header
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 780, company_display)
    c.setFont("Helvetica", 12)
    c.drawString(50, 760, "Official Payslip")

    c.setFont("Helvetica", 10)
    c.drawString(50, 730, f"Employee ID: {emp.id}")
    c.drawString(50, 715, f"Employee Name: {emp.first_name} {emp.last_name}")
    c.drawString(50, 700, f"Position: {emp.role}")
    c.drawString(300, 730, f"Date Hired: {emp.date_started}")
    c.drawString(300, 715, f"Department: {emp.company}")
    c.drawString(300, 700, f"Daily Rate: {float(emp.daily_rate or 0):.2f}")
    c.drawString(300, 685, f"Cut-off: {payroll_record.cutoff_start} to {payroll_record.cutoff_end}")

    data = [
        ["Earnings", "", "", "Deductions", "", ""],
        ["Description", "Days/Hrs", "Amount", "Description", "Mins", "Amount"],
        ["Actual Worked Days", str(payslip['actual_worked_days']), f"{payslip['gross_income']:.2f}", "Late/UT", "", f"{payslip['late_ut']:.2f}"],
        ["Adjustment", "", f"{payslip['adjustment']:.2f}", "SSS", "", f"{payslip['sss']:.2f}"],
        ["Night Differential", "", f"{payslip['night_differential']:.2f}", "PhilHealth", "", f"{payslip['philhealth']:.2f}"],
        ["Regular Overtime", "", f"{payslip['regular_overtime']:.2f}", "HDMF", "", f"{payslip['pagibig']:.2f}"],
        ["Rest Day", "", f"{payslip['rest_day']:.2f}", "SSS Loan", "", f"{payslip['sss_loan']:.2f}"],
        ["Special Holiday", "", f"{payslip['special_holiday']:.2f}", "HDMF Loan", "", f"{payslip['hdmf_loan']:.2f}"],
        ["Special Holiday OT", "", f"{payslip['special_holiday_ot']:.2f}", "Cash Advance", "", f"{payslip['cash_advance']:.2f}"],
        ["Regular Holiday", "", f"{payslip['regular_holiday']:.2f}", "Withholding Tax", "", f"{payslip['withholding_tax']:.2f}"],
        ["Regular Holiday OT", "", f"{payslip['regular_holiday_ot']:.2f}", "Gross Deductions", "", f"{payslip['total_deductions']:.2f}"],
        ["Gross Income", "", f"{payslip['gross_income']:.2f}", "NET PAY", "", f"{payslip['net_pay']:.2f}"],
    ]

    table = Table(data, colWidths=[120, 60, 80, 120, 60, 80])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,1), colors.whitesmoke),
        ('FONTNAME', (0,0), (-1,1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.75, colors.black),
        ('ALIGN', (1,1), (-1,-1), 'CENTER'),
        ('ALIGN', (2,2), (2,-1), 'RIGHT'),
        ('ALIGN', (5,2), (5,-1), 'RIGHT'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 6),
    ]))
    table.wrapOn(c, 50, 600)
    table.drawOn(c, 50, 420)

    c.line(50, 400, 550, 400)
    c.setFont("Helvetica-Oblique", 10)
    c.drawString(50, 385, "Authorized by Admin")
    c.line(50, 340, 250, 340)
    c.line(350, 340, 550, 340)
    c.setFont("Helvetica", 10)
    c.drawString(50, 325, "Authorized Person Signature")
    c.drawString(350, 325, "Date")

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
         selected_year=None,
         years=[])


@app.route('/payroll/<int:employee_id>/monthly')
@login_required
def monthly_payroll(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    if current_user.id != employee_id and 'admin' not in current_user.role.lower():
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
        'deductions': sum(float(record.total_deductions or 0) for record in records),
        'net_pay': sum(float(record.net_pay or 0) for record in records),
        'loan': sum(float(record.loan or 0) for record in records),
    }

    if request.args.get('download') == 'true':
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        pdf.setFont('Helvetica-Bold', 16)
        pdf.drawString(50, 780, 'MONTHLY PAYSLIP SUMMARY')
        pdf.setFont('Helvetica', 10)
        pdf.drawString(50, 750, f'Employee: {emp.first_name} {emp.last_name} (ID: {emp.id})')
        pdf.drawString(50, 735, f'Month: {month_start.strftime("%B %Y")}')
        y = 690
        for label, key in [('Gross Income', 'gross_income'), ('Total Deductions', 'deductions'), ('Loan Deductions', 'loan'), ('NET PAY', 'net_pay')]:
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


@app.route('/payroll/<int:employee_id>/finalize', methods=['POST'])
@login_required
def finalize_payroll(employee_id):
    if 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    Employee.query.get_or_404(employee_id)
    return redirect(url_for('payroll', employee_id=employee_id, finalize='true'))


@app.route('/payslip/<int:emp_id>/<int:payroll_id>')
@login_required
def payslip(emp_id, payroll_id):
    employee = Employee.query.get_or_404(emp_id)
    payroll_record = Payroll.query.filter_by(
        id=payroll_id,
        employee_id=emp_id
    ).first_or_404()

    if current_user.id != emp_id and 'admin' not in current_user.role.lower():
        flash("Access denied.", "danger")
        return redirect(url_for('dashboard_staff'))

    allowance = float(employee.allowance or 0)
    incentives = float(employee.incentives or 0)
    gross_income = float(payroll_record.gross_income or 0)
    basic_pay = max(gross_income - allowance - incentives, 0)
    ot_pay = 0.0
    tax = 0.0

    return render_template(
        "payslip.html",
        employee=employee,
        payroll=payroll_record,
        basic_pay=basic_pay,
        ot_pay=ot_pay,
        tax=tax
    )


@app.route('/payslip/<int:emp_id>/<int:payroll_id>/download')
@login_required
def download_payslip(emp_id, payroll_id):
    employee = Employee.query.get_or_404(emp_id)
    payroll_record = Payroll.query.filter_by(id=payroll_id, employee_id=emp_id).first_or_404()
    if current_user.id != emp_id and 'admin' not in current_user.role.lower():
        return 'Access denied', 403

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont('Helvetica-Bold', 16)
    pdf.drawString(50, 780, payroll_company_name(employee))
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawString(50, 760, 'PAYSLIP')
    pdf.setFont('Helvetica', 10)
    pdf.drawString(50, 730, f'Employee: {employee.first_name} {employee.last_name} (ID: {employee.id})')
    pdf.drawString(50, 715, f'Cutoff: {payroll_record.cutoff_start} to {payroll_record.cutoff_end}')
    pdf.drawString(50, 700, f'Daily Rate: PHP {float(employee.daily_rate or 0):,.2f}')
    y = 675
    for label, amount in [
        ('Gross Income', payroll_record.gross_income),
        ('SSS', payroll_record.sss),
        ('PhilHealth', payroll_record.philhealth),
        ('Pag-IBIG', payroll_record.pagibig),
        ('Loan Deduction', payroll_record.loan),
        ('Cash Advance', payroll_record.cash_advance),
        ('Total Deductions', payroll_record.total_deductions),
        ('NET PAY', payroll_record.net_pay),
    ]:
        pdf.setFont('Helvetica-Bold' if label == 'NET PAY' else 'Helvetica', 12)
        pdf.drawString(70, y, label)
        pdf.drawRightString(500, y, f'PHP {float(amount or 0):,.2f}')
        y -= 26
    pdf.line(70, y - 20, 260, y - 20)
    pdf.line(330, y - 20, 500, y - 20)
    pdf.setFont('Helvetica', 10)
    pdf.drawString(70, y - 35, 'Authorized Person Signature')
    pdf.drawString(330, y - 35, 'Date')
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f'Payslip_{employee.first_name}_{employee.last_name}_{payroll_record.cutoff_start}.pdf', mimetype='application/pdf')


# ------------------ PAYROLL DASHBOARD------------------
@app.route('/payroll_dashboard', methods=['GET', 'POST'])
@login_required
def payroll_dashboard():
    today = datetime.today().date()
    start_cutoff = datetime.combine(
        today - timedelta(days=(today.weekday() + 2) % 7),
        time.min
    )
    end_cutoff = start_cutoff + timedelta(days=7)

    if request.method == 'POST':
        for emp in Employee.query.all():
            daily_rate = request.form.get(f'daily_rate_{emp.id}')
            allowance = request.form.get(f'allowance_{emp.id}')
            incentives = request.form.get(f'incentives_{emp.id}')
            loan_balance = request.form.get(f'loan_{emp.id}')
            try:
                if daily_rate is not None:
                    emp.daily_rate = max(float(daily_rate), 0)
                if allowance is not None:
                    emp.allowance = max(float(allowance), 0)
                if incentives is not None:
                    emp.incentives = max(float(incentives), 0)
                if loan_balance is not None:
                    emp.loan_balance = max(float(loan_balance), 0)
            except (TypeError, ValueError):
                db.session.rollback()
                flash('Please enter valid non-negative payroll values.', 'danger')
                return redirect(url_for('payroll_dashboard'))
        db.session.commit()
        flash('Payroll rates and allowances updated successfully.', 'success')
        return redirect(url_for('payroll_dashboard'))

    payroll_data = []
    employees = Employee.query.all()

    for emp in employees:
        paid_attendance = Attendance.query.filter(
            Attendance.employee_id == emp.id,
            Attendance.clock_out != None,
            Attendance.clock_in >= start_cutoff,
            Attendance.clock_in <= end_cutoff
        ).all()
        worked_days_count = len(paid_attendance)

        daily_rate = float(emp.daily_rate or 0)
        basic_pay = sum(regular_day_pay(attendance, daily_rate) for attendance in paid_attendance)
        ot_records = [
            attendance for attendance in Attendance.query.filter(
                Attendance.employee_id == emp.id,
                Attendance.clock_out != None,
                Attendance.clock_in >= start_cutoff,
                Attendance.clock_in <= end_cutoff
            ).all()
            if (
                attendance.is_weekday_ot or attendance.is_restday_ot or attendance.is_holiday_ot
                or (
                    attendance.clock_out
                    and (
                        attendance.clock_out.hour > 17
                        or (attendance.clock_out.hour == 17 and attendance.clock_out.minute > 0)
                    )
                )
            )
        ]
        ot_hours = sum(
            float(attendance.overtime_hours or 0)
            if attendance.overtime_hours
            else max((attendance.clock_out.hour - 17) + attendance.clock_out.minute / 60, 0)
            for attendance in ot_records
        )
        approved_ot_hours = sum(
            hours for attendance, hours in [
                (
                    attendance,
                    float(attendance.overtime_hours or 0)
                    if attendance.overtime_hours
                    else max((attendance.clock_out.hour - 17) + attendance.clock_out.minute / 60, 0)
                )
                for attendance in ot_records
            ]
            if attendance.ot_status == 'Approved'
        )
        approved_ot_pay = sum(
            (daily_rate / 8) * holiday_multiplier(attendance) * float(attendance.overtime_hours or 0)
            for attendance in ot_records
            if attendance.ot_status == 'Approved'
        )
        gross_income = basic_pay + float(emp.allowance or 0) + float(emp.incentives or 0) + approved_ot_pay

        deduction_values = compute_weekly_deductions(daily_rate * worked_days_count, weeks=1)
        sss = deduction_values['sss']
        philhealth = deduction_values['philhealth']
        pagibig = deduction_values['pagibig']
        loan = loan_cutoff_deduction(emp.loan_balance)

        deductions = sss + philhealth + pagibig + loan
        net_pay = gross_income - deductions

        payroll_data.append({
            "emp": emp,
            "worked_days": worked_days_count,
            "ot_hours": ot_hours,
            "approved_ot_hours": approved_ot_hours,
            "approved_ot_pay": approved_ot_pay,
            "ot_statuses": sorted({attendance.ot_status or 'Pending' for attendance in ot_records}),
            "loan_deduction": loan,
            "gross_income": gross_income,
            "deductions": deductions,
            "net_pay": net_pay
        })

    return render_template("payroll_dashboard.html",
                           payroll_data=payroll_data,
                           start_cutoff=start_cutoff.date(),
                           end_cutoff=(end_cutoff - timedelta(days=1)).date())

# ------------------ HOLIDAY + OVERTIME (Unified with Approvals + Beyond 5PM) ------------------
@app.route('/holiday_overtime', methods=['GET','POST'])
@login_required
def holiday_ot_dashboard():
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    # --- Handle Approve/Reject actions ---
    if request.method == 'POST':
        att_id = request.form.get("att_id")
        action = request.form.get("action")
        if att_id and action:
            att = Attendance.query.get_or_404(att_id)
            if action == "approve":
                att.ot_status = "Approved"
                flash(f"✅ Overtime #{att.id} approved.", "success")
            elif action == "reject":
                att.ot_status = "Rejected"
                flash(f"❌ Overtime #{att.id} rejected.", "danger")
            db.session.commit()
            return redirect(url_for('holiday_ot_dashboard'))

    # --- Query lahat ng attendance na may OT OR lumabas beyond 5 PM ---
    query = Attendance.query.filter(Attendance.clock_out != None)
    records = [
        attendance for attendance in query.order_by(Attendance.date.desc()).all()
        if (
            attendance.is_holiday_ot or attendance.is_weekday_ot or attendance.is_restday_ot
            or (
                attendance.clock_out.hour > 17
                or (attendance.clock_out.hour == 17 and attendance.clock_out.minute > 0)
            )
        )
    ]

    applications = OTApplication.query.order_by(OTApplication.ot_date.desc()).all()

    # --- Optional filter by status ---
    filter_status = request.args.get("status")
    if filter_status:
        records = [attendance for attendance in records if (attendance.ot_status or 'Pending') == filter_status]

    # --- Holidays dictionary ---
    holidays = {h.date: h.description for h in Holiday.query.all()}

    # --- Export CSV ---
    export_type = request.args.get("export")
    if export_type == "csv":
        def generate():
            data = [['Attendance ID','Employee','Date','Holiday Name','Status','Clock Out','OT Hours','OT Type','OT Status']]
            for att in records:
                ot_type = "Weekday" if att.is_weekday_ot else "Rest Day" if att.is_restday_ot else "Holiday" if att.is_holiday_ot else "Beyond 5PM"
                # auto compute OT hours if beyond 5PM
                if att.clock_out and (
                    att.clock_out.hour > 17
                    or (att.clock_out.hour == 17 and att.clock_out.minute > 0)
                ):
                    ot_hours = (att.clock_out.hour - 17) + (att.clock_out.minute/60)
                else:
                    ot_hours = att.overtime_hours or 0
                row = [
                    att.id,
                    f"{att.employee.first_name} {att.employee.last_name}",
                    att.date,
                    holidays.get(att.date, ""),
                    att.status,
                    att.clock_out,
                    ot_hours,
                    ot_type,
                    getattr(att, "ot_status", "Pending")
                ]
                data.append(row)
            return '\n'.join([','.join(map(str, row)) for row in data])

        return Response(generate(), mimetype="text/csv",
                        headers={"Content-Disposition":"attachment;filename=holiday_overtime.csv"})

    return render_template("holiday_ot_dashboard.html",
                           records=records,
                           applications=applications,
                           holidays=holidays,
                           filter_status=filter_status,
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
@app.route('/approve_loan/<int:id>', methods=['GET','POST'])
@login_required
def approve_loan(id):
    if current_user.role.lower() != 'admin':
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('loan'))
    loan = Loan.query.get_or_404(id)
    loan.status = "Approved"
    loan.decision_date = datetime.utcnow()
    loan.approver = f"{current_user.first_name} {current_user.last_name}"
    db.session.commit()
    send_loan_email(Employee.query.get_or_404(loan.employee_id), loan, 'approved')
    flash("✅ Loan approved.", "success")
    return redirect(url_for('dashboard_admin'))


@app.route('/reject_loan/<int:id>', methods=['POST'])
@login_required
def reject_loan(id):
    if current_user.role.lower() != 'admin':
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('loan'))
    reason = request.form.get('reason')
    loan = Loan.query.get_or_404(id)
    loan.status = "Rejected"
    loan.remarks = reason
    loan.decision_date = datetime.utcnow()
    loan.approver = f"{current_user.first_name} {current_user.last_name}"
    db.session.commit()
    send_loan_email(Employee.query.get_or_404(loan.employee_id), loan, 'rejected')
    flash("❌ Loan request rejected.", "danger")
    return redirect(url_for('dashboard_admin'))


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
        loan = Loan.query.get_or_404(int(loan_id))
        loan.status = "Approved" if action == "approve" else "Rejected"
        loan.decision_date = datetime.utcnow()
        loan.approver = f"{current_user.first_name} {current_user.last_name}"
        db.session.commit()
        send_loan_email(
            Employee.query.get_or_404(loan.employee_id),
            loan,
            'approved' if action == 'approve' else 'rejected'
        )
        flash(f"Loan {action}d successfully!", "success")
        return redirect(url_for('loan'))

    # --- PDF-only print view ---
    if action == "print":
        loans = Loan.query.filter_by(employee_id=current_user.id).all()
        approved_loans = [l for l in loans if l.status == "Approved"] or loans
        return render_template(
            "loan.html",
            loans=approved_loans,
            approved=Loan.query.filter_by(employee_id=current_user.id, status="Approved").count(),
            pending=Loan.query.filter_by(employee_id=current_user.id, status="Pending").count(),
            rejected=Loan.query.filter_by(employee_id=current_user.id, status="Rejected").count(),
            print_mode=True,
            current_user=current_user,
            now=datetime.now()
        )

    # --- Loan Application ---
    today = datetime.today()
    year_start = datetime(today.year, 1, 1)
    attendance_days = Attendance.query.filter(
        Attendance.employee_id==current_user.id,
        Attendance.clock_out!=None,
        Attendance.clock_in>=year_start
    ).count()

    emp = Employee.query.get(current_user.id)
    base_limit = 3000
    accumulated = (emp.daily_rate or 0) * attendance_days / 12
    loan_limit = base_limit + accumulated

    active_loans = Loan.query.filter_by(employee_id=current_user.id, status="Approved").all()
    balance = sum(l.amount for l in active_loans)
    remaining_limit = max(0, loan_limit - balance)

    if request.method == 'POST':
        amount = float(request.form.get('amount'))
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

    approved = Loan.query.filter_by(status="Approved").count()
    pending = Loan.query.filter_by(status="Pending").count()
    rejected = Loan.query.filter_by(status="Rejected").count()

    loan_trend = LoanHistory.query.order_by(LoanHistory.cutoff_start.desc()).limit(6).all()
    loan_labels = [f"{r.cutoff_start.strftime('%b %d')} - {r.cutoff_end.strftime('%b %d')}" for r in loan_trend][::-1]
    loan_values = [r.total_amount - r.deductions_applied for r in loan_trend][::-1]

    return render_template("loan.html",
                           loans=loans,
                           approved=approved,
                           pending=pending,
                           rejected=rejected,
                           remaining_limit=remaining_limit,
                           loan_labels=loan_labels,
                           loan_values=loan_values,
                           now=datetime.now(),
                           print_mode=False)


# ------------------ EVALUATION DASHBOARD------------------
@app.route('/evaluation_dashboard', methods=['GET','POST'])
@login_required
def evaluation_dashboard():
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    ensure_peer_questions()
    employees = Employee.query.all()
    questions = EvaluationQuestion.query.filter_by(is_active=True).all()
    month_start = datetime.today().date().replace(day=1)
    month_start_dt = datetime.combine(month_start, datetime.min.time())
    peer_status = []
    for emp in employees:
        submitted = Evaluation.query.filter(
            Evaluation.employee_id == emp.id,
            Evaluation.date >= month_start_dt,
            Evaluation.category.like("peer_%")
        ).first() is not None
        peer_status.append({"employee": emp, "submitted": submitted})

    if request.method == 'POST':
        for emp in employees:
            remarks = request.form.get(f'remarks_{emp.id}')
            for q in questions:
                rating_value = request.form.get(f'q{q.id}_{emp.id}')
                if rating_value:
                    eval = Evaluation(
                        employee_id=emp.id,
                        evaluator_id=current_user.id,
                        rating=int(rating_value),
                        remarks=f"{q.text}: {remarks}" if remarks else q.text,
                        date=datetime.today()
                    )
                    db.session.add(eval)
        db.session.commit()
        flash("✅ Evaluations saved successfully!", "success")
        return redirect(url_for('evaluation_dashboard'))

    evaluations = Evaluation.query.order_by(Evaluation.date.desc()).all()
    return render_template("evaluation_dashboard.html",
                           employees=employees,
                           questions=questions,
                           evaluations=evaluations,
                           peer_status=peer_status,
                           month_start=month_start)


@app.route('/evaluation_results/<string:file_format>', methods=['GET'])
@login_required
def evaluation_results_export(file_format):
    if file_format not in {'csv', 'pdf'}:
        return "Unsupported evaluation export format", 400

    requested_employee_id = request.args.get('employee_id', type=int)
    if "admin" in current_user.role.lower():
        query = Evaluation.query
        employee_label = "All Employees"
        if requested_employee_id:
            employee = Employee.query.get_or_404(requested_employee_id)
            query = query.filter_by(employee_id=employee.id)
            employee_label = employee.full_name()
    else:
        query = Evaluation.query.filter_by(employee_id=current_user.id)
        employee_label = current_user.full_name()

    evaluations = query.order_by(Evaluation.date.desc()).all()

    if file_format == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Date', 'Employee', 'Evaluator', 'Rating', 'Category', 'Remarks'])
        for evaluation in evaluations:
            writer.writerow([
                evaluation.date.strftime('%Y-%m-%d %H:%M') if evaluation.date else '',
                evaluation.employee.full_name() if evaluation.employee else '',
                evaluation.evaluator.full_name() if evaluation.evaluator else 'N/A',
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
    pdf.drawString(50, 780, payroll_company_name(emp))
    pdf.setFont('Helvetica-Bold', 14)
    pdf.drawString(50, 760, 'MONTHLY PAYSLIP SUMMARY')
    pdf.setFont('Helvetica', 10)
    pdf.drawString(50, 730, f'Employee: {emp.first_name} {emp.last_name} (ID: {emp.id})')
    pdf.drawString(50, 715, f'Month: {month_start.strftime("%B %Y")}')
    for evaluation in evaluations:
        date_text = evaluation.date.strftime('%Y-%m-%d') if evaluation.date else 'N/A'
        employee_text = evaluation.employee.full_name() if evaluation.employee else 'N/A'
        evaluator_text = evaluation.evaluator.full_name() if evaluation.evaluator else 'N/A'
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

categories = ["Engine", "Transmission", "Electrical", "Suspension", 
              "Cooling", "Car Brands", "Brakes", "Steering", "Exhaust"]

if scheduler:
    for cat in categories:
        scheduler.add_job(generate_auto_quiz, 'cron', day=1, hour=0, args=[cat])
else:
    logger.warning("Scheduler not available; skipping auto-quiz job registration for categories.")


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
    mode = request.form.get("mode", "auto")
    category = request.form.get("category", "General")

    if request.method == 'POST' and mode == "upload" and 'file' in request.files:
        flash("❌ Questionnaire uploads are available to administrators only.", "danger")
        return redirect(url_for('quiz', employee_id=employee_id))

    # Get current month quizzes by category
    current_month = datetime.utcnow().month
    category_quizzes = Quiz.query.filter(
        db.extract('month', Quiz.date_created) == current_month,
        Quiz.category == category
    ).all()

    if len({question.question.strip().casefold() for question in category_quizzes}) < 10:
        fallback_questions = [
            ("Ano ang pangunahing layunin ng regular vehicle maintenance?", "Maiwasan ang sira", "Dagdagan ang ingay", "Bawasan ang safety", "Tanggalin ang preno", "A"),
            ("Ano ang dapat gawin bago magtrabaho sa engine?", "Patayin at palamigin ito", "Buksan ang lahat ng ilaw", "Tanggalin ang gulong", "Lagyan ng tubig ang fuel", "A"),
            ("Ano ang gamit ng warning light sa dashboard?", "Magbigay ng alerto", "Magpalit ng gulong", "Magdagdag ng gasolina", "Maglinis ng upuan", "A"),
            ("Ano ang mahalaga kapag nag-iinspeksyon ng sasakyan?", "Sundin ang checklist", "Hulaan ang resulta", "Laktawan ang safety", "Itago ang sira", "A"),
            ("Ano ang dapat gamitin sa pagprotekta ng mata?", "Safety goggles", "Open sandals", "Loose cloth", "Paper bag", "A"),
            ("Ano ang unang hakbang kapag may nakitang oil leak?", "I-report at siyasatin", "Balewalain ito", "Dagdagan ang bilis", "Takpan ng papel", "A"),
            ("Bakit kailangang panatilihing malinis ang work area?", "Para maiwasan ang aksidente", "Para bumigat ang tools", "Para madulas ang sahig", "Para mawala ang labels", "A"),
            ("Ano ang tamang asal sa paggamit ng tools?", "Gamitin ayon sa purpose", "Ihagis pagkatapos gamitin", "Gamitin kahit sira", "Itago nang basa", "A"),
            ("Ano ang dapat gawin sa sirang equipment?", "I-tag at i-report", "Gamitin pa rin", "Itago sa daan", "Ibigay sa customer", "A"),
            ("Ano ang dapat suriin bago i-release ang sasakyan?", "Safety at work quality", "Kulay lang", "Busina lang", "Radio lang", "A")
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
            db.extract('month', Quiz.date_created) == current_month,
            Quiz.category == category
        ).all()

    # Auto-generate kung wala pang quiz sa buwan na ito
    if not category_quizzes and mode == "auto":
        generate_auto_quiz(category)
        category_quizzes = Quiz.query.filter(
            db.extract('month', Quiz.date_created) == current_month,
            Quiz.category == category
        ).all()

    selected_ids = [int(value) for value in request.form.get('quiz_ids', '').split(',') if value.isdigit()]
    if request.method == 'POST' and mode == "take" and selected_ids:
        quizzes = Quiz.query.filter(Quiz.id.in_(selected_ids)).all()
        quizzes.sort(key=lambda question: selected_ids.index(question.id))
    else:
        available_quizzes = list({question.question.strip().casefold(): question for question in category_quizzes}.values())
        if len(available_quizzes) < 10:
            available_quizzes = Quiz.query.filter(
                db.extract('month', Quiz.date_created) == current_month
            ).all()
            available_quizzes = list({question.question.strip().casefold(): question for question in available_quizzes}.values())
        quizzes = random.sample(available_quizzes, min(10, len(available_quizzes)))

    # Handle quiz answers
    if request.method == 'POST' and mode == "take":
        score, total_points = 0, 0
        for quiz in quizzes:
            answer = request.form.get(f"quiz_{quiz.id}")
            total_points += quiz.points
            if answer == quiz.correct_answer:
                score += quiz.points

        percentage = (score / total_points * 100) if total_points else 0

        existing = QuizResult.query.filter_by(
            employee_id=employee_id,
            is_official=True
        ).filter(db.extract('month', QuizResult.date_taken) == datetime.utcnow().month).first()

        if existing:
            result = QuizResult(employee_id=employee_id, score=score, total_points=total_points, is_official=False)
            flash("Retake completed. This attempt is for practice only.", "info")
        else:
            result = QuizResult(employee_id=employee_id, score=score, total_points=total_points, is_official=True)
            if percentage >= 75:
                flash("🎉 Congratulations! You passed the quiz!", "success")
                emp.merit_points += 5
            else:
                flash("⚠️ You need to retake the quiz.", "warning")
                emp.demerit_points += 3

        db.session.add(result)
        db.session.commit()
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
        quiz_duration_seconds=60
    )

# ------------------ MERIT / DEMERIT ------------------
@app.route('/merit_demerit/<int:employee_id>')
@login_required
def merit_demerit(employee_id):
    emp = Employee.query.get_or_404(employee_id)

    # Attendance records
    attendance_logs = Attendance.query.filter_by(employee_id=employee_id).order_by(Attendance.date.desc()).all()
    from collections import defaultdict
    monthly_records = defaultdict(list)
    for log in attendance_logs:
        month_key = log.date.strftime("%Y-%m")
        monthly_records[month_key].append(log)

    # Attendance points
    attendance_merit = sum(1 for log in attendance_logs if log.status == "Present")
    attendance_demerit = sum(1 for log in attendance_logs if log.status == "Absent")

    # Evaluation points
    evaluations = Evaluation.query.filter_by(employee_id=employee_id).all()
    eval_merit = sum(5 if e.score >= 90 else 3 if e.score >= 75 else 1 for e in evaluations)
    eval_demerit = sum(2 for e in evaluations if e.score < 60)

    # Quiz points
    quizzes = QuizResult.query.filter_by(employee_id=employee_id).all()
    quiz_merit = sum(3 if q.score >= 90 else 1 if q.score >= 75 else 0 for q in quizzes)
    quiz_demerit = sum(2 for q in quizzes if q.score < 75)

    # Totals
    merit_points = attendance_merit + eval_merit + quiz_merit
    demerit_points = attendance_demerit + eval_demerit + quiz_demerit
    cash_value = merit_points * 10  # 1 point = ₱10

    return render_template("merit_demerit.html",
                           employee=emp,
                           monthly_records=monthly_records,
                           attendance_merit=attendance_merit,
                           attendance_demerit=attendance_demerit,
                           eval_merit=eval_merit,
                           eval_demerit=eval_demerit,
                           quiz_merit=quiz_merit,
                           quiz_demerit=quiz_demerit,
                           merit_points=merit_points,
                           demerit_points=demerit_points,
                           cash_value=cash_value)


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
    c.drawString(200, 750, "Merit Points Redemption Form")
    c.setFont("Helvetica", 12)
    c.drawString(100, 700, f"Employee: {emp.first_name} {emp.last_name}")
    c.drawString(100, 680, f"Role: {emp.role}")
    c.drawString(100, 660, f"Company: {emp.company}")
    c.drawString(100, 640, f"Merit Points Redeemed: {merit_points}")
    c.drawString(100, 620, f"Cash Equivalent: PHP {cash_value:.2f}")
    c.drawString(100, 600, f"Date Redeemed: {datetime.utcnow().strftime('%Y-%m-%d')}")
    c.drawString(100, 580, "Signature: __________________________")
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
    normalized_company = 'Trece' if str(company or '').lower().startswith('trece') else company
    folder = COMPANY_FILE_FOLDERS.get(normalized_company)
    if not folder:
        return None
    directory = os.path.join(app.config['FILES_FOLDER'], folder)
    os.makedirs(directory, exist_ok=True)
    return directory


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
    company = 'Trece' if str(current_user.company or '').lower().startswith('trece') else current_user.company
    directory = company_file_directory(company)
    files = sorted(os.listdir(directory)) if directory and os.path.isdir(directory) else []
    return render_template('company_files.html', files=files, company=company)


@app.route('/download/<string:company>/<string:filename>')
@login_required
def download_file(company, filename):
    normalized_user_company = 'Trece' if str(current_user.company or '').lower().startswith('trece') else current_user.company
    if company != normalized_user_company and 'admin' not in current_user.role.lower():
        return 'Access denied', 403
    directory = company_file_directory(company)
    safe_filename = secure_filename(filename)
    if not directory or not safe_filename or safe_filename != filename:
        return 'File not found', 404

    filepath = os.path.join(directory, safe_filename)
    if not os.path.isfile(filepath):
        return 'File not found', 404
    return send_file(filepath, as_attachment=True, download_name=filename)


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
@app.route('/backup')
@login_required
def backup():
    if "admin" not in current_user.role.lower():
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('dashboard_staff'))

    database_path = os.path.join(basedir, "instance", "hris.db")
    backup_dir = os.path.join(basedir, "instance", "backups")
    os.makedirs(backup_dir, exist_ok=True)
    local_exists = os.path.isfile(database_path)
    backup_status = "failed"
    last_backup = datetime.now().strftime("%B %d, %Y %I:%M %p")
    backup_filename = None

    if local_exists:
        import zipfile
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"hris_full_backup_{timestamp}.zip"
        archive_path = os.path.join(backup_dir, backup_filename)
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(database_path, 'instance/hris.db')
            for relative_root in ('static/uploads', 'static/files'):
                source_root = os.path.join(basedir, relative_root)
                if not os.path.isdir(source_root):
                    continue
                for root, _, filenames in os.walk(source_root):
                    for filename in filenames:
                        source_path = os.path.join(root, filename)
                        archive_name = os.path.relpath(source_path, basedir).replace(os.sep, '/')
                        archive.write(source_path, archive_name)
            archive.writestr(
                'BACKUP_CONTENTS.txt',
                'This archive contains the HRIS database and uploaded profile/company files.\n'
            )
        backup_status = "success"

    return render_template("backup_local.html",
                           local_exists=local_exists,
                           backup_status=backup_status,
                           last_backup=last_backup,
                           backup_filename=backup_filename)


@app.route('/download_db')
@login_required
def download_db():
    if "admin" not in current_user.role.lower():
        return "Access denied", 403

    backup_dir = os.path.join(basedir, "instance", "backups")
    backup_filename = request.args.get("filename", "")
    if not backup_filename or os.path.basename(backup_filename) != backup_filename:
        return "Backup not found", 404

    backup_path = os.path.join(backup_dir, backup_filename)
    if not os.path.isfile(backup_path):
        return "Backup not found", 404
    return send_file(backup_path, as_attachment=True, download_name=backup_filename)

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
