import json
from datetime import date, datetime, timedelta
from io import BytesIO

import pytest
from pypdf import PdfReader

from hris import (
    TRECE_WAGE_INCREASE_DATE,
    app,
    build_thirteenth_month_rows,
    daily_rate_on,
    record_daily_rate_change,
    seed_trece_minimum_wage_history,
    thirteenth_month_pdf,
)
from models import Attendance, DailyRateHistory, Employee, Holiday, Payroll, db


def at(day, hour):
    return datetime.combine(day, datetime.min.time()).replace(hour=hour)


def free_weekday(start, used):
    day = start
    while day.weekday() == 6 or day in used or Holiday.query.filter_by(date=day).first():
        day = date.fromordinal(day.toordinal() + 1)
    return day


def cleanup(employee_id, holiday_id=None):
    db.session.rollback()
    Payroll.query.filter_by(employee_id=employee_id).delete()
    DailyRateHistory.query.filter_by(employee_id=employee_id).delete()
    Attendance.query.filter_by(employee_id=employee_id).delete()
    if holiday_id:
        db.session.delete(db.session.get(Holiday, holiday_id))
    db.session.delete(db.session.get(Employee, employee_id))
    db.session.commit()


def test_thirteenth_month_uses_rate_in_effect_and_excludes_holiday_premium():
    with app.app_context():
        staff = Employee(first_name='Wage', last_name='History', role='staff',
                         company='Trece-Uno', daily_rate=645)
        db.session.add(staff)
        db.session.flush()
        old_day = free_weekday(date(2026, 10, 19), set())
        half_day = free_weekday(date(2026, 10, 20), {old_day})
        new_day = free_weekday(date(2026, 11, 2), set())
        holiday_day = free_weekday(date(2026, 9, 1), set())
        holiday = Holiday(date=holiday_day, description='Test special day',
                          holiday_type='Special Non-Working Holiday')
        db.session.add(holiday)
        db.session.add_all([
            DailyRateHistory(employee_id=staff.id, daily_rate=600, effective_date=date(2026, 1, 1)),
            DailyRateHistory(employee_id=staff.id, daily_rate=645, effective_date=TRECE_WAGE_INCREASE_DATE),
            Attendance(employee_id=staff.id, date=old_day, clock_in=at(old_day, 8),
                       clock_out=at(old_day, 17), status='Present', company='Trece-Uno'),
            Attendance(employee_id=staff.id, date=half_day, clock_in=at(half_day, 8),
                       clock_out=at(half_day, 12), status='Present', company='Trece-Uno'),
            Attendance(employee_id=staff.id, date=new_day, clock_in=at(new_day, 8),
                       clock_out=at(new_day, 17), status='Present', company='Trece-Uno'),
            Attendance(employee_id=staff.id, date=holiday_day, clock_in=at(holiday_day, 8),
                       clock_out=at(holiday_day, 17), status='Present', company='Trece-Uno'),
        ])
        db.session.commit()
        staff_id, holiday_id = staff.id, holiday.id

    try:
        with app.app_context():
            row = build_thirteenth_month_rows(2026, staff_id)[0]
        lines = {line['rate']: line for line in row['rate_lines']}
        assert lines[600.0]['days'] == 3 and lines[600.0]['amount'] == 1800.0
        assert lines[645.0]['days'] == 1 and lines[645.0]['amount'] == 645.0
        # 600 (full) + 300 (half day) + 600 (holiday at 1x only) + 645 (new rate)
        assert row['basic_pay'] == 2145.0
        assert row['short_deduction'] == 300.0
        assert row['holiday_days'] == 1
        assert row['holiday_excluded'] == 180.0
        assert row['thirteenth_month'] == round(2145.0 / 12, 2)
        assert row['coverage_start'] == holiday_day
    finally:
        with app.app_context():
            cleanup(staff_id, holiday_id)


def test_seed_sets_trece_600_before_oct_28_and_rate_changes_are_recorded():
    with app.app_context():
        trece = Employee(first_name='Seed', last_name='Trece', role='staff',
                         company='Trece-Uno', daily_rate=645)
        auto = Employee(first_name='Seed', last_name='Auto', role='staff',
                        company='Auto Expert', daily_rate=645)
        db.session.add_all([trece, auto])
        db.session.commit()
        trece_id, auto_id = trece.id, auto.id

    try:
        with app.app_context():
            seed_trece_minimum_wage_history()
            seed_trece_minimum_wage_history()
            trece = db.session.get(Employee, trece_id)
            auto = db.session.get(Employee, auto_id)
            assert DailyRateHistory.query.filter_by(employee_id=trece_id).count() == 2
            assert DailyRateHistory.query.filter_by(employee_id=auto_id).count() == 0
            assert daily_rate_on(trece, date(2026, 9, 15)) == 600.0
            assert daily_rate_on(trece, date(2026, 10, 27)) == 600.0
            assert daily_rate_on(trece, date(2026, 10, 28)) == 645.0
            assert daily_rate_on(auto, date(2026, 9, 15)) == 645.0

            record_daily_rate_change(auto, 645, 700, effective_date=date(2026, 12, 1))
            db.session.commit()
            assert daily_rate_on(auto, date(2026, 11, 30)) == 645.0
            assert daily_rate_on(auto, date(2026, 12, 1)) == 700.0
    finally:
        with app.app_context():
            DailyRateHistory.query.filter_by(employee_id=auto_id).delete()
            db.session.commit()
            cleanup(trece_id)
            cleanup(auto_id)


@pytest.fixture
def payroll_staff():
    with app.app_context():
        staff = Employee(first_name='Basic', last_name='Fallback', role='staff',
                         company='Trece-Uno', daily_rate=645)
        db.session.add(staff)
        db.session.commit()
        staff_id = staff.id
    yield staff_id
    with app.app_context():
        cleanup(staff_id)


def saved_earnings(basic_pay):
    return json.dumps({
        'snapshot_version': 1, 'attendance_issues': [],
        'basic_pay': basic_pay, 'late_ut': 300, 'regular_overtime': 800,
        'allowance': 300, 'rice_allowance': 200, 'incentives': 100,
    })


def test_payroll_fallback_uses_saved_basic_only_not_gross_or_drafts(payroll_staff):
    with app.app_context():
        db.session.add_all([
            Payroll(employee_id=payroll_staff, cutoff_start=date(2026, 9, 5),
                    cutoff_end=date(2026, 9, 11), is_paid=True,
                    gross_income=2600, net_pay=2100, payslip_snapshot=saved_earnings(1200)),
            Payroll(employee_id=payroll_staff, cutoff_start=date(2026, 9, 12),
                    cutoff_end=date(2026, 9, 18), is_paid=False,
                    gross_income=50000, payslip_snapshot=saved_earnings(40000)),
        ])
        db.session.commit()
        row = build_thirteenth_month_rows(2026, payroll_staff)[0]
        assert row['source'] == 'payroll'
        assert row['basic_pay'] == 1200
        assert row['thirteenth_month'] == 100
        assert row['payroll_periods'] == 1
        assert len(row['calculation_warnings']) == 1
        assert 'draft payroll' in row['calculation_warnings'][0]


def attended(staff_id, day, end_hour=17):
    return Attendance(employee_id=staff_id, date=day, clock_in=at(day, 8),
                      clock_out=at(day, end_hour), status='Present', company='Trece-Uno')


def test_partial_attendance_uses_finalized_basic_for_cutoffs_without_attendance(payroll_staff):
    with app.app_context():
        attendance_day = free_weekday(date(2026, 10, 5), set())
        db.session.add_all([
            attended(payroll_staff, attendance_day),
            # Cutoff covered by attendance: payroll basic must not be added again.
            Payroll(employee_id=payroll_staff, cutoff_start=attendance_day,
                    cutoff_end=attendance_day + timedelta(days=6), is_paid=True,
                    gross_income=2600, payslip_snapshot=saved_earnings(645)),
            # Cutoff with no attendance at all: only its saved basic pay counts.
            Payroll(employee_id=payroll_staff, cutoff_start=date(2026, 3, 7),
                    cutoff_end=date(2026, 3, 13), is_paid=True,
                    gross_income=9000, payslip_snapshot=saved_earnings(1200)),
        ])
        db.session.commit()
        row = build_thirteenth_month_rows(2026, payroll_staff)[0]
        assert row['source'] == 'mixed'
        assert row['attendance_basic'] == 645.0
        assert row['payroll_basic'] == 1200.0
        assert row['payroll_periods'] == 1
        assert row['basic_pay'] == 1845.0
        assert row['thirteenth_month'] == round(1845.0 / 12, 2)
        assert row['calculation_warnings'] == []
        assert row['coverage_start'] == date(2026, 3, 7)

def test_partial_attendance_flags_unverified_cutoffs_and_missing_punches(payroll_staff):
    with app.app_context():
        attendance_day = free_weekday(date(2026, 10, 5), set())
        db.session.add_all([
            attended(payroll_staff, attendance_day),
            Payroll(employee_id=payroll_staff, cutoff_start=attendance_day,
                    cutoff_end=attendance_day + timedelta(days=6), is_paid=True,
                    gross_income=4000, payslip_snapshot=saved_earnings(3225)),
            Payroll(employee_id=payroll_staff, cutoff_start=date(2026, 3, 7),
                    cutoff_end=date(2026, 3, 13), is_paid=True, gross_income=9000),
        ])
        db.session.commit()
        row = build_thirteenth_month_rows(2026, payroll_staff)[0]
        assert row['basic_pay'] == 645.0
        warnings = ' '.join(row['calculation_warnings'])
        assert len(row['calculation_warnings']) == 2
        assert '2026-03-07' in warnings and 'unverified' in warnings
        assert 'PHP 645.00' in warnings and 'PHP 3,225.00' in warnings


@pytest.mark.parametrize('snapshot', [
    None, '{broken', '[]',
    json.dumps({'snapshot_version': 2, 'basic_pay': 1200}),
    json.dumps({'snapshot_version': 1}),
    saved_earnings(-100), saved_earnings(float('nan')),
    saved_earnings(float('inf')), saved_earnings(True), saved_earnings('1200'),
])
def test_payroll_fallback_never_guesses_missing_or_invalid_basic(
    payroll_staff, snapshot, caplog,
):
    with app.app_context():
        db.session.add(Payroll(
            employee_id=payroll_staff, cutoff_start=date(2026, 9, 5),
            cutoff_end=date(2026, 9, 11), is_paid=True,
            gross_income=2600, payslip_snapshot=snapshot,
        ))
        db.session.commit()
        row = build_thirteenth_month_rows(2026, payroll_staff)[0]
        assert row['basic_pay'] == row['thirteenth_month'] == 0
        assert row['calculation_warnings']
        assert '2026-09-05' in row['calculation_warnings'][0]
        assert '13th-month' in caplog.text


@pytest.mark.parametrize('start,end', [
    (date(2025, 12, 27), date(2026, 1, 2)),
    (date(2026, 12, 26), date(2027, 1, 1)),
])
def test_payroll_fallback_does_not_assign_cross_year_salary_to_one_year(
    payroll_staff, start, end,
):
    with app.app_context():
        db.session.add(Payroll(
            employee_id=payroll_staff, cutoff_start=start, cutoff_end=end,
            is_paid=True, gross_income=2600, payslip_snapshot=saved_earnings(1200),
        ))
        db.session.commit()
        row = build_thirteenth_month_rows(2026, payroll_staff)[0]
        assert row['basic_pay'] == row['thirteenth_month'] == 0
        assert row['calculation_warnings']


def test_draft_only_fallback_is_flagged_and_zero_basic_is_valid(payroll_staff):
    with app.app_context():
        record = Payroll(
            employee_id=payroll_staff, cutoff_start=date(2026, 9, 5),
            cutoff_end=date(2026, 9, 11), is_paid=False,
            gross_income=2600, payslip_snapshot=saved_earnings(1200),
        )
        db.session.add(record)
        db.session.commit()
        row = build_thirteenth_month_rows(2026, payroll_staff)[0]
        assert row['basic_pay'] == row['thirteenth_month'] == 0
        assert row['calculation_warnings']
        record.is_paid = True
        record.payslip_snapshot = saved_earnings(0)
        db.session.commit()
        row = build_thirteenth_month_rows(2026, payroll_staff)[0]
        assert row['basic_pay'] == row['thirteenth_month'] == 0
        assert row['calculation_warnings'] == []


def test_partial_payroll_basis_is_flagged_on_dashboard_report_and_pdf(payroll_staff):
    year = datetime.now().year
    with app.app_context():
        db.session.add_all([
            Payroll(employee_id=payroll_staff, cutoff_start=date(year, 9, 5),
                    cutoff_end=date(year, 9, 11), is_paid=True,
                    gross_income=2600, payslip_snapshot=saved_earnings(1200)),
            Payroll(employee_id=payroll_staff, cutoff_start=date(year, 9, 12),
                    cutoff_end=date(year, 9, 18), is_paid=True, gross_income=6000),
        ])
        db.session.commit()
        rows = build_thirteenth_month_rows(year, payroll_staff)
        assert rows[0]['basic_pay'] == 1200
        assert rows[0]['thirteenth_month'] == 100
        assert len(rows[0]['calculation_warnings']) == 1
        pdf = thirteenth_month_pdf(year, rows, {
            'document_id': 'basic-pay-test', 'verify_url': 'https://example.com/verify',
        })
    text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf.getvalue())).pages)
    assert 'INCOMPLETE' in text
    assert f'{year}-09-12' in text
    assert 'PHP 100.00' in text

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(payroll_staff)
        session['_fresh'] = True
    dashboard = client.get('/dashboard_staff')
    assert dashboard.status_code == 200
    card = dashboard.get_data(as_text=True).split('<h3>Attendance</h3>')[1].split('<h3>Payroll</h3>')[0]
    assert 'PHP 100.00' in card
    assert 'Needs review' not in card
    assert 'Gross pay from' not in card
    report = client.get(f'/13th-month?year={year}')
    assert report.status_code == 200
    assert 'Needs review' in report.get_data(as_text=True)
    assert 'Verified subtotal (incomplete)' in report.get_data(as_text=True)
