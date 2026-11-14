import importlib.util
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from hris import (
    app, build_company_payroll_summary, build_government_contribution_month,
    calculate_payroll_snapshot, cutoff_statutory_contributions, save_payroll_snapshot,
)
from models import Attendance, Employee, GovContributionAudit, Payroll, PayslipVerification, db


@pytest.mark.parametrize(
    'company, role, start, paused',
    [
        ('Auto Expert', 'staff', date(2026, 11, 7), True),
        ('Auto-Expert', 'staff', date(2026, 11, 28), True),
        ('Auto Expert', 'staff', date(2026, 10, 31), False),
        ('Trece-Uno', 'staff', date(2026, 11, 7), False),
        ('Auto Expert', 'admin', date(2026, 11, 7), False),
    ],
)
def test_employee_deduction_pause_keeps_employer_shares(company, role, start, paused):
    with app.app_context():
        employee = Employee(company=company, role=role, daily_rate=645)
        db.session.add(employee)
        db.session.flush()
        db.session.add(Attendance(
            employee_id=employee.id, date=start, status='Present',
            clock_in=datetime.combine(start, datetime.min.time()).replace(hour=8),
            clock_out=datetime.combine(start, datetime.min.time()).replace(hour=17),
        ))
        db.session.flush()
        contributions = cutoff_statutory_contributions(employee, start, 645)
        assert (sum(contributions['employee'].values()) == 0) is paused
        assert (contributions['monthly_employee_total'] == 0) is paused
        assert sum(contributions['employer'].values()) > 0
        db.session.rollback()


def test_pause_flows_into_payroll_summary_and_preserves_government_dues():
    start = date(2026, 11, 7)
    with app.app_context():
        employee = Employee(
            first_name='Pause', last_name='Staff', company='Auto Expert',
            role='staff', daily_rate=645,
        )
        db.session.add(employee)
        db.session.flush()
        db.session.add(Attendance(
            employee_id=employee.id, date=start, status='Present',
            clock_in=datetime(2026, 11, 7, 8), clock_out=datetime(2026, 11, 7, 17),
        ))
        record = Payroll(
            employee_id=employee.id, cutoff_start=start, cutoff_end=start + timedelta(days=6),
            loan=100, loan_deduction_override=100, other_deductions=20,
        )
        db.session.add(record)
        db.session.flush()
        snapshot = calculate_payroll_snapshot(employee, record, start, start + timedelta(days=7))
        assert snapshot['sss'] == snapshot['philhealth'] == snapshot['pagibig'] == 0
        save_payroll_snapshot(record, snapshot)
        record.is_paid = True
        db.session.flush()
        saved = json.loads(record.payslip_snapshot)
        assert saved['sss'] == saved['philhealth'] == saved['pagibig'] == 0
        assert saved['net_pay'] == record.net_pay
        summary = build_company_payroll_summary('Auto Expert', start, start + timedelta(days=7))
        row = next(item for item in summary if item['employee'].id == employee.id)
        assert row['sss'] == row['philhealth'] == row['pagibig'] == 0
        month = build_government_contribution_month(start, only_employee_id=employee.id)
        government = month['rows'][0]
        assert government['deducted_ee'] == 0
        assert government['employee_share'] > 0
        assert government['employer_share'] > 0
        assert government['ee_difference'] == government['employee_share']
        db.session.rollback()


def load_pause_migration():
    path = (
        Path(__file__).resolve().parents[1] / 'migrations' / 'versions'
        / 'f3b4c5d6e7f8_pause_auto_expert_employee_deductions.py'
    )
    spec = importlib.util.spec_from_file_location('auto_expert_pause_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def saved_payroll_engine():
    engine = sa.create_engine('sqlite:///:memory:')
    db.metadata.create_all(engine, tables=[
        Employee.__table__, Payroll.__table__, PayslipVerification.__table__,
        GovContributionAudit.__table__,
    ])
    with engine.begin() as connection:
        connection.execute(Employee.__table__.insert(), [
            dict(id=1, company='Auto Expert', role='staff', loan_balance=800),
            dict(id=2, company='Trece-Uno', role='staff', loan_balance=800),
            dict(id=3, company='Auto Expert', role='admin', loan_balance=800),
        ])
        for record_id, employee_id, start, paid, with_snapshot in [
            (1, 1, date(2026, 11, 7), True, True),
            (2, 1, date(2026, 11, 14), False, False),
            (3, 1, date(2026, 10, 31), True, True),
            (4, 2, date(2026, 11, 7), True, True),
            (5, 3, date(2026, 11, 7), True, True),
        ]:
            amounts = dict(sss=100.25, philhealth=50, pagibig=25,
                           total_deductions=400.25, net_pay=599.75)
            snapshot = dict(
                snapshot_version=1, gross_income=1000, company_loan=150,
                sss_loan=30, pagibig_loan=20,
                withholding_tax=25, company='Auto Expert', **amounts,
            )
            connection.execute(Payroll.__table__.insert().values(
                id=record_id, employee_id=employee_id, cutoff_start=start,
                cutoff_end=start + timedelta(days=6), is_paid=paid, gross_income=1000,
                loan=150, sss_loan=30, pagibig_loan=20,
                loan_deduction_applied=paid, withholding_tax=25,
                confirmation_status='Confirmed' if paid else None,
                confirmation_note='Original acknowledgment' if paid else None,
                confirmed_at=datetime(2026, 11, 14) if paid else None,
                employer_sss=200, employer_sss_ec=10, employer_philhealth=50,
                employer_pagibig=25, payslip_snapshot=json.dumps(snapshot) if with_snapshot else None,
                **amounts,
            ))
            connection.execute(PayslipVerification.__table__.insert().values(
                employee_id=employee_id, payroll_id=record_id, document_type='payslip',
                document_id=f'PAUSE-{record_id}', verification_hash=f'hash-{record_id}',
                net_pay=599.75,
            ))
    yield engine
    engine.dispose()


def test_pause_migration_refunds_saved_deductions_once_without_reapplying_balances(saved_payroll_engine):
    migration = load_pause_migration()
    with saved_payroll_engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        records = {row.id: row for row in connection.execute(sa.select(Payroll.__table__))}
        for record_id in (1, 2):
            record = records[record_id]
            assert record.sss == record.philhealth == record.pagibig == 0
            assert record.total_deductions == 225
            assert record.net_pay == 775
            assert record.gross_income == 1000
            assert record.loan == 150 and record.withholding_tax == 25
            assert record.sss_loan == 30 and record.pagibig_loan == 20
            assert record.employer_sss == 200 and record.employer_philhealth == 50
            assert record.employer_pagibig == 25 and record.employer_sss_ec == 10
        assert records[1].is_paid and records[1].loan_deduction_applied
        assert records[1].confirmation_status == 'Pending'
        assert records[1].confirmation_note is None and records[1].confirmed_at is None
        assert not records[2].is_paid and not records[2].loan_deduction_applied
        snapshot = json.loads(records[1].payslip_snapshot)
        assert snapshot['sss'] == snapshot['philhealth'] == snapshot['pagibig'] == 0
        assert snapshot['net_pay'] == 775 and snapshot['total_deductions'] == 225
        assert snapshot['employee_contribution_pause']['original_deductions']['sss'] == 100.25
        assert records[2].payslip_snapshot is None
        for record_id in (3, 4, 5):
            assert records[record_id].net_pay == 599.75
            assert records[record_id].sss == 100.25
        assert connection.scalar(sa.select(Employee.loan_balance).where(Employee.id == 1)) == 800
        assert connection.scalar(sa.select(sa.func.count()).select_from(GovContributionAudit)) == 2
        audit_details = json.loads(connection.scalar(
            sa.select(GovContributionAudit.details).order_by(GovContributionAudit.id)
        ))
        assert audit_details['previous_confirmation']['status'] == 'Confirmed'
        verifications = list(connection.execute(
            sa.select(PayslipVerification.__table__).order_by(PayslipVerification.payroll_id)
        ))
        assert all(row.superseded_at is not None for row in verifications[:2])
        assert all(row.superseded_at is None for row in verifications[2:])


def test_pause_migration_rolls_back_on_invalid_saved_snapshot(saved_payroll_engine):
    migration = load_pause_migration()
    with saved_payroll_engine.begin() as connection:
        connection.execute(Payroll.__table__.update().where(Payroll.id == 2).values(
            payslip_snapshot='{"snapshot_version": 99}'
        ))
    with pytest.raises(ValueError, match='snapshot'):
        with saved_payroll_engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
    with saved_payroll_engine.connect() as connection:
        assert connection.scalar(sa.select(Payroll.net_pay).where(Payroll.id == 1)) == 599.75
        assert connection.scalar(sa.select(sa.func.count()).select_from(GovContributionAudit)) == 0
