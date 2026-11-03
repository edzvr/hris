import csv
import io
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from hris import app, db, employee_performance_sources, workforce_analytics
from models import (
    Attendance, AuditLog, Employee, Evaluation, IncidentReport, LeaveRequest,
    MeritDemerit, Payroll, QuizResult, RedemptionHistory,
)
from utils.performance import actual_evaluation_points, evaluation_points, merit_report


@pytest.mark.parametrize("rating,admin,peer", [
    (1, (0, 5), (0, 2)), (1.49, (0, 5), (0, 2)),
    (1.5, (0, 3), (0, 1)), (2.49, (0, 3), (0, 1)),
    (2.5, (0, 0), (0, 0)), (3.49, (0, 0), (0, 0)),
    (3.5, (3, 0), (1, 0)), (4.49, (3, 0), (1, 0)),
    (4.5, (5, 0), (2, 0)), (5, (5, 0), (2, 0)),
])
def test_approval_and_reporting_thresholds(rating, admin, peer):
    assert actual_evaluation_points(rating) == admin
    assert evaluation_points(rating) == peer


@pytest.mark.parametrize("score,total,official,points", [
    (3, 4, True, 5), (74, 100, True, 0), (90, 100, True, 5),
    (4, 4, False, 0), (0, 0, True, 0),
])
def test_quiz_uses_percentage_official_attempt_only(score, total, official, points):
    quiz = SimpleNamespace(score=score, total_points=total, is_official=official)
    report = merit_report([], [], [quiz], [])
    assert report["categories"]["quiz"] == (points, 0)


def test_invalid_quiz_is_flagged_instead_of_awarding_points():
    report = merit_report([], [], [
        SimpleNamespace(score=5, total_points=4, is_official=True),
        SimpleNamespace(score=-1, total_points=4, is_official=True),
    ], [])
    assert report["categories"]["quiz"] == (0, 0)
    assert report["invalid_quizzes"] == 2


def test_evaluations_group_questions_and_do_not_double_count_ledger():
    def evaluation(category, rating, status="Approved", month=10):
        return SimpleNamespace(
            category=category, rating=rating, approval_status=status,
            evaluator_id=3, date=datetime(2026, month, 5),
        )
    report = merit_report([], [
        evaluation("admin_1", 5), evaluation("admin_2", 4),
        evaluation("peer_1", 5), evaluation("peer_2", 4),
        evaluation("peer_1", 1, month=9), evaluation("core", 90),
        evaluation("admin_3", 5, "Pending"), evaluation("peer_3", 1, "Rejected"),
        evaluation("admin_4", 5, None),
    ], [], [SimpleNamespace(source="evaluation", merit_points=100, demerit_points=50)])
    assert report["categories"]["admin"] == (5, 0)
    assert report["categories"]["peer"] == (2, 2)
    assert report["merit"] == 7
    assert report["demerit"] == 2
    assert report["excluded_evaluations"] == 4


@pytest.fixture
def people():
    with app.app_context():
        admin = Employee(first_name="Analytics", last_name="Admin", role="admin", company="Analytics Other")
        staff = Employee(
            first_name="Analytics", last_name="Staff", role="staff", company="Analytics Trece",
            daily_rate=600, merit_points=123, demerit_points=45, employment_status="Regular",
            admin_payroll_access=True,
        )
        other = Employee(first_name="Other", last_name="Worker", role="staff", company="Analytics Other")
        db.session.add_all([admin, staff, other])
        db.session.flush()
        ids = admin.id, staff.id, other.id
        db.session.add_all([
            Attendance(employee_id=staff.id, date=date(2026, 10, 5), status="Present",
                       clock_in=datetime(2026, 10, 5, 8), clock_out=datetime(2026, 10, 5, 17), hours=8),
            Attendance(employee_id=staff.id, date=date(2026, 10, 5), status="Present",
                       clock_in=datetime(2026, 10, 5, 8), clock_out=datetime(2026, 10, 5, 17, 20), hours=8),
            Attendance(employee_id=staff.id, date=date(2026, 10, 6), status="Late",
                       clock_in=datetime(2026, 10, 6, 8, 11), clock_out=datetime(2026, 10, 6, 12),
                       hours=4, overtime_hours=3, ot_status="Pending"),
            Attendance(employee_id=staff.id, date=date(2026, 10, 7), status="Absent"),
            Attendance(employee_id=staff.id, date=date(2026, 10, 8), status="Absent"),
            LeaveRequest(employee_id=staff.id, start_date=date(2026, 10, 7),
                         end_date=date(2026, 10, 7), status="Approved", leave_type="SIL"),
            Evaluation(employee_id=staff.id, evaluator_id=admin.id, category="admin_1",
                       rating=5, approval_status="Approved", date=datetime(2026, 10, 5)),
            Evaluation(employee_id=staff.id, evaluator_id=admin.id, category="admin_2",
                       rating=4, approval_status="Approved", date=datetime(2026, 10, 5)),
            Evaluation(employee_id=staff.id, evaluator_id=other.id, category="peer_1",
                       rating=5, approval_status="Approved", date=datetime(2026, 10, 5)),
            QuizResult(employee_id=staff.id, score=3, total_points=4, is_official=True,
                       date_taken=datetime(2026, 10, 5)),
            QuizResult(employee_id=staff.id, score=4, total_points=4, is_official=False,
                       date_taken=datetime(2026, 10, 5)),
            MeritDemerit(employee_id=staff.id, merit_points=3, demerit_points=1, date=date(2026, 10, 5)),
            MeritDemerit(employee_id=staff.id, merit_points=5, source="evaluation", date=date(2026, 10, 5)),
            Payroll(employee_id=staff.id, cutoff_start=date(2026, 10, 3), cutoff_end=date(2026, 10, 9),
                    gross_income=111, total_deductions=20, net_pay=150, is_paid=True),
            Payroll(employee_id=staff.id, cutoff_start=date(2026, 10, 10), cutoff_end=date(2026, 10, 16),
                    gross_income=222, total_deductions=22, net_pay=200, is_paid=False),
            Payroll(employee_id=staff.id, cutoff_start=date(2026, 9, 26), cutoff_end=date(2026, 10, 2),
                    gross_income=999, total_deductions=0, net_pay=999, is_paid=True),
            RedemptionHistory(employee_id=staff.id, points_redeemed=90, cash_value=900,
                              date_redeemed=datetime(2026, 10, 1)),
        ])
        db.session.commit()
    yield ids
    with app.app_context():
        db.session.rollback()
        for model in (IncidentReport, Attendance, Evaluation, QuizResult, MeritDemerit,
                      Payroll, RedemptionHistory, LeaveRequest):
            model.query.filter(model.employee_id.in_(ids)).delete(synchronize_session=False)
        AuditLog.query.filter(AuditLog.employee_id.in_(ids)).delete(synchronize_session=False)
        Employee.query.filter(Employee.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


def client_for(employee_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(employee_id)
        session["_fresh"] = True
    return client


def source_state(ids):
    result = {}
    for model in (Employee, Attendance, Evaluation, QuizResult, MeritDemerit, Payroll, RedemptionHistory, LeaveRequest):
        field = model.id if model == Employee else model.employee_id
        result[model.__tablename__] = [
            tuple(getattr(row, col.name) for col in model.__table__.columns)
            for row in model.query.filter(field.in_(ids)).order_by(model.id)
        ]
    return result


def test_filtered_analytics_aligns_with_shared_merit_and_saved_payroll(people):
    with app.app_context():
        data = workforce_analytics(date(2026, 10, 1), date(2026, 10, 31), "Analytics Trece")
        assert len(data["rows"]) == 1
        row = data["rows"][0]
        assert (row["present"], row["absent"], row["late"], row["late_minutes"]) == (1, 1, 1, 1)
        assert row["ot_hours"] == 0
        assert row["report"]["merit"] == 16
        assert row["report"]["demerit"] == 2
        assert row["report"]["duplicate_attendance"] == 1
        assert row["report"]["legacy_awards"] == 1
        assert data["paid"] == {"count": 1, "gross": 111, "deductions": 20, "net": 150}
        assert data["draft"] == {"count": 1, "gross": 222, "deductions": 22, "net": 200}
        assert data["legacy_payrolls"] == 1
        assert data["monthly"]["2026-10"]["paid_net"] == 150
        assert data["statuses"] == {"Regular": 1}
        _, _, report = employee_performance_sources(people[1], date(2026, 10, 1), date(2026, 10, 31))
        assert report == row["report"]


def test_partial_date_filter_keeps_whole_approved_evaluation_group(people):
    with app.app_context():
        answer = Evaluation.query.filter_by(employee_id=people[1], category="admin_2").one()
        answer.date = datetime(2026, 10, 7)
        db.session.commit()
        _, _, before = employee_performance_sources(people[1], date(2026, 10, 5), date(2026, 10, 5))
        _, _, completed = employee_performance_sources(people[1], date(2026, 10, 7), date(2026, 10, 7))
        assert before["categories"]["admin"] == (0, 0)
        assert completed["categories"]["admin"] == (5, 0)
        assert completed["admin_average"] == 4.5


def test_analytics_and_merit_exports_never_change_historical_records(people):
    with app.app_context():
        original = source_state(people)
    client = client_for(people[0])
    urls = [
        "/admin/analytics?start=2026-10-01&end=2026-10-31&company=Analytics+Trece",
        "/admin/analytics?start=2026-10-01&end=2026-10-31&company=Analytics+Trece&export=csv",
        f"/merit_demerit/{people[1]}",
        f"/export/merit_demerit_csv?employee_id={people[1]}",
        f"/export/merit_demerit_excel?employee_id={people[1]}",
        f"/export/merit_demerit_pdf?employee_id={people[1]}",
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
    assert b"Legacy Redemption (separate calculation)" in client.get(urls[2]).data
    with app.app_context():
        assert source_state(people) == original


@pytest.mark.parametrize("query", [
    "start=invalid", "start=2026-11-02&end=2026-11-01",
    "company=unknown", "employee_id=invalid", "employee_id=999999999",
    "end=9999-12-31", "export=pdf",
])
def test_invalid_analytics_filters_are_explicit_errors(people, query):
    response = client_for(people[0]).get("/admin/analytics?" + query)
    assert response.status_code == 400
    assert b"Select a valid date range" in response.data


def test_staff_cannot_access_analytics_or_other_merit_reports(people):
    client = client_for(people[1])
    assert client.get("/admin/analytics").status_code == 403
    assert client.get("/admin/analytics?export=csv").status_code == 403
    assert client.get(f"/merit_demerit/{people[2]}").status_code == 403
    assert client.get(f"/export/merit_demerit_csv?employee_id={people[2]}").status_code == 403
    assert client.get(f"/merit_demerit/{people[1]}").status_code == 200
    assert app.test_client().get("/admin/analytics").status_code == 302


def test_personal_analytics_and_csv_are_own_account_only_and_read_only(people):
    with app.app_context():
        other = db.session.get(Employee, people[2])
        other.first_name = "Confidential"
        other.last_name = "Coworker"
        other.company = "Private Company"
        evaluation = Evaluation.query.filter_by(employee_id=people[1], category="peer_1").one()
        evaluation.remarks = "PRIVATE EVALUATOR COMMENT"
        db.session.add(Payroll(
            employee_id=other.id, cutoff_start=date(2026, 10, 3), cutoff_end=date(2026, 10, 9),
            gross_income=9876543, total_deductions=0, net_pay=9876543, is_paid=True,
        ))
        db.session.commit()
        original = source_state(people)
    client = client_for(people[1])
    url = "/my-analytics?start=2026-10-01&end=2026-10-31"
    response = client.get(url)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "<h1>My Analytics</h1>" in html
    assert "My saved payroll" in html
    assert "16 reporting merit / 2 reporting demerit" in html
    assert 'name="company"' not in html
    assert 'name="employee_id"' not in html
    assert "Current workforce breakdown" not in html
    for private in ("Confidential Coworker", "Private Company", "9876543", "PRIVATE EVALUATOR COMMENT"):
        assert private not in html
    export = client.get(url + "&export=csv")
    assert export.status_code == 200
    assert "my_analytics_" in export.headers["Content-Disposition"]
    text = export.get_data(as_text=True)
    for private in ("Confidential Coworker", "Private Company", "9876543", "PRIVATE EVALUATOR COMMENT"):
        assert private not in text
    assert "Current workforce:" not in text
    rows = list(csv.reader(io.StringIO(text)))
    assert ["paid", "1", "111.0", "20.0", "150.0"] in rows
    assert b"View My Analytics" in client.get("/dashboard_staff").data
    with app.app_context():
        assert source_state(people) == original


@pytest.mark.parametrize("query", [
    "employee_id=999999", "employee_id=bad", "company=Another",
    "employee_id=&employee_id=999999", "company=&company=Another",
])
def test_personal_analytics_rejects_scope_tampering(people, query):
    client = client_for(people[1])
    assert client.get("/my-analytics?" + query).status_code == 403
    assert client.get("/my-analytics?export=csv&" + query).status_code == 403


def test_personal_analytics_empty_filters_and_authentication(people):
    client = client_for(people[1])
    assert client.get("/my-analytics?start=invalid").status_code == 400
    assert client.get("/my-analytics?export=pdf").status_code == 400
    assert client.get("/my-analytics?start=2026-11-02&end=2026-11-01").status_code == 400
    assert app.test_client().get("/my-analytics").status_code == 302
    with app.app_context():
        db.session.get(Employee, people[1]).company = None
        db.session.commit()
    response = client.get("/my-analytics?start=2027-01-01&end=2027-01-31")
    assert response.status_code == 200
    assert b"No recorded activity in this range." in response.data
    assert b"Unassigned" in response.data


def test_admin_personal_route_stays_personal(people):
    response = client_for(people[0]).get(
        "/my-analytics?start=2026-10-01&end=2026-10-31&export=csv"
    )
    assert response.status_code == 200
    assert b"Snapshot Staff" not in response.data
    assert b"Analytics Staff" not in response.data
    assert ["paid", "0", "0.0", "0.0", "0.0"] in list(csv.reader(io.StringIO(
        response.get_data(as_text=True)
    )))


def test_employee_filter_rejects_mismatched_company_and_handles_empty_range(people):
    client = client_for(people[0])
    assert client.get(
        f"/admin/analytics?company=Analytics+Other&employee_id={people[1]}"
    ).status_code == 400
    with app.app_context():
        data = workforce_analytics(date(2027, 1, 1), date(2027, 1, 31), employee_id=people[1])
        assert data["paid"]["count"] == 0
        assert data["draft"]["count"] == 0
        assert data["totals"]["present"] == 0
        assert data["rows"][0]["report"]["merit"] == 0


def test_finalized_snapshot_ot_cost_does_not_use_current_rates(people):
    with app.app_context():
        record = Payroll.query.filter_by(employee_id=people[1], is_paid=True, gross_income=111).one()
        import json
        record.payslip_snapshot = json.dumps({
            "snapshot_version": 1, "attendance_issues": [],
            "regular_overtime": 10, "rest_day": 20,
            "special_holiday_ot": 30, "regular_holiday_ot": 40,
        })
        db.session.get(Employee, people[1]).daily_rate = 9999
        db.session.commit()
        data = workforce_analytics(date(2026, 10, 1), date(2026, 10, 31), employee_id=people[1])
        assert data["snapshot_ot"] == 100
        assert data["legacy_payrolls"] == 0
        assert data["paid"]["net"] == 150


def test_csv_matches_screen_scope_and_neutralizes_spreadsheet_formulas(people):
    with app.app_context():
        db.session.get(Employee, people[1]).company = "=1+1"
        db.session.commit()
    response = client_for(people[0]).get(
        f"/admin/analytics?start=2026-10-01&end=2026-10-31&employee_id={people[1]}&export=csv"
    )
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    header_index = next(i for i, row in enumerate(rows) if row[:2] == ["Employee ID", "Employee"])
    row = dict(zip(rows[header_index], rows[header_index + 1]))
    assert row["Company"] == "'=1+1"
    assert row["Reporting merit"] == "16"
    assert row["Reporting demerit"] == "2"
    assert ["paid", "1", "111.0", "20.0", "150.0"] in rows
    assert ["TOTAL", "16", "2"] in list(csv.reader(io.StringIO(
        client_for(people[0]).get(f"/export/merit_demerit_csv?employee_id={people[1]}").get_data(as_text=True)
    )))


@pytest.mark.parametrize("category,merit,demerit", [("compliment", 5, 0), ("lapse", 0, 5)])
def test_incident_awards_reported_employee_once_without_rewriting_history(people, category, merit, demerit):
    with app.app_context():
        report = IncidentReport(
            employee_id=people[2], reported_employee_id=people[1],
            category=category, description="Synthetic incident", signature="test", status="pending",
        )
        db.session.add(report)
        db.session.commit()
        report_id = report.id
    client = client_for(people[0])
    for _ in range(2):
        assert client.post(f"/incident_review/{report_id}", data={
            "action": "approve", "admin_signature": "test",
        }).status_code == 302
    with app.app_context():
        target = db.session.get(Employee, people[1])
        reporter = db.session.get(Employee, people[2])
        assert (target.merit_points, target.demerit_points) == (123 + merit, 45 + demerit)
        assert (reporter.merit_points, reporter.demerit_points) == (0, 0)
        awards = MeritDemerit.query.filter_by(employee_id=target.id, source="incident").all()
        assert len(awards) == 1
        assert awards[0].reference.startswith(f"Incident #{report_id}:")
        assert RedemptionHistory.query.filter_by(employee_id=target.id).one().cash_value == 900


@pytest.mark.parametrize("recipient,action,status", [
    (False, "approve", "pending"), (True, "reject", "rejected"),
])
def test_legacy_or_rejected_incident_does_not_award_points(people, recipient, action, status):
    with app.app_context():
        report = IncidentReport(
            employee_id=people[2], reported_employee_id=people[1] if recipient else None,
            category="compliment", description="Synthetic incident", signature="test", status="pending",
        )
        db.session.add(report)
        db.session.commit()
        report_id = report.id
    response = client_for(people[0]).post(f"/incident_review/{report_id}", data={
        "action": action, "admin_signature": "test",
    })
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(IncidentReport, report_id).status == status
        assert db.session.get(Employee, people[1]).merit_points == 123
        assert not MeritDemerit.query.filter_by(source="incident", employee_id=people[1]).count()


def test_trece_sunday_hours_and_schedule_are_not_weekday_undertime(people):
    with app.app_context():
        staff = db.session.get(Employee, people[1])
        staff.company = "Trece-Uno"
        db.session.add(Attendance(
            employee_id=staff.id, date=date(2026, 10, 18), status="Present",
            clock_in=datetime(2026, 10, 18, 8), clock_out=datetime(2026, 10, 18, 14),
            hours=4,
        ))
        db.session.commit()
        data = workforce_analytics(date(2026, 10, 18), date(2026, 10, 18), employee_id=staff.id)
        assert data["totals"]["ot_hours"] == 4
        assert data["totals"]["undertime"] == 0
        assert data["totals"]["ot_estimate"] == pytest.approx(390)
