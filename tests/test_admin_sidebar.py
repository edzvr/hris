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
