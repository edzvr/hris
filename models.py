from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

# ------------------ EMPLOYEE ------------------
class Employee(db.Model, UserMixin):
    __tablename__ = "employees"   # ✅ important para tugma sa foreign keys

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50))
    middle_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    suffix_name = db.Column(db.String(20))
    dob = db.Column(db.Date, nullable=True)
    role = db.Column(db.String(20))          # Staff/Admin/Employee
    company = db.Column(db.String(50))       # Trece-Uno / Auto Expert
    email = db.Column(db.String(100))        # not unique
    contact_no = db.Column(db.String(20))
    password = db.Column(db.String(200))
    profile_pic = db.Column(db.String(200))
    registered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=True)
    address = db.Column(db.String(200))
    date_started = db.Column(db.Date)
    sss = db.Column(db.String(50))
    philhealth = db.Column(db.String(50))
    tin = db.Column(db.String(50))
    pagibig = db.Column(db.String(50))
    emergency_person = db.Column(db.String(100))
    emergency_contact = db.Column(db.String(100))
    emergency_address = db.Column(db.String(200))
    sil_credits = db.Column(db.Integer, default=5)
    sil_eligible = db.Column(db.Boolean, nullable=False, default=True)
    merit_points = db.Column(db.Integer, default=0)
    demerit_points = db.Column(db.Integer, default=0)

    # Payroll-related fields
    daily_rate = db.Column(db.Float, default=695.0)
    allowance = db.Column(db.Float, default=0.0)
    incentives = db.Column(db.Float, default=0.0)
    loan_balance = db.Column(db.Float, default=0.0)
    sl_credits = db.Column(db.Integer, default=5)

    # Relationships
    attendances = db.relationship("Attendance", back_populates="employee", lazy=True)
    leave_requests = db.relationship("LeaveRequest", backref="employee", lazy=True)
    loans = db.relationship("Loan", backref="employee", lazy=True)
    payrolls = db.relationship("Payroll", backref="employee", lazy=True)
    redemptions = db.relationship("RedemptionHistory", backref="employee", lazy=True)
    
    # Password helpers
    def set_password(self, raw_password):
        self.password = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password, raw_password)

    def full_name(self):
        name_parts = [self.first_name, self.middle_name, self.last_name, self.suffix_name]
        return " ".join(part.strip() for part in name_parts if part and part.strip())


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False, nullable=False)

    employee = db.relationship("Employee", backref="reset_tokens")


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, nullable=True)
    employee_name = db.Column(db.String(120), nullable=False)
    company = db.Column(db.String(50), nullable=True)
    action = db.Column(db.String(120), nullable=False)
    path = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    status_code = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

# ------------------ ATTENDANCE ------------------
class Attendance(db.Model):
    __tablename__ = "attendances"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    clock_in = db.Column(db.DateTime, nullable=True)
    clock_out = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False)
    hours = db.Column(db.Float, nullable=True)
    company = db.Column(db.String(50), nullable=True)

    overtime_hours = db.Column(db.Float, default=0.0)
    is_weekday_ot = db.Column(db.Boolean, default=False)
    is_restday_ot = db.Column(db.Boolean, default=False)
    is_holiday_ot = db.Column(db.Boolean, default=False)
    ot_status = db.Column(db.String(20), default="Pending")

    employee = db.relationship("Employee", back_populates="attendances")

    def __repr__(self):
        return f"<Attendance {self.date} - {self.status}>"


class OTApplication(db.Model):
    __tablename__ = "ot_applications"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    ot_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Pending")
    decision_note = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    decided_at = db.Column(db.DateTime, nullable=True)

    employee = db.relationship("Employee", backref="ot_applications")

# ------------------ HOLIDAY ------------------
class Holiday(db.Model):
    __tablename__ = "holidays"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False)
    description = db.Column(db.String(100), nullable=False)
    holiday_type = db.Column(db.String(40), nullable=False, default="Special Non-Working Holiday")

# ------------------ LEAVE ------------------
class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    leave_type = db.Column(db.String(50), nullable=False)
    days = db.Column(db.Integer, nullable=False, default=1)
    reason = db.Column(db.String(200))
    status = db.Column(db.String(20), default="Pending")
    approver = db.Column(db.String(100))
    decision_date = db.Column(db.DateTime)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    request_date = db.Column(db.DateTime, default=datetime.utcnow)
    date_filed = db.Column(db.DateTime, default=datetime.utcnow)
    is_paid = db.Column(db.Boolean, default=True)
    
class LeaveHistory(db.Model):
    __tablename__ = "leave_history"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    cutoff_start = db.Column(db.Date, nullable=False)
    cutoff_end = db.Column(db.Date, nullable=False)
    approved_count = db.Column(db.Integer, default=0)
    total_requests = db.Column(db.Integer, default=0)
    rejected_count = db.Column(db.Integer, default=0)

# ------------------ LOAN ------------------
class Loan(db.Model):
    __tablename__ = "loans"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date_needed = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), default="Pending")
    remarks = db.Column(db.String(200))
    approver = db.Column(db.String(100))
    decision_date = db.Column(db.DateTime)
    date_filed = db.Column(db.DateTime, default=datetime.utcnow)

class LoanHistory(db.Model):
    __tablename__ = "loan_history"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    cutoff_start = db.Column(db.Date, nullable=False)
    cutoff_end = db.Column(db.Date, nullable=False)
    total_amount = db.Column(db.Float, default=0)
    deductions_applied = db.Column(db.Float, default=0)

# ------------------ EVALUATION ------------------
class Evaluation(db.Model):
    __tablename__ = "evaluations"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    evaluator_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    rating = db.Column(db.Integer, nullable=True)
    remarks = db.Column(db.String(255))
    date = db.Column(db.DateTime, default=datetime.utcnow)
    category = db.Column(db.String(50), default="core")  # halimbawa: core, attitude, etc.
    employee = db.relationship(
        "Employee",
        foreign_keys=[employee_id],
        backref=db.backref("evaluations", lazy=True),
    )
    evaluator = db.relationship(
        "Employee",
        foreign_keys=[evaluator_id],
        backref=db.backref("evaluations_given", lazy=True),
    )

    @property
    def score(self):
        return self.rating

    @score.setter
    def score(self, value):
        self.rating = value


class EvaluationQuestion(db.Model):
    __tablename__ = "evaluation_questions"

    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50), nullable=False, default="core")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

        

# ------------------ QUIZ ------------------
class Quiz(db.Model):
    __tablename__ = "quizzes"
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.String(300), nullable=False)
    choice_a = db.Column(db.String(100), nullable=False)
    choice_b = db.Column(db.String(100), nullable=False)
    choice_c = db.Column(db.String(100))
    choice_d = db.Column(db.String(100))
    correct_answer = db.Column(db.String(1), nullable=False)
    points = db.Column(db.Integer, default=1)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    category = db.Column(db.String(100))

class QuizResult(db.Model):
    __tablename__ = "quiz_results"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey("quizzes.id"), nullable=True)
    score = db.Column(db.Integer, nullable=False)
    total_points = db.Column(db.Integer, nullable=False)
    date_taken = db.Column(db.DateTime, default=datetime.utcnow)
    employee = db.relationship("Employee", backref="quiz_results")
    quiz = db.relationship("Quiz", backref="results")
    is_official = db.Column(db.Boolean, default=True)

# ------------------ BULLETIN ------------------
class Bulletin(db.Model):
    __tablename__ = "bulletins"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    author = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ------------------ PAYROLL ------------------
class Payroll(db.Model):
    __tablename__ = "payrolls"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    cutoff_start = db.Column(db.Date, nullable=False)
    cutoff_end = db.Column(db.Date, nullable=False)
    gross_income = db.Column(db.Float, default=0.0)
    total_deductions = db.Column(db.Float, default=0.0)
    net_pay = db.Column(db.Float, default=0.0)
    sss = db.Column(db.Float, default=0.0)
    philhealth = db.Column(db.Float, default=0.0)
    pagibig = db.Column(db.Float, default=0.0)
    loan = db.Column(db.Float, default=0.0)
    cash_advance = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_paid = db.Column(db.Boolean, default=False)

# ------------------ MERIT / DEMERIT ------------------
class MeritDemerit(db.Model):
    __tablename__ = 'merit_demerit'
    pass
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'))
    date = db.Column(db.Date, default=datetime.utcnow)
    merit_points = db.Column(db.Integer, default=0)
    demerit_points = db.Column(db.Integer, default=0)

    employee = db.relationship("Employee", backref="merit_demerit_records")


class RedemptionHistory(db.Model):
    __tablename__ = 'redemption_history'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'))
    points_redeemed = db.Column(db.Integer, nullable=False)
    cash_value = db.Column(db.Float, nullable=False)
    date_redeemed = db.Column(db.DateTime, default=datetime.utcnow)


class IncidentReport(db.Model):
    __tablename__ = "incident_reports"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    reported_employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    category = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    signature = db.Column(db.Text, nullable=False)
    staff_signature = db.Column(db.Text, nullable=True)
    admin_signature = db.Column(db.Text, nullable=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    employee = db.relationship(
        "Employee",
        foreign_keys=[employee_id],
        backref="incident_reports"
    )
    reported_employee = db.relationship(
        "Employee",
        foreign_keys=[reported_employee_id],
        backref="incidents_reported_against"
    )
    reviewer = db.relationship("Employee", foreign_keys=[reviewed_by])


class EmployeeDocument(db.Model):
    __tablename__ = "employee_documents"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    phase = db.Column(db.String(30), nullable=False)
    document_type = db.Column(db.String(120), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    retention_years = db.Column(db.Integer, nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    employee = db.relationship("Employee", backref="employee_documents")
