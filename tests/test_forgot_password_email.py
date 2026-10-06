from types import SimpleNamespace
from unittest.mock import patch

from hris import app


def test_forgot_password_sends_case_insensitive_registered_email():
    app.config["TESTING"] = True
    employee = SimpleNamespace(id=456, email="owner@example.com")
    reset_token = SimpleNamespace(token="test-reset-token")

    with app.app_context():
        with (
            patch("hris.Employee.query") as employee_query,
            patch("hris.PasswordResetToken", return_value=reset_token),
            patch("hris.db.session.add"),
            patch("hris.db.session.commit"),
            patch("hris.db.session.delete") as delete,
            patch("hris.secrets.token_urlsafe", return_value="test-reset-token"),
            patch("hris.send_notification_email", return_value=True) as send_email,
        ):
            employee_query.filter.return_value.first.return_value = employee
            response = app.test_client().post(
                "/forgot_password",
                data={"email": " OWNER@EXAMPLE.COM "},
                base_url="https://hris.example.com",
                follow_redirects=False,
            )

    assert response.status_code == 302
    assert employee_query.filter.called
    assert send_email.called
    assert "https://hris.example.com/reset_password/test-reset-token" in send_email.call_args.args[2]
    assert not delete.called


def test_forgot_password_removes_token_when_email_delivery_fails():
    app.config["TESTING"] = True
    employee = SimpleNamespace(id=789, email="owner@example.com")
    reset_token = SimpleNamespace(token="undelivered-reset-token")

    with app.app_context():
        with (
            patch("hris.Employee.query") as employee_query,
            patch("hris.PasswordResetToken", return_value=reset_token),
            patch("hris.db.session.add"),
            patch("hris.db.session.commit"),
            patch("hris.db.session.delete") as delete,
            patch("hris.send_notification_email", return_value=False),
        ):
            employee_query.filter.return_value.first.return_value = employee
            response = app.test_client().post(
                "/forgot_password",
                data={"email": "owner@example.com"},
                follow_redirects=False,
            )

    assert response.status_code == 302
    delete.assert_called_once_with(reset_token)
