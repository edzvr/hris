from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "secret"

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(basedir, 'instance', 'hris.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(os.path.join(basedir, app.config['UPLOAD_FOLDER']), exist_ok=True)

from models import db, Employee, Attendance, Holiday, LeaveRequest, LeaveHistory, Loan, LoanHistory, Evaluation, Quiz, QuizResult, Bulletin, Payroll

db.init_app(app)
migrate = Migrate(app, db)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ------------------ LOGIN MANAGER ------------------
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Employee, int(user_id))

# ------------------ ROUTES ------------------
@app.route('/')
def home():
    return render_template('home.html')

from werkzeug.security import generate_password_hash, check_password_hash

# ----------------- LOGIN + FORGOT PASSWORD ------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    action = request.form.get('action')  # alin ang pinindot: login o forgot

    if request.method == 'POST':
        email = request.form.get('email')

        # --- LOGIN FLOW ---
        if action == "login":
            password = request.form.get('password')
            remember = 'remember' in request.form

            user = Employee.query.filter_by(email=email).first()
            if user and check_password_hash(user.password, password):
                login_user(user, remember=remember)

                # ✅ Role-based redirect
                if "admin" in user.role.lower():
                    return redirect(url_for("dashboard_admin"))
                elif "staff" in user.role.lower():
                    return redirect(url_for("dashboard_staff"))
                else:
                    flash("⚠️ Unknown role, contact admin.", "warning")
                    return redirect(url_for("login"))
            else:
                flash("❌ Invalid email or password", "danger")

        # --- FORGOT PASSWORD FLOW ---
        elif action == "forgot":
            emp = Employee.query.filter_by(email=email).first()
            if emp:
                temp_pass = "Temp1234"
                emp.password = generate_password_hash(temp_pass)
                db.session.commit()
                flash(f"🔑 Temporary password generated: {temp_pass}", "info")
                return redirect(url_for('login'))
            else:
                flash("❌ Email not found", "danger")

    return render_template("login.html")

# ------------------ GLOBAL AUTH CHECK ------------------
@app.before_request
def check_authentication():
    exempt_routes = ['login', 'register', 'static']
    if not current_user.is_authenticated and request.endpoint not in exempt_routes:
        return redirect(url_for('login'))


# ------------------ REGISTER EMP ADMIN ------------------
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
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
            last_name=request.form['last_name'],
            dob=dob_val,
            role=request.form['role'],
            company=request.form['company'],
            email=request.form['email'],
            password=generate_password_hash(request.form['password']),
            contact_no=request.form.get('contact_no'),
            date_started=date_started_val,
            sss=request.form.get('sss'),
            philhealth=request.form.get('philhealth'),
            tin=request.form.get('tin'),
            pagibig=request.form.get('pagibig'),
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

    return render_template("profile.html", emp=emp, viewer=current_user)

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

        if request.form.get('same_address'):
            current_user.emergency_address = current_user.address
        else:
            current_user.emergency_address = request.form.get('emergency_address')

        file = request.files.get('profile_pic')
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            current_user.profile_pic = filename

        db.session.commit()
        flash("✅ Admin profile updated successfully!", "success")
        return redirect(url_for('dashboard_admin'))

    # Data for dashboard
    trece_employees = Employee.query.filter_by(company="Trece-Uno").all()
    auto_employees = Employee.query.filter_by(company="Auto Expert").all()
    trece_leaves = LeaveRequest.query.join(Employee).filter(Employee.company=="Trece-Uno").all()
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
        basic_pay = (emp.daily_rate or 0) * worked_days_count
        gross_income = basic_pay + (emp.allowance or 0) + (emp.incentives or 0)
        deductions = 193.75 + 96.88 + 50.00 + (500.0 if emp.loan_balance >= 500 else emp.loan_balance)
        net_pay = gross_income - deductions
        payroll_total += net_pay
        if emp.company in company_payroll:
            company_payroll[emp.company] += net_pay

    pending_ot = Attendance.query.filter_by(ot_status="Pending").count()
    pending_leaves = LeaveRequest.query.filter_by(status="Pending").count()

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
        company_payroll=company_payroll,
        trend_labels=trend_labels,
        trend_values=trend_values,
        start_cutoff=start_cutoff.date(),
        end_cutoff=end_cutoff.date()
    )
# ------------------ STAFF DASHBOARD ------------------
@app.route('/dashboard_staff', methods=["GET", "POST"])
@login_required
def dashboard_staff():
    if "staff" not in current_user.role.lower():
        flash("❌ Access denied. Staff only.", "danger")
        return redirect(url_for('login'))

    # Handle Clock-In/Clock-Out
    if request.method == "POST":
        if "clockin" in request.form:
            return redirect(url_for('attendance_clockin', employee_id=current_user.id))
        elif "clockout" in request.form:
            return redirect(url_for('attendance_clockout', employee_id=current_user.id))

    return render_template("dashboard_staff.html")


# ------------------ HOLIDAY + OVERTIME DASHBOARD ------------------
@app.route('/holiday_ot_dashboard')
@login_required
def holiday_ot_dashboard():
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    # Query lahat ng attendance na may overtime o holiday
    holiday_attendance = Attendance.query.filter(
        (Attendance.is_holiday_ot == True) |
        (Attendance.is_weekday_ot == True) |
        (Attendance.is_restday_ot == True)
    ).all()

    # Dictionary ng Philippine holidays (sample lang, pwede mong palitan/expand)
    PH_HOLIDAYS_2026 = {
        datetime(2026, 1, 1).date(): "New Year's Day",
        datetime(2026, 6, 12).date(): "Independence Day",
        datetime(2026, 12, 25).date(): "Christmas Day",
        datetime(2026, 12, 30).date(): "Rizal Day",
    }

    return render_template(
        "holiday_ot_dashboard.html",
        holiday_attendance=holiday_attendance,
        PH_HOLIDAYS_2026=PH_HOLIDAYS_2026
    )


# ------------------ CLOCK IN / OUT (Unified) ------------------
@app.route('/attendance_action/<int:employee_id>', methods=['POST'])
@login_required
def attendance_action(employee_id):
    emp = Employee.query.get_or_404(employee_id)

    if "clockin" in request.form:
        # --- Clock In Logic ---
        log = Attendance(date=datetime.today().date(),
                         clock_in=datetime.now(),
                         status="Present",
                         employee_id=employee_id)
        db.session.add(log)
        db.session.commit()
        flash("🟢 Clocked in successfully!", "success")

    elif "clockout" in request.form:
        # --- Clock Out Logic ---
        log = Attendance.query.filter_by(employee_id=employee_id,
                                         date=datetime.today().date()).first()
        if log and not log.clock_out:
            log.clock_out = datetime.now()
            db.session.commit()
            flash("🔴 Clocked out successfully!", "success")
        else:
            flash("⚠️ No active clock-in found.", "warning")

    return redirect(url_for('attendance', employee_id=employee_id))


# ------------------ ATTENDANCE REPORT ------------------
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
            "branch": getattr(log, "branch", "N/A")
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
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
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
            line = f"{log.date.strftime('%Y-%m-%d')} | {log.clock_in.strftime('%H:%M:%S') if log.clock_in else 'N/A'} | {log.clock_out.strftime('%H:%M:%S') if log.clock_out else 'N/A'} | {log.status} | {hours_worked:.2f} | {getattr(log, 'branch', 'N/A')}"
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

        import io, csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Date","Clock In","Clock Out","Status","Hours","Branch"])
        for log in filtered:
            hours_worked = (log.clock_out - log.clock_in).seconds / 3600 if log.clock_in and log.clock_out else 0
            writer.writerow([log.date, log.clock_in, log.clock_out, log.status, f"{hours_worked:.2f}", getattr(log, "branch", "N/A")])

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

# ------------------ OVERTIME MANAGEMENT ------------------
@app.route('/overtime/<int:attendance_id>', methods=['GET', 'POST'])
@login_required
def overtime(attendance_id):
    att = Attendance.query.get_or_404(attendance_id)

    # Only admins can adjust OT
    if not current_user.is_admin:
        flash("⚠️ Only admins can adjust overtime records.", "danger")
        return redirect(url_for('dashboard_staff'))

    if request.method == 'POST':
        # Safe conversion for overtime hours
        ot_val = request.form.get('overtime_hours', '').strip()
        try:
            att.overtime_hours = float(ot_val) if ot_val else 0.0
        except ValueError:
            att.overtime_hours = 0.0

        # Update OT type flags
        ot_type = request.form.get('ot_type', 'weekday')
        att.is_weekday_ot = (ot_type == 'weekday')
        att.is_restday_ot = (ot_type == 'restday')
        att.is_holiday_ot = (ot_type == 'holiday')

        # Update approval status
        att.ot_status = request.form.get('action', 'pending')  # approved/rejected/pending

        db.session.commit()
        flash("✅ Overtime record updated successfully!", "success")
        return redirect(url_for('overtime', attendance_id=attendance_id))

    return render_template("overtime.html", att=att)


# ------------------ HOLIDAY + OVERTIME DASHBOARD ------------------
@app.route('/export_holiday_ot')
@login_required
def export_holiday_ot():
    PH_HOLIDAYS_2026 = {
        datetime(2026, 1, 1).date(): "New Year's Day",
        datetime(2026, 4, 9).date(): "Araw ng Kagitingan",
        datetime(2026, 5, 1).date(): "Labor Day",
        datetime(2026, 6, 12).date(): "Independence Day",
        datetime(2026, 8, 21).date(): "Ninoy Aquino Day",
        datetime(2026, 8, 31).date(): "National Heroes Day",
        datetime(2026, 11, 1).date(): "All Saints' Day",
        datetime(2026, 11, 30).date(): "Bonifacio Day",
        datetime(2026, 12, 25).date(): "Christmas Day",
        datetime(2026, 12, 30).date(): "Rizal Day",
    }

    holiday_attendance = Attendance.query.filter(
        Attendance.date.in_(PH_HOLIDAYS_2026.keys())
    ).all()

    def generate():
        data = [['Attendance ID','Employee ID','Date','Holiday Name','Status','OT Hours','OT Type','OT Status']]
        for att in holiday_attendance:
            ot_type = "Weekday" if att.is_weekday_ot else "Rest Day" if att.is_restday_ot else "Holiday" if att.is_holiday_ot else "None"
            row = [
                att.id,
                att.employee_id,
                att.date,
                PH_HOLIDAYS_2026.get(att.date, ""),
                att.status,
                att.overtime_hours or 0,
                ot_type,
                getattr(att, "ot_status", "Pending")
            ]
            data.append(row)
        return '\n'.join([','.join(map(str, row)) for row in data])

    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition":"attachment;filename=holiday_ot_summary.csv"})

# ------------------ PAYROLL + PAYSLIP ------------------
@app.route('/payroll/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def payroll(employee_id):
    emp = Employee.query.get_or_404(employee_id)

    # STAFF VIEW: payslip + history only
    if current_user.role.lower() == 'staff':
        if current_user.id != employee_id:
            flash("❌ Access denied.", "danger")
            return redirect(url_for('dashboard_staff'))

        # Query salary history ng staff
        history = Payroll.query.filter_by(employee_id=current_user.id)\
                               .order_by(Payroll.cutoff_start.desc()).all()

        return render_template("payslip_staff.html",
                               emp=current_user,
                               history=history)

    # ADMIN VIEW: full payroll computation
    today = datetime.today()
    start_cutoff = today - timedelta(days=(today.weekday() + 2) % 7)  # last Saturday
    end_cutoff = start_cutoff + timedelta(days=6)  # following Friday

    if request.method == 'POST':
        # Update daily rate
        rate_val = request.form.get('daily_rate', '').strip()
        emp.daily_rate = float(rate_val) if rate_val else emp.daily_rate

        # Update allowance
        allowance_val = request.form.get('allowance', '').strip()
        emp.allowance = float(allowance_val) if allowance_val else emp.allowance

        # Update incentives
        incentives_val = request.form.get('incentives', '').strip()
        emp.incentives = float(incentives_val) if incentives_val else emp.incentives

        # Automatic loan deduction per cutoff
        if emp.loan_balance > 0:
            deduction = 500.0 if emp.loan_balance >= 500 else emp.loan_balance
            emp.loan_balance -= deduction

        db.session.commit()
        flash("✅ Payroll updated successfully!", "success")
        return redirect(url_for('payroll', employee_id=employee_id))

    # Count worked days within cutoff
    worked_days_count = Attendance.query.filter(
        Attendance.employee_id == employee_id,
        Attendance.clock_out != None,
        Attendance.clock_in >= start_cutoff,
        Attendance.clock_in <= end_cutoff
    ).count()

    daily_rate = emp.daily_rate or 0
    basic_pay = daily_rate * worked_days_count

    # Standard deductions
    sss = 193.75
    philhealth = 96.88
    pagibig = 50.00
    loan = float(emp.loan_balance) if emp.loan_balance < 500 else 500.00

    gross_income = basic_pay + (emp.allowance or 0) + (emp.incentives or 0)
    total_deductions = sss + philhealth + pagibig + loan
    net_pay = gross_income - total_deductions

  # 👉 DITO MO ILALAGAY ANG WORKFLOW (SAVE TO PAYROLL TABLE)
    payroll_record = Payroll(
        employee_id=emp.id,
        cutoff_start=start_cutoff.date(),
        cutoff_end=end_cutoff.date(),
        gross_income=gross_income,
        total_deductions=total_deductions,
        net_pay=net_pay
    )
    db.session.add(payroll_record)
    db.session.commit()

    return render_template("payroll_admin.html",
         emp=emp, worked_days_count=worked_days_count, basic_pay=basic_pay, 
         gross_income=gross_income, sss=sss, philhealth=philhealth, 
         pagibig=pagibig, loan=loan, net_pay=net_pay,
         start_cutoff=start_cutoff.date(), end_cutoff=end_cutoff.date())

# ------------------ PAYROLL DASHBOARD ------------------
@app.route('/payroll_dashboard', methods=['GET', 'POST'])
@login_required
def payroll_dashboard():
    employees = Employee.query.all()

    if request.method == 'POST':
        for emp in employees:
            # Update daily rate
            rate_val = request.form.get(f'daily_rate_{emp.id}', '').strip()
            emp.daily_rate = float(rate_val) if rate_val else emp.daily_rate

            # Update allowance
            allowance_val = request.form.get(f'allowance_{emp.id}', '').strip()
            emp.allowance = float(allowance_val) if allowance_val else emp.allowance

            # Update incentives
            incentives_val = request.form.get(f'incentives_{emp.id}', '').strip()
            emp.incentives = float(incentives_val) if incentives_val else emp.incentives

        db.session.commit()
        flash("✅ Payroll updated successfully for all employees!", "success")
        return redirect(url_for('payroll_dashboard'))

    # Compute cutoff
    today = datetime.today()
    start_cutoff = today - timedelta(days=(today.weekday() + 2) % 7)
    end_cutoff = start_cutoff + timedelta(days=6)

    payroll_data = []
    for emp in employees:
        worked_days_count = Attendance.query.filter(
            Attendance.employee_id == emp.id,
            Attendance.clock_out != None,
            Attendance.clock_in >= start_cutoff,
            Attendance.clock_in <= end_cutoff
        ).count()

        basic_pay = (emp.daily_rate or 0) * worked_days_count
        sss = 193.75
        philhealth = 96.88
        pagibig = 50.00
        loan = float(emp.loan_balance) if emp.loan_balance < 500 else 500.00

        gross_income = basic_pay + (emp.allowance or 0) + (emp.incentives or 0)
        total_deductions = sss + philhealth + pagibig + loan
        net_pay = gross_income - total_deductions

        payroll_data.append({
            "emp": emp,
            "worked_days": worked_days_count,
            "basic_pay": basic_pay,
            "gross_income": gross_income,
            "deductions": total_deductions,
            "net_pay": net_pay
        })

    return render_template("payroll_dashboard.html",
                           payroll_data=payroll_data,
                           start_cutoff=start_cutoff.date(),
                           end_cutoff=end_cutoff.date())

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


# ------------------ PAYSLIP ------------------
@app.route('/payslip/<int:employee_id>/download')
@login_required
def download_payslip(employee_id):
    emp = Employee.query.get_or_404(employee_id)

    # Compute cutoff (same logic as payroll)
    today = datetime.today()
    start_cutoff = today - timedelta(days=(today.weekday() + 2) % 7)
    end_cutoff = start_cutoff + timedelta(days=6)

    worked_days_count = Attendance.query.filter(
        Attendance.employee_id == employee_id,
        Attendance.clock_out != None,
        Attendance.clock_in >= start_cutoff,
        Attendance.clock_in <= end_cutoff
    ).count()

    daily_rate = emp.daily_rate or 0
    basic_pay = daily_rate * worked_days_count

    sss = 193.75
    philhealth = 96.88
    pagibig = 50.00
    loan = float(emp.loan_balance) if emp.loan_balance < 500 else 500.00

    gross_income = basic_pay + (emp.allowance or 0) + (emp.incentives or 0)
    total_deductions = sss + philhealth + pagibig + loan
    net_pay = gross_income - total_deductions

    # 👉 Generate PDF in memory
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(200, 750, "Payslip")

    c.setFont("Helvetica", 12)
    c.drawString(50, 720, f"Employee: {emp.first_name} {emp.last_name}")
    c.drawString(50, 700, f"Cutoff: {start_cutoff.date()} to {end_cutoff.date()}")

    c.drawString(50, 670, f"Worked Days: {worked_days_count}")
    c.drawString(50, 650, f"Daily Rate: ₱{daily_rate:.2f}")
    c.drawString(50, 630, f"Basic Pay: ₱{basic_pay:.2f}")
    c.drawString(50, 610, f"Allowance: ₱{(emp.allowance or 0):.2f}")
    c.drawString(50, 590, f"Incentives: ₱{(emp.incentives or 0):.2f}")
    c.drawString(50, 570, f"Gross Income: ₱{gross_income:.2f}")

    c.drawString(50, 540, "Deductions:")
    c.drawString(70, 520, f"SSS: ₱{sss:.2f}")
    c.drawString(70, 500, f"PhilHealth: ₱{philhealth:.2f}")
    c.drawString(70, 480, f"Pag-IBIG: ₱{pagibig:.2f}")
    c.drawString(70, 460, f"Loan: ₱{loan:.2f}")
    c.drawString(50, 440, f"Total Deductions: ₱{total_deductions:.2f}")

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, 410, f"Net Pay: ₱{net_pay:.2f}")

    c.showPage()
    c.save()
    buffer.seek(0)

    return send_file(buffer, as_attachment=True,
                     download_name=f"Payslip_{emp.first_name}_{emp.last_name}.pdf",
                     mimetype='application/pdf')

# ------------------ LEAVE MODULE ------------------
from datetime import datetime, date
from flask_mail import Mail, Message

mail = Mail(app)  # configure sa app init

def send_leave_email(emp, leave, action):
    subject = f"Leave Request {action.capitalize()}"
    body = f"""
    Hi {emp.first_name},

    Your leave request ({leave.leave_type}, {leave.days} day/s from {leave.start_date} to {leave.end_date})
    has been {action}.

    Status: {leave.status}
    Reason: {leave.reason}

    Regards,
    HR Department
    """
    msg = Message(subject, recipients=[emp.email])
    msg.body = body
    mail.send(msg)

@app.route('/leave', methods=['GET','POST'])
@login_required
def leave():
    # --- Apply leave (POST) ---
    if request.method == 'POST':
        leave_type = request.form.get('leave_type')
        days = int(request.form.get('days'))
        start_date = datetime.strptime(request.form.get('start_date'), "%Y-%m-%d").date()
        end_date = datetime.strptime(request.form.get('end_date'), "%Y-%m-%d").date()

        # ✅ Validation: check SIL credits
        if leave_type == "SIL":
            emp = Employee.query.get(current_user.id)
            if emp.sil_credits < days:
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
            is_paid=(leave_type != "LWOP")
        )
        db.session.add(new_leave)
        db.session.commit()
        flash("✅ Leave application submitted!", "success")
        return redirect(url_for('leave'))

    # --- Approval action (GET param) ---
    leave_id = request.args.get("leave_id")
    action = request.args.get("action")
    if leave_id and action and current_user.role.lower() == 'admin':
        leave = LeaveRequest.query.get_or_404(int(leave_id))
        emp = Employee.query.get_or_404(leave.employee_id)

        if action == "approve":
            if leave.leave_type == "SIL" and leave.status != "Approved":
                if emp.sil_credits >= leave.days:
                    emp.sil_credits -= leave.days
                    leave.status = "Approved"
                    leave.is_paid = True
                    leave.decision_date = datetime.utcnow()
                    db.session.commit()
                    send_leave_email(emp, leave, "approved")
                    flash("✅ SIL leave approved and credits updated.", "success")
                else:
                    flash("❌ Not enough SIL credits.", "danger")
            else:
                leave.status = "Approved"
                leave.is_paid = (leave.leave_type != "LWOP")
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
        employees = Employee.query.all()
        for emp in employees:
            emp.sil_credits = 5
        db.session.commit()
        flash("🔄 SIL credits reset to 5 days for all employees.", "info")

    # --- Query leaves ---
    if current_user.role.lower() == 'admin':
        leaves = LeaveRequest.query.order_by(LeaveRequest.date_filed.desc()).all()
    else:
        leaves = LeaveRequest.query.filter_by(employee_id=current_user.id)\
                                   .order_by(LeaveRequest.date_filed.desc()).all()

    # --- Summary counts ---
    approved = LeaveRequest.query.filter_by(status="Approved").count()
    pending = LeaveRequest.query.filter_by(status="Pending").count()
    rejected = LeaveRequest.query.filter_by(status="Rejected").count()
    lwop = LeaveRequest.query.filter_by(leave_type="LWOP").count()

    # --- Trend (last 6 cutoff periods) ---
    leave_trend = LeaveHistory.query.order_by(LeaveHistory.cutoff_start.desc()).limit(6).all()
    leave_labels = [f"{r.cutoff_start.strftime('%b %d')} - {r.cutoff_end.strftime('%b %d')}" for r in leave_trend][::-1]
    leave_values = [r.approved_count for r in leave_trend][::-1]

    # --- Export/Print options ---
    export_type = request.args.get("export")
    if export_type == "csv":
        import csv
        from io import StringIO
        si = StringIO()
        writer = csv.writer(si)
        writer.writerow(["Type","Days","Start","End","Reason","Status","Paid?"])
        for l in leaves:
            paid_flag = "No" if l.leave_type == "LWOP" else "Yes"
            writer.writerow([l.leave_type, l.days, l.start_date, l.end_date,
                             l.reason, l.status, paid_flag])
        output = si.getvalue()
        return Response(output, mimetype="text/csv",
                        headers={"Content-Disposition":"attachment;filename=leave_history.csv"})
    elif export_type == "excel":
        import pandas as pd
        from io import BytesIO
        df = pd.DataFrame([{
            "Type": l.leave_type,
            "Days": l.days,
            "Start": l.start_date,
            "End": l.end_date,
            "Reason": l.reason,
            "Status": l.status,
            "Paid?": "No" if l.leave_type == "LWOP" else "Yes"
        } for l in leaves])
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return Response(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition":"attachment;filename=leave_history.xlsx"})
    elif export_type == "print":
        return render_template("leave_print.html", leaves=leaves)

    # --- Default: normal leave page ---
    return render_template("leave.html",
                           leaves=leaves,
                           approved=approved,
                           pending=pending,
                           rejected=rejected,
                           lwop=lwop,
                           leave_labels=leave_labels,
                           leave_values=leave_values)

# ------------------ LOAN ------------------

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
        flash(f"Loan {action}d successfully!", "success")
        return redirect(url_for('loan'))

    # --- Export CSV ---
    if action == "csv":
        loans = Loan.query.filter_by(employee_id=current_user.id).all()
        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(["Amount","Reason","Date Needed","Status","Remarks"])
        for l in loans:
            cw.writerow([l.amount, l.reason, l.date_needed, l.status, l.remarks or ""])
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=loan_history.csv"
        output.headers["Content-type"] = "text/csv"
        return output

    # --- Export Excel ---
    if action == "excel":
        loans = Loan.query.filter_by(employee_id=current_user.id).all()
        df = pd.DataFrame([{
            "Amount": l.amount,
            "Reason": l.reason,
            "Date Needed": l.date_needed,
            "Status": l.status,
            "Remarks": l.remarks or ""
        } for l in loans])
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Loans")
        output.seek(0)
        return send_file(output, download_name="loan_history.xlsx", as_attachment=True)

    # --- Print View (PDF) ---
    if action == "print":
        loans = Loan.query.filter_by(employee_id=current_user.id).all()
        return render_template("loan.html", loans=loans, print_mode=True)

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
            new_loan = Loan(
                employee_id=current_user.id,
                amount=amount,
                reason=request.form.get('reason'),
                date_needed=request.form.get('date_needed'),
                status='Pending'
            )
            db.session.add(new_loan)
            db.session.commit()
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
                           print_mode=False)

# ------------------ ASSESSMENT MODULE ------------------
@app.route('/assessment', methods=['GET','POST'])
@login_required
def assessment():
    action = request.args.get('action', 'menu')
    view = action

    results = []
    evals = []
    quiz_results = []
    suggestion = "Sample AI suggestion"

    if action == "upload_quiz" and request.method == "POST":
        file = request.files.get("quiz_file")
        if file:
            flash("✅ Quiz uploaded successfully!", "success")
        else:
            flash("⚠ No file selected.", "warning")

    elif action == "quiz_results":
        results = [
            (type("R", (), {"score": 8, "total_points": 10, "date_taken": datetime(2026,7,25,14,30)}),
             "Kristina Emma", "Ronquillo", "Geography Basics"),
            (type("R", (), {"score": 6, "total_points": 10, "date_taken": datetime(2026,7,26,10,15)}),
             "Mae", "Santos", "Math Fundamentals"),
        ]

    elif action == "quiz_leaderboard":
        quiz_id = request.args.get("quiz_id")
        results = [
            (type("R", (), {"score": 9, "total_points": 10}), "Kristina Emma", "Ronquillo"),
            (type("R", (), {"score": 8, "total_points": 10}), "Mae", "Santos"),
            (type("R", (), {"score": 7, "total_points": 10}), "Danica", "Lopez"),
        ]

    elif action == "evaluation":
        if request.method == "POST" and request.args.get("action") == "peer_eval":
            emp_id = request.form.get("employee_id")
            score = request.form.get("score")
            remarks = request.form.get("remarks")
            flash("✅ Peer evaluation submitted!", "success")

    elif action == "history":
        quiz_results = [
            {"date_taken": datetime(2026,7,25), "score": 8, "total_points": 10},
            {"date_taken": datetime(2026,7,26), "score": 6, "total_points": 10},
            {"date_taken": datetime(2026,6,15), "score": 9, "total_points": 10},
        ]
        monthly_results = {}
        for q in quiz_results:
            month_key = q["date_taken"].strftime("%Y-%m")
            if month_key not in monthly_results:
                monthly_results[month_key] = []
            monthly_results[month_key].append(q)

        evals = [
            {"date": "2026-07-20", "score": 9, "remarks": "Excellent teamwork"},
            {"date": "2026-07-22", "score": 7, "remarks": "Needs improvement in punctuality"},
        ]

    elif action in ["ai_insights","ai_private","ai_public"]:
        suggestion = "Encourage more peer‑to‑peer evaluations to balance quiz performance with teamwork insights."

    elif action == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Date", "Score", "Remarks"])
        for e in evals:
            writer.writerow([e["date"], e["score"], e["remarks"]])
        response = make_response(output.getvalue())
        response.headers["Content-Disposition"] = "attachment; filename=assessment.csv"
        response.headers["Content-type"] = "text/csv"
        return response

    elif action == "excel":
        import pandas as pd
        df = pd.DataFrame(evals)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Assessment")
        response = make_response(output.getvalue())
        response.headers["Content-Disposition"] = "attachment; filename=assessment.xlsx"
        response.headers["Content-type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return response

    elif action == "pdf":
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(200, 750, "Assessment Report")
        y = 700
        for e in evals:
            c.setFont("Helvetica", 12)
            c.drawString(100, y, f"{e['date']} - Score: {e['score']} ({e['remarks']})")
            y -= 20
        c.save()
        buffer.seek(0)
        return send_file(buffer, as_attachment=True,
                         download_name="assessment.pdf",
                         mimetype="application/pdf")

    return render_template("assessment.html",
                           view=view,
                           results=results,
                           evals=evals,
                           quiz_results=quiz_results,
                           suggestion=suggestion)


# ------------------ STAFF EVALUATION ------------------
@app.route('/evaluation_dashboard', methods=['GET','POST'])
@login_required
def evaluation_dashboard():
    if current_user.role.lower() != "admin":
        flash("❌ Access denied. Admins only.", "danger")
        return redirect(url_for('login'))

    employees = Employee.query.all()

    if request.method == 'POST':
        for emp in employees:
            rating = request.form.get(f'rating_{emp.id}')
            remarks = request.form.get(f'remarks_{emp.id}')
            if rating or remarks:
                eval = Evaluation(employee_id=emp.id, rating=rating, remarks=remarks, date=datetime.today())
                db.session.add(eval)
        db.session.commit()
        flash("✅ Evaluations saved successfully!", "success")
        return redirect(url_for('evaluation_dashboard'))

    evaluations = Evaluation.query.order_by(Evaluation.date.desc()).all()
    return render_template("evaluation_dashboard.html", employees=employees, evaluations=evaluations)

# ------------------ QUIZ MODULE ------------------
import random, io, csv
from apscheduler.schedulers.background import BackgroundScheduler

def generate_auto_quiz():
    auto_questions = [
        Quiz(question="Ano ang gamit ng spark plug?",
             choice_a="Nagbibigay ng kuryente sa ilaw",
             choice_b="Nagpapasimula ng combustion sa engine",
             choice_c="Nagpapalamig ng makina",
             choice_d="Nagpapadulas ng piston",
             correct_answer="B", points=1),
        Quiz(question="Kailan dapat magpalit ng engine oil?",
             choice_a="Tuwing 1,000 km",
             choice_b="Tuwing 5,000 km o ayon sa manual",
             choice_c="Tuwing flat tire",
             choice_d="Tuwing car wash",
             correct_answer="B", points=1),
        Quiz(question="Anong brand ng sasakyan ang madalas gamitin sa Pilipinas?",
             choice_a="Toyota",
             choice_b="Honda",
             choice_c="Mitsubishi",
             choice_d="Isuzu",
             correct_answer="A", points=1),
    ]
    for q in auto_questions:
        db.session.add(q)
    db.session.commit()

scheduler = BackgroundScheduler()
scheduler.add_job(generate_auto_quiz, 'cron', day=1, hour=0)
scheduler.start()

# ------------------ QUIZ RESULT------------------

@app.route('/quiz/<int:employee_id>', methods=['GET','POST'])
@login_required
def quiz(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    mode = request.form.get("mode", "auto")
    category = request.form.get("category", "General")

    # Manual upload option
    if request.method == 'POST' and mode == "upload" and 'file' in request.files:
        file = request.files['file']
        if file and file.filename.endswith('.csv'):
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            reader = csv.DictReader(stream)
            for row in reader:
                quiz = Quiz(
                    category=row.get('category', category),
                    question=row['question'],
                    choice_a=row['choice_a'],
                    choice_b=row['choice_b'],
                    choice_c=row.get('choice_c'),
                    choice_d=row.get('choice_d'),
                    correct_answer=row['correct_answer'],
                    points=int(row.get('points',1))
                )
                db.session.add(quiz)
            db.session.commit()
            flash("Quiz uploaded successfully!", "success")
            return redirect(url_for('quiz', employee_id=employee_id))

    # Get current month quizzes by category
    current_month = datetime.utcnow().month
    quizzes = Quiz.query.filter(
        db.extract('month', Quiz.date_created) == current_month,
        Quiz.category == category
    ).all()
    if not quizzes and mode == "auto":
        generate_auto_quiz(category)
        quizzes = Quiz.query.filter(
            db.extract('month', Quiz.date_created) == current_month,
            Quiz.category == category
        ).all()

    random.shuffle(quizzes)

    # Handle quiz answers
    if request.method == 'POST' and mode == "take":
        score, total_points = 0, 0
        for quiz in quizzes:
            answer = request.form.get(f"quiz_{quiz.id}")
            total_points += quiz.points
            if answer == quiz.correct_answer:
                score += quiz.points

        percentage = (score / total_points * 100) if total_points else 0

        # Check kung may official result na sa buwan na ito
        existing = QuizResult.query.filter_by(
            employee_id=employee_id,
            is_official=True
        ).filter(db.extract('month', QuizResult.date_taken) == datetime.utcnow().month).first()

        if existing:
            # Retake attempt (uncounted)
            result = QuizResult(employee_id=employee_id, score=score, total_points=total_points, is_official=False)
            flash("Retake completed. This attempt is for practice only.", "info")
        else:
            # First attempt (official)
            result = QuizResult(employee_id=employee_id, score=score, total_points=total_points, is_official=True)
            if percentage >= 75:
                flash("🎉 Congratulations! You passed the quiz!", "success")
                emp.merit_points += 5  # example increment
            else:
                flash("⚠️ You need to retake the quiz.", "warning")
                emp.demerit_points += 3  # example increment
                # Optional: create notification entry
                # notif = QuizNotification(employee_id=employee_id, message="Retake required")
                # db.session.add(notif)

        db.session.add(result)
        db.session.commit()
        return redirect(url_for('quiz', employee_id=employee_id))

    results = QuizResult.query.filter_by(employee_id=employee_id).all()
    return render_template("quiz.html", emp=emp, quizzes=quizzes, results=results, category=category)


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
# ------------------ BULLETIN MODULE ------------------
@app.route('/bulletin')
@login_required
def bulletin():
    # Sample placeholder data (palitan mo ng actual DB query)
    posts = [
        {"title": "Team Meeting", "content": "Reminder: Meeting on Friday at 3PM", "date": "2026-07-28"},
        {"title": "Holiday Notice", "content": "Office closed on August 21 for Ninoy Aquino Day", "date": "2026-07-29"},
    ]
    return render_template("bulletin.html", posts=posts)

# ------------------BACK UP ------------------
@app.route('/backup')
@login_required
def backup():
    local_exists = os.path.exists('hris.db')
    backup_status = "success"
    last_backup = datetime.now().strftime("%B %d, %Y %I:%M %p")

    # Optional: create backup file
    if local_exists:
        import shutil
        shutil.copy('hris.db', f"backup_hris_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")

    return render_template("backup_local.html",
                           local_exists=local_exists,
                           backup_status=backup_status,
                           last_backup=last_backup)

# ------------------ LOG OUT ------------------
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ------------------ MAIN ------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, host="0.0.0.0", port=5002)
