"""Effective-dated collection pause; government contribution liabilities remain due."""

import json
from datetime import date, datetime, timezone

import sqlalchemy as sa

from services.government_contributions import money


AUTO_EXPERT_PAUSE_START = date(2026, 11, 7)
AUTO_EXPERT_RESUME_FROM: date | None = None
# Production employee IDs that keep SSS/PhilHealth/Pag-IBIG deductions during the pause (12 = Mark Gil Masilang).
AUTO_EXPERT_PAUSE_EXEMPT_EMPLOYEE_IDS = frozenset({12})
EMPLOYEE_DEDUCTIONS = ('sss', 'philhealth', 'pagibig')


def employee_deductions_paused(company, role, cutoff_start, *, attendance_exempt=False, employee_id=None):
    normalized_company = ''.join(char for char in str(company or '').lower() if char.isalpha())
    return (
        employee_id not in AUTO_EXPERT_PAUSE_EXEMPT_EMPLOYEE_IDS
        and normalized_company.startswith(('autoexpert', 'autoxpert'))
        and str(role or '').strip().lower() == 'staff'
        and not attendance_exempt
        and cutoff_start >= AUTO_EXPERT_PAUSE_START
        and (AUTO_EXPERT_RESUME_FROM is None or cutoff_start < AUTO_EXPERT_RESUME_FROM)
    )


def adjust_saved_employee_deductions(connection):
    """Refund only the saved employee shares, preserving all other payroll history."""
    metadata = sa.MetaData()
    payrolls = sa.Table('payrolls', metadata, autoload_with=connection)
    employees = sa.Table('employees', metadata, autoload_with=connection)
    verifications = sa.Table('payslip_verifications', metadata, autoload_with=connection)
    audit = sa.Table('gov_contribution_audit', metadata, autoload_with=connection)
    candidates = connection.execute(
        sa.select(
            payrolls, employees.c.company, employees.c.role,
            employees.c.payroll_attendance_exempt,
        ).join(employees, employees.c.id == payrolls.c.employee_id)
        .where(payrolls.c.cutoff_start >= AUTO_EXPERT_PAUSE_START)
        .order_by(payrolls.c.id)
        .with_for_update(of=payrolls)
    ).mappings().all()
    adjusted = 0
    adjusted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in candidates:
        if not employee_deductions_paused(
            row['company'], row['role'], row['cutoff_start'],
            attendance_exempt=row['payroll_attendance_exempt'],
            employee_id=row['employee_id'],
        ):
            continue
        original = {key: money(row[key]) for key in EMPLOYEE_DEDUCTIONS}
        if any(value < 0 for value in original.values()):
            raise ValueError(f"Payroll {row['id']}: negative saved employee contribution.")
        refund = sum(original.values())
        if refund == 0:
            continue
        if row['total_deductions'] is None or row['net_pay'] is None:
            raise ValueError(f"Payroll {row['id']}: missing saved payroll totals.")
        total, net = money(row['total_deductions']), money(row['net_pay'])
        if refund > total:
            raise ValueError(f"Payroll {row['id']}: employee contributions exceed saved deductions.")
        changes = {key: 0.0 for key in EMPLOYEE_DEDUCTIONS}
        changes.update(total_deductions=float(total - refund), net_pay=float(net + refund))
        before = {key: float(value) for key, value in original.items()}
        before.update(total_deductions=float(total), net_pay=float(net))
        if row['payslip_snapshot']:
            snapshot = json.loads(row['payslip_snapshot'])
            if not isinstance(snapshot, dict) or snapshot.get('snapshot_version') != 1:
                raise ValueError(f"Payroll {row['id']}: unsupported saved payslip snapshot.")
            if not employee_deductions_paused(
                snapshot.get('company', row['company']), row['role'], row['cutoff_start'],
                attendance_exempt=row['payroll_attendance_exempt'],
                employee_id=row['employee_id'],
            ):
                continue
            if any(key not in snapshot or money(snapshot[key]) != money(value)
                   for key, value in before.items()):
                raise ValueError(f"Payroll {row['id']}: snapshot does not match saved payroll totals.")
            snapshot.update(changes)
            snapshot['employee_contribution_pause'] = {
                'effective_start': AUTO_EXPERT_PAUSE_START.isoformat(),
                'original_deductions': before,
                'refunded': float(refund),
            }
            changes['payslip_snapshot'] = json.dumps(snapshot, allow_nan=False)
        if row['is_paid']:
            changes.update(confirmation_status='Pending', confirmation_note=None, confirmed_at=None)
        connection.execute(
            payrolls.update().where(payrolls.c.id == row['id']).values(**changes)
        )
        connection.execute(verifications.update().where(
            verifications.c.payroll_id == row['id'],
            verifications.c.document_type == 'payslip',
            verifications.c.superseded_at.is_(None),
        ).values(superseded_at=adjusted_at))
        connection.execute(audit.insert().values(
            actor_name='System', action='Employee statutory deductions paused',
            details=json.dumps({
                'payroll_id': row['id'], 'cutoff_start': row['cutoff_start'].isoformat(),
                'finalized': bool(row['is_paid']), 'before': before,
                'after': {key: changes[key] for key in before},
                'previous_confirmation': {
                    'status': row['confirmation_status'], 'note': row['confirmation_note'],
                    'confirmed_at': row['confirmed_at'].isoformat() if row['confirmed_at'] else None,
                },
                'employer_shares_and_other_saved_amounts_unchanged': True,
            }, allow_nan=False),
            created_at=adjusted_at,
        ))
        adjusted += 1
    return adjusted
