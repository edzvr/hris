from types import SimpleNamespace
from unittest.mock import patch

from flask import Response

from hris import app, inject_authenticated_sidebar


def navigation_response(role, path="/dashboard_admin", company="Trece-Uno"):
    user = SimpleNamespace(id=123, role=role, company=company, is_authenticated=True)
    with app.test_request_context(path), patch("hris.current_user", user):
        response = inject_authenticated_sidebar(
            Response("<html><head></head><body><main>Content</main></body></html>", mimetype="text/html")
        )
    return response.get_data(as_text=True)


def test_admin_has_grouped_top_navigation_and_all_routes():
    html = navigation_response("admin")
    assert 'class="hris-admin-sidebar-page"' in html
    assert 'aria-label="Admin navigation"' in html
    assert '<header id="hris-global-sidebar" class="admin-topbar"' in html
    assert 'id="admin-topbar-navigation"' in html
    assert 'class="admin-nav-dropdown"' in html
    assert 'padding-left:0 !important' in html
    assert 'new ResizeObserver' in html
    assert "window.matchMedia('(max-width: 760px)')" in html
    assert 'if (activeGroup) activeGroup.open = true' not in html
    for heading in (
        "Payroll &amp; Contributions", "People &amp; Attendance",
        "Reports &amp; Performance", "Account &amp; System",
    ):
        assert heading in html
    assert '<details aria-label="Payroll &amp; Contributions">' in html
    assert '<details aria-label="People &amp; Attendance">' in html
    assert '<details aria-label="Reports &amp; Performance">' in html
    assert '<details aria-label="Account &amp; System">' in html
    assert html.index("Monthly Gov Dues Guide") > html.index("Payroll &amp; Contributions")
    assert html.count("Monthly Gov Dues Guide") == 1
    payroll_menu = html.split('<details aria-label="Payroll &amp; Contributions">', 1)[1].split('</details>', 1)[0]
    assert 'Monthly Gov Dues Guide' in payroll_menu
    quick_links = html.split('aria-label="Quick access">', 1)[1].split('</div>', 1)[0]
    assert 'Monthly Gov Dues Guide' not in quick_links
    assert 'data-active-prefix="/admin/government-contributions/portal"' in html
    assert "section.addEventListener('toggle'" in html
    with app.test_request_context():
        from flask import url_for
        for endpoint in (
            "manage_job_descriptions", "biometric_import", "payroll_dashboard",
            "admin_payroll_history", "monthly_deductions", "government_contributions", "thirteenth_month",
            "employee_liabilities", "holiday_ot_dashboard", "leave", "loan",
            "employee_201_selector", "evaluation_dashboard", "admin_quiz_upload",
            "admin_incidents", "staff_concerns", "hr_documents", "admin_files",
            "tax_reports", "compliance_reports", "backup", "audit_logs", "logout", "analytics",
        ):
            assert f'href="{url_for(endpoint)}"' in html
        assert f'href="{url_for("government_portal_payment")}"' in html
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
        for endpoint in ("leave", "loan", "staff_help", "monthly_reminders", "thirteenth_month", "my_analytics",
                         "assessment", "peer_evaluation", "submit_incident", "attendance_correction",
                         "apply_ot", "employee_liabilities", "hr_documents", "staff_concerns",
                         "bulletin", "company_files", "staff_guide"):
            assert f'href="{url_for(endpoint)}"' in html
        for endpoint in ("quiz", "merit_demerit"):
            assert f'href="{url_for(endpoint, employee_id=123)}"' in html


def test_company_logos_follow_role_and_employee_company():
    admin_html = navigation_response('admin')
    assert 'images/autoxpertlogo.jpg' in admin_html
    assert 'images/trece-uno-logo.jpg' in admin_html
    assert 'aria-label="Open Auto Expert dashboard"' in admin_html
    assert 'aria-label="Open Trece-Uno dashboard"' in admin_html
    assert '/dashboard_admin?company=Auto+Expert' in admin_html
    assert '/dashboard_admin?company=Trece-Uno' in admin_html
    for company in ('Trece', 'Trece-Uno'):
        html = navigation_response('staff', '/dashboard_staff', company)
        assert 'images/trece-uno-logo.jpg' in html
        assert 'images/autoxpertlogo.jpg' not in html
    html = navigation_response('staff', '/dashboard_staff', 'Auto Expert')
    assert 'images/autoxpertlogo.jpg' in html
    assert 'images/trece-uno-logo.jpg' not in html
    html = navigation_response('staff', '/dashboard_staff', None)
    assert 'Company not assigned' in html
    assert '<img ' not in html


def test_workspace_theme_follows_each_staff_and_admin_company():
    auto = navigation_response('staff', '/dashboard_staff', 'Auto Expert')
    assert 'document.body.dataset.companyTheme = "autoxpert"' in auto
    trece = navigation_response('staff', '/dashboard_staff', 'Trece-Uno')
    assert 'document.body.dataset.companyTheme = "trece-uno"' in trece
    unassigned = navigation_response('staff', '/dashboard_staff', None)
    assert 'document.body.dataset.companyTheme = "neutral"' in unassigned
    auto_admin = navigation_response('admin', company='Auto Expert')
    assert 'document.body.dataset.companyTheme = "autoxpert"' in auto_admin
    trece_admin = navigation_response('admin', company='Trece-Uno')
    assert 'document.body.dataset.companyTheme = "trece-uno"' in trece_admin


def test_workspace_theme_loader_is_included_on_other_routes():
    for role, path in (
        ('admin', '/admin/government-contributions/portal'),
        ('admin', '/payroll_dashboard'),
        ('staff', '/attendance/123'),
    ):
        html = navigation_response(role, path)
        assert html.count('src="/static/workspace_theme.js"') == 1
        assert html.index('document.body.dataset.companyTheme') < html.index('src="/static/workspace_theme.js"')
        assert "const storedAccent" not in html


def test_login_and_non_html_do_not_receive_admin_sidebar():
    user = SimpleNamespace(id=123, role="admin", is_authenticated=True)
    for path, mimetype in (("/login", "text/html"), ("/dashboard_admin", "application/json")):
        with app.test_request_context(path), patch("hris.current_user", user):
            response = Response("<body>Content</body>", mimetype=mimetype)
            assert inject_authenticated_sidebar(response).get_data(as_text=True) == "<body>Content</body>"


def test_admin_dashboard_has_mobile_table_regions_and_compact_sections():
    from flask import render_template
    from hris import dashboard_summary, admin_request_calendar, admin_cutoff_overview, current_payroll_cutoff
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
            cutoff_overview=admin_cutoff_overview(current_payroll_cutoff()[0], employee_ids=[]),
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
    assert '<input type="color" id="buttonColor"' in html
    assert '<input type="color" id="topbarColor"' in html
    assert "topbarColor: document.getElementById('topbarColor').value" in html
    assert 'Choose separate colors for the Admin top bar and buttons' in html
    assert 'Staff colors are unchanged.' in html
    assert 'Buttons &amp; sidebar color' not in html
