from datetime import date, datetime, timedelta
from inspect import unwrap
from unittest.mock import patch

from hris import app, finalize_payroll_record
from models import AuditLog, Attendance, Employee, Payroll, PayslipVerification, db


START = date(2026, 10, 31)
END = START + timedelta(days=7)


def login(client, employee_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True


def worked(employee_id, day):
    return Attendance(employee_id=employee_id, date=day, status='Present', company='Trece-Uno',
                      clock_in=datetime.combine(day, datetime.min.time()).replace(hour=8),
                      clock_out=datetime.combine(day, datetime.min.time()).replace(hour=17))


def test_card_shows_change_and_one_click_update_refinalizes_without_double_loan():
    with app.app_context():
        admin = Employee(first_name='Update', last_name='Admin', role='admin')
        staff = Employee(first_name='Update', last_name='Staff', role='staff', company='Trece-Uno',
                         daily_rate=645, loan_balance=1000)
        db.session.add_all([admin, staff])
        db.session.flush()
        db.session.add_all([worked(staff.id, date(2026, 10, 31)), worked(staff.id, date(2026, 11, 3))])
        record = Payroll(employee_id=staff.id, cutoff_start=START, cutoff_end=END - timedelta(days=1))
        db.session.add(record)
        db.session.flush()
        finalize_payroll_record(staff, record, START, END)
        db.session.commit()
        ids = admin.id, staff.id
        old_net, record_id = float(record.net_pay), record.id
        assert db.session.get(Employee, staff.id).loan_balance == 500
        # Approved correction / added attendance after finalizing.
        db.session.add(worked(staff.id, date(2026, 11, 4)))
        db.session.commit()

    patched = {'completed_cutoff': lambda today=None: (START, END)}
    try:
        client = app.test_client()
        login(client, ids[0])
        dashboard_globals = unwrap(app.view_functions['payroll_dashboard']).__globals__
        update_globals = unwrap(app.view_functions['update_finalized_payroll']).__globals__
        with patch.dict(dashboard_globals, patched), patch.dict(update_globals, patched):
            html = client.get(f'/payroll_dashboard?cutoff_start={START}').get_data(as_text=True)
            assert 'Nagbago ang attendance pagkatapos i-finalize' in html
            assert f'/payroll/{ids[1]}/update-finalized' in html
            response = client.post(f'/payroll/{ids[1]}/update-finalized', data={'cutoff_start': START.isoformat()})
            assert response.status_code == 302
            html = client.get(f'/payroll_dashboard?cutoff_start={START}').get_data(as_text=True)
            assert f'/payroll/{ids[1]}/update-finalized' not in html
        with app.app_context():
            record = db.session.get(Payroll, record_id)
            assert record.is_paid and record.loan_deduction_applied
            assert float(record.net_pay) > old_net
            assert float(record.loan) == 500
            assert db.session.get(Employee, ids[1]).loan_balance == 500
    finally:
        with app.app_context():
            db.session.rollback()
            for model in (PayslipVerification, Attendance, Payroll, AuditLog):
                model.query.filter(model.employee_id.in_(ids)).delete(synchronize_session=False)
            Employee.query.filter(Employee.id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()
