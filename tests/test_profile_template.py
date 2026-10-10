from datetime import date
from types import SimpleNamespace

import pytest

from hris import Employee, app, render_template


@pytest.mark.parametrize("role", ["staff", "admin"])
def test_profile_template_renders_tabs(role):
    with app.app_context():
        employee = Employee(id=1, first_name="Profile", last_name="Test", role=role)
        with app.test_request_context():
            rendered = render_template(
                "profile.html",
                emp=employee,
                viewer=employee,
                bulletins=[],
                job_descriptions=[],
                employment_statuses=["Trainee", "Probationary", "Regular"],
                cutoff_start=date(2026, 10, 3),
                cutoff_end=date(2026, 10, 9),
                attendance_month=date(2026, 10, 1),
                profile_attendance_records=[],
                profile_monthly_summary=SimpleNamespace(
                    present=0, late=0, half_day=0, absent=0, incomplete=0
                ),
            )
        assert 'id="attendance"' in rendered
        assert 'id="payroll"' in rendered
        assert 'id="feed"' in rendered
        assert "showTab" in rendered
        assert rendered.count('name="resume_summary"') == 1
        assert "Personal &amp; Contact Information" in rendered
        assert "Government IDs" in rendered
        assert 'class="table-scroll"' in rendered
        assert ('name="payroll_preparation_access"' in rendered) == (role == "admin")
        assert ('name="bank_account_number"' in rendered) == (role == "admin")
