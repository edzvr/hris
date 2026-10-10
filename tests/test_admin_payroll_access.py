from datetime import date, datetime, timedelta

import hris
from hris import app, ensure_employee_hr_columns
from models import Attendance, Employee, Payroll, db


def log_in(client, employee):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee.id)
        session['_fresh'] = True


def test_admin_can_grant_staff_admin_payroll_access_from_profile():
    with app.app_context():
        admin = Employee(first_name='Payroll', last_name='Owner', role='admin')
        staff = Employee(
            first_name='Payroll',
            last_name='Delegate',
            role='staff',
            company='Trece-Uno',
            job_description='Office Staff',
            employment_status='Regular',
            payroll_attendance_exempt=True,
        )
        db.session.add_all([admin, staff])
        db.session.commit()
        admin_id, staff_id = admin.id, staff.id
    try:
        client = app.test_client()
        with app.app_context():
            admin = db.session.get(Employee, admin_id)
            log_in(client, admin)
        profile_page = client.get(f'/profile/{staff_id}')
        assert profile_page.status_code == 200
        assert b'Owner: exclude attendance pay and payroll summaries' not in profile_page.data
        response = client.post(
            f'/profile/{staff_id}',
            data={
                'first_name': 'Payroll',
                'last_name': 'Delegate',
                'job_description': 'Office Staff',
                'employment_status': 'Regular',
                'role': 'staff',
                'company': 'Trece-Uno',
                'admin_payroll_access': '1',
                'payroll_attendance_exempt': '1',
            },
        )
        assert response.status_code == 302
        with app.app_context():
            staff = db.session.get(Employee, staff_id)
            assert staff.admin_payroll_access is True
            assert staff.payroll_attendance_exempt is False
            log_in(client, staff)
        assert client.get('/payroll_dashboard').status_code == 403
        assert client.get('/payroll-loan-review').status_code == 200
        assert client.get(
            '/payroll/summary?company=Trece-Uno&view=true&cutoff_start=2026-10-03'
        ).status_code == 200
        dashboard = client.get('/dashboard_staff').data
        assert b'Review Loan Deductions' in dashboard
        assert b'Cash Payroll Summary' in dashboard
        assert dashboard.index(b'Review Loan Deductions') < dashboard.index(
            b'<details class="dashboard-tools">'
        )
        assert b'Payroll Loan Review' in dashboard
        assert b'Cash Payroll Summary' in dashboard
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.delete(db.session.get(Employee, admin_id))
            db.session.commit()


def test_staff_with_shared_payroll_access_are_not_owner_exempt_in_admin_payroll():
    with app.app_context():
        admin = Employee(first_name='Payroll', last_name='Owner', role='admin')
        karl = Employee(
            first_name='Karl',
            last_name='Ronquillo',
            role='staff',
            company='Trece-Uno',
            payroll_preparation_access=True,
            admin_payroll_access=True,
            payroll_attendance_exempt=True,
        )
        keira = Employee(
            first_name='Keira',
            last_name='Ronquillo',
            role='staff',
            company='Trece-Uno',
            payroll_preparation_access=True,
            admin_payroll_access=True,
            payroll_attendance_exempt=True,
        )
        owner = Employee(
            first_name='Payroll',
            last_name='Admin',
            role='admin',
            company='Trece-Uno',
            payroll_attendance_exempt=True,
        )
        db.session.add_all([admin, karl, keira, owner])
        db.session.commit()
        admin_id, karl_id, keira_id, owner_id = (
            admin.id, karl.id, keira.id, owner.id
        )

    try:
        with app.app_context():
            ensure_employee_hr_columns()
            assert db.session.get(Employee, karl_id).payroll_attendance_exempt is False
            assert db.session.get(Employee, keira_id).payroll_attendance_exempt is False
            assert db.session.get(Employee, owner_id).payroll_attendance_exempt is True

        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, admin_id))
        response = client.get('/payroll_dashboard?cutoff_start=2026-10-03')
        assert response.status_code == 200
        assert b'SSS Loan' in response.data
        assert b'Pag-IBIG Loan' in response.data
        assert b'Karl Ronquillo' in response.data
        assert b'Keira Ronquillo' in response.data
        assert b'Finalize Payroll' in response.data
        assert b'Payroll Admin' not in response.data
        assert f'name="loan_deduction_{karl_id}"'.encode() in response.data
        assert f'name="loan_deduction_{keira_id}"'.encode() in response.data
        assert b'Company Loan Deduction This Cutoff' in response.data
        assert f'name="loan_deduction_{karl_id}"'.encode() in response.data
        assert f'name="loan_deduction_{keira_id}"'.encode() in response.data
        assert b'Company Loan Deduction This Cutoff' in response.data
    finally:
        with app.app_context():
            db.session.rollback()
            for employee_id in (karl_id, keira_id, owner_id, admin_id):
                employee = db.session.get(Employee, employee_id)
                if employee:
                    db.session.delete(employee)
            db.session.commit()


def test_company_loan_can_be_saved_from_reopened_detailed_payslip(monkeypatch):
    cutoff_start = date(2026, 10, 3)
    cutoff_end = cutoff_start + timedelta(days=6)
    with app.app_context():
        admin = Employee(first_name='Payroll', last_name='Editor', role='admin')
        staff = Employee(
            first_name='Payroll',
            last_name='Loan Staff',
            role='staff',
            company='Trece-Uno',
            daily_rate=600,
            loan_balance=1000,
        )
        payroll = Payroll(
            employee=staff,
            cutoff_start=cutoff_start,
            cutoff_end=cutoff_end,
            loan=500,
        )
        db.session.add_all([admin, staff, payroll])
        db.session.commit()
        admin_id, staff_id, payroll_id = admin.id, staff.id, payroll.id

    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, admin_id))

        payroll_url = f'/payroll/{staff_id}?cutoff_start={cutoff_start.isoformat()}'
        response = client.get(payroll_url)
        assert response.status_code == 200
        assert b'name="loan_deduction"' in response.data
        assert b'name="sss_loan"' in response.data
        assert b'name="pagibig_loan"' in response.data
        assert b'Save & Recalculate Payroll' in response.data

        response = client.post(
            f'/payroll/{staff_id}',
            data={
                'cutoff_start': cutoff_start.isoformat(),
                'save_payroll': '1',
                'loan_deduction': '300',
                'sss_loan': '25',
                'pagibig_loan': '20',
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            payroll = db.session.get(Payroll, payroll_id)
            assert payroll.loan == 300
            assert payroll.sss_loan == 25
            assert payroll.pagibig_loan == 20
            assert payroll.total_deductions == 345
            assert payroll.net_pay == -345
            assert payroll.is_paid is False

        response = client.get(
            f'/payroll/{staff_id}?finalize=true&cutoff_start={cutoff_start.isoformat()}'
        )
        assert response.status_code == 200
        assert b'name="loan_deduction"' not in response.data
        assert b'name="sss_loan"' not in response.data
        assert b'name="pagibig_loan"' not in response.data
        assert b'Reopen this finalized payroll to edit and save' in response.data

        monkeypatch.setattr(
            hris,
            'completed_cutoff',
            lambda: (cutoff_start, cutoff_start + timedelta(days=7)),
        )
        response = client.post(f'/payroll/{staff_id}/reopen')
        assert response.status_code == 302
        response = client.get(payroll_url)
        assert response.status_code == 200
        assert b'name="loan_deduction"' in response.data

        client.post(
            f'/payroll/{staff_id}',
            data={
                'cutoff_start': cutoff_start.isoformat(),
                'save_payroll': '1',
                'loan_deduction': '450',
            },
            follow_redirects=True,
        )
        with app.app_context():
            payroll = db.session.get(Payroll, payroll_id)
            assert payroll.loan == 450
            assert payroll.sss_loan == 25
            assert payroll.pagibig_loan == 20
            assert payroll.total_deductions == 495
            assert payroll.net_pay == -495
            assert payroll.is_paid is False
    finally:
        with app.app_context():
            db.session.rollback()
            payroll = db.session.get(Payroll, payroll_id)
            staff = db.session.get(Employee, staff_id)
            admin = db.session.get(Employee, admin_id)
            if payroll:
                db.session.delete(payroll)
            if staff:
                db.session.delete(staff)
            if admin:
                db.session.delete(admin)
            db.session.commit()


def test_attendance_cutoff_report_searches_employee_and_includes_missing_punches():
    cutoff_start = date(2026, 10, 3)
    with app.app_context():
        admin = Employee(first_name='Attendance', last_name='Admin', role='admin')
        staff = Employee(
            first_name='Attendance',
            last_name='Report Staff',
            role='staff',
            company='Trece-Uno',
        )
        complete = Attendance(
            employee=staff,
            date=cutoff_start,
            clock_in=datetime(2026, 10, 3, 8, 25),
            clock_out=datetime(2026, 10, 3, 12, 0),
            status='Late',
            hours=3.58,
        )
        missing_punch = Attendance(
            employee=staff,
            date=cutoff_start + timedelta(days=1),
            clock_in=None,
            clock_out=None,
            status='No In / No Out',
            hours=0,
        )
        db.session.add_all([admin, staff, complete, missing_punch])
        db.session.commit()
        admin_id, staff_id = admin.id, staff.id

    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, admin_id))
        response = client.get(
            '/holiday_overtime?cutoff_start=2026-10-03&employee_search=Report'
        )
        assert response.status_code == 200
        assert b'Attendance Report by Cutoff' in response.data
        assert b'Attendance Report Staff' in response.data
        assert b'15' in response.data
        assert b'No In / No Out' not in response.data
        assert b'No In' in response.data
        assert b'No Out' in response.data

        csv_response = client.get(
            '/holiday_overtime?cutoff_start=2026-10-03'
            '&employee_search=Report&export=attendance_csv'
        )
        assert csv_response.status_code == 200
        assert b'Missing Clock In' in csv_response.data
        assert b'Attendance Report Staff' in csv_response.data
    finally:
        with app.app_context():
            db.session.rollback()
            staff = db.session.get(Employee, staff_id)
            admin = db.session.get(Employee, admin_id)
            if staff:
                Attendance.query.filter_by(employee_id=staff_id).delete()
                db.session.delete(staff)
            if admin:
                db.session.delete(admin)
            db.session.commit()


def test_unselected_staff_cannot_open_admin_payroll_routes():
    with app.app_context():
        staff = Employee(
            first_name='Payroll',
            last_name='Unselected',
            role='staff',
            company='Auto Expert',
            admin_payroll_access=False,
        )
        db.session.add(staff)
        db.session.commit()
        staff_id = staff.id
    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, staff_id))
        assert client.get('/payroll_dashboard').status_code == 403
        assert client.get('/payroll-loan-review').status_code == 403
        assert client.get('/admin/payroll-history').status_code == 403
        assert client.post(
            '/payroll/bulk-finalize',
            data={'cutoff_start': '2026-10-03', 'selected_employee_ids': ['1']},
        ).status_code == 403
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.commit()


def test_payroll_preparer_sees_only_company_summary_and_admin_summary_is_grouped():
    with app.app_context():
        preparer = Employee(
            first_name='Payroll',
            last_name='Preparer',
            role='staff',
            company='Auto Expert',
            payroll_preparation_access=True,
        )
        staff = Employee(
            first_name='Company',
            last_name='Staff',
            role='staff',
            company='Auto Expert',
        )
        admin = Employee(
            first_name='Company',
            last_name='Admin',
            role='admin',
            company='Auto Expert',
            payroll_attendance_exempt=False,
        )
        delegate = Employee(
            first_name='Payroll',
            last_name='LoanReviewer',
            role='staff',
            company='Auto Expert',
            admin_payroll_access=True,
        )
        db.session.add_all([preparer, staff, admin, delegate])
        db.session.commit()
        preparer_id, staff_id, admin_id, delegate_id = (
            preparer.id, staff.id, admin.id, delegate.id
        )
    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, preparer_id))
        own_summary = client.get(
            '/payroll/summary?company=Auto+Expert&view=true&cutoff_start=2026-10-03'
        )
        assert own_summary.status_code == 200
        assert b'AUTO EXPERT PAYROLL SUMMARY' in own_summary.data
        assert b'Company Staff' in own_summary.data
        assert b'Company Admin' not in own_summary.data
        assert b'Payroll Summary' in client.get('/dashboard_staff').data
        assert client.get(
            '/payroll/summary?company=Trece-Uno&view=true&cutoff_start=2026-10-03'
        ).status_code == 403

        with app.app_context():
            log_in(client, db.session.get(Employee, delegate_id))
        delegate_summary = client.get(
            '/payroll/summary?company=Auto+Expert&view=true&cutoff_start=2026-10-03'
        )
        assert delegate_summary.status_code == 200
        assert b'Company Staff' in delegate_summary.data
        assert b'Company Admin' in delegate_summary.data
        assert b'Admin Payroll' in delegate_summary.data
        assert b'Staff Payroll' in delegate_summary.data
        assert b'Cash Payroll Summary' in client.get('/dashboard_staff').data
        assert b'Payroll Loan Review' in client.get('/dashboard_staff').data
        assert client.get('/payroll_dashboard').status_code == 403
        assert client.get(
            '/payroll/summary?company=Trece-Uno&view=true&cutoff_start=2026-10-03'
        ).status_code == 403
        assert client.post(
            '/payroll/bulk-finalize',
            data={'cutoff_start': '2026-10-03', 'selected_employee_ids': ['1']},
        ).status_code == 403

        with app.app_context():
            log_in(client, db.session.get(Employee, admin_id))
        admin_summary = client.get(
            '/payroll/summary?company=Auto+Expert&view=true&cutoff_start=2026-10-03'
        )
        assert admin_summary.status_code == 200
        assert b'Admin Payroll' in admin_summary.data
        assert b'Staff Payroll' in admin_summary.data
        assert admin_summary.data.index(b'Admin Payroll') < admin_summary.data.index(
            b'Staff Payroll'
        )
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(Employee, preparer_id))
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.delete(db.session.get(Employee, admin_id))
            db.session.delete(db.session.get(Employee, delegate_id))
            db.session.commit()


def test_payroll_cutoff_picker_shows_date_ranges_and_save_preserves_selection():
    with app.app_context():
        admin = Employee(first_name='Cutoff', last_name='Admin', role='admin')
        staff = Employee(
            first_name='Rice',
            last_name='Default',
            role='staff',
            company='Trece-Uno',
            rice_allowance_per_day=0,
        )
        db.session.add_all([admin, staff])
        db.session.commit()
        admin_id, staff_id = admin.id, staff.id
    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, admin_id))
        response = client.get('/payroll_dashboard?cutoff_start=2026-10-03')
        assert response.status_code == 200
        assert b'Pumili ng payroll cutoff' in response.data
        assert b'Oct 03' in response.data
        assert b'Oct 09, 2026' in response.data
        assert b'De Minimis Ceiling (not extra pay)' in response.data
        assert b'value="95"' in response.data
        assert b'Rice Ceiling / Month' not in response.data
        assert b'Rice De Minimis' not in response.data

        response = client.post(
            '/payroll_dashboard',
            data={'cutoff_start': '2026-10-03'},
        )
        assert response.status_code == 302
        assert response.location.endswith(
            '/payroll_dashboard?cutoff_start=2026-10-03'
        )
    finally:
        with app.app_context():
            db.session.rollback()
            admin = db.session.get(Employee, admin_id)
            staff = db.session.get(Employee, staff_id)
            if admin:
                db.session.delete(admin)
            if staff:
                db.session.delete(staff)
            if admin or staff:
                db.session.commit()


def test_loan_reviewer_only_sees_and_updates_loan_fields():
    with app.app_context():
        delegate = Employee(
            first_name='Loan',
            last_name='Reviewer',
            role='staff',
            company='Trece-Uno',
            admin_payroll_access=True,
            loan_balance=900,
        )
        owner = Employee(
            first_name='Private',
            last_name='Owner',
            role='admin',
            company='Trece-Uno',
            payroll_attendance_exempt=True,
            loan_balance=5000,
        )
        trece_admin_staff = Employee(
            first_name='Trece',
            last_name='AdminRole',
            role='admin',
            company='Trece-Uno',
            payroll_attendance_exempt=False,
        )
        other_company_staff = Employee(
            first_name='Auto',
            last_name='ExpertStaff',
            role='staff',
            company='Auto Expert',
            loan_balance=1200,
        )
        db.session.add_all([
            delegate, owner, trece_admin_staff, other_company_staff
        ])
        db.session.flush()
        payroll = Payroll(
            employee_id=delegate.id,
            cutoff_start=date(2026, 10, 3),
            cutoff_end=date(2026, 10, 9),
            loan=150,
            gross_income=12345,
            net_pay=10000,
        )
        owner_payroll = Payroll(
            employee_id=owner.id,
            cutoff_start=date(2026, 10, 3),
            cutoff_end=date(2026, 10, 9),
            loan=200,
            gross_income=54321,
            net_pay=50000,
        )
        db.session.add_all([payroll, owner_payroll])
        db.session.commit()
        delegate_id, owner_id, trece_admin_staff_id, other_company_staff_id = (
            delegate.id, owner.id, trece_admin_staff.id, other_company_staff.id
        )
        payroll_id, owner_payroll_id = payroll.id, owner_payroll.id

    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, delegate_id))
        response = client.get('/payroll-loan-review?cutoff_start=2026-10-03')
        assert response.status_code == 200
        assert b'Loan Reviewer' in response.data
        assert b'900.00' in response.data
        assert b'Auto ExpertStaff' not in response.data
        assert b'1,200.00' not in response.data
        assert b'12,345' not in response.data
        assert b'Private Owner' not in response.data
        assert b'Trece AdminRole' not in response.data
        assert b'Admin Payroll' not in response.data
        assert client.get('/admin/payroll-history').status_code == 403
        summary = client.get(
            '/payroll/summary?company=Trece-Uno&view=true&cutoff_start=2026-10-03'
        )
        assert summary.status_code == 200
        assert b'Loan Reviewer' in summary.data
        assert b'Private Owner' not in summary.data
        assert b'Trece AdminRole' in summary.data
        assert b'Admin Payroll' in summary.data
        assert client.get(
            '/payroll/summary?company=Auto+Expert&view=true&cutoff_start=2026-10-03'
        ).status_code == 403

        response = client.post(
            '/payroll-loan-review',
            data={
                'cutoff_start': '2026-10-03',
                f'loan_balance_{delegate_id}': '700',
                f'loan_deduction_{delegate_id}': '350',
                f'loan_balance_{other_company_staff_id}': '0',
                f'loan_deduction_{other_company_staff_id}': '1200',
                f'daily_rate_{delegate_id}': '1',
            },
        )
        assert response.status_code == 302
        assert response.location.endswith(
            '/payroll-loan-review?cutoff_start=2026-10-03'
        )
        with app.app_context():
            delegate = db.session.get(Employee, delegate_id)
            other_company_staff = db.session.get(
                Employee, other_company_staff_id
            )
            payroll = db.session.get(Payroll, payroll_id)
            assert delegate.loan_balance == 700
            assert other_company_staff.loan_balance == 1200
            assert delegate.daily_rate == 695
            assert payroll.loan == 350
            assert payroll.gross_income == 12345
            assert payroll.net_pay == 10000

        assert client.post(
            '/payroll/bulk-finalize',
            data={'cutoff_start': '2026-10-03', 'selected_employee_ids': [str(delegate_id)]},
        ).status_code == 403
        assert client.post(
            f'/payroll/{delegate_id}/finalize',
            data={'cutoff_start': '2026-10-03'},
        ).status_code == 403

        admin_client = app.test_client()
        with app.app_context():
            log_in(admin_client, db.session.get(Employee, owner_id))
        admin_payroll = admin_client.get(
            '/payroll_dashboard?cutoff_start=2026-10-03'
        )
        assert admin_payroll.status_code == 200
        assert b'Private Owner' not in admin_payroll.data
        assert b'Owner Monthly Contributions' not in admin_payroll.data
        assert b'Owner payroll is kept' not in admin_payroll.data
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(Payroll, payroll_id))
            db.session.delete(db.session.get(Payroll, owner_payroll_id))
            db.session.delete(db.session.get(Employee, delegate_id))
            db.session.delete(db.session.get(Employee, owner_id))
            db.session.delete(db.session.get(Employee, trece_admin_staff_id))
            db.session.delete(db.session.get(Employee, other_company_staff_id))
            db.session.commit()
