from datetime import date, datetime

from hris import app, auto_finalize_ready_payroll
from models import Attendance, Employee, LeaveRequest, Payroll, db

FRIDAY = date(2031, 1, 10)
CUTOFF_START = date(2031, 1, 4)


def at(day, hour):
    return datetime.combine(day, datetime.min.time()).replace(hour=hour)


def worked(employee, day, clock_out=True):
    return Attendance(employee_id=employee.id, date=day, clock_in=at(day, 8),
                      clock_out=at(day, 17) if clock_out else None,
                      status='Present', company='Trece-Uno')


def test_auto_finalize_only_finalizes_ready_payroll():
    with app.app_context():
        ready = Employee(first_name='Auto', last_name='Ready', role='staff', company='Trece-Uno', daily_rate=645)
        open_punch = Employee(first_name='Auto', last_name='OpenPunch', role='staff', company='Trece-Uno', daily_rate=645)
        on_leave = Employee(first_name='Auto', last_name='PendingLeave', role='staff', company='Trece-Uno', daily_rate=645)
        no_rate = Employee(first_name='Auto', last_name='NoRate', role='staff', company='Trece-Uno', daily_rate=0)
        people = [ready, open_punch, on_leave, no_rate]
        db.session.add_all(people)
        db.session.flush()
        db.session.add_all([
            worked(ready, date(2031, 1, 6)), worked(ready, date(2031, 1, 7)),
            worked(open_punch, date(2031, 1, 6)), worked(open_punch, date(2031, 1, 7), clock_out=False),
            worked(on_leave, date(2031, 1, 6)),
            LeaveRequest(employee_id=on_leave.id, leave_type='SIL', days=1, status='Pending',
                         start_date=date(2031, 1, 8), end_date=date(2031, 1, 8)),
            worked(no_rate, date(2031, 1, 6)),
        ])
        db.session.commit()
        ids = [person.id for person in people]
        try:
            finalized, skipped = auto_finalize_ready_payroll(FRIDAY)
            assert 'Auto Ready' in finalized
            assert skipped['Auto Openpunch'] == ['Incomplete clock in/out']
            assert skipped['Auto Pendingleave'] == ['Leave request pending']
            assert 'Missing daily rate' in skipped['Auto Norate']

            records = {r.employee_id: r for r in Payroll.query.filter(
                Payroll.employee_id.in_(ids), Payroll.cutoff_start == CUTOFF_START).all()}
            assert set(records) == {ready.id}
            assert records[ready.id].is_paid and records[ready.id].net_pay > 0

            # Running again never re-finalizes or duplicates.
            again, _ = auto_finalize_ready_payroll(FRIDAY)
            assert 'Auto Ready' not in again
            assert Payroll.query.filter_by(employee_id=ready.id).count() == 1
        finally:
            db.session.rollback()
            for model in (Payroll, Attendance, LeaveRequest):
                model.query.filter(model.employee_id.in_(ids)).delete(synchronize_session=False)
            Employee.query.filter(Employee.id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()
