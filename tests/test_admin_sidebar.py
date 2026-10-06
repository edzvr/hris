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
    assert "#request-calendar" in html
    assert 'href="/attendance/123"' in html
    assert 'aria-expanded="false"' in html


def test_staff_navigation_groups_preserve_routes():
    html = navigation_response("staff", "/dashboard_staff")
    assert 'aria-label="Employee navigation"' in html
    assert "hris-admin-sidebar-page" not in html
    for label in ("Attendance", "Payroll &amp; Reports", "Loan &amp; Leave", "Performance",
                  "Documents &amp; Updates", "Help &amp; Concerns", "My Account"):
        assert label in html
    assert 'Resources &amp; Account' not in html
    assert 'Staff menu' in html
    assert 'padding-left:240px' in html
    from html.parser import HTMLParser

    class GroupParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.inside_group = False
            self.link_counts = []

        def handle_starttag(self, tag, attrs):
            if tag == 'details':
                self.inside_group = True
                self.link_counts.append(0)
            elif tag == 'a' and self.inside_group:
                self.link_counts[-1] += 1

        def handle_endtag(self, tag):
            if tag == 'details':
                self.inside_group = False

    parser = GroupParser()
    parser.feed(html)
    assert len(parser.link_counts) == 7
    assert max(parser.link_counts) <= 4
    for path in ("/attendance/123", "/payroll/123", "/profile/123", "/logout", "/peer_evaluation"):
        assert f'href="{path}"' in html
    with app.test_request_context():
        from flask import url_for
        for endpoint in ("leave", "loan", "staff_help", "monthly_reminders", "thirteenth_month",
                         "assessment", "peer_evaluation", "submit_incident", "attendance_correction",
                         "apply_ot", "employee_liabilities", "hr_documents", "staff_concerns",
                         "bulletin", "company_files", "staff_guide"):
            assert f'href="{url_for(endpoint)}"' in html
        for endpoint in ("quiz", "merit_demerit"):
            assert f'href="{url_for(endpoint, employee_id=123)}"' in html


def test_login_and_non_html_do_not_receive_admin_sidebar():
    user = SimpleNamespace(id=123, role="admin", is_authenticated=True)
    for path, mimetype in (("/login", "text/html"), ("/dashboard_admin", "application/json")):
        with app.test_request_context(path), patch("hris.current_user", user):
            response = Response("<body>Content</body>", mimetype=mimetype)
            assert inject_authenticated_sidebar(response).get_data(as_text=True) == "<body>Content</body>"


def test_admin_dashboard_has_mobile_table_regions_and_compact_sections():
    from flask import render_template
    from hris import dashboard_summary, admin_request_calendar
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
            summary=dashboard_summary(),
            request_calendar=admin_request_calendar(dashboard_summary()['month_start']),
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
    assert 'Employee directory &amp; attendance records' in html
    assert 'summary-workspace' in html
