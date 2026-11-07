import json
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from importlib import import_module
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import SQLAlchemyError

import hris
from models import (
    Attendance, AttendanceCorrection, AuditLog, Employee, Payroll, db,
)


START = date(2026, 10, 31)
END = START + timedelta(days=7)
MISSING_DAY = date(2026, 11, 6)


class AttendanceEditFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.action = None
        self.inputs = {}
        self.options = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form' and attrs.get('id') == 'admin-attendance-edit':
            self.active = True
            self.action = attrs.get('action')
        elif self.active and tag == 'input':
            self.inputs[attrs.get('name')] = attrs.get('value', '')
        elif self.active and tag == 'option' and '|' in attrs.get('value', ''):
            day, _ = attrs['value'].split('|', 1)
            self.options[day] = attrs

    def handle_endtag(self, tag):
        if tag == 'form' and self.active:
            self.active = False


def login(client, employee_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True


def get_form(client, employee_id, day=MISSING_DAY):
    response = client.get(
        f'/attendance/{employee_id}?cutoff_start={START.isoformat()}'
    )
    assert response.status_code == 200
    parser = AttendanceEditFormParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.action is not None
    return parser, {
        'csrf_token': parser.inputs['csrf_token'],
        'cutoff_start': START.isoformat(),
        'attendance_entry': parser.options[day.isoformat()]['value'],
        'clock_in': '08:00',
        'clock_out': '17:00',
        'reason': 'Verified timesheet',
    }


@pytest.fixture
def people(monkeypatch):
    monkeypatch.setattr(hris, 'completed_cutoff', lambda today=None: (START, END))
    with hris.app.app_context():
        admin = Employee(first_name='Attendance', last_name='Admin', role='admin')
        employee = Employee(
            first_name='Attendance', last_name='Staff', role='staff',
            company='Trece-Uno', daily_rate=645, rice_allowance_per_day=95,
        )
        other = Employee(first_name='Other', last_name='Staff', role='staff')
        db.session.add_all([admin, employee, other])
        db.session.flush()
        for day in (
            date(2026, 10, 31), date(2026, 11, 2), date(2026, 11, 3),
            date(2026, 11, 4), date(2026, 11, 5), MISSING_DAY,
        ):
            db.session.add(Attendance(
                employee_id=employee.id, date=day, company='Trece-Uno',
                clock_in=datetime.combine(day, datetime.min.time()).replace(hour=8),
                clock_out=None if day == MISSING_DAY else datetime.combine(
                    day, datetime.min.time()
                ).replace(hour=17),
                status='Present',
            ))
        record = Payroll(
            employee_id=employee.id, cutoff_start=START,
            cutoff_end=END - timedelta(days=1), is_paid=False,
        )
        db.session.add(record)
        db.session.commit()
        ids = admin.id, employee.id, other.id, record.id
    client = hris.app.test_client()
    login(client, ids[0])
    try:
        yield client, ids
    finally:
        with hris.app.app_context():
            db.session.rollback()
            employee_ids = ids[:3]
            if hasattr(hris, 'AttendanceAdminEdit'):
                hris.AttendanceAdminEdit.query.filter(
                    hris.AttendanceAdminEdit.employee_id.in_(employee_ids)
                ).delete(synchronize_session=False)
            for model in (AttendanceCorrection, Attendance, Payroll, AuditLog):
                model.query.filter(model.employee_id.in_(employee_ids)).delete(
                    synchronize_session=False
                )
            Employee.query.filter(Employee.id.in_(employee_ids)).delete(
                synchronize_session=False
            )
            db.session.commit()


def payroll_basic_row(employee_id, record_id):
    with hris.app.app_context():
        employee = db.session.get(Employee, employee_id)
        record = db.session.get(Payroll, record_id)
        projected, _, _ = hris.draft_payroll_preview(employee, record, START, END)
        return hris.weekly_payslip_table_data(
            hris.build_payslip_breakdown(employee, projected)
        )[1][:3]


def test_admin_corrects_nov_6_and_fresh_payroll_preview_includes_sixth_day(people):
    client, (admin_id, employee_id, _, payroll_id) = people
    assert payroll_basic_row(employee_id, payroll_id) == ['Basic Pay', '5 / 32.00', '2,580.00']
    form, data = get_form(client, employee_id)
    response = client.post(form.action, data=data, follow_redirects=True)
    assert response.status_code == 200
    assert b'attendance' in response.data.lower()
    assert payroll_basic_row(employee_id, payroll_id) == ['Basic Pay', '6 / 40.00', '3,225.00']
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        assert row.clock_in == datetime(2026, 11, 6, 8)
        assert row.clock_out == datetime(2026, 11, 6, 17)
        assert not db.session.get(Payroll, payroll_id).is_paid
        edit = hris.AttendanceAdminEdit.query.filter_by(
            employee_id=employee_id, attendance_date=MISSING_DAY,
        ).one()
        assert edit.performed_by == admin_id
        assert edit.reason == 'Verified timesheet'
        assert json.loads(edit.original_row)['clock_out'] is None
        assert datetime.fromisoformat(json.loads(edit.corrected_row)['clock_out']) == datetime(
            2026, 11, 6, 17,
        )
        assert AuditLog.query.filter(
            AuditLog.employee_id == admin_id,
            AuditLog.action.like('%attendance%'),
        ).count() >= 1
    preview = client.get(
        f'/payroll/{employee_id}/recalculate?cutoff_start={START.isoformat()}'
    )
    assert preview.status_code == 200
    assert b'6 / 40.00' in preview.data
    assert f'/attendance/{employee_id}?cutoff_start={START.isoformat()}'.encode() in preview.data


def test_admin_edit_invalidates_an_older_draft_preview(people):
    client, (_, employee_id, _, payroll_id) = people
    preview_url = f'/payroll/{employee_id}/recalculate?cutoff_start={START.isoformat()}'
    assert client.get(preview_url).status_code == 200
    with client.session_transaction() as session:
        payroll_csrf = session['payroll_recalculation_csrf']
    with hris.app.app_context():
        payroll = db.session.get(Payroll, payroll_id)
        original_net = payroll.net_pay
        original_snapshot = payroll.payslip_snapshot
    form, data = get_form(client, employee_id)
    assert client.post(form.action, data=data).status_code == 302
    response = client.post(preview_url, data={
        'csrf_token': payroll_csrf,
        'cutoff_start': START.isoformat(),
        'approved': 'yes',
    })
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert any('Preview expired, changed, or not approved' in message
                   for _, message in session['_flashes'])
    with hris.app.app_context():
        payroll = db.session.get(Payroll, payroll_id)
        assert payroll.net_pay == original_net
        assert payroll.payslip_snapshot == original_snapshot


def test_only_admin_can_edit_and_csrf_is_required(people):
    client, (_, employee_id, other_id, _) = people
    form, data = get_form(client, employee_id)
    bad_token = dict(data, csrf_token='invalid')
    assert client.post(form.action, data=bad_token).status_code == 400
    staff = hris.app.test_client()
    login(staff, employee_id)
    own_page = staff.get(f'/attendance/{employee_id}?cutoff_start={START.isoformat()}')
    assert b'id="admin-attendance-edit"' not in own_page.data
    assert staff.post(form.action, data=data).status_code == 403
    login(staff, other_id)
    assert staff.post(form.action, data=data).status_code == 403
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        assert row.clock_out is None


@pytest.mark.parametrize(
    'changes',
    [
        {'clock_in': ''},
        {'clock_out': ''},
        {'clock_in': '17:00', 'clock_out': '08:00'},
        {'clock_in': '08:00', 'clock_out': '08:00'},
        {'reason': ''},
        {'reason': 'x' * 501},
        {'attendance_entry': '2026-11-13|invalid'},
        {'cutoff_start': '2026-11-02'},
    ],
)
def test_invalid_edits_leave_attendance_unchanged(people, changes):
    client, (_, employee_id, _, _) = people
    form, data = get_form(client, employee_id)
    data.update(changes)
    response = client.post(form.action, data=data, follow_redirects=True)
    assert response.status_code == 200
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        assert row.clock_out is None
        assert hris.AttendanceAdminEdit.query.filter_by(employee_id=employee_id).count() == 0


def test_stale_edit_and_duplicate_attendance_are_not_overwritten(people):
    client, (_, employee_id, _, _) = people
    form, data = get_form(client, employee_id)
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        row.clock_in = datetime(2026, 11, 6, 9)
        db.session.commit()
    response = client.post(form.action, data=data, follow_redirects=True)
    assert b'changed' in response.data.lower()
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        assert row.clock_in == datetime(2026, 11, 6, 9)
        assert row.clock_out is None
        db.session.add(Attendance(
            employee_id=employee_id, date=MISSING_DAY, status='Present',
            clock_in=datetime(2026, 11, 6, 10), company='Trece-Uno',
        ))
        db.session.commit()
    duplicate_form, duplicate_data = get_form(client, employee_id)
    assert 'disabled' in duplicate_form.options[MISSING_DAY.isoformat()]
    response = client.post(duplicate_form.action, data=duplicate_data, follow_redirects=True)
    assert b'duplicate' in response.data.lower()
    with hris.app.app_context():
        assert Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).count() == 2
        assert hris.AttendanceAdminEdit.query.filter_by(employee_id=employee_id).count() == 0


def test_pending_staff_correction_blocks_direct_edit(people):
    client, (_, employee_id, _, _) = people
    form, data = get_form(client, employee_id)
    with hris.app.app_context():
        db.session.add(AttendanceCorrection(
            employee_id=employee_id, correction_date=MISSING_DAY,
            requested_clock_out=datetime(2026, 11, 6, 17),
            reason='Missing out', status='Pending',
        ))
        db.session.commit()
    response = client.post(form.action, data=data, follow_redirects=True)
    assert b'pending' in response.data.lower()
    refreshed_form, _ = get_form(client, employee_id)
    assert 'disabled' in refreshed_form.options[MISSING_DAY.isoformat()]
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        assert row.clock_out is None
        assert hris.AttendanceAdminEdit.query.filter_by(employee_id=employee_id).count() == 0


def test_new_record_is_audited_and_finalized_payroll_is_not_changed(people):
    client, (_, employee_id, _, payroll_id) = people
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        db.session.delete(row)
        payroll = db.session.get(Payroll, payroll_id)
        payroll.is_paid = True
        payroll.net_pay = 1234
        db.session.commit()
    form, data = get_form(client, employee_id)
    response = client.post(form.action, data=data, follow_redirects=True)
    assert b'finalized' in response.data.lower()
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        assert row.clock_out == datetime(2026, 11, 6, 17)
        edit = hris.AttendanceAdminEdit.query.filter_by(employee_id=employee_id).one()
        assert edit.original_row is None
        payroll = db.session.get(Payroll, payroll_id)
        assert payroll.is_paid and payroll.net_pay == 1234


def test_older_finalized_cutoff_requires_separate_payroll_review(people, monkeypatch):
    client, (_, employee_id, _, payroll_id) = people
    monkeypatch.setattr(
        hris, 'completed_cutoff',
        lambda today=None: (START + timedelta(days=7), END + timedelta(days=7)),
    )
    with hris.app.app_context():
        db.session.get(Payroll, payroll_id).is_paid = True
        db.session.commit()
    form, data = get_form(client, employee_id)
    response = client.post(form.action, data=data, follow_redirects=True)
    assert b'only for the latest finalized cutoff' in response.data.lower()
    with hris.app.app_context():
        assert db.session.get(Payroll, payroll_id).is_paid


def test_failed_audit_write_rolls_back_attendance(people):
    client, (_, employee_id, _, _) = people
    form, data = get_form(client, employee_id)
    with patch('hris.AttendanceAdminEdit', side_effect=SQLAlchemyError('audit unavailable')):
        response = client.post(form.action, data=data, follow_redirects=True)
    assert b'could not be saved' in response.data.lower()
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        assert row.clock_out is None
        assert hris.AttendanceAdminEdit.query.filter_by(employee_id=employee_id).count() == 0


def test_existing_overtime_fields_are_preserved(people):
    client, (_, employee_id, _, _) = people
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        row.is_weekday_ot = True
        row.overtime_hours = 1.5
        row.ot_status = 'Approved'
        row.clock_in = datetime(2026, 11, 6, 8, 0, 7, 123456)
        db.session.commit()
    form, data = get_form(client, employee_id)
    assert form.options[MISSING_DAY.isoformat()]['data-clock-in'] == '08:00:07'
    data.update(clock_in='08:00:07', clock_out='18:30')
    response = client.post(form.action, data=data, follow_redirects=True)
    assert b'overtime and leave approvals' in response.data.lower()
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        assert row.clock_in == datetime(2026, 11, 6, 8, 0, 7, 123456)
        assert row.clock_out == datetime(2026, 11, 6, 18, 30)
        assert row.is_weekday_ot
        assert row.overtime_hours == 1.5
        assert row.ot_status == 'Approved'


def test_preserved_clock_in_precision_cannot_reverse_time_order(people):
    client, (_, employee_id, _, _) = people
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        row.clock_in = datetime(2026, 11, 6, 8, 0, 0, 900000)
        db.session.commit()
    form, data = get_form(client, employee_id)
    data.update(clock_in='08:00:00', clock_out='08:00:00.5')
    response = client.post(form.action, data=data, follow_redirects=True)
    assert b'clock-out must be later' in response.data.lower()
    with hris.app.app_context():
        row = Attendance.query.filter_by(employee_id=employee_id, date=MISSING_DAY).one()
        assert row.clock_in == datetime(2026, 11, 6, 8, 0, 0, 900000)
        assert row.clock_out is None
        assert hris.AttendanceAdminEdit.query.filter_by(employee_id=employee_id).count() == 0


def test_payroll_card_links_to_attendance_for_draft_and_finalized_records(people):
    client, (_, employee_id, _, payroll_id) = people
    with hris.app.test_request_context():
        dashboard_url = hris.url_for('payroll_dashboard', cutoff_start=START)
    expected = f'/attendance/{employee_id}?cutoff_start={START.isoformat()}#admin-attendance-edit'
    for finalized in (False, True):
        with hris.app.app_context():
            payroll = db.session.get(Payroll, payroll_id)
            payroll.is_paid = finalized
            db.session.commit()
        response = client.get(dashboard_url)
        assert response.status_code == 200
        assert expected.encode() in response.data


def test_attendance_edit_audit_migration_is_idempotent():
    migration = import_module('migrations.versions.f1a2b3c4d5e6_add_attendance_admin_edits')
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.execute(sa.text('CREATE TABLE employees (id INTEGER PRIMARY KEY)'))
        operations = Operations(MigrationContext.configure(connection))
        with patch.object(migration, 'op', operations):
            migration.upgrade()
            migration.upgrade()
            with pytest.raises(RuntimeError, match='history must be preserved'):
                migration.downgrade()
        inspector = sa.inspect(connection)
        assert 'attendance_admin_edits' in inspector.get_table_names()
        assert {column['name'] for column in inspector.get_columns('attendance_admin_edits')} == {
            'id', 'employee_id', 'attendance_date', 'performed_by', 'attendance_id',
            'original_row', 'corrected_row', 'reason', 'created_at',
        }
        assert {
            (tuple(key['constrained_columns']), key['referred_table'])
            for key in inspector.get_foreign_keys('attendance_admin_edits')
        } == {(('employee_id',), 'employees'), (('performed_by',), 'employees')}
