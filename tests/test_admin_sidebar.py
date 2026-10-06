from types import SimpleNamespace
from unittest.mock import patch

from flask import Response

from hris import app, inject_authenticated_sidebar


def navigation_response(role, path="/dashboard_admin"):
    user = SimpleNamespace(id=123, role=role, is_authenticated=True)
    with app.test_request_context(path), patch("hris.current_user", user):
        response = inject_authenticated_sidebar(
            Response("<html><head></head><body><main>Content</main></body></html>", mimetype="text/html")
        )
    return response.get_data(as_text=True)


def test_admin_has_grouped_vertical_navigation_and_all_routes():
    html = navigation_response("admin")
    assert 'class="hris-admin-sidebar-page"' in html
    assert 'aria-label="Admin navigation"' in html
    for heading in (
        "Overview &amp; Account", "Employees &amp; Attendance",
        "Payroll &amp; Contributions", "Performance &amp; Concerns",
        "Documents &amp; Reports", "System",
    ):
        assert heading in html
    with app.test_request_context():
        from flask import url_for
        for endpoint in (
            "manage_job_descriptions", "biometric_import", "payroll_dashboard",
            "admin_payroll_history", "monthly_deductions", "thirteenth_month",
            "employee_liabilities", "holiday_ot_dashboard", "leave", "loan",
            "employee_201_selector", "evaluation_dashboard", "admin_quiz_upload",
            "admin_incidents", "staff_concerns", "hr_documents", "admin_files",
            "tax_reports", "compliance_reports", "backup", "audit_logs", "logout",
        ):
            assert f'href="{url_for(endpoint)}"' in html
    assert "#settings" in html
    assert "#employees" in html
    assert "#bulletins" in html
    assert "#analytics" in html
    assert 'href="/attendance/123"' in html
    assert 'aria-expanded="false"' in html


def test_staff_navigation_stays_in_existing_top_layout():
    html = navigation_response("staff", "/dashboard_staff")
    assert "grid-template-rows: repeat(2" in html
    assert "hris-admin-sidebar-page" not in html
    assert "Employee self-service" in html


def test_login_and_non_html_do_not_receive_admin_sidebar():
    user = SimpleNamespace(id=123, role="admin", is_authenticated=True)
    for path, mimetype in (("/login", "text/html"), ("/dashboard_admin", "application/json")):
        with app.test_request_context(path), patch("hris.current_user", user):
            response = Response("<body>Content</body>", mimetype=mimetype)
            assert inject_authenticated_sidebar(response).get_data(as_text=True) == "<body>Content</body>"


def test_admin_dashboard_has_mobile_table_regions_and_compact_sections():
    from flask import render_template
    from html.parser import HTMLParser

    class LayoutParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.tables = 0

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "table":
                assert any(
                    "admin-table-scroll" in item.get("class", "")
                    for _, item in self.stack
                )
                self.tables += 1
            if tag not in {"input", "img", "meta", "link", "br"}:
                self.stack.append((tag, attrs))

        def handle_endtag(self, tag):
            assert self.stack and self.stack[-1][0] == tag, (tag, self.stack[-1:])
            self.stack.pop()

    with app.test_request_context("/dashboard_admin"):
        html = render_template(
            "dashboard_admin.html",
            admin=SimpleNamespace(id=123, first_name="Preview", last_name="Admin"),
            pending_attendance_corrections=[], pending_ot=0, pending_leaves=0,
            pending_loans=0, pending_evaluations=0, total_employees=0, payroll_total=0,
            trece_employees=[], auto_employees=[], staff_attendance_by_employee={},
            pending_leaves_list=[], pending_loans_list=[], bulletins=[],
            trend_labels=[], trend_values=[],
        )
    parser = LayoutParser()
    parser.feed(html)
    assert parser.tables == 6
    assert not parser.stack
    assert "admin_dashboard.css" in html
    assert '<details class="profile">' in html
    assert '<details class="dashboard-shortcuts">' in html
    assert "actionsContent.appendChild" not in html
    assert "responsive: true" in html
