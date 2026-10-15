from types import SimpleNamespace
from unittest.mock import patch
from datetime import date

import pytest

from hris import app, apply_approved_loan_balance
from models import (
    AuditLog,
    Employee,
    Loan,
    LoanDeductionSkipRequest,
    Payroll,
    db,
)


def test_approved_loan_updates_outstanding_once():
    employee = SimpleNamespace(loan_balance=1000.0)
    loan = SimpleNamespace(employee_id=7, amount=500.0, balance_applied=False)

    with patch("hris.db.session.get", return_value=employee):
        assert apply_approved_loan_balance(loan) is True
        assert apply_approved_loan_balance(loan) is False

    assert employee.loan_balance == 1500.0
    assert loan.balance_applied is True


@pytest.fixture
def loan_account():
    with app.app_context():
        admin = Employee(first_name='Loan', last_name='Manager', role='admin')
        staff = Employee(
            first_name='Loan', last_name='Applicant', role='staff',
            company='Trece-Uno', loan_balance=200,
        )
        db.session.add_all([admin, staff])
        db.session.flush()
        request = Loan(
            employee_id=staff.id, amount=1000, reason='Test request',
            date_needed=date(2026, 10, 12), status='Pending',
        )
        db.session.add(request)
        db.session.commit()
        ids = admin.id, staff.id, request.id
    try:
        yield ids
    finally:
        with app.app_context():
            db.session.rollback()
            Loan.query.filter_by(id=ids[2]).delete()
            AuditLog.query.filter(AuditLog.employee_id.in_(ids[:2])).delete(synchronize_session=False)
            Employee.query.filter(Employee.id.in_(ids[:2])).delete(synchronize_session=False)
            db.session.commit()


def authenticated_client(employee_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True
    return client


def test_payroll_shared_reviewer_can_approve_skip_for_one_cutoff(loan_account):
    admin_id, staff_id, _ = loan_account
    cutoff_start = date(2026, 10, 10)
    cutoff_end = date(2026, 10, 16)
    reviewer_id = None
    with app.app_context():
        reviewer = Employee(
            first_name='Payroll', last_name='Reviewer', role='staff',
            company='Trece-Uno', admin_payroll_access=True,
        )
        payroll_record = Payroll(
            employee_id=staff_id,
            cutoff_start=cutoff_start,
            cutoff_end=cutoff_end,
            loan=200,
        )
        db.session.add_all([reviewer, payroll_record])
        db.session.commit()
        reviewer_id = reviewer.id

    try:
        staff_client = authenticated_client(staff_id)
        with patch('hris.current_payroll_cutoff', return_value=(cutoff_start, cutoff_end)):
            with patch('hris.notify_admins'):
                response = staff_client.post(
                    '/loan',
                    data={
                        'action': 'request_deduction_skip',
                        'skip_reason': 'Unexpected emergency expense.',
                    },
                )
        assert response.status_code == 302

        reviewer_client = authenticated_client(reviewer_id)
        review_page = reviewer_client.get(
            f'/payroll-loan-review?cutoff_start={cutoff_start.isoformat()}'
        )
        assert review_page.status_code == 200
        assert b'Requests to Skip Loan Deduction' in review_page.data
        assert b'Unexpected emergency expense.' in review_page.data

        with app.app_context():
            skip_request = LoanDeductionSkipRequest.query.filter_by(
                employee_id=staff_id,
                cutoff_start=cutoff_start,
            ).one()
            request_id = skip_request.id
        response = reviewer_client.post(
            f'/loan/deduction-skip/{request_id}/approve'
        )
        assert response.status_code == 302
        with app.app_context():
            skip_request = db.session.get(LoanDeductionSkipRequest, request_id)
            payroll_record = Payroll.query.filter_by(
                employee_id=staff_id,
                cutoff_start=cutoff_start,
            ).one()
            assert skip_request.status == 'Approved'
            assert payroll_record.loan == 0
            assert db.session.get(Employee, staff_id).loan_balance == 200

        response = reviewer_client.post(
            '/payroll-loan-review',
            data={
                'cutoff_start': cutoff_start.isoformat(),
                f'loan_balance_{staff_id}': '200',
                f'loan_deduction_{staff_id}': '200',
            },
        )
        assert response.status_code == 302
        with app.app_context():
            payroll_record = Payroll.query.filter_by(
                employee_id=staff_id,
                cutoff_start=cutoff_start,
            ).one()
            assert payroll_record.loan == 0
            assert db.session.get(Employee, staff_id).loan_balance == 200
    finally:
        with app.app_context():
            db.session.rollback()
            LoanDeductionSkipRequest.query.filter_by(employee_id=staff_id).delete()
            Payroll.query.filter_by(employee_id=staff_id, cutoff_start=cutoff_start).delete()
            reviewer = Employee.query.filter_by(
                id=reviewer_id,
            ).first() if reviewer_id else None
            if reviewer:
                db.session.delete(reviewer)
            db.session.commit()


def test_pending_approval_credits_employee_once_and_cannot_be_rejected(loan_account):
    admin_id, staff_id, loan_id = loan_account
    client = authenticated_client(admin_id)
    with app.app_context():
        assert db.session.get(Employee, staff_id).loan_balance == 200
    with patch('hris.send_loan_email') as email:
        assert client.get(f'/approve_loan/{loan_id}').status_code == 405
        response = client.post(f'/approve_loan/{loan_id}')
        assert response.location.endswith('/loan')
        client.post(f'/approve_loan/{loan_id}')
        client.post(f'/reject_loan/{loan_id}', data={'reason': 'Late decline'})
        assert email.call_count == 1
    with app.app_context():
        assert db.session.get(Employee, staff_id).loan_balance == 1200
        saved = db.session.get(Loan, loan_id)
        assert saved.status == 'Approved'
        assert saved.balance_applied is True
    staff_page = authenticated_client(staff_id).get('/loan')
    assert staff_page.status_code == 200
    assert b'1,200.00' in staff_page.data
    assert b'Approved' in staff_page.data


def test_edit_remaining_balance_persists_without_approving_pending_loan(loan_account):
    admin_id, staff_id, loan_id = loan_account
    client = authenticated_client(admin_id)
    page = client.get('/loan')
    assert b'Edit Remaining Loan' in page.data
    assert f'/loan/balance/{staff_id}'.encode() in page.data
    response = client.post(
        f'/loan/balance/{staff_id}',
        data={'balance': '650.50', 'previous_balance': '200'},
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Employee, staff_id).loan_balance == 650.5
        assert db.session.get(Loan, loan_id).status == 'Pending'
        assert AuditLog.query.filter(
            AuditLog.action.like(f'Loan balance employee #{staff_id}:%')
        ).count() == 1
    assert b'650.50' in authenticated_client(staff_id).get('/loan').data
    client.post(
        f'/loan/balance/{staff_id}',
        data={'balance': '0', 'previous_balance': '200'},
    )
    with app.app_context():
        assert db.session.get(Employee, staff_id).loan_balance == 650.5


@pytest.mark.parametrize('balance', ['-1', 'nan', 'inf', 'invalid', ''])
def test_invalid_balance_is_not_saved(loan_account, balance):
    admin_id, staff_id, _ = loan_account
    response = authenticated_client(admin_id).post(
        f'/loan/balance/{staff_id}',
        data={'balance': balance, 'previous_balance': '200'}, follow_redirects=True,
    )
    assert b'valid non-negative' in response.data
    with app.app_context():
        assert db.session.get(Employee, staff_id).loan_balance == 200


def test_staff_cannot_edit_or_approve_and_legacy_get_does_not_approve(loan_account):
    admin_id, staff_id, loan_id = loan_account
    staff = authenticated_client(staff_id)
    assert staff.post(
        f'/loan/balance/{staff_id}', data={'balance': '0', 'previous_balance': '200'},
    ).status_code == 403
    staff.post(f'/approve_loan/{loan_id}')
    authenticated_client(admin_id).get(f'/loan?action=approve&loan_id={loan_id}')
    with app.app_context():
        assert db.session.get(Loan, loan_id).status == 'Pending'
        assert db.session.get(Employee, staff_id).loan_balance == 200


def test_decline_pending_request_does_not_credit_balance(loan_account):
    admin_id, staff_id, loan_id = loan_account
    client = authenticated_client(admin_id)
    client.post(f'/reject_loan/{loan_id}', data={'reason': ''})
    with patch('hris.send_loan_email'):
        client.post(f'/reject_loan/{loan_id}', data={'reason': 'Not released'})
        client.post(f'/approve_loan/{loan_id}')
    with app.app_context():
        assert db.session.get(Employee, staff_id).loan_balance == 200
        assert db.session.get(Loan, loan_id).status == 'Rejected'
def test_remaining_loan_after_each_deduction_accounts_for_new_loans():
    from datetime import date, datetime
    from types import SimpleNamespace
    from hris import loan_remaining_after_deductions

    def credited(amount, on):
        return SimpleNamespace(
            amount=amount, loan_type=None, decision_date=datetime.combine(on, datetime.min.time()),
            date_filed=datetime.combine(on, datetime.min.time()), date_needed=on,
        )

    loans = [credited(3000, date(2026, 9, 1)), credited(1000, date(2026, 9, 28))]
    deductions = [
        SimpleNamespace(id=1, loan=500, cutoff_start=date(2026, 9, 19), cutoff_end=date(2026, 9, 25)),
        SimpleNamespace(id=2, loan=500, cutoff_start=date(2026, 9, 26), cutoff_end=date(2026, 10, 2)),
        SimpleNamespace(id=3, loan=500, cutoff_start=date(2026, 10, 3), cutoff_end=date(2026, 10, 9)),
    ]

    assert loan_remaining_after_deductions(2500, loans, deductions) == {1: 2500, 2: 3000, 3: 2500}


def test_admin_can_delete_duplicate_approved_loan_and_balance_is_reversed(loan_account):
    admin_id, staff_id, loan_id = loan_account
    client = authenticated_client(admin_id)
    with patch('hris.send_loan_email'):
        client.post(f'/approve_loan/{loan_id}')
    page = client.get('/loan').get_data(as_text=True)
    assert 'loan-employee-search' in page
    assert f'/loan/{loan_id}/delete' in page
    assert authenticated_client(staff_id).post(f'/loan/{loan_id}/delete').status_code == 403
    response = client.post(f'/loan/{loan_id}/delete')
    assert response.location.endswith('/loan')
    with app.app_context():
        assert db.session.get(Loan, loan_id) is None
        assert db.session.get(Employee, staff_id).loan_balance == 200
        assert AuditLog.query.filter(
            AuditLog.employee_id == admin_id,
            AuditLog.action.like(f'Deleted LOAN-{loan_id:06d}%'),
        ).count() == 1


def test_deleting_pending_loan_keeps_balance(loan_account):
    admin_id, staff_id, loan_id = loan_account
    authenticated_client(admin_id).post(f'/loan/{loan_id}/delete')
    with app.app_context():
        assert db.session.get(Loan, loan_id) is None
        assert db.session.get(Employee, staff_id).loan_balance == 200
