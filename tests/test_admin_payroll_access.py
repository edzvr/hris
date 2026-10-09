from datetime import date

from hris import app
from models import Employee, Payroll, db


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
        )
        db.session.add_all([admin, staff])
        db.session.commit()
        admin_id, staff_id = admin.id, staff.id
    try:
        client = app.test_client()
        with app.app_context():
            admin = db.session.get(Employee, admin_id)
            log_in(client, admin)
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
            },
        )
        assert response.status_code == 302
        with app.app_context():
            staff = db.session.get(Employee, staff_id)
            assert staff.admin_payroll_access is True
            log_in(client, staff)
        assert client.get('/payroll_dashboard').status_code == 403
        assert client.get('/payroll-loan-review').status_code == 200
        assert b'Review Loan Deductions' in client.get('/dashboard_staff').data
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(Employee, staff_id))
            db.session.delete(db.session.get(Employee, admin_id))
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


def test_payroll_cutoff_picker_shows_date_ranges_and_save_preserves_selection():
    with app.app_context():
        admin = Employee(first_name='Cutoff', last_name='Admin', role='admin')
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id
    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, admin_id))
        response = client.get('/payroll_dashboard?cutoff_start=2026-10-03')
        assert response.status_code == 200
        assert b'Pumili ng payroll cutoff' in response.data
        assert b'Oct 03' in response.data
        assert b'Oct 09, 2026' in response.data
        assert b'De Minimis (combined monthly ceiling)' in response.data
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
            if admin:
                db.session.delete(admin)
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
        db.session.add_all([delegate, owner])
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
        delegate_id, owner_id = delegate.id, owner.id
        payroll_id, owner_payroll_id = payroll.id, owner_payroll.id

    try:
        client = app.test_client()
        with app.app_context():
            log_in(client, db.session.get(Employee, delegate_id))
        response = client.get('/payroll-loan-review?cutoff_start=2026-10-03')
        assert response.status_code == 200
        assert b'Loan Reviewer' in response.data
        assert b'900.00' in response.data
        assert b'12,345' not in response.data
        assert b'Private Owner' not in response.data
        assert b'Admin Payroll' not in response.data
        assert client.get('/admin/payroll-history').status_code == 403
        assert client.get('/payroll/summary?company=Trece-Uno').status_code == 403

        response = client.post(
            '/payroll-loan-review',
            data={
                'cutoff_start': '2026-10-03',
                f'loan_balance_{delegate_id}': '700',
                f'loan_deduction_{delegate_id}': '350',
                f'daily_rate_{delegate_id}': '1',
            },
        )
        assert response.status_code == 302
        assert response.location.endswith(
            '/payroll-loan-review?cutoff_start=2026-10-03'
        )
        with app.app_context():
            delegate = db.session.get(Employee, delegate_id)
            payroll = db.session.get(Payroll, payroll_id)
            assert delegate.loan_balance == 700
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
            db.session.commit()
