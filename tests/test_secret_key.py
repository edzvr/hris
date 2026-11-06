import hashlib
from datetime import date

import hris
from hris import app, db
from models import Employee, PayslipVerification


def test_weak_secret_key_is_replaced_with_persistent_random_key(monkeypatch):
    with app.app_context():
        monkeypatch.setattr(app, 'secret_key', 'secret')
        hris.ensure_app_secret_key()
        first_key = app.secret_key
        monkeypatch.setattr(app, 'secret_key', '')
        hris.ensure_app_secret_key()

        assert first_key not in hris.WEAK_SECRET_KEYS
        assert len(first_key) == 64
        assert app.secret_key == first_key


def test_strong_environment_secret_key_is_kept(monkeypatch):
    with app.app_context():
        monkeypatch.setattr(app, 'secret_key', 'x' * 40)
        hris.ensure_app_secret_key()
        assert app.secret_key == 'x' * 40


def test_qr_signed_with_legacy_key_still_verifies():
    with app.app_context():
        employee = Employee.query.filter(Employee.company.isnot(None)).first()
        label = 'Legacy-Secret-Test'
        payload = (
            f"payroll_summary|{employee.id}|{label}|none|{date(2026, 10, 3)}|"
            f"{date(2026, 10, 9)}|100.0|secret"
        )
        record = PayslipVerification(
            employee_id=employee.id, document_type='payroll_summary',
            document_id='SEC-LEGACY1', document_label=label, net_pay=100.0,
            cutoff_start=date(2026, 10, 3), cutoff_end=date(2026, 10, 9),
            verification_hash=hashlib.sha256(payload.encode()).hexdigest(),
        )
        db.session.add(record)
        db.session.commit()
        try:
            response = app.test_client().get('/verify-document/SEC-LEGACY1')
            assert b'This document is authentic' in response.data
        finally:
            db.session.delete(record)
            db.session.commit()


def test_payslip_qr_is_invalid_after_payroll_is_reopened_or_changed():
    from models import Payroll
    with app.app_context():
        employee = Employee(first_name='Qr', last_name='Release', role='staff', company='Trece-Uno')
        payroll = Payroll(employee=employee, cutoff_start=date(2026, 10, 3), cutoff_end=date(2026, 10, 9),
                          is_paid=True, gross_income=4000, total_deductions=500, net_pay=3500)
        db.session.add_all([employee, payroll])
        db.session.commit()
        with app.test_request_context():
            doc_id = hris.build_payslip_verification(
                employee.id, payroll.id, payroll.cutoff_start, payroll.cutoff_end, payroll.net_pay)['document_id']
        client = app.test_client()
        try:
            ok = client.get(f'/verify-document/{doc_id}').data
            assert b'This document is authentic' in ok and b'OK to release: PHP 3500.00' in ok
            payroll.net_pay = 3400
            db.session.commit()
            changed = client.get(f'/verify-document/{doc_id}').data
            assert b'DO NOT RELEASE' in changed and b'OK to release' not in changed
            payroll.net_pay, payroll.is_paid = 3500, False
            db.session.commit()
            assert b'DO NOT RELEASE' in client.get(f'/verify-document/{doc_id}').data
        finally:
            PayslipVerification.query.filter_by(document_id=doc_id).delete()
            db.session.delete(payroll)
            db.session.delete(employee)
            db.session.commit()
