from hris import Employee, app, render_template


def test_profile_template_renders_tabs():
    with app.app_context():
        employee = Employee.query.first()
        with app.test_request_context():
            rendered = render_template(
                "profile.html",
                emp=employee,
                viewer=employee,
                bulletins=[]
            )
        assert 'id="attendance"' in rendered
        assert 'id="payroll"' in rendered
        assert 'id="feed"' in rendered
        assert "showTab" in rendered
