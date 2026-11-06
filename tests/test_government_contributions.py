import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from hris import (
    app,
    calculate_payroll_snapshot,
    contribution_cutoff_basis,
    cutoff_statutory_contributions,
    payroll_cutoff_dates_in_month,
)
from models import (
    Attendance,
    Employee,
    GovContributionAudit,
    GovContributionSetting,
    Payroll,
    SSSContributionBracket,
    db,
)
from services.government_contributions import (
    ContributionSettingsRepository,
    GovernmentContributionCalculator,
    PayrollService,
    format_sss_table,
    official_sss_2025_rows,
    parse_sss_table,
    validate_brackets,
)
from utils.helpers import compute_weekly_deductions, compute_weekly_employer_deductions

D = Decimal
OCTOBER = date(2026, 10, 1)


def log_in(client, employee_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(employee_id)
        session["_fresh"] = True


@pytest.fixture
def calculator():
    with app.app_context():
        yield GovernmentContributionCalculator.for_month(OCTOBER)


@pytest.fixture
def people():
    created = {}
    with app.app_context():
        admin = Employee(first_name="Gov", last_name="Admin", role="admin", company="Auto Expert")
        staff = Employee(first_name="Gov", last_name="Staff", role="staff", company="Auto Expert")
        worker = Employee(
            first_name="Juan", last_name="Remit", role="staff", company="Trece-Uno",
            daily_rate=645, sss="34-1234567-8", philhealth="12-345678901-2", pagibig="1234-5678-9012",
        )
        twin = Employee(
            first_name="Pedro", last_name="Twin", role="staff", company="Auto Expert",
            daily_rate=600, sss="3412345678", philhealth="", pagibig="",
        )
        db.session.add_all([admin, staff, worker, twin])
        db.session.flush()
        for employee in (worker, twin):
            for cutoff in payroll_cutoff_dates_in_month(OCTOBER):
                for offset in range(7):
                    day = cutoff + timedelta(days=offset)
                    if day.weekday() >= 5:
                        continue
                    db.session.add(Attendance(
                        employee_id=employee.id, date=day, status="Present",
                        clock_in=datetime.combine(day, time(8, 0)),
                        clock_out=datetime.combine(day, time(17, 0)),
                        company=employee.company,
                    ))
        db.session.commit()
        created.update(admin=admin.id, staff=staff.id, worker=worker.id, twin=twin.id)
    yield created
    with app.app_context():
        db.session.rollback()
        ids = list(created.values())
        Payroll.query.filter(Payroll.employee_id.in_(ids)).delete(synchronize_session=False)
        Attendance.query.filter(Attendance.employee_id.in_(ids)).delete(synchronize_session=False)
        Employee.query.filter(Employee.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


def test_sss_bracket_lookup_uses_official_table(calculator):
    result = calculator.sss.compute(14400)
    assert (result.salary_from, result.salary_to, result.msc) == (D("14250.00"), D("14749.99"), D("14500.00"))
    assert (result.employee, result.employer, result.ec) == (D("725.00"), D("1450.00"), D("10.00"))
    assert result.total == D("2185.00")
    assert calculator.sss.compute(5249.99).msc == D("5000.00")
    assert calculator.sss.compute(5250).msc == D("5500.00")
    assert calculator.sss.compute(15000).ec == D("30.00")
    top = calculator.sss.compute(100000)
    assert (top.msc, top.employee, top.employer, top.ec, top.mpf_msc) == (
        D("35000.00"), D("1750.00"), D("3500.00"), D("30.00"), D("15000.00"))
    assert calculator.sss.compute(0).total == D("0.00")


def test_philhealth_uses_emr_not_actual_earnings(calculator):
    five_day = calculator.philhealth.compute(645, 5)
    assert (five_day.emr, five_day.basis, five_day.premium) == (D("14028.75"), D("14028.75"), D("701.44"))
    assert (five_day.employee, five_day.employer) == (D("350.72"), D("350.72"))
    # Company comparison table: 26 working days a month.
    six_day = calculator.philhealth.compute(755, 6)
    assert six_day.emr == D("19630.00")
    assert six_day.premium == D("981.50")
    assert (six_day.employee, six_day.employer) == (D("490.75"), D("490.75"))


    assert six_day.employee + six_day.employer == six_day.premium
    assert calculator.philhealth.compute(300, 5).basis == D("10000.00")
    assert calculator.philhealth.compute(10000, 6).basis == D("100000.00")
    # Same PhilHealth whether the employee worked 10 or 20 days in the month.
    assert calculator.monthly(6450, 645, 5).philhealth == calculator.monthly(12900, 645, 5).philhealth
    assert calculator.monthly(0, 645, 5).philhealth.total == D("0.00")


def test_sss_basis_is_capped_at_26_days_in_five_cutoff_months(calculator):
    for rate, days, sss_ee, ph_ee in ((645, 30, D("850.00"), D("419.25")),
                                      (755, 30, D("975.00"), D("490.75")),
                                      (645, 24, D("775.00"), D("419.25"))):
        monthly = calculator.monthly(rate * days, rate, 6)
        assert (monthly.sss.employee, monthly.philhealth.employee, monthly.pagibig.employee) == (
            sss_ee, ph_ee, D("200.00"))


def test_pagibig_rates_and_cap(calculator):
    assert (calculator.pagibig.compute(14400).employee, calculator.pagibig.compute(14400).employer) == (D("200.00"), D("200.00"))
    low = calculator.pagibig.compute(1200)
    assert (low.employee, low.employer) == (D("12.00"), D("24.00"))
    mid = calculator.pagibig.compute(8000)
    assert (mid.employee, mid.employer) == (D("160.00"), D("160.00"))


def test_sss_table_validation_and_round_trip():
    rows = official_sss_2025_rows()
    assert validate_brackets(rows) == []
    parsed, errors = parse_sss_table(format_sss_table(rows))
    assert errors == [] and parsed == rows
    gap = [dict(row) for row in rows]
    gap[3]["salary_from"] += D("10")
    assert any("no gaps" in error for error in validate_brackets(gap))
    closed = [dict(row) for row in rows]
    closed[-1]["salary_to"] = D("99999")
    assert any("last SSS bracket" in error for error in validate_brackets(closed))
    _, bad = parse_sss_table("1,2,3\nx,1,2,3,4,5")
    assert len(bad) == 2


def test_last_cutoff_true_up_matches_exact_monthly_amount(calculator):
    bases = [D("3000"), D("2500"), D("3225"), D("3000"), D("2580")]
    prior = []
    for index in range(4):
        amounts, _ = PayrollService.allocate(calculator, bases, index, [], 645, 5)
        prior.append(amounts)
    last, monthly_ee = PayrollService.allocate(calculator, bases, 4, prior, 645, 5)
    exact = calculator.monthly(sum(bases), 645, 5).amounts()
    for key, value in exact.items():
        assert sum(item[key] for item in prior) + last[key] == value
    assert monthly_ee == exact["sss"] + exact["philhealth"] + exact["pagibig"]

    # Over-deduction earlier in the month reduces the last cutoff, but never below zero.
    inflated = [dict(item, sss=item["sss"] + D("50")) for item in prior]
    reduced, _ = PayrollService.allocate(calculator, bases, 4, inflated, 645, 5)
    assert reduced["sss"] == max(last["sss"] - D("200"), D("0"))
    over = [dict(item, philhealth=item["philhealth"] + D("500")) for item in prior]
    clamped, _ = PayrollService.allocate(calculator, bases, 4, over, 645, 5)
    assert clamped["philhealth"] == D("0")
    assert clamped["sss"] == last["sss"] and clamped["pagibig"] == last["pagibig"]


def test_october_legacy_priors_do_not_make_last_cutoff_philhealth_negative(calculator):
    # Oct 3-24 were finalized with the legacy formula (~PHP 87.94 PhilHealth each) before
    # the new rules were deployed; the Oct 31 true-up must not become -1.03.
    bases = [D("3547.50")] * 5
    legacy_prior = [{"sss": D("175"), "philhealth": D("87.94"), "pagibig": D("40")}] * 4
    last, _ = PayrollService.allocate(calculator, bases, 4, legacy_prior, 645, 5)
    assert all(value >= 0 for value in last.values())
    assert last["philhealth"] == D("0")


def test_seeded_rules_switch_once_to_six_day_workweek():
    from models import GovContributionSetting
    from services.government_contributions import (
        OFFICIAL_SOURCES, apply_six_day_workweek_default,
    )
    with app.app_context():
        setting = GovContributionSetting.query.order_by(GovContributionSetting.effective_month).first()
        original = (setting.default_workweek, setting.source_reference)
        try:
            setting.default_workweek, setting.source_reference = 5, OFFICIAL_SOURCES
            db.session.commit()
            assert apply_six_day_workweek_default() >= 1
            assert db.session.get(GovContributionSetting, setting.id).default_workweek == 6
            # A later administrator choice of 5-day is never overwritten.
            setting.default_workweek = 5
            db.session.commit()
            assert apply_six_day_workweek_default() == 0
            assert db.session.get(GovContributionSetting, setting.id).default_workweek == 5
        finally:
            setting.default_workweek, setting.source_reference = original
            db.session.commit()
        philhealth = GovernmentContributionCalculator.for_month(OCTOBER).philhealth.compute(645, 6)
        assert (philhealth.emr, philhealth.employee) == (D("16770.00"), D("419.25"))


def test_payroll_uses_true_up_from_october_and_legacy_before(people):
    with app.app_context():
        worker = db.session.get(Employee, people["worker"])
        cutoffs = payroll_cutoff_dates_in_month(OCTOBER)
        assert cutoffs[-1] == date(2026, 10, 31)
        bases = [contribution_cutoff_basis(worker, item) for item in cutoffs]
        assert bases[0] == 645 * 5
        saved = []
        for cutoff in cutoffs[:-1]:
            amounts = cutoff_statutory_contributions(worker, cutoff, 0)
            record = Payroll(
                employee_id=worker.id, cutoff_start=cutoff, cutoff_end=cutoff + timedelta(days=6),
                sss=amounts["employee"]["sss"], philhealth=amounts["employee"]["philhealth"],
                pagibig=amounts["employee"]["pagibig"], employer_sss=amounts["employer"]["sss"],
                employer_sss_ec=amounts["employer"]["sss_ec"],
                employer_philhealth=amounts["employer"]["philhealth"],
                employer_pagibig=amounts["employer"]["pagibig"], is_paid=True,
            )
            db.session.add(record)
            saved.append(record)
        last_record = Payroll(employee_id=worker.id, cutoff_start=cutoffs[-1], cutoff_end=cutoffs[-1] + timedelta(days=6))
        db.session.add(last_record)
        db.session.flush()
        snapshot = calculate_payroll_snapshot(worker, last_record, cutoffs[-1], cutoffs[-1] + timedelta(days=7))
        exact = GovernmentContributionCalculator.for_month(OCTOBER).monthly(sum(bases), 645, None)
        assert round(sum(item.sss for item in saved) + snapshot["sss"], 2) == float(exact.sss.employee)
        assert round(sum(item.philhealth for item in saved) + snapshot["philhealth"], 2) == float(exact.philhealth.employee)
        assert round(sum(item.pagibig for item in saved) + snapshot["pagibig"], 2) == float(exact.pagibig.employee)
        assert round(sum(item.employer_sss_ec for item in saved) + snapshot["employer_sss_ec"], 2) == float(exact.sss.ec)
        assert snapshot["contribution_salary_base"] == bases[-1]

        september = date(2026, 9, 5)
        legacy = cutoff_statutory_contributions(worker, september, 3000.0)
        weeks = len(payroll_cutoff_dates_in_month(september))
        assert legacy["employee"] == compute_weekly_deductions(3000.0 * weeks, weeks=weeks)
        assert legacy["employer"] == compute_weekly_employer_deductions(3000.0 * weeks, weeks=weeks)
        db.session.rollback()


def test_dashboard_preview_portal_and_validations(people):
    client = app.test_client()
    log_in(client, people["admin"])
    dashboard = client.get("/admin/government-contributions?month=2026-10")
    assert dashboard.status_code == 200
    body = dashboard.get_data(as_text=True)
    assert "Labor Cost Dashboard" in body and "Juan Remit" in body
    assert "PhilHealth uses EMR (Equivalent Monthly Rate)." in body
    assert "Duplicate SSS number shared with Juan Remit" in body
    assert "Missing PhilHealth number" in body
    assert "Missing payroll data" in body
    assert body.index("Employer Payment Monitor") < body.index("<h3>Contribution Reminders")

    preview = client.get("/admin/government-contributions/preview?month=2026-10&employee_id=%d" % people["worker"])
    text = preview.get_data(as_text=True)
    assert preview.status_code == 200
    assert "₱16,770.00" in text and "₱419.25" in text and "TRUE COST TO COMPANY" in text
    assert "<h4>Pedro Twin" not in text
    assert "Duplicate SSS number shared with Pedro Twin" in text
    assert text.index("TRUE COST TO COMPANY") < text.index("<h3>Contribution Reminders")

    portal = client.get("/admin/government-contributions/portal?month=2026-10&company=Trece-Uno")
    text = portal.get_data(as_text=True)
    assert portal.status_code == 200
    assert "Grand Total SSS Payable" in text and "TOTAL TO PAY IN PORTALS" in text
    assert "SSS &mdash; PRN and e-CL" in text
    assert "PhilHealth &mdash; EPRS and SPA" in text
    assert "Open official EPRS employer portal" in text
    assert "Pag-IBIG &mdash; employer eSRS" in text
    assert "agency-logo--sss" in text and "agency-logo--philhealth" in text and "agency-logo--pagibig" in text
    assert "monthly compensation and SSS number" in text
    assert "Daily-paid MBS / EMR rule" in text
    assert "agency-generated payment amount as the final payable" in text
    assert text.index("TOTAL TO PAY IN PORTALS") < text.index("Reminders &amp; Portal Payment Instructions")
    assert text.index("Download CSV") < text.index("SSS &mdash; PRN and e-CL")
    assert text.index("TOTAL TO PAY IN PORTALS") < text.index("<h3>Contribution Reminders")
    assert "Pedro Twin" not in text

    selected_portal = client.get(
        f"/admin/government-contributions/portal?month=2026-10&employee_id={people['worker']}"
    )
    text = selected_portal.get_data(as_text=True)
    assert selected_portal.status_code == 200
    assert "Juan Remit" in text and "1 employee(s)" in text
    assert text.count("Pedro Twin") == 1
    assert "employee_id=%d" % people["worker"] in text

    history = client.get(f"/admin/government-contributions/history/{people['worker']}")
    assert history.status_code == 200


def test_exports_in_all_formats(people):
    client = app.test_client()
    log_in(client, people["admin"])
    for report in ("register", "sss", "philhealth", "pagibig", "remittance", "payment", "liability", "labor", "cost_per_employee"):
        for fmt, mimetype in (("csv", "text/csv"), ("pdf", "application/pdf"),
                              ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")):
            response = client.get(f"/admin/government-contributions/export?month=2026-10&report={report}&format={fmt}")
            assert response.status_code == 200, (report, fmt)
            assert response.mimetype == mimetype
    workbook = load_workbook(io.BytesIO(client.get(
        "/admin/government-contributions/export?month=2026-10&company=Trece-Uno&report=sss&format=xlsx"
    ).data))
    sheet = workbook.active
    assert sheet["A1"].value == "SSS Monthly Report"
    assert [cell.value for cell in sheet[4]][0] == "Juan Remit"
    history = client.get(
        f"/admin/government-contributions/export?report=history&format=csv&employee_id={people['worker']}"
    )
    assert history.status_code == 200
    assert client.get("/admin/government-contributions/export?month=2026-10&report=bogus&format=csv").status_code == 404


def test_staff_cannot_access_government_contributions(people):
    client = app.test_client()
    log_in(client, people["staff"])
    for path in ("/admin/government-contributions", "/admin/government-contributions/portal",
                 "/admin/government-contributions/settings",
                 "/admin/government-contributions/export?report=register&format=csv"):
        assert client.get(path).status_code == 403


def test_admin_can_add_validated_settings_version_with_audit(people):
    client = app.test_client()
    log_in(client, people["admin"])
    page = client.get("/admin/government-contributions/settings/new")
    assert page.status_code == 200
    with client.session_transaction() as session:
        token = session["gov_contribution_csrf"]
    form = {
        "csrf_token": token, "effective_month": "2027-01", "default_workweek": "5",
        "philhealth_rate": "5", "philhealth_floor": "10000", "philhealth_ceiling": "100000",
        "pagibig_low_threshold": "1500", "pagibig_employee_rate_low": "1",
        "pagibig_employee_rate_high": "2", "pagibig_employer_rate": "2",
        "pagibig_max_fund_salary": "10000", "source_reference": "Test circular",
        "sss_table": "salary_from,salary_to,msc,employee_share,employer_share,ec\n0,9999.99,5000,250,500,10\n10000,,10000,500,1000,30",
    }
    assert client.post("/admin/government-contributions/settings/new", data=dict(form, csrf_token="bad")).status_code == 400
    invalid = client.post("/admin/government-contributions/settings/new",
                          data=dict(form, sss_table="0,100,5000,250,500,10"))
    assert invalid.status_code == 200
    assert "Invalid Government Contribution Settings" in invalid.get_data(as_text=True)
    try:
        saved = client.post("/admin/government-contributions/settings/new", data=form)
        assert saved.status_code == 302
        with app.app_context():
            setting = GovContributionSetting.query.filter_by(effective_month=date(2027, 1, 1)).one()
            assert setting.philhealth_rate == pytest.approx(0.05)
            assert len(setting.sss_brackets) == 2
            assert ContributionSettingsRepository.for_month(date(2027, 3, 1)).id == setting.id
            assert ContributionSettingsRepository.for_month(OCTOBER).id != setting.id
            calc = GovernmentContributionCalculator.for_month(date(2027, 3, 1))
            assert calc.sss.compute(12000).employee == D("500.00")
            audit = GovContributionAudit.query.filter_by(entity_id=setting.id).one()
            assert audit.action == "Created contribution settings"
    finally:
        with app.app_context():
            setting = GovContributionSetting.query.filter_by(effective_month=date(2027, 1, 1)).first()
            if setting is not None:
                GovContributionAudit.query.filter_by(entity_id=setting.id).delete()
                SSSContributionBracket.query.filter_by(setting_id=setting.id).delete()
                db.session.delete(setting)
                db.session.commit()
