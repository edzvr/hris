from datetime import date
from io import BytesIO

from pypdf import PdfReader

from hris import app
from models import Employee, Loan, db


def test_admin_generates_agreement_with_approved_loan_amount_and_reason():
    with app.app_context():
        admin = Employee(first_name='Loan', last_name='Admin', role='admin')
        employee = Employee(first_name='Loan', last_name='Borrower', role='staff')
        db.session.add_all([admin, employee])
        db.session.flush()
        loan = Loan(
            employee_id=employee.id,
            amount=2750.00,
            date_needed=date(2026, 10, 20),
            reason='Emergency home repair',
            status='Approved',
        )
        db.session.add(loan)
        db.session.commit()
        admin_id, employee_id, loan_id = admin.id, employee.id, loan.id

    try:
        client = app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(admin_id)
            session['_fresh'] = True

        preview = client.get(f'/loan/{loan_id}/agreement')
        assert preview.status_code == 200
        assert b'PHP 2750.00' in preview.data
        assert b'Emergency home repair' in preview.data

        response = client.post(
            f'/loan/{loan_id}/agreement',
            data={'deduction_per_cutoff': '425.00'},
        )
        assert response.status_code == 200
        assert response.mimetype == 'application/pdf'
        pdf_text = '\n'.join(
            page.extract_text() or ''
            for page in PdfReader(BytesIO(response.data)).pages
        )
        assert 'PHP 2,750.00' in pdf_text
        assert 'Emergency home repair' in pdf_text
        assert '425.00 pesos' in pdf_text
        assert 'Borrower/Employee' in pdf_text

        staff_client = app.test_client()
        with staff_client.session_transaction() as session:
            session['_user_id'] = str(employee_id)
            session['_fresh'] = True
        assert staff_client.get(f'/loan/{loan_id}/agreement').status_code == 403
    finally:
        with app.app_context():
            db.session.rollback()
            db.session.delete(db.session.get(Loan, loan_id))
            db.session.delete(db.session.get(Employee, employee_id))
            db.session.delete(db.session.get(Employee, admin_id))
            db.session.commit()
