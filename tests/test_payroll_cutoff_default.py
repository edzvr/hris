from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch

import pytest
from pypdf import PdfReader
from werkzeug.http import parse_options_header

import hris
from hris import app, current_payroll_cutoff, payroll_cutoff_from_request
from models import Attendance, Employee, Payroll, PayslipVerification, db


def freeze_clock(monkeypatch, instant):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(hris, 'datetime', FrozenDateTime)


@pytest.mark.parametrize(
    'instant, expected_start',
    [
        (datetime(2026, 11, 13, 15, 59, tzinfo=timezone.utc), date(2026, 11, 7)),
        (datetime(2026, 11, 13, 16, 0, tzinfo=timezone.utc), date(2026, 11, 7)),
        (datetime(2026, 11, 14, 15, 59, tzinfo=timezone.utc), date(2026, 11, 7)),
        (datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc), date(2026, 11, 14)),
        (datetime(2026, 11, 15, 16, 0, tzinfo=timezone.utc), date(2026, 11, 14)),
        (datetime(2027, 1, 1, 16, 0, tzinfo=timezone.utc), date(2026, 12, 26)),
    ],
)
def test_default_payroll_cutoff_switches_on_sunday_manila_time(
    monkeypatch, instant, expected_start,
):
    freeze_clock(monkeypatch, instant)
    assert payroll_cutoff_from_request() == (
        expected_start,
        expected_start + timedelta(days=7),
    )


def test_actual_payroll_period_still_starts_on_saturday():
    assert current_payroll_cutoff(date(2026, 11, 14)) == (
        date(2026, 11, 14),
        date(2026, 11, 20),
    )


def test_explicit_payroll_cutoff_selection_is_preserved():
    with patch('hris.current_payroll_cutoff') as current_cutoff:
        assert payroll_cutoff_from_request('2026-10-03') == (
            date(2026, 10, 3),
            date(2026, 10, 10),
        )
    current_cutoff.assert_not_called()

    with pytest.raises(ValueError):
        payroll_cutoff_from_request('2026-10-04')


@pytest.fixture
def payroll_people():
    with app.app_context():
        admin = Employee(first_name='Cutoff', last_name='Admin', role='admin')
        staff = Employee(
            first_name='Cutoff', last_name='Staff', role='staff',
            company='Trece-Uno', payroll_preparation_access=True,
        )
        db.session.add_all([admin, staff])
        db.session.flush()
        db.session.add_all([
            Attendance(employee_id=staff.id, date=date(2026, 11, 11), status='Absent'),
            Attendance(
                employee_id=staff.id, date=date(2026, 11, 14), status='Present',
                clock_in=datetime(2026, 11, 14, 8),
                clock_out=datetime(2026, 11, 14, 9),
            ),
        ])
        db.session.commit()
        ids = admin.id, staff.id
    try:
        yield ids
    finally:
        with app.app_context():
            db.session.rollback()
            PayslipVerification.query.filter(
                PayslipVerification.employee_id.in_(ids)
            ).delete(synchronize_session=False)
            Payroll.query.filter(Payroll.employee_id.in_(ids)).delete(synchronize_session=False)
            Attendance.query.filter(Attendance.employee_id.in_(ids)).delete(synchronize_session=False)
            for employee_id in ids:
                db.session.delete(db.session.get(Employee, employee_id))
            db.session.commit()


def log_in(client, employee_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(employee_id)
        session['_fresh'] = True


@pytest.mark.parametrize(
    'instant, start, end',
    [
        (datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc), '2026-11-07', '2026-11-13'),
        (datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc), '2026-11-14', '2026-11-20'),
    ],
)
def test_payroll_dashboard_and_summary_use_same_default(
    monkeypatch, payroll_people, instant, start, end,
):
    freeze_clock(monkeypatch, instant)
    client = app.test_client()
    log_in(client, payroll_people[0])

    dashboard = client.get('/payroll_dashboard')
    assert dashboard.status_code == 200
    assert f'option value="{start}" selected'.encode() in dashboard.data
    assert f'cutoff_start={start}'.encode() in dashboard.data

    loan_review = client.get('/payroll-loan-review')
    assert loan_review.status_code == 200
    assert f'option value="{start}" selected'.encode() in loan_review.data

    summary = client.get('/payroll/summary?company=Trece-Uno&view=true')
    assert summary.status_code == 200
    assert f'Cutoff: {start} to {end}'.encode() in summary.data
    assert f'option value="{start}" selected'.encode() in summary.data

    pdf = client.get('/payroll/summary?company=Trece-Uno')
    assert pdf.status_code == 200
    filename = parse_options_header(pdf.headers['Content-Disposition'])[1]['filename']
    assert filename.startswith(f'Payroll_Summary_{start}_to_{end}_Trece-Uno_DL_')
    assert filename.endswith('_PHT.pdf')
    text = '\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(pdf.data)).pages)
    assert f'Cutoff: {start} to {end}' in text
    if start == '2026-11-07':
        assert 'Nov 11' in text.split('ATTENDANCE & LOAN BALANCE', 1)[1]


@pytest.mark.parametrize(
    'instant, selected_start, selected_end',
    [
        (datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc), '2026-11-14', '2026-11-20'),
        (datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc), '2026-11-07', '2026-11-13'),
    ],
)
def test_summary_allows_manual_cutoff_selection(
    monkeypatch, payroll_people, instant, selected_start, selected_end,
):
    freeze_clock(monkeypatch, instant)
    client = app.test_client()
    log_in(client, payroll_people[1])
    summary = client.get(
        '/payroll/summary',
        query_string={
            'company': 'Trece-Uno', 'view': 'true', 'cutoff_start': selected_start,
        },
    )
    assert summary.status_code == 200
    assert f'Cutoff: {selected_start} to {selected_end}'.encode() in summary.data
    assert f'option value="{selected_start}" selected'.encode() in summary.data


def test_attendance_views_keep_current_saturday_period(monkeypatch, payroll_people):
    freeze_clock(monkeypatch, datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc))
    client = app.test_client()
    log_in(client, payroll_people[1])
    for path in (f'/attendance/{payroll_people[1]}', f'/profile/{payroll_people[1]}'):
        response = client.get(path)
        assert response.status_code == 200
        assert b'name="cutoff_start" value="2026-11-14"' in response.data

    log_in(client, payroll_people[0])
    attendance_ot = client.get('/holiday_overtime')
    assert attendance_ot.status_code == 200
    assert b'name="cutoff_start" value="2026-11-14"' in attendance_ot.data
