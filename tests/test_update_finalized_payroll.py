from datetime import date, datetime, time, timedelta
import json
from html.parser import HTMLParser
from inspect import unwrap
from unittest.mock import patch

import pytest

import hris
from hris import app, finalize_payroll_record
from models import (
    AuditLog, Attendance, AttendanceCorrection, Employee, EmployeeLiability, LeaveRequest,
    LoanDeductionSkipRequest, OTApplication, Payroll, PayslipVerification, db,
)


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


class DashboardCards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = {}
        self.card = None
        self.action = None
        self.details_depth = 0
        self.cutoff_value = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('type') == 'hidden' and attrs.get('name') == 'cutoff_start':
            self.cutoff_value = attrs.get('value')
        if tag == 'article' and 'payroll-row' in attrs.get('class', '').split():
            self.card = {'actions': [], 'text': []}
            self.cards[attrs['data-employee']] = self.card
        if self.card is None:
            return
        if tag == 'details':
            self.details_depth += 1
        elif tag in ('button', 'a'):
            self.action = {'attrs': attrs, 'depth': self.details_depth, 'text': []}
            self.card['actions'].append(self.action)

    def handle_endtag(self, tag):
        if self.card is None:
            return
        if tag in ('button', 'a'):
            self.action = None
        elif tag == 'details':
            self.details_depth -= 1
        elif tag == 'article':
            self.card = None

    def handle_data(self, data):
        if self.card is not None:
            self.card['text'].append(data)
            if self.action is not None:
                self.action['text'].append(data)


def dashboard_card(client, employee_id, cutoff=START):
    response = client.get(f'/payroll_dashboard?cutoff_start={cutoff.isoformat()}')
    assert response.status_code == 200
    parser = DashboardCards()
    parser.feed(response.get_data(as_text=True))
    return parser.cards[str(employee_id)], parser.cutoff_value


def card_action(card, attr, value):
    return next(
        (action for action in card['actions'] if action['attrs'].get(attr) == value),
        None,
    )


@pytest.fixture
def finalized_case(request, monkeypatch):
    monkeypatch.setattr(hris, 'completed_cutoff', lambda today=None: (START, END))
    with app.app_context():
        admin = Employee(first_name='Card', last_name='Manager', role='admin')
        employee = Employee(
            first_name='Card', last_name='Staff', role='staff', company='Trece-Uno',
            daily_rate=645, loan_balance=1000,
        )
        db.session.add_all([admin, employee])
        db.session.flush()
        db.session.add_all([
            worked(employee.id, date(2026, 10, 31)),
            worked(employee.id, date(2026, 11, 3)),
        ])
        if getattr(request, 'param', False):
            db.session.add(EmployeeLiability(
                employee_id=employee.id, description='Payroll test',
                total_amount=100, deduction_per_cutoff=100,
                amount_deducted=0, amount_recovered=0,
                status='Active', acknowledgment_status='Acknowledged',
            ))
        record = Payroll(
            employee_id=employee.id, cutoff_start=START,
            cutoff_end=END - timedelta(days=1),
        )
        db.session.add(record)
        db.session.flush()
        finalize_payroll_record(employee, record, START, END)
        db.session.commit()
        admin_id, employee_id, payroll_id = admin.id, employee.id, record.id
    client = app.test_client()
    login(client, admin_id)
    try:
        yield client, admin_id, employee_id, payroll_id
    finally:
        with app.app_context():
            db.session.rollback()
            for model in (
                PayslipVerification, AttendanceCorrection, LoanDeductionSkipRequest,
                OTApplication, LeaveRequest, Attendance, Payroll,
                EmployeeLiability, AuditLog,
            ):
                model.query.filter(model.employee_id.in_([admin_id, employee_id])).delete(
                    synchronize_session=False
                )
            Employee.query.filter(Employee.id.in_([admin_id, employee_id])).delete(
                synchronize_session=False
            )
            db.session.commit()


def test_finalized_card_shows_recalculate_outside_details_when_net_is_unchanged(finalized_case):
    client, _, employee_id, payroll_id = finalized_case
    with app.app_context():
        employee = db.session.get(Employee, employee_id)
        record = db.session.get(Payroll, payroll_id)
        preview = hris.finalized_payroll_update_preview(employee, record, START, END)
        assert preview['net_pay'] == pytest.approx(record.net_pay)
    card, cutoff_value = dashboard_card(client, employee_id)
    action = card_action(card, 'formaction', f'/payroll/{employee_id}/update-finalized')
    assert action is not None
    assert action['depth'] == 0
    assert 'disabled' not in action['attrs']
    assert 'Recalculate' in ''.join(action['text'])
    assert cutoff_value == START.isoformat()


def test_staff_card_shows_pay_breakdown_and_direct_edit_action(finalized_case):
    client, _, employee_id, _ = finalized_case
    card, _ = dashboard_card(client, employee_id)
    text = ' '.join(card['text'])
    assert 'Kita' in text and 'Mga bawas' in text
    assert 'Basic pay' in text and 'Company loan' in text
    assert 'Mabilisang edit' not in text
    assert 'View / Edit Details' not in text
    assert card_action(card, 'name', 'save_employee_id') is None


def test_draft_staff_card_saves_only_selected_employee_then_opens_preview(finalized_case):
    client, _, employee_id, _ = finalized_case
    with app.app_context():
        record = db.session.get(Payroll, Payroll.query.filter_by(employee_id=employee_id).one().id)
        record.is_paid = False
        record.loan_deduction_applied = False
        record.liability_deduction_applied = False
        employee = db.session.get(Employee, employee_id)
        employee.loan_balance = 1000
        other = Employee(
            first_name='Unselected', last_name='Card', role='staff',
            company='Trece-Uno', daily_rate=600, allowance=15,
        )
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    card, _ = dashboard_card(client, employee_id)
    action = card_action(card, 'name', 'save_employee_id')
    assert action is not None and action['attrs']['value'] == str(employee_id)
    text = ' '.join(card['text'])
    assert 'Mabilisang edit' in text and 'Save & Review Preview' in text
    response = client.post('/payroll_dashboard', data={
        'cutoff_start': START.isoformat(),
        'save_employee_id': str(employee_id),
        f'daily_rate_{employee_id}': '700',
        f'allowance_{employee_id}': '120',
        f'incentives_{employee_id}': '50',
        f'sss_loan_{employee_id}': '75',
        f'pagibig_loan_{employee_id}': '0',
        f'cash_advance_{employee_id}': '20',
        f'other_deductions_{employee_id}': '10',
    })
    assert response.status_code == 302
    assert response.location.endswith(
        f'/payroll/{employee_id}/recalculate?cutoff_start={START.isoformat()}'
    )
    with app.app_context():
        employee = db.session.get(Employee, employee_id)
        record = db.session.get(Payroll, Payroll.query.filter_by(employee_id=employee_id).one().id)
        other = db.session.get(Employee, other_id)
        assert employee.daily_rate == 700 and employee.allowance == 120
        assert employee.incentives == 50 and employee.loan_balance == 1000
        assert record.sss_loan == 75
        assert record.cash_advance == 20 and record.other_deductions == 10
        assert other.daily_rate == 600 and other.allowance == 15
    assert client.get(response.location).status_code == 200


def test_invalid_quick_edit_employee_id_does_not_run_bulk_save(finalized_case):
    client, _, employee_id, _ = finalized_case
    response = client.post('/payroll_dashboard', data={
        'cutoff_start': START.isoformat(),
        'save_employee_id': 'not-an-employee-id',
        f'daily_rate_{employee_id}': '999',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'Select a valid employee to save.' in response.data
    with app.app_context():
        assert db.session.get(Employee, employee_id).daily_rate == 645


def test_missing_clock_out_is_visible_and_blocks_recalculation_even_if_net_is_unchanged(finalized_case):
    client, _, employee_id, payroll_id = finalized_case
    with app.app_context():
        incomplete = worked(employee_id, date(2026, 11, 4))
        incomplete.clock_out = None
        db.session.add(incomplete)
        db.session.commit()
        old_net = db.session.get(Payroll, payroll_id).net_pay
        old_balance = db.session.get(Employee, employee_id).loan_balance
    card, cutoff_value = dashboard_card(client, employee_id)
    action = card_action(card, 'formaction', f'/payroll/{employee_id}/update-finalized')
    assert action is not None and action['depth'] == 0
    assert 'disabled' in action['attrs']
    assert 'Incomplete clock in/out' in ' '.join(card['text'])
    response = client.post(
        f'/payroll/{employee_id}/update-finalized',
        data={'cutoff_start': cutoff_value},
        follow_redirects=True,
    )
    assert b'Incomplete clock in/out' in response.data
    with app.app_context():
        record = db.session.get(Payroll, payroll_id)
        assert record.is_paid and record.net_pay == old_net
        assert db.session.get(Employee, employee_id).loan_balance == old_balance


def test_approved_clock_out_correction_automatically_updates_finalized_salary(finalized_case):
    client, _, employee_id, payroll_id = finalized_case
    with app.app_context():
        incomplete = worked(employee_id, date(2026, 11, 4))
        incomplete.clock_out = None
        db.session.add(incomplete)
        db.session.flush()
        correction = AttendanceCorrection(
            employee_id=employee_id, attendance_id=incomplete.id,
            correction_date=incomplete.date, requested_clock_out=datetime(2026, 11, 4, 17),
            reason='Missing clock out',
        )
        db.session.add(correction)
        db.session.commit()
        correction_id = correction.id
        old_net = db.session.get(Payroll, payroll_id).net_pay
    card, _ = dashboard_card(client, employee_id)
    assert 'Incomplete clock in/out' in ' '.join(card['text'])
    with patch('hris.send_notification_email'):
        response = client.post(f'/admin/attendance-correction/{correction_id}/approve')
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert any(
            'automatically updated and finalized' in message
            for _, message in session.get('_flashes', [])
        )
    card, cutoff_value = dashboard_card(client, employee_id)
    action = card_action(card, 'formaction', f'/payroll/{employee_id}/update-finalized')
    assert action is not None and action['depth'] == 0 and 'disabled' not in action['attrs']
    assert 'Attendance changed since saved payroll' not in ' '.join(card['text'])
    with app.app_context():
        record = db.session.get(Payroll, payroll_id)
        assert record.is_paid and record.net_pay > old_net
        assert db.session.get(Employee, employee_id).loan_balance == 500
        assert db.session.get(AttendanceCorrection, correction_id).status == 'Approved'
        review = hris.payroll_review_checklist(
            db.session.get(Employee, employee_id), record, START, END,
        )
        assert not review['attendance_changed'] and not review['alerts']
    card, _ = dashboard_card(client, employee_id)
    assert 'Attendance changed since saved payroll' not in ' '.join(card['text'])
    assert b'Saved payroll alerts' not in client.get('/dashboard_admin?company=Trece-Uno').data
    with patch('hris.send_notification_email') as notify:
        assert client.post(f'/admin/attendance-correction/{correction_id}/approve').status_code == 302
        notify.assert_not_called()
    with app.app_context():
        assert db.session.get(Employee, employee_id).loan_balance == 500


def test_same_pay_correction_still_alerts_after_reload_and_cutoff_rollover(finalized_case, monkeypatch):
    client, admin_id, employee_id, payroll_id = finalized_case
    with app.app_context():
        attendance = Attendance.query.filter_by(employee_id=employee_id).first()
        correction = AttendanceCorrection(
            employee_id=employee_id, attendance_id=attendance.id,
            correction_date=attendance.date, requested_clock_out=attendance.clock_out,
            reason='Verify correct out time',
        )
        db.session.add(correction)
        db.session.add(LeaveRequest(
            employee_id=employee_id, start_date=START, end_date=START, days=1, leave_type='SIL',
        ))
        db.session.commit()
        correction_id = correction.id
        record = db.session.get(Payroll, payroll_id)
        saved = record.payslip_snapshot
        old_net = record.net_pay
    with patch('hris.send_notification_email'):
        assert client.post(f'/admin/attendance-correction/{correction_id}/approve').status_code == 302
    new_client = app.test_client()
    login(new_client, admin_id)
    card, _ = dashboard_card(new_client, employee_id)
    assert 'Attendance changed since saved payroll' in ' '.join(card['text'])
    with app.app_context():
        employee = db.session.get(Employee, employee_id)
        record = db.session.get(Payroll, payroll_id)
        assert hris.finalized_payroll_update_preview(employee, record, START, END)['net_pay'] == old_net
        assert record.payslip_snapshot == saved
        assert employee.loan_balance == 500
    monkeypatch.setattr(hris, 'completed_cutoff', lambda today=None: (END, END + timedelta(days=7)))
    response = new_client.get('/dashboard_admin?company=Trece-Uno')
    assert response.status_code == 200
    assert b'Saved payroll alerts' in response.data
    assert b'cutoff_start=2026-10-31' in response.data
    isolated = new_client.get('/dashboard_admin?company=Auto+Expert')
    assert isolated.status_code == 200 and b'Saved payroll alerts' not in isolated.data
    assert new_client.get(f'/payroll/{employee_id}?cutoff_start={START}').status_code == 200


def test_legacy_payroll_only_requests_verification_when_correction_evidence_exists(finalized_case):
    _, _, employee_id, payroll_id = finalized_case
    with app.app_context():
        employee = db.session.get(Employee, employee_id)
        record = db.session.get(Payroll, payroll_id)
        snapshot = json.loads(record.payslip_snapshot)
        del snapshot['payroll_attendance_fingerprint']
        record.payslip_snapshot = json.dumps(snapshot)
        db.session.commit()
        review = hris.payroll_review_checklist(employee, record, START, END)
        assert not review['alerts'] and not review['attendance_changed']
        assert review['checks'][-1]['unverified']
        db.session.add(AttendanceCorrection(
            employee_id=employee_id, correction_date=START, reason='Legacy correction',
            status='Approved', reviewed_at=datetime(2026, 11, 8),
        ))
        db.session.commit()
        review = hris.payroll_review_checklist(employee, record, START, END)
        assert not review['attendance_changed']
        assert 'no source checkpoint' in review['alerts'][0]
        assert db.session.get(Payroll, payroll_id).payslip_snapshot == json.dumps(snapshot)


@pytest.mark.parametrize('bulk', [False, True])
@pytest.mark.parametrize('issue', ['punch', 'duplicate', 'correction', 'leave', 'skip', 'overtime'])
def test_manual_finalize_blocks_pending_sources_atomically(finalized_case, issue, bulk):
    client, admin_id, employee_id, payroll_id = finalized_case
    expected = {
        'punch': 'Incomplete clock in/out', 'duplicate': 'Duplicate attendance records',
        'correction': 'Attendance correction pending', 'leave': 'Leave request pending',
        'skip': 'Loan deduction skip request pending', 'overtime': 'OT request pending',
    }[issue]
    with app.app_context():
        record = db.session.get(Payroll, payroll_id)
        employee = db.session.get(Employee, employee_id)
        record.is_paid = False
        record.loan_deduction_applied = False
        employee.loan_balance = 1000
        if issue == 'punch':
            Attendance.query.filter_by(employee_id=employee_id).first().clock_out = None
        elif issue == 'duplicate':
            db.session.add(worked(employee_id, START))
        elif issue == 'correction':
            db.session.add(AttendanceCorrection(employee_id=employee_id, correction_date=START, reason='Check'))
        elif issue == 'leave':
            db.session.add(LeaveRequest(
                employee_id=employee_id, start_date=START, end_date=START, days=1, leave_type='SIL',
            ))
        elif issue == 'skip':
            db.session.add(LoanDeductionSkipRequest(
                employee_id=employee_id, cutoff_start=START, cutoff_end=END - timedelta(days=1), reason='Check',
            ))
        else:
            db.session.add(OTApplication(
                employee_id=employee_id, ot_date=START, start_time=time(17), end_time=time(18), reason='Check',
            ))
        db.session.commit()
        saved = record.payslip_snapshot
    data = {'cutoff_start': START.isoformat(), f'daily_rate_{employee_id}': '999'}
    route = f'/payroll/{employee_id}/finalize'
    if bulk:
        route = '/payroll/bulk-finalize'
        data['selected_employee_ids'] = [str(admin_id), str(employee_id)]
    response = client.post(route, data=data, follow_redirects=True)
    assert response.status_code == 200
    assert expected.encode() in response.data
    assert b'No changes were saved' in response.data
    with app.app_context():
        record = db.session.get(Payroll, payroll_id)
        employee = db.session.get(Employee, employee_id)
        assert not record.is_paid
        assert record.payslip_snapshot == saved
        assert employee.loan_balance == 1000 and employee.daily_rate == 645
        assert Payroll.query.filter_by(employee_id=admin_id).count() == 0


def test_recorded_absence_is_allowed_but_incomplete_punch_is_not(finalized_case):
    _, _, employee_id, payroll_id = finalized_case
    with app.app_context():
        db.session.add(Attendance(employee_id=employee_id, date=date(2026, 11, 4), status='Absent'))
        db.session.commit()
        employee = db.session.get(Employee, employee_id)
        assert hris.payroll_cutoff_pending_issues(employee, START, END) == []
        record = db.session.get(Payroll, payroll_id)
        record.is_paid = False
        assert hris.finalize_payroll_record(employee, record, START, END)
        absence = Attendance.query.filter_by(employee_id=employee_id, status='Absent').first()
        absence.clock_in = datetime(2026, 11, 4, 8)
        assert 'Incomplete clock in/out' in hris.payroll_cutoff_pending_issues(employee, START, END)
        db.session.rollback()


def add_correction(employee_id, same_pay=False):
    with app.app_context():
        if same_pay:
            attendance = Attendance.query.filter_by(employee_id=employee_id).first()
        else:
            attendance = worked(employee_id, date(2026, 11, 4))
            attendance.clock_out = None
            db.session.add(attendance)
            db.session.flush()
        correction = AttendanceCorrection(
            employee_id=employee_id, attendance_id=attendance.id,
            correction_date=attendance.date,
            requested_clock_out=datetime.combine(attendance.date, time(17)),
            reason='Missing or incorrect clock out',
        )
        db.session.add(correction)
        db.session.commit()
        return correction.id


def test_invalid_approved_times_cannot_trigger_automatic_salary_update(finalized_case):
    client, _, employee_id, payroll_id = finalized_case
    with app.app_context():
        attendance = Attendance.query.filter_by(employee_id=employee_id).first()
        correction = AttendanceCorrection(
            employee_id=employee_id, attendance_id=attendance.id,
            correction_date=attendance.date,
            requested_clock_out=datetime.combine(attendance.date, time(7)),
            reason='Invalid legacy request',
        )
        db.session.add(correction)
        db.session.commit()
        correction_id = correction.id
        old_snapshot = db.session.get(Payroll, payroll_id).payslip_snapshot
    with patch('hris.send_notification_email') as notify:
        response = client.post(f'/admin/attendance-correction/{correction_id}/approve', follow_redirects=True)
        notify.assert_not_called()
    assert b'clock-out must be later than clock-in' in response.data
    with app.app_context():
        assert db.session.get(AttendanceCorrection, correction_id).status == 'Pending'
        assert db.session.get(Payroll, payroll_id).payslip_snapshot == old_snapshot
        assert db.session.get(Employee, employee_id).loan_balance == 500


@pytest.mark.parametrize('same_pay', [False, True])
@pytest.mark.parametrize('finalized_case', [True], indirect=True)
def test_automatic_correction_preserves_balances_invalidates_qr_and_is_idempotent(finalized_case, same_pay):
    client, _, employee_id, payroll_id = finalized_case
    correction_id = add_correction(employee_id, same_pay)
    with app.test_request_context(), app.app_context():
        record = db.session.get(Payroll, payroll_id)
        old_net = record.net_pay
        old_qr = hris.build_payslip_verification(
            employee_id, payroll_id, START, END - timedelta(days=1), old_net,
        )['document_id']
    with patch('hris.send_notification_email'):
        response = client.post(f'/admin/attendance-correction/{correction_id}/approve')
    assert response.status_code == 302
    with app.app_context():
        record = db.session.get(Payroll, payroll_id)
        net = record.net_pay
        if same_pay:
            assert net == pytest.approx(old_net)
        else:
            assert net == pytest.approx(912.05)
        assert record.is_paid and record.loan_deduction_applied and record.liability_deduction_applied
        assert record.confirmation_status == 'Pending'
        assert record.loan == 500 and record.liability_deduction == 100
        assert db.session.get(Employee, employee_id).loan_balance == 500
        assert EmployeeLiability.query.filter_by(employee_id=employee_id).one().amount_deducted == 100
        assert json.loads(record.payslip_snapshot)['payroll_approved_correction_ids'] == [correction_id]
        assert not hris.payroll_review_checklist(
            db.session.get(Employee, employee_id), record, START, END,
        )['alerts']
    assert b'DO NOT RELEASE' in client.get(f'/verify-document/{old_qr}').data
    with patch('hris.send_notification_email') as notify:
        client.post(f'/admin/attendance-correction/{correction_id}/approve')
        notify.assert_not_called()
    with app.app_context():
        assert db.session.get(Payroll, payroll_id).net_pay == net
        assert db.session.get(Employee, employee_id).loan_balance == 500


@pytest.mark.parametrize('existing_draft', [False, True])
def test_current_draft_correction_updates_without_finalizing_or_deducting(finalized_case, monkeypatch, existing_draft):
    client, _, employee_id, payroll_id = finalized_case
    monkeypatch.setattr(hris, 'current_payroll_cutoff', lambda today=None: (START, END - timedelta(days=1)))
    with app.app_context():
        record = db.session.get(Payroll, payroll_id)
        employee = db.session.get(Employee, employee_id)
        employee.loan_balance = 1000
        if existing_draft:
            record.is_paid = False
            record.loan_deduction_applied = False
            record.liability_deduction_applied = False
        else:
            db.session.delete(record)
        db.session.commit()
    correction_id = add_correction(employee_id)
    with patch('hris.send_notification_email'):
        assert client.post(f'/admin/attendance-correction/{correction_id}/approve').status_code == 302
    with app.app_context():
        record = Payroll.query.filter_by(employee_id=employee_id, cutoff_start=START).one()
        assert not record.is_paid and not record.loan_deduction_applied
        assert not record.liability_deduction_applied
        assert record.net_pay > 0 and record.loan == 500
        assert db.session.get(Employee, employee_id).loan_balance == 1000
        assert not hris.payroll_review_checklist(
            db.session.get(Employee, employee_id), record, START, END,
        )['alerts']


def test_automatic_draft_update_invalidates_an_older_preview(finalized_case):
    client, _, employee_id, payroll_id = finalized_case
    with app.app_context():
        record = db.session.get(Payroll, payroll_id)
        record.is_paid = False
        record.loan_deduction_applied = False
        record.liability_deduction_applied = False
        db.session.get(Employee, employee_id).loan_balance = 1000
        db.session.commit()
    correction_id = add_correction(employee_id)
    assert client.get(f'/payroll/{employee_id}/recalculate?cutoff_start={START}').status_code == 200
    with client.session_transaction() as session:
        token = session['payroll_recalculation_csrf']
    with patch('hris.send_notification_email'):
        client.post(f'/admin/attendance-correction/{correction_id}/approve')
    with app.app_context():
        new_snapshot = db.session.get(Payroll, payroll_id).payslip_snapshot
    response = client.post(
        f'/payroll/{employee_id}/recalculate',
        data={'cutoff_start': START.isoformat(), 'csrf_token': token, 'approved': 'yes'},
        follow_redirects=True,
    )
    assert b'Preview expired, changed, or not approved' in response.data
    with app.app_context():
        assert db.session.get(Payroll, payroll_id).payslip_snapshot == new_snapshot
        assert db.session.get(Employee, employee_id).loan_balance == 1000


def test_attendance_checkpoint_is_stable_across_integer_float_database_roundtrip(finalized_case):
    _, _, employee_id, payroll_id = finalized_case
    with app.app_context():
        attendance = Attendance.query.filter_by(employee_id=employee_id).first()
        attendance.hours = 8
        attendance.overtime_hours = 0
        db.session.flush()
        record = db.session.get(Payroll, payroll_id)
        employee = db.session.get(Employee, employee_id)
        hris.save_payroll_snapshot(record, hris.calculate_payroll_snapshot(employee, record, START, END))
        checkpoint = json.loads(record.payslip_snapshot)['payroll_attendance_fingerprint']
        db.session.commit()
        db.session.expire_all()
        assert checkpoint == hris.payroll_attendance_fingerprint(employee_id, START, END)


@pytest.mark.parametrize('pending', ['leave', 'overtime', 'skip', 'correction'])
def test_automatic_update_resumes_when_last_pending_request_is_resolved(finalized_case, pending):
    client, _, employee_id, payroll_id = finalized_case
    correction_id = add_correction(employee_id)
    with app.app_context():
        if pending == 'leave':
            blocker = LeaveRequest(employee_id=employee_id, start_date=START, end_date=START, days=1, leave_type='SIL')
        elif pending == 'overtime':
            blocker = OTApplication(
                employee_id=employee_id, ot_date=START, start_time=time(17), end_time=time(18), reason='Work',
            )
        elif pending == 'skip':
            blocker = LoanDeductionSkipRequest(
                employee_id=employee_id, cutoff_start=START, cutoff_end=END - timedelta(days=1), reason='Emergency',
            )
        else:
            blocker = AttendanceCorrection(employee_id=employee_id, correction_date=START, reason='Wrong request')
        db.session.add(blocker)
        db.session.commit()
        blocker_id = blocker.id
        old_net = db.session.get(Payroll, payroll_id).net_pay
    with patch('hris.send_notification_email'):
        client.post(f'/admin/attendance-correction/{correction_id}/approve')
    with app.app_context():
        assert db.session.get(AttendanceCorrection, correction_id).status == 'Approved'
        assert db.session.get(Payroll, payroll_id).net_pay == old_net
        assert db.session.get(Employee, employee_id).loan_balance == 500
    card, _ = dashboard_card(client, employee_id)
    assert 'Attendance changed since saved payroll' in ' '.join(card['text'])
    routes = {
        'leave': f'/reject_leave/{blocker_id}',
        'overtime': f'/admin/ot_applications/{blocker_id}/reject',
        'skip': f'/loan/deduction-skip/{blocker_id}/approve',
        'correction': f'/admin/attendance-correction/{blocker_id}/reject',
    }
    with patch('hris.send_notification_email'), patch('hris.send_leave_email'):
        response = client.post(routes[pending], data={'reason': 'Declined'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'automatically updated and finalized' in response.data
    with app.app_context():
        record = db.session.get(Payroll, payroll_id)
        assert record.net_pay > old_net and record.is_paid
        employee = db.session.get(Employee, employee_id)
        assert employee.loan_balance == (1000 if pending == 'skip' else 500)
        assert record.loan == (0 if pending == 'skip' else 500)
        assert not hris.payroll_review_checklist(employee, record, START, END)['alerts']


@pytest.mark.parametrize('finalized_case', [True], indirect=True)
def test_automatic_update_failure_preserves_approval_salary_balances_and_old_qr(finalized_case, monkeypatch):
    client, _, employee_id, payroll_id = finalized_case
    correction_id = add_correction(employee_id)
    with app.test_request_context(), app.app_context():
        record = db.session.get(Payroll, payroll_id)
        old_net, old_snapshot = record.net_pay, record.payslip_snapshot
        old_qr = hris.build_payslip_verification(
            employee_id, payroll_id, START, END - timedelta(days=1), old_net,
        )['document_id']

    def fail_snapshot(*args):
        raise ValueError('Injected snapshot failure')

    monkeypatch.setattr(hris, 'save_payroll_snapshot', fail_snapshot)
    with patch('hris.send_notification_email'):
        response = client.post(f'/admin/attendance-correction/{correction_id}/approve', follow_redirects=True)
    assert response.status_code == 200
    assert b'automatic payroll update failed' in response.data
    with app.app_context():
        record = db.session.get(Payroll, payroll_id)
        assert record.is_paid and record.net_pay == old_net
        assert record.payslip_snapshot == old_snapshot
        assert record.loan_deduction_applied and record.liability_deduction_applied
        assert db.session.get(Employee, employee_id).loan_balance == 500
        assert EmployeeLiability.query.filter_by(employee_id=employee_id).one().amount_deducted == 100
        assert db.session.get(AttendanceCorrection, correction_id).status == 'Approved'
        assert db.session.get(AttendanceCorrection, correction_id).attendance.clock_out is not None
    assert b'OK to release' in client.get(f'/verify-document/{old_qr}').data


def test_historical_correction_does_not_direct_admin_to_latest_cutoff_action(finalized_case):
    client, _, employee_id, _ = finalized_case
    with app.app_context():
        day = date(2026, 10, 29)
        incomplete = worked(employee_id, day)
        incomplete.clock_out = None
        old_record = Payroll(
            employee_id=employee_id, cutoff_start=START - timedelta(days=7),
            cutoff_end=START - timedelta(days=1), is_paid=True,
            gross_income=500, net_pay=500,
        )
        db.session.add_all([incomplete, old_record])
        db.session.flush()
        correction = AttendanceCorrection(
            employee_id=employee_id, attendance_id=incomplete.id, correction_date=day,
            requested_clock_out=datetime(2026, 10, 29, 17), reason='Missing clock out',
        )
        db.session.add(correction)
        db.session.commit()
        correction_id = correction.id
        old_record_id = old_record.id
    with patch('hris.send_notification_email'):
        assert client.post(f'/admin/attendance-correction/{correction_id}/approve').status_code == 302
    with client.session_transaction() as session:
        messages = [message for _, message in session.get('_flashes', [])]
    assert any('review older payroll separately' in message for message in messages)
    assert not any('Recalculate & Finalize' in message for message in messages)
    with app.app_context():
        record = db.session.get(Payroll, old_record_id)
        assert record.is_paid and record.net_pay == 500


def test_unpaid_card_links_directly_to_recalculation_preview(finalized_case):
    client, _, employee_id, _ = finalized_case
    card, _ = dashboard_card(client, employee_id, END)
    action = card_action(card, 'name', 'save_employee_id')
    assert action is not None and action['depth'] == 0
    assert action['attrs']['value'] == str(employee_id)
    assert 'Save & Review Preview' in ''.join(action['text'])
    response = client.post('/payroll_dashboard', data={
        'cutoff_start': END.isoformat(),
        'save_employee_id': str(employee_id),
        f'daily_rate_{employee_id}': '645',
        f'allowance_{employee_id}': '0',
        f'incentives_{employee_id}': '0',
    })
    assert response.status_code == 302
    assert response.location.endswith(
        f'/payroll/{employee_id}/recalculate?cutoff_start={END.isoformat()}'
    )
    assert client.get(response.location).status_code == 200


def test_historical_reopen_does_not_change_latest_cutoff(finalized_case):
    client, _, employee_id, payroll_id = finalized_case
    older = START - timedelta(days=7)
    with app.app_context():
        db.session.add(Payroll(
            employee_id=employee_id, cutoff_start=older,
            cutoff_end=START - timedelta(days=1), is_paid=True,
            gross_income=500, net_pay=500,
        ))
        db.session.commit()
    card, cutoff_value = dashboard_card(client, employee_id, older)
    action = card_action(card, 'formaction', f'/payroll/{employee_id}/reopen')
    assert action is not None and 'disabled' in action['attrs']
    assert 'pinakahuling natapos na cutoff' in ' '.join(card['text']).lower()
    response = client.post(
        f'/payroll/{employee_id}/reopen', data={'cutoff_start': cutoff_value},
        follow_redirects=True,
    )
    assert b'latest completed cutoff' in response.data.lower()
    with app.app_context():
        assert db.session.get(Payroll, payroll_id).is_paid
        assert Payroll.query.filter_by(employee_id=employee_id, cutoff_start=older).one().is_paid


def test_same_net_recalculation_supersedes_old_payslip_qr(finalized_case):
    client, _, employee_id, payroll_id = finalized_case
    with app.test_request_context(), app.app_context():
        record = db.session.get(Payroll, payroll_id)
        old_net = record.net_pay
        old_id = hris.build_payslip_verification(
            employee_id, payroll_id, START, END - timedelta(days=1), old_net,
        )['document_id']
    assert b'OK to release' in client.get(f'/verify-document/{old_id}').data
    card, cutoff_value = dashboard_card(client, employee_id)
    response = client.post(
        f'/payroll/{employee_id}/update-finalized', data={'cutoff_start': cutoff_value},
        follow_redirects=True,
    )
    assert b'payroll updated and finalized' in response.data
    assert b'DO NOT RELEASE' in client.get(f'/verify-document/{old_id}').data
    with app.test_request_context(), app.app_context():
        record = db.session.get(Payroll, payroll_id)
        assert record.is_paid and record.net_pay == pytest.approx(old_net)
        assert db.session.get(Employee, employee_id).loan_balance == 500
        new_id = hris.build_payslip_verification(
            employee_id, payroll_id, START, END - timedelta(days=1), record.net_pay,
        )['document_id']
        assert new_id != old_id
    assert b'OK to release' in client.get(f'/verify-document/{new_id}').data


def test_reopening_keeps_old_qr_superseded_after_refinalizing_same_net(finalized_case):
    client, _, employee_id, payroll_id = finalized_case
    with app.test_request_context(), app.app_context():
        record = db.session.get(Payroll, payroll_id)
        old_net = record.net_pay
        old_id = hris.build_payslip_verification(
            employee_id, payroll_id, START, END - timedelta(days=1), old_net,
        )['document_id']
    response = client.post(
        f'/payroll/{employee_id}/reopen', data={'cutoff_start': START.isoformat()},
    )
    assert response.status_code == 302
    assert b'DO NOT RELEASE' in client.get(f'/verify-document/{old_id}').data
    response = client.post(
        f'/payroll/{employee_id}/finalize', data={'cutoff_start': START.isoformat()},
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Payroll, payroll_id).net_pay == pytest.approx(old_net)
    assert b'DO NOT RELEASE' in client.get(f'/verify-document/{old_id}').data


def test_preview_failure_is_visible_and_logged(finalized_case, monkeypatch, caplog):
    client, _, employee_id, _ = finalized_case

    def fail_preview(*args):
        raise ValueError('Invalid payroll source')

    monkeypatch.setattr(hris, 'finalized_payroll_update_preview', fail_preview)
    card, _ = dashboard_card(client, employee_id)
    action = card_action(card, 'formaction', f'/payroll/{employee_id}/update-finalized')
    assert action is not None and 'disabled' in action['attrs']
    assert 'Hindi ma-compute ang preview' in ' '.join(card['text'])
    assert 'Could not preview finalized payroll' in caplog.text


def test_update_rejects_missing_or_nonlatest_cutoff_and_staff_access(finalized_case):
    client, _, employee_id, payroll_id = finalized_case
    for data in ({}, {'cutoff_start': (START - timedelta(days=7)).isoformat()}):
        response = client.post(
            f'/payroll/{employee_id}/update-finalized', data=data, follow_redirects=True,
        )
        assert b'Nothing was changed' in response.data
    staff_client = app.test_client()
    login(staff_client, employee_id)
    assert staff_client.post(
        f'/payroll/{employee_id}/update-finalized',
        data={'cutoff_start': START.isoformat()},
    ).status_code == 403
    with app.app_context():
        assert db.session.get(Payroll, payroll_id).is_paid
        assert db.session.get(Employee, employee_id).loan_balance == 500


def test_duplicate_finalized_records_show_an_error_without_changes(finalized_case):
    client, _, employee_id, payroll_id = finalized_case
    with app.app_context():
        db.session.add(Payroll(
            employee_id=employee_id, cutoff_start=START,
            cutoff_end=END - timedelta(days=1), is_paid=True,
            gross_income=500, net_pay=500,
        ))
        db.session.commit()
        old_net = db.session.get(Payroll, payroll_id).net_pay
    response = client.post(
        f'/payroll/{employee_id}/update-finalized',
        data={'cutoff_start': START.isoformat()}, follow_redirects=True,
    )
    assert b'No single finalized payroll found' in response.data
    with app.app_context():
        assert db.session.get(Payroll, payroll_id).net_pay == old_net
        assert db.session.get(Employee, employee_id).loan_balance == 500


@pytest.mark.parametrize('finalized_case', [True], indirect=True)
def test_preview_restores_finalized_liability_without_changing_balances(finalized_case):
    _, _, employee_id, payroll_id = finalized_case
    with app.app_context():
        employee = db.session.get(Employee, employee_id)
        record = db.session.get(Payroll, payroll_id)
        liability = EmployeeLiability.query.filter_by(employee_id=employee_id).one()
        assert liability.status == 'Fully Paid'
        assert liability.amount_deducted == 100
        assert record.liability_deduction == 100
        preview = hris.finalized_payroll_update_preview(employee, record, START, END)
        assert preview['liability_deduction'] == pytest.approx(100)
        assert preview['net_pay'] == pytest.approx(record.net_pay)
        assert liability.status == 'Fully Paid'
        assert liability.amount_deducted == 100
        assert employee.loan_balance == 500


@pytest.mark.parametrize('finalized_case', [True], indirect=True)
def test_preview_matches_refinalization_with_multiple_liabilities(finalized_case):
    client, _, employee_id, payroll_id = finalized_case
    with app.app_context():
        db.session.add(EmployeeLiability(
            employee_id=employee_id, description='Additional payroll test',
            total_amount=300, deduction_per_cutoff=75,
            amount_deducted=0, amount_recovered=0,
            status='Active', acknowledgment_status='Acknowledged',
        ))
        db.session.add(worked(employee_id, date(2026, 11, 4)))
        db.session.commit()
        employee = db.session.get(Employee, employee_id)
        record = db.session.get(Payroll, payroll_id)
        preview = hris.finalized_payroll_update_preview(employee, record, START, END)
        assert preview['liability_deduction'] == pytest.approx(175)
        assert not db.session.is_modified(employee)
    card, cutoff_value = dashboard_card(client, employee_id)
    assert 'Bagong kalkulasyon' in ' '.join(card['text'])
    response = client.post(
        f'/payroll/{employee_id}/update-finalized',
        data={'cutoff_start': cutoff_value}, follow_redirects=True,
    )
    assert b'payroll updated and finalized' in response.data
    with app.app_context():
        record = db.session.get(Payroll, payroll_id)
        assert record.is_paid
        assert record.net_pay == pytest.approx(preview['net_pay'])
        assert record.liability_deduction == pytest.approx(175)
        assert db.session.get(Employee, employee_id).loan_balance == 500
        liabilities = EmployeeLiability.query.filter_by(employee_id=employee_id).order_by(
            EmployeeLiability.id
        ).all()
        assert [(item.amount_deducted, item.status) for item in liabilities] == [
            (100, 'Fully Paid'), (75, 'Active'),
        ]


@pytest.mark.parametrize('finalized_case', [True], indirect=True)
def test_failed_recalculation_rolls_back_balances_and_old_qr(finalized_case, monkeypatch):
    client, _, employee_id, payroll_id = finalized_case
    with app.test_request_context(), app.app_context():
        record = db.session.get(Payroll, payroll_id)
        old_net = record.net_pay
        old_id = hris.build_payslip_verification(
            employee_id, payroll_id, START, END - timedelta(days=1), old_net,
        )['document_id']

    def fail_finalization(*args):
        raise ValueError('Invalid payroll source')

    monkeypatch.setattr(hris, 'finalize_payroll_record', fail_finalization)
    response = client.post(
        f'/payroll/{employee_id}/update-finalized',
        data={'cutoff_start': START.isoformat()}, follow_redirects=True,
    )
    assert b'Payroll could not be updated. Nothing was changed.' in response.data
    assert b'OK to release' in client.get(f'/verify-document/{old_id}').data
    with app.app_context():
        record = db.session.get(Payroll, payroll_id)
        liability = EmployeeLiability.query.filter_by(employee_id=employee_id).one()
        assert record.is_paid and record.net_pay == pytest.approx(old_net)
        assert db.session.get(Employee, employee_id).loan_balance == 500
        assert (liability.amount_deducted, liability.status) == (100, 'Fully Paid')


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
            assert 'Bagong kalkulasyon' in html
            assert f'/payroll/{ids[1]}/update-finalized' in html
            response = client.post(f'/payroll/{ids[1]}/update-finalized', data={'cutoff_start': START.isoformat()})
            assert response.status_code == 302
            html = client.get(f'/payroll_dashboard?cutoff_start={START}').get_data(as_text=True)
            assert f'/payroll/{ids[1]}/update-finalized' in html
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
